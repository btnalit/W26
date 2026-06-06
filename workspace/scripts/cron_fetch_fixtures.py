#!/usr/bin/env python3
"""Fixture-refresh cron — polls football-data API for WC fixture changes.

Pre-tournament mode: free tier only, no paid API calls.
Detects when knockout team names change (null → real team after group deciders).

Output: one-line summary (empty = no change, meaning no new snapshot written).
  "✅ 无变化 (104场)"  or  "⚠️ 更新: M073 墨西哥 vs 加拿大"

Register as: no_agent cron every 6h.
"""
import json, os, ssl, sys, time, urllib.request
from datetime import datetime, timezone

WORKSPACE = os.environ.get("WORKSPACE", "/hermesdata/worldcup-2026-handicap")
FIXTURE_DIR = os.path.join(WORKSPACE, "snapshots", "fixtures")
ENV_FILE = os.path.join(WORKSPACE, ".env")
API_URL = "https://api.football-data.org/v4/competitions/2000/matches"
LATEST_FILE = os.path.join(FIXTURE_DIR, "football-data-wc-matches-latest.json")

# Snapshot wrapper format — ensures backward compat with model_runner.py
# which reads data['data']['matches'] (wrapped), not raw API response
def wrap_snapshot(api_data: dict, captured_at: str | None = None) -> dict:
    """Wrap raw football-data API response in backward-compatible format."""
    return {
        "captured_at_utc": captured_at or datetime.now(timezone.utc).isoformat(),
        "source": "football-data.org",
        "data": {
            "filters": {"season": "2026"},
            "resultSet": {
                "count": len(api_data.get("matches", [])),
                "first": api_data.get("resultSet", {}).get("first", ""),
                "last": api_data.get("resultSet", {}).get("last", ""),
                "played": api_data.get("resultSet", {}).get("played", 0),
            },
            "competition": {"id": 2000, "name": "FIFA World Cup", "code": "WC", "type": "CUP"},
            "matches": api_data.get("matches", []),
        },
    }

# SSL context (host has SSL issues with this API)
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# Ensure snapshots dir exists
os.makedirs(FIXTURE_DIR, exist_ok=True)


def load_token() -> str:
    """Load API token from .env"""
    if not os.path.exists(ENV_FILE):
        print("[fixture-refresh] ERROR: .env not found")
        sys.exit(1)
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("FOOTBALL_DATA_TOKEN="):
                return line.split("=", 1)[1].strip("\"'")
    print("[fixture-refresh] ERROR: FOOTBALL_DATA_TOKEN not in .env")
    sys.exit(1)


def fetch_fixtures(token: str) -> dict:
    """Fetch WC fixtures from football-data API."""
    req = urllib.request.Request(
        API_URL,
        headers={"X-Auth-Token": token},
    )
    with urllib.request.urlopen(req, timeout=60, context=_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_matches(data: dict) -> list:
    """Get match list from either wrapped snapshot format or raw API response."""
    if "data" in data and isinstance(data.get("data"), dict):
        return data["data"].get("matches", [])
    return data.get("matches", [])


def extract_knockout_teams(data: dict) -> list[tuple]:
    """Extract (home, away) team name pairs for knockout matches."""
    matches = _get_matches(data)
    result = []
    for m in matches:
        if m.get("stage", "") == "GROUP_STAGE":
            continue
        ht = m.get("homeTeam", {}).get("name")
        at = m.get("awayTeam", {}).get("name")
        result.append((ht, at))
    return result


def detect_changes(new_data: dict) -> list[str]:
    """Compare new data with current snapshot. Return list of change descriptions."""
    if not os.path.exists(LATEST_FILE):
        return ["NO_PREVIOUS_SNAPSHOT"]

    with open(LATEST_FILE) as f:
        cur_data = json.load(f)

    new_ko = extract_knockout_teams(new_data)
    cur_ko = extract_knockout_teams(cur_data)

    if len(new_ko) != len(cur_ko):
        return [f"Knockout count changed: {len(cur_ko)} → {len(new_ko)}"]

    changes = []
    for i, ((nh, na), (ch, ca)) in enumerate(zip(new_ko, cur_ko)):
        if (nh, na) != (ch, ca):
            mid = f"M{i + 73:03d}"
            changes.append(f"{mid}: {ch} vs {ca} → {nh} vs {na}")

    return changes if changes else ["NO_CHANGE"]


def count_tbd(teams: list[tuple]) -> int:
    return sum(1 for h, a in teams if h is None or a is None)


def main():
    # PID lock
    lockfile = "/tmp/wc26-fetch-fixtures.lock"
    try:
        lock_fd = os.open(lockfile, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, BlockingIOError):
            print("[fixture-refresh] Lock held — skipping.")
            sys.exit(0)
    except Exception:
        pass

    token = load_token()

    try:
        data = fetch_fixtures(token)
    except Exception as e:
        print(f"[fixture-refresh] Fetch failed: {e}")
        sys.exit(1)

    matches = data.get("matches", [])
    if len(matches) < 16:
        print(f"[fixture-refresh] Expected >=16 matches, got {len(matches)}")
        sys.exit(1)

    # Detect changes
    changes = detect_changes(data)
    no_change = changes == ["NO_CHANGE"]
    first_run = changes == ["NO_PREVIOUS_SNAPSHOT"]

    if no_change:
        print(f"✅ 无变化 ({len(matches)}场)")
        sys.exit(0)

    # Write timestamped snapshot + update latest
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    wrapped = wrap_snapshot(data, captured_at=ts)
    snapshot_file = os.path.join(FIXTURE_DIR, f"football-data-wc-matches-{ts}.json")
    with open(snapshot_file, "w") as f:
        json.dump(wrapped, f, indent=2)
    with open(LATEST_FILE, "w") as f:
        json.dump(wrapped, f, indent=2)

    # Prune old snapshots (keep newest 2)
    snaps = sorted(
        f for f in os.listdir(FIXTURE_DIR)
        if f.startswith("football-data-wc-matches-2") and f.endswith(".json")
    )
    for old in snaps[:-2]:
        os.remove(os.path.join(FIXTURE_DIR, old))

    # Output
    ko_teams = extract_knockout_teams(data)
    tbd = count_tbd(ko_teams)
    print(f"⚽ {len(matches)}场 / {tbd}/32 KO队名TBD")
    if first_run:
        print(f"📸 初始快照写入: {snapshot_file}")
    else:
        for c in changes:
            print(f"  {c}")
        print(f"📸 更新写入: {snapshot_file}")

    sys.exit(0)


if __name__ == "__main__":
    main()
