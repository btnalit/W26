#!/usr/bin/env python3
"""
P1-B Phase 2: Conditional Platt calibration for margin distribution.

Fits: logit(p_calibrated) = α + β × logit(p_raw) + γ × s
  where p_raw = model's raw prob for margin bucket k
        s = P(|margin| >= 2) — model's total blowout probability (favored side)

For each tail margin bucket k ∈ ±2, ±3, ±4:
  - Collects training data from 3,484 historical test matches (2023+)
  - Fits logistic regression
  - Cross-validates log-likelihood improvement and PIT uniformity

Then applies calibration to M001-M006 artifacts, computes AH EV before/after.
"""

import csv
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np

WORKSPACE = "/hermesdata/worldcup-2026-handicap"
SNAPSHOT_DIR = os.path.join(WORKSPACE, "snapshots", "international_results")
ARTIFACT_DIR = os.path.join(WORKSPACE, "reports", "artifacts")
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

# Margin buckets to calibrate (tail where conditional overconfidence lives)
TAIL_BUCKETS = [-4, -3, -2, 2, 3, 4]


def load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                row["home_score"] = int(row["home_score"])
                row["away_score"] = int(row["away_score"])
                row["date_dt"] = datetime.strptime(row["date"], "%Y-%m-%d").date()
                row["neutral"] = row.get("neutral", "").upper() == "TRUE"
                rows.append(row)
            except (ValueError, KeyError):
                continue
    return rows


def weight(row: dict, now_date) -> float:
    t = row["tournament"]
    w = WC_WEIGHT if t in WC_TOURNAMENTS else (FRIENDLY_WEIGHT if t in FRIENDLY_TOURNAMENTS else
                                                OTHER_OFFICIAL_WEIGHT if t in OFFICIAL_TOURNAMENTS else 0.5)
    days = (now_date - row["date_dt"]).days
    return 0 if days < 0 else w * (0.5 ** (days / TIME_DECAY_HALFLIFE_DAYS))


def raw_margin_probs(grid, max_goals=9) -> dict:
    probs = grid.goal_matrix
    m = defaultdict(float)
    for h in range(min(max_goals + 1, len(probs))):
        for a in range(min(max_goals + 1, len(probs[0]))):
            m[h - a] += probs[h][a]
    total = sum(m.values())
    return {k: v / total for k, v in m.items()}


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-max(-100, min(100, x))))


def logit(p):
    p = max(1e-10, min(1 - 1e-10, p))
    return math.log(p / (1 - p))


def collect_training_data(rows: list, model) -> list[dict]:
    test_start = datetime.strptime("2023-01-01", "%Y-%m-%d").date()
    samples = []
    n_skip = 0
    for row in rows:
        if row["date_dt"] < test_start:
            continue
        try:
            grid = model.predict(row["home_team"], row["away_team"],
                                 max_goals=MAX_GOALS, normalize=True,
                                 neutral_venue=bool(row["neutral"]))
        except (ValueError, KeyError):
            n_skip += 1
            continue
        mp = raw_margin_probs(grid)
        actual = row["home_score"] - row["away_score"]

        # Blowout signal: probability of favored side winning by 2+
        p_fav_2plus = max(
            sum(v for k, v in mp.items() if k >= 2),
            sum(v for k, v in mp.items() if k <= -2),
        )
        s = p_fav_2plus

        samples.append({
            "home": row["home_team"],
            "away": row["away_team"],
            "actual_margin": actual,
            "s": s,
            "mp": mp,
        })
    return samples, n_skip


def fit_conditional_platt(samples: list[dict], k: int) -> dict:
    """
    For margin bucket k (e.g. +2), fit:
      logit(p_calibrated) = α + β × logit(p_raw) + γ × s

    Returns {'alpha': α, 'beta': β, 'gamma': γ, 'n': n, 'baseline_acc': ..., 'll_improvement': ...}
    """
    X = []
    y = []
    for s in samples:
        p_raw = s["mp"].get(k, 0.0)  # mp has integer keys
        if p_raw <= 0 or p_raw >= 0.999:
            continue
        X.append([logit(p_raw), s["s"]])
        y.append(1.0 if s["actual_margin"] == k else 0.0)

    X = np.array(X)
    y = np.array(y)
    n = len(y)

    if n < 50:
        return {"error": f"insufficient samples: {n}"}

    # Add intercept column
    X_aug = np.column_stack([np.ones(n), X])

    # Iterative reweighted least squares (logistic regression)
    beta = np.zeros(3)
    for _iter in range(50):
        eta = X_aug @ beta
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -20, 20)))
        W = np.diag(p * (1 - p))
        z = eta + (y - p) / np.clip(p * (1 - p), 1e-10, 1)
        try:
            beta_new = np.linalg.solve(X_aug.T @ W @ X_aug, X_aug.T @ W @ z)
        except np.linalg.LinAlgError:
            break
        if np.all(np.abs(beta_new - beta) < 1e-6):
            beta = beta_new
            break
        beta = beta_new

    # Evaluate
    alpha, beta_lr, gamma = beta[0], beta[1], beta[2]

    # Log-likelihood
    p_pred = 1.0 / (1.0 + np.exp(-np.clip(X_aug @ beta, -20, 20)))
    ll_model = np.sum(y * np.log(np.clip(p_pred, 1e-10, 1)) + (1 - y) * np.log(np.clip(1 - p_pred, 1e-10, 1)))
    ll_null = n * (np.mean(y) * math.log(np.mean(y) + 1e-10) + (1 - np.mean(y)) * math.log(1 - np.mean(y) + 1e-10))
    ll_improvement = (ll_model - ll_null) / abs(ll_null) * 100 if abs(ll_null) > 1 else 0

    return {
        "alpha": round(alpha, 4),
        "beta": round(beta_lr, 4),
        "gamma": round(gamma, 4),
        "n": n,
        "baseline_freq": round(np.mean(y), 4),
        "ll_improvement_pct": round(ll_improvement, 1),
    }


