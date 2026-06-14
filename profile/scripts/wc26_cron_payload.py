#!/usr/bin/env python3
"""Production payloads for Hermes-managed WC26 cron jobs.

Hermes owns scheduling, run/pause/resume/remove, and delivery. This file only
implements the deterministic work that a scheduled job executes.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import re
import subprocess
import sys
import importlib.util
import hashlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


WORKSPACE = pathlib.Path(os.environ.get("WC26_WORKSPACE", "/hermesdata/worldcup-2026-handicap"))
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
PYTHON = pathlib.Path(os.environ.get("WC26_PYTHON", str(WORKSPACE / ".venv" / "bin" / "python")))
ROOT_HERMES_HOME = os.environ.get("WC26_ROOT_HERMES_HOME", "/root/.hermes")
CONTEXT_OVERRIDES_PATH = pathlib.Path(os.environ.get("WC26_MATCH_CONTEXT_OVERRIDES", str(WORKSPACE / "config" / "match-context-overrides.json")))
STATE_DIR = WORKSPACE / "state"
GRADING_DIR = WORKSPACE / "grading"
GRADING_CARDS_DIR = GRADING_DIR / "cards"
PATH_C_LEDGER_DIR = GRADING_DIR / "path_c_signal_ledger"


WINDOW_SPECS = [
    ("T-72h_early", "early_structural", 60.0, 84.0),
    ("T-24h_confirm", "confirmation", 18.0, 30.0),
    ("T-60m_lineup_final", "lineup_final", 0.75, 1.25),
    ("T-45m_price_guard", "price_guard", 0.5, 0.75),
]

# ── Late-window fixtures trigger forced odds refresh ──
# T-60m and T-45m windows exist solely to capture the latest price.
# Reusing a cached snapshot defeats their purpose.  When any fixture
# is inside the late-window range (0–1.25 h to kickoff), the odds
# collector must skip TTL reuse and always fetch fresh prices.
# LO=0 ensures that even ad-hoc analyses inside T-30m (e.g. triggered
# by breaking news) get a fresh snapshot rather than falling back to
# TTL reuse.
LATE_WINDOW_HOURS_LO = 0.0
LATE_WINDOW_HOURS_HI = 1.25


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


FIXTURE_REGISTRY = load_module(
    "fixture_registry",
    SCRIPT_DIR.parent / "skills" / "odds-analysis" / "scripts" / "fixture_registry.py",
)
DEVIG = load_module(
    "devig",
    SCRIPT_DIR.parent / "skills" / "odds-analysis" / "scripts" / "devig.py",
)
NO_PLAY_CLASSIFIER = load_module(
    "no_play_classifier",
    SCRIPT_DIR.parent / "skills" / "odds-analysis" / "scripts" / "no_play_classifier.py",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_time() -> datetime:
    override = os.environ.get("WC26_NOW_UTC")
    if override:
        value = override.replace("Z", "+00:00")
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def load_env() -> None:
    env_path = WORKSPACE / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: pathlib.Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return int(payload.get("exit_code", 0))


def manifest(job: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"job": job, "status": status, "captured_at_utc": utc_now(), **extra}


def python_bin() -> str:
    return str(PYTHON) if PYTHON.exists() else sys.executable


def force_refresh_requested() -> bool:
    return os.environ.get("WC26_FORCE_REFRESH", "").strip().lower() in {"1", "true", "yes", "y"}


def parse_snapshot_time(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def snapshot_time(path: pathlib.Path) -> datetime | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("captured_at_utc", "created_at_utc", "snapshot_at_utc"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw:
            parsed = parse_snapshot_time(raw)
            if parsed is not None:
                return parsed
    return None


def latest_snapshot(directory: pathlib.Path, patterns: list[str]) -> tuple[pathlib.Path, datetime] | None:
    candidates: list[tuple[pathlib.Path, datetime]] = []
    seen: set[pathlib.Path] = set()
    for pattern in patterns:
        for path in directory.glob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            captured = snapshot_time(path)
            if captured is not None:
                candidates.append((path, captured))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1])


def reusable_snapshot(directory: pathlib.Path, patterns: list[str], ttl_minutes: int) -> dict[str, Any] | None:
    latest = latest_snapshot(directory, patterns)
    if latest is None:
        return None
    path, captured = latest
    age_minutes = max(0.0, (current_time() - captured).total_seconds() / 60.0)
    if age_minutes > ttl_minutes:
        return None
    return {
        "snapshot_path": str(path),
        "captured_at_utc": captured.isoformat().replace("+00:00", "Z"),
        "age_minutes": round(age_minutes, 2),
        "ttl_minutes": ttl_minutes,
    }


def cache_reuse_manifest(job: str, snapshot: dict[str, Any], reason: str = "fresh_snapshot_within_ttl") -> dict[str, Any]:
    return manifest(
        job,
        "reused_cache",
        refresh_skipped=True,
        refresh_reason=reason,
        quota_spent=0,
        **snapshot,
    )


def window_reuse_policy(window: str) -> dict[str, Any]:
    policies = {
        "T-72h_early": ("early_structural", 720, "T-72h/T-48h structural snapshots are reusable within TTL"),
        "T-48h_early_update": ("early_structural", 720, "T-72h/T-48h structural snapshots are reusable within TTL"),
        "T-24h_confirm": ("confirmation", 180, "confirmation snapshots are reusable unless a material news event appears"),
        "T-6h_preflight": ("preflight", 60, "preflight snapshots are reusable inside the short freshness window"),
        "T-90m_lineup_probe": ("late_lineup_price", 30, "T-90/T-75/T-60/T-45 lineup and price snapshots are reusable within TTL"),
        "T-75m_team_sheet_checkpoint": ("late_lineup_price", 30, "T-90/T-75/T-60/T-45 lineup and price snapshots are reusable within TTL"),
        "T-60m_lineup_final": ("late_lineup_price", 30, "T-90/T-75/T-60/T-45 lineup and price snapshots are reusable within TTL"),
        "T-45m_price_guard": ("late_lineup_price", 30, "T-90/T-75/T-60/T-45 lineup and price snapshots are reusable within TTL"),
    }
    group, ttl, note = policies.get(window, ("manual", 90, "manual requests reuse cache unless explicitly refreshed"))
    return {"reuse_group": group, "max_odds_freshness_minutes": ttl, "note": note}


def fixture_entries() -> list[dict[str, Any]]:
    fixture_path = WORKSPACE / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json"
    if not fixture_path.exists():
        return []
    try:
        registry = FIXTURE_REGISTRY.load_registry(fixture_path)
    except Exception:
        return []
    return list(registry.get("entries", []))


def hours_to_kickoff(entry: dict[str, Any], now: datetime | None = None) -> float | None:
    raw = entry.get("kickoff_utc")
    if not raw:
        return None
    kickoff = parse_snapshot_time(str(raw))
    if kickoff is None:
        return None
    return (kickoff - (now or current_time())).total_seconds() / 3600.0


def has_late_window_fixtures(now: datetime | None = None) -> bool:
    """Return True if any fixture is 0–1.25 h from kickoff.

    These windows exist solely to capture the latest price.  When a
    fixture is within 75 minutes of KO, every odds snapshot must be
    fresh — cached reuse is not acceptable.  LO=0 covers ad-hoc
    analyses triggered inside T-30m (e.g. breaking news).
    """
    now = now or current_time()
    for entry in fixture_entries():
        hours = hours_to_kickoff(entry, now)
        if hours is not None and LATE_WINDOW_HOURS_LO <= hours <= LATE_WINDOW_HOURS_HI:
            return True
    return False


def due_windows(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or current_time()
    due: list[dict[str, Any]] = []
    for entry in fixture_entries():
        hours = hours_to_kickoff(entry, now)
        if hours is None:
            continue
        for window, timing_class, lo, hi in WINDOW_SPECS:
            if lo <= hours <= hi:
                due.append({
                    "match_id": entry.get("local_ordinal_id"),
                    "football_data_id": entry.get("football_data_id"),
                    "home": entry.get("home"),
                    "away": entry.get("away"),
                    "kickoff_utc": entry.get("kickoff_utc"),
                    "window": window,
                    "timing_class": timing_class,
                    "hours_to_kickoff": round(hours, 2),
                })
    return due


def manifest_match_id(payload: dict[str, Any]) -> str:
    match = payload.get("match") if isinstance(payload.get("match"), dict) else {}
    return str(payload.get("match_id") or match.get("match_id") or match.get("local_ordinal_id") or "").upper()


def manifest_window(payload: dict[str, Any]) -> str:
    return str(payload.get("window") or "").strip()


def manifest_report_path(payload: dict[str, Any]) -> pathlib.Path | None:
    candidates = [
        payload.get("report_path"),
        payload.get("metadata", {}).get("report_path") if isinstance(payload.get("metadata"), dict) else None,
    ]
    for raw in candidates:
        if isinstance(raw, str) and raw.strip():
            path = pathlib.Path(raw.strip())
            if path.exists():
                return path
    return None


def latest_manifest_for(match_id: str, window: str) -> tuple[pathlib.Path, dict[str, Any]] | None:
    artifacts_dir = WORKSPACE / "reports" / "artifacts"
    candidates: list[tuple[float, pathlib.Path, dict[str, Any]]] = []
    if not artifacts_dir.exists():
        return None
    for path in artifacts_dir.glob("manifest-*.json"):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        if manifest_match_id(payload) != str(match_id).upper():
            continue
        if manifest_window(payload) != window:
            continue
        candidates.append((path.stat().st_mtime, path, payload))
    if not candidates:
        return None
    _, path, payload = max(candidates, key=lambda item: item[0])
    return path, payload


def run_direct_summary(manifest_path: pathlib.Path, report_path: pathlib.Path | None = None) -> str:
    script = SCRIPT_DIR.parent / "skills" / "odds-analysis" / "scripts" / "direct_summary.py"
    cmd = [python_bin(), str(script), "--manifest", str(manifest_path), "--max-chars", "3900"]
    if report_path:
        cmd.extend(["--report", str(report_path)])
    completed = subprocess.run(
        cmd,
        cwd=str(WORKSPACE),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "direct_summary failed").strip())
    return completed.stdout.strip()


def latest_snapshot_status(label: str, directory: pathlib.Path, patterns: list[str]) -> str:
    latest = latest_snapshot(directory, patterns)
    if not latest:
        return f"- {label}: missing"
    path, captured = latest
    age_minutes = max(0.0, (current_time() - captured).total_seconds() / 60.0)
    return f"- {label}: {path.name} | age={age_minutes:.1f}m"


def report_manifest_summaries() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    artifacts_dir = WORKSPACE / "reports" / "artifacts"
    if not artifacts_dir.exists():
        return rows
    for path in sorted(artifacts_dir.glob("manifest-*.json")):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        rows.append({
            "path": str(path),
            "payload": payload,
            "match_id": manifest_match_id(payload) or "unknown",
            "window": manifest_window(payload) or "unknown",
            "final_status": payload.get("final_status") or "unknown",
            "source_quality_cap": payload.get("source_quality_cap") or payload.get("source_quality") or "unknown",
            "created_at_utc": payload.get("created_at_utc") or payload.get("cutoff_utc") or "",
            "created_date_utc": manifest_created_date(path, payload),
        })
    return rows


def load_state(name: str) -> dict[str, Any]:
    state = read_json(STATE_DIR / name, {})
    return state if isinstance(state, dict) else {}


def save_state(name: str, payload: dict[str, Any]) -> None:
    write_json(STATE_DIR / name, payload)


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def report_field(text: str, key: str, default: str = "") -> str:
    match = re.search(rf"(?im)^\s*{re.escape(key)}\s*:\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else default


def numeric_or_none(value: Any) -> float | None:
    if value in (None, "", "N/A", "n/a"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def utc_date_key(value: str | None) -> str:
    parsed = parse_snapshot_time(str(value or ""))
    return parsed.date().isoformat() if parsed else ""


def file_mtime_utc_date(path: pathlib.Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
    except Exception:
        return ""


def artifact_caps(artifact: dict[str, Any]) -> set[str]:
    caps = set()
    provides = artifact.get("provides")
    if isinstance(provides, list):
        caps.update(str(item) for item in provides)
    for key in ("artifact_type", "artifact_kind", "script"):
        raw = str(artifact.get(key) or "")
        if raw:
            caps.add(raw)
    return caps


def resolve_path(raw: Any, base: pathlib.Path | None = None) -> pathlib.Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = pathlib.Path(raw.strip())
    if not path.is_absolute() and base is not None:
        path = (base.parent / path).resolve()
    return path


def load_artifact_payload(artifact: dict[str, Any], manifest_path: pathlib.Path | None = None) -> dict[str, Any] | None:
    path = resolve_path(artifact.get("path"), manifest_path)
    if path is None or not path.exists():
        return None
    payload = read_json(path, None)
    return payload if isinstance(payload, dict) else None


def manifest_created_date(path: pathlib.Path, payload: dict[str, Any]) -> str:
    for key in ("created_at_utc", "cutoff_utc", "generated_at_utc", "captured_at_utc"):
        date_key = utc_date_key(payload.get(key))
        if date_key:
            return date_key
    return file_mtime_utc_date(path)


def analysis_gate_status(gates: Any, name: str) -> str:
    if not isinstance(gates, dict):
        return "missing"
    value = gates.get(name)
    if isinstance(value, dict):
        return str(value.get("status") or value.get("gate") or value.get("result") or "unknown").lower()
    if value is None:
        return "missing"
    return str(value).lower()


def manifest_has_capability(payload: dict[str, Any], capability: str) -> bool:
    for artifact in payload.get("artifacts", []):
        if isinstance(artifact, dict) and capability in artifact_caps(artifact):
            return True
    return False


def is_guarded_direct_manifest(payload: dict[str, Any]) -> bool:
    report_path = manifest_report_path(payload)
    direct_request_path = resolve_path(payload.get("direct_request_path"))
    return bool(
        payload.get("workflow_contract") == "wc26.direct_report.v1"
        and str(payload.get("direct_request_id") or "").strip()
        and direct_request_path is not None
        and direct_request_path.exists()
        and report_path is not None
        and report_path.exists()
    )


def grading_card_path(card_id: str) -> pathlib.Path:
    return GRADING_CARDS_DIR / f"{card_id}.json"


def load_grading_cards() -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    if not GRADING_CARDS_DIR.exists():
        return cards
    for path in sorted(GRADING_CARDS_DIR.glob("*.json")):
        payload = read_json(path, None)
        if isinstance(payload, dict):
            payload.setdefault("_path", str(path))
            cards.append(payload)
    return cards


def write_grading_card(card: dict[str, Any]) -> bool:
    """Write a grading card only when the stable scored content changed."""
    path = grading_card_path(str(card["card_id"]))
    existing = read_json(path, None) if path.exists() else None
    if isinstance(existing, dict) and existing.get("content_hash") == card.get("content_hash"):
        return False
    write_json(path, card)
    return True




def path_c_signal_ledger_path(signal_id: str) -> pathlib.Path:
    return PATH_C_LEDGER_DIR / f"{signal_id}.json"


def write_path_c_signal_ledger(entry: dict[str, Any]) -> bool:
    path = path_c_signal_ledger_path(str(entry["signal_id"]))
    existing = read_json(path, None) if path.exists() else None
    if isinstance(existing, dict) and existing.get("content_hash") == entry.get("content_hash"):
        return False
    write_json(path, entry)
    return True


def remove_superseded_grading_cards(card: dict[str, Any]) -> list[str]:
    """Keep one current postmatch grading card per football-data fixture.

    The postmatch cron grades the latest governed report for a settled match.  If a
    previous bad run wrote the same fixture against a different report/window, leaving
    it in place pollutes first-page counts and min_graded_cards.  Removal is scoped by
    stable football_data_id/match_id and never touches unrelated fixtures.
    """
    removed: list[str] = []
    if not GRADING_CARDS_DIR.exists():
        return removed
    current = str(card.get("card_id") or "")
    fdid = str(card.get("football_data_id") or "")
    match_id = str(card.get("match_id") or "")
    for path in GRADING_CARDS_DIR.glob("*.json"):
        payload = read_json(path, None)
        if not isinstance(payload, dict) or payload.get("card_id") == current:
            continue
        same_fixture = (fdid and str(payload.get("football_data_id") or "") == fdid) or (match_id and str(payload.get("match_id") or "") == match_id)
        if same_fixture:
            path.unlink()
            removed.append(str(path))
    return removed


def remove_superseded_path_c_ledgers(entry: dict[str, Any]) -> list[str]:
    removed: list[str] = []
    if not PATH_C_LEDGER_DIR.exists():
        return removed
    current = str(entry.get("signal_id") or "")
    match_id = str(entry.get("match_id") or "")
    for path in PATH_C_LEDGER_DIR.glob("*.json"):
        payload = read_json(path, None)
        if not isinstance(payload, dict) or payload.get("signal_id") == current:
            continue
        if match_id and str(payload.get("match_id") or "") == match_id:
            path.unlink()
            removed.append(str(path))
    return removed


def canonical_match_id_for_fixture(fm: dict[str, Any], fallback: str, fixture_path: pathlib.Path) -> str:
    fallback = str(fallback or "").strip().upper()
    if re.fullmatch(r"M\d{3}", fallback):
        return fallback
    try:
        registry = FIXTURE_REGISTRY.load_registry(fixture_path)
        entry = FIXTURE_REGISTRY.resolve_fixture(registry, football_data_id=fm.get("id"))
        local_id = str(entry.get("local_ordinal_id") or "").strip().upper()
        if re.fullmatch(r"M\d{3}", local_id):
            return local_id
    except Exception:
        pass
    return fallback


def _event_team(value: Any) -> str | None:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("team") or "").strip() or None
    if isinstance(value, str):
        return value.strip() or None
    return None


def _event_minute(event: dict[str, Any]) -> int | None:
    for key in ("minute", "elapsed"):
        value = event.get(key)
        if value not in (None, ""):
            try:
                return int(value)
            except Exception:
                pass
    time_payload = event.get("time") if isinstance(event.get("time"), dict) else {}
    value = time_payload.get("elapsed")
    try:
        return int(value) if value not in (None, "") else None
    except Exception:
        return None


def _clean_flag(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [])}


def extract_match_context_flags(fm: dict[str, Any], score_h: int, score_a: int) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    existing = fm.get("match_context_flags")
    if isinstance(existing, list):
        for item in existing:
            if isinstance(item, dict) and item.get("type"):
                flags.append(_clean_flag(dict(item)))

    events: list[dict[str, Any]] = []
    for key in ("events", "incidents"):
        raw = fm.get(key)
        if isinstance(raw, list):
            events.extend(item for item in raw if isinstance(item, dict))

    for event in events:
        type_text = " ".join(str(event.get(key, "")) for key in ("type", "detail", "event_type", "incidentType")).lower()
        card_text = " ".join(str(event.get(key, "")) for key in ("card", "card_type", "cardType")).lower()
        team = _event_team(event.get("team")) or _event_team(event.get("side"))
        minute = _event_minute(event)
        player = str(event.get("player") or event.get("player_name") or event.get("name") or "").strip() or None
        reason = str(event.get("reason") or event.get("var_reason") or "").strip() or None
        if "red" in type_text or "red" in card_text:
            flags.append(_clean_flag({"type": "red_card", "team": team, "minute": minute, "player": player, "reason": reason}))
        elif "disallowed" in type_text or (reason is not None and "offside" in reason.lower()):
            flags.append(_clean_flag({"type": "disallowed_goal", "team": team, "minute": minute, "player": player, "reason": reason}))
        elif "penalty" in type_text:
            flags.append(_clean_flag({"type": "penalty", "team": team, "minute": minute, "player": player, "reason": reason}))
        elif "goal" in type_text and minute is not None and minute >= 90 and abs(int(score_h) - int(score_a)) == 1:
            flags.append(_clean_flag({"type": "stoppage_winner", "team": team, "minute": minute, "player": player}))

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for flag in flags:
        key = json.dumps(flag, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            deduped.append(flag)
    return deduped


def artifact_payload_by_capability(
    manifest_payload: dict[str, Any],
    manifest_path: pathlib.Path | None,
    capability: str,
) -> tuple[Any, Any]:
    for artifact in manifest_payload.get("artifacts", []):
        if not isinstance(artifact, dict) or capability not in artifact_caps(artifact):
            continue
        payload = load_artifact_payload(artifact, manifest_path)
        if isinstance(payload, dict):
            return artifact, payload
    return None, None


def normalize_team_name(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    aliases = {
        "czechia": "czechrepublic",
        "czechrep": "czechrepublic",
        "rsa": "southafrica",
        "kor": "southkorea",
    }
    return aliases.get(normalized, normalized)


def team_label_matches(label: Any, team: Any) -> bool:
    left = normalize_team_name(label)
    right = normalize_team_name(team)
    return bool(left and right and (left == right or left in right or right in left))


def manifest_football_data_id(payload: dict[str, Any]) -> str:
    match = payload.get("match") if isinstance(payload.get("match"), dict) else {}
    for value in (payload.get("football_data_id"), match.get("football_data_id"), payload.get("canonical_id"), match.get("canonical_id")):
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text.startswith("fd:"):
            text = text.split(":", 1)[1]
        if text:
            return text
    return ""


def load_report_manifest(report_path: pathlib.Path) -> tuple[pathlib.Path | None, dict[str, Any]]:
    text = report_path.read_text(encoding="utf-8", errors="replace")
    manifest_path = resolve_path(report_field(text, "artifact_manifest_path", ""))
    if manifest_path and manifest_path.exists():
        payload = read_json(manifest_path, {})
        if isinstance(payload, dict):
            return manifest_path, payload
    return None, {}


def report_candidates_for_fixture(
    reports: list[pathlib.Path],
    fm: dict[str, Any],
    fixture_path: pathlib.Path,
) -> list[tuple[float, pathlib.Path, pathlib.Path | None, dict[str, Any]]]:
    """Return report candidates that match the fixture by stable identity, not text mention."""
    fixture_id = str(fm.get("id") or "").strip()
    fallback_id = canonical_match_id_for_fixture(fm, "", fixture_path)
    home = (fm.get("homeTeam") or {}).get("name", "")
    away = (fm.get("awayTeam") or {}).get("name", "")
    candidates: list[tuple[float, pathlib.Path, pathlib.Path | None, dict[str, Any]]] = []
    for report_path in reports:
        manifest_path, payload = load_report_manifest(report_path)
        text = report_path.read_text(encoding="utf-8", errors="replace")
        ids = {manifest_football_data_id(payload), report_field(text, "football_data_id", "")}
        local_ids = {manifest_match_id(payload), report_field(text, "match_id", "").upper()}
        exact_id = bool(fixture_id and fixture_id in ids)
        exact_local = bool(fallback_id and fallback_id in local_ids)
        manifest_match = payload.get("match") if isinstance(payload.get("match"), dict) else {}
        manifest_home = payload.get("home") or manifest_match.get("home")
        manifest_away = payload.get("away") or manifest_match.get("away")
        exact_teams = bool(manifest_home and manifest_away and team_label_matches(manifest_home, home) and team_label_matches(manifest_away, away))
        teams_field = report_field(text, "teams", "")
        frontmatter_teams = bool(teams_field and team_label_matches(home, teams_field) and team_label_matches(away, teams_field))
        if exact_id or exact_local or exact_teams or frontmatter_teams:
            candidates.append((report_path.stat().st_mtime, report_path, manifest_path, payload))
    return sorted(candidates, key=lambda item: item[0], reverse=True)


def parse_report_1x2_model_probs(text: str) -> list[float] | None:
    rows: list[float] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "1X2" not in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 7 or cells[0].lower() in {"market", "---"}:
            continue
        prob = None
        for cell in cells[5:]:
            parts = cell.split()
            value = numeric_or_none(parts[0] if parts else cell)
            if value is not None and 0 <= value <= 1:
                prob = value
                break
        if prob is not None:
            rows.append(prob)
    return rows[:3] if len(rows) >= 3 else None


def uniform_three_way_brier_baseline() -> float:
    return 2.0 / 3.0


def market_outcomes_from_pinnacle(close_odds_raw: dict[str, Any], market_key: str) -> list[dict[str, Any]]:
    for bk in close_odds_raw.get("bookmakers", []):
        if bk.get("key") != "pinnacle":
            continue
        for mk in bk.get("markets", []):
            valid_keys = {market_key, "1x2"} if market_key == "h2h" else {market_key}
            if mk.get("key") in valid_keys:
                outcomes = mk.get("outcomes", [])
                return [oc for oc in outcomes if isinstance(oc, dict)]
    return []


def devig_outcomes_by_name(outcomes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows = [(str(oc.get("name", "")).strip(), numeric_or_none(oc.get("price")), oc.get("point")) for oc in outcomes]
    rows = [(name, price, point) for name, price, point in rows if name and price and price > 0]
    if len(rows) < 2:
        return {}
    probs = DEVIG.devig_shin([price for _, price, _ in rows])
    return {name: {"price": price, "point": point, "fair_prob": prob} for (name, price, point), prob in zip(rows, probs)}


def parse_entry_positions(text: str, home: str) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 6:
            continue
        market = cells[0]
        label = cells[1] if len(cells) > 1 else ""
        if not team_label_matches(label, home):
            continue
        entry_odds = None
        for cell in cells[3:6]:
            parts = cell.split()
            value = numeric_or_none(parts[0] if parts else cell)
            if value is not None and value > 1.01:
                entry_odds = value
                break
        if entry_odds is None:
            continue
        market_lower = market.lower()
        if "1x2" in market_lower:
            positions.append({"market": "h2h", "side": "home", "label": label, "entry_odds": entry_odds, "entry_src": "report_market_board"})
        elif "ah" in market_lower or "spread" in market_lower:
            m = re.search(r"[-+]?\d+(?:\.\d+)?", market)
            line_value = numeric_or_none(m.group(0)) if m else None
            positions.append({"market": "spreads", "side": "home", "label": label, "entry_odds": entry_odds, "entry_line": line_value, "entry_src": "report_market_board"})
    return positions


def compute_clv_positions(text: str, close_odds_raw: dict[str, Any], home: str) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for entry in parse_entry_positions(text, home):
        by_name = devig_outcomes_by_name(market_outcomes_from_pinnacle(close_odds_raw, str(entry["market"])))
        matched_name = next((name for name in by_name if team_label_matches(name, home)), None)
        if not matched_name:
            continue
        closing = by_name[matched_name]
        if entry.get("market") == "spreads" and entry.get("entry_line") is not None and closing.get("point") is not None:
            if abs(abs(float(entry["entry_line"])) - abs(float(closing["point"]))) > 1e-9:
                continue
        clv_ev = round(float(entry["entry_odds"]) * float(closing["fair_prob"]) - 1.0, 4)
        positions.append({
            **entry,
            "closing_outcome": matched_name,
            "closing_point": closing.get("point"),
            "closing_board_odds": closing.get("price"),
            "closing_fair_prob": closing.get("fair_prob"),
            "clv_ev": clv_ev,
            "clv_pct": round(clv_ev * 100.0, 2),
            "unit": "percent_ev_fraction",
        })
    return positions


def score_deep_research_findings(findings: Any, actual_outcome: str, context_flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(findings, list):
        return []
    red_card_seen = any(flag.get("type") == "red_card" for flag in context_flags if isinstance(flag, dict))
    scored: list[dict[str, Any]] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        direction = str(item.get("direction") or "").lower()
        finding_id = str(item.get("finding_id") or "")
        score = "not_applicable"
        if direction in {"toward_favorite", "toward_home"}:
            score = "hit" if actual_outcome == "home" else "miss"
        elif direction in {"toward_underdog", "toward_away"}:
            score = "hit" if actual_outcome == "away" else "miss"
        elif direction == "toward_under":
            score = "confounded_by_red_card" if red_card_seen and (finding_id == "DR-F4" or "defens" in str(item.get("claim", "")).lower()) else "ledger_only"
        elif direction in {"mixed", "neutral"}:
            score = "mixed"
        scored.append({
            "finding_id": finding_id,
            "direction": item.get("direction"),
            "confidence": item.get("confidence"),
            "score": score,
            "red_card_downgraded": bool(score == "confounded_by_red_card"),
            "claim": item.get("claim"),
        })
    return scored


def scoreline_profile_from_path_c(path_c_payload: dict[str, Any] | None, actual_score: str) -> dict[str, Any]:
    result = {"actual_score": actual_score, "prob": None, "prob_pct": None, "rank": None, "tied_rank": None, "source": "missing"}
    if not isinstance(path_c_payload, dict):
        return result
    profile = path_c_payload.get("market_profile") if isinstance(path_c_payload.get("market_profile"), dict) else {}
    rows = profile.get("score_distribution") if isinstance(profile.get("score_distribution"), list) else None
    source = "score_distribution"
    if rows is None:
        rows = profile.get("top_scores") if isinstance(profile.get("top_scores"), list) else []
        source = "top_scores"
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or str(row.get("score")) != actual_score:
            continue
        rank = row.get("rank") if row.get("rank") is not None else index
        tied_rank = row.get("tied_rank") if row.get("tied_rank") is not None else rank
        return {
            "actual_score": actual_score,
            "prob": row.get("prob"),
            "prob_pct": row.get("prob_pct"),
            "rank": rank,
            "tied_rank": tied_rank,
            "fair_odds": row.get("fair_odds"),
            "source": source,
        }
    return result


def total_settlement_score(total_goals: int, line: float, side: str) -> float:
    doubled = round(float(line) * 2)
    if abs(float(line) * 2 - doubled) < 1e-9:
        legs = [float(line)]
    else:
        legs = [math.floor(float(line) * 2) / 2.0, math.ceil(float(line) * 2) / 2.0]
    scores = []
    for leg in legs:
        if side == "over":
            scores.append(1.0 if total_goals > leg else (0.5 if total_goals == leg else 0.0))
        else:
            scores.append(1.0 if total_goals < leg else (0.5 if total_goals == leg else 0.0))
    return sum(scores) / len(scores) if scores else 0.0


def signal_pp_band(value: float | None) -> str | None:
    if value is None:
        return None
    pp = abs(value)
    if pp < 5:
        return "lt5"
    if pp < 10:
        return "5_10"
    if pp < 15:
        return "10_15"
    return "ge15"


def path_c_outcome_agrees(path_c_payload: dict[str, Any], score_h: int, score_a: int) -> bool | None:
    discrepancy = path_c_payload.get("discrepancy") if isinstance(path_c_payload.get("discrepancy"), dict) else {}
    signal = path_c_payload.get("signal") if isinstance(path_c_payload.get("signal"), dict) else {}
    direction = str(discrepancy.get("direction") or signal.get("direction") or "").strip().lower()
    profile = path_c_payload.get("market_profile") if isinstance(path_c_payload.get("market_profile"), dict) else {}
    total_lean = profile.get("total_line_lean") if isinstance(profile.get("total_line_lean"), dict) else {}
    line = numeric_or_none(total_lean.get("line") or discrepancy.get("line"))
    if line is None or direction not in {"under_cheap", "over_cheap"}:
        return None
    side = "under" if direction == "under_cheap" else "over"
    settlement = total_settlement_score(int(score_h) + int(score_a), line, side)
    if settlement > 0.5:
        return True
    if settlement < 0.5:
        return False
    return None


def build_path_c_signal_ledger(
    artifact: dict[str, Any],
    path_c_payload: dict[str, Any],
    match_id: str,
    window: str,
    result_str: str,
    score_h: int,
    score_a: int,
    graded_at: str,
) -> dict[str, Any]:
    signal = path_c_payload.get("signal") if isinstance(path_c_payload.get("signal"), dict) else {}
    discrepancy = path_c_payload.get("discrepancy") if isinstance(path_c_payload.get("discrepancy"), dict) else {}
    raw_pp = numeric_or_none(discrepancy.get("raw_pp"))
    pp = numeric_or_none(discrepancy.get("pp"))
    signal_pp = pp if pp is not None else numeric_or_none(signal.get("raw_discrepancy_pp"))
    if signal_pp is None:
        signal_pp = raw_pp
    direction = str(discrepancy.get("direction") or signal.get("direction") or "").strip() or None
    signal_id = "pathc-" + stable_hash([match_id, window, artifact.get("artifact_id"), result_str])
    core = {
        "schema_version": "wc26.path_c_signal_ledger.v1",
        "signal_id": signal_id,
        "match_id": match_id,
        "window": window,
        "artifact_id": artifact.get("artifact_id"),
        "actual_score": result_str,
        "actual_total_goals": int(score_h) + int(score_a),
        "signal_type": signal.get("type"),
        "raw_type": signal.get("raw_type"),
        "strength": signal.get("strength"),
        "raw_strength": signal.get("raw_strength"),
        "direction": direction,
        "pp": pp,
        "raw_pp": raw_pp,
        "signal_pp": signal_pp,
        "pp_band": signal_pp_band(signal_pp),
        "suppressed": bool(signal.get("suppressed") or discrepancy.get("suppressed")),
        "suppress_reason": signal.get("suppress_reason") or discrepancy.get("suppress_reason"),
        "outcome_agrees": path_c_outcome_agrees(path_c_payload, score_h, score_a),
        "graded_at_utc": graded_at,
    }
    return {**core, "content_hash": stable_hash(core)}


def closing_odds_snapshot_for_kickoff(kickoff: datetime | None) -> tuple[pathlib.Path, datetime] | None:
    if kickoff is None:
        return latest_snapshot(WORKSPACE / "snapshots" / "odds", ["the-odds-api-*.json"])
    candidates: list[tuple[pathlib.Path, datetime]] = []
    for path in (WORKSPACE / "snapshots" / "odds").glob("the-odds-api-*.json"):
        if not path.is_file():
            continue
        captured = snapshot_time(path)
        if captured is not None and captured < kickoff:
            candidates.append((path, captured))
    return max(candidates, key=lambda item: item[1]) if candidates else None


def closing_odds_by_match(snapshot: tuple[pathlib.Path, datetime] | None) -> dict[str, Any]:
    if not snapshot:
        return {}
    odds_data = json.loads(snapshot[0].read_text())
    odds_list = odds_data if isinstance(odds_data, list) else odds_data.get("data", [])
    closing_odds: dict[str, Any] = {}
    for om in odds_list:
        key = f"{om.get('home_team','')}_{om.get('away_team','')}".lower().replace(" ", "")
        closing_odds[key] = om
    return closing_odds


def closing_odds_for_fixture(closing_odds: dict[str, Any], home: str, away: str) -> dict[str, Any]:
    exact_key = f"{home}_{away}".lower().replace(" ", "")
    if exact_key in closing_odds:
        return closing_odds[exact_key]
    for om in closing_odds.values():
        if not isinstance(om, dict):
            continue
        if team_label_matches(om.get("home_team"), home) and team_label_matches(om.get("away_team"), away):
            return om
    return {}


def stale_postmatch_fixture_records(all_matches: list[dict[str, Any]], fixture_captured: datetime) -> list[dict[str, Any]]:
    grace = int(os.environ.get("WC26_POSTMATCH_FIXTURE_GRACE_MINUTES", "120"))
    now = current_time()
    stale: list[dict[str, Any]] = []
    for fm in all_matches:
        status = str(fm.get("status", fm.get("stage", "")))
        if status in {"FINISHED", "AWARDED"}:
            continue
        kickoff = parse_snapshot_time(str(fm.get("utcDate") or fm.get("kickoff_utc") or ""))
        if kickoff is None:
            continue
        if now >= kickoff + timedelta(minutes=grace) and fixture_captured < kickoff:
            stale.append(
                {
                    "football_data_id": fm.get("id"),
                    "home": (fm.get("homeTeam") or {}).get("name"),
                    "away": (fm.get("awayTeam") or {}).get("name"),
                    "kickoff_utc": kickoff.isoformat().replace("+00:00", "Z"),
                    "status": status,
                }
            )
    return stale
def proposal_summaries(today: str) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    proposals_dir = WORKSPACE / "proposals"
    proposals = sorted(proposals_dir.glob("*.md")) if proposals_dir.exists() else []
    today_items = [path for path in proposals if file_mtime_utc_date(path) == today]
    return proposals, today_items


def path_a_stats(manifests: list[dict[str, Any]], today: str | None = None) -> dict[str, Any]:
    totals = Counter()
    artifact_paths: set[str] = set()
    for row in manifests:
        if today and row.get("created_date_utc") != today:
            continue
        manifest_path = pathlib.Path(str(row.get("path", "")))
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        for artifact in payload.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            if "path_a_crossbook" not in artifact_caps(artifact):
                continue
            artifact_path = resolve_path(artifact.get("path"), manifest_path)
            artifact_key = str(artifact_path) if artifact_path else str(artifact.get("artifact_id") or artifact.get("path"))
            if artifact_key in artifact_paths:
                continue
            artifact_paths.add(artifact_key)
            crossbook = load_artifact_payload(artifact, manifest_path)
            if not crossbook:
                totals["missing_artifact"] += 1
                continue
            markets = crossbook.get("markets") if isinstance(crossbook.get("markets"), dict) else {}
            for market in markets.values():
                if not isinstance(market, dict):
                    continue
                totals["quotes_scanned"] += int(market.get("quotes_scanned") or 0)
                totals["edge_count"] += int(market.get("edge_count") or 0)
                totals["noise_edge_count"] += int(market.get("noise_edge_count") or 0)
                totals["actionable_count"] += int(market.get("actionable_count") or 0)
                totals["raw_actionable_count"] += int(market.get("raw_actionable_count", market.get("actionable_count")) or 0)
                totals["relay_actionable_count"] += int(market.get("relay_actionable_count") or 0)
                totals["qualified_play_count"] += int(market.get("qualified_play_count", market.get("relay_actionable_count")) or 0)
                quotes = market.get("quotes") if isinstance(market.get("quotes"), list) else []
                totals["suspect_count"] += sum(1 for quote in quotes if isinstance(quote, dict) and quote.get("suspect"))
    totals["artifact_count"] = len(artifact_paths)
    return dict(totals)


def mechanism_gap_stats(manifests: list[dict[str, Any]], today: str | None = None) -> dict[str, Any]:
    stats = Counter()
    considered = 0
    for row in manifests:
        if today and row.get("created_date_utc") != today:
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        considered += 1
        gates = payload.get("analysis_gates")
        for name, capability in (("role_engine", "role_engine"), ("mechanism_audit", "mechanism_audit")):
            gate = analysis_gate_status(gates, name)
            has_artifact = manifest_has_capability(payload, capability)
            if gate in {"missing", "fail", "failed", "blocked", "block"} or not has_artifact:
                stats[f"{name}_missing_or_blocked"] += 1
            else:
                stats[f"{name}_present"] += 1
    stats["manifests_considered"] = considered
    return dict(stats)


def grading_aggregates(cards: list[dict[str, Any]], today: str) -> dict[str, Any]:
    today_cards = [card for card in cards if utc_date_key(card.get("graded_at_utc")) == today]
    clv_values = [value for value in (numeric_or_none(card.get("clv_raw")) for card in cards) if value is not None]
    brier_values = [value for value in (numeric_or_none(card.get("brier")) for card in cards) if value is not None]
    logloss_values = [value for value in (numeric_or_none(card.get("log_loss")) for card in cards) if value is not None]
    clv_by_window: dict[str, list[float]] = {}
    bucket_counts = Counter()
    for card in cards:
        clv = numeric_or_none(card.get("clv_raw"))
        if clv is not None:
            clv_by_window.setdefault(str(card.get("timing_class") or card.get("window") or "unknown"), []).append(clv)
        p_adj = numeric_or_none(card.get("p_adj_actual_outcome"))
        if p_adj is None:
            bucket_counts["p_adj_missing"] += 1
        else:
            low = int(p_adj * 10) * 10
            high = min(low + 10, 100)
            bucket_counts[f"{low:02d}-{high:02d}%"] += 1
    return {
        "cards_total": len(cards),
        "cards_today": len(today_cards),
        "today_cards": today_cards,
        "clv_mean": mean(clv_values),
        "clv_count": len(clv_values),
        "brier_mean": mean(brier_values),
        "brier_count": len(brier_values),
        "log_loss_mean": mean(logloss_values),
        "log_loss_count": len(logloss_values),
        "clv_by_window": {key: {"n": len(values), "mean": mean(values)} for key, values in sorted(clv_by_window.items())},
        "calibration_buckets": dict(sorted(bucket_counts.items())),
    }


def source_health() -> int:
    script = SCRIPT_DIR.parent / "skills" / "odds-analysis" / "scripts" / "verify_keys.py"
    if not script.exists():
        return emit(manifest("wc26-source-health", "fail", reason="verify_keys.py missing", exit_code=1))
    env = os.environ.copy()
    # Default source health must not spend scarce paid snapshot quota. The
    # operator may opt in by setting these env vars before a manual run.
    env.setdefault("VERIFY_ODDS_PROBE", "0")
    env.setdefault("VERIFY_ODDSPAPI_HEALTH", "0")
    env.setdefault("VERIFY_ODDSPAPI_ODDS", "0")
    env.setdefault("VERIFY_ODDSPAPI_MARKETS", "0")
    completed = subprocess.run(
        [python_bin(), str(script)],
        cwd=str(WORKSPACE),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
    )
    output = completed.stdout.rstrip()
    if output:
        print(output)
    status = "ok" if completed.returncode == 0 else "fail"
    write_json(WORKSPACE / "logs" / "wc26-source-health-last.json", manifest("wc26-source-health", status, returncode=completed.returncode))
    return completed.returncode


def _merge_fixture_detail_fields(match: dict[str, Any], detail_payload: dict[str, Any]) -> bool:
    """Merge event/incidents/context detail fields from a football-data match detail payload."""
    detail = detail_payload.get("match") if isinstance(detail_payload.get("match"), dict) else detail_payload
    if not isinstance(detail, dict):
        return False
    changed = False
    for key in ("events", "incidents", "match_context_flags"):
        value = detail.get(key)
        if isinstance(value, list):
            match[key] = value
            changed = True
    return changed


def _fixture_detail_statuses() -> set[str]:
    raw = os.environ.get("WC26_FIXTURE_DETAIL_STATUSES", "FINISHED,AWARDED")
    return {part.strip().upper() for part in raw.split(",") if part.strip()}


def enrich_fixture_details(matches: list[dict[str, Any]], token: str) -> dict[str, Any]:
    """Fetch per-match detail records for settled fixtures so postmatch flags are data-backed."""
    statuses = _fixture_detail_statuses()
    summary = {"attempted": 0, "merged": 0, "failed": 0, "http_statuses": {}}
    for match in matches:
        if not isinstance(match, dict):
            continue
        status = str(match.get("status") or "").upper()
        fixture_id = match.get("id")
        if status not in statuses or fixture_id in (None, ""):
            continue
        summary["attempted"] += 1
        try:
            response = requests.get(
                f"https://api.football-data.org/v4/matches/{fixture_id}",
                headers={"X-Auth-Token": token},
                timeout=30,
            )
        except requests.RequestException:
            summary["failed"] += 1
            continue
        status_key = str(response.status_code)
        summary["http_statuses"][status_key] = int(summary["http_statuses"].get(status_key, 0)) + 1
        if response.status_code != 200:
            summary["failed"] += 1
            continue
        if _merge_fixture_detail_fields(match, response.json()):
            summary["merged"] += 1
    return summary


def apply_match_context_overrides(matches: list[dict[str, Any]], overrides_path: pathlib.Path = CONTEXT_OVERRIDES_PATH) -> dict[str, Any]:
    """Apply governed manual context flags when upstream fixture detail has no event payload."""
    summary = {"path": str(overrides_path), "applied": 0, "missing_file": False}
    if not overrides_path.exists():
        summary["missing_file"] = True
        return summary
    payload = read_json(overrides_path, {})
    if not isinstance(payload, dict):
        return summary
    by_id = payload.get("matches") if isinstance(payload.get("matches"), dict) else {}
    for match in matches:
        if not isinstance(match, dict):
            continue
        flags = by_id.get(str(match.get("id")))
        if not isinstance(flags, list) or not flags:
            continue
        has_source_events = any(isinstance(match.get(key), list) and match.get(key) for key in ("events", "incidents"))
        existing = match.get("match_context_flags") if isinstance(match.get("match_context_flags"), list) else []
        if has_source_events or existing:
            continue
        match["match_context_flags"] = [dict(item) for item in flags if isinstance(item, dict)]
        if match["match_context_flags"]:
            summary["applied"] += 1
    return summary


def fixture_collect() -> int:
    if not force_refresh_requested():
        reusable = reusable_snapshot(
            WORKSPACE / "snapshots" / "fixtures",
            ["football-data-wc-matches-latest.json"],
            int(os.environ.get("WC26_FIXTURE_TTL_MINUTES", "1440")),
        )
        if reusable:
            return emit(cache_reuse_manifest("wc26-fixture-collect", reusable))
    token = os.environ.get("FOOTBALL_DATA_TOKEN")
    if not token:
        return emit(manifest("wc26-fixture-collect", "blocked", reason="FOOTBALL_DATA_TOKEN missing", exit_code=1))

    # ── 重试 3 次，指数退避：内部代理到 football-data.org 的 SSL 握手间歇性超时 ──
    import time as _time
    last_exc: Exception | None = None
    response = None
    for attempt in range(3):
        try:
            response = requests.get(
                "https://api.football-data.org/v4/competitions/WC/matches",
                headers={"X-Auth-Token": token},
                timeout=30,
            )
            last_exc = None
            break  # connection succeeded, exit retry loop
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < 2:  # don't sleep on last attempt
                wait = 2 ** attempt * 5  # 5s, 10s
                _time.sleep(wait)
    if last_exc is not None:
        return emit(manifest("wc26-fixture-collect", "fail", error=str(last_exc)[:240], exit_code=1))
    payload = manifest(
        "wc26-fixture-collect",
        "ok" if response.status_code == 200 else "fail",
        http_status=response.status_code,
        minute_remaining=response.headers.get("X-Requests-Available-Minute"),
    )
    if response.status_code == 200:
        data = response.json()
        matches = data.get("matches", []) if isinstance(data, dict) else []
        detail_summary = enrich_fixture_details(matches, token) if isinstance(matches, list) else {"attempted": 0, "merged": 0, "failed": 0, "http_statuses": {}}
        override_summary = apply_match_context_overrides(matches) if isinstance(matches, list) else {"applied": 0}
        path = WORKSPACE / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json"
        write_json(path, {"captured_at_utc": utc_now(), "source": "football-data.org", "fixture_detail_source": "football-data.org/v4/matches/{id}", "fixture_detail_summary": detail_summary, "match_context_override_summary": override_summary, "data": data})
        payload.update(match_count=len(matches), fixture_detail_summary=detail_summary, match_context_override_summary=override_summary, snapshot_path=str(path))
    else:
        payload.update(error=response.text[:240], exit_code=1)
    return emit(payload)


def odds_broad_scan() -> int:
    # ── Late-window auto-force-refresh ──
    # T-60m and T-45m windows exist to capture the latest price.
    # When any fixture is within 0.5-1.25h of kickoff, skip TTL
    # reuse regardless of the cron schedule or env override.
    force = force_refresh_requested() or has_late_window_fixtures()
    if not force:
        reusable = reusable_snapshot(
            WORKSPACE / "snapshots" / "odds",
            ["the-odds-api-multibook-*.json"],
            int(os.environ.get("WC26_ODDS_BROAD_SCAN_TTL_MINUTES", "120")),
        )
        if reusable:
            return emit(cache_reuse_manifest("wc26-odds-broad-scan", reusable))
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        return emit(manifest("wc26-odds-broad-scan", "blocked", reason="ODDS_API_KEY missing", exit_code=1))
    params = {
        "apiKey": key,
        "regions": os.environ.get("WC26_ODDS_REGION", "eu"),
        "markets": os.environ.get("WC26_ODDS_MARKETS", "h2h,spreads,totals"),
        "oddsFormat": "decimal",
    }
    bookmakers = os.environ.get("WC26_ODDS_BOOKMAKERS", "").strip()
    if bookmakers:
        params["bookmakers"] = bookmakers
    try:
        response = requests.get(
            "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds/",
            params=params,
            timeout=30,
        )
    except requests.RequestException as exc:
        return emit(manifest("wc26-odds-broad-scan", "fail", error=str(exc)[:240], exit_code=1))
    payload = manifest(
        "wc26-odds-broad-scan",
        "ok" if response.status_code == 200 else "fail",
        http_status=response.status_code,
        requests_last=response.headers.get("x-requests-last"),
        requests_remaining=response.headers.get("x-requests-remaining"),
    )
    if response.status_code == 200:
        data = response.json()
        book_keys = sorted(
            {
                str(book.get("key"))
                for event in data
                if isinstance(event, dict)
                for book in event.get("bookmakers", [])
                if isinstance(book, dict) and book.get("key")
            }
        )
        prefix = "the-odds-api-bookmakers" if bookmakers else "the-odds-api-multibook"
        path = WORKSPACE / "snapshots" / "odds" / f"{prefix}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        write_json(
            path,
            {
                "captured_at_utc": utc_now(),
                "source": "the-odds-api",
                "bookmakers_filter": bookmakers,
                "bookmaker_count": len(book_keys),
                "bookmakers": book_keys,
                "data": data,
            },
        )
        payload.update(event_count=len(data), bookmaker_count=len(book_keys), snapshot_path=str(path))

        # ── Validate snapshot quality before any downstream consumer sees it ──
        validator = SCRIPT_DIR / "snapshot_validator.py"
        health_dir = WORKSPACE / "snapshots" / "health"
        health_dir.mkdir(parents=True, exist_ok=True)
        health_path = health_dir / f"{path.stem}.health.json"
        try:
            vret = subprocess.run(
                [python_bin(), str(validator), str(path),
                 "--output", str(health_path)],
                cwd=str(WORKSPACE),
                text=True,
                timeout=60,
            )
            health_status = "validated"
            if vret.returncode == 2:
                health_status = "has_errors"
            elif vret.returncode == 1:
                health_status = "has_warnings"
            payload["snapshot_health"] = str(health_path)
            payload["snapshot_health_status"] = health_status
        except Exception as vexc:
            payload["snapshot_health_error"] = str(vexc)[:200]
    else:
        payload.update(error=response.text[:240], exit_code=1)
    return emit(payload)


def oddspapi_ah_snapshot() -> int:
    if not force_refresh_requested():
        reusable = reusable_snapshot(
            WORKSPACE / "snapshots" / "odds",
            ["oddspapi-*.json"],
            int(os.environ.get("WC26_ODDSPAPI_AH_TTL_MINUTES", "720")),
        )
        if reusable:
            return emit(cache_reuse_manifest("wc26-oddspapi-ah-snapshot", reusable))
    key = os.environ.get("ODDSPAPI_KEY")
    if not key:
        return emit(manifest("wc26-oddspapi-ah-snapshot", "blocked", reason="ODDSPAPI_KEY missing", exit_code=1))
    try:
        response = requests.get(
            "https://api.oddspapi.io/v4/odds-by-tournaments",
            params={
                "apiKey": key,
                "tournamentIds": os.environ.get("WC26_ODDSPAPI_TOURNAMENT_ID", "16"),
                "bookmaker": os.environ.get("WC26_ODDSPAPI_BOOKMAKER", "pinnacle"),
                "language": "en",
                "verbosity": "3",
                "oddsFormat": "decimal",
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        return emit(manifest("wc26-oddspapi-ah-snapshot", "fail", error=str(exc)[:240], exit_code=1))
    payload = manifest("wc26-oddspapi-ah-snapshot", "ok" if response.status_code == 200 else "fail", http_status=response.status_code)
    if response.status_code == 200:
        data = response.json()
        path = WORKSPACE / "snapshots" / "odds" / f"oddspapi-t16-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        write_json(path, {"captured_at_utc": utc_now(), "source": "oddspapi", "data": data})
        payload.update(row_count=len(data) if isinstance(data, list) else None, snapshot_path=str(path))

        # ── Validate snapshot quality ──
        validator = SCRIPT_DIR / "snapshot_validator.py"
        health_dir = WORKSPACE / "snapshots" / "health"
        health_dir.mkdir(parents=True, exist_ok=True)
        health_path = health_dir / f"{path.stem}.health.json"
        try:
            vret = subprocess.run(
                [python_bin(), str(validator), str(path),
                 "--output", str(health_path)],
                cwd=str(WORKSPACE),
                text=True,
                timeout=60,
            )
            health_status = "validated"
            if vret.returncode == 2:
                health_status = "has_errors"
            elif vret.returncode == 1:
                health_status = "has_warnings"
            payload["snapshot_health"] = str(health_path)
            payload["snapshot_health_status"] = health_status
        except Exception as vexc:
            payload["snapshot_health_error"] = str(vexc)[:200]
    else:
        payload.update(error=response.text[:240], exit_code=1)
    return emit(payload)


def postmatch_grade() -> int:
    """
    Post-match grading for all settled WC26 matches.
    Runs after each matchday (~1h post-kickoff).

    For each report where the match has finished:
      1. Fetch actual result from football-data fixture snapshot
      2. Fetch closing odds from the last pre-kickoff the-odds-api snapshot
      3. Read report entry prices from report frontmatter
      4. Compute CLV: entry_odds × closing_fair_prob − 1
      5. Compute L1: model Brier/log-loss vs actual result
      6. L2 audit: decision consistency
      7. Write Section 11 to report .md
      8. Append to grading record
    """
    # --- Load fixture snapshot ---
    fixture_snap = latest_snapshot(
        WORKSPACE / "snapshots" / "fixtures",
        ["football-data-wc-matches-latest.json"],
    )
    if not fixture_snap:
        return emit(manifest("wc26-postmatch-grade", "fail", reason="no_fixture_snapshot", exit_code=1))

    fixtures = json.loads(fixture_snap[0].read_text())
    all_matches = fixtures.get("data", {}).get("matches",
                   fixtures.get("matches", []))


    stale_records = stale_postmatch_fixture_records(all_matches, fixture_snap[1])
    if stale_records and not any(str(fm.get("status", fm.get("stage", ""))) in {"FINISHED", "AWARDED"} for fm in all_matches):
        return emit(manifest(
            "wc26-postmatch-grade",
            "blocked_stale_fixture_snapshot",
            exit_code=2,
            stale_match_count=len(stale_records),
            stale_matches=stale_records[:10],
            fixture_snapshot=str(fixture_snap[0]),
            fixture_captured_at_utc=fixture_snap[1].isoformat().replace("+00:00", "Z"),
            required_action="run wc26-fixture-collect with WC26_FORCE_REFRESH=1 before grading",
        ))

    # --- Load all reports ---
    reports_dir = WORKSPACE / "reports" / "match"
    reports = sorted(reports_dir.glob("*.md")) if reports_dir.exists() else []
    print(f"[postmatch] {len(reports)} reports, {len(all_matches)} fixture records")

    graded = 0
    settled_seen = 0
    graded_cards: list[dict[str, Any]] = []
    for fm in all_matches:
        status = fm.get("status", fm.get("stage", ""))
        if status not in ("FINISHED", "AWARDED"):
            continue
        settled_seen += 1

        home = fm.get("homeTeam", {}).get("name", "")
        away = fm.get("awayTeam", {}).get("name", "")
        score_h = fm.get("score", {}).get("fullTime", {}).get("home",
                 fm.get("home_score"))
        score_a = fm.get("score", {}).get("fullTime", {}).get("away",
                 fm.get("away_score"))
        if score_h is None or score_a is None:
            continue

        match_key = f"{home}_{away}".lower().replace(" ", "")
        result_str = f"{score_h}-{score_a}"
        actual_margin = int(score_h or 0) - int(score_a or 0)
        actual_vec = [1,0,0] if score_h > score_a else ([0,1,0] if score_h == score_a else [0,0,1])

        kickoff = parse_snapshot_time(str(fm.get("utcDate") or ""))
        odds_snap = closing_odds_snapshot_for_kickoff(kickoff)
        closing_odds = closing_odds_by_match(odds_snap)
        closing_snapshot_age_at_kickoff = None
        closing_quality = "missing"
        if odds_snap and kickoff is not None:
            closing_snapshot_age_at_kickoff = round((kickoff - odds_snap[1]).total_seconds() / 60)
            max_age = int(os.environ.get("WC26_CLOSING_MAX_AGE_MINUTES", "90"))
            closing_quality = "ok" if closing_snapshot_age_at_kickoff <= max_age else "degraded"
        elif odds_snap:
            closing_quality = "unknown"

        # Find matching report by governed identity (football_data_id / local Mxxx),
        # never by free-text team mention.  This prevents group-context mentions from
        # drifting M001 into the KOR-CZE report.
        candidates = report_candidates_for_fixture(reports, fm, fixture_snap[0])
        if not candidates:
            continue
        _, report_path, report_manifest_path, report_manifest = candidates[0]
        text = report_path.read_text(encoding="utf-8", errors="replace")
        report_mtime = datetime.fromtimestamp(report_path.stat().st_mtime, tz=timezone.utc)
        if not isinstance(report_manifest, dict):
            report_manifest = {}

        # --- Parse entry prices from report frontmatter ---
        entry_match = re.search(r"entry_price:\s*(.+)", text)
        entry_price_str = entry_match.group(1).strip() if entry_match else "N/A"

        # Parse p_model from Market Board
        p_model_match = re.findall(
            r"\| 1X2 [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \| ([\d.]+) \| info",
            text
        )
        model_probs = [float(x) for x in p_model_match[:3]] if len(p_model_match) >= 3 else None
        if model_probs is None:
            model_probs = parse_report_1x2_model_probs(text)

        # --- L1: Model Brier ---
        brier = None
        brier_baseline_uniform = uniform_three_way_brier_baseline()
        brier_skill_vs_uniform = None
        ll = None
        if model_probs and len(model_probs) == 3:
            brier = sum((model_probs[i] - actual_vec[i])**2 for i in range(3))
            brier_skill_vs_uniform = round(brier - brier_baseline_uniform, 4)
            eps = 1e-15
            clipped = [max(min(p, 1-eps), eps) for p in model_probs]
            ll = -sum(actual_vec[i] * math.log(clipped[i]) for i in range(3))

        # --- CLV from closing odds ---
        clv = None
        clv_detail = {}
        close_odds_raw = closing_odds_for_fixture(closing_odds, home, away)
        if close_odds_raw:
            # Find Pinnacle closing h2h
            for bk in close_odds_raw.get("bookmakers", []):
                if bk.get("key") == "pinnacle":
                    for mk in bk.get("markets", []):
                        if mk.get("key") in ("h2h", "1x2"):
                            outcomes = mk.get("outcomes", [])
                            if len(outcomes) >= 3:
                                # Build name→price mapping (alignment by outcome name, not index)
                                close_by_name = {}
                                for oc in outcomes:
                                    name = str(oc.get("name", "")).strip()
                                    price = oc.get("price", 0)
                                    if name and price > 0:
                                        close_by_name[name] = price
                                if len(close_by_name) < 3:
                                    break
                                close_prices = list(close_by_name.values())
                                close_names = list(close_by_name.keys())
                                # Compute closing no-vig (shin)
                                close_nv = DEVIG.devig_shin(close_prices)
                                close_nv_by_name = dict(zip(close_names, close_nv))
                                # Match home team by name, not by index.
                                # the-odds-api outcome order is not guaranteed;
                                # only the fixture's home team name is authoritative.
                                home_team = (home or "").strip().lower()
                                home_outcome_name = None
                                for cname in close_names:
                                    if cname.strip().lower() == home_team:
                                        home_outcome_name = cname
                                        break
                                if not home_outcome_name:
                                    # Fallback: the-odds-api always puts hoem first in practice
                                    home_outcome_name = close_names[0]
                                # Prefer artifact JSON for entry data, fall back to regex
                                entry_odds = None
                                entry_nv = None
                                entry_src = "regex"
                                # First: try manifest artifact
                                # Real devig artifact schema: decimal_odds[], no_vig_probabilities[]
                                # (parallel lists, no no_vig dict or markets.1x2.prices)
                                for art in report_manifest.get("artifacts", []):
                                    if not isinstance(art, dict):
                                        continue
                                    if "devig_1x2" in artifact_caps(art):
                                        art_payload = load_artifact_payload(art, report_manifest_path)
                                        if isinstance(art_payload, dict):
                                            art_odds = art_payload.get("decimal_odds")
                                            art_nv = art_payload.get("no_vig_probabilities")
                                            if isinstance(art_odds, list) and isinstance(art_nv, list) and len(art_odds) >= 3 and len(art_nv) >= 3:
                                                # Match home outcome by name from the API order
                                                close_oc_list = list(close_by_name.keys())
                                                for idx, cname in enumerate(close_oc_list):
                                                    if cname == home_outcome_name and idx < len(art_odds) and idx < len(art_nv):
                                                        entry_odds = art_odds[idx]
                                                        entry_nv = art_nv[idx]
                                                        entry_src = "artifact"
                                                        break
                                                if entry_odds is None:
                                                    # If no name match, use same index as home (positional fallback)
                                                    entry_odds = art_odds[0]
                                                    entry_nv = art_nv[0]
                                                    entry_src = "artifact"
                                                break
                                # Fallback: regex from report text
                                if entry_odds is None:
                                    entry_market = re.search(
                                        r"\| 1X2 H [^|]+ \| [^|]+ \| decimal \| ([\d.]+) [^|]+ [^|]+ [^|]+ ([\d.]+)",
                                        text
                                    )
                                    if entry_market:
                                        entry_odds = float(entry_market.group(1))
                                        entry_nv = float(entry_market.group(2))
                                        entry_src = "regex"
                                # Compute CLV = entry_odds × closing_fair_prob − 1
                                if entry_odds is not None and entry_odds > 0:
                                    closing_fair_prob = close_nv_by_name.get(home_outcome_name or close_names[0])
                                    if closing_fair_prob is not None and closing_fair_prob > 0:
                                        clv = round(entry_odds * closing_fair_prob - 1, 4)
                                        clv_detail = {
                                            "entry_odds": entry_odds,
                                            "entry_no_vig": entry_nv,
                                            "closing_no_vig": closing_fair_prob,
                                            "closing_outcome": home_outcome_name or close_names[0],
                                            "clv_raw": clv,
                                            "clv_pct": round(clv * 100.0, 2),
                                            "unit": "percent_ev_fraction",
                                            "entry_src": entry_src,
                                            "market": "h2h",
                                        }
                                    break

        clv_positions = compute_clv_positions(text, close_odds_raw, home) if close_odds_raw else []
        if clv_positions:
            primary = next((item for item in clv_positions if item.get("market") == "h2h"), clv_positions[0])
            clv = primary["clv_ev"]
            clv_detail = {
                "entry_odds": primary["entry_odds"],
                "closing_no_vig": primary["closing_fair_prob"],
                "closing_outcome": primary["closing_outcome"],
                "clv_raw": primary["clv_ev"],
                "clv_pct": primary["clv_pct"],
                "unit": "percent_ev_fraction",
                "entry_src": primary.get("entry_src"),
                "market": primary.get("market"),
            }

        # --- L2: Decision audit ---
        status_match = re.search(r"final_status:\s*(\S+)", text)
        fs = status_match.group(1) if status_match else str(report_manifest.get("final_status") or "unknown")
        actual_outcome = "home" if score_h > score_a else ("draw" if score_h == score_a else "away")
        p_adj_payload = report_manifest.get("p_adj") if isinstance(report_manifest.get("p_adj"), dict) else {}
        p_adj_actual = numeric_or_none(p_adj_payload.get(actual_outcome)) if p_adj_payload else None

        audit = []
        if fs == "watch":
            if actual_margin > 0:
                audit.append("L2: NO PLAY correct — model was competitive_fail, result does not change that")
            else:
                audit.append("L2: NO PLAY correct — match outcome consistent with watch status")
        elif fs in ("qualified_play", "lean"):
            audit.append("L2: PLAY decision — check CLV for edge validation")
        if clv is not None and clv < -0.02:
            audit.append("L2: CLV negative — entry price was worse than closing")
        elif clv is not None and clv > 0.02:
            audit.append("L2: CLV positive — entry price beat the closing market")
        audit.append(f"L2: final_status={fs}, result={result_str}, CLV={clv}")


        raw_match_id = report_field(text, "match_id", manifest_match_id(report_manifest))
        match_id_value = canonical_match_id_for_fixture(fm, raw_match_id, fixture_snap[0])
        window_value = report_field(text, "window", manifest_window(report_manifest))
        context_flags = extract_match_context_flags(fm, int(score_h), int(score_a))
        path_c_artifact, path_c_payload = artifact_payload_by_capability(report_manifest, report_manifest_path, "path_c_consistency")
        deep_research_artifact, deep_research_payload = artifact_payload_by_capability(report_manifest, report_manifest_path, "deep_research")
        scoreline_profile = scoreline_profile_from_path_c(path_c_payload, result_str)
        deep_research_findings = score_deep_research_findings(
            deep_research_payload.get("findings") if isinstance(deep_research_payload, dict) else None,
            actual_outcome,
            context_flags,
        )
        matchday_raw = None
        stage_raw = None
        if isinstance(report_manifest.get("match"), dict):
            matchday_raw = report_manifest["match"].get("matchday")
            stage_raw = report_manifest["match"].get("stage")
        matchday_raw = matchday_raw if matchday_raw is not None else fm.get("matchday")
        stage_raw = str(stage_raw or fm.get("stage") or "GROUP_STAGE")
        try:
            md_int = int(matchday_raw) if matchday_raw is not None else None
        except Exception:
            md_int = None
        if stage_raw.upper() != "GROUP_STAGE":
            reflection_phase = "knockout"
        elif md_int == 1:
            reflection_phase = "opener"
        elif md_int == 2:
            reflection_phase = "group_mid"
        elif md_int == 3:
            reflection_phase = "group_final"
        else:
            reflection_phase = "unknown"
        actual_total_goals = int(score_h) + int(score_a)
        actual_over25 = actual_total_goals > 2.5
        market_over25_implied = None
        for art in report_manifest.get("artifacts", []):
            if not isinstance(art, dict):
                continue
            if "totals" not in artifact_caps(art):
                continue
            totals_payload = load_artifact_payload(art, report_manifest_path)
            if isinstance(totals_payload, dict):
                for key in ("market_over25_implied", "no_vig_over", "over25_prob", "over_implied"):
                    if totals_payload.get(key) is not None:
                        try:
                            market_over25_implied = float(totals_payload.get(key))
                        except Exception:
                            market_over25_implied = None
                        break
            if market_over25_implied is not None:
                break
        favorite_side = "home" if close_odds_raw and home else None
        favorite_covered_main_handicap = None
        reflection_payload = report_manifest.get("reflection_layer") if isinstance(report_manifest.get("reflection_layer"), dict) else {}
        nop_payload = reflection_payload.get("no_play_classification") if isinstance(reflection_payload.get("no_play_classification"), dict) else None
        if isinstance(nop_payload, dict):
            nop_backfilled = NO_PLAY_CLASSIFIER.backfill_direction_hit(nop_payload, {"home_score": score_h, "away_score": score_a})
        else:
            nop_backfilled = None

        # --- Build Section 11 text ---
        graded_at = utc_now()
        brier_line = f"{brier:.4f}" if brier is not None else "N/A"
        ll_line = f"{ll:.4f}" if ll is not None else "N/A"
        section_11 = f"""
