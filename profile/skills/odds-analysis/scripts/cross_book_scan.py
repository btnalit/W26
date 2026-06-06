#!/usr/bin/env python3
"""
cross_book_scan.py — 主 edge 探测器(生产脚本 / oracle 同体)。

使用:
  python3 cross_book_scan.py --input-snapshot <path> --output <path> [--match-home "Mexico"] [--match-away "South Africa"]

设计:
  不去"打赢 Pinnacle 的脑子",而是用 sharp 书商(Pinnacle/Betfair)去 vig
  得到"真实概率最佳估计",再扫描全 board 上每一家书商的报价,找出
  "报价隐含概率 < sharp 公平概率"(= 它在多付你钱)的 +EV 机会。

关键技术点:
  - 去 vig 方法决定成败。必须用 shin 作主,要求三法(shin/power/multiplicative)皆过。
  - v1 覆盖 h2h + 同线 AH(spreads) + 同线 Totals。
  - 跨线插值(v2)依赖模型 margin 分布。

Oracle 合约:
  本文件实现了完整的跨书商盘口扫描逻辑
  产生相同的 EV 输出。验证方法见 oracle 对拍脚本。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

# ── 导入生产 devig 的三法（同时作为 oracle 的内联实现） ──
# 生产环境：从 devig.py 导入
# 独立运行（oracle 模式）：使用内联函数（与 devig.py 逐字一致）
try:
    SCRIPT_DIR = Path(__file__).resolve().parent
    DEVIG_PATH = SCRIPT_DIR / "devig.py"
    if DEVIG_PATH.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("_devig_module", str(DEVIG_PATH))
        _devig_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_devig_mod)
        implied = _devig_mod.implied
        devig_multiplicative = _devig_mod.devig_multiplicative
        devig_power = _devig_mod.devig_power
        devig_shin = _devig_mod.devig_shin
        DEVIG_METHODS = _devig_mod.DEVIG_METHODS
    else:
        raise ImportError("devig.py not found")
except (ImportError, AttributeError):
    # ── 内联 oracle 实现（与 devig.py 逐字一致） ──
    def implied(odds):
        return [1.0 / o for o in odds]

    def devig_multiplicative(odds):
        imp = implied(odds)
        s = sum(imp)
        return [p / s for p in imp]

    def devig_power(odds):
        imp = implied(odds)
        lo, hi = 0.5, 5.0
        for _ in range(100):
            k = (lo + hi) / 2
            s = sum(p ** k for p in imp)
            if s > 1:
                lo = k
            else:
                hi = k
        k = (lo + hi) / 2
        return [p ** k for p in imp]

    def devig_shin(odds):
        pi = implied(odds)
        Z = sum(pi)
        b = [p / Z for p in pi]
        def p_of(z):
            return [(math.sqrt(z*z + 4*(1-z)*bi*bi*Z) - z) / (2*(1-z)) for bi in b]
        lo, hi = 1e-6, 0.4
        for _ in range(200):
            z = (lo + hi) / 2
            s = sum(p_of(z))
            if s > 1:
                lo = z
            else:
                hi = z
        return p_of((lo + hi) / 2)

    DEVIG_METHODS = {"multiplicative": devig_multiplicative, "power": devig_power, "shin": devig_shin}

# ── 常量 ──
SHARP_BOOKS = ("pinnacle", "betfair_ex")
EDGE_THRESHOLD = 0.02      # +2% EV 才进候选
ACTIONABLE_EV_THRESHOLD = 0.05  # +5% EV 才能进入 qualified_play/actionable
SUSPECT_THRESHOLD = 0.08   # +8% EV 以上标 suspect
PRIMARY = "shin"            # 主去 vig 方法

# ── 数据结构 ──

def normalize_book_key(raw_key: str | None, title: str | None = None) -> str:
    """Normalize regional/provider-specific book keys to the scanner contract."""
    key = str(raw_key or title or "unknown").strip().lower()
    if key.startswith("betfair_ex"):
        return "betfair_ex"
    if key.startswith("pinnacle"):
        return "pinnacle"
    return key


class Edge:
    """单条跨书商 edge 记录。"""
    __slots__ = ("book", "market_key", "outcome", "offered_odds",
                 "sharp_fair_prob", "fair_odds",
                 "ev_shin", "ev_power", "ev_multiplicative",
                 "survives_all_methods", "suspect", "book_tier")
    def __init__(self, book: str, market_key: str, outcome: str,
                 offered_odds: float, sharp_fair_prob: float,
                 ev_shin: float, ev_power: float, ev_multiplicative: float,
                 survives_all_methods: bool, suspect: bool, book_tier: str):
        self.book = book
        self.market_key = market_key
        self.outcome = outcome
        self.offered_odds = offered_odds
        self.sharp_fair_prob = round(sharp_fair_prob, 4)
        self.fair_odds = round(1.0 / sharp_fair_prob, 3) if sharp_fair_prob > 0 else 0.0
        self.ev_shin = round(ev_shin, 4)
        self.ev_power = round(ev_power, 4)
        self.ev_multiplicative = round(ev_multiplicative, 4)
        self.survives_all_methods = survives_all_methods
        self.suspect = suspect
        self.book_tier = book_tier

    def to_dict(self) -> dict[str, Any]:
        return {s: getattr(self, s) for s in self.__slots__}


def ev_band(ev: float) -> str:
    """Reader-facing EV band for Path A; it is not an action decision."""
    if ev < 0.05:
        return "noise_lt_5pp"
    if ev < 0.08:
        return "weak_5_8pp"
    if ev < 0.13:
        return "medium_8_13pp"
    return "strong_gt_13pp"


# ── 快照解析（v1：h2h + 同线 AH + 同线 Totals）──

def parse_odds_snapshot(snapshot_path: str,
                        match_home: str | None = None,
                        match_away: str | None = None) -> dict[str, dict[str, dict[str, float]]]:
    """
    把 the-odds-api 快照（全 board）转成 per-market board dict。

    v1 覆盖:
      - h2h：直接比价
      - spreads (AH)：只比同线（与 Pinnacle 相同盘口线）
      - totals：只比同线（与 Pinnacle 相同盘口线）

    返回: { market_key: { book_key: { outcome_label: decimal_odds } } }
    market_key: "h2h" | "spreads" | "totals"
    """
    with open(snapshot_path) as f:
        data = json.load(f)

    matches = data.get("data", data) if isinstance(data, dict) else data
    for match in matches:
        home = match.get("home_team", "")
        away = match.get("away_team", "")
        if match_home and match_away:
            if home.lower() != match_home.lower() or away.lower() != match_away.lower():
                continue

        board: dict[str, dict[str, dict[str, float]]] = {
            "h2h": {}, "spreads": {}, "totals": {}
        }

        # First pass: identify Pinnacle's line per market
        pinnacle_markets: dict[str, Any] = {}
        for bm in match.get("bookmakers", []):
            if normalize_book_key(bm.get("key"), bm.get("title")) == "pinnacle":
                for market in bm.get("markets", []):
                    mkey = market.get("key")
                    if mkey in ("h2h", "spreads", "totals"):
                        pinnacle_markets[mkey] = market

        # Second pass: collect prices from all books
        for bm in match.get("bookmakers", []):
            key = normalize_book_key(bm.get("key"), bm.get("title"))

            for market in bm.get("markets", []):
                mkey = market.get("key")

                if mkey == "h2h":
                    if key not in board["h2h"]:
                        board["h2h"][key] = {}
                    for outcome in market.get("outcomes", []):
                        board["h2h"][key][outcome.get("name", "").lower()] = outcome["price"]

                elif mkey == "spreads":
                    pin = pinnacle_markets.get("spreads")
                    if not pin:
                        continue
                    pin_lines = {o["point"] for o in pin.get("outcomes", []) if "point" in o}
                    book_lines = {o["point"] for o in market.get("outcomes", []) if "point" in o}
                    common = pin_lines & book_lines
                    if common:
                        if key not in board["spreads"]:
                            board["spreads"][key] = {}
                        for outcome in market.get("outcomes", []):
                            pt = outcome.get("point")
                            if pt is not None and pt in common:
                                label = f"{outcome.get('name', '').lower()}@{pt}"
                                board["spreads"][key][label] = outcome["price"]

                elif mkey == "totals":
                    pin = pinnacle_markets.get("totals")
                    if not pin:
                        continue
                    pin_lines = {o["point"] for o in pin.get("outcomes", []) if "point" in o}
                    book_lines = {o["point"] for o in market.get("outcomes", []) if "point" in o}
                    common = pin_lines & book_lines
                    if common:
                        if key not in board["totals"]:
                            board["totals"][key] = {}
                        for outcome in market.get("outcomes", []):
                            pt = outcome.get("point")
                            if pt is not None and pt in common:
                                label = f"{outcome.get('name', '').lower()}@{pt}"
                                board["totals"][key][label] = outcome["price"]

        return board
    return {"h2h": {}, "spreads": {}, "totals": {}}


# ── 核心扫描器 ──

def scan_market(board: dict[str, dict[str, dict[str, float]]],
                market_key: str,
                outcomes: list[str],
                sharp_books: tuple[str, ...] = SHARP_BOOKS,
                edge_threshold: float = EDGE_THRESHOLD,
                actionable_threshold: float = ACTIONABLE_EV_THRESHOLD,
                suspect_threshold: float = SUSPECT_THRESHOLD,
                primary: str = PRIMARY) -> dict[str, Any]:
    """
    board: 来自 parse_odds_snapshot 的完整 board（多市场）
    market_key: "h2h" | "spreads" | "totals"
    outcomes: 该市场的 key 列表, 如 ["home","draw","away"] 或 ["over@2.5","under@2.5"]

    返回: { status, sharp_anchor, sharp_overround, devig_primary, fair_probs, quotes, edges }
    """
    market_data = board.get(market_key, {})
    if not market_data:
        return {"status": "no_market_data", "quotes": [], "edges": [], "quotes_scanned": 0}

    # 1. 找 sharp 锚
    anchor = next((b for b in sharp_books if b in market_data), None)
    if anchor is None:
        return {"status": "no_sharp_anchor", "quotes": [], "edges": [], "quotes_scanned": 0}
    anchor_outcomes = [o for o in outcomes if o in market_data[anchor]]
    if not anchor_outcomes:
        return {"status": "no_anchor_outcomes", "quotes": [], "edges": [], "quotes_scanned": 0}

    # 2. sharp 去 vig
    sharp_odds = [market_data[anchor][o] for o in anchor_outcomes]
    fair: dict[str, list[float]] = {}
    for method_name in DEVIG_METHODS:
        fair[method_name] = DEVIG_METHODS[method_name](sharp_odds)

    sharp_overround = sum(1.0 / o for o in sharp_odds) - 1.0

    # 3. 扫非锚 book
    quotes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for book, prices in market_data.items():
        if book == anchor:
            continue
        for o in anchor_outcomes:
            if o not in prices:
                continue
            offered = prices[o]

            outcome_index = anchor_outcomes.index(o)
            ev_shin = fair["shin"][outcome_index] * offered - 1.0
            ev_power = fair["power"][outcome_index] * offered - 1.0
            ev_mult = fair["multiplicative"][outcome_index] * offered - 1.0
            ev_primary = {"shin": ev_shin, "power": ev_power, "multiplicative": ev_mult}[primary]
            edge_candidate = ev_primary >= edge_threshold
            survives = (ev_shin >= edge_threshold and
                        ev_power >= edge_threshold and
                        ev_mult >= edge_threshold)
            suspect = ev_primary > suspect_threshold
            actionable = (survives and not suspect and ev_primary >= actionable_threshold)

            quote = Edge(
                book=book, market_key=market_key, outcome=o,
                offered_odds=offered,
                sharp_fair_prob=fair[primary][outcome_index],
                ev_shin=ev_shin, ev_power=ev_power,
                ev_multiplicative=ev_mult,
                survives_all_methods=survives,
                suspect=suspect,
                book_tier="sharp" if book in sharp_books else "soft",
            ).to_dict()
            quote["edge_candidate"] = edge_candidate
            quote["actionable"] = actionable
            quote["qualifies"] = False
            quote["ev_band"] = ev_band(ev_primary)
            quotes.append(quote)
            if edge_candidate:
                edges.append(quote.copy())

    # 排序：稳健优先，非 suspect 优先，按 EV(shin)降序
    edges.sort(key=lambda e: (e["survives_all_methods"], not e["suspect"], e["ev_shin"]), reverse=True)
    quotes.sort(key=lambda q: q["ev_shin"], reverse=True)
    noise_edge_count = sum(1 for edge in edges if edge.get("ev_band") == "noise_lt_5pp")
    actionable_count = sum(1 for edge in edges if edge.get("actionable") is True)

    return {
        "status": "ok",
        "sharp_anchor": anchor,
        "sharp_overround": round(sharp_overround, 4),
        "market": market_key,
        "devig_primary": primary,
        "edge_threshold": edge_threshold,
        "actionable_threshold": actionable_threshold,
        "suspect_threshold": suspect_threshold,
        "books_scanned": len(market_data),
        "outcomes_scanned": anchor_outcomes,
        "quotes_scanned": len(quotes),
        "edge_count": len(edges),
        "noise_edge_count": noise_edge_count,
        "actionable_count": actionable_count,
        "raw_actionable_count": actionable_count,
        "relay_actionable_count": 0,
        "qualified_play_count": 0,
        "fair_probs": {
            m: {o: round(fair[m][i], 4) for i, o in enumerate(anchor_outcomes)}
            for m in DEVIG_METHODS
        },
        "quotes": quotes,
        "edges": edges,
    }


def build_summary(results: dict[str, Any]) -> dict[str, Any]:
    all_edges: list[dict[str, Any]] = []
    noise_edges: list[dict[str, Any]] = []
    actionable_edges: list[dict[str, Any]] = []
    quotes_scanned = 0
    for market_result in results.get("markets", {}).values():
        if not isinstance(market_result, dict):
            continue
        quotes_scanned += int(market_result.get("quotes_scanned") or 0)
        for edge in market_result.get("edges", []):
            if not isinstance(edge, dict):
                continue
            all_edges.append(edge)
            if edge.get("ev_band") == "noise_lt_5pp":
                noise_edges.append(edge)
            if edge.get("actionable") is True or edge.get("qualifies") is True:
                actionable_edges.append(edge)

    best_edge = max(all_edges, key=lambda e: e.get("ev_shin", -999), default=None)
    best_noise = max(noise_edges, key=lambda e: e.get("ev_shin", -999), default=None)
    best_actionable = max(actionable_edges, key=lambda e: e.get("ev_shin", -999), default=None)
    return {
        "markets_scanned": list(results.get("markets", {}).keys()),
        "quotes_scanned": quotes_scanned,
        "edge_count": len(all_edges),
        "noise_edge_count": len(noise_edges),
        "actionable_count": len(actionable_edges),
        "raw_actionable_count": len(actionable_edges),
        "relay_actionable_count": 0,
        "qualified_play_count": 0,
        "qualified_count": 0,
        "best_ev": best_edge.get("ev_shin") if best_edge else None,
        "best_edge": best_edge,
        "best_noise_edge": best_noise,
        "best_actionable_edge": best_actionable,
        "best_qualified_edge": None,
    }


# ── 主入口 ──

def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-book value scan (Path A)")
    parser.add_argument("--input-snapshot", required=True, help="Path to the-odds-api snapshot JSON")
    parser.add_argument("--output", required=True, help="Path to write cross-book scan artifact JSON")
    parser.add_argument("--match-home", help="Home team name (case-insensitive)")
    parser.add_argument("--match-away", help="Away team name (case-insensitive)")
    parser.add_argument("--edge-threshold", type=float, default=EDGE_THRESHOLD,
                        help=f"Minimum EV to consider (default: {EDGE_THRESHOLD})")
    parser.add_argument("--actionable-threshold", type=float, default=ACTIONABLE_EV_THRESHOLD,
                        help=f"Minimum EV to call actionable/qualified_play (default: {ACTIONABLE_EV_THRESHOLD})")
    parser.add_argument("--suspect-threshold", type=float, default=SUSPECT_THRESHOLD,
                        help=f"EV above this is suspect (default: {SUSPECT_THRESHOLD})")
    args = parser.parse_args()

    # Parse snapshot
    board = parse_odds_snapshot(args.input_snapshot,
                                match_home=args.match_home,
                                match_away=args.match_away)

    # Scan each market
    results: dict[str, Any] = {
        "artifact_type": "crossbook_scan",
        "artifact_kind": "cross_book_scan",
        "script": "cross_book_scan.py",
        "provides": ["path_a_crossbook"],
        "scan_timestamp_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "input_snapshot": args.input_snapshot,
        "source_snapshot_id": Path(args.input_snapshot).name,
        "match_home": args.match_home,
        "match_away": args.match_away,
        "edge_threshold": args.edge_threshold,
        "actionable_threshold": args.actionable_threshold,
        "suspect_threshold": args.suspect_threshold,
        "markets": {},
    }

    # h2h — outcomes are team names from snapshot, not "home/draw/away"
    h2h_board = board.get("h2h", {})
    if h2h_board:
        # Collect all unique outcome names from the h2h board
        h2h_outcomes: list[str] = []
        for bm_prices in h2h_board.values():
            for outcome_name in bm_prices:
                if outcome_name not in h2h_outcomes:
                    h2h_outcomes.append(outcome_name)
        if h2h_outcomes:
            h2h_result = scan_market(board, "h2h", h2h_outcomes,
                                     edge_threshold=args.edge_threshold,
                                     actionable_threshold=args.actionable_threshold,
                                     suspect_threshold=args.suspect_threshold)
            results["markets"]["h2h"] = h2h_result

    # spreads (AH) — outcomes are labels like "mexico@-1.25"
    spreads_board = board.get("spreads", {})
    if spreads_board:
        all_outcomes: list[str] = []
        for bm_prices in spreads_board.values():
            for label in bm_prices:
                if label not in all_outcomes:
                    all_outcomes.append(label)
        if all_outcomes:
            spread_result = scan_market(board, "spreads", all_outcomes,
                                        edge_threshold=args.edge_threshold,
                                        actionable_threshold=args.actionable_threshold,
                                        suspect_threshold=args.suspect_threshold)
            results["markets"]["spreads"] = spread_result

    # totals — same approach
    totals_board = board.get("totals", {})
    if totals_board:
        all_outcomes = []
        for bm_prices in totals_board.values():
            for label in bm_prices:
                if label not in all_outcomes:
                    all_outcomes.append(label)
        if all_outcomes:
            totals_result = scan_market(board, "totals", all_outcomes,
                                        edge_threshold=args.edge_threshold,
                                        actionable_threshold=args.actionable_threshold,
                                        suspect_threshold=args.suspect_threshold)
            results["markets"]["totals"] = totals_result

    results["summary"] = build_summary(results)

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
