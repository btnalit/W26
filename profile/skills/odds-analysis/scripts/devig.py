#!/usr/bin/env python3
"""Small no-vig and EV helper for odds-analysis.

Reads decimal odds from CLI and prints normalized implied probabilities.
Does not read secrets.
"""

from __future__ import annotations

import argparse
import json
import math
from typing import Iterable


SUPPORTED_ODDS_FORMATS = {"decimal", "hk", "hong_kong", "water", "malay", "american"}


def to_decimal(value: float, odds_format: str = "decimal") -> float:
    """Normalize supported odds units to decimal odds.

    `water` is treated as Hong Kong style water: net profit per 1 unit stake.
    Malay odds use positive values as net profit per 1 unit stake, and negative
    values as stake required to win 1 unit.
    """
    fmt = odds_format.strip().lower().replace("-", "_")
    if fmt not in SUPPORTED_ODDS_FORMATS:
        raise ValueError(f"unsupported odds format: {odds_format}")
    if fmt == "decimal":
        decimal = value
    elif fmt in {"hk", "hong_kong", "water"}:
        if value <= 0:
            raise ValueError("Hong Kong/water odds must be positive")
        decimal = value + 1.0
    elif fmt == "malay":
        if value == 0:
            raise ValueError("Malay odds cannot be zero")
        decimal = 1.0 + value if value > 0 else 1.0 + (1.0 / abs(value))
    else:  # american
        if abs(value) < 100:
            raise ValueError("American odds magnitude must be at least 100")
        decimal = 1.0 + (value / 100.0 if value > 0 else 100.0 / abs(value))
    validate_decimal_odds(decimal)
    return decimal


def validate_decimal_odds(value: float) -> None:
    if not math.isfinite(value):
        raise ValueError("decimal odds must be finite")
    if value <= 1.0:
        raise ValueError("decimal odds must be > 1.0 after normalization")


def normalize_odds(values: Iterable[float], odds_format: str = "decimal") -> list[float]:
    return [to_decimal(value, odds_format) for value in values]


def no_vig(decimal_odds: Iterable[float]) -> list[float]:
    normalized = list(decimal_odds)
    for odd in normalized:
        validate_decimal_odds(odd)
    implied = [1.0 / x for x in normalized]
    total = sum(implied)
    return [x / total for x in implied]


def implied(decimal_odds: Iterable[float]) -> list[float]:
    normalized = list(decimal_odds)
    for odd in normalized:
        validate_decimal_odds(odd)
    return [1.0 / odd for odd in normalized]


def devig_multiplicative(decimal_odds: Iterable[float]) -> list[float]:
    return no_vig(decimal_odds)


def devig_power(decimal_odds: Iterable[float]) -> list[float]:
    imp = implied(decimal_odds)
    lo, hi = 0.5, 5.0
    for _ in range(100):
        k = (lo + hi) / 2
        total = sum(p ** k for p in imp)
        if total > 1:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    probs = [p ** k for p in imp]
    total = sum(probs)
    return [p / total for p in probs]


def devig_shin(decimal_odds: Iterable[float]) -> list[float]:
    pi = implied(decimal_odds)
    overround = sum(pi) - 1.0
    if overround <= 1e-12:
        return no_vig(decimal_odds)
    z_max = min(0.4, max(1e-6, overround / (1.0 + overround)))
    baseline = no_vig(decimal_odds)

    def p_of(z: float) -> list[float]:
        return [
            (math.sqrt(z * z + 4 * (1 - z) * base * base * sum(pi)) - z)
            / (2 * (1 - z))
            for base in baseline
        ]

    lo, hi = 1e-9, z_max
    for _ in range(200):
        z = (lo + hi) / 2
        total = sum(p_of(z))
        if total > 1:
            lo = z
        else:
            hi = z
    probs = p_of((lo + hi) / 2)
    total = sum(probs)
    return [p / total for p in probs]


DEVIG_METHODS = {
    "shin": devig_shin,
    "power": devig_power,
    "multiplicative": devig_multiplicative,
}


def devig_three_method(decimal_odds: Iterable[float], tolerance: float = 0.02) -> dict[str, object]:
    normalized = list(decimal_odds)
    for odd in normalized:
        validate_decimal_odds(odd)
    methods = {name: fn(normalized) for name, fn in DEVIG_METHODS.items()}
    max_abs_delta = 0.0
    for idx in range(len(normalized)):
        values = [probs[idx] for probs in methods.values()]
        max_abs_delta = max(max_abs_delta, max(values) - min(values))
    return {
        "primary": "shin",
        "methods": methods,
        "probabilities": methods["shin"],
        "max_abs_delta": max_abs_delta,
        "tolerance": tolerance,
        "survives_all_methods": max_abs_delta <= tolerance,
    }