## 11. Post-Match Grading Slot

**Auto-graded:** {graded_at}

**Result:** {result_str}

**L1 — Model Score:**
- Brier: {brier_line}
- Uniform 3-way Brier baseline: {brier_baseline_uniform:.4f}
- Brier skill vs uniform (Brier - baseline; lower is better): {brier_skill_vs_uniform if brier_skill_vs_uniform is not None else "N/A"}
- Log-loss: {ll_line}

**CLV:"""
        if clv_detail:
            section_11 += f"""
- Primary market: {clv_detail.get('market')}
- Entry odds: {clv_detail['entry_odds']:.4f}
- Closing fair probability: {clv_detail['closing_no_vig']:.4f}
- CLV EV: {clv_detail['clv_pct']:+.2f}%
- Unit: percent EV = entry_odds × closing_fair_prob − 1
- Direction: {"market_moved_in_favor" if clv and clv > 0 else "market_moved_against_entry"}"""
            if clv_positions:
                section_11 += "\n- CLV positions: " + json.dumps(clv_positions, ensure_ascii=False)
        else:
            section_11 += "\n- N/A (no closing odds available)"

        section_11 += f"""
**L2 — Audit:"""
        for a in audit:
            section_11 += f"\n- {a}"

        section_11 += f"""
**Context Flags:** {json.dumps(context_flags, ensure_ascii=False) if context_flags else "[]"}

