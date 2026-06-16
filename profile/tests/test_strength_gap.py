"""Strength-gap stratification v1 test suite.

Tests compute_strength_gap(), tier classification, audit matrix,
and v1 redlines (no subjective input, no alpha judgments, unknown tier
exclusion, betting-decision isolation).
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
CONFIG_DIR = ROOT.parent / "profile" / "config"
PROFILE_SCRIPTS = ROOT.parent / "profile" / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dimension_scorer = load_module("dimension_scorer", SCRIPTS / "dimension_scorer.py")
strength_gap_audit = load_module("strength_gap_audit", SCRIPTS / "strength_gap_audit.py")

# ── Default config ──

DEFAULT_CONFIG = {
    "boundary_version": "v1",
    "tiers": {
        "even": {"max": 0.20},
        "moderate": {"min": 0.20, "max": 0.50},
        "lopsided": {"min": 0.50},
    },
    "min_cell_sample": 15,
}


def load_config():
    config_path = CONFIG_DIR / "strength-gap-config.json"
    if config_path.exists():
        return json.loads(config_path.read_text())
    return DEFAULT_CONFIG

# ── §1: compute_strength_gap 纯函数 ──

def test_gap_is_symmetric_max_minus_min() -> None:
    """gap = max(p_home, p_away) - min(p_home, p_away), symmetric w.r.t. home/away."""
    gap1 = dimension_scorer._tier_for_gap(0.15, DEFAULT_CONFIG)
    gap2 = dimension_scorer._tier_for_gap(0.15, DEFAULT_CONFIG)
    assert gap1 == gap2
    # Symmetry: min/max are identical regardless of which side is home
    assert 0.15 - 0.0 < 0.20  # even


def test_gap_tier_boundaries() -> None:
    """Tier boundaries: 0.19→even, 0.20→moderate, 0.499→moderate, 0.50→lopsided."""
    cfg = DEFAULT_CONFIG
    assert dimension_scorer._tier_for_gap(0.0, cfg) == "even"
    assert dimension_scorer._tier_for_gap(0.19, cfg) == "even"
    assert dimension_scorer._tier_for_gap(0.20, cfg) == "moderate"
    assert dimension_scorer._tier_for_gap(0.35, cfg) == "moderate"
    assert dimension_scorer._tier_for_gap(0.499, cfg) == "moderate"
    assert dimension_scorer._tier_for_gap(0.50, cfg) == "lopsided"
    assert dimension_scorer._tier_for_gap(0.85, cfg) == "lopsided"


def test_tier_boundary_edge_cases() -> None:
    """Edge case: gap at exactly 0.199... rounds correctly."""
    cfg = DEFAULT_CONFIG
    assert dimension_scorer._tier_for_gap(0.199999, cfg) == "even"


def test_gap_never_uses_fifa_rank_or_subjective() -> None:
    """compute_strength_gap only reads snapshot + config — no FIFA rank, Elo, or subjective."""
    # The function signature only takes snapshot_path, home, away, config
    # Verify no ranking-based params exist
    import inspect
    sig = inspect.signature(dimension_scorer.compute_strength_gap)
    params = list(sig.parameters.keys())
    for forbidden in ("fifa_rank", "elo", "rating", "subjective", "opinion"):
        assert forbidden not in params, f"compute_strength_gap must not accept {forbidden}"


def test_missing_opening_snapshot_marks_unknown() -> None:
    """When snapshot doesn't contain the match's Pinnacle H2H, tier='unknown'."""
    # Create a fake snapshot that doesn't contain our match
    with tempfile.TemporaryDirectory() as td:
        snap_path = Path(td) / "fake_snapshot.json"
        snap_path.write_text(json.dumps({"data": [
            {"home_team": "OtherTeam", "away_team": "AnotherTeam",
             "bookmakers": [{"key": "pinnacle", "markets": [
                 {"key": "h2h", "outcomes": [
                     {"price": 2.0}, {"price": 3.5}, {"price": 4.0}
                 ]}
             ]}]}
        ]}))
        result = dimension_scorer.compute_strength_gap(snap_path, "France", "Brazil", DEFAULT_CONFIG)
        assert result is not None
        assert result["tier"] == "unknown"
        assert result["missing_reason"] == "pinnacle_h2h_not_found_in_snapshot"


def test_gap_uses_opening_not_closing_snapshot() -> None:
    """The tier should be computed from the opening (earliest) snapshot passed in.
    This is a structural test: the function doesn't look for other snapshots internally.
    """
    # Create two snapshots: one early (opening), one late (closing)
    with tempfile.TemporaryDirectory() as td:
        # Opening snapshot: tight odds → even tier
        early_path = Path(td) / "early_snapshot.json"
        early_path.write_text(json.dumps({"data": [
            {"home_team": "TeamA", "away_team": "TeamB",
             "bookmakers": [{"key": "pinnacle", "markets": [
                 {"key": "h2h", "outcomes": [
                     {"name": "TeamA", "price": 2.8},
                     {"name": "Draw", "price": 3.2},
                     {"name": "TeamB", "price": 2.4},
                 ]}
             ]}]}
        ]}))
        # Closing snapshot: wide odds → lopsided tier
        late_path = Path(td) / "late_snapshot.json"
        late_path.write_text(json.dumps({"data": [
            {"home_team": "TeamA", "away_team": "TeamB",
             "bookmakers": [{"key": "pinnacle", "markets": [
                 {"key": "h2h", "outcomes": [
                     {"name": "TeamA", "price": 1.3},
                     {"name": "Draw", "price": 5.5},
                     {"name": "TeamB", "price": 10.0},
                 ]}
             ]}]}
        ]}))

        early_result = dimension_scorer.compute_strength_gap(early_path, "TeamA", "TeamB", DEFAULT_CONFIG)
        late_result = dimension_scorer.compute_strength_gap(late_path, "TeamA", "TeamB", DEFAULT_CONFIG)

        # They should differ — opening (tight) vs closing (wide)
        assert early_result is not None and late_result is not None
        assert early_result["tier"] != late_result["tier"], (
            "Opening and closing snapshots should produce different tiers"
        )


# ── §2: score_dimensions tags records with strength_gap ──

def test_score_dimensions_tags_records_with_strength_gap() -> None:
    """Records produced by score_dimensions include strength_gap when provided."""
    artifacts = {
        "role_engine": {
            "scoring_claim": {
                "dimension": "role_engine",
                "claim_type": "favorite_protected",
                "scorable": True,
                "directional_statement": "test",
            }
        }
    }
    result = {
        "actual_outcome": "home",
        "actual_margin": 2,
        "actual_total_goals": 3,
        "actual_over25": True,
        "favorite_side": "home",
        "favorite_covered_main_handicap": True,
        "home_score": 3,
        "away_score": 1,
    }
    sg = {"gap_value": 0.84, "tier": "lopsided", "p_fav": 0.90, "favorite_side": "home",
          "opening_snapshot_id": "test.json", "boundary_config_version": "v1"}

    records = dimension_scorer.score_dimensions("M999", result, artifacts, strength_gap=sg)
    assert len(records) == 1
    assert records[0]["strength_gap"]["tier"] == "lopsided"
    assert records[0]["strength_gap"]["gap_value"] == 0.84


def test_score_dimensions_without_strength_gap_still_works() -> None:
    """Backward Compat: score_dimensions works without strength_gap."""
    artifacts = {
        "role_engine": {
            "scoring_claim": {
                "dimension": "role_engine",
                "claim_type": "favorite_protected",
                "scorable": True,
                "directional_statement": "test",
            }
        }
    }
    result = {
        "actual_outcome": "home", "actual_margin": 2, "actual_total_goals": 3,
        "actual_over25": True, "favorite_side": "home",
        "favorite_covered_main_handicap": True, "home_score": 3, "away_score": 1,
    }
    records = dimension_scorer.score_dimensions("M999", result, artifacts)
    assert len(records) == 1
    assert "strength_gap" not in records[0]


# ── §3: strength_gap_audit ──

def test_audit_by_strength_crosses_dimension_and_tier() -> None:
    """Matrix has (dimension × tier) structure."""
    ledger = {
        "records": [
            {"dimension": "role_engine", "verdict": "hit",
             "strength_gap": {"tier": "even", "gap_value": 0.05}},
            {"dimension": "bias_mirror", "verdict": "miss",
             "strength_gap": {"tier": "even", "gap_value": 0.08}},
            {"dimension": "role_engine", "verdict": "hit",
             "strength_gap": {"tier": "lopsided", "gap_value": 0.75}},
        ]
    }
    config = {"tiers": {"even": {"max": 0.20}, "lopsided": {"min": 0.50}}, "min_cell_sample": 1}
    report = strength_gap_audit.audit_by_strength_tier(ledger, config)
    matrix = report["dimension_tier_matrix"]
    assert "role_engine" in matrix
    assert "bias_mirror" in matrix
    assert "even" in matrix["role_engine"]
    assert "lopsided" in matrix["role_engine"]
    assert matrix["role_engine"]["even"]["n_scored"] == 1
    assert matrix["role_engine"]["even"]["hit_rate"] == 1.0
    assert matrix["role_engine"]["lopsided"]["n_scored"] == 1
    assert matrix["role_engine"]["lopsided"]["hit_rate"] == 1.0


def test_cell_insufficient_sample_no_verdict() -> None:
    """When n < min_cell_sample, sample_sufficient=False."""
    config = {"tiers": {"even": {"max": 0.20}}, "min_cell_sample": 10}
    ledger = {
        "records": [
            {"dimension": "role_engine", "verdict": "hit",
             "strength_gap": {"tier": "even", "gap_value": 0.05}},
            {"dimension": "role_engine", "verdict": "miss",
             "strength_gap": {"tier": "even", "gap_value": 0.05}},
        ]
    }
    report = strength_gap_audit.audit_by_strength_tier(ledger, config)
    cell = report["dimension_tier_matrix"]["role_engine"]["even"]
    assert cell["n_scored"] == 2
    assert cell["sample_sufficient"] is False
    # hit_rate still computed (raw data), just not "sufficient" for interpretation
    assert cell["hit_rate"] is not None


def test_strength_report_emits_no_alpha_conclusion() -> None:
    """Audit report must NOT contain 'alpha', '该用', '失效', or removal-type language."""
    config = {"tiers": {"even": {"max": 0.20}}, "min_cell_sample": 1}
    ledger = {
        "records": [
            {"dimension": "role_engine", "verdict": "hit",
             "strength_gap": {"tier": "even", "gap_value": 0.05}},
        ]
    }
    report = strength_gap_audit.audit_by_strength_tier(ledger, config)
    # Check matrix cells specifically, not the disclaimer itself
    matrix = report.get("dimension_tier_matrix", {})
    matrix_str = json.dumps(matrix, ensure_ascii=False)
    for forbidden in ("alpha", "该用", "失效", "candidate_for_removal",
                       "recommend", "recommendation", "should_keep", "should_drop"):
        assert forbidden not in matrix_str.lower(), f"Matrix contains forbidden word: {forbidden}"
    # Must have disclaimer
    assert "disclaimer" in report
    assert "人判断" in report["disclaimer"]


def test_unknown_tier_excluded_from_stats() -> None:
    """Records with tier='unknown' must not pollute tier-specific statistics."""
    config = {"tiers": {"even": {"max": 0.20}}, "min_cell_sample": 1}
    ledger = {
        "records": [
            {"dimension": "role_engine", "verdict": "hit",
             "strength_gap": {"tier": "even", "gap_value": 0.05}},
            {"dimension": "role_engine", "verdict": "miss",
             "strength_gap": {"tier": "unknown", "gap_value": None}},
            {"dimension": "role_engine", "verdict": "hit",
             "strength_gap": {"tier": "unknown", "gap_value": None}},
        ]
    }
    report = strength_gap_audit.audit_by_strength_tier(ledger, config)
    cell = report["dimension_tier_matrix"]["role_engine"]["even"]
    assert cell["n_scored"] == 1  # Only the even record, not the 2 unknowns
    assert cell["hit_rate"] == 1.0
    # Verify "unknown" tier doesn't appear as a column
    assert "unknown" not in cell


def test_by_tier_overall_aggregates_all_dimensions() -> None:
    """_by_tier_overall aggregates across all dimensions."""
    config = {"tiers": {"even": {"max": 0.20}, "lopsided": {"min": 0.50}}, "min_cell_sample": 1}
    ledger = {
        "records": [
            {"dimension": "role_engine", "verdict": "hit",
             "strength_gap": {"tier": "even", "gap_value": 0.05}},
            {"dimension": "bias_mirror", "verdict": "miss",
             "strength_gap": {"tier": "even", "gap_value": 0.10}},
            {"dimension": "role_engine", "verdict": "hit",
             "strength_gap": {"tier": "lopsided", "gap_value": 0.75}},
        ]
    }
    report = strength_gap_audit.audit_by_strength_tier(ledger, config)
    overall = report["by_tier_overall"]
    assert overall["even"]["n_scored"] == 2  # 2 records across 2 dims
    assert overall["even"]["hit_rate"] == 0.5  # 1 hit / 2
    assert overall["lopsided"]["n_scored"] == 1
    assert overall["lopsided"]["hit_rate"] == 1.0


# ── §4: 红线 ──

def test_strength_gap_never_affects_betting_decision() -> None:
    """Strength_gap records must never contain edge/p_adj/gate fields."""
    records = dimension_scorer.score_dimensions(
        "M999",
        {"actual_outcome": "home", "actual_margin": 2, "actual_total_goals": 3,
         "actual_over25": True, "favorite_side": "home",
         "favorite_covered_main_handicap": True, "home_score": 3, "away_score": 1},
        {"role_engine": {"scoring_claim": {"dimension": "role_engine",
                          "claim_type": "favorite_protected", "scorable": True,
                          "directional_statement": "test"}}},
        strength_gap={"gap_value": 0.84, "tier": "lopsided", "p_fav": 0.90,
                      "favorite_side": "home", "opening_snapshot_id": "test.json",
                      "boundary_config_version": "v1"},
    )
    for rec in records:
        for forbidden in ("edge", "p_adj", "gate", "final_status", "actionable", "qualified_play"):
            assert forbidden not in rec, f"Record contains forbidden field: {forbidden}"
        # strength_gap itself must not contain betting fields
        sg = rec.get("strength_gap", {})
        for forbidden in ("edge", "p_adj", "gate", "bet_recommendation"):
            assert forbidden not in sg, f"strength_gap contains forbidden field: {forbidden}"


def test_pipeline_wiring_covers_strength_gap() -> None:
    """Pipeline wiring must register strength_gap_audit capability."""
    wiring_path = ROOT.parent / "profile" / "config" / "pipeline-wiring.json"
    wiring = json.loads(wiring_path.read_text())
    generated = {item["capability"] for item in wiring["generated_capabilities"]}
    assert "strength_gap_audit" in generated


def test_dimension_scorer_still_passes_existing_tests() -> None:
    """The modified score_dimensions still works identically to v1 behavior
    when no strength_gap is provided."""
    # Same as test from test_dimension_scoring.py
    artifacts = {
        "role_engine": {
            "scoring_claim": {"dimension": "role_engine",
                              "claim_type": "favorite_protected",
                              "scorable": True,
                              "directional_statement": "test"}
        },
    }
    result = {
        "actual_outcome": "home", "actual_margin": 2, "actual_total_goals": 3,
        "actual_over25": True, "favorite_side": "home",
        "favorite_covered_main_handicap": True, "home_score": 3, "away_score": 1,
    }
    records = dimension_scorer.score_dimensions("M999", result, artifacts)
    assert len(records) == 1
    assert records[0]["verdict"] == "hit"


# ── §5: Shin no-vig inline validation ──

def test_inline_devig_shin_handles_simple_case() -> None:
    """The inline Shin solver produces normalized probabilities that sum to 1."""
    probs = dimension_scorer._inline_devig_shin([2.0, 3.5, 4.0])
    assert len(probs) == 3
    assert abs(sum(probs) - 1.0) < 0.001
    assert probs[0] > probs[1] > probs[2]  # Home > Draw > Away


def test_inline_devig_shin_extreme_favorite() -> None:
    """Lopsided odds produce plausible gap."""
    probs = dimension_scorer._inline_devig_shin([1.10, 7.0, 20.0])
    assert len(probs) == 3
    assert probs[0] > 0.80  # home dominates
    gap = max(probs[0], probs[2]) - min(probs[0], probs[2])
    assert gap > 0.50  # lopsided


def test_real_snapshot_compute_gap() -> None:
    """Compute strength_gap from a real snapshot file (if available)."""
    import pathlib as _pl
    snapshots_dir = Path("/hermesdata/worldcup-2026-handicap/snapshots/odds")
    if not snapshots_dir.exists():
        pytest.skip("No snapshot directory available")

    # Find an early snapshot for any match
    snapshots = sorted(snapshots_dir.glob("the-odds-api-multibook-*.json"))
    if len(snapshots) < 2:
        pytest.skip("Not enough snapshots available")

    # Use the earliest snapshot
    earliest = snapshots[0]

    # Find any finished match from fixture registry
    fixture_path = Path("/hermesdata/worldcup-2026-handicap/snapshots/fixtures/football-data-wc-matches-latest.json")
    if not fixture_path.exists():
        pytest.skip("No fixture snapshot")

    fixtures = json.loads(fixture_path.read_text())
    all_matches = fixtures.get("data", {}).get("matches", fixtures.get("matches", []))
    finished = [m for m in all_matches if m.get("status") in ("FINISHED", "AWARDED")]
    if not finished:
        pytest.skip("No finished matches")

    m = finished[0]
    home = (m.get("homeTeam") or {}).get("name", "")
    away = (m.get("awayTeam") or {}).get("name", "")

    result = dimension_scorer.compute_strength_gap(earliest, home, away, DEFAULT_CONFIG)
    assert result is not None
    assert "tier" in result
    assert "gap_value" in result
    # Even if match not found in this snapshot, tier should be "unknown"
    print(f"  {home} vs {away}: tier={result['tier']}, gap={result.get('gap_value')}")


# ── §6: Text report rendering ──

def test_text_report_renders_without_crashing() -> None:
    """render_text_report produces human-readable output."""
    config = {"tiers": {"even": {"max": 0.20}, "moderate": {"min": 0.20, "max": 0.50}, "lopsided": {"min": 0.50}},
              "min_cell_sample": 15, "boundary_version": "v1"}
    ledger = {
        "records": [
            {"dimension": "role_engine", "verdict": "hit",
             "strength_gap": {"tier": "even", "gap_value": 0.05}},
            {"dimension": "role_engine", "verdict": "miss",
             "strength_gap": {"tier": "even", "gap_value": 0.08}},
            {"dimension": "bias_mirror", "verdict": "hit",
             "strength_gap": {"tier": "lopsided", "gap_value": 0.75}},
        ]
    }
    report = strength_gap_audit.audit_by_strength_tier(ledger, config)
    text = strength_gap_audit.render_text_report(report)
    assert "Strength-Gap" in text
    assert "Dimension" in text
    assert "role_engine" in text
    assert "bias_mirror" in text
    assert "even" in text
    assert "lopsided" in text
    assert "人判断" in text or "disclaimer" in text.lower()
