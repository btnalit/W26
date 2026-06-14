#!/usr/bin/env python3
"""Deterministic World Cup group-stage motivation context.

This module is intentionally descriptive. It never adjusts p_adj and never emits
specific probability-point magnitudes. Its only job is to label when matchday-3
standings arithmetic creates a human-readable motivation context, then require a
separate market-reflection check before anything can be reviewed as a candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


CONTRACT = "wc26.motivation_context.v1"
DEFAULT_RULES: dict[str, Any] = {
    "competition": "FIFA World Cup 2026",
    "direct_slots": 2,
    "best_third_slots": 8,
    "third_place_policy": "external_cross_group_dependency",
    "tiebreakers": ["Pts", "GD", "GF", "fair_play"],
    "note": "Group-local deterministic ranking uses points, goal difference, goals for, then fair-play/drawing-lots fallback. Best-third qualification requires cross-group context and is treated as external unless supplied by caller.",
}
OUTCOMES = ("H", "D", "A")


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_standing(row: dict[str, Any]) -> dict[str, int]:
    gf = _int(row.get("GF"))
    ga = _int(row.get("GA"))
    gd = _int(row.get("GD"), gf - ga)
    return {
        "played": _int(row.get("played")),
        "W": _int(row.get("W")),
        "D": _int(row.get("D")),
        "L": _int(row.get("L")),
        "GF": gf,
        "GA": ga,
        "GD": gd,
        "Pts": _int(row.get("Pts")),
        "fair_play": _int(row.get("fair_play"), 0),
    }


def _apply_result(table: dict[str, dict[str, int]], home: str, away: str, outcome: str) -> None:
    table[home]["played"] += 1
    table[away]["played"] += 1
    if outcome == "H":
        home_goals, away_goals = 1, 0
        table[home]["W"] += 1
        table[away]["L"] += 1
        table[home]["Pts"] += 3
    elif outcome == "A":
        home_goals, away_goals = 0, 1
        table[away]["W"] += 1
        table[home]["L"] += 1
        table[away]["Pts"] += 3
    elif outcome == "D":
        home_goals, away_goals = 0, 0
        table[home]["D"] += 1
        table[away]["D"] += 1
        table[home]["Pts"] += 1
        table[away]["Pts"] += 1
    else:
        raise ValueError(f"unknown result outcome: {outcome}")

    table[home]["GF"] += home_goals
    table[home]["GA"] += away_goals
    table[away]["GF"] += away_goals
    table[away]["GA"] += home_goals
    table[home]["GD"] = table[home]["GF"] - table[home]["GA"]
    table[away]["GD"] = table[away]["GF"] - table[away]["GA"]


def _sort_key(item: tuple[str, dict[str, int]]) -> tuple[Any, ...]:
    team, row = item
    # Higher points/GD/GF first. Lower fair-play penalty is better. Team name is
    # deterministic drawing-lots fallback so tests and artifacts are stable.
    return (-row["Pts"], -row["GD"], -row["GF"], row.get("fair_play", 0), team)


def final_table_after_results(
    standings: dict[str, dict[str, Any]],
    fixtures: list[dict[str, str]],
    result_by_fixture: dict[int, str],
    advancement_rules: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return final sorted group table after applying deterministic W/D/L outcomes.

    Scorelines are intentionally minimal (1-0 / 0-0 / 0-1). The module answers
    qualification possibility/control questions, not exact goal-margin strategy.
    Boundary tests still exercise GD/GF ordering from the supplied standings.
    """

    table = {team: _normalize_standing(row) for team, row in standings.items()}
    for idx, fixture in enumerate(fixtures):
        outcome = result_by_fixture[idx]
        home = fixture["home"]
        away = fixture["away"]
        if home not in table or away not in table:
            raise ValueError(f"fixture team missing from standings: {home} vs {away}")
        _apply_result(table, home, away, outcome)

    ranked = []
    for rank, (team, row) in enumerate(sorted(table.items(), key=_sort_key), 1):
        ranked.append({"rank": rank, "team": team, **row})
    return ranked