**Scoreline Profile:** {json.dumps(scoreline_profile, ensure_ascii=False)}

**Deep Research Finding Scores:** {json.dumps(deep_research_findings, ensure_ascii=False) if deep_research_findings else "[]"}

**Lesson:** (to be filled by owner)

**CLV_by_timing_class:** N/A (auto-postmatch)
"""  # noqa: E501


        content_core = {
            "schema_version": "wc26.grading_card.v1",
            "football_data_id": fm.get("id"),
            "home": home,
            "away": away,
            "result": result_str,
            "actual_margin": actual_margin,
            "actual_outcome": actual_outcome,
            "report_path": str(report_path),
            "report_mtime_utc": report_mtime.isoformat().replace("+00:00", "Z"),
            "manifest_path": str(report_manifest_path) if report_manifest_path else "",
            "match_id": match_id_value,
            "raw_match_id": raw_match_id,
            "window": window_value,
            "idempotency_key": f"{match_id_value}:{window_value}",
            "timing_class": report_field(text, "timing_class", str(report_manifest.get("timing_class") or "")),
            "source_quality_cap": report_field(text, "source_quality_cap", str(report_manifest.get("source_quality_cap") or report_manifest.get("source_quality") or "")),
            "final_status": fs,
            "phase": reflection_phase,
            "actual_total_goals": actual_total_goals,
            "actual_over25": actual_over25,
            "market_over25_implied": market_over25_implied,
            "favorite_side": favorite_side,
            "favorite_covered_main_handicap": favorite_covered_main_handicap,
            "no_play_type": nop_backfilled.get("type") if isinstance(nop_backfilled, dict) else None,
            "blocked_direction": nop_backfilled.get("direction_if_any") if isinstance(nop_backfilled, dict) else None,
            "post_result_direction_hit": nop_backfilled.get("post_result_direction_hit") if isinstance(nop_backfilled, dict) else None,
            "entry_price": entry_price_str,
            "model_probs": model_probs,
            "p_adj_actual_outcome": p_adj_actual,
            "brier": round(brier, 4) if brier is not None else None,
            "brier_baseline_uniform": round(brier_baseline_uniform, 4),
            "brier_skill_vs_uniform": brier_skill_vs_uniform,
            "log_loss": round(ll, 4) if ll is not None else None,
            "clv_raw": clv,
            "clv_ev": clv,
            "clv_pct": round(clv * 100.0, 2) if clv is not None else None,
            "clv_unit": "percent_ev_fraction",
            "clv_detail": clv_detail,
            "clv_positions": clv_positions,
            "audit": audit,
            "match_context_flags": context_flags,
            "scoreline_profile": scoreline_profile,
            "deep_research_finding_scores": deep_research_findings,
            "closing_odds_snapshot": str(odds_snap[0]) if odds_snap else "",
            "closing_snapshot_captured_at_utc": odds_snap[1].isoformat().replace("+00:00", "Z") if odds_snap else None,
            "closing_snapshot_age_at_kickoff_minutes": closing_snapshot_age_at_kickoff,
            "closing_quality": closing_quality,
            "fixture_snapshot": str(fixture_snap[0]),
        }
        card_id = "grade-" + stable_hash([match_id_value, window_value])
        hash_core = {key: value for key, value in content_core.items() if key not in {"report_mtime_utc"}}
        content_hash = stable_hash(hash_core)
        card = {
            **content_core,
            "card_id": card_id,
            "content_hash": content_hash,
            "graded_at_utc": graded_at,
        }
        card_changed = write_grading_card(card)
        removed_cards = remove_superseded_grading_cards(card)

        if isinstance(path_c_artifact, dict) and isinstance(path_c_payload, dict):
            ledger_entry = build_path_c_signal_ledger(
                path_c_artifact,
                path_c_payload,
                match_id_value,
                window_value,
                result_str,
                int(score_h),
                int(score_a),
                graded_at,
            )
            write_path_c_signal_ledger(ledger_entry)
            remove_superseded_path_c_ledgers(ledger_entry)

        # --- Write Section 11 to report ---
        new_text = text
        # If old Section 11 exists, replace it; otherwise append
        if "## 11. Post-Match Grading Slot" in text:
            new_text = re.sub(
                r"## 11\. Post-Match Grading Slot.*?(?=\n## |\Z)",
                section_11.strip(),
                text,
                flags=re.DOTALL,
            )
        else:
            new_text += "\n" + section_11
        
        if card_changed or "**Auto-graded:**" not in text:
            report_path.write_text(new_text, encoding="utf-8")
        if card_changed:
            graded += 1
            graded_cards.append(card)
            print(f"  [postmatch] {home} vs {away}: {result_str} — graded")

    if graded > 0:
        return emit(manifest(
            "wc26-postmatch-grade", "ok",
            matches_graded=graded,
            grading_cards_written=[str(grading_card_path(str(card["card_id"]))) for card in graded_cards],
            reports_seen=len(reports),
        ))
    return emit(manifest(
        "wc26-postmatch-grade", "ready_no_new_cards" if settled_seen else "ready_no_settled_cards",
        reports_seen=len(reports),
        settled_matches_seen=settled_seen,
        reason="settled matches already graded idempotently" if settled_seen else "no FINISHED matches found yet (pre-tournament)",
    ))


def daily_reflect() -> int:
    reports_dir = WORKSPACE / "reports" / "match"
    reports = sorted(reports_dir.glob("*.md")) if reports_dir.exists() else []
    manifests = report_manifest_summaries()
    now = current_time()
    today = now.date().isoformat()
    reports_today = [path for path in reports if file_mtime_utc_date(path) == today]
    manifests_today = [row for row in manifests if row.get("created_date_utc") == today]
    status_counts = Counter(str(row["final_status"]) for row in manifests)
    today_status_counts = Counter(str(row["final_status"]) for row in manifests_today)
    cap_counts = Counter(str(row["source_quality_cap"]) for row in manifests)
    upcoming = due_windows()
    missing_guarded = []
    for item in upcoming:
        found = latest_manifest_for(str(item["match_id"]), str(item["window"]))
        if not found or not is_guarded_direct_manifest(found[1]):
            missing_guarded.append(item)
    next_24h = [
        entry for entry in fixture_entries()
        if (hours := hours_to_kickoff(entry, now)) is not None and 0 <= hours <= 24
    ]
    grading_cards = load_grading_cards()
    grade_stats = grading_aggregates(grading_cards, today)
    path_a_today = path_a_stats(manifests, today)
    path_a_total = path_a_stats(manifests)
    gap_today = mechanism_gap_stats(manifests, today)
    gap_total = mechanism_gap_stats(manifests)
    proposals, proposals_today = proposal_summaries(today)

    print("# WC26 Daily Review Digest")
    print("")
    print(f"date_utc: {utc_now()}")
    print(f"today_utc: {today}")
    print(f"reports_seen: {len(reports)}")
    print(f"manifests_seen: {len(manifests)}")
    print(f"grading_cards_seen: {grade_stats['cards_total']}")
    print("mode: deterministic_no_agent")
    print("")
    print("## Source Freshness")
    print(latest_snapshot_status("fixtures", WORKSPACE / "snapshots" / "fixtures", ["football-data-wc-matches-latest.json", "football-data-wc-matches-*.json"]))
    print(latest_snapshot_status("odds broad", WORKSPACE / "snapshots" / "odds", ["the-odds-api-multibook-*.json"]))
    print(latest_snapshot_status("oddspapi AH", WORKSPACE / "snapshots" / "odds", ["oddspapi-*.json"]))
    print("")
    print("## Reports Today")
    print(f"- new_report_files: {len(reports_today)}")
    print(f"- new_manifests: {len(manifests_today)}")
    print(f"- today_final_status: {dict(sorted(today_status_counts.items()))}")
    print(f"- all_final_status: {dict(sorted(status_counts.items()))}")
    print(f"- source_quality_cap_all: {dict(sorted(cap_counts.items()))}")
    for row in manifests_today[:8]:
        print(f"  - {row['match_id']} | {row['window']} | status={row['final_status']} | cap={row['source_quality_cap']}")
    print("")
    print("## Window Watch")
    print(f"- due_now: {len(upcoming)}")
    print(f"- missing_guarded_report_now: {len(missing_guarded)}")
    for item in upcoming[:8]:
        print(f"  - {item['match_id']} {item['home']} vs {item['away']} | {item['window']} | T-{item['hours_to_kickoff']:.1f}h")
    for item in missing_guarded[:8]:
        print(f"  - BLOCKED missing guarded: {item['match_id']} {item['home']} vs {item['away']} | {item['window']}")
    print(f"- next_24h_matches: {len(next_24h)}")
    print("")
    print("## Postmatch Grades")
    print(f"- new_graded_today: {grade_stats['cards_today']}")
    print(f"- graded_total: {grade_stats['cards_total']}")
    for card in grade_stats["today_cards"][:8]:
        print(
            f"  - {card.get('match_id') or card.get('home')} {card.get('home')} vs {card.get('away')}: "
            f"result={card.get('result')} | status={card.get('final_status')} | "
            f"CLV={card.get('clv_raw', 'N/A')} | Brier={card.get('brier', 'N/A')}"
        )
    print("")
    print("## CLV / Calibration")
    print(f"- CLV mean: {grade_stats['clv_mean']} | n={grade_stats['clv_count']}")
    print(f"- Brier mean: {grade_stats['brier_mean']} | n={grade_stats['brier_count']}")
    print(f"- Log-loss mean: {grade_stats['log_loss_mean']} | n={grade_stats['log_loss_count']}")
    print(f"- CLV by window: {grade_stats['clv_by_window']}")
    print(f"- calibration buckets by p_adj: {grade_stats['calibration_buckets']}")
    print("")
    print("## Path A Arithmetic")
    print(f"- today: {path_a_today}")
    print(f"- all: {path_a_total}")
    print("")
    print("## Mechanism Gaps")
    print(f"- today: {gap_today}")
    print(f"- all: {gap_total}")
    print("")
    print("## Proposals")
    if proposals_today:
        print(f"- proposals_today: {len(proposals_today)}")
        for path in proposals_today[:5]:
            print(f"  - {path.name}")
    else:
        print("- no proposal today")
    print(f"- proposals_total: {len(proposals)}")
    print("")
    print("## Discipline")
    print("- advisory_only: true")
    print("- no_auto_bet: true")
    print("- qualified_play_requires_human_review: true")
    print("- daily_reflect_may_flag_bias: true")
    print("- daily_reflect_must_not_apply_parameters: true")
    print("- direct Telegram reports must use direct_summary.py / role_engine artifacts")
    return 0


def postmatch_notify() -> int:
    """Emit a Telegram-safe digest only for newly written grading cards."""
    state = load_state("postmatch-notify.json")
    sent = set(state.get("sent", [])) if isinstance(state.get("sent"), list) else set()
    new_items: list[dict[str, Any]] = []

    for card in load_grading_cards():
        key = f"{card.get('card_id')}|{card.get('content_hash')}"
        if not card.get("card_id") or key in sent:
            continue
        card["key"] = key
        new_items.append(card)

    if not new_items:
        return 0

    print("# WC26 Postmatch Grade Digest")
    print("")
    print(f"date_utc: {utc_now()}")
    print(f"new_graded_reports: {len(new_items)}")
    print("")
    for item in new_items[:12]:
        print(
            f"- {item.get('match_id') or item.get('card_id')}: "
            f"{item.get('home')} vs {item.get('away')} | result={item.get('result')} | "
            f"status={item.get('final_status')} | CLV={item.get('clv_raw', 'N/A')} | Brier={item.get('brier', 'N/A')}"
        )
        print(f"  report: {item.get('report_path')}")
        print(f"  card: {item.get('_path') or grading_card_path(str(item.get('card_id')))}")
    print("")
    print("Discipline: grade result is audit evidence, not a retroactive betting claim.")

    sent.update(item["key"] for item in new_items)
    save_state("postmatch-notify.json", {"sent": sorted(sent), "updated_at_utc": utc_now()})
    return 0


def match_window_direct() -> int:
    """Emit direct summaries for newly due T-72h/T-24h/T-60m/T-45m windows.

    The scanner is deterministic and no-agent. It does not fabricate reports.
    If a due window lacks a guarded manifest/report, it emits a one-time
    BLOCKED notice so the missing analysis cannot stay hidden.

    Late-window fixtures (T-60m, T-45m) are handled by odds_broad_scan's
    has_late_window_fixtures() auto-force-refresh — the cron collector
    fetches fresh odds every cycle when any match is 0.5-1.25h from KO.
    """
    state = load_state("match-window-direct.json")
    sent = state.get("sent") if isinstance(state.get("sent"), dict) else {}
    sent = sent if isinstance(sent, dict) else {}
    dry_run = os.environ.get("WC26_MATCH_WINDOW_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}
    max_messages = int(os.environ.get("WC26_MATCH_WINDOW_MAX_MESSAGES", "3"))
    due = due_windows()
    messages: list[str] = []

    for item in due:
        if len(messages) >= max_messages:
            break
        key = f"{item['match_id']}|{item['window']}|{item['kickoff_utc']}"
        prior = sent.get(key)
        found = latest_manifest_for(str(item["match_id"]), str(item["window"]))
        if found:
            manifest_path, payload = found
            report_path = manifest_report_path(payload)
            if prior == "summary_sent":
                continue
            try:
                summary = run_direct_summary(manifest_path, report_path)
            except Exception as exc:
                if prior == "summary_failed":
                    continue
                messages.append(
                    "\n".join([
                        f"BLOCKED — WC26 {item['match_id']} {item['home']} vs {item['away']} {item['window']} direct_summary failed",
                        f"manifest: {manifest_path}",
                        f"error: {str(exc)[:500]}",
                    ])
                )
                sent[key] = "summary_failed"
                continue
            messages.append(summary)
            sent[key] = "summary_sent"
            continue

        if prior == "blocked_no_guarded_report":
            continue
        messages.append(
            "\n".join([
                f"BLOCKED — WC26 {item['match_id']} {item['home']} vs {item['away']} entered {item['window']}",
                f"kickoff_utc: {item['kickoff_utc']} | hours_to_kickoff: {item['hours_to_kickoff']}",
                "reason: no guarded report/manifest for this exact window yet",
                "required: run WC26 direct analysis, generate role_engine + mechanism_audit, then relay direct_summary.py",
            ])
        )
        sent[key] = "blocked_no_guarded_report"

    if not messages:
        return 0

    print("\n\n---\n\n".join(messages))
    if not dry_run:
        save_state("match-window-direct.json", {"sent": sent, "updated_at_utc": utc_now()})
    return 0


def apply_approved_adjustment() -> int:
    return emit(
        manifest(
            "wc26-apply-approved-adjustment",
            "ready_no_approved_proposals",
            policy_versions_dir=str(WORKSPACE / "calibration" / "policy_versions"),
        )
    )


DISPATCH = {
    "wc26-source-health": source_health,
    "wc26-fixture-collect": fixture_collect,
    "wc26-odds-broad-scan": odds_broad_scan,
    "wc26-oddspapi-ah-snapshot": oddspapi_ah_snapshot,
    "wc26-postmatch-grade": postmatch_grade,
    "wc26-postmatch-notify": postmatch_notify,
    "wc26-daily-reflect": daily_reflect,
    "wc26-match-window-direct": match_window_direct,
    "wc26-apply-approved-adjustment": apply_approved_adjustment,
}


def main() -> int:
    load_env()
    stem = pathlib.Path(sys.argv[0]).stem
    job = os.environ.get("WC26_JOB", stem)
    func = DISPATCH.get(job)
    if not func:
        return emit(manifest("wc26-unknown", "fail", requested_job=job, exit_code=2))
    return func()


if __name__ == "__main__":
    raise SystemExit(main())
