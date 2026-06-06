#!/usr/bin/env python3
"""Focused checks for calibration proposal gating."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = ROOT / "skills" / "odds-analysis" / "scripts" / "calibration_gate.py"
spec = importlib.util.spec_from_file_location("calibration_gate", GATE_PATH)
gate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(gate)


def base_proposal() -> dict:
    return {
        "trigger": "host_premium CLV negative over sufficient sample",
        "param": "bias.host_premium_pct",
        "current_value": 0.03,
        "proposed_value": 0.02,
        "bounded_delta_cap": 0.02,
        "evidence": {
            "n_graded_cards": 28,
            "primary_signal": "clv",
            "effect": "host premium bucket CLV mean -2.1%",
            "sample_window": "T-72h and T-24h host-premium cards",
            "uses_single_match_result": False,
        },
        "rollback_rule": {
            "rollback_after_n": 15,
            "metric": "clv",
            "condition": "CLV does not improve versus prior version",
            "target_version": "v1",
        },
        "approval": "PENDING",
    }


def test_valid_pending_proposal_is_review_only() -> None:
    result = gate.validate_proposal(base_proposal())
    assert result["proposal_valid"] is True
    assert result["apply_allowed"] is False


def test_apply_requires_approval() -> None:
    proposal = base_proposal()
    result = gate.validate_proposal(proposal, mode="apply")
    assert result["proposal_valid"] is False
    assert "approval=APPROVED" in " ".join(result["errors"])
    proposal["approval"] = "APPROVED"
    result = gate.validate_proposal(proposal, mode="apply")
    assert result["proposal_valid"] is True
    assert result["apply_allowed"] is True


def test_low_sample_rejected() -> None:
    proposal = base_proposal()
    proposal["evidence"]["n_graded_cards"] = 8
    result = gate.validate_proposal(proposal)
    assert result["proposal_valid"] is False
    assert "below threshold" in " ".join(result["errors"])


def test_hit_rate_or_single_result_rejected() -> None:
    proposal = base_proposal()
    proposal["evidence"]["primary_signal"] = "hit_rate"
    proposal["evidence"]["uses_single_match_result"] = True
    result = gate.validate_proposal(proposal)
    assert result["proposal_valid"] is False
    errors = " ".join(result["errors"])
    assert "hit rate" in errors
    assert "single-match" in errors


def test_locked_anchor_rejected() -> None:
    proposal = base_proposal()
    proposal["param"] = "p_adj_default"
    result = gate.validate_proposal(proposal)
    assert result["proposal_valid"] is False
    assert "locked anchor zone" in " ".join(result["errors"])


def test_risk_increase_requires_explicit_approval() -> None:
    proposal = base_proposal()
    proposal["param"] = "kelly_fraction"
    proposal["current_value"] = 0.25
    proposal["proposed_value"] = 0.30
    proposal["bounded_delta_cap"] = 0.05
    proposal["evidence"]["n_graded_cards"] = 35
    result = gate.validate_proposal(proposal)
    assert result["proposal_valid"] is False
    assert "increase requires" in " ".join(result["errors"])
    proposal["risk_increase_explicit_approval"] = True
    proposal["approval"] = "APPROVED"
    result = gate.validate_proposal(proposal, mode="apply")
    assert result["proposal_valid"] is True


if __name__ == "__main__":
    test_valid_pending_proposal_is_review_only()
    test_apply_requires_approval()
    test_low_sample_rejected()
    test_hit_rate_or_single_result_rejected()
    test_locked_anchor_rejected()
    test_risk_increase_requires_explicit_approval()
    print("calibration gate tests PASS")
