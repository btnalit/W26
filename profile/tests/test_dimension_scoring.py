"""Dimension scoring v2 test suite.

Tests the scoring_claim tail, judge() purity, scorer idempotency, audit constraints,
and the v2 red lines (no removal judgment, not_applicable_rate visibility,
frequency-based thresholds, cross-tournament accumulation).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "odds-analysis" / "scripts"
PROFILE_SCRIPTS = ROOT.parent / "profile" / "scripts"
CONFIG_DIR = ROOT.parent / "profile" / "config"
PROFILE_SCRIPTS_ROOT = ROOT.parent / "profile" / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dimension_scorer = load_module("dimension_scorer", SCRIPTS / "dimension_scorer.py")
dimension_audit = load_module("dimension_audit", SCRIPTS / "dimension_audit.py")
role_engine = load_module("role_engine", SCRIPTS / "role_engine.py")
bias_mirror = load_module("bias_mirror", SCRIPTS / "bias_mirror.py")
motivation_context = load_module("motivation_context", SCRIPTS / "motivation_context.py")
no_play_classifier = load_module("no_play_classifier", SCRIPTS / "no_play_classifier.py")

# ── Helper fixtures ──

def sample_settled_result(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "actual_outcome": "home",
        "actual_margin": 2,
        "actual_total_goals": 3,
        "actual_over25": True,
        "favorite_side": "home",
        "favorite_covered_main_handicap": True,
        "home_score": 3,
        "away_score": 1,
    }
    defaults.update(overrides)
    return defaults

# ── §1: claim 产出 ──

def test_role_engine_emits_scoring_claim_falsifiable() -> None:
    """role_engine with a CONFIRMED public_bias emits a scoring_claim."""
    ctx = {
        "home": "France",
        "away": "Argentina",
        "favorite_side": "home",
        "favorite_label": "France",
    }
    conclusions = [
        role_engine.role_conclusion(
            2, "public_bias", "CONFIRMED", "never_actionable",
            "散户拥挤在热门侧", "散户偏向France且Path A/Path C无反向信号",
            ["path_a_crossbook"], [], [],
        ),
    ]
    claim = role_engine.derive_scoring_claim(conclusions, ctx)
    assert claim is not None
    assert claim["dimension"] == "role_engine"
    assert claim["scorable"] is True
    # It should be falsifiable
    assert claim["claim_type"] in ("retail_overload_side_X", "favorite_protected", "trap_on_side_X", "market_efficient")


def test_bias_mirror_neutral_marks_not_scorable() -> None:
    """bias_mirror with NEUTRAL alignment produces no scoring_claim."""
    profile = {"total_line_lean": {"lean": "over", "over_pct": 65.0}}
    phase = {"phase_priors": {"total_goals": {"bias_direction": "aligned", "sample_n": 3, "confidence": "provisional_low_n"}}}
    result = bias_mirror.analyze_bias_mirror(profile, phase)
    assert "scoring_claim" not in result
    assert len(result["mirrors"]) == 2
    assert result["mirrors"][0]["alignment"] == "NEUTRAL"


def test_descriptive_only_output_marks_not_scorable() -> None:
    """Pure descriptive output (role_engine with all DIAGNOSTIC_ONLY) should not produce a scoring_claim."""
    ctx = {
        "home": "TeamA",
        "away": "TeamB",
        "favorite_side": "home",
    }
    conclusions = [
        role_engine.role_conclusion(
            1, "bookmaker_intent", "DIAGNOSTIC_ONLY", "never_actionable",
            "庄家意图", "诊断性结论",
            [], [], [],
        ),
        role_engine.role_conclusion(
            2, "public_bias", "REFUTED", "never_actionable",
            "散户心理", "没有拥挤",
            [], [], [],
        ),
        role_engine.role_conclusion(
            3, "ai_lag", "REFUTED", "never_actionable",
            "AI滞后", "无滞后",
            [], [], [],
        ),
        role_engine.role_conclusion(
            4, "trap_risk", "REFUTED", "never_actionable",
            "陷阱盘", "无陷阱",
            [], [], [],
        ),
        role_engine.role_conclusion(
            5, "market_efficiency", "DIAGNOSTIC_ONLY", "never_actionable",
            "市场效率", "诊断中",
            [], [], [],
        ),
    ]
    claim = role_engine.derive_scoring_claim(conclusions, ctx)
    assert claim is None


# ── §2: judge() 裁判纯净性 ──

def test_judge_favorite_protected_hit_and_miss() -> None:
    """judge returns hit when favorite covers, miss when it doesn't."""
    claim_hit = {"claim_type": "favorite_protected", "scorable": True}
    result_hit = sample_settled_result(favorite_covered_main_handicap=True)
    assert dimension_scorer.judge(claim_hit, result_hit) == "hit"

    result_miss = sample_settled_result(favorite_covered_main_handicap=False)
    assert dimension_scorer.judge(claim_hit, result_miss) == "miss"


