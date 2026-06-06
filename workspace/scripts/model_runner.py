#!/usr/bin/env python3
"""Dixon-Coles model runner for wc26-handicap workspace.

Two modes:
  --mode batch      : fit on all data, predict all upcoming matches
  --mode match      : fit on all data, predict one specified match

Output: reports/artifacts/model-{match_id}-{window}.json
"""

import argparse
import csv
from collections import defaultdict
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
from penaltyblog.models import DixonColesGoalModel

WORKSPACE = os.environ.get(
    "WORKSPACE",
    "/hermesdata/worldcup-2026-handicap",
)
SNAPSHOT_DIR = os.path.join(WORKSPACE, "snapshots", "international_results")
ARTIFACT_DIR = os.path.join(WORKSPACE, "reports", "artifacts")
FIXTURE_PATH = os.path.join(
    WORKSPACE, "snapshots", "fixtures", "football-data-wc-matches-latest.json"
)
CALIBRATION_DB = os.path.join(WORKSPACE, "grading", "model_calibration.duckdb")

# Model parameters
LAST_N_YEARS = 8  # data window
TIME_DECAY_HALFLIFE_DAYS = 730  # 2 years
FRIENDLY_WEIGHT = 0.3
WC_WEIGHT = 1.0
OTHER_OFFICIAL_WEIGHT = 0.8
MAX_GOALS = 9

# Tournament categories
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

# Team name mapping: football-data fixture name → martj42 name
# Without this, batch mode crashes on mismatches (Czechia vs Czech Republic, etc.)
TEAM_NAME_MAP = {
    # football-data name → martj42 name (verified against results-20260604 CSV)
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Cape Verde Islands": "Cape Verde",
    "Congo DR": "DR Congo",
    "Czechia": "Czech Republic",
}

# Host home matches — these have real home advantage (Azteca, Toronto, US venues).
# Key: match_id → (effective_home, effective_away) for DC model prediction order.
# The artifact still records the fixture-listed home/away; only the model call uses
# this order so the home-advantage parameter applies to the actual host.
HOST_HOME_MATCHES: dict[str, tuple[str, str]] = {
    # Mexico at Azteca / Mexican venues
    "M001": ("Mexico", "South Africa"),
    "M028": ("Mexico", "South Korea"),
    "M053": ("Mexico", "Czech Republic"),  # Mexico listed as away, mapped from Czechia
    # Canada at Toronto / BC Place
    "M003": ("Canada", "Bosnia and Herzegovina"),
    "M027": ("Canada", "Qatar"),
    "M049": ("Canada", "Switzerland"),     # Canada listed as away
    # USA at US venues
    "M004": ("United States", "Paraguay"),
    "M029": ("United States", "Australia"),
    "M059": ("United States", "Turkey"),   # USA listed as away
}



def load_international_results(csv_path: str) -> list[dict]:
    """Load martj42 CSV, filter to valid rows, return list of dicts."""
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
    """Compute sample weight: tournament factor × time decay."""
    # Tournament weight
    t = row["tournament"]
    if t in WC_TOURNAMENTS:
        w = WC_WEIGHT
    elif t in FRIENDLY_TOURNAMENTS:
        w = FRIENDLY_WEIGHT
    elif t in OFFICIAL_TOURNAMENTS:
        w = OTHER_OFFICIAL_WEIGHT
    else:
        # Other minor tournaments
        w = 0.5

    # Time decay: halflife = TIME_DECAY_HALFLIFE_DAYS
    days_ago = (now_date - row["date_dt"]).days
    if days_ago < 0:
        return 0  # future match
    decay = 0.5 ** (days_ago / TIME_DECAY_HALFLIFE_DAYS)

    return w * decay


def load_upcoming_fixtures() -> list[dict]:
    """Load upcoming WC fixtures from football-data snapshot."""
    if not os.path.exists(FIXTURE_PATH):
        print("[model_runner] No fixture snapshot found, skipping upcoming prediction")
        return []

    with open(FIXTURE_PATH, "r") as f:
        data = json.load(f)

    matches = data.get("data", {}).get("matches", [])
    now = datetime.now(timezone.utc)
    upcoming = []
    for idx, m in enumerate(matches, 1):
        utc_date = m.get("utcDate", "")
        if not utc_date:
            continue
        try:
            kt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
        except ValueError:
            continue
        if kt <= now:
            continue
        ht = m.get("homeTeam", {}).get("name", "")
        at = m.get("awayTeam", {}).get("name", "")
        if not ht or not at:
            continue
        # Apply team name mapping so model can find them in martj42 data
        ht = TEAM_NAME_MAP.get(ht, ht)
        at = TEAM_NAME_MAP.get(at, at)
        upcoming.append({
            "match_id": f"M{idx:03d}",
            "home_team": ht,
            "away_team": at,
            "kickoff_utc": utc_date,
            "utc_date": kt,
        })
    return upcoming


