#!/usr/bin/env python3
"""Dimension scorekeeper: post-match claim adjudication.

Pure observer layer. Never affects p_adj, edge, gate, or betting decisions.
Judges each dimension's scoring_claim against the settled result and writes
a dimension_score_ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTRACT = "wc26.dimension_scorer.v1"
LEDGER_SCHEMA_VERSION = "wc26.dimension_score_ledger.v1"

# ── claim_type → judge function mapping ──

def judge_favorite_protected(claim: dict[str, Any], result: dict[str, Any]) -> str:
    fav_covered = result.get("favorite_covered_main_handicap")
    if fav_covered is None:
        return "not_applicable"
    return "hit" if fav_covered is True else "miss"


def judge_trap_on_side_x(claim: dict[str, Any], result: dict[str, Any]) -> str:
    trap_side = str(claim.get("trap_side") or claim.get("directional_statement") or "").lower()
    if not trap_side:
        return "not_applicable"
    actual_outcome = str(result.get("actual_outcome") or "").lower()
    margin = result.get("actual_margin")
    if margin is None:
        return "not_applicable"
    # trap on side X means the market lured people onto X, but X failed
    # "X方实际未达盘" → if actual_outcome != trap_side or margin <= 0 for home
    if trap_side == "home":
        trap_failed = actual_outcome != "home"
    elif trap_side == "away":
        trap_failed = actual_outcome != "away"
    else:
        return "not_applicable"
    return "hit" if trap_failed else "miss"


def judge_market_efficient(claim: dict[str, Any], result: dict[str, Any]) -> str:
    # "market_efficient" → no obvious mispricing; check if actual falls in top prob area
    top_probs = result.get("market_top_probs")
    actual_outcome = str(result.get("actual_outcome") or "")
    if not top_probs or not actual_outcome:
        return "not_applicable"
    if actual_outcome in top_probs:
        return "hit"
    # if not in top probs but the claim is market was efficient, it's ambiguous
    # single-match efficiency hard to falsify; mark not_applicable for tight claims
    return "not_applicable"


def judge_retail_overload_side(claim: dict[str, Any], result: dict[str, Any]) -> str:
    overload_side = str(claim.get("overload_side") or "").lower()
    if not overload_side:
        return "not_applicable"
    # "retail_overload_side_X" → X performed worse than market pricing
    # proxy: market had X as favorite but X lost/drew
    fav = str(result.get("favorite_side") or "").lower()
    if overload_side != fav:
        return "not_applicable"  # only meaningful when overload side was the favorite
    fav_covered = result.get("favorite_covered_main_handicap")
    if fav_covered is None:
        return "not_applicable"
    return "hit" if fav_covered is False else "miss"


def judge_profile_lean_discounted(claim: dict[str, Any], result: dict[str, Any]) -> str:
    # bias_mirror: "画像偏Over被打折 → 实际Under ? hit"
    direction = str(claim.get("directional_statement") or "").lower()
    if "over" in direction and "under" in direction:
        # profile leans Over but discounted → hit if actual Under
        over25 = result.get("actual_over25")
        if over25 is None:
            return "not_applicable"
        return "hit" if over25 is False else "miss"
    if "under" in direction and "over" in direction:
        over25 = result.get("actual_over25")
        if over25 is None:
            return "not_applicable"
        return "hit" if over25 is True else "miss"
    return "not_applicable"


def judge_mutual_draw_incentive(claim: dict[str, Any], result: dict[str, Any]) -> str:
    actual_outcome = str(result.get("actual_outcome") or "")
    if not actual_outcome:
        return "not_applicable"
    return "hit" if actual_outcome == "draw" else "miss"


def judge_rotation_vs_desperation(claim: dict[str, Any], result: dict[str, Any]) -> str:
    # "强队是否如预期降档(让球未穿) ? hit"
    fav_covered = result.get("favorite_covered_main_handicap")
    if fav_covered is None:
        return "not_applicable"
    return "hit" if fav_covered is False else "miss"


def judge_mutual_desperation(claim: dict[str, Any], result: dict[str, Any]) -> str:
    actual_outcome = str(result.get("actual_outcome") or "")
    total = result.get("actual_total_goals")
    if not actual_outcome or total is None:
        return "not_applicable"
    # "非平局/进球数高" → hit if non-draw or high goals
    non_draw = actual_outcome != "draw"
    high_goals = total > 2.5
    return "hit" if (non_draw or high_goals) else "miss"


JUDGERS = {
    "favorite_protected": judge_favorite_protected,
    "trap_on_side_X": judge_trap_on_side_x,
    "market_efficient": judge_market_efficient,
    "retail_overload_side_X": judge_retail_overload_side,
    "profile_lean_discounted": judge_profile_lean_discounted,
    "mutual_draw_incentive": judge_mutual_draw_incentive,
    "rotation_vs_desperation": judge_rotation_vs_desperation,
    "mutual_desperation": judge_mutual_desperation,
}

# ── Scoring entry points ──

def judge(claim: dict[str, Any], settled_result: dict[str, Any]) -> str:
    """Pure function. Determines hit/miss/not_applicable from claim + result only.

    Never reads dimension self-confidence or any internal state.
    """
    if not isinstance(claim, dict) or not claim.get("scorable"):
        return "not_scorable"
    claim_type = str(claim.get("claim_type") or "").strip()
    if not claim_type:
        return "not_applicable"
    judger = JUDGERS.get(claim_type)
    if judger is None:
        return "not_applicable"
    verdict = judger(claim, settled_result)
    if verdict not in ("hit", "miss", "not_applicable"):
        return "not_applicable"
    return verdict


def stable_record_id(match_id: str, dimension: str, claim_type: str) -> str:
    raw = f"{match_id}:{dimension}:{claim_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def score_dimensions(
    match_id: str,
    settled_result: dict[str, Any],
    manifest_artifacts: dict[str, Any],
    ledger_path: Path | None = None,
    strength_gap: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Score all claims from manifest artifacts against settled result.

    Each dimension artifact with a scoring_claim is judged and recorded.
    Returns list of new ledger records (does not write to disk — caller handles persistence).

    If strength_gap is provided, each record is tagged with it for later
    stratification (strength-gap-spec v1).
    """
    records: list[dict[str, Any]] = []
    scored_at = datetime.now(timezone.utc).isoformat()

    # Collect artifacts that may have scoring_claims
    # The manifest_artifacts dict may have: "role_engine", "bias_mirror",
    # "motivation_context", "no_play_classification"
    for dimension, artifact in manifest_artifacts.items():
        if not isinstance(artifact, dict):
            continue
        claim = artifact.get("scoring_claim") if isinstance(artifact.get("scoring_claim"), dict) else None
        if not claim:
            continue
        base_record: dict[str, Any] = {
            "match_id": match_id,
            "dimension": dimension,
            "claim_type": str(claim.get("claim_type") or ""),
            "scored_at_utc": scored_at,
            "record_id": stable_record_id(match_id, dimension, str(claim.get("claim_type") or "")),
        }
        if strength_gap:
            base_record["strength_gap"] = strength_gap
        if not claim.get("scorable", True):
            base_record["verdict"] = "not_scorable"
            records.append(base_record)
            continue
        verdict = judge(claim, settled_result)
        base_record["verdict"] = verdict
        base_record["directional_statement"] = str(claim.get("directional_statement") or "")
        records.append(base_record)
    return records