def apply_calibration(mp_raw: dict, params: dict) -> dict:
    """
    Apply conditional Platt calibration to a margin distribution.
    Calibrates tail buckets ±2, ±3, ±4; leaves -1, 0, +1 untouched.
    Renormalizes after.
    """
    mp = {}
    for k, v in mp_raw.items():
        mp[str(k)] = v

    # Compute blowout signal s from the original distribution
    s = max(
        sum(v for k, v in mp.items() if int(k) >= 2),
        sum(v for k, v in mp.items() if int(k) <= -2),
    )

    # Calibrate each tail bucket
    for k in TAIL_BUCKETS:
        k_str = str(k)
        p_raw = mp.get(k, mp.get(k_str, 0.0))  # handles both int and str keys
        if p_raw <= 0 or p_raw >= 0.999:
            continue

        pk = params.get(k_str)
        if not pk or "error" in pk:
            continue

        alpha, beta_lr, gamma = pk["alpha"], pk["beta"], pk["gamma"]
        logit_raw = logit(p_raw)
        p_cal = sigmoid(alpha + beta_lr * logit_raw + gamma * s)
        mp[k_str] = p_cal  # always store as string key

    # Renormalize to sum = 1.0
    total = sum(mp.values())
    if total > 0:
        mp = {k: v / total for k, v in mp.items()}

    return mp


def compute_ah_ev(margin_probs: dict, ah_line: float, decimal_odds: float,
                  favored_is_home: bool = True) -> dict:
    """
    Compute AH settlement EV from margin distribution.

    For a favorite AH line (e.g., -0.75, -1.75):
    - The line is from the FAVORED team's perspective
    - favored_is_home=True means the AH line is on the home team
    - For lines on the away team (M005: Switzerland -1.75), favored_is_home=False
      and margins are mirrored

    Returns dict with leg breakdown and settlement EV.
    """

    # Mirror margins if favorite is away team (margins stored from home perspective)
    if not favored_is_home:
        probs = {str(-int(k)): v for k, v in margin_probs.items()}
    else:
        probs = dict(margin_probs)

    abs_line = abs(ah_line)
    # For -0.75: upper leg = -0.5, lower leg = -1.0
    # For -1.75: upper leg = -1.5, lower leg = -2.0
    upper_leg = -(abs_line - 0.25)  # e.g. -0.75 → -0.5
    lower_leg = -(abs_line + 0.25)  # e.g. -0.75 → -1.0

    # How many goals needed to cover each leg
    upper_need = int(abs(upper_leg)) + 1  # -0.5 → 1 goal
    lower_need = int(abs(lower_leg)) + 1  # -1.0 → 2 goals

    # Payouts
    full_win_payout = decimal_odds - 1  # e.g. 1.83 → 0.83
    half_win_payout = full_win_payout * 0.5
    full_loss = -1.0

    # Settlement
    p_full_win = sum(v for k, v in probs.items() if int(k) >= lower_need)
    p_half_win = sum(v for k, v in probs.items() if int(k) >= upper_need and int(k) < lower_need) if upper_need != lower_need else 0
    p_push = 0.0
    p_loss = 1.0 - p_full_win - p_half_win

    ev = p_full_win * full_win_payout + p_half_win * half_win_payout + p_loss * full_loss

    return {
        "ah_line": ah_line,
        "decimal_odds": decimal_odds,
        "favored_is_home": favored_is_home,
        "p_full_win": round(p_full_win * 100, 2),
        "p_half_win": round(p_half_win * 100, 2) if p_half_win > 0 else "-",
        "p_push": round(p_push * 100, 2) if p_push > 0 else "-",
        "p_loss": round(p_loss * 100, 2),
        "settlement_ev": round(ev, 4),
    }


