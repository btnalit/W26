#!/usr/bin/env python3
"""CLV computation test: construct entry + closing snapshots, assert sign and value.

The postmatch grade computes CLV as:
  CLV = entry_odds × closing_fair_prob − 1

where closing_fair_prob is the shin-devigged probability of the home outcome
at market close, and entry_odds is the decimal odds received at entry time.

Positive CLV means entry beat the closing fair price (good).
Negative CLV means entry was worse than closing (bad).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEVIG_PATH = ROOT / "skills" / "odds-analysis" / "scripts" / "devig.py"
spec = importlib.util.spec_from_file_location("devig", DEVIG_PATH)
devig = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(devig)


def compute_clv(entry_odds: float, close_home_odds: float, close_draw_odds: float, close_away_odds: float) -> dict:
    """Replicate the CLV logic from postmatch_grade."""
    close_prices = [close_home_odds, close_draw_odds, close_away_odds]
    close_nv = devig.devig_shin(close_prices)
    closing_fair_home = close_nv[0]
    clv = round(entry_odds * closing_fair_home - 1, 4)
    return {"clv": clv, "close_fair_home": closing_fair_home, "close_nv": close_nv}


def test_clv_positive_when_entry_beats_closing() -> None:
    """Entry odds longer than closing fair price → positive CLV."""
    # Closing: home team priced at 2.00 (implied fair ~50% after vig removal)
    # Entry: got 2.20 (better price)
    result = compute_clv(entry_odds=2.20, close_home_odds=2.00, close_draw_odds=3.40, close_away_odds=3.60)
    assert result["clv"] > 0, f"Expected positive CLV, got {result['clv']}"
    # Sanity: CLV ~ (2.20 * fair_home_prob - 1), fair_home_prob should be ~0.495 for 2.00/3.40/3.60
    assert 0.40 < result["close_fair_home"] < 0.55


def test_clv_negative_when_entry_worse_than_closing() -> None:
    """Entry odds shorter than closing fair price → negative CLV."""
    # Closing: home team priced at 2.50 (less confident)
    # Entry: got 2.10 (worse price for the same outcome)
    result = compute_clv(entry_odds=2.10, close_home_odds=2.50, close_draw_odds=3.20, close_away_odds=3.00)
    assert result["clv"] < 0, f"Expected negative CLV, got {result['clv']}"


def test_clv_zero_when_entry_equals_closing_fair() -> None:
    """Entry odds exactly match closing fair price → CLV ≈ 0 (within numeric tolerance)."""
    # Use the closing odds to derive the fair price, then use that as entry
    close_prices = [2.50, 3.20, 3.00]
    close_nv = devig.devig_shin(close_prices)
    fair_home_price = round(1.0 / close_nv[0], 4)
    result = compute_clv(entry_odds=fair_home_price, close_home_odds=2.50, close_draw_odds=3.20, close_away_odds=3.00)
    # CLV should be very close to zero (rounding tolerance)
    assert abs(result["clv"]) < 0.005, f"Expected CLV ≈ 0, got {result['clv']}"


def test_clv_known_value_from_odds_triplet() -> None:
    """Known odds triplet → deterministic CLV."""
    # Entry: 2.10 on home
    # Close: Pinnacle 2.00 / 3.40 / 3.60
    # devig_shin([2.0, 3.4, 3.6]) is deterministic — compute expected CLV from it
    result = compute_clv(entry_odds=2.10, close_home_odds=2.00, close_draw_odds=3.40, close_away_odds=3.60)
    close_nv = result["close_nv"]
    # Verify the devig math: close_nv should sum to ~1.0
    assert abs(sum(close_nv) - 1.0) < 0.01, f"close_nv should sum to ~1.0: {sum(close_nv)}"
    # CLV = entry_odds × closing_fair_home − 1
    expected = round(2.10 * close_nv[0] - 1, 4)
    assert result["clv"] == expected, f"CLV mismatch: {result['clv']} != {expected}"
    # The CLV sign depends on whether 2.10 beats the closing fair price
    # Closing fair price for home = 1/close_nv[0]
    fair_price = round(1.0 / close_nv[0], 4)
    expected_sign = "positive" if 2.10 > fair_price else ("negative" if 2.10 < fair_price else "zero")
    actual_sign = "positive" if result["clv"] > 0 else ("negative" if result["clv"] < 0 else "zero")
    assert expected_sign == actual_sign, (
        f"CLV sign mismatch: entry=2.10, close_fair_price={fair_price}, "
        f"clv={result['clv']}, expected_sign={expected_sign}, got={actual_sign}"
    )


def test_clv_uses_home_index_zero() -> None:
    """Verification: CLV always uses index 0 (home) from the devig output."""
    # Regardless of odds values, index 0 must be the one used
    result1 = compute_clv(entry_odds=2.00, close_home_odds=2.00, close_draw_odds=3.00, close_away_odds=4.00)
    result2 = compute_clv(entry_odds=2.00, close_home_odds=4.00, close_draw_odds=3.00, close_away_odds=2.00)
    # Reversed home/away should give different CLVs
    assert result1["clv"] != result2["clv"], "Reversed home/away should give different CLV"
