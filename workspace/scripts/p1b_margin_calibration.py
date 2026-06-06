#!/usr/bin/env python3
"""
P1-B Phase 1: Margin calibration analysis — compare model's predicted margin
distribution against actual historical results.

For each test match (2023+), records:
- model's predicted probability for each margin bucket (-8 to +8)
- actual margin (home_score - away_score)

Outputs calibration curve: for each bucket, predicted avg vs actual frequency.
Shows overconfidence in blowout margins (key known bias).
"""

import csv
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

import numpy as np

WORKSPACE = "/hermesdata/worldcup-2026-handicap"
SNAPSHOT_DIR = os.path.join(WORKSPACE, "snapshots", "international_results")
LAST_N_YEARS = 8
TIME_DECAY_HALFLIFE_DAYS = 730
FRIENDLY_WEIGHT = 0.3
WC_WEIGHT = 1.0
OTHER_OFFICIAL_WEIGHT = 0.8
MAX_GOALS = 9

WC_TOURNAMENTS = {"FIFA World Cup"}
OFFICIAL_TOURNAMENTS = {
    "FIFA World Cup qualification", "UEFA Euro", "UEFA Euro qualification",
    "African Cup of Nations", "African Cup of Nations qualification",
    "Copa América", "Copa América qualification",
    "AFC Asian Cup", "AFC Asian Cup qualification",
    "CONCACAF Gold Cup", "CONCACAF Gold Cup qualification",
    "CONCACAF Nations League", "UEFA Nations League", "OFC Nations Cup",
}
FRIENDLY_TOURNAMENTS = {"Friendly"}


def load_csv(csv_path: str) -> list[dict]:
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                hs, aws = int(row["home_score"]), int(row["away_score"])
                row["date_dt"] = datetime.strptime(row["date"], "%Y-%m-%d").date()
                row["home_score"] = hs
                row["away_score"] = aws
                row["neutral"] = row.get("neutral", "").upper() == "TRUE"
                rows.append(row)
            except (ValueError, KeyError):
                continue
    return rows


def compute_weight(row, now_date) -> float:
    t = row["tournament"]
    if t in WC_TOURNAMENTS:
        w = WC_WEIGHT
    elif t in FRIENDLY_TOURNAMENTS:
        w = FRIENDLY_WEIGHT
    elif t in OFFICIAL_TOURNAMENTS:
        w = OTHER_OFFICIAL_WEIGHT
    else:
        w = 0.5
    days_ago = (now_date - row["date_dt"]).days
    return 0 if days_ago < 0 else w * (0.5 ** (days_ago / TIME_DECAY_HALFLIFE_DAYS))


def margin_distribution_from_grid(grid, max_goals=MAX_GOALS):
    """Extract margin probabilities from DC model grid."""
    probs = grid.goal_matrix
    margins = defaultdict(float)
    for h in range(min(max_goals + 1, len(probs))):
        for a in range(min(max_goals + 1, len(probs[0]))):
            margins[h - a] += probs[h][a]
    total = sum(margins.values())
    return {k: round(v / total, 6) for k, v in sorted(margins.items()) if v / total > 1e-8}


def load_latest_csv():
    if not os.path.exists(SNAPSHOT_DIR):
        return None
    files = sorted([f for f in os.listdir(SNAPSHOT_DIR) if f.startswith("results-") and f.endswith(".csv")])
    return os.path.join(SNAPSHOT_DIR, files[-1]) if files else None