def ev(probability: float, decimal_odds: float) -> float:
    validate_decimal_odds(decimal_odds)
    if not 0 <= probability <= 1:
        raise ValueError("probability must be between 0 and 1")
    return probability * decimal_odds - 1.0


def asian_handicap_legs(line: float) -> list[float]:
    """Return settlement legs for a quarter, half, or whole Asian line."""
    quarter_units = round(line * 4)
    if abs(line * 4 - quarter_units) > 1e-9:
        raise ValueError("asian handicap line must be on quarter-goal granularity")
    if quarter_units % 2 == 0:
        return [quarter_units / 4]
    scaled_half = line * 2
    return [math.floor(scaled_half) / 2, math.ceil(scaled_half) / 2]


def settlement_return(margin: int, line: float, decimal_odds: float) -> float:
    """Net return per unit stake for a selected-side Asian handicap bet."""
    validate_decimal_odds(decimal_odds)
    returns = []
    for leg in asian_handicap_legs(line):
        adjusted = margin + leg
        if adjusted > 1e-9:
            returns.append(decimal_odds - 1.0)
        elif adjusted < -1e-9:
            returns.append(-1.0)
        else:
            returns.append(0.0)
    return sum(returns) / len(returns)


def asian_handicap_ev(margin_probs: dict[int, float], line: float, decimal_odds: float) -> float:
    validate_decimal_odds(decimal_odds)
    return sum(prob * settlement_return(margin, line, decimal_odds) for margin, prob in margin_probs.items())


def kelly_fraction_from_returns(return_probs: dict[float, float]) -> float:
    """Full Kelly fraction for arbitrary settlement returns via log-utility."""
    expected = sum(ret * prob for ret, prob in return_probs.items())
    if expected <= 0:
        return 0.0
    losses = [ret for ret in return_probs if ret < 0]
    if not losses:
        return 1.0
    upper = min(1.0, -0.999999 / min(losses))

    def derivative(fraction: float) -> float:
        return sum(prob * ret / (1.0 + fraction * ret) for ret, prob in return_probs.items())

    if derivative(upper) > 0:
        return upper
    low = 0.0
    high = upper
    for _ in range(80):
        mid = (low + high) / 2
        if derivative(mid) > 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def asian_handicap_kelly(margin_probs: dict[int, float], line: float, decimal_odds: float) -> float:
    validate_decimal_odds(decimal_odds)
    return_probs: dict[float, float] = {}
    for margin, prob in margin_probs.items():
        ret = settlement_return(margin, line, decimal_odds)
        return_probs[ret] = return_probs.get(ret, 0.0) + prob
    return kelly_fraction_from_returns(return_probs)


def parse_margin_probs(raw: str) -> dict[int, float]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("margin probabilities must be a JSON object")
    margin_probs = {int(margin): float(prob) for margin, prob in payload.items()}
    if any(prob < 0 for prob in margin_probs.values()):
        raise ValueError("margin probabilities must be non-negative")
    total = sum(margin_probs.values())
    if total <= 0:
        raise ValueError("margin probabilities must sum to a positive value")
    if abs(total - 1.0) > 0.01:
        raise ValueError("margin probabilities must sum to 1.0 within 0.01")
    return {margin: prob / total for margin, prob in margin_probs.items()}


def uncertainty_total(uncertainties: Iterable[float]) -> float:
    return math.sqrt(sum(value * value for value in uncertainties))


