#!/usr/bin/env python3
"""
P1 validation: Brier stratification + brier_gap analysis.
Fits DC model on 2018-2022, predicts 2023+ matches, computes Brier
per competitive bucket (favorite implied 40-75% vs >75%).

NOTE: Historical odds data (Pinnacle) not available for 3484 holdout matches.
This run computes model Brier per bucket to verify the stratification hypothesis.
For Pinnacle baseline (方案B), see discussion at end.
"""

import csv
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

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


def get_latest_snapshot():
    if not os.path.exists(SNAPSHOT_DIR):
        return None
    files = sorted([f for f in os.listdir(SNAPSHOT_DIR)
                    if f.startswith("results-") and f.endswith(".csv")])
    return os.path.join(SNAPSHOT_DIR, files[-1]) if files else None


def main():
    csv_path = get_latest_snapshot()
    if not csv_path:
        print(json.dumps({"error": "No data snapshot found"}))
        sys.exit(1)

    rows = load_csv(csv_path)

    train_end = datetime.strptime("2022-12-31", "%Y-%m-%d").date()
    test_start = datetime.strptime("2023-01-01", "%Y-%m-%d").date()

    train_rows = [r for r in rows
                  if r["date_dt"] <= train_end
                  and r["date_dt"] >= train_end.replace(year=train_end.year - LAST_N_YEARS)]
    test_rows = [r for r in rows if r["date_dt"] >= test_start]

    print(json.dumps({
        "stage": "data_loaded",
        "total_rows": len(rows),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "train_period": f"{train_rows[0]['date']} to {train_rows[-1]['date']}",
        "test_period": f"{test_rows[0]['date']} to {test_rows[-1]['date']}",
    }, indent=2))

    # Fit model
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
    print(json.dumps({"stage": "fitting", "n_train_weighted": n_train}))

    model = DixonColesGoalModel(
        goals_home=np.array(goals_h, dtype=float),
        goals_away=np.array(goals_a, dtype=float),
        teams_home=np.array(teams_h),
        teams_away=np.array(teams_a),
        weights=np.array(weights, dtype=float),
        neutral_venue=np.array(neutral, dtype=bool),
    )
    model.fit()

    # Predict on test set — record per-match data
    per_match = []
    n_skipped = 0
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
        ll = log_loss(p, actual)

        # Favorite probability = max(p_home, p_draw, p_away)
        fav_idx = max(range(3), key=lambda i: p[i])
        fav_prob = p[fav_idx]
        labels = ["home", "draw", "away"]

        per_match.append({
            "date": row["date"],
            "home": row["home_team"],
            "away": row["away_team"],
            "score": f"{row['home_score']}-{row['away_score']}",
            "tournament": row["tournament"],
            "p": p,
            "fav_label": labels[fav_idx],
            "fav_prob": fav_prob,
            "brier": brier,
            "log_loss": ll,
        })

    # Compute Brier per bucket
    buckets = defaultdict(lambda: {"n": 0, "brier": 0.0, "ll": 0.0, "matches": []})

    for m in per_match:
        fp = m["fav_prob"]
        for bname, (lo, hi) in BUCKET_RANGES.items():
            if lo <= fp < hi:
                buckets[bname]["n"] += 1
                buckets[bname]["brier"] += m["brier"]
                buckets[bname]["ll"] += m["log_loss"]
                buckets[bname]["matches"].append(m)
                break
        else:
            # Exactly 1.0
            buckets["blowout"]["n"] += 1
            buckets["blowout"]["brier"] += m["brier"]
            buckets["blowout"]["ll"] += m["log_loss"]
            buckets["blowout"]["matches"].append(m)

    # Aggregation
    result_buckets = {}
    for bname in ["tossup", "competitive", "blowout"]:
        b = buckets[bname]
        n = b["n"]
        result_buckets[bname] = {
            "n": n,
            "pct": round(n / len(per_match) * 100, 1),
            "brier": round(b["brier"] / n, 4) if n > 0 else None,
            "log_loss": round(b["ll"] / n, 4) if n > 0 else None,
            "fav_prob_range": BUCKET_RANGES[bname],
        }

    # Overall Brier (weighted average, same as current calibration)
    overall_brier = sum(m["brier"] for m in per_match) / len(per_match)
    overall_ll = sum(m["log_loss"] for m in per_match) / len(per_match)

    # Naive benchmark per bucket: compare uniform [1/3,1/3,1/3] against actual results
    naive = {}
    for bname in ["tossup", "competitive", "blowout"]:
        b = buckets[bname]
        if b["n"] == 0:
            naive[bname] = None
            continue
        naive_brier = sum(
            brier_score([1/3, 1/3, 1/3],
                        actual_to_vector(int(m["score"].split("-")[0]),
                                          int(m["score"].split("-")[1])))
            for m in b["matches"]
        ) / b["n"]
        naive[bname] = round(naive_brier, 4)

    # ALSO compute Brier using Elo/FIFA ranking as "market" baseline for competitive bucket
    # Since we don't have historical Pinnacle odds, use the prior:
    # For each match, "Pinnacle-like" baseline = Elo implied probability
    # But we don't have Elo data either — so note this gap

    # Top tournament breakdown w/ bucket info
    tourn_bucket = defaultdict(lambda: {"n": 0, "competitive_n": 0, "blowout_n": 0, "tossup_n": 0})

    output = {
        "analysis_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_predictions": len(per_match),
        "skipped": n_skipped,
        "overall": {
            "brier": round(overall_brier, 4),
            "log_loss": round(overall_ll, 4),
            "naive_benchmark": round(
                sum(brier_score([1/3, 1/3, 1/3],
                                actual_to_vector(int(m["score"].split("-")[0]),
                                                  int(m["score"].split("-")[1])))
                    for m in per_match) / len(per_match), 4),
        },
        "buckets": result_buckets,
        "naive_per_bucket": naive,
        "key_findings": [],
        "data_gaps": [],
    }

    # Key findings
    cb = result_buckets["competitive"]
    bb = result_buckets["blowout"]
    tb = result_buckets["tossup"]

    f1 = f"Competitive bucket (40-75%): n={cb['n']} ({cb['pct']}% of holdout), Brier={cb['brier']}"
    f2 = f"Blowout bucket (>75%): n={bb['n']} ({bb['pct']}% of holdout), Brier={bb['brier']}"
    f3 = f"Tossup bucket (<40%): n={tb['n']} ({tb['pct']}% of holdout), Brier={tb['brier']}"

    output["key_findings"].extend([f1, f2, f3])

    diff = cb["brier"] - bb["brier"]
    output["competitive_vs_blowout_gap"] = round(diff, 4)
    if diff > 0.05:
        output["key_findings"].append(
            f"Competitive Brier is {diff*100:.1f}pp WORSE than blowout Brier — "
            "confirms stratification hypothesis: overall Brier is artificially lowered by easy matches."
        )
    else:
        output["key_findings"].append(
            f"Competitive vs blowout gap is only {diff*100:.1f}pp — "
            "stratification effect is smaller than expected."
        )

    # Check if competitive Brier is informative
    naive_comp = naive["competitive"]
    if naive_comp and cb["brier"]:
        gap_vs_naive = round(cb["brier"] - naive_comp, 4)
        output["competitive_vs_naive_gap"] = gap_vs_naive
        if gap_vs_naive < 0:
            output["key_findings"].append(
                f"Competitive Brier ({cb['brier']}) is {abs(gap_vs_naive)*100:.1f}pp BELOW "
                f"competitive naive benchmark ({naive_comp}) — model beats random on competitive matches."
            )
        else:
            output["key_findings"].append(
                f"Competitive Brier ({cb['brier']}) is {gap_vs_naive*100:.1f}pp ABOVE "
                f"competitive naive benchmark ({naive_comp}) — model does NOT beat random on competitive matches!"
            )

    # Data gaps
    output["data_gaps"].append(
        "Historical Pinnacle odds not available for 3484 holdout matches. "
        "Cannot compute Pinnacle brier_gap without acquiring historical odds dataset. "
        "Alternative: compute competitive Brier as standalone threshold (absolute), "
        "or acquire odds history from the-odds-api / oddspapi / other provider."
    )

    # How many competitive bucket matches are from each tournament
    output["by_tournament"] = {}
    for m in per_match:
        tourn_bucket[m["tournament"]]["n"] += 1
        fp = m["fav_prob"]
        for bname, (lo, hi) in BUCKET_RANGES.items():
            if lo <= fp < hi:
                tourn_bucket[m["tournament"]][f"{bname}_n"] += 1
                break
        else:
            tourn_bucket[m["tournament"]]["blowout_n"] += 1

    output["by_tournament"] = dict(sorted(
        {t: v for t, v in tourn_bucket.items() if v["n"] >= 20}.items(),
        key=lambda x: -x[1]["n"]
    )[:15])

    print(json.dumps(output, indent=2, ensure_ascii=False))

    # Save result
    out_path = os.path.join(WORKSPACE, "reports", "artifacts", "p1-stratification-analysis.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n[ok] Written to {out_path}")


if __name__ == "__main__":
    main()
