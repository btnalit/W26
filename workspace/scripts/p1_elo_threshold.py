#!/usr/bin/env python3
"""
P1 validation part 2: Elo baseline Brier + competitive bucket threshold analysis.
Computes Elo ratings from martj42 historical data, then compares Elo Brier
vs Model Brier on competitive bucket (40-75%).

Combines B (Elo proxy for market) + C (competitive bucket standalone threshold).
"""

import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from collections import defaultdict

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
    "FIFA World Cup qualification",
    "UEFA Euro", "UEFA Euro qualification",
    "African Cup of Nations", "African Cup of Nations qualification",
    "Copa América", "Copa América qualification",
    "AFC Asian Cup", "AFC Asian Cup qualification",
    "CONCACAF Gold Cup", "CONCACAF Gold Cup qualification",
    "CONCACAF Nations League",
    "UEFA Nations League",
    "OFC Nations Cup",
}
FRIENDLY_TOURNAMENTS = {"Friendly"}

# Elo parameters
ELO_INIT = 1500
ELO_K = 32  # Standard K-factor
HOME_ADVANTAGE_ELO = 100  # ~100 Elo points for home field
DRAW_PROB_BASE = 0.25  # Base draw probability at equal Elo
DRAW_DECAY = 400  # Elo points gap where draw prob halves

# Redefine bucket ranges inline for independence
BUCKET_RANGES = {
    "tossup": (0.0, 0.40),
    "competitive": (0.40, 0.75),
    "blowout": (0.75, 1.0),
}


def load_csv(csv_path: str) -> list[dict]:
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["home_score"] = int(row["home_score"])
                row["away_score"] = int(row["away_score"])
                row["neutral"] = row.get("neutral", "").upper() == "TRUE"
                row["date_dt"] = datetime.strptime(row["date"], "%Y-%m-%d").date()
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
    if days_ago < 0:
        return 0
    decay = 0.5 ** (days_ago / TIME_DECAY_HALFLIFE_DAYS)
    return w * decay


def actual_to_vector(home_score, away_score):
    if home_score > away_score:
        return [1.0, 0.0, 0.0]
    elif home_score == away_score:
        return [0.0, 1.0, 0.0]
    else:
        return [0.0, 0.0, 1.0]


def brier_score(p, actual):
    return sum((p[i] - actual[i]) ** 2 for i in range(3))


def log_loss(p, actual, eps=1e-15):
    clipped = [max(min(x, 1 - eps), eps) for x in p]
    return -sum(actual[i] * math.log(clipped[i]) for i in range(3))


def elo_expected(diff: float) -> float:
    """Expected score for player with rating advantage `diff`."""
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def elo_probabilities(home_elo: float, away_elo: float, neutral: bool) -> list[float]:
    """
    Convert Elo rating difference to [home_win, draw, away_win] probabilities.
    Uses a simple Elo + draw model.
    """
    if neutral:
        dr = home_elo - away_elo
    else:
        dr = home_elo - away_elo + HOME_ADVANTAGE_ELO

    # Expected score (home_win_prob + 0.5 * draw_prob in Elo terms)
    expected_home = elo_expected(dr)

    # Estimate draw probability from rating gap
    gap = abs(dr)
    draw_prob = DRAW_PROB_BASE * math.exp(-gap / DRAW_DECAY)
    draw_prob = min(draw_prob, 0.45)  # cap

    # Remaining probability splits by expected score
    remaining = 1.0 - draw_prob
    home_win_prob = expected_home * remaining
    away_win_prob = remaining - home_win_prob

    # Clamp
    home_win_prob = max(0.01, min(0.98, home_win_prob))
    draw_prob = max(0.01, min(0.45, draw_prob))
    away_win_prob = max(0.01, min(0.98, 1.0 - home_win_prob - draw_prob))

    norm = home_win_prob + draw_prob + away_win_prob
    return [home_win_prob / norm, draw_prob / norm, away_win_prob / norm]