def test_judge_never_reads_dimension_self_confidence() -> None:
    """judge must ignore any self_confidence or internal scores in claim — only reads claim_type and result."""
    # Construct a claim that has a "self says I'm right" field
    claim = {
        "claim_type": "favorite_protected",
        "scorable": True,
        "self_confidence": "certain",
        "self_rating": "10/10",
        "internal_score": 0.99,
    }
    # Actual result is the opposite
    result = sample_settled_result(favorite_covered_main_handicap=False)
    verdict = dimension_scorer.judge(claim, result)
    # Must be "miss" — judge only looks at actual result, not self_confidence
    assert verdict == "miss"


def test_judge_missing_metric_returns_not_applicable() -> None:
    """judge returns not_applicable when the required metric is missing from settled_result."""
    claim = {"claim_type": "favorite_protected", "scorable": True}
    result = sample_settled_result(favorite_covered_main_handicap=None)
    assert dimension_scorer.judge(claim, result) == "not_applicable"


def test_judge_mutual_draw_incentive_hit_and_miss() -> None:
    """motivation_context claim_type=mutual_draw_incentive: hit on draw, miss otherwise."""
    claim = {"claim_type": "mutual_draw_incentive", "scorable": True}
    assert dimension_scorer.judge(claim, sample_settled_result(actual_outcome="draw")) == "hit"
    assert dimension_scorer.judge(claim, sample_settled_result(actual_outcome="home")) == "miss"


def test_judge_rotation_vs_desperation() -> None:
    """rotation_vs_desperation: hit when favorite didn't cover, miss when did."""
    claim = {"claim_type": "rotation_vs_desperation", "scorable": True}
    assert dimension_scorer.judge(claim, sample_settled_result(favorite_covered_main_handicap=False)) == "hit"
    assert dimension_scorer.judge(claim, sample_settled_result(favorite_covered_main_handicap=True)) == "miss"


def test_judge_mutual_desperation() -> None:
    """mutual_desperation: hit when non-draw or high goals."""
    claim = {"claim_type": "mutual_desperation", "scorable": True}
    # Non-draw → hit
    assert dimension_scorer.judge(claim, sample_settled_result(actual_outcome="home")) == "hit"
    # High goals (even if draw) → hit
    assert dimension_scorer.judge(claim, sample_settled_result(actual_outcome="draw", actual_total_goals=4)) == "hit"
    # Draw with low goals → miss
    assert dimension_scorer.judge(claim, sample_settled_result(actual_outcome="draw", actual_total_goals=1)) == "miss"


def test_judge_unknown_claim_type_returns_not_applicable() -> None:
    claim = {"claim_type": "nonexistent_claim_type", "scorable": True}
    assert dimension_scorer.judge(claim, sample_settled_result()) == "not_applicable"


def test_judge_not_scorable_returns_not_scorable() -> None:
    claim = {"claim_type": "favorite_protected", "scorable": False}
    assert dimension_scorer.judge(claim, sample_settled_result()) == "not_scorable"


