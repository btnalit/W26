#!/usr/bin/env python3
"""WC26 fixture identity registry.

football-data.org match IDs are the canonical identity. Local `Mxxx` ordinals
are display aliases derived from the current fixture cache ordering and must
never be trusted without a team/kickoff cross-check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any


DEFAULT_FIXTURE_PATH = Path("/hermesdata/worldcup-2026-handicap/snapshots/fixtures/football-data-wc-matches-latest.json")
LOCAL_MATCH_ID_RE = re.compile(r"^M\d{3}$")


DEFAULT_VENUE_OVERRIDES_PATH = Path(
    os.environ.get(
        "WC26_VENUE_OVERRIDES_PATH",
        "/hermesdata/worldcup-2026-handicap/snapshots/fixtures/venue-overrides.json",
    )
)


def load_venue_overrides(path: Path = DEFAULT_VENUE_OVERRIDES_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("venues", payload)
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value).strip() for key, value in raw.items() if str(value).strip()}


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().replace("-", " ").split())


def _matches_from_cache(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return payload.get("data", {}).get("matches", []) or payload.get("matches", [])


def _team_name(item: dict[str, Any], side: str) -> str:
    team = item.get(f"{side}Team") or {}
    return str(team.get("name") or "").strip()


def _team_tla(item: dict[str, Any], side: str) -> str:
    team = item.get(f"{side}Team") or {}
    return str(team.get("tla") or "").strip()


def venue_for_entry(
    local_id: str,
    football_data_id: Any,
    item: dict[str, Any],
    venue_overrides: dict[str, str] | None = None,
) -> str | None:
    overrides = venue_overrides or {}
    for key in (local_id, f"fd:{football_data_id}", str(football_data_id)):
        value = overrides.get(key)
        if value:
            return value
    return item.get("venue")


def build_entry(index: int, item: dict[str, Any], venue_overrides: dict[str, str] | None = None) -> dict[str, Any]:
    home = _team_name(item, "home")
    away = _team_name(item, "away")
    football_data_id = item.get("id")
    return {
        "canonical_id": f"fd:{football_data_id}",
        "football_data_id": football_data_id,
        "local_ordinal_id": f"M{index:03d}",
        "home": home,
        "away": away,
        "home_tla": _team_tla(item, "home"),
        "away_tla": _team_tla(item, "away"),
        "kickoff_utc": item.get("utcDate"),
        "stage": item.get("stage"),
        "group": item.get("group"),
        "matchday": item.get("matchday"),
        "status": item.get("status"),
        "venue": venue_for_entry(f"M{index:03d}", football_data_id, item, venue_overrides),
        "home_norm": normalize_name(home),
        "away_norm": normalize_name(away),
    }


def load_registry(path: Path = DEFAULT_FIXTURE_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    venue_overrides = load_venue_overrides()
    playable: list[dict[str, Any]] = []
    for item in sorted(_matches_from_cache(payload), key=lambda m: (m.get("utcDate", ""), m.get("id", 0))):
        home = _team_name(item, "home")
        away = _team_name(item, "away")
        if not home or not away or home == "TBD" or away == "TBD":
            continue
        playable.append(item)

    entries = [build_entry(index, item, venue_overrides) for index, item in enumerate(playable, 1)]
    return {
        "fixture_path": str(path),
        "entries": entries,
        "by_local_id": {entry["local_ordinal_id"]: entry for entry in entries},
        "by_football_data_id": {str(entry["football_data_id"]): entry for entry in entries},
        "by_pair": {(entry["home_norm"], entry["away_norm"]): entry for entry in entries},
    }


def resolve_fixture(
    registry: dict[str, Any],
    match_id: str | None = None,
    football_data_id: int | str | None = None,
    home: str | None = None,
    away: str | None = None,
) -> dict[str, Any]:
    if football_data_id not in (None, ""):
        entry = registry["by_football_data_id"].get(str(football_data_id))
        if entry:
            return entry
        raise ValueError(f"football_data_id not found in fixture cache: {football_data_id}")
    if match_id:
        entry = registry["by_local_id"].get(str(match_id).upper())
        if entry:
            return entry
        raise ValueError(f"local match id not found in fixture cache: {match_id}")
    if home and away:
        key = (normalize_name(home), normalize_name(away))
        entry = registry["by_pair"].get(key)
        if entry:
            return entry
        reverse = registry["by_pair"].get((key[1], key[0]))
        if reverse:
            raise ValueError(f"fixture found with reversed teams: {reverse['local_ordinal_id']} {reverse['home']} vs {reverse['away']}")
        raise ValueError(f"team pair not found in fixture cache: {home} vs {away}")
    raise ValueError("resolve_fixture requires football_data_id, match_id, or home+away")


def _payload_identity(payload: dict[str, Any]) -> dict[str, Any]:
    match = payload.get("match") if isinstance(payload.get("match"), dict) else {}
    teams = payload.get("teams") if isinstance(payload.get("teams"), list) else []
    return {
        "match_id": payload.get("match_id") or match.get("match_id") or payload.get("local_ordinal_id") or match.get("local_ordinal_id"),
        "football_data_id": payload.get("football_data_id") or match.get("football_data_id"),
        "home": match.get("home") or payload.get("home") or (teams[0] if teams else None),
        "away": match.get("away") or payload.get("away") or (teams[1] if len(teams) > 1 else None),
        "kickoff_utc": payload.get("kickoff_utc") or match.get("kickoff_utc"),
    }


def validate_identity(registry: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    identity = _payload_identity(payload)

    local_entry = None
    canonical_entry = None
    match_id = str(identity.get("match_id") or "").upper()
    if match_id:
        if not LOCAL_MATCH_ID_RE.fullmatch(match_id):
            errors.append("match_id must use local canonical format M0xx")
        else:
            local_entry = registry["by_local_id"].get(match_id)
            if local_entry is None:
                errors.append(f"match_id {match_id} not found in fixture registry")

    football_data_id = identity.get("football_data_id")
    if football_data_id not in (None, ""):
        canonical_entry = registry["by_football_data_id"].get(str(football_data_id))
        if canonical_entry is None:
            errors.append(f"football_data_id {football_data_id} not found in fixture registry")

    entry = canonical_entry or local_entry
    if canonical_entry and local_entry and canonical_entry["football_data_id"] != local_entry["football_data_id"]:
        errors.append(
            f"identity mismatch: {match_id} maps to {local_entry['home']} vs {local_entry['away']} "
            f"but football_data_id {football_data_id} maps to {canonical_entry['home']} vs {canonical_entry['away']}"
        )
        entry = canonical_entry

    if entry is not None:
        home = identity.get("home")
        away = identity.get("away")
        if home and away and (normalize_name(str(home)) != entry["home_norm"] or normalize_name(str(away)) != entry["away_norm"]):
            errors.append(
                f"fixture identity mismatch: {entry['local_ordinal_id']} maps to {entry['home']} vs {entry['away']}, "
                f"not {home} vs {away}"
            )
        kickoff = identity.get("kickoff_utc")
        if kickoff and entry.get("kickoff_utc") and str(kickoff) != str(entry["kickoff_utc"]):
            errors.append(
                f"kickoff mismatch for {entry['local_ordinal_id']}: fixture cache {entry['kickoff_utc']} vs report {kickoff}"
            )
    elif identity.get("home") and identity.get("away"):
        try:
            entry = resolve_fixture(registry, home=str(identity["home"]), away=str(identity["away"]))
            warnings.append(
                f"match_id absent; resolved by team pair to {entry['local_ordinal_id']} / {entry['canonical_id']}"
            )
        except ValueError as exc:
            errors.append(str(exc))

    return {
        "valid": not errors,
        "identity": identity,
        "canonical_entry": entry,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-path", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--match-id")
    parser.add_argument("--football-data-id")
    parser.add_argument("--home")
    parser.add_argument("--away")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    registry = load_registry(args.fixture_path)
    if args.list:
        print(json.dumps(registry["entries"], ensure_ascii=False, indent=2))
        return 0

    entry = resolve_fixture(
        registry,
        match_id=args.match_id,
        football_data_id=args.football_data_id,
        home=args.home,
        away=args.away,
    )
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
