#!/usr/bin/env python3
"""Validate WC26 calibration proposals before any approved apply job.

This script is intentionally conservative. It validates whether a proposal is
eligible for review or apply; it does not write profile files, MEMORY.md, or
live Hermes state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_POLICY = {
    "min_graded_cards": 25,
    "allowed_primary_signals": {"clv", "calibration", "brier_logloss"},
    "forbidden_primary_signals": {"hit_rate", "single_result", "win_loss"},
    "allowed_params": {
        "market_shrinkage_coef": {"max_abs_delta": 0.05, "min_n": 25},
        "dixon_coles_xi": {"max_abs_delta": 0.02, "min_n": 30},
        "bias.host_premium_pct": {"max_abs_delta": 0.02, "min_n": 25},
        "bias.favorite_tax_pct": {"max_abs_delta": 0.02, "min_n": 25},
        "bias.over_bias_pct": {"max_abs_delta": 0.02, "min_n": 25},
        "bias.public_narrative_pct": {"max_abs_delta": 0.02, "min_n": 25},
        "ev_threshold": {"max_abs_delta": 0.01, "min_n": 25},
        "source_freshness_min": {"max_abs_delta": 30.0, "min_n": 25},
        "kelly_fraction": {"max_abs_delta": 0.05, "min_n": 30, "risk_param": True},
        "max_stake_pct": {"max_abs_delta": 0.005, "min_n": 30, "risk_param": True},
    },
    "locked_prefixes": (
        "soul.",
        "identity.",
        "boundary.",
        "forbidden.",
        "memory.",
        "p_adj_default",
        "three_probability_rule",
        "no_auto_bet",
        "five_dimension_framework",
    ),
}


def load_structured(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
    else:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("YAML proposal requires PyYAML; use JSON or install pyyaml") from exc
        payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ValueError("proposal file must contain an object")
    if "calibration_proposal" in payload:
        payload = payload["calibration_proposal"]
    if not isinstance(payload, dict):
        raise ValueError("calibration_proposal must be an object")
    return payload


def as_float(value: Any, field: str, errors: list[str]) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"{field} must be numeric")
        return None


def validate_proposal(proposal: dict[str, Any], *, mode: str = "review") -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    policy = DEFAULT_POLICY

    param = str(proposal.get("param", ""))
    if not param:
        errors.append("param is required")
    if any(param == prefix or param.startswith(prefix) for prefix in policy["locked_prefixes"]):
        errors.append(f"param {param!r} is in the locked anchor zone")
    param_policy = policy["allowed_params"].get(param)
    if param and not param_policy:
        errors.append(f"param {param!r} is not in the allowed execution zone")

    current_value = as_float(proposal.get("current_value"), "current_value", errors)
    proposed_value = as_float(proposal.get("proposed_value"), "proposed_value", errors)
    declared_cap = as_float(proposal.get("bounded_delta_cap"), "bounded_delta_cap", errors)
    delta = None
    if current_value is not None and proposed_value is not None:
        delta = proposed_value - current_value

    if param_policy and delta is not None:
        policy_cap = float(param_policy["max_abs_delta"])
        cap = min(policy_cap, declared_cap) if declared_cap is not None else policy_cap
        if abs(delta) > cap + 1e-12:
            errors.append(f"delta {delta:.6g} exceeds cap {cap:.6g} for {param}")
        if param_policy.get("risk_param") and delta > 0:
            if not proposal.get("risk_increase_explicit_approval"):
                errors.append(f"{param} can auto-tighten only; increase requires risk_increase_explicit_approval")

    evidence = proposal.get("evidence", {})
    if not isinstance(evidence, dict):
        errors.append("evidence must be an object")
        evidence = {}
    n_graded = int(evidence.get("n_graded_cards") or 0)
    min_n = int(param_policy.get("min_n", policy["min_graded_cards"])) if param_policy else policy["min_graded_cards"]
    if n_graded < min_n:
        errors.append(f"n_graded_cards {n_graded} below threshold {min_n}")

    primary_signal = str(evidence.get("primary_signal", "")).lower()
    if primary_signal in policy["forbidden_primary_signals"]:
        errors.append(f"primary_signal {primary_signal!r} is forbidden; do not tune from hit rate or single results")
    if primary_signal and primary_signal not in policy["allowed_primary_signals"]:
        warnings.append(f"primary_signal {primary_signal!r} is not a preferred process metric")
    if evidence.get("uses_single_match_result"):
        errors.append("uses_single_match_result is forbidden; single-match outcomes are noise")

    sample_window = str(evidence.get("sample_window", ""))
    if not sample_window:
        errors.append("evidence.sample_window is required")
    effect = str(evidence.get("effect", ""))
    if not effect:
        errors.append("evidence.effect is required")

    rollback = proposal.get("rollback_rule", {})
    if not isinstance(rollback, dict):
        errors.append("rollback_rule must be an object")
        rollback = {}
    rollback_after = int(rollback.get("rollback_after_n") or 0)
    if rollback_after < 10:
        errors.append("rollback_rule.rollback_after_n must be at least 10")
    if str(rollback.get("metric", "")).lower() not in {"clv", "calibration", "brier_logloss"}:
        errors.append("rollback_rule.metric must be clv, calibration, or brier_logloss")
    if not rollback.get("condition"):
        errors.append("rollback_rule.condition is required")
    if not rollback.get("target_version"):
        errors.append("rollback_rule.target_version is required")

    approval = str(proposal.get("approval", "PENDING")).upper()
    if approval not in {"PENDING", "APPROVED", "REJECTED"}:
        errors.append("approval must be PENDING, APPROVED, or REJECTED")
    if mode == "apply" and approval != "APPROVED":
        errors.append("apply mode requires approval=APPROVED")

    valid = not errors
    apply_allowed = valid and approval == "APPROVED"
    return {
        "proposal_valid": valid,
        "apply_allowed": apply_allowed,
        "mode": mode,
        "param": param,
        "delta": delta,
        "n_graded_cards": n_graded,
        "approval": approval,
        "errors": errors,
        "warnings": warnings,
        "contract": "CLV/calibration proposal gate; never tune from single-match result",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("proposal", type=Path)
    parser.add_argument("--mode", choices=["review", "apply"], default="review")
    args = parser.parse_args()

    proposal = load_structured(args.proposal)
    result = validate_proposal(proposal, mode=args.mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["proposal_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