def _infer_match_id(m: dict) -> str:
    """Infer match_id from the fixture. Try id, matchday, etc."""
    mid = m.get("id", "")
    # Use last 4 digits of football-data id as match id
    # For now, derive from team names
    ht = m.get("homeTeam", {}).get("name", "")
    at = m.get("awayTeam", {}).get("name", "")
    groups = ["GROUP_A", "GROUP_B", "GROUP_C", "GROUP_D",
              "GROUP_E", "GROUP_F", "GROUP_G", "GROUP_H"]
    group = m.get("group", "")
    stage = m.get("stage", "")
    if group in groups:
        idx = groups.index(group) + 1
        return f"M{idx:03d}"
    return "MXXX"


# ---------------------------------------------------------------------------
# Margin calibration: conditional Platt parameters (fitted 2026-06-05, P1-B Phase 2)
# Calibrates logit(p_cal) = α + β × logit(p_raw) + γ × s
# where s = max(P(margin >= 2), P(margin <= -2)) = favorite's blowout probability
# See: PROPOSAL-P1-model-credibility.md § P1-B
#
# Parameters can be auto-recalibrated via fit_margin_calibration().
# Defaults below are loaded from MARGIN_CALIBRATION_PATH when available.
# ---------------------------------------------------------------------------
MARGIN_CALIBRATION_DIR = os.path.join(ARTIFACT_DIR, "calibration")
MARGIN_CALIBRATION_PATH = os.path.join(MARGIN_CALIBRATION_DIR, "margin-calibration-params.json")
CALIBRATION_TAIL_BUCKETS = [-4, -3, -2, -1, 0, 1, 2, 3, 4]  # ALL margins calibrated

# Hardcoded defaults (fallback if no JSON file exists)
DEFAULT_MARGIN_CALIBRATION_PARAMS = {
    "-4": {"alpha": -1.9829, "beta": 0.6185, "gamma": 1.266},
    "-3": {"alpha": -0.7941, "beta": 0.8098, "gamma": 0.4984},
    "-2": {"alpha": -0.6571, "beta": 0.896, "gamma": 0.7938},
    "-1": {"alpha": 0.0, "beta": 1.0, "gamma": 0.0},   # identity (no bias detected)
    "0": {"alpha": 0.0, "beta": 1.0, "gamma": 0.0},    # identity
    "1": {"alpha": 0.0, "beta": 1.0, "gamma": 0.0},    # identity
    "2": {"alpha": -0.3631, "beta": 0.8267, "gamma": 0.269},
    "3": {"alpha": -1.227, "beta": 0.6392, "gamma": 0.7792},
    "4": {"alpha": -0.698, "beta": 0.6978, "gamma": 0.0235},
}


def _load_calibration_params() -> dict:
    """Load calibration params from JSON file, fallback to hardcoded defaults."""
    if os.path.exists(MARGIN_CALIBRATION_PATH):
        try:
            with open(MARGIN_CALIBRATION_PATH) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            print(f"[model_runner] Warning: failed to load {MARGIN_CALIBRATION_PATH}, using defaults")
    return dict(DEFAULT_MARGIN_CALIBRATION_PARAMS)


def _save_calibration_params(params: dict):
    """Save calibration params to JSON file."""
    os.makedirs(MARGIN_CALIBRATION_DIR, exist_ok=True)
    with open(MARGIN_CALIBRATION_PATH, "w") as f:
        json.dump(params, f, indent=2)
    print(f"[model_runner] Calibration params saved to {MARGIN_CALIBRATION_PATH}")


# Global (loaded once at module init or on first call)
_MARGIN_CALIBRATION_PARAMS_CACHE = None


def _get_calibration_params() -> dict:
    global _MARGIN_CALIBRATION_PARAMS_CACHE
    if _MARGIN_CALIBRATION_PARAMS_CACHE is None:
        _MARGIN_CALIBRATION_PARAMS_CACHE = _load_calibration_params()
    return _MARGIN_CALIBRATION_PARAMS_CACHE