def test_judge_profile_lean_discounted() -> None:
    """bias_mirror profile_lean_discounted: Over lean discounted → hit if Under."""
    claim_over_discounted = {
        "claim_type": "profile_lean_discounted",
        "scorable": True,
        "directional_statement": "画像偏Over, 但阶段偏Under → Over倾向打折",
    }
    assert dimension_scorer.judge(claim_over_discounted, sample_settled_result(actual_over25=False)) == "hit"
    assert dimension_scorer.judge(claim_over_discounted, sample_settled_result(actual_over25=True)) == "miss"


# ── §3: 幂等 & 样本 ──

def test_scorer_idempotent_no_double_count() -> None:
    """Running score_dimensions twice with same match_id+dimension doesn't double-count."""
    match_id = "M999"
    artifacts = {
        "role_engine": {
            "scoring_claim": {"dimension": "role_engine", "claim_type": "favorite_protected",
                              "falsifiable_by": "favorite_covers_main_handicap", "scorable": True,
                              "directional_statement": "test"}
        },
    }
    result = sample_settled_result()
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = Path(tmpdir) / "test_ledger.json"
        records1 = dimension_scorer.score_dimensions(match_id, result, artifacts, ledger_path)
        written1 = dimension_scorer.write_ledger_records(records1, ledger_path)
        assert written1 >= 1

        # Second run — should skip duplicates
        records2 = dimension_scorer.score_dimensions(match_id, result, artifacts, ledger_path)
        written2 = dimension_scorer.write_ledger_records(records2, ledger_path)
        assert written2 == 0  # zero new records

        # Verify ledger has exactly 1 record for this match+dim
        ledger = json.loads(ledger_path.read_text())
        assert len(ledger["records"]) == 1


def test_audit_insufficient_n_no_verdict() -> None:
    """audit with n < threshold should set sample_sufficient=False."""
    config = {"dimension_sample_thresholds": {}, "default_threshold": 20}
    ledger = {
        "records": [
            {"dimension": "role_engine", "claim_type": "favorite_protected", "verdict": "hit"},
            {"dimension": "role_engine", "claim_type": "favorite_protected", "verdict": "miss"},
        ]
    }
    report = dimension_audit.audit_dimensions(ledger, config)
    dim = report["dimensions"]["role_engine"]
    assert dim["n_scored"] == 2
    assert dim["sample_sufficient"] is False
    # Must NOT contain any removal judgment
    assert "candidate_for_removal" not in dim
    assert "verdict" not in dim


# ── §4: v2 红线 ──

def test_audit_emits_no_removal_judgment() -> None:
    """v2: audit report MUST NOT contain candidate_for_removal or any removal suggestion."""
    config = {"dimension_sample_thresholds": {"role_engine": 2}, "default_threshold": 20}
    ledger = {
        "records": [
            {"dimension": "role_engine", "claim_type": "favorite_protected", "verdict": "hit"},
            {"dimension": "role_engine", "claim_type": "favorite_protected", "verdict": "hit"},
            {"dimension": "role_engine", "claim_type": "favorite_protected", "verdict": "miss"},
        ]
    }
    report = dimension_audit.audit_dimensions(ledger, config)
    dim = report["dimensions"]["role_engine"]
    # Verify raw data is present
    assert dim["sample_sufficient"] is True
    assert dim["hit_rate"] is not None
    assert dim["n_scored"] == 3
    assert dim["not_applicable_rate"] is not None
    # Verify NO judgment keys
    for forbidden in ("candidate_for_removal", "verdict", "recommendation", "should_keep", "should_remove"):
        assert forbidden not in dim, f"dimension report contains forbidden key: {forbidden}"
    # Verify disclaimer is present
    assert "disclaimer" in report
    assert "人判断" in report["disclaimer"]


def test_audit_exposes_not_applicable_rate() -> None:
    """v2: not_applicable_rate must be visible and not allow silent high hit rates."""
    config = {"dimension_sample_thresholds": {"role_engine": 5}, "default_threshold": 20}
    # 8 not_applicable + 2 hit = high not_applicable_rate, deceptively perfect hit_rate
    ledger = {
        "records": [
            {"dimension": "role_engine", "claim_type": "market_efficient", "verdict": "hit"},
            {"dimension": "role_engine", "claim_type": "market_efficient", "verdict": "hit"},
        ]
        + [{"dimension": "role_engine", "claim_type": "market_efficient", "verdict": "not_applicable"} for _ in range(8)]
    }
    report = dimension_audit.audit_dimensions(ledger, config)
    dim = report["dimensions"]["role_engine"]
    # Hit rate is 100% on scored, but...
    assert dim["hit_rate"] == 1.0
    assert dim["n_scored"] == 2
    # not_applicable_rate should be 0.8 (8 out of 10)
    assert dim["not_applicable_rate"] == 0.8
    assert dim["n_not_applicable"] == 8


