from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "odds-analysis" / "scripts"


def load_module(name: str, filename: str):
    path = SCRIPTS / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_ledger(n: int, phase: str = "opener", over_count: int = 3) -> list[dict]:
    rows = []
    for i in range(n):
        rows.append(
            {
                "match_id": f"M{i+1:03d}",
                "phase": phase,
                "actual_total_goals": 3 if i < over_count else 1,
                "actual_over25": i < over_count,
                "market_over25_implied": 0.49,
                "favorite_side": "home",
                "favorite_covered_main_handicap": i % 3 == 0,
            }
        )
    return rows


def test_ledger_phase_stats_three_state_bias_direction() -> None:
    ledger_reflection = load_module("ledger_reflection_test", "ledger_reflection.py")

    under = ledger_reflection.ledger_phase_stats("opener", sample_ledger(8, over_count=2))
    assert under["total_goals"]["ledger_value"] == 0.25
    assert under["total_goals"]["market_implied_avg"] == 0.49
    assert under["total_goals"]["bias_direction"] == "market_over_actual_under"
    assert under["total_goals"]["sample_n"] == 8
    assert under["total_goals"]["confidence"] == "provisional_low_n"

    aligned_rows = sample_ledger(12, over_count=6)
    aligned = ledger_reflection.ledger_phase_stats("opener", aligned_rows)
    assert aligned["total_goals"]["bias_direction"] == "aligned"
    assert aligned["total_goals"]["confidence"] == "emerging"

    over_rows = sample_ledger(30, over_count=24)
    over = ledger_reflection.ledger_phase_stats("opener", over_rows)
    assert over["total_goals"]["bias_direction"] == "market_under_actual_over"
    assert over["total_goals"]["confidence"] == "established"


def test_strategic_signal_insufficient_n_no_interpretation() -> None:
    ledger_reflection = load_module("ledger_reflection_test", "ledger_reflection.py")
    ledger = [{"no_play_type": "directional_blocked", "post_result_direction_hit": True} for _ in range(3)]

    signal = ledger_reflection.strategic_signal(ledger)

    assert signal["directional_blocked_count"] == 3
    assert signal["sample_n"] == 3
    assert "样本不足" in signal["interpretation"]
    assert signal["disclaimer_zh"].startswith("此为系统自我评估")


def test_strategic_signal_high_hitrate_flags_execution_bottleneck() -> None:
    ledger_reflection = load_module("ledger_reflection_test", "ledger_reflection.py")
    ledger = [
        {"no_play_type": "directional_blocked", "post_result_direction_hit": i < 12}
        for i in range(20)
    ]

    signal = ledger_reflection.strategic_signal(ledger)

    assert signal["directional_blocked_count"] == 20
    assert signal["direction_hit_rate"] == 0.6
    assert "执行面" in signal["interpretation"]


def test_phase_opener_md1_and_prior_recomputed_from_ledger() -> None:
    phase_context = load_module("phase_context_test", "phase_context.py")
    fixture = {"matchday": 1, "stage": "GROUP_STAGE", "group": "GROUP_A", "match_id": "M001"}

    opener = phase_context.analyze_phase_context(fixture, sample_ledger(8, over_count=2))
    changed = phase_context.analyze_phase_context(fixture, sample_ledger(8, over_count=7))

    assert opener["contract"] == "wc26.phase_context.v1"
    assert opener["phase"] == "opener"
    assert opener["phase_priors"]["total_goals"]["ledger_value"] == 0.25
    assert opener["phase_priors"]["total_goals"]["confidence"] == "provisional_low_n"
    assert changed["phase_priors"]["total_goals"]["ledger_value"] == 0.875
    assert changed["phase_priors"]["total_goals"]["bias_direction"] == "market_under_actual_over"


def test_phase_final_md3_attaches_motivation() -> None:
    phase_context = load_module("phase_context_test", "phase_context.py")
    fixture = {"matchday": 3, "stage": "GROUP_STAGE", "group": "GROUP_A", "match_id": "M003", "home": "Alpha", "away": "Beta"}

    artifact = phase_context.analyze_phase_context(fixture, [], standings=[], group_remaining_fixtures=[], advancement_rules={})

    assert artifact["phase"] == "group_final"
    assert isinstance(artifact["motivation_context"], dict)
    assert artifact["motivation_context"]["contract"] == "wc26.motivation_context.v1"


