#!/usr/bin/env python3
"""Fetch international football results from martj42/international_results.

Cron collector mode — no paid API, no LLM, deterministic.
Output: snapshots/international_results/results-{timestamp}.csv
"""

import csv
import json
import os
import sys
import time
import urllib.request

WORKSPACE = os.environ.get(
    "WORKSPACE",
    "/hermesdata/worldcup-2026-handicap",
)
SNAPSHOT_DIR = os.path.join(WORKSPACE, "snapshots", "international_results")
URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
MAX_SNAPSHOTS = 2  # keep most recent 2

EXPECTED_HEADER = [
    "date", "home_team", "away_team", "home_score", "away_score",
    "tournament", "city", "country", "neutral",
]
EXPECTED_COLS = len(EXPECTED_HEADER)


def fetch_csv(url: str) -> str:
    """Fetch CSV content from URL, return as string."""
    req = urllib.request.Request(url, headers={"User-Agent": "wc26-handicap/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def validate_csv(content: str) -> int:
    """Validate CSV structure. Return row count (excluding header)."""
    lines = content.strip().splitlines()
    if len(lines) < 2:
        raise ValueError("CSV has no data rows")

    header = next(csv.reader([lines[0]]))
    if len(header) != EXPECTED_COLS:
        raise ValueError(
            f"Header has {len(header)} columns, expected {EXPECTED_COLS}. "
            f"Got: {header}"
        )
    # Check first data row parses
    first_row = next(csv.reader([lines[1]]))
    if len(first_row) != EXPECTED_COLS:
        raise ValueError(
            f"First data row has {len(first_row)} columns, expected {EXPECTED_COLS}"
        )
    return len(lines) - 1  # exclude header


def compute_stats(content: str) -> dict:
    """Compute summary statistics from CSV content."""
    reader = csv.DictReader(content.strip().splitlines())
    teams = set()
    tournaments = {}
    n_neutral = 0
    n_total = 0
    min_date = None
    max_date = None

    for row in reader:
        n_total += 1
        teams.add(row["home_team"])
        teams.add(row["away_team"])
        t = row["tournament"]
        tournaments[t] = tournaments.get(t, 0) + 1
        if row.get("neutral", "").upper() == "TRUE":
            n_neutral += 1
        d = row.get("date", "")
        if d:
            if min_date is None or d < min_date:
                min_date = d
            if max_date is None or d > max_date:
                max_date = d

    return {
        "n_matches": n_total,
        "n_teams": len(teams),
        "n_neutral": n_neutral,
        "date_range": [min_date, max_date],
        "top_tournaments": dict(
            sorted(tournaments.items(), key=lambda x: -x[1])[:15]
        ),
    }


def prune_snapshots(dirpath: str, max_keep: int):
    """Keep only the most recent max_keep snapshots."""
    files = sorted([
        f for f in os.listdir(dirpath)
        if f.startswith("results-") and f.endswith(".csv")
    ])
    for f in files[:-max_keep] if len(files) > max_keep else []:
        os.remove(os.path.join(dirpath, f))


def main():
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    print(f"[fetch_international_data] Fetching {URL} ...", flush=True)
    content = fetch_csv(URL)
    row_count = validate_csv(content)
    stats = compute_stats(content)

    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    filename = f"results-{timestamp}.csv"
    filepath = os.path.join(SNAPSHOT_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    prune_snapshots(SNAPSHOT_DIR, MAX_SNAPSHOTS)

    report = {
        "status": "ok",
        "filename": filename,
        "rows": row_count,
        "stats": stats,
    }
    print(json.dumps(report, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
