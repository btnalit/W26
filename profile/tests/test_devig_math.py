#!/usr/bin/env python3
"""Focused math checks for WC26 odds-analysis helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEVIG_PATH = ROOT / "skills" / "odds-analysis" / "scripts" / "devig.py"
spec = importlib.util.spec_from_file_location("devig", DEVIG_PATH)
devig = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(devig)


DIST = {-2: 0.10, -1: 0.15, 0: 0.25, 1: 0.30, 2: 0.20}
PRICE = 1.95


def naive_cover_ev(line: float) -> float:
    cover_probability = sum(prob for margin, prob in DIST.items() if margin + line > 0)
    return cover_probability * PRICE - 1.0


def test_quarter_ball_ev_uses_split_legs() -> None:
    line = -0.25
    assert devig.asian_handicap_legs(line) == [-0.5, 0.0]
    assert abs(devig.asian_handicap_ev(DIST, line, PRICE) - naive_cover_ev(line)) > 1e-9


def test_level_ball_ev_handles_push() -> None:
    line = 0.0
    assert devig.asian_handicap_legs(line) == [0.0]
    assert abs(devig.asian_handicap_ev(DIST, line, PRICE) - naive_cover_ev(line)) > 1e-9


def test_integer_favorite_ev_handles_push() -> None:
    line = -1.0
    assert devig.asian_handicap_legs(line) == [-1.0]
    assert abs(devig.asian_handicap_ev(DIST, line, PRICE) - naive_cover_ev(line)) > 1e-9


def test_uncertainty_gate_stresses_distribution_downward() -> None:
    sigma = devig.uncertainty_total([0.03, 0.04])
    stressed = devig.adverse_shift_distribution(DIST, -0.25, PRICE, sigma)
    assert abs(sum(stressed.values()) - 1.0) < 1e-9
    assert devig.asian_handicap_ev(stressed, -0.25, PRICE) < devig.asian_handicap_ev(DIST, -0.25, PRICE)


def test_water_and_malay_odds_normalize_to_decimal() -> None:
    assert abs(devig.to_decimal(0.95, "water") - 1.95) < 1e-9
    assert abs(devig.to_decimal(0.95, "hk") - 1.95) < 1e-9
    assert abs(devig.to_decimal(0.95, "malay") - 1.95) < 1e-9
    assert abs(devig.to_decimal(-0.95, "malay") - (1.0 + 1.0 / 0.95)) < 1e-9


def test_decimal_odds_must_be_greater_than_one() -> None:
    try:
        devig.no_vig([0.95, 0.90])
    except ValueError as exc:
        assert "decimal odds must be > 1.0" in str(exc)
    else:
        raise AssertionError("non-decimal water odds should not be silently accepted as decimal")


if __name__ == "__main__":
    test_quarter_ball_ev_uses_split_legs()
    test_level_ball_ev_handles_push()
    test_integer_favorite_ev_handles_push()
    test_uncertainty_gate_stresses_distribution_downward()
    test_water_and_malay_odds_normalize_to_decimal()
    test_decimal_odds_must_be_greater_than_one()
    print("devig math tests PASS")
