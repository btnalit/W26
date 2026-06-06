#!/usr/bin/env python3
"""
resolve_model.py — Read the best available DC model artifact for a match.

Worker usage:
  python3 scripts/resolve_model.py --match-id M001

Output: JSON with p_model, margin_distribution, calibration, or error.

The worker should call this BEFORE running its own ad-hoc Poisson baseline.
If this script returns a valid model artifact, use it. Only fall back to
model_margin.py if this script returns status=not_found.
"""

from __future__ import annotations

import json
import os
import sys
import glob
import re

WORKSPACE = os.environ.get(
    "WORKSPACE",
    "/hermesdata/worldcup-2026-handicap",
)
ARTIFACT_DIR = os.path.join(WORKSPACE, "reports", "artifacts")
FIXTURE_PATH = os.path.join(
    WORKSPACE, "snapshots", "fixtures", "football-data-wc-matches-latest.json"
)


def get_fixture_teams(match_id: str) -> tuple[str, str] | None:
    """Get home/away team names from fixture data for validation."""
    if not os.path.exists(FIXTURE_PATH):
        return None
    try:
        data = json.load(open(FIXTURE_PATH))
        matches = data.get("data", {}).get("matches", [])
        for idx, m in enumerate(matches, 1):
            mid = f"M{idx:03d}"
            if mid == match_id:
                ht = m.get("homeTeam", {}).get("name", "")
                at = m.get("awayTeam", {}).get("name", "")
                if ht and at:
                    return (ht, at)
    except (OSError, json.JSONDecodeError):
        pass
    return None


TEAM_NAME_MAP = {
    "Czechia": "Czech Republic",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Cape Verde Islands": "Cape Verde",
    "Congo DR": "DR Congo",
}

EN_ZH_MAP = {
    "Mexico": "墨西哥",
    "South Africa": "南非",
    "South Korea": "韩国",
    "Czech Republic": "捷克",
    "Czechia": "捷克",
    "Canada": "加拿大",
    "Bosnia-Herzegovina": "波黑",
    "Bosnia and Herzegovina": "波黑",
    "United States": "美国",
    "Paraguay": "巴拉圭",
    "Qatar": "卡塔尔",
    "Switzerland": "瑞士",
    "Brazil": "巴西",
    "Morocco": "摩洛哥",
    "Haiti": "海地",
    "Scotland": "苏格兰",
    "Australia": "澳大利亚",
    "Turkey": "土耳其",
    "Germany": "德国",
    "Curaçao": "库拉索",
    "Netherlands": "荷兰",
    "Japan": "日本",
    "Ivory Coast": "科特迪瓦",
    "Ecuador": "厄瓜多尔",
    "Sweden": "瑞典",
    "Tunisia": "突尼斯",
    "Spain": "西班牙",
    "Cape Verde": "佛得角",
    "Cape Verde Islands": "佛得角",
    "Belgium": "比利时",
    "Egypt": "埃及",
    "Saudi Arabia": "沙特",
    "Uruguay": "乌拉圭",
    "Iran": "伊朗",
    "New Zealand": "新西兰",
    "France": "法国",
    "Senegal": "塞内加尔",
    "Iraq": "伊拉克",
    "Norway": "挪威",
    "Argentina": "阿根廷",
    "Algeria": "阿尔及利亚",
    "Austria": "奥地利",
    "Jordan": "约旦",
    "Portugal": "葡萄牙",
    "Congo DR": "刚果(金)",
    "DR Congo": "刚果(金)",
    "England": "英格兰",
    "Croatia": "克罗地亚",
    "Ghana": "加纳",
    "Panama": "巴拿马",
    "Uzbekistan": "乌兹别克",
    "Colombia": "哥伦比亚",
}


def resolve(match_id: str) -> dict:
    """
    Find the most recent DC model artifact for this match_id.
    
    Returns:
        status: "ok" | "not_found" | "wrong_teams" | "no_artifact_dir"
        artifact_path: path to artifact file (if ok)
        p_model: {home, draw, away} probabilities (if ok)
        margin_distribution: {margin: prob} dict (if ok)
        calibration: {status, n_graded_live, brier_historical} (if ok)
        home_team: team name
        away_team: team name
        home_team_zh: Chinese name (if mapped)
        away_team_zh: Chinese name (if mapped)
        model_type: "dixon_coles"
    """
    if not os.path.isdir(ARTIFACT_DIR):
        return {"status": "no_artifact_dir"}
    
    # List all model-{MATCH_ID} artifacts, pick newest by timestamp
    pattern = os.path.join(ARTIFACT_DIR, f"model-{match_id}-*.json")
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        return {"status": "not_found", "note": f"No model artifact for {match_id}"}
    
    latest = candidates[-1]
    try:
        data = json.load(open(latest))
    except (OSError, json.JSONDecodeError) as e:
        return {"status": "error", "note": str(e)}
    
    # Validate team names against fixture
    fixture_teams = get_fixture_teams(match_id)
    if fixture_teams:
        ft_home_mapped = TEAM_NAME_MAP.get(fixture_teams[0], fixture_teams[0])
        ft_away_mapped = TEAM_NAME_MAP.get(fixture_teams[1], fixture_teams[1])
        art_home = data.get("home_team", "")
        art_away = data.get("away_team", "")
        
        if art_home != ft_home_mapped or art_away != ft_away_mapped:
            return {
                "status": "wrong_teams",
                "note": f"Artifact has {art_home} vs {art_away}, "
                       f"fixture expects {ft_home_mapped} vs {ft_away_mapped}",
                "artifact_path": latest,
            }
    
    p_model = data.get("p_model", {})
    margin_dist = data.get("margin_probabilities", {})
    calibration = data.get("calibration", {})
    home_team = data.get("home_team", "")
    away_team = data.get("away_team", "")
    
    return {
        "status": "ok",
        "artifact_path": latest,
        "p_model": p_model,
        "margin_distribution": margin_dist,
        "calibration": calibration,
        "home_team": home_team,
        "away_team": away_team,
        "home_team_zh": EN_ZH_MAP.get(home_team, home_team),
        "away_team_zh": EN_ZH_MAP.get(away_team, away_team),
        "model_type": "dixon_coles",
        "fitted_at_utc": data.get("fitted_at_utc", "?"),
        "model_params": data.get("model_params", {}),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Resolve DC model artifact for a match")
    parser.add_argument("--match-id", required=True, help="e.g. M001")
    args = parser.parse_args()
    
    result = resolve(args.match_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
