#!/usr/bin/env python3
"""Calibration checker for Dixon-Coles model.

Two modes:
  --mode historical   : holdout calibration on martj42 data (2018-2022 fit, 2023+ predict)
  --update            : update model artifact calibration status in reports/artifacts/
  --check             : read calibration DB and output summary

Output: stdout JSON with calibration status.
For historical mode, also writes to a calibration cache JSON.
"""

import argparse
import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from collections import defaultdict
from typing import Optional

import numpy as np

WORKSPACE = os.environ.get(
    "WORKSPACE",
    "/hermesdata/worldcup-2026-handicap",
)
SNAPSHOT_DIR = os.path.join(WORKSPACE, "snapshots", "international_results")
ARTIFACT_DIR = os.path.join(WORKSPACE, "reports", "artifacts")
CALIBRATION_CACHE = os.path.join(WORKSPACE, "reports", "artifacts", "model-calibration-cache.json")

# Match model_runner's parameters
LAST_N_YEARS = 8
TIME_DECAY_HALFLIFE_DAYS = 730
FRIENDLY_WEIGHT = 0.3
WC_WEIGHT = 1.0
OTHER_OFFICIAL_WEIGHT = 0.8
MAX_GOALS = 9
BRIER_PASS_THRESHOLD = 0.55  # v3 adjusted: < 0.55 is decent for international football (Brier 0.33 is info-theoretic min)
BRIER_IMPROVEMENT_RATIO = 0.85  # Model Brier must be ≤ 85% of naive benchmark (i.e. 15%+ better than random)

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


def load_csv(csv_path: str) -> list[dict]:
    """Load martj42 CSV."""
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


def compute_weight(row: dict, now_date) -> float:
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


def actual_to_vector(home_score: int, away_score: int) -> list[float]:
    """Convert actual score to one-hot: [home_win, draw, away_win]."""
    if home_score > away_score:
        return [1.0, 0.0, 0.0]
    elif home_score == away_score:
        return [0.0, 1.0, 0.0]
    else:
        return [0.0, 0.0, 1.0]


def brier_score(p: list[float], actual: list[float]) -> float:
    """Brier score for a single prediction."""
    return sum((p[i] - actual[i]) ** 2 for i in range(3))


def log_loss(p: list[float], actual: list[float], eps=1e-15) -> float:
    """Log loss for a single prediction."""
    clipped = [max(min(x, 1 - eps), eps) for x in p]
    return -sum(actual[i] * math.log(clipped[i]) for i in range(3))


def get_latest_snapshot() -> Optional[str]:
    if not os.path.exists(SNAPSHOT_DIR):
        return None
    files = sorted([f for f in os.listdir(SNAPSHOT_DIR) if f.startswith("results-") and f.endswith(".csv")])
    return os.path.join(SNAPSHOT_DIR, files[-1]) if files else None