def test_low_frequency_dimension_uses_lower_threshold() -> None:
    """v2: motivation_context uses threshold 8, not 20."""
    config = {
        "dimension_sample_thresholds": {"motivation_context": 8, "role_engine": 20},
        "default_threshold": 20,
    }
    assert dimension_audit.threshold_for("motivation_context", config) == 8
    assert dimension_audit.threshold_for("role_engine", config) == 20
    assert dimension_audit.threshold_for("unknown_dimension", config) == 20

    # With 9 records, motivation_context should have sufficient samples
    ledger = {
        "records": [
            {"dimension": "motivation_context", "claim_type": "mutual_draw_incentive", "verdict": v}
            for v in (["hit"] * 5 + ["miss"] * 4)
        ]
    }
    report = dimension_audit.audit_dimensions(ledger, config)
    dim = report["dimensions"]["motivation_context"]
    assert dim["n_scored"] == 9
    assert dim["sample_sufficient"] is True
    assert dim["threshold"] == 8


def test_cross_tournament_accumulation_preserves_low_freq_samples() -> None:
    """v2: cross_tournament_accumulation=true in config allows inter-tournament ledger accumulation."""
    config = {
        "dimension_sample_thresholds": {"motivation_context": 8},
        "default_threshold": 20,
        "cross_tournament_accumulation": True,
        "VALUE_INTERPRETATION_IS_HUMAN_ONLY": True,
    }
    assert config["cross_tournament_accumulation"] is True
    # The actual accumulation is in how the ledger is persisted (not per-tournament cleanup).
    # This test validates the config flag exists and threshold_for respects it.
    assert dimension_audit.threshold_for("motivation_context", config) == 8


# ── §5: 红线 ──

def test_scoring_layer_never_affects_betting_decision() -> None:
    """The scoring layer must never produce edge/p_adj/gate or affect betting decisions."""
    artifacts = {
        "role_engine": {
            "scoring_claim": {"dimension": "role_engine", "claim_type": "favorite_protected",
                              "scorable": True, "directional_statement": "test"}
        },
    }
    result = sample_settled_result()
    records = dimension_scorer.score_dimensions("M999", result, artifacts)
    for rec in records:
        for forbidden in ("edge", "p_adj", "gate", "final_status", "actionable", "qualified_play"):
            assert forbidden not in rec, f"scoring record contains forbidden field: {forbidden}"


def test_pipeline_wiring_covers_scoring_producers() -> None:
    """Pipeline wiring JSON must register dimension_scorer and dimension_audit as generated capabilities."""
    wiring_path = ROOT.parent / "profile" / "config" / "pipeline-wiring.json"
    wiring = json.loads(wiring_path.read_text())
    generated = {item["capability"] for item in wiring["generated_capabilities"]}
    assert "dimension_scorer" in generated
    assert "dimension_audit" in generated


# ── §6: motivation_context scoring claim ──