def enumerate_result_branches(
    standings: dict[str, dict[str, Any]],
    fixtures: list[dict[str, str]],
    advancement_rules: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not fixtures:
        return []
    branches: list[dict[str, Any]] = []

    def walk(idx: int, results: dict[int, str]) -> None:
        if idx == len(fixtures):
            table = final_table_after_results(standings, fixtures, results, advancement_rules)
            branches.append({"results": dict(results), "table": table})
            return
        for outcome in OUTCOMES:
            results[idx] = outcome
            walk(idx + 1, results)
        results.pop(idx, None)

    walk(0, {})
    return branches


def _current_fixture_index(fixtures: list[dict[str, str]], home: str, away: str) -> int:
    for idx, fixture in enumerate(fixtures):
        if fixture.get("home") == home and fixture.get("away") == away:
            return idx
    for idx, fixture in enumerate(fixtures):
        if fixture.get("home") == away and fixture.get("away") == home:
            return idx
    raise ValueError(f"match_under_analysis not found in group_remaining_fixtures: {home} vs {away}")


def _advances(team: str, table: list[dict[str, Any]], rules: dict[str, Any]) -> bool:
    direct_slots = int(rules.get("direct_slots", 2))
    best_third_slots = int(rules.get("best_third_slots", 0))
    rank = next(row["rank"] for row in table if row["team"] == team)
    if rank <= direct_slots:
        return True
    # A caller may explicitly enable group-local third-place possibility for
    # scenario modelling. By default tests use 0 because real best-third needs
    # other groups and should not be silently promoted to certain qualification.
    return rank == direct_slots + 1 and best_third_slots > 0 and rules.get("treat_third_as_possible", False)


def _team_outcome_for_fixture(team: str, fixture: dict[str, str], fixture_outcome: str) -> str:
    is_home = fixture.get("home") == team
    if fixture_outcome == "D":
        return "D"
    if (fixture_outcome == "H" and is_home) or (fixture_outcome == "A" and not is_home):
        return "W"
    return "L"


def classify_team_motivation(team: str, branches: list[dict[str, Any]], current_fixture_idx: int, fixtures: list[dict[str, str]], rules: dict[str, Any]) -> dict[str, str]:
    by_result: dict[str, list[bool]] = {"W": [], "D": [], "L": []}
    fixture = fixtures[current_fixture_idx]
    for branch in branches:
        own_result = _team_outcome_for_fixture(team, fixture, branch["results"][current_fixture_idx])
        by_result[own_result].append(_advances(team, branch["table"], rules))

    possible = {key: any(values) for key, values in by_result.items()}
    guaranteed = {key: bool(values) and all(values) for key, values in by_result.items()}

    if all(guaranteed.values()):
        tag = "already_through"
        reason = "本场任何结果都能锁定出线，动机可能转向轮换/控风险"
    elif not any(possible.values()):
        tag = "dead_rubber"
        reason = "本场任何结果都无法进入出线区，数学上已出局"
    elif possible["W"] and not possible["D"] and not possible["L"]:
        tag = "must_win"
        reason = "只有赢球分支仍保留出线可能，平/负分支全部出局"
    elif guaranteed["W"] and guaranteed["D"] and not guaranteed["L"]:
        tag = "draw_enough"
        reason = "平局即可锁定出线，赢球更优但非必需"
    elif guaranteed["W"] and guaranteed["D"] and not possible["L"]:
        tag = "must_avoid_loss"
        reason = "赢或平都能出线，只有输球会失去出线位置"
    elif guaranteed["W"] and (not guaranteed["D"] or not guaranteed["L"]):
        tag = "win_to_control"
        reason = "赢球可自主锁定出线，平/负需要看另一场或净胜球"
    else:
        tag = "complex"
        reason = "出线路径依赖另一场结果及净胜球/进球数等多变量"

    return {"tag": tag, "reason": reason}


def _situation_from_team_tags(home: str, away: str, team_motivation: dict[str, dict[str, str]]) -> tuple[str, str, dict[str, str]]:
    htag = team_motivation[home]["tag"]
    atag = team_motivation[away]["tag"]
    tags = {htag, atag}

    if htag == atag == "draw_enough":
        return (
            "MUTUAL_DRAW_INCENTIVE",
            "high",
            {
                "draw_prob": "underestimated_by_model",
                "direction": "draw↑ / total↓ / cover↓",
                "magnitude": "qualitative_only",
                "note_zh": "两队平局皆可出线，实际平局倾向可能高于模型；历史上此情境有默契风险。",
            },
        )
    if "already_through" in tags and ("must_win" in tags or "win_to_control" in tags):
        return (
            "ROTATION_VS_DESPERATION",
            "medium",
            {
                "cover": "favorite_cover_risk_if_through_side_rotates",
                "direction": "through_side_cover↓ / total↑ / upset↑",
                "magnitude": "qualitative_only",
                "note_zh": "已出线方可能轮换，需求方更可能提高进攻强度。",
            },
        )
    if "dead_rubber" in tags and ("must_win" in tags or "win_to_control" in tags):
        return (
            "DEAD_RUBBER_ASYMMETRY",
            "high",
            {
                "cover": "demand_side_cover_supported",
                "direction": "demand_side_cover↑",
                "magnitude": "qualitative_only",
                "note_zh": "出局方动机缺失，需求方动机充足。",
            },
        )
    if htag == atag == "must_win":
        return (
            "MUTUAL_DESPERATION",
            "medium",
            {
                "draw_prob": "overestimated_by_model",
                "direction": "draw↓ / total↑ / BTTS↑",
                "magnitude": "qualitative_only",
                "note_zh": "两队皆需取胜，比赛可能更开放。",
            },
        )
    return (
        "COMPLEX_MOTIVATION" if any(tag != "complex" for tag in tags) else "COMPLEX",
        "low",
        {
            "direction": "qualitative_review_only",
            "magnitude": "qualitative_only",
            "note_zh": "动机路径非单变量，必须人工结合盘口对账。",
        },
    )


def _none_artifact(match: dict[str, Any], reason: str) -> dict[str, Any]:
    home = match.get("home")
    away = match.get("away")
    return {
        "artifact_field": "motivation_context",
        "contract": CONTRACT,
        "matchday": match.get("matchday"),
        "status": "none",
        "match": f"{home} vs {away}" if home and away else None,
        "standings_snapshot_id": None,
        "team_motivation": {},
        "situation_tag": "NONE",
        "priority": "none",
        "model_hint": {"direction": "none", "magnitude": "qualitative_only"},
        "footnote_zh": reason,
        "market_reflection_check": None,
        "ledger_ref": None,
    }


def analyze_motivation_context(
    standings: dict[str, dict[str, Any]] | None,
    group_remaining_fixtures: list[dict[str, str]] | None,
    match_under_analysis: dict[str, Any],
    advancement_rules: dict[str, Any] | None = None,
    market_reflection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fixed-schema motivation artifact for one match.

    matchday is taken from match_under_analysis, which should be populated from
    the fixture registry. Callers must not infer it from CLI/window text.
    """

    matchday = match_under_analysis.get("matchday")
    if matchday is None:
        return _none_artifact(match_under_analysis, "fixture 未提供真实 matchday，动机模块 fail-closed 为 NONE。")
    if int(matchday) < 3:
        return _none_artifact(match_under_analysis, "首轮/次轮或积分未分化，无动机变量，纯实力盘。")
    if not standings or not group_remaining_fixtures:
        artifact = _none_artifact(match_under_analysis, "第三轮但缺少积分表或剩余赛程，无法确定性计算。")
        artifact["status"] = "insufficient_data"
        return artifact

    rules = deepcopy(DEFAULT_RULES)
    if advancement_rules:
        rules.update(advancement_rules)
    home = str(match_under_analysis["home"])
    away = str(match_under_analysis["away"])
    current_idx = _current_fixture_index(group_remaining_fixtures, home, away)
    branches = enumerate_result_branches(standings, group_remaining_fixtures, rules)
    team_motivation = {
        home: classify_team_motivation(home, branches, current_idx, group_remaining_fixtures, rules),
        away: classify_team_motivation(away, branches, current_idx, group_remaining_fixtures, rules),
    }
    situation_tag, priority, model_hint = _situation_from_team_tags(home, away, team_motivation)
    snapshot_id = "standings:" + _stable_hash({"standings": standings, "fixtures": group_remaining_fixtures})
    ledger_ref = "motivation-ledger-" + _stable_hash({"match": match_under_analysis, "snapshot": snapshot_id})[:8]

    return {
        "artifact_field": "motivation_context",
        "contract": CONTRACT,
        "matchday": int(matchday),
        "status": "active",
        "match": f"{home} vs {away}",
        "standings_snapshot_id": snapshot_id,
        "team_motivation": team_motivation,
        "situation_tag": situation_tag,
        "priority": priority,
        "model_hint": model_hint,
        "footnote_zh": "动机情境·描述性·非下注信号；市场大概率已定价，仅当软盘未反映时才可能成缝。",
        "market_reflection_check": market_reflection,
        "ledger_ref": ledger_ref,
    }


def market_reflection_check(
    direction: str,
    market_profile: dict[str, Any],
    neutral_baseline: dict[str, Any],
    path_a_scan: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Qualitative price-reflection gate.

    This deliberately accepts qualitative states instead of emitting probability
    deltas. The only reviewable path is: motivation direction + market not
    reflected + soft-vs-sharp lag + 5% Path-A gate.
    """

    path_a_scan = path_a_scan or {}
    market_state = str(market_profile.get("draw_prob_state") or market_profile.get("state") or "unknown")
    baseline_state = str(neutral_baseline.get("draw_prob_state") or neutral_baseline.get("state") or "unknown")
    direction_points_draw = "draw" in direction.lower() or "平" in direction or "draw↑" in direction

    if direction_points_draw and market_state in {"elevated_vs_baseline", "high", "priced_for_draw"}:
        status = "priced_in"
    elif market_state == baseline_state or market_state in {"normal", "neutral", "unknown"}:
        status = "potential_gap"
    else:
        status = "priced_in"

    soft_lag = bool(path_a_scan.get("soft_vs_sharp_lag"))
    passes_gate = bool(path_a_scan.get("passes_5pct_gate"))
    if status == "potential_gap" and soft_lag and passes_gate:
        decision = "MANUAL_REVIEW_CANDIDATE"
        actionability = "supports_path_a_review"
    else:
        decision = "NO_PLAY"
        actionability = "diagnostic_only"

    return {
        "status": status,
        "decision": decision,
        "actionability": actionability,
        "requires": "motivation_direction + unpriced_market + soft_vs_sharp_lag + path_a_5pct_gate",
    }


def ledger_entry(artifact: dict[str, Any], actual_result: str | None = None) -> dict[str, Any]:
    direction = artifact.get("model_hint", {}).get("direction", "none")
    reflection = artifact.get("market_reflection_check") or {}
    return {
        "match": artifact.get("match"),
        "situation_tag": artifact.get("situation_tag"),
        "model_hint_direction": direction,
        "market_reflection": reflection.get("status"),
        "actual_result": actual_result,
        "actual_was_draw": actual_result == "D" if actual_result is not None else None,
        "outcome_agrees": None,
        "gap_was_real": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--standings", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--match", type=Path, required=True)
    parser.add_argument("--rules", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    rules = json.loads(args.rules.read_text(encoding="utf-8")) if args.rules else DEFAULT_RULES
    artifact = analyze_motivation_context(
        standings=json.loads(args.standings.read_text(encoding="utf-8")),
        group_remaining_fixtures=json.loads(args.fixtures.read_text(encoding="utf-8")),
        match_under_analysis=json.loads(args.match.read_text(encoding="utf-8")),
        advancement_rules=rules,
    )
    rendered = json.dumps(artifact, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