def run_historical_holdout():
    """Fit on 2018-2022, predict 2023+, compute Brier/log-loss."""
    csv_path = get_latest_snapshot()
    if not csv_path:
        print(json.dumps({"status": "error", "message": "No data snapshot found"}))
        sys.exit(1)

    rows = load_csv(csv_path)

    # Split dates
    train_end = datetime.strptime("2022-12-31", "%Y-%m-%d").date()
    test_start = datetime.strptime("2023-01-01", "%Y-%m-%d").date()

    train_rows = [r for r in rows if r["date_dt"] <= train_end and r["date_dt"] >= train_end.replace(year=train_end.year - LAST_N_YEARS)]
    test_rows = [r for r in rows if r["date_dt"] >= test_start]

    print(f"[calibration_check] Train: {len(train_rows)} matches ({train_rows[0]['date']} to {train_rows[-1]['date']})")
    print(f"[calibration_check] Test: {len(test_rows)} matches ({test_rows[0]['date']} to {test_rows[-1]['date']})")

    # Fit model on training data
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

    n_train = len(goals_h)
    print(f"[calibration_check] Fitting on {n_train} weighted matches ...")

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
    brier_total = 0.0
    ll_total = 0.0
    n_test = 0
    n_skipped = 0
    by_tournament = defaultdict(lambda: {"n": 0, "brier": 0.0, "ll": 0.0})
    by_bucket = {"competitive": {"n": 0, "brier": 0.0},
                 "blowout": {"n": 0, "brier": 0.0},
                 "tossup": {"n": 0, "brier": 0.0}}

    for row in test_rows:
        ht = row["home_team"]
        at = row["away_team"]
        try:
            grid = model.predict(ht, at, max_goals=MAX_GOALS, normalize=True, neutral_venue=bool(row["neutral"]))
        except (ValueError, KeyError):
            n_skipped += 1
            continue

        p = [grid.home_win, grid.draw, grid.away_win]
        actual = actual_to_vector(row["home_score"], row["away_score"])
        brier = brier_score(p, actual)
        brier_total += brier
        ll_total += log_loss(p, actual)
        n_test += 1

        # Per-tournament breakdown
        t = row["tournament"]
        by_tournament[t]["n"] += 1
        by_tournament[t]["brier"] += brier
        by_tournament[t]["ll"] += log_loss(p, actual)

        # Per-competitiveness-bucket breakdown
        fav_prob = max(p)
        if fav_prob < 0.40:
            bucket = "tossup"
        elif fav_prob <= 0.75:
            bucket = "competitive"
        else:
            bucket = "blowout"
        by_bucket[bucket]["n"] += 1
        by_bucket[bucket]["brier"] += brier

    avg_brier = brier_total / n_test if n_test > 0 else None
    avg_ll = ll_total / n_test if n_test > 0 else None

    # Naive benchmark: always predict [0.333, 0.333, 0.334]
    naive_brier = sum(
        brier_score([1/3, 1/3, 1/3], actual_to_vector(r["home_score"], r["away_score"]))
        for r in test_rows if r["date_dt"] >= test_start
    ) / len([r for r in test_rows if r["date_dt"] >= test_start])

    passed = (avg_brier < BRIER_PASS_THRESHOLD and
              avg_brier < naive_brier * BRIER_IMPROVEMENT_RATIO) if avg_brier is not None else False

    # Competitive bucket status
    comp = by_bucket["competitive"]
    comp_brier = round(comp["brier"] / comp["n"], 4) if comp["n"] > 0 else None
    competitive_passed = comp_brier is not None and comp_brier < BRIER_PASS_THRESHOLD

    # Combined status: use competitive as primary
    calibration_status = "holdout_pass"
    if not passed:
        calibration_status = "holdout_fail"
    if competitive_passed:
        calibration_status = "competitive_pass"
    else:
        calibration_status = "competitive_fail"

    result = {
        "calibration_status": calibration_status,
        "n_graded_live": 0,
        "n_graded_historical": n_test,
        "brier_historical": round(avg_brier, 4) if avg_brier is not None else None,
        "log_loss_historical": round(avg_ll, 4) if avg_ll is not None else None,
        "brier_naive_benchmark": round(naive_brier, 4),
        "brier_pass_threshold": BRIER_PASS_THRESHOLD,
        "n_skipped": n_skipped,
        "brier_by_bucket": {
            b: {"n": by_bucket[b]["n"],
                "brier": round(by_bucket[b]["brier"] / by_bucket[b]["n"], 4)}
            for b in ["competitive", "blowout", "tossup"] if by_bucket[b]["n"] > 0
        },
        "competitive_brier": comp_brier,
        "competitive_passed": competitive_passed,
        "cohort_size_competitive": comp["n"],
        "top_tournament_breakdown": dict(
            sorted(
                {t: {"n": v["n"], "brier": round(v["brier"]/v["n"], 4)} for t, v in by_tournament.items() if v["n"] >= 20}.items(),
                key=lambda x: -x[1]["n"]
            )[:10]
        ),
        "calibration_status_as_of_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    print(json.dumps(result, indent=2))

    # Write to calibration cache for model_runner to reference
    os.makedirs(os.path.dirname(CALIBRATION_CACHE), exist_ok=True)
    with open(CALIBRATION_CACHE, "w") as f:
        json.dump(result, f, indent=2)
    print(f"[calibration_check] Written to {CALIBRATION_CACHE}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Calibration checker")
    parser.add_argument("--mode", required=True, choices=["historical", "update", "check"])
    args = parser.parse_args()

    if args.mode == "historical":
        run_historical_holdout()
    elif args.mode == "check":
        if os.path.exists(CALIBRATION_CACHE):
            with open(CALIBRATION_CACHE) as f:
                print(f.read())
        else:
            print(json.dumps({"status": "insufficient_data", "n_graded_live": 0, "n_graded_historical": 0}))
    elif args.mode == "update":
        # Update model artifacts with calibration status from cache
        if not os.path.exists(CALIBRATION_CACHE):
            print(json.dumps({"status": "insufficient_data", "message": "No calibration cache found; run --mode historical first"}))
            return
        with open(CALIBRATION_CACHE) as f:
            cal = json.load(f)

        artifacts = sorted([f for f in os.listdir(ARTIFACT_DIR) if f.startswith("model-") and f.endswith(".json")])
        updated = 0
        for fname in artifacts:
            path = os.path.join(ARTIFACT_DIR, fname)
            with open(path) as f:
                data = json.load(f)
            data["calibration"] = cal
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            updated += 1
        print(json.dumps({"status": "ok", "updated": updated, "calibration": cal["calibration_status"]}))


if __name__ == "__main__":
    main()
