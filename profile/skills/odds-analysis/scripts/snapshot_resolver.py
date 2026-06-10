#!/usr/bin/env python3
"""Resolve reusable WC26 snapshots without spending quota.

This script is the cache-reuse contract shared by cron-created window tasks and
manual Telegram handoff tasks. It never calls external APIs. It only selects
existing snapshots and states whether a deterministic collector refresh is
needed.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple


DEFAULT_WORKSPACE = Path("/hermesdata/worldcup-2026-handicap")


WINDOW_REUSE = {
    "early_structural": {"group": "early_structural", "odds_ttl_minutes": 720, "lineup_ttl_minutes": None},
    "T-72h_early": {"group": "early_structural", "odds_ttl_minutes": 720, "lineup_ttl_minutes": None},
    "T-48h_early_update": {"group": "early_structural", "odds_ttl_minutes": 720, "lineup_ttl_minutes": None},
    "T-24h_confirm": {"group": "confirmation", "odds_ttl_minutes": 180, "lineup_ttl_minutes": None},
    "T-6h_preflight": {"group": "preflight", "odds_ttl_minutes": 60, "lineup_ttl_minutes": None},
    "T-90m_lineup_probe": {"group": "late_lineup_price", "odds_ttl_minutes": 30, "lineup_ttl_minutes": 30},
    "T-75m_team_sheet_checkpoint": {"group": "late_lineup_price", "odds_ttl_minutes": 30, "lineup_ttl_minutes": 30},
    "T-60m_lineup_final": {"group": "late_lineup_price", "odds_ttl_minutes": 30, "lineup_ttl_minutes": 30},
    "T-45m_price_guard": {"group": "late_lineup_price", "odds_ttl_minutes": 30, "lineup_ttl_minutes": 30},
    "manual_now": {"group": "manual", "odds_ttl_minutes": 90, "lineup_ttl_minutes": 30},
}

WINDOW_REQUIRED_SOURCES = {
    "early_structural": {"fixtures", "odds_broad", "oddspapi_ah"},
    "T-72h_early": {"fixtures", "odds_broad", "oddspapi_ah"},
    "T-48h_early_update": {"fixtures", "odds_broad", "oddspapi_ah"},
    "T-24h_confirm": {"fixtures", "odds_broad", "oddspapi_ah"},
    "T-6h_preflight": {"fixtures", "odds_broad", "oddspapi_ah", "weather"},
    "T-90m_lineup_probe": {"fixtures", "odds_broad", "oddspapi_ah", "lineups", "weather"},
    "T-75m_team_sheet_checkpoint": {"fixtures", "odds_broad", "oddspapi_ah", "lineups", "weather"},
    "T-60m_lineup_final": {"fixtures", "odds_broad", "oddspapi_ah", "lineups", "weather"},
    "T-45m_price_guard": {"fixtures", "odds_broad", "oddspapi_ah", "lineups", "weather"},
    "manual_now": {"fixtures", "odds_broad", "oddspapi_ah"},
}

SOURCE_SPECS = {
    "fixtures": {
        "directory": "snapshots/fixtures",
        "patterns": ["football-data-wc-matches-latest.json", "*.json"],
        "ttl_minutes": 1440,
    },
    "odds_broad": {
        "directory": "snapshots/odds",
        "patterns": ["the-odds-api-multibook-*.json"],
        "ttl_key": "odds_ttl_minutes",
    },
    "oddspapi_ah": {
        "directory": "snapshots/odds",
        "patterns": ["oddspapi-*.json"],
        "ttl_key": "odds_ttl_minutes",
    },
    "lineups": {
        "directory": "snapshots/lineups",
        "patterns": ["*.json"],
        "ttl_key": "lineup_ttl_minutes",
    },
    "weather": {
        "directory": "snapshots/weather",
        "patterns": ["*.json"],
        "ttl_minutes": 180,
    },
}


class SnapshotCandidate(NamedTuple):
    path: Path
    captured_at: datetime
    payload: dict[str, Any]


def parse_utc(raw: str) -> datetime:
    value = raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def now_from_arg(raw: str | None) -> datetime:
    return parse_utc(raw) if raw else datetime.now(timezone.utc)


def captured_at(payload: dict[str, Any], path: Path) -> datetime | None:
    for key in ("captured_at_utc", "created_at_utc", "snapshot_at_utc"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw:
            try:
                return parse_utc(raw)
            except ValueError:
                pass
    # Timestamped filenames are a fallback only. Prefer payload metadata.
    stem = path.stem
    for token in stem.replace("-", "_").split("_"):
        if len(token) == 16 and token.endswith("Z") and "T" in token:
            try:
                return datetime.strptime(token, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def iter_candidates(workspace: Path, source: str) -> list[SnapshotCandidate]:
    spec = SOURCE_SPECS[source]
    directory = workspace / str(spec["directory"])
    if not directory.exists():
        return []
    candidates: list[SnapshotCandidate] = []
    seen: set[Path] = set()
    for pattern in spec["patterns"]:
        for path in directory.glob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                import sys
                print(f"WARN: corrupted snapshot skipped: {path} — {exc}", file=sys.stderr)
                continue
            if not isinstance(payload, dict):
                import sys
                print(f"WARN: non-dict snapshot skipped: {path}", file=sys.stderr)
                continue
            captured = captured_at(payload, path)
            if captured is None:
                continue
            candidates.append(SnapshotCandidate(path=path, captured_at=captured, payload=payload))
    candidates.sort(key=lambda item: item.captured_at, reverse=True)
    return candidates


def ttl_for(source: str, window: str, override_minutes: int | None = None) -> int | None:
    if override_minutes is not None:
        return override_minutes
    spec = SOURCE_SPECS[source]
    if "ttl_minutes" in spec:
        return int(spec["ttl_minutes"])
    window_spec = WINDOW_REUSE.get(window, WINDOW_REUSE["manual_now"])
    value = window_spec.get(str(spec["ttl_key"]))
    return int(value) if value is not None else None


def required_sources(window: str) -> set[str]:
    return set(WINDOW_REQUIRED_SOURCES.get(window, WINDOW_REQUIRED_SOURCES["manual_now"]))


def resolve_source(
    workspace: Path,
    source: str,
    window: str,
    now: datetime,
    force_refresh: bool = False,
    ttl_override_minutes: int | None = None,
) -> dict[str, Any]:
    ttl = ttl_for(source, window, ttl_override_minutes)
    candidates = iter_candidates(workspace, source)
    latest = candidates[0] if candidates else None
    window_spec = WINDOW_REUSE.get(window, WINDOW_REUSE["manual_now"])
    required = source in required_sources(window)
    result: dict[str, Any] = {
        "source": source,
        "window": window,
        "reuse_group": window_spec["group"],
        "required": required,
        "ttl_minutes": ttl,
        "force_refresh": force_refresh,
        "snapshot_count": len(candidates),
        "selected_snapshot_path": None,
        "selected_snapshot_id": None,
        "captured_at_utc": None,
        "age_minutes": None,
        "cache_hit": False,
        "must_refresh": False,
        "reason": "",
    }
    if latest is None:
        if required:
            result.update(must_refresh=True, reason="no_snapshot_available")
        else:
            result.update(must_refresh=False, reason="optional_source_missing")
        return result
    age = max(0.0, (now - latest.captured_at).total_seconds() / 60.0)
    snapshot_id = latest.payload.get("snapshot_id") or latest.payload.get("id") or latest.path.name
    result.update(
        selected_snapshot_path=str(latest.path),
        selected_snapshot_id=str(snapshot_id),
        captured_at_utc=latest.captured_at.isoformat().replace("+00:00", "Z"),
        age_minutes=round(age, 2),
    )
    if force_refresh:
        result.update(must_refresh=required, reason="explicit_refresh_requested" if required else "optional_explicit_refresh_skipped")
    elif ttl is not None and age > ttl:
        if required:
            result.update(must_refresh=True, reason="snapshot_stale")
        else:
            result.update(must_refresh=False, reason="optional_source_stale")
    else:
        result.update(cache_hit=True, reason="reuse_existing_snapshot")
    return result


def resolve_all(workspace: Path, window: str, now: datetime, force_refresh: bool = False) -> dict[str, Any]:
    sources = ["fixtures", "odds_broad", "oddspapi_ah", "lineups", "weather"]
    resolved = [resolve_source(workspace, source, window, now, force_refresh) for source in sources]
    return {
        "ok": True,
        "workspace": str(workspace),
        "window": window,
        "reuse_group": WINDOW_REUSE.get(window, WINDOW_REUSE["manual_now"])["group"],
        "now_utc": now.isoformat().replace("+00:00", "Z"),
        "force_refresh": force_refresh,
        "sources": resolved,
        "cache_hits": sum(1 for item in resolved if item["cache_hit"]),
        "must_refresh": [item["source"] for item in resolved if item["must_refresh"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--window", default="manual_now", choices=sorted(WINDOW_REUSE))
    parser.add_argument("--source", default="all", choices=["all", *sorted(SOURCE_SPECS)])
    parser.add_argument("--now-utc")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--ttl-minutes", type=int)
    args = parser.parse_args()

    now = now_from_arg(args.now_utc)
    if args.source == "all":
        payload = resolve_all(args.workspace, args.window, now, args.force_refresh)
    else:
        payload = resolve_source(args.workspace, args.source, args.window, now, args.force_refresh, args.ttl_minutes)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
