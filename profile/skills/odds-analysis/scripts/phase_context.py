#!/usr/bin/env python3
"""WC26 phase context artifact.

Deterministic reflection layer module. It reads fixture matchday/stage and
settled grading ledger, then emits a descriptive phase prior. No market numbers
are modified.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ledger_reflection = load_module("ledger_reflection", SCRIPT_DIR / "ledger_reflection.py")
motivation_context = load_module("motivation_context_for_phase", SCRIPT_DIR / "motivation_context.py")


def determine_phase(match_under_analysis: dict[str, Any]) -> str:
    stage = str(match_under_analysis.get("stage") or "").upper()
    matchday = match_under_analysis.get("matchday")
    try:
        md = int(matchday)
    except Exception:
        md = None
    if stage and stage != "GROUP_STAGE":
        return "knockout"
    if md == 1:
        return "opener"
    if md == 2:
        return "group_mid"
    if md == 3:
        return "group_final"
    return "unknown"


def analyze_phase_context(
    match_under_analysis: dict[str, Any],
    settled_ledger: list[dict[str, Any]] | None,
    standings: Any = None,
    group_remaining_fixtures: Any = None,
    advancement_rules: Any = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    phase = determine_phase(match_under_analysis or {})
    try:
        matchday = int((match_under_analysis or {}).get("matchday"))
    except Exception:
        matchday = None
    priors = ledger_reflection.ledger_phase_stats(phase, settled_ledger or [], config=config)
    motivation = None
    if phase == "group_final":
        motivation = motivation_context.analyze_motivation_context(
            standings=standings,
            group_remaining_fixtures=group_remaining_fixtures,
            match_under_analysis=match_under_analysis or {},
            advancement_rules=advancement_rules,
        )
    return {
        "artifact_field": "phase_context",
        "contract": "wc26.phase_context.v1",
        "phase": phase,
        "matchday": matchday,
        "phase_priors": priors,
        "motivation_context": motivation,
        "footnote_zh": "阶段先验·基于台账实时回算·描述性·非下注信号;样本不足时倾向不可依赖。",
    }