def run_elo_and_model():
    csv_path = None
    if os.path.exists(SNAPSHOT_DIR):
        files = sorted([f for f in os.listdir(SNAPSHOT_DIR)
                        if f.startswith("results-") and f.endswith(".csv")])
        csv_path = os.path.join(SNAPSHOT_DIR, files[-1]) if files else None
    if not csv_path:
        print(json.dumps({"error": "No data snapshot found"}))
        sys.exit(1)

    all_rows = load_csv(csv_path)
    print(json.dumps({"stage": "data_loaded", "total_rows": len(all_rows)}))

    # Split into pre-2023 (Elo training + DC training) and 2023+ (test)
    train_end = datetime.strptime("2022-12-31", "%Y-%m-%d").date()
    test_start = datetime.strptime("2023-01-01", "%Y-%m-%d").date()

    # Sort by date for Elo
    all_rows_sorted = sorted(all_rows, key=lambda r: r["date_dt"])

    # ====================================================================
    # PART 1: Elo ratings over ALL historical data
    # ====================================================================
    print(json.dumps({"stage": "elo_computing", "total_matches": len(all_rows_sorted)}))

    elo = defaultdict(lambda: ELO_INIT)  # team -> current Elo
    elo_predictions = []  # Store predictions on 2023+ test set

    for row in all_rows_sorted:
        ht, at = row["home_team"], row["away_team"]
        h_elo = elo[ht]
        a_elo = elo[at]
        neutral = row["neutral"]

        # Predict if this is a test match (2023+)
        if row["date_dt"] >= test_start:
            p = elo_probabilities(h_elo, a_elo, neutral)
            actual = actual_to_vector(row["home_score"], row["away_score"])
            brier = brier_score(p, actual)
            ll = log_loss(p, actual)
            fav_prob = max(p)
            fav_idx = max(range(3), key=lambda i: p[i])
            labels = ["home", "draw", "away"]
            elo_predictions.append({
                "date": row["date"],
                "home": ht,
                "away": at,
                "score": f"{row['home_score']}-{row['away_score']}",
                "p": p,
                "fav_label": labels[fav_idx],
                "fav_prob": fav_prob,
                "brier": brier,
                "log_loss": ll,
            })

        # Elo update (on all matches, not just test)
        # Actual score: 1=home win, 0.5=draw, 0=away win
        home_goals, away_goals = row["home_score"], row["away_score"]
        if neutral:
            dr = h_elo - a_elo
        else:
            dr = h_elo - a_elo + HOME_ADVANTAGE_ELO

        expected_home = elo_expected(dr)

        if home_goals > away_goals:
            actual_home = 1.0
        elif home_goals == away_goals:
            actual_home = 0.5
        else:
            actual_home = 0.0

        new_h_elo = h_elo + ELO_K * (actual_home - expected_home)
        new_a_elo = a_elo + ELO_K * ((1 - actual_home) - (1 - expected_home))

        elo[ht] = new_h_elo
        elo[at] = new_a_elo

    # ====================================================================
    # PART 2: DC Model predictions on test set
    # ====================================================================
    print(json.dumps({"stage": "dc_model_fitting"}))

    # Filter training data for DC model (last 8 years)
    train_rows = [r for r in all_rows
                  if r["date_dt"] <= train_end
                  and r["date_dt"] >= train_end.replace(year=train_end.year - LAST_N_YEARS)]

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

    print(json.dumps({"stage": "dc_fit", "n_train": len(goals_h)}))

    model = DixonColesGoalModel(
        goals_home=np.array(goals_h, dtype=float),
        goals_away=np.array(goals_a, dtype=float),
        teams_home=np.array(teams_h),
        teams_away=np.array(teams_a),
        weights=np.array(weights, dtype=float),
        neutral_venue=np.array(neutral, dtype=bool),
    )
    model.fit()

    # Predict on test set
    model_predictions = []
    n_skipped = 0
    for row in all_rows:
        if row["date_dt"] < test_start:
            continue
        ht, at = row["home_team"], row["away_team"]
        try:
            grid = model.predict(ht, at, max_goals=MAX_GOALS, normalize=True, neutral_venue=bool(row["neutral"]))
        except (ValueError, KeyError):
            n_skipped += 1
            continue

        p = [grid.home_win, grid.draw, grid.away_win]
        actual = actual_to_vector(row["home_score"], row["away_score"])
        brier = brier_score(p, actual)
        ll = log_loss(p, actual)
        fav_prob = max(p)
        fav_idx = max(range(3), key=lambda i: p[i])
        labels = ["home", "draw", "away"]
        model_predictions.append({
            "date": row["date"],
            "home": ht,
            "away": at,
            "score": f"{row['home_score']}-{row['away_score']}",
            "p": p,
            "fav_label": labels[fav_idx],
            "fav_prob": fav_prob,
            "brier": brier,
            "log_loss": ll,
        })

    print(json.dumps({"stage": "dc_predicted", "n_test": len(model_predictions), "skipped": n_skipped}))

    # ====================================================================
    # PART 3: Bucket analysis for BOTH Elo and Model
    # ====================================================================
    def bucket_analysis(predictions, label):
        buckets = defaultdict(lambda: {"n": 0, "brier": 0.0, "ll": 0.0})
        for m in predictions:
            fp = m["fav_prob"]
            for bname, (lo, hi) in BUCKET_RANGES.items():
                if lo <= fp < hi:
                    buckets[bname]["n"] += 1
                    buckets[bname]["brier"] += m["brier"]
                    buckets[bname]["ll"] += m["log_loss"]
                    break
            else:
                buckets["blowout"]["n"] += 1
                buckets["blowout"]["brier"] += m["brier"]
                buckets["blowout"]["ll"] += m["log_loss"]

        result = {}
        for bname in ["tossup", "competitive", "blowout"]:
            b = buckets[bname]
            n = b["n"]
            result[bname] = {
                "n": n,
                "brier": round(b["brier"] / n, 4) if n > 0 else None,
                "log_loss": round(b["ll"] / n, 4) if n > 0 else None,
            }
        return result

    elo_buckets = bucket_analysis(elo_predictions, "elo")
    model_buckets = bucket_analysis(model_predictions, "model")

    # ====================================================================
    # PART 4: Build output
    # ====================================================================
    comp_m = model_buckets["competitive"]
    comp_e = elo_buckets["competitive"]
    blow_m = model_buckets["blowout"]
    blow_e = elo_buckets["blowout"]
    overall_model_brier = sum(m["brier"] for m in model_predictions) / len(model_predictions)

    # Model vs Elo gap on competitive bucket
    model_vs_elo_comp = round(comp_m["brier"] - comp_e["brier"], 4)

    # Model vs naive (0.667) on competitive
    model_vs_naive_comp = round(comp_m["brier"] - 0.6667, 4)

    key_findings = [
        f"Model overall Brier: {round(overall_model_brier, 4)} (current pass: <0.55 →",
        f"  {'PASS' if overall_model_brier < 0.55 else 'FAIL'})",
        f"",
        f"Elo competitive Brier: {comp_e['brier']} (n={comp_e['n']}) → proxy for Pinnacle baseline",
        f"Model competitive Brier: {comp_m['brier']} (n={comp_m['n']})",
        f"Model vs Elo gap (competitive): {model_vs_elo_comp:+.4f}",
        f"Model vs naive gap (competitive): {model_vs_naive_comp:+.4f}",
        f"",
        f"Blowout: Elo Brier={blow_e['brier']}, Model Brier={blow_m['brier']}",
    ]

    if model_vs_elo_comp < 0:
        key_findings.append(f"✅ Model BEATS Elo on competitive bucket by {abs(model_vs_elo_comp)*100:.2f}pp")
    else:
        key_findings.append(f"⚠️ Model is WORSE than Elo on competitive bucket by {model_vs_elo_comp*100:.2f}pp")

    # Elo Brier as threshold: what if we used Elo as Pinnacle proxy?
    # brier_gap = model_comp_brier - elo_comp_brier
    # Suggested tolerance = how much worse than free baseline is acceptable?

    output = {
        "analysis_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_model_predictions": len(model_predictions),
        "n_elo_predictions": len(elo_predictions),
        "model_buckets": model_buckets,
        "elo_buckets": elo_buckets,
        "model_overall_brier": round(overall_model_brier, 4),
        "model_vs_elo_competitive_gap": model_vs_elo_comp,
        "model_vs_naive_competitive_gap": model_vs_naive_comp,
        "key_findings": key_findings,
        "data_note": "Elo is a free, objective baseline. NOT Pinnacle quality — Pinnacle would be better (lower Brier). "
                     "If model beats Elo, it's informative but may still trail Pinnacle. "
                     "If model trails Elo, it's definitely not ready.",
        "recommendation": None,
    }

    # Determine recommendation
    if model_vs_elo_comp < -0.02:
        output["recommendation"] = (
            f"Model beats Elo by {abs(model_vs_elo_comp)*100:.1f}pp on competitive bucket. "
            f"Informative but not sharp — continue diagnostic mode. "
            f"brier_gap vs Pinnacle (unknown) is likely wider than vs Elo."
        )
    elif model_vs_elo_comp < 0.02:
        output["recommendation"] = (
            f"Model and Elo are essentially tied on competitive bucket ({model_vs_elo_comp*100:.1f}pp gap). "
            f"Model shows no predictive improvement over a free Elo baseline. "
            f"Holding at diagnostic is correct — model is not adding value vs basic Elo."
        )
    else:
        output["recommendation"] = (
            f"Model is WORSE than Elo by {model_vs_elo_comp*100:.1f}pp on competitive bucket. "
            f"A free Elo baseline beats the DC model on the matches that matter. "
            f"Model should remain info-only indefinitely."
        )

    # Competitive threshold analysis
    output["competitive_threshold_analysis"] = {
        "current_competitive_brier": comp_m["brier"],
        "current_overall_brier": round(overall_model_brier, 4),
        "naive_benchmark": 0.6667,
        "brier_improvement_vs_naive": f"{abs(model_vs_naive_comp)*100:.1f}pp {'below' if model_vs_naive_comp<0 else 'above'} naive",
        "would_pass_0_55": "FAIL" if comp_m["brier"] > 0.55 else "PASS",
        "would_pass_0_50": "FAIL" if comp_m["brier"] > 0.50 else "PASS",
        "would_pass_0_60": "PASS" if comp_m["brier"] < 0.60 else "FAIL",
    }

    print(json.dumps(output, indent=2, ensure_ascii=False))

    # Save
    out_path = os.path.join(WORKSPACE, "reports", "artifacts", "p1-elo-threshold-analysis.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n[ok] Written to {out_path}")


if __name__ == "__main__":
    run_elo_and_model()