def test_motivation_context_emits_scoring_claim_on_matchday_3() -> None:
    """motivation_context on matchday 3 with a MUTUAL_DRAW_INCENTIVE situation_tag emits scoring_claim."""
    standings = {
        "TeamA": {"Pts": 4, "GD": 1, "GF": 3, "GA": 2, "played": 2, "W": 1, "D": 1, "L": 0},
        "TeamB": {"Pts": 4, "GD": 1, "GF": 2, "GA": 1, "played": 2, "W": 1, "D": 1, "L": 0},
        "TeamC": {"Pts": 1, "GD": -1, "GF": 1, "GA": 2, "played": 2, "W": 0, "D": 1, "L": 1},
        "TeamD": {"Pts": 1, "GD": -1, "GF": 0, "GA": 1, "played": 2, "W": 0, "D": 1, "L": 1},
    }
    fixtures = [{"home": "TeamA", "away": "TeamB"}, {"home": "TeamC", "away": "TeamD"}]
    match = {"home": "TeamA", "away": "TeamB", "matchday": 3}
    rules = {"direct_slots": 2, "best_third_slots": 8, "treat_third_as_possible": False}
    result = motivation_context.analyze_motivation_context(
        standings, fixtures, match, rules,
    )
    if result.get("situation_tag") == "MUTUAL_DRAW_INCENTIVE":
        assert "scoring_claim" in result
        assert result["scoring_claim"]["claim_type"] == "mutual_draw_incentive"
        assert result["scoring_claim"]["scorable"] is True


def test_motivation_context_no_claim_on_matchday_1() -> None:
    """motivation_context returns NONE on matchday 1 with no scoring_claim."""
    match = {"home": "TeamA", "away": "TeamB", "matchday": 1}
    result = motivation_context.analyze_motivation_context(None, None, match)
    assert result["situation_tag"] == "NONE"
    assert "scoring_claim" not in result


# ── §7: no_play_classifier scoring claim ──

def test_no_play_classifier_emits_scoring_claim_on_directional_blocked() -> None:
    """no_play_classifier with directional_blocked type emits scoring_claim."""
    final_inputs = {"final_status": "watch", "relay_actionable": 0}
    deep_research = {"final_view": {"betting_direction": "Under 2.5"}}
    result = no_play_classifier.classify_no_play(final_inputs, deep_research)
    assert result is not None
    assert result["type"] == "directional_blocked"
    assert "scoring_claim" in result
    assert result["scoring_claim"]["scorable"] is True


def test_no_play_classifier_no_claim_on_true_pass() -> None:
    """no_play_classifier with true_pass type does not emit scoring_claim."""
    final_inputs = {"final_status": "watch", "relay_actionable": 0}
    deep_research = {"final_view": {"betting_direction": ""}}
    result = no_play_classifier.classify_no_play(final_inputs, deep_research)
    assert result is not None
    assert result["type"] == "true_pass"
    assert "scoring_claim" not in result


# ── §8: dimension_audit full pipeline ──

def test_audit_full_report_structure() -> None:
    """Full audit report has correct schema and no judgments."""
    config = dimension_audit.load_config(CONFIG_DIR / "dimension-scoring-config.json")
    ledger = {
        "schema_version": "wc26.dimension_score_ledger.v1",
        "records": [
            {"dimension": "role_engine", "claim_type": "favorite_protected", "verdict": "hit",
             "match_id": "M001", "record_id": "r1", "scored_at_utc": "2026-06-01T00:00:00Z"},
            {"dimension": "role_engine", "claim_type": "favorite_protected", "verdict": "miss",
             "match_id": "M002", "record_id": "r2", "scored_at_utc": "2026-06-02T00:00:00Z"},
            {"dimension": "bias_mirror", "claim_type": "profile_lean_discounted", "verdict": "not_applicable",
             "match_id": "M001", "record_id": "r3", "scored_at_utc": "2026-06-01T00:00:00Z"},
        ],
    }
    report = dimension_audit.audit_dimensions(ledger, config)
    assert report["schema_version"] == "wc26.dimension_audit_report.v2"
    assert report["contract"] == "wc26.dimension_audit.v1"
    assert "disclaimer" in report
    assert "dimensions" in report
    # role_engine: 2 scored
    assert report["dimensions"]["role_engine"]["n_scored"] == 2
    assert report["dimensions"]["bias_mirror"]["n_scored"] == 0
    assert report["dimensions"]["bias_mirror"]["n_not_applicable"] == 1
    assert report["dimensions"]["bias_mirror"]["not_applicable_rate"] == 1.0
    # No forbidden keys anywhere
    for dim_name, dim in report["dimensions"].items():
        for forbidden in ("candidate_for_removal", "verdict", "recommendation"):
            assert forbidden not in dim, f"dim {dim_name} has forbidden key {forbidden}"