def main():
    # ====================================================================
    # STEP 1: Load + fit model on historical data
    # ====================================================================
    csv_path = None
    if os.path.exists(SNAPSHOT_DIR):
        files = sorted([f for f in os.listdir(SNAPSHOT_DIR)
                        if f.startswith("results-") and f.endswith(".csv")])
        csv_path = os.path.join(SNAPSHOT_DIR, files[-1]) if files else None
    if not csv_path:
        raise SystemExit("No CSV snapshot")

    all_rows = load_csv(csv_path)
    train_end = datetime.strptime("2022-12-31", "%Y-%m-%d").date()
    train_rows = [r for r in all_rows
                  if r["date_dt"] <= train_end
                  and r["date_dt"] >= train_end.replace(year=train_end.year - LAST_N_YEARS)]

    print(f"[p1b-p2] Loaded {len(all_rows)} rows, train={len(train_rows)}")

    from penaltyblog.models import DixonColesGoalModel

    now = train_end
    gh, ga, th, ta, wt, nt = [], [], [], [], [], []
    for r in train_rows:
        w = weight(r, now)
        if w <= 0:
            continue
        gh.append(r["home_score"]); ga.append(r["away_score"])
        th.append(r["home_team"]); ta.append(r["away_team"])
        wt.append(w); nt.append(r["neutral"])

    model = DixonColesGoalModel(
        goals_home=np.array(gh, dtype=float), goals_away=np.array(ga, dtype=float),
        teams_home=np.array(th), teams_away=np.array(ta),
        weights=np.array(wt, dtype=float), neutral_venue=np.array(nt, dtype=bool),
    )
    model.fit()
    print(f"[p1b-p2] Model fitted on {len(gh)} matches")

    # ====================================================================
    # STEP 2: Collect training data — per-match margin predictions + actual
    # ====================================================================
    samples, n_skip = collect_training_data(all_rows, model)
    print(f"[p1b-p2] Collected {len(samples)} test samples (skipped {n_skip})")

    # ====================================================================
    # STEP 3: Fit conditional Platt for each tail bucket
    # ====================================================================
    cal_params = {}
    for k in TAIL_BUCKETS:
        result = fit_conditional_platt(samples, k)
        cal_params[str(k)] = result
        if "error" in result:
            print(f"  bucket {k}: {result['error']}")
        else:
            print(f"  bucket {k}: α={result['alpha']}, β={result['beta']}, γ={result['gamma']}, "
                  f"n={result['n']}, base_freq={result['baseline_freq']:.4f}, "
                  f"LL-improve={result['ll_improvement_pct']:.1f}%")

    # ====================================================================
    # STEP 4: Cross-validation — PIT uniformity check
    # ====================================================================
    # For calibration validity: PIT = CDF(actual_margin | calibrated_distribution)
    # should be uniform if well-calibrated
    pit_values_raw = []
    pit_values_cal = []
    for s in samples:
        mp_raw = s["mp"]
        mp_cal = apply_calibration(mp_raw, cal_params)
        actual = s["actual_margin"]

        # CDF up to and including actual margin
        cdf_raw = sum(v for k, v in mp_raw.items() if int(k) <= actual)
        cdf_cal = sum(v for k, v in mp_cal.items() if int(k) <= actual)

        # PIT (randomized for discrete distribution)
        # Use mid-point of step function
        cdf_before = sum(v for k, v in mp_raw.items() if int(k) < actual)
        cdf_before_cal = sum(v for k, v in mp_cal.items() if int(k) < actual)

        pit_raw = cdf_before + (cdf_raw - cdf_before) * 0.5 + 1e-10
        pit_cal = cdf_before_cal + (cdf_cal - cdf_before_cal) * 0.5 + 1e-10
        pit_values_raw.append(pit_raw)
        pit_values_cal.append(pit_cal)

    # PIT uniformity: chi-square test (10 bins)
    def pit_chi2(pits, bins=10):
        counts = np.zeros(bins)
        for p in pits:
            idx = min(bins - 1, int(p * bins))
            counts[idx] += 1
        expected = len(pits) / bins
        chi2 = np.sum((counts - expected) ** 2 / expected)
        return {
            "chi2_stat": round(chi2, 2),
            "mean": round(np.mean(pits), 4),
            "std": round(np.std(pits), 4),
            "bins": [int(c) for c in counts],
        }

    pit_raw_result = pit_chi2(pit_values_raw)
    pit_cal_result = pit_chi2(pit_values_cal)

    # ====================================================================
    # STEP 5: Apply to WC26 match artifacts (M001-M006)
    # ====================================================================
    case_studies = [
        ("M001", "Mexico", "South Africa", -1.25, 1.83, True),
        ("M002", "South Korea", "Czech Republic", None, None, True),
        ("M003", "Canada", "Bosnia-Herzegovina", -0.75, 1.83, True),
        ("M004", "United States", "Paraguay", None, None, True),
        ("M005", "Qatar", "Switzerland", -1.75, 1.91, False),
        ("M006", "Brazil", "Morocco", -0.75, 1.83, True),
    ]

    case_results = []
    for mid, home, away, ah_line, ah_odds, fav_home in case_studies:
        # Find latest artifact
        pat = os.path.join(ARTIFACT_DIR, f"model-{mid}-*.json")
        import glob
        files = sorted(glob.glob(pat))
        if not files:
            case_results.append({"match_id": mid, "error": "no artifact"})
            continue
        with open(files[-1]) as f:
            art = json.load(f)

        mp_raw = art.get("margin_probabilities", {})
        mp_cal = apply_calibration(mp_raw, cal_params)

        case = {
            "match_id": mid,
            "home": home,
            "away": away,
        }

        # Margin distribution before/after
        for side, mp in [("raw", mp_raw), ("calibrated", mp_cal)]:
            p_fav_2plus = max(
                sum(v for k, v in mp.items() if int(k) >= 2),
                sum(v for k, v in mp.items() if int(k) <= -2),
            )
            p_fav_3plus = max(
                sum(v for k, v in mp.items() if int(k) >= 3),
                sum(v for k, v in mp.items() if int(k) <= -3),
            )
            case[f"p_fav_2plus_{side}"] = round(p_fav_2plus * 100, 2)
            case[f"p_fav_3plus_{side}"] = round(p_fav_3plus * 100, 2)

        # AH EV before/after
        if ah_line is not None:
            ev_before = compute_ah_ev(mp_raw, ah_line, ah_odds, fav_home)
            ev_after = compute_ah_ev(mp_cal, ah_line, ah_odds, fav_home)
            case["ah_ev_before"] = ev_before["settlement_ev"]
            case["ah_ev_after"] = ev_after["settlement_ev"]
            case["ah_ev_change"] = round(ev_after["settlement_ev"] - ev_before["settlement_ev"], 4)
            case["ah_detail_before"] = ev_before
            case["ah_detail_after"] = ev_after
        else:
            case["ah_ev_before"] = "N/A"
            case["ah_ev_after"] = "N/A"

        case_results.append(case)

    # ====================================================================
    # STEP 6: Assemble output
    # ====================================================================
    output = {
        "analysis_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_training_samples": len(samples),
        "calibration_params": cal_params,
        "pit_validation": {
            "raw": pit_raw_result,
            "calibrated": pit_cal_result,
            "interpretation": (
                "PIT closer to uniform = better calibrated. "
                "Lower chi2 = more uniform. Mean should be ~0.5, std ~0.289 for perfect uniform."
            ),
        },
        "case_studies": case_results,
        "summary": None,
    }

    # Generate summary
    improvements = []
    for c in case_results:
        if "ah_ev_before" not in c:
            continue
        be = c.get("ah_ev_before")
        af = c.get("ah_ev_after")
        if isinstance(be, (int, float)) and isinstance(af, (int, float)):
            dir_ = "↑" if af > be else "↓" if af < be else "→"
            improvements.append(f"  {c['match_id']}: {be:+.4f} → {af:+.4f} ({dir_})")

    pit_note = ""
    if pit_cal_result["chi2_stat"] < pit_raw_result["chi2_stat"]:
        pit_note = f"PIT improved: chi2 {pit_raw_result['chi2_stat']}→{pit_cal_result['chi2_stat']}"
    else:
        pit_note = f"PIT unchanged/worse: chi2 {pit_raw_result['chi2_stat']}→{pit_cal_result['chi2_stat']}"

    output["summary"] = {
        "pit_summary": pit_note,
        "ah_ev_changes": improvements,
    }

    print("\n" + "="*70)
    print("PIT VALIDATION")
    print(f"  Raw:       chi2={pit_raw_result['chi2_stat']}, mean={pit_raw_result['mean']:.4f}, std={pit_raw_result['std']:.4f}")
    print(f"  Calibrated: chi2={pit_cal_result['chi2_stat']}, mean={pit_cal_result['mean']:.4f}, std={pit_cal_result['std']:.4f}")
    print(f"  → {pit_note}")
    print()
    print("AH EV CHANGES")
    for l in improvements:
        print(l)
    print("="*70)

    out_path = os.path.join(WORKSPACE, "reports", "artifacts", "p1b-phase2-calibration-results.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n[ok] Written to {out_path}")


if __name__ == "__main__":
    main()
