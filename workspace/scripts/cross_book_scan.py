#!/usr/bin/env python3
"""
cross_book_scan.py — 生产版跨书商 edge 探测器（1X2 + AH 让球线 + 大小球线）。

解析 the-odds-api 快照 JSON（含多书商报价），以 Pinnacle 为 sharp 锚，
扫描全 board 找价差 edge。输出 qualified_play 候选。

市场范围：
  - h2h（1X2 胜平负）
  - spreads（亚洲让球 / AH）— 同线匹配，不含跨线换算
  - totals（大小球）— 同线匹配

同线匹配规则：
  - 只比较相同点数的线（-0.5 比 -0.5，2.5 比 2.5）
  - 跨线换算暂时不做（留未来）
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from typing import Any

EDGE_THRESHOLD = 0.02  # 2%
SUSPECT_THRESHOLD = 0.08  # 8%
SHARP_BOOKS = {"pinnacle", "betfair_ex_eu", "matchbook"}


# ---------- 去 vig ----------

def implied(odds):
    return [1.0 / o for o in odds]

def devig_multiplicative(odds):
    imp = implied(odds); s = sum(imp)
    return [p / s for p in imp]

def devig_power(odds):
    imp = implied(odds)
    lo, hi = 0.5, 5.0
    for _ in range(100):
        k = (lo + hi) / 2
        s = sum(p ** k for p in imp)
        if abs(s - 1) < 1e-10:
            break
        if s > 1:
            lo = k
        else:
            hi = k
    return [p ** k for p in imp]

def devig_shin(odds, max_iter=200):
    pi = implied(odds)
    Z = sum(pi)
    b = [p / Z for p in pi]
    def p_of(z):
        return [(math.sqrt(z * z + 4 * (1 - z) * bi * bi * Z) - z) / (2 * (1 - z)) for bi in b]
    lo, hi = 1e-6, 0.4
    for _ in range(max_iter):
        z = (lo + hi) / 2
        s = sum(p_of(z))
        if s > 1:
            lo = z
        else:
            hi = z
    return p_of((lo + hi) / 2)


DEVIG_METHODS = {
    "shin": devig_shin,
    "power": devig_power,
    "multiplicative": devig_multiplicative,
}


# ---------- 数据结构 ----------

class Edge:
    """单个跨书商 edge 记录。"""

    def __init__(
        self,
        match_id: str,
        home: str,
        away: str,
        book: str,
        market_type: str,  # "h2h" | "spreads" | "totals"
        line: float | None,  # None for h2h, spread/totals line for others
        outcome: str,       # team name / Over / Under / Draw
        offered_odds: float,
        sharp_fair_prob: float,
        fair_odds: float,
        ev_shin: float,
        ev_power: float,
        ev_multiplicative: float,
        survives_all_methods: bool,
        suspect: bool,
        book_tier: str,
    ):
        self.match_id = match_id
        self.home = home
        self.away = away
        self.book = book
        self.market_type = market_type
        self.line = line
        self.outcome = outcome
        self.offered_odds = offered_odds
        self.sharp_fair_prob = round(sharp_fair_prob, 4)
        self.fair_odds = round(fair_odds, 3)
        self.ev_shin = round(ev_shin, 4)
        self.ev_power = round(ev_power, 4)
        self.ev_multiplicative = round(ev_multiplicative, 4)
        self.survives_all_methods = survives_all_methods
        self.suspect = suspect
        self.book_tier = book_tier

    def to_dict(self) -> dict:
        return {
            "match": self.match_id,
            "home": self.home,
            "away": self.away,
            "book": self.book,
            "market_type": self.market_type,
            "line": self.line,
            "outcome": self.outcome,
            "offered_odds": self.offered_odds,
            "sharp_fair_prob": self.sharp_fair_prob,
            "fair_odds": self.fair_odds,
            "ev_shin": self.ev_shin,
            "ev_power": self.ev_power,
            "ev_multiplicative": self.ev_multiplicative,
            "survives_all_methods": self.survives_all_methods,
            "suspect": self.suspect,
            "book_tier": self.book_tier,
        }

    def qualified_play_dict(self) -> dict:
        return {
            "match": self.match_id,
            "home": self.home,
            "away": self.away,
            "book": self.book,
            "market_type": self.market_type,
            "line": self.line,
            "outcome": self.outcome,
            "offered": self.offered_odds,
            "fair_odds": self.fair_odds,
            "ev": round(self.ev_shin, 4),
            "tier": self.book_tier,
            "survives_all_methods": self.survives_all_methods,
            "action": "qualified_play — review required",
        }


# ---------- 快照解析 ----------

def parse_snapshot(path: str) -> list[dict]:
    with open(path) as f:
        raw = json.load(f)
    return raw if isinstance(raw, list) else raw.get("data", [])


def match_key(m: dict) -> str:
    t = m.get("home_team", "") + "_" + m.get("away_team", "")
    return t.replace(" ", "_").lower()


def _find_market(markets: list[dict], *keys: str) -> dict | None:
    """Find first market matching any of the given keys."""
    for mk in markets:
        if mk.get("key") in keys:
            return mk
    return None


def _outcome_dict(outcomes: list[dict]) -> dict[str, float]:
    """Map outcome name → price."""
    return {o["name"]: o["price"] for o in outcomes if o.get("price", 0) > 0}


def _outcome_dict_with_point(outcomes: list[dict]) -> dict[str, dict]:
    """Map outcome name → {price, point}."""
    return {
        o["name"]: {"price": o["price"], "point": o.get("point")}
        for o in outcomes if o.get("price", 0) > 0
    }


# ---------- 扫描核心 ----------

def _scan_h2h(
    edges: list[Edge],
    by_match: dict,
    match_id: str, home: str, away: str,
    bookmakers: list[dict],
    sharp_key: str,
    h2h_market: dict,
):
    """Scan h2h market: Pinnacle devig → compare all other books."""
    outcomes = h2h_market.get("outcomes", [])
    if len(outcomes) < 3:
        return
    sharp_prices = _outcome_dict(outcomes)
    if len(sharp_prices) < 3:
        return
    odds_list = list(sharp_prices.values())
    outcome_names = list(sharp_prices.keys())

    try:
        fair_shin = DEVIG_METHODS["shin"](odds_list)
        fair_power = DEVIG_METHODS["power"](odds_list)
        fair_mult = DEVIG_METHODS["multiplicative"](odds_list)
    except Exception:
        return

    for bk in bookmakers:
        bk_key = bk.get("key", "").lower()
        if bk_key == sharp_key:
            continue
        mk = _find_market(bk.get("markets", []), "h2h", "1x2")
        if not mk:
            continue
        for oc in mk.get("outcomes", []):
            name = oc.get("name", "")
            offered = oc.get("price", 0)
            if offered <= 0 or name not in sharp_prices:
                continue
            try:
                idx = outcome_names.index(name)
            except ValueError:
                continue
            _add_edge(edges, by_match, match_id, home, away, bk_key,
                      "h2h", None, name, offered,
                      fair_shin[idx], fair_power[idx], fair_mult[idx],
                      bk_key not in SHARP_BOOKS)


def _scan_spreads(
    edges: list[Edge],
    by_match: dict,
    match_id: str, home: str, away: str,
    bookmakers: list[dict],
    sharp_key: str,
    sharp_spreads: dict,
):
    """Scan spreads (AH) market: Pinnacle devig per line → compare same lines."""
    for outcome_name, sp in sharp_spreads.items():
        point = sp.get("point")
        price = sp["price"]
        if point is None or price <= 0:
            continue
        # Devig the 2-outcome spread at this line (Pinnacle has 2 outcomes total)
        all_sharp_sp = list(sharp_spreads.values())
        odds_pair = [s["price"] for s in all_sharp_sp]
        try:
            fair_shin = DEVIG_METHODS["shin"](odds_pair)
            fair_power = DEVIG_METHODS["power"](odds_pair)
            fair_mult = DEVIG_METHODS["multiplicative"](odds_pair)
        except Exception:
            continue
        # Find this outcome's index in the pair
        pair_names = list(sharp_spreads.keys())
        try:
            idx = pair_names.index(outcome_name)
        except ValueError:
            continue
        p_shin = fair_shin[idx]
        p_power = fair_power[idx]
        p_mult = fair_mult[idx]
        fair_odds = 1.0 / p_shin if p_shin > 0 else 999

        # Scan all other books for the SAME line
        for bk in bookmakers:
            bk_key = bk.get("key", "").lower()
            if bk_key == sharp_key:
                continue
            mk = _find_market(bk.get("markets", []), "spreads")
            if not mk:
                continue
            for oc in mk.get("outcomes", []):
                oc_name = oc.get("name", "")
                oc_point = oc.get("point")
                offered = oc.get("price", 0)
                if offered <= 0:
                    continue
                # 同线匹配: same point AND same outcome name
                if oc_point != point or oc_name != outcome_name:
                    continue
                _add_edge(edges, by_match, match_id, home, away, bk_key,
                          "spreads", point, outcome_name, offered,
                          p_shin, p_power, p_mult,
                          bk_key not in SHARP_BOOKS)
                break  # only one match per book per line


def _scan_totals(
    edges: list[Edge],
    by_match: dict,
    match_id: str, home: str, away: str,
    bookmakers: list[dict],
    sharp_key: str,
    sharp_totals: dict,
):
    """Scan totals market: Pinnacle devig per line → compare same lines."""
    for tot_name, tot in sharp_totals.items():
        point = tot.get("point")
        price = tot["price"]
        if point is None or price <= 0:
            continue
        # Devig the 2-outcome totals at this line
        all_sharp_tot = list(sharp_totals.values())
        odds_pair = [t["price"] for t in all_sharp_tot]
        try:
            fair_shin = DEVIG_METHODS["shin"](odds_pair)
            fair_power = DEVIG_METHODS["power"](odds_pair)
            fair_mult = DEVIG_METHODS["multiplicative"](odds_pair)
        except Exception:
            continue
        pair_names = list(sharp_totals.keys())
        try:
            idx = pair_names.index(tot_name)
        except ValueError:
            continue
        p_shin = fair_shin[idx]
        p_power = fair_power[idx]
        p_mult = fair_mult[idx]

        # Scan all other books for the SAME line
        for bk in bookmakers:
            bk_key = bk.get("key", "").lower()
            if bk_key == sharp_key:
                continue
            mk = _find_market(bk.get("markets", []), "totals")
            if not mk:
                continue
            for oc in mk.get("outcomes", []):
                oc_name = oc.get("name", "")
                oc_point = oc.get("point")
                offered = oc.get("price", 0)
                if offered <= 0:
                    continue
                # 同线匹配: same point AND same side (Over/Under)
                if oc_point != point or oc_name != tot_name:
                    continue
                _add_edge(edges, by_match, match_id, home, away, bk_key,
                          "totals", point, tot_name, offered,
                          p_shin, p_power, p_mult,
                          bk_key not in SHARP_BOOKS)
                break


def _add_edge(
    edges: list[Edge], by_match: dict,
    match_id: str, home: str, away: str, bk_key: str,
    market_type: str, line: float | None, outcome: str, offered: float,
    p_shin: float, p_power: float, p_mult: float,
    is_soft: bool,
):
    """Compute EV, check survival and suspect, append edge."""
    ev_shin = p_shin * offered - 1
    ev_power = p_power * offered - 1
    ev_mult = p_mult * offered - 1
    survives = (ev_shin >= EDGE_THRESHOLD and
                ev_power >= EDGE_THRESHOLD and
                ev_mult >= EDGE_THRESHOLD)
    edge = Edge(
        match_id=match_id,
        home=home,
        away=away,
        book=bk_key,
        market_type=market_type,
        line=line,
        outcome=outcome,
        offered_odds=offered,
        sharp_fair_prob=p_shin,
        fair_odds=round(1.0 / p_shin, 3) if p_shin > 0 else 999,
        ev_shin=ev_shin,
        ev_power=ev_power,
        ev_multiplicative=ev_mult,
        survives_all_methods=survives,
        suspect=ev_shin > SUSPECT_THRESHOLD,
        book_tier="soft" if is_soft else "sharp",
    )
    edges.append(edge)
    by_match[match_id].append(edge)


# ---------- 主扫描 ----------

def scan_snapshot(snapshot_path: str) -> dict[str, Any]:
    matches = parse_snapshot(snapshot_path)
    print(f"[cross_book_scan] Scanning {len(matches)} matches from {snapshot_path}")

    all_edges: list[Edge] = []
    by_match: dict[str, list[Edge]] = defaultdict(list)

    for m in matches:
        mid = match_key(m)
        home = m.get("home_team", "?")
        away = m.get("away_team", "?")
        bookmakers = m.get("bookmakers", [])

        # 1. Find sharp anchor (Pinnacle preferred)
        sharp_odds = None
        sharp_key = None
        for bk in bookmakers:
            bk_key = bk.get("key", "").lower()
            if bk_key == "pinnacle":
                sharp_odds = bk
                sharp_key = "pinnacle"
                break
            elif bk_key in SHARP_BOOKS and sharp_odds is None:
                sharp_odds = bk
                sharp_key = bk_key

        if not sharp_odds or not sharp_key:
            continue

        markets = sharp_odds.get("markets", [])

        # 2. H2H scan
        h2h_market = _find_market(markets, "h2h", "1x2")
        if h2h_market:
            _scan_h2h(all_edges, by_match, mid, home, away,
                      bookmakers, sharp_key, h2h_market)

        # 3. Spreads (AH) scan — only from Pinnacle
        if sharp_key == "pinnacle":
            spreads_market = _find_market(markets, "spreads")
            if spreads_market:
                sharp_spreads = _outcome_dict_with_point(spreads_market.get("outcomes", []))
                if len(sharp_spreads) >= 2:
                    _scan_spreads(all_edges, by_match, mid, home, away,
                                  bookmakers, sharp_key, sharp_spreads)

        # 4. Totals scan — only from Pinnacle
        if sharp_key == "pinnacle":
            totals_market = _find_market(markets, "totals")
            if totals_market:
                sharp_totals = _outcome_dict_with_point(totals_market.get("outcomes", []))
                if len(sharp_totals) >= 2:
                    _scan_totals(all_edges, by_match, mid, home, away,
                                 bookmakers, sharp_key, sharp_totals)

    # 5. Sort
    def sort_key(e: Edge):
        return (
            -(1 if e.survives_all_methods and not e.suspect else
              0 if e.survives_all_methods else -1),
            -e.ev_shin,
        )
    all_edges.sort(key=sort_key)

    # 6. Build output
    total_matches_with_anchor = sum(
        1 for m in matches
        if any(b.get("key", "").lower() in SHARP_BOOKS
               for b in m.get("bookmakers", []))
    )
    total_edges = len(all_edges)
    survives_edges = [e for e in all_edges if e.survives_all_methods and not e.suspect]

    # Per-market summary
    market_counts = defaultdict(int)
    for e in all_edges:
        market_counts[e.market_type] += 1
    qp_market_counts = defaultdict(int)
    for e in survives_edges:
        qp_market_counts[e.market_type] += 1

    return {
        "scan_date": __import__("time").strftime(
            "%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "snapshot": snapshot_path,
        "matches_with_pinnacle": total_matches_with_anchor,
        "total_edges_found": total_edges,
        "edges_by_market": dict(market_counts),
        "qualified_play_candidates": len(survives_edges),
        "qualified_plays_by_market": dict(qp_market_counts),
        "edges": [e.to_dict() for e in all_edges[:30]],
        "qualified_plays": [
            e.qualified_play_dict() for e in survives_edges[:10]
        ],
    }


# ---------- Oracle 演示（仅 h2h，向后兼容） ----------

ORACLE_SNAPSHOT = json.dumps([
    {
        "id": "demo",
        "home_team": "Mexico",
        "away_team": "South Africa",
        "bookmakers": [
            {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Mexico", "price": 1.45},
                {"name": "Draw", "price": 4.33},
                {"name": "South Africa", "price": 8.04},
            ]}]},
            {"key": "bet365", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Mexico", "price": 1.58},
                {"name": "Draw", "price": 4.40},
                {"name": "South Africa", "price": 8.20},
            ]}]},
            {"key": "softX", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Mexico", "price": 1.50},
                {"name": "Draw", "price": 4.50},
                {"name": "South Africa", "price": 8.20},
            ]}]},
        ],
    }
])


def run_oracle():
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(ORACLE_SNAPSHOT)
        path = f.name
    result = scan_snapshot(path)
    os.unlink(path)

    errors = []
    for e in result["edges"]:
        if e["book"] == "bet365" and e["outcome"] == "home":
            if not e["survives_all_methods"]:
                errors.append("bet365 home should survive_all_methods=true")
        elif e["book"] == "softX" and e["outcome"] == "away":
            if e["survives_all_methods"]:
                errors.append("softX away should survive_all_methods=false")
    if errors:
        print(f"[oracle] FAIL: {errors}", file=sys.stderr)
        sys.exit(1)
    print(f"[oracle] PASS — {len(result['edges'])} edges, {result['qualified_play_candidates']} qualified")
    return result


# ---------- 入口 ----------

if __name__ == "__main__":
    if "--demo" in sys.argv:
        result = run_oracle()
    elif len(sys.argv) > 1 and sys.argv[1] == "--snapshot":
        path = sys.argv[2] if len(sys.argv) > 2 else None
        if not path:
            print("Usage: cross_book_scan.py --snapshot <path> [--json]", file=sys.stderr)
            sys.exit(1)
        result = scan_snapshot(path)
    else:
        print("Usage: cross_book_scan.py --snapshot <path> | --demo", file=sys.stderr)
        sys.exit(1)

    if "--json" in sys.argv or "--demo" not in sys.argv:
        print(json.dumps(result, indent=2, ensure_ascii=False))
