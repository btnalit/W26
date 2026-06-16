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
) -> list[dict[str, Any]]:
    """Score all claims from manifest artifacts against settled result.

    Each dimension artifact with a scoring_claim is judged and recorded.
    Returns list of new ledger records (does not write to disk — caller handles persistence).
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
        if not claim.get("scorable", True):
            records.append({
                "match_id": match_id,
                "dimension": dimension,
                "claim_type": str(claim.get("claim_type") or ""),
                "verdict": "not_scorable",
                "scored_at_utc": scored_at,
                "record_id": stable_record_id(match_id, dimension, str(claim.get("claim_type") or "")),
            })
            continue
        verdict = judge(claim, settled_result)
        records.append({
            "match_id": match_id,
            "dimension": dimension,
            "claim_type": str(claim.get("claim_type") or ""),
            "directional_statement": str(claim.get("directional_statement") or ""),
            "verdict": verdict,
            "scored_at_utc": scored_at,
            "record_id": stable_record_id(match_id, dimension, str(claim.get("claim_type") or "")),
        })
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