def write_ledger_records(records: list[dict[str, Any]], ledger_path: Path) -> int:
    """Write dimension score records to ledger, respecting idempotency.

    Returns number of new records written.
    """
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, dict[str, Any]] = {}
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if isinstance(ledger, dict):
            for rec in ledger.get("records", []):
                if isinstance(rec, dict) and rec.get("record_id"):
                    existing[str(rec["record_id"])] = rec
    else:
        ledger = {"schema_version": LEDGER_SCHEMA_VERSION, "records": []}

    written = 0
    for rec in records:
        rid = str(rec.get("record_id") or "")
        if rid and rid in existing:
            continue  # idempotent: skip already scored
        ledger["records"].append(rec)
        existing[rid] = rec
        written += 1

    if written > 0:
        ledger_path.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return written


def load_ledger(ledger_path: Path) -> dict[str, Any]:
    if not ledger_path.exists():
        return {"schema_version": LEDGER_SCHEMA_VERSION, "records": []}
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {"schema_version": LEDGER_SCHEMA_VERSION, "records": []}


# ── Strength-gap computation (strength-gap-spec v1) ──

def _load_shin_no_vig_from_snapshot(
    snapshot_path: Path,
    home: str,
    away: str,
) -> tuple[float, float, str | None] | None:
    """Extract Pinnacle Shin no-vig probabilities for a match from an odds snapshot.

    Returns (p_home, p_away, snapshot_id) or None if not found.
    Uses the snapshot's own Pinnacle data — does not call paid APIs.
    """
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    # Support both list-of-matches and {data: [matches]} formats
    matches = payload if isinstance(payload, list) else payload.get("data", [])
    home_lower = home.strip().lower()
    away_lower = away.strip().lower()
    for om in matches:
        if not isinstance(om, dict):
            continue
        om_home = str(om.get("home_team") or om.get("homeTeam", {}).get("name", "") or "").strip().lower()
        om_away = str(om.get("away_team") or om.get("awayTeam", {}).get("name", "") or "").strip().lower()
        if om_home != home_lower or om_away != away_lower:
            continue
        bookmakers = om.get("bookmakers", [])
        for bk in bookmakers:
            if not isinstance(bk, dict) or bk.get("key") != "pinnacle":
                continue
            for market in bk.get("markets", []):
                if not isinstance(market, dict):
                    continue
                if market.get("key") not in ("h2h", "1x2"):
                    continue
                outcomes = market.get("outcomes", [])
                if len(outcomes) < 3:
                    continue
                # Collect decimal prices
                prices: list[float] = []
                for oc in outcomes:
                    price = oc.get("price", 0)
                    if isinstance(price, (int, float)) and price > 1.0:
                        prices.append(float(price))
                if len(prices) < 3:
                    continue
                # Compute Shin no-vig
                try:
                    no_vig = devig_shin(prices)
                    if len(no_vig) >= 3:
                        snapshot_id = str(snapshot_path.name)
                        return (no_vig[0], no_vig[1], snapshot_id)
                except Exception:
                    continue
    return None


