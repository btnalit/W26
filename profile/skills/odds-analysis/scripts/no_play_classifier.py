#!/usr/bin/env python3
"""NO PLAY dual-track classifier for WC26 reflection layer.

Diagnostic only: classifies why a non-play outcome stayed non-play. It never
creates a playable recommendation.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

NON_PLAY_STATUSES = {"no_play", "watch", "pass_incomplete", "pass", "simulation_only"}
NO_DIRECTION_TOKENS = {"", "none", "no_play", "no_play_watch", "watch", "无方向", "空过", "n/a", "null"}


def _final_status(inputs: dict[str, Any]) -> str:
    return str(inputs.get("final_status") or inputs.get("status") or "").strip().lower()


def _relay_actionable(inputs: dict[str, Any]) -> int:
    for key in ("relay_actionable", "qualified_play_count", "actionable_count"):
        value = inputs.get(key)
        try:
            if value is not None:
                return int(value)
        except Exception:
            continue
    return 0


def _direction_from_deep_research(deep_research: dict[str, Any] | None) -> str:
    payload = deep_research or {}
    final_view = payload.get("final_view") if isinstance(payload.get("final_view"), dict) else payload
    if not isinstance(final_view, dict):
        return ""
    for key in ("betting_direction", "direction", "recommended_direction"):
        value = final_view.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def has_direction(direction: str) -> bool:
    low = direction.strip().lower()
    return low not in NO_DIRECTION_TOKENS


def classify_no_play(
    final_inputs: dict[str, Any],
    deep_research: dict[str, Any] | None = None,
    path_c: dict[str, Any] | None = None,
    role_engine: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    status = _final_status(final_inputs or {})
    actionable = _relay_actionable(final_inputs or {})
    if status not in NON_PLAY_STATUSES or actionable > 0:
        return None
    direction = _direction_from_deep_research(deep_research)
    if not has_direction(direction):
        kind = "true_pass"
        rationale = "DR未给出明确方向且执行层无价格 → 正确空过"
        direction_out = None
    else:
        kind = "directional_blocked"
        rationale = f"DR给出明确方向({direction}),但Path A零可执行价格 → 看对方向缺执行面"
        direction_out = direction
    result = {
        "artifact_field": "no_play_classification",
        "contract": "wc26.no_play_classification.v1",
        "type": kind,
        "direction_if_any": direction_out,
        "rationale_zh": rationale,
        "post_result_direction_hit": None,
        "footnote_zh": "NO PLAY分类·描述性;directional_blocked 表示分析层有方向、执行层无价格,非下注信号。",
    }
    # scoring_claim tail: only when directional_blocked
    if kind == "directional_blocked" and direction_out:
        # Determine claim_type from direction
        dir_low = direction_out.lower()
        if "under" in dir_low:
            ct = "retail_overload_side_X"  # directional_blocked = market pricing not aligned with direction
        elif "over" in dir_low:
            ct = "retail_overload_side_X"
        elif "受让" in direction_out or "dog" in dir_low:
            ct = "trap_on_side_X"
        else:
            ct = "retail_overload_side_X"
        result["scoring_claim"] = {
            "dimension": "no_play_classifier",
            "claim_type": ct,
            "directional_statement": direction_out,
            "falsifiable_by": "post_result_direction_hit",
            "scorable": True,
            "post_result_verdict": None,
        }
    return result


def backfill_direction_hit(classification: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(classification)
    if updated.get("type") != "directional_blocked":
        updated["post_result_direction_hit"] = None
        return updated
    direction = str(updated.get("direction_if_any") or "")
    try:
        home_score = int(result.get("home_score"))
        away_score = int(result.get("away_score"))
    except Exception:
        updated["post_result_direction_hit"] = None
        return updated
    total = home_score + away_score
    hit: bool | None = None
    low = direction.lower()
    if "under" in low:
        line = _extract_number_after(direction, "Under")
        if line is not None:
            hit = total < line
    elif "over" in low:
        line = _extract_number_after(direction, "Over")
        if line is not None:
            hit = total > line
    elif "不败" in direction:
        team = str(result.get("team") or result.get("side") or "").lower()
        if team == "home":
            hit = home_score >= away_score
        elif team == "away":
            hit = away_score >= home_score
    elif "受让" in direction:
        line = _extract_number_after(direction, "+")
        # If no explicit handicap exists, use underdog-not-losing as conservative proxy.
        if line is None:
            hit = home_score <= away_score or away_score <= home_score
        else:
            side = str(result.get("side") or "away").lower()
            margin = (home_score - away_score) if side == "home" else (away_score - home_score)
            hit = margin + abs(line) >= 0
    updated["post_result_direction_hit"] = hit
    return updated


def _extract_number_after(text: str, marker: str) -> float | None:
    import re
    if marker == "+":
        match = re.search(r"\+\s*(\d+(?:\.\d+)?)", text)
    else:
        match = re.search(marker + r"\s*(\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None