def test_mirror_contradicts_when_profile_over_phase_under_and_does_not_mutate_profile() -> None:
    bias_mirror = load_module("bias_mirror_test", "bias_mirror.py")
    profile = {"total_line_lean": {"lean": "over", "label": "Over 2.5", "over_pct": 50.6}}
    before = json.dumps(profile, sort_keys=True, ensure_ascii=False)
    phase = {"phase_priors": {"total_goals": {"bias_direction": "market_over_actual_under", "ledger_value": 0.25, "sample_n": 12, "confidence": "emerging"}}}

    artifact = bias_mirror.analyze_bias_mirror(profile, phase)

    assert json.dumps(profile, sort_keys=True, ensure_ascii=False) == before
    mirror = artifact["mirrors"][0]
    assert mirror["alignment"] == "CONTRADICTS"
    assert mirror["profile_says"] == "Over 2.5 (50.6%)"
    assert "可信度降低" in mirror["read_zh"]


def test_mirror_neutral_when_prior_low_confidence_and_never_emits_reverse_bet_advice() -> None:
    bias_mirror = load_module("bias_mirror_test", "bias_mirror.py")
    profile = {"total_line_lean": {"lean": "over", "label": "Over 2.5", "over_pct": 50.6}}
    phase = {"phase_priors": {"total_goals": {"bias_direction": "market_over_actual_under", "ledger_value": 0.25, "sample_n": 8, "confidence": "provisional_low_n"}}}

    artifact = bias_mirror.analyze_bias_mirror(profile, phase)
    text = json.dumps(artifact, ensure_ascii=False)

    assert artifact["mirrors"][0]["alignment"] == "NEUTRAL"
    forbidden = ["买", "bet", "play", "应该下", "推荐"]
    assert not any(word in text for word in forbidden)
    assert "不构成反向下注理由" in text


def test_true_pass_when_no_direction_and_directional_blocked_when_direction_but_no_edge() -> None:
    classifier = load_module("no_play_classifier_test", "no_play_classifier.py")

    true_pass = classifier.classify_no_play({"final_status": "watch", "relay_actionable": 0}, {"final_view": {"betting_direction": "no_play_watch"}})
    blocked = classifier.classify_no_play({"final_status": "pass_incomplete", "relay_actionable": 0}, {"final_view": {"betting_direction": "受让方/Under 2.25"}})
    skipped = classifier.classify_no_play({"final_status": "qualified_play", "relay_actionable": 1}, {"final_view": {"betting_direction": "Under 2.25"}})

    assert true_pass["type"] == "true_pass"
    assert blocked["type"] == "directional_blocked"
    assert blocked["direction_if_any"] == "受让方/Under 2.25"
    assert skipped is None


def test_grading_backfills_direction_hit() -> None:
    classifier = load_module("no_play_classifier_test", "no_play_classifier.py")
    card = {"type": "directional_blocked", "direction_if_any": "Under 2.5"}

    updated = classifier.backfill_direction_hit(card, {"home_score": 1, "away_score": 0})

    assert updated["post_result_direction_hit"] is True


def test_reflection_modules_are_never_actionable() -> None:
    phase_context = load_module("phase_context_test", "phase_context.py")
    bias_mirror = load_module("bias_mirror_test", "bias_mirror.py")
    classifier = load_module("no_play_classifier_test", "no_play_classifier.py")
    outputs = [
        phase_context.analyze_phase_context({"matchday": 1, "stage": "GROUP_STAGE"}, []),
        bias_mirror.analyze_bias_mirror({}, {}),
        classifier.classify_no_play({"final_status": "watch", "relay_actionable": 0}, {}),
    ]
    forbidden = {"edge", "p_adj", "gate", "actionable_edge"}
    for output in outputs:
        encoded = json.dumps(output, ensure_ascii=False)
        assert not any(f'"{key}"' in encoded for key in forbidden)