def _tier_for_gap(gap: float, config: dict[str, Any]) -> str:
    """Map a gap value to a tier based on config boundaries.

    even:     gap <  max (exclusive upper bound)
    moderate: min <= gap < max (inclusive lower, exclusive upper)
    lopsided: gap >= min (inclusive lower bound)
    """
    tiers = config.get("tiers", {})
    # Find "even" tier: gap < max
    even_bounds = tiers.get("even")
    if isinstance(even_bounds, dict) and gap < even_bounds.get("max", 0.20):
        return "even"
    # Find "moderate" tier: min <= gap < max
    mod_bounds = tiers.get("moderate")
    if isinstance(mod_bounds, dict):
        lo = mod_bounds.get("min", 0.20)
        hi = mod_bounds.get("max", 0.50)
        if lo <= gap < hi:
            return "moderate"
    # Find "lopsided" tier: gap >= min
    lop_bounds = tiers.get("lopsided")
    if isinstance(lop_bounds, dict) and gap >= lop_bounds.get("min", 0.50):
        return "lopsided"
    return "unknown"


def compute_strength_gap(
    snapshot_path: Path,
    home: str,
    away: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Compute match strength-gap from opening market snapshot.

    Uses Pinnacle 1X2 Shin no-vig from the snapshot:
        p_fav = max(p_home, p_away)
        p_dog = min(p_home, p_away)
        gap   = p_fav - p_dog

    Never uses FIFA ranks, Elo, or any subjective input.
    Returns None if the match/snapshot is unavailable (tier: "unknown").
    """
    if config is None:
        # Load default config
        import os as _os
        candidates = [
            Path(_os.environ.get("WC26_CONFIG_DIR", "")) / "strength-gap-config.json",
            Path(__file__).resolve().parent.parent.parent.parent / "config" / "strength-gap-config.json",
        ]
        for c in candidates:
            if c.exists():
                config = json.loads(c.read_text(encoding="utf-8"))
                break
        if config is None:
            config = {"tiers": {"even": {"max": 0.20}, "moderate": {"min": 0.20, "max": 0.50}, "lopsided": {"min": 0.50}}}

    result = _load_shin_no_vig_from_snapshot(snapshot_path, home, away)
    if result is None:
        return {
            "gap_value": None,
            "tier": "unknown",
            "p_fav": None,
            "p_away_or_dog": None,
            "favorite_side": None,
            "opening_snapshot_id": str(snapshot_path.name),
            "boundary_config_version": str(config.get("boundary_version", "v1")),
            "missing_reason": "pinnacle_h2h_not_found_in_snapshot",
        }

    p_home, p_away, snapshot_id = result
    p_fav = max(p_home, p_away)
    p_dog = min(p_home, p_away)
    gap = round(p_fav - p_dog, 6)
    favorite_side = "home" if p_home >= p_away else "away"
    tier = _tier_for_gap(gap, config)

    return {
        "gap_value": gap,
        "tier": tier,
        "p_fav": round(p_fav, 4),
        "p_dog": round(p_dog, 4),
        "favorite_side": favorite_side,
        "opening_snapshot_id": snapshot_id,
        "boundary_config_version": str(config.get("boundary_version", "v1")),
    }


# Import devig_shin locally to avoid circular imports at module load
def _get_devig_shin():
    try:
        from devig import devig_shin
        return devig_shin
    except ImportError:
        # Use the inline implementation as fallback
        return _inline_devig_shin


def _inline_devig_shin(decimal_odds):
    """Minimal Shin no-vig implementation for standalone use."""
    import math as _math
    odds = [float(o) for o in decimal_odds]
    n = len(odds)
    if n < 2:
        return [1.0 / o if o > 0 else 0.0 for o in odds]
    implied = [1.0 / o for o in odds]
    overround = sum(implied) - 1.0
    if overround <= 0:
        return [p / sum(implied) if sum(implied) > 0 else 1.0 / n for p in implied]
    # Iterative Shin solver
    z = 0.0
    for _ in range(50):
        total = 0.0
        for o_i in odds:
            if o_i <= 0:
                continue
            total += _math.sqrt(z * z + 4 * (1 - z) * (1.0 / o_i) ** 2)
        new_z = (total - 2) / (n - 2) if n > 2 else 0.0
        new_z = max(0.0, min(0.5, new_z))
        if abs(new_z - z) < 1e-12:
            z = new_z
            break
        z = new_z
    probs = []
    for o_i in odds:
        if o_i <= 0:
            probs.append(0.0)
        else:
            val = (_math.sqrt(z * z + 4 * (1 - z) * (1.0 / o_i) ** 2) - z) / (2 * (1 - z))
            probs.append(val)
    total = sum(probs)
    return [p / total if total > 0 else 1.0 / n for p in probs]


# Use inline Shin to avoid circular import
devig_shin = _inline_devig_shin


# ── CLI ──

def main() -> int:
    parser = argparse.ArgumentParser(description="Dimension scorekeeper: post-match claim adjudication")
    parser.add_argument("--match-id", required=True, help="Canonical match identifier")
    parser.add_argument("--settled-result", type=Path, required=True,
                        help="JSON with settled_result fields")
    parser.add_argument("--manifest-artifacts", type=Path, required=True,
                        help="JSON with dimension:artifact mapping")
    parser.add_argument("--ledger", type=Path,
                        help="Path to dimension_score_ledger JSON")
    parser.add_argument("--output", type=Path,
                        help="Write new records to this path (stdout if omitted)")
    args = parser.parse_args()

    result = json.loads(args.settled_result.read_text(encoding="utf-8"))
    artifacts = json.loads(args.manifest_artifacts.read_text(encoding="utf-8"))

    records = score_dimensions(args.match_id, result, artifacts)

    if args.ledger:
        written = write_ledger_records(records, args.ledger)
        print(f"Ledger updated: {written} new records")
    elif args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps({"records": records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        print(json.dumps({"records": records}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
