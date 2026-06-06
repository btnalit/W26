#!/usr/bin/env python3
"""Margin-distribution helper for Asian handicap analysis.

The analyst must not price Asian handicap from 1X2 probabilities alone. This
helper turns a score matrix into a selected-side goal-margin distribution. A
Poisson matrix builder is included as a lightweight baseline when a richer
Dixon-Coles/penaltyblog matrix is not available.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable


def poisson_pmf(lam: float, goals: int) -> float:
    if lam <= 0:
        raise ValueError("expected goals must be positive")
    if goals < 0:
        raise ValueError("goals must be non-negative")
    return math.exp(-lam) * (lam**goals) / math.factorial(goals)


def poisson_score_matrix(home_xg: float, away_xg: float, max_goals: int = 10) -> list[list[float]]:
    if max_goals < 3:
        raise ValueError("max_goals must be at least 3")
    matrix: list[list[float]] = []
    for home_goals in range(max_goals + 1):
        row = []
        for away_goals in range(max_goals + 1):
            row.append(poisson_pmf(home_xg, home_goals) * poisson_pmf(away_xg, away_goals))
        matrix.append(row)
    total = sum(sum(row) for row in matrix)
    if total <= 0:
        raise ValueError("score matrix total probability must be positive")
    return [[value / total for value in row] for row in matrix]


def margin_distribution_from_score_matrix(matrix: Iterable[Iterable[float]]) -> dict[int, float]:
    distribution: dict[int, float] = {}
    total = 0.0
    for home_goals, row in enumerate(matrix):
        for away_goals, raw_probability in enumerate(row):
            probability = float(raw_probability)
            if probability < 0:
                raise ValueError("score matrix probabilities must be non-negative")
            margin = home_goals - away_goals
            distribution[margin] = distribution.get(margin, 0.0) + probability
            total += probability
    if total <= 0:
        raise ValueError("score matrix probabilities must sum to a positive value")
    return {margin: probability / total for margin, probability in sorted(distribution.items())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home-xg", type=float, required=True)
    parser.add_argument("--away-xg", type=float, required=True)
    parser.add_argument("--max-goals", type=int, default=10)
    args = parser.parse_args()

    matrix = poisson_score_matrix(args.home_xg, args.away_xg, args.max_goals)
    margins = margin_distribution_from_score_matrix(matrix)
    payload = {
        "model_contract": "score_matrix_to_margin_distribution",
        "matrix_source": "poisson_baseline",
        "home_xg": args.home_xg,
        "away_xg": args.away_xg,
        "max_goals": args.max_goals,
        "margin_probabilities": {str(margin): probability for margin, probability in margins.items()},
        "sum_probability": sum(margins.values()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