def _invalidate_calibration_cache():
    """Force reload on next call (call after fit_margin_calibration saves new file)."""
    global _MARGIN_CALIBRATION_PARAMS_CACHE
    _MARGIN_CALIBRATION_PARAMS_CACHE = None


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-100, min(100, x))))


def _logit(p: float) -> float:
    p = max(1e-10, min(1 - 1e-10, p))
    return math.log(p / (1 - p))


def calibrate_margin_distribution(margin_probs: dict) -> dict:
    """
    Apply conditional Platt calibration to margin distribution.

    Args:
        margin_probs: raw margin distribution dict with string keys,
                      e.g. {"-8": 0.000001, ..., "0": 0.35, ..., "8": 0.000001}

    Returns:
        calibrated margin distribution dict (same key format, renormalized)
    """
    # Normalize all keys to strings
    mp = {str(k): float(v) for k, v in margin_probs.items()}

    # Compute blowout signal s from raw distribution
    s = max(
        sum(v for k, v in mp.items() if int(k) >= 2),
        sum(v for k, v in mp.items() if int(k) <= -2),
    )

    # Calibrate each tail bucket
    for k in CALIBRATION_TAIL_BUCKETS:
        k_str = str(k)
        p_raw = mp.get(k_str, 0.0)
        if p_raw <= 0 or p_raw >= 0.999:
            continue

        pk = _get_calibration_params().get(k_str)
        if not pk:
            continue

        p_cal = _sigmoid(pk["alpha"] + pk["beta"] * _logit(p_raw) + pk["gamma"] * s)
        mp[k_str] = p_cal

    # Renormalize
    total = sum(mp.values())
    if total > 0:
        mp = {k: v / total for k, v in mp.items()}

    return mp


def fit_margin_calibration(model: DixonColesGoalModel, test_rows: list[dict]) -> dict:
    """
    Fit conditional Platt calibration params from model predictions on historical test data.
    Runs model.predict on each test match, aggregates per-margin-bucket, runs logistic regression.

    Returns dict like {"-4": {"alpha": ..., "beta": ..., "gamma": ...}, ...}
    Covers all CALIBRATION_TAIL_BUCKETS.
    Saves result to MARGIN_CALIBRATION_PATH.
    """
    import numpy as np

    TAIL = CALIBRATION_TAIL_BUCKETS  # [-4..4]
    samples = {k: {"X": [], "y": []} for k in TAIL}

    def logit(p):
        p = max(1e-10, min(1 - 1e-10, p))
        return math.log(p / (1 - p))

    n_skip = 0
    for row in test_rows:
        ht, at = row["home_team"], row["away_team"]
        try:
            grid = model.predict(ht, at, max_goals=MAX_GOALS, normalize=True,
                                 neutral_venue=bool(row["neutral"]))
        except (ValueError, KeyError):
            n_skip += 1
            continue

        mp = compute_margin_distribution(model, ht, at, bool(row["neutral"]))
        s = max(sum(float(v) for k, v in mp.items() if int(k) >= 2),
                sum(float(v) for k, v in mp.items() if int(k) <= -2))
        actual = row["home_score"] - row["away_score"]

        for k in TAIL:
            p_raw = float(mp.get(str(k), 0.0))
            if p_raw <= 0 or p_raw >= 0.999:
                continue
            samples[k]["X"].append([logit(p_raw), s])
            samples[k]["y"].append(1.0 if actual == k else 0.0)

    # Fit logistic regression per bucket (IRLS)
    params = {}
    for k in TAIL:
        X = np.array(samples[k]["X"])
        y = np.array(samples[k]["y"])
        n = len(y)
        if n < 30:
            # Not enough data — use identity
            params[str(k)] = {"alpha": 0.0, "beta": 1.0, "gamma": 0.0, "n": n,
                              "note": "identity (insufficient samples)"}
            continue

        X_aug = np.column_stack([np.ones(n), X])
        beta = np.zeros(3)
        for _ in range(50):
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

        params[str(k)] = {
            "alpha": round(beta[0], 4),
            "beta": round(beta[1], 4),
            "gamma": round(beta[2], 4),
            "n": n,
            "baseline_freq": round(float(np.mean(y)), 4),
        }

    params["_metadata"] = {
        "fitted_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_test_matches": len(test_rows),
        "n_skipped": n_skip,
        "version": "conditional_platt_v1",
    }

    _save_calibration_params(params)
    _invalidate_calibration_cache()
    print(f"[model_runner] Calibration refit complete. {n_skip} skipped.")
    return params