def main():
    csv_path = load_latest_csv()
    if not csv_path:
        print(json.dumps({"error": "No data snapshot found"}))
        sys.exit(1)

    all_rows = load_csv(csv_path)
    train_end = datetime.strptime("2022-12-31", "%Y-%m-%d").date()
    test_start = datetime.strptime("2023-01-01", "%Y-%m-%d").date()

    train_rows = [r for r in all_rows
                  if r["date_dt"] <= train_end
                  and r["date_dt"] >= train_end.replace(year=train_end.year - LAST_N_YEARS)]
    test_rows = [r for r in all_rows if r["date_dt"] >= test_start]

    print(f"[p1b] Loading {len(all_rows)} total rows")
    print(f"[p1b] Train: {len(train_rows)}, Test: {len(test_rows)}")

    # --- Fit DC model ---
    from penaltyblog.models import DixonColesGoalModel

    now_date = train_end
    goals_h, goals_a, teams_h, teams_a, weights, neutral = [], [], [], [], [], []
    for row in train_rows:
        w = compute_weight(row, now_date)
        if w <= 0:
            continue
        goals_h.append(row["home_score"])
        goals_a.append(row["away_score"])
        teams_h.append(row["home_team"])
        teams_a.append(row["away_team"])
        weights.append(w)
        neutral.append(row["neutral"])

    print(f"[p1b] Fitting DC model on {len(goals_h)} weighted matches...")
    model = DixonColesGoalModel(
        goals_home=np.array(goals_h, dtype=float),
        goals_away=np.array(goals_a, dtype=float),
        teams_home=np.array(teams_h),
        teams_away=np.array(teams_a),
        weights=np.array(weights, dtype=float),
        neutral_venue=np.array(neutral, dtype=bool),
    )
    model.fit()
    print(f"[p1b] Model fitted OK")

    # --- Predict test set, capture margin distributions ---
    margin_buckets = defaultdict(lambda: {"pred_sum": 0.0, "actual_count": 0, "n_matches": 0})
    all_margins = []  # per-match detail for analysis
    n_skipped = 0

    for row in test_rows:
        ht, at = row["home_team"], row["away_team"]
        try:
            grid = model.predict(ht, at, max_goals=MAX_GOALS, normalize=True, neutral_venue=bool(row["neutral"]))
        except (ValueError, KeyError):
            n_skipped += 1
            continue

        pred_margins = margin_distribution_from_grid(grid)
        actual_margin = row["home_score"] - row["away_score"]

        # Clamp to our bucket range -8..+8
        clamped = max(-8, min(8, actual_margin))

        # Aggregate: for each margin bucket k, model predicted some prob
        # We want: across all test matches, avg predicted prob for bucket k vs actual freq of bucket k
        for k_str, p in pred_margins.items():
            k = int(k_str)
            if -8 <= k <= 8:
                margin_buckets[k]["pred_sum"] += p
                margin_buckets[k]["n_matches"] += 1

        margin_buckets[clamped]["actual_count"] += 1

        # Also compute win-by-2+ probability (key for AH -0.75 / -1.75)
        p_win2plus = sum(v for k, v in pred_margins.items() if int(k) >= 2)
        p_win3plus = sum(v for k, v in pred_margins.items() if int(k) >= 3)

        all_margins.append({
            "date": row["date"],
            "home": ht,
            "away": at,
            "actual_margin": actual_margin,
            "actual_margin_clamped": clamped,
            "pred_win2plus": round(p_win2plus, 4),
            "pred_win3plus": round(p_win3plus, 4),
            "home_score": row["home_score"],
            "away_score": row["away_score"],
            "tournament": row["tournament"],
        })

    print(f"[p1b] Predicted {len(all_margins)} matches, skipped {n_skipped}")

    # --- Build calibration curve ---
    calibration_curve = {}
    for k in range(-8, 9):
        b = margin_buckets[k]
        n = b["n_matches"]
        avg_pred = b["pred_sum"] / n if n > 0 else 0
        actual_freq = b["actual_count"] / len(all_margins)
        bias = avg_pred - actual_freq
        calibration_curve[str(k)] = {
            "avg_predicted_prob": round(avg_pred, 6),
            "actual_frequency_fraction": round(actual_freq, 6),
            "bias_pp": round(bias * 100, 2),
            "overconfident": "YES" if bias > 0.01 else "NO",
        }

    # --- Key overconfidence metrics ---
    # Win by 2+ margins (margins >= 2)
    actual_win2plus = sum(1 for m in all_margins if m["actual_margin"] >= 2) / len(all_margins) * 100
    avg_pred_win2plus = sum(m["pred_win2plus"] for m in all_margins) / len(all_margins) * 100

    actual_win3plus = sum(1 for m in all_margins if m["actual_margin"] >= 3) / len(all_margins) * 100
    avg_pred_win3plus = sum(m["pred_win3plus"] for m in all_margins) / len(all_margins) * 100

    # Per-margin-bucket bias table (compact)
    bias_table = {}
    for k in range(-8, 9):
        c = calibration_curve[str(k)]
        bias_table[str(k)] = {
            "avg_pred%": round(c["avg_predicted_prob"] * 100, 2),
            "actual%": round(c["actual_frequency_fraction"] * 100, 2),
            "bias_pp": c["bias_pp"],
        }

    # --- Build output ---
    output = {
        "analysis_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_test_matches": len(all_margins),
        "n_skipped": n_skipped,
        "margin_calibration_curve": bias_table,
        "key_overconfidence_metrics": {
            "win2plus": {
                "avg_predicted": round(avg_pred_win2plus, 2),
                "actual_frequency": round(actual_win2plus, 2),
                "bias_pp": round(avg_pred_win2plus - actual_win2plus, 2),
                "interpretation": (
                    f"Model predicts win-by-2+ at {avg_pred_win2plus:.1f}% across all test matches, "
                    f"actual frequency is {actual_win2plus:.1f}% — "
                    f"overconfident by {avg_pred_win2plus - actual_win2plus:.1f}pp"
                ),
            },
            "win3plus": {
                "avg_predicted": round(avg_pred_win3plus, 2),
                "actual_frequency": round(actual_win3plus, 2),
                "bias_pp": round(avg_pred_win3plus - actual_win3plus, 2),
                "interpretation": (
                    f"Model predicts win-by-3+ at {avg_pred_win3plus:.1f}% across all test matches, "
                    f"actual frequency is {actual_win3plus:.1f}% — "
                    f"overconfident by {avg_pred_win3plus - actual_win3plus:.1f}pp"
                ),
            },
        },
        "headline": None,
    }

    # Set headline
    win2_bias = round(avg_pred_win2plus - actual_win2plus, 2)
    win3_bias = round(avg_pred_win3plus - actual_win3plus, 2)
    if win2_bias > 5 and win3_bias > 2:
        output["headline"] = (
            f"OVERCONFIDENCE CONFIRMED: Model systematically overestimates blowout wins. "
            f"Win-by-2+ predicted {avg_pred_win2plus:.1f}% vs actual {actual_win2plus:.1f}% "
            f"(bias +{win2_bias}pp). Win-by-3+ predicted {avg_pred_win3plus:.1f}% vs actual "
            f"{actual_win3plus:.1f}% (bias +{win3_bias}pp). "
            f"This directly explains the inflated AH EV in cases like M005 (Swiss -1.75)."
        )
    else:
        output["headline"] = (
            f"Margin overconfidence is modest: win-by-2+ bias = {win2_bias}pp, "
            f"win-by-3+ bias = {win3_bias}pp. May still matter for specific match-ups."
        )

    print(json.dumps(output, indent=2, ensure_ascii=False))

    # Save
    out_path = os.path.join(WORKSPACE, "reports", "artifacts", "p1b-margin-calibration-raw.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Also save per-match detail for deep dive
    detail_path = os.path.join(WORKSPACE, "reports", "artifacts", "p1b-per-match-margins.json")
    with open(detail_path, "w") as f:
        json.dump({
            "n": len(all_margins),
            "matches": all_margins,
        }, f, indent=2, ensure_ascii=False)

    print(f"\n[ok] Calibration curve → {out_path}")
    print(f"[ok] Per-match detail → {detail_path}")


if __name__ == "__main__":
    main()