def adverse_shift_distribution(
    margin_probs: dict[int, float],
    line: float,
    decimal_odds: float,
    probability_mass: float,
) -> dict[int, float]:
    """Conservative stress: move mass from best settlement returns to worst."""
    shifted = dict(margin_probs)
    if probability_mass <= 0:
        return shifted
    returns = {margin: settlement_return(margin, line, decimal_odds) for margin in shifted}
    sources = sorted(shifted, key=lambda margin: returns[margin], reverse=True)
    destinations = sorted(shifted, key=lambda margin: returns[margin])
    remaining = min(probability_mass, 1.0)
    for source in sources:
        if remaining <= 1e-12:
            break
        for destination in destinations:
            if source == destination or returns[source] <= returns[destination]:
                continue
            amount = min(shifted[source], remaining)
            shifted[source] -= amount
            shifted[destination] += amount
            remaining -= amount
            break
    return shifted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("odds", nargs="+", type=float, help="Decimal odds, e.g. 2.10 3.30 3.60")
    parser.add_argument("--odds-format", default="decimal", choices=sorted(SUPPORTED_ODDS_FORMATS), help="Input odds unit for positional odds")
    parser.add_argument("--prob", type=float, help="Adjusted probability p_adj for EV")
    parser.add_argument("--price", type=float, help="Decimal price for EV")
    parser.add_argument("--price-format", default="decimal", choices=sorted(SUPPORTED_ODDS_FORMATS), help="Input odds unit for --price")
    parser.add_argument("--p-market", type=float, help="No-vig market probability for uncertainty gate")
    parser.add_argument("--ev-threshold", type=float, default=0.03, help="Minimum robust EV for qualified play")
    parser.add_argument("--uncertainty-pct", nargs="*", type=float, default=[], help="1-sigma probability uncertainties, e.g. 0.015 0.01")
    parser.add_argument("--ah-line", type=float, help="Selected-side Asian handicap line, e.g. -0.25, 0, -1")
    parser.add_argument("--ah-price", type=float, help="Decimal price for selected-side Asian handicap")
    parser.add_argument("--ah-price-format", default="decimal", choices=sorted(SUPPORTED_ODDS_FORMATS), help="Input odds unit for --ah-price")
    parser.add_argument("--margin-probs-json", help='JSON margin distribution for selected side, e.g. {"-1":0.2,"0":0.3,"1":0.5}')
    args = parser.parse_args()

    decimal_odds = normalize_odds(args.odds, args.odds_format)
    price = to_decimal(args.price, args.price_format) if args.price is not None else None
    ah_price = to_decimal(args.ah_price, args.ah_price_format) if args.ah_price is not None else None
    probs = no_vig(decimal_odds)
    payload = {
        "input_odds": args.odds,
        "input_odds_format": args.odds_format,
        "decimal_odds": decimal_odds,
        "odds_unit_contract": "all probability and EV math uses normalized decimal odds > 1.0",
        "no_vig_probabilities": probs,
        "overround": sum(1.0 / x for x in decimal_odds) - 1.0,
    }
    if args.prob is not None and price is not None:
        payload["probability_used"] = args.prob
        payload["probability_contract"] = "p_adj"
        payload["price_input"] = args.price
        payload["price_input_format"] = args.price_format
        payload["price_decimal"] = price
        payload["ev"] = ev(args.prob, price)
        if args.p_market is not None or args.uncertainty_pct:
            sigma = uncertainty_total(args.uncertainty_pct)
            robust_prob = max(0.0, min(1.0, args.prob - sigma))
            payload["uncertainty_gate"] = {
                "sigma_total": sigma,
                "robust_probability": robust_prob,
                "robust_edge": robust_prob - args.p_market if args.p_market is not None else None,
                "robust_ev": ev(robust_prob, price),
                "ev_threshold": args.ev_threshold,
                "pass": (args.p_market is None or robust_prob > args.p_market) and ev(robust_prob, price) >= args.ev_threshold,
            }
    if args.ah_line is not None or args.ah_price is not None or args.margin_probs_json is not None:
        if args.ah_line is None or ah_price is None or not args.margin_probs_json:
            parser.error("--ah-line, --ah-price, and --margin-probs-json must be supplied together")
        margin_probs = parse_margin_probs(args.margin_probs_json)
        returns = {str(margin): settlement_return(margin, args.ah_line, ah_price) for margin in sorted(margin_probs)}
        ah_payload = {
            "settlement_contract": "asian_handicap_by_legs",
            "line": args.ah_line,
            "legs": asian_handicap_legs(args.ah_line),
            "price_input": args.ah_price,
            "price_input_format": args.ah_price_format,
            "price": ah_price,
            "margin_probabilities": {str(margin): margin_probs[margin] for margin in sorted(margin_probs)},
            "returns_by_margin": returns,
            "ev": asian_handicap_ev(margin_probs, args.ah_line, ah_price),
            "kelly_fraction_full": asian_handicap_kelly(margin_probs, args.ah_line, ah_price),
        }
        if args.uncertainty_pct:
            sigma = uncertainty_total(args.uncertainty_pct)
            robust_margin_probs = adverse_shift_distribution(margin_probs, args.ah_line, ah_price, sigma)
            ah_payload["uncertainty_gate"] = {
                "sigma_total": sigma,
                "stress_method": "shift_probability_mass_from_best_returns_to_worst_returns",
                "robust_margin_probabilities": {str(margin): robust_margin_probs[margin] for margin in sorted(robust_margin_probs)},
                "robust_ev": asian_handicap_ev(robust_margin_probs, args.ah_line, ah_price),
                "robust_kelly_fraction_full": asian_handicap_kelly(robust_margin_probs, args.ah_line, ah_price),
                "ev_threshold": args.ev_threshold,
                "pass": asian_handicap_ev(robust_margin_probs, args.ah_line, ah_price) >= args.ev_threshold,
            }
        payload["asian_handicap"] = ah_payload
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