def compute_margin_distribution(
    model: DixonColesGoalModel,
    home_team: str,
    away_team: str,
    neutral: bool = False,
    max_goals: int = MAX_GOALS,
) -> dict:
    """Compute margin distribution from model prediction."""
    grid = model.predict(home_team, away_team, max_goals=max_goals, normalize=True, neutral_venue=neutral)
    probs = grid.goal_matrix

    margins = defaultdict(float)
    for h in range(min(max_goals + 1, len(probs))):
        for a in range(min(max_goals + 1, len(probs[0]))):
            p = probs[h][a]
            margins[h - a] += p

    # Normalize
    total = sum(margins.values())
    return {str(k): round(v / total, 6) for k, v in sorted(margins.items()) if v / total > 1e-8}


def compute_elo_reference(home_team: str, away_team: str, rows: list[dict], now_date) -> dict:
    """Simple Elo reference from historical results."""
    elo = defaultdict(lambda: 1500.0)
    K = 32

    # Sort by date
    sorted_rows = sorted(rows, key=lambda r: r["date"])
    for row in sorted_rows:
        h = row["home_team"]
        a = row["away_team"]
        hs = row["home_score"]
        as_ = row["away_score"]
        if hs is None or as_ is None:
            continue

        # Expected score
        home_exp = 1.0 / (1.0 + 10.0 ** ((elo[a] - elo[h]) / 400.0))
        away_exp = 1.0 - home_exp

        # Actual score
        if hs > as_:
            home_actual, away_actual = 1.0, 0.0
        elif hs == as_:
            home_actual, away_actual = 0.5, 0.5
        else:
            home_actual, away_actual = 0.0, 1.0

        elo[h] += K * (home_actual - home_exp)
        elo[a] += K * (away_actual - away_exp)

    he = elo.get(home_team, 1500.0)
    ae = elo.get(away_team, 1500.0)
    p_home = 1.0 / (1.0 + 10.0 ** ((ae - he) / 400.0))
    return {"home_elo": round(he, 1), "away_elo": round(ae, 1), "elo_p_home": round(p_home, 4)}


def fit_model(rows: list[dict], now_date) -> DixonColesGoalModel:
    """Fit Dixon-Coles model on filtered and weighted data."""
    # Filter to last N years
    cutoff_date = now_date.replace(year=now_date.year - LAST_N_YEARS)
    filtered = [r for r in rows if r["date_dt"] >= cutoff_date]

    # Prepare data arrays
    goals_home, goals_away = [], []
    teams_home, teams_away = [], []
    weights = []
    neutral = []

    for row in filtered:
        w = compute_weight(row, now_date)
        if w <= 0:
            continue
        goals_home.append(row["home_score"])
        goals_away.append(row["away_score"])
        teams_home.append(row["home_team"])
        teams_away.append(row["away_team"])
        weights.append(w)
        neutral.append(row["neutral"])

    n_matches = len(goals_home)
    if n_matches < 100:
        raise ValueError(f"Only {n_matches} matches after filtering, need >= 100")

    model = DixonColesGoalModel(
        goals_home=np.array(goals_home, dtype=float),
        goals_away=np.array(goals_away, dtype=float),
        teams_home=np.array(teams_home),
        teams_away=np.array(teams_away),
        weights=np.array(weights, dtype=float),
        neutral_venue=np.array(neutral, dtype=bool),
    )
    model.fit()

    return model, filtered


def resolve_host_advantage(
    match_id: str, fixture_home: str, fixture_away: str,
) -> tuple[str, str, bool]:
    """Determine effective prediction order and neutral flag.

    For host home matches: return (host, opponent, neutral=False).
    For host away matches: the model call order is swapped so DC's home-advantage
    parameter applies to the actual host. The artifact records fixture-listed teams.
    For all other WC matches: return (fixture_home, fixture_away, neutral=True).
    """
    if match_id in HOST_HOME_MATCHES:
        eff_home, eff_away = HOST_HOME_MATCHES[match_id]
        return (eff_home, eff_away, False)
    # All other WC matches: neutral ground
    return (fixture_home, fixture_away, True)


def predict_match(
    model: DixonColesGoalModel,
    home_team: str,
    away_team: str,
    is_neutral: bool,
) -> dict:
    """Predict a single match and return p_model + margin distribution."""
    grid = model.predict(home_team, away_team, max_goals=MAX_GOALS, normalize=True, neutral_venue=is_neutral)
    return {
        "home": round(grid.home_win, 6),
        "draw": round(grid.draw, 6),
        "away": round(grid.away_win, 6),
    }

def output_artifact(
    match_id: str,
    home_team: str,
    away_team: str,
    p_model: dict,
    margin_dist: dict,
    margin_dist_calibrated: dict,
    elo_ref: dict,
    model_params: dict,
    calibration: dict,
    data_snapshot: str,
    neutral: bool,
):
    """Write model artifact JSON with raw + calibrated margin distributions."""
    artifact = {
        "artifact_id": f"model:{match_id}:{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}",
        "artifact_type": "model",
        "script": "model_runner.py",
        "model_name": "dixon_coles_v1",
        "home_team": home_team,
        "away_team": away_team,
        "match_id": match_id,
        "neutral_venue": neutral,
        "p_model": p_model,
        "margin_probabilities": margin_dist,
        "margin_probabilities_calibrated": margin_dist_calibrated,
        "margin_calibration_version": "conditional_platt_v1_20260605",
        "elo_reference": elo_ref,
        "model_params": model_params,
        "calibration": calibration,
        "source_data_snapshot": data_snapshot,
        "fitted_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_contract": "p_model_is_clean_strength_baseline",
    }

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    path = os.path.join(ARTIFACT_DIR, f"model-{match_id}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json")
    with open(path, "w") as f:
        json.dump(artifact, f, indent=2)

    return path


def get_latest_snapshot() -> str:
    """Get the most recent results CSV."""
    if not os.path.exists(SNAPSHOT_DIR):
        return None
    files = sorted([f for f in os.listdir(SNAPSHOT_DIR) if f.startswith("results-") and f.endswith(".csv")])
    return os.path.join(SNAPSHOT_DIR, files[-1]) if files else None


def run_batch():
    """Batch mode: fit once, predict all upcoming fixtures."""
    csv_path = get_latest_snapshot()
    if not csv_path:
        print("[model_runner] No data snapshot found. Run fetch_international_data.py first.")
        sys.exit(1)

    print(f"[model_runner] Loading data from {csv_path} ...")
    rows = load_international_results(csv_path)
    now_date = datetime.now().date()
    print(f"[model_runner] Loaded {len(rows)} rows. Fitting DC model ...")

    model, filtered = fit_model(rows, now_date)
    n_matches = len([r for r in filtered if compute_weight(r, now_date) > 0])
    print(f"[model_runner] Fit complete. Used {n_matches} weighted matches from {len(filtered)} filtered rows.")

    # Auto-recalibrate margin calibration params on 2023+ test data
    test_start = datetime.strptime("2023-01-01", "%Y-%m-%d").date()
    test_rows = [r for r in rows if hasattr(r, 'date_dt') and r["date_dt"] >= test_start]
    if not test_rows:
        print("[model_runner] No 2023+ data for calibration refit; using cached/default params")
    else:
        print(f"[model_runner] Recalibrating margin params on {len(test_rows)} test matches...")
        fit_margin_calibration(model, test_rows)

    upcoming = load_upcoming_fixtures()
    print(f"[model_runner] Found {len(upcoming)} upcoming fixtures.")

    elo_ref = compute_elo_reference("Mexico", "South Africa", rows, now_date)
    data_snapshot = os.path.basename(csv_path)
    artifacts = []

    for fixture in upcoming:
        ht = fixture["home_team"]
        at = fixture["away_team"]
        mid = fixture["match_id"]

        # Resolve host advantage: host home matches get real home boost,
        # all other WC matches are neutral ground
        model_home, model_away, is_neutral = resolve_host_advantage(mid, ht, at)

        elo_ref = compute_elo_reference(ht, at, rows, now_date)
        # Predict with effective ordering (host first for host home matches),
        # then re-align p_model to fixture order for artifact consistency
        raw_pmodel = predict_match(model, model_home, model_away, is_neutral)
        margin_dist = compute_margin_distribution(model, model_home, model_away, is_neutral)
        margin_dist_cal = calibrate_margin_distribution(margin_dist)

        # Re-align: if model call used different order than fixture, swap probs back
        if (model_home, model_away) != (ht, at):
            # model_home corresponds to fixture-listed 'at' and vice versa
            p_model = {"home": raw_pmodel["away"], "draw": raw_pmodel["draw"], "away": raw_pmodel["home"]}
        else:
            p_model = raw_pmodel

        calibration = {
            "status": "insufficient_data",
            "n_graded_live": 0,
            "n_graded_historical": 0,
            "brier_historical": None,
        }

        path = output_artifact(
            match_id=mid,
            home_team=ht,
            away_team=at,
            p_model=p_model,
            margin_dist=margin_dist,
            margin_dist_calibrated=margin_dist_cal,
            elo_ref=elo_ref,
            model_params={
                "time_decay_halflife_days": TIME_DECAY_HALFLIFE_DAYS,
                "friendly_weight": FRIENDLY_WEIGHT,
                "neutral_ground_home_off": True,
                "data_date_range": [
                    str(filtered[0]["date_dt"]) if filtered else None,
                    str(filtered[-1]["date_dt"]) if filtered else None,
                ],
                "n_matches_used": n_matches,
            },
            calibration=calibration,
            data_snapshot=data_snapshot,
            neutral=is_neutral,
        )
        artifacts.append({"match_id": mid, "artifact": path, "p_model": p_model})

    result = {
        "status": "ok",
        "n_matches_used": n_matches,
        "n_fixtures": len(upcoming),
        "artifacts": artifacts,
    }
    print(json.dumps(result, indent=2))
    return result


def run_match(home_team: str, away_team: str, match_id: str = "M001"):
    """Single match mode: fit and predict one match."""
    csv_path = get_latest_snapshot()
    if not csv_path:
        print("[model_runner] No data snapshot found.")
        sys.exit(1)

    print(f"[model_runner] Loading data from {csv_path} ...")
    rows = load_international_results(csv_path)
    now_date = datetime.now().date()

    print(f"[model_runner] Fitting DC model ...")
    model, filtered = fit_model(rows, now_date)
    n_matches = len([r for r in filtered if compute_weight(r, now_date) > 0])
    print(f"[model_runner] Fit complete. Used {n_matches} weighted matches.")

    model_home, model_away, is_neutral = resolve_host_advantage(match_id, home_team, away_team)
    elo_ref = compute_elo_reference(home_team, away_team, rows, now_date)
    raw_pmodel = predict_match(model, model_home, model_away, is_neutral)
    margin_dist = compute_margin_distribution(model, model_home, model_away, is_neutral)
    margin_dist_cal = calibrate_margin_distribution(margin_dist)

    # Re-align p_model to fixture order
    if (model_home, model_away) != (home_team, away_team):
        p_model = {"home": raw_pmodel["away"], "draw": raw_pmodel["draw"], "away": raw_pmodel["home"]}
    else:
        p_model = raw_pmodel

    data_snapshot = os.path.basename(csv_path)
    calibration = {"status": "insufficient_data", "n_graded_live": 0, "n_graded_historical": 0, "brier_historical": None}
    n_weighted = n_matches

    path = output_artifact(
        match_id=match_id,
        home_team=home_team,
        away_team=away_team,
        p_model=p_model,
        margin_dist=margin_dist,
        margin_dist_calibrated=margin_dist_cal,
        elo_ref=elo_ref,
        model_params={
            "time_decay_halflife_days": TIME_DECAY_HALFLIFE_DAYS,
            "friendly_weight": FRIENDLY_WEIGHT,
            "neutral_ground_home_off": True,
            "data_date_range": [
                str(filtered[0]["date_dt"]) if filtered else None,
                str(filtered[-1]["date_dt"]) if filtered else None,
            ],
            "n_matches_used": n_weighted,
        },
        calibration=calibration,
        data_snapshot=data_snapshot,
        neutral=is_neutral,
    )

    result = {
        "status": "ok",
        "match_id": match_id,
        "home_team": home_team,
        "away_team": away_team,
        "p_model": p_model,
        "elo_reference": elo_ref,
        "neutral_venue": is_neutral,
        "n_matches_used": n_weighted,
        "artifact_path": path,
    }
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description="Dixon-Coles model runner")
    parser.add_argument("--mode", required=True, choices=["batch", "match"])
    parser.add_argument("--home", default=None, help="Home team (match mode)")
    parser.add_argument("--away", default=None, help="Away team (match mode)")
    parser.add_argument("--match-id", default="M001", help="Match ID (match mode)")
    args = parser.parse_args()

    if args.mode == "batch":
        run_batch()
    elif args.mode == "match":
        if not args.home or not args.away:
            print("[model_runner] match mode requires --home and --away")
            sys.exit(1)
        run_match(args.home, args.away, args.match_id)


if __name__ == "__main__":
    main()
