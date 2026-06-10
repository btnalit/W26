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
from datetime import datetime, timezone
from typing import Any

import requests


WORKSPACE = pathlib.Path(os.environ.get("WC26_WORKSPACE", "/hermesdata/worldcup-2026-handicap"))
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
PYTHON = pathlib.Path(os.environ.get("WC26_PYTHON", str(WORKSPACE / ".venv" / "bin" / "python")))
ROOT_HERMES_HOME = os.environ.get("WC26_ROOT_HERMES_HOME", "/root/.hermes")
STATE_DIR = WORKSPACE / "state"
GRADING_DIR = WORKSPACE / "grading"
GRADING_CARDS_DIR = GRADING_DIR / "cards"


WINDOW_SPECS = [
    ("T-72h_early", "early_structural", 60.0, 84.0),
    ("T-24h_confirm", "confirmation", 18.0, 30.0),
    ("T-60m_lineup_final", "lineup_final", 0.75, 1.25),
]


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
    try:
        response = requests.get(
            "https://api.football-data.org/v4/competitions/WC/matches",
            headers={"X-Auth-Token": token},
            timeout=30,
        )
    except requests.RequestException as exc:
        return emit(manifest("wc26-fixture-collect", "fail", error=str(exc)[:240], exit_code=1))
    payload = manifest(
        "wc26-fixture-collect",
        "ok" if response.status_code == 200 else "fail",
        http_status=response.status_code,
        minute_remaining=response.headers.get("X-Requests-Available-Minute"),
    )
    if response.status_code == 200:
        data = response.json()
        path = WORKSPACE / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json"
        write_json(path, {"captured_at_utc": utc_now(), "source": "football-data.org", "data": data})
        payload.update(match_count=len(data.get("matches", [])), snapshot_path=str(path))
    else:
        payload.update(error=response.text[:240], exit_code=1)
    return emit(payload)


def odds_broad_scan() -> int:
    if not force_refresh_requested():
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
      2. Fetch closing odds from latest the-odds-api snapshot
      3. Read report entry prices from report frontmatter
      4. Compute CLV: (entry_no_vig - closing_no_vig) / closing_no_vig
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

    # --- Load odds snapshot for closing prices ---
    odds_snap = latest_snapshot(
        WORKSPACE / "snapshots" / "odds",
        ["the-odds-api-*.json"],
    )
    closing_odds = {}
    if odds_snap:
        odds_data = json.loads(odds_snap[0].read_text())
        odds_list = odds_data if isinstance(odds_data, list) else odds_data.get("data", [])
        for om in odds_list:
            key = f"{om.get('home_team','')}_{om.get('away_team','')}".lower().replace(" ","")
            closing_odds[key] = om

    # --- Load all reports ---
    reports_dir = WORKSPACE / "reports" / "match"
    reports = sorted(reports_dir.glob("*.md")) if reports_dir.exists() else []
    print(f"[postmatch] {len(reports)} reports, {len(all_matches)} fixture records")

    graded = 0
    graded_cards: list[dict[str, Any]] = []
    for fm in all_matches:
        status = fm.get("status", fm.get("stage", ""))
        if status not in ("FINISHED", "AWARDED"):
            continue

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

        # Find matching report
        report_path = None
        for rp in reports:
            text = rp.read_text(encoding="utf-8", errors="replace")
            if home in text and away in text:
                report_path = rp
                break
        if not report_path:
            continue

        text = report_path.read_text(encoding="utf-8", errors="replace")
        report_mtime = datetime.fromtimestamp(report_path.stat().st_mtime, tz=timezone.utc)
        report_manifest: dict[str, Any] = {}
        report_manifest_path = resolve_path(report_field(text, "artifact_manifest_path", ""))
        if report_manifest_path and report_manifest_path.exists():
            loaded_manifest = read_json(report_manifest_path, {})
            if isinstance(loaded_manifest, dict):
                report_manifest = loaded_manifest

        # --- Parse entry prices from report frontmatter ---
        entry_match = re.search(r"entry_price:\s*(.+)", text)
        entry_price_str = entry_match.group(1).strip() if entry_match else "N/A"

        # Parse p_model from Market Board
        p_model_match = re.findall(
            r"\| 1X2 [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \| ([\d.]+) \| info",
            text
        )
        model_probs = [float(x) for x in p_model_match[:3]] if len(p_model_match) >= 3 else None

        # --- L1: Model Brier ---
        brier = None
        ll = None
        if model_probs and len(model_probs) == 3:
            brier = sum((model_probs[i] - actual_vec[i])**2 for i in range(3))
            eps = 1e-15
            clipped = [max(min(p, 1-eps), eps) for p in model_probs]
            ll = -sum(actual_vec[i] * math.log(clipped[i]) for i in range(3))

        # --- CLV from closing odds ---
        clv = None
        clv_detail = {}
        close_odds = closing_odds.get(match_key, {})
        if close_odds:
            # Find Pinnacle closing h2h
            for bk in close_odds.get("bookmakers", []):
                if bk.get("key") == "pinnacle":
                    for mk in bk.get("markets", []):
                        if mk.get("key") in ("h2h", "1x2"):
                            outcomes = mk.get("outcomes", [])
                            if len(outcomes) >= 3:
                                close_prices = [o.get("price", 0) for o in outcomes]
                                if all(p > 0 for p in close_prices):
                                    # Compute closing no-vig (shin)
                                    close_nv = DEVIG.devig_shin(close_prices)
                                    # Compare with market board entry no-vig
                                    entry_market = re.search(
                                        r"\| 1X2 H [^|]+ \| [^|]+ \| decimal \| ([\d.]+) [^|]+ [^|]+ [^|]+ ([\d.]+)",
                                        text
                                    )
                                    if entry_market:
                                        entry_odds = float(entry_market.group(1))
                                        entry_nv = float(entry_market.group(2))
                                        clv = round(entry_nv - close_nv[0], 4)
                                        clv_detail = {
                                            "entry_no_vig": entry_nv,
                                            "closing_no_vig": close_nv[0],
                                            "clv_raw": clv,
                                        }
                                    break

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
- Log-loss: {ll_line}

**CLV:"""
        if clv_detail:
            section_11 += f"""
- Entry no-vig: {clv_detail['entry_no_vig']:.4f}
- Closing no-vig: {clv_detail['closing_no_vig']:.4f}
- CLV raw: {clv_detail['clv_raw']:+.4f}
- Direction: {"market_moved_in_favor" if clv and clv > 0 else "market_moved_against_entry"}"""
        else:
            section_11 += "\n- N/A (no closing odds available)"

        section_11 += f"""
**L2 — Audit:"""
        for a in audit:
            section_11 += f"\n- {a}"

        section_11 += f"""
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
            "match_id": report_field(text, "match_id", manifest_match_id(report_manifest)),
            "window": report_field(text, "window", manifest_window(report_manifest)),
            "timing_class": report_field(text, "timing_class", str(report_manifest.get("timing_class") or "")),
            "source_quality_cap": report_field(text, "source_quality_cap", str(report_manifest.get("source_quality_cap") or report_manifest.get("source_quality") or "")),
            "final_status": fs,
            "entry_price": entry_price_str,
            "model_probs": model_probs,
            "p_adj_actual_outcome": p_adj_actual,
            "brier": round(brier, 4) if brier is not None else None,
            "log_loss": round(ll, 4) if ll is not None else None,
            "clv_raw": clv,
            "clv_detail": clv_detail,
            "audit": audit,
            "closing_odds_snapshot": str(odds_snap[0]) if odds_snap else "",
            "fixture_snapshot": str(fixture_snap[0]),
        }
        card_id = "grade-" + stable_hash([fm.get("id"), str(report_path)])
        content_hash = stable_hash(content_core)
        card = {
            **content_core,
            "card_id": card_id,
            "content_hash": content_hash,
            "graded_at_utc": graded_at,
        }
        card_changed = write_grading_card(card)

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
        "wc26-postmatch-grade", "ready_no_settled_cards",
        reports_seen=len(reports),
        reason="no FINISHED matches found yet (pre-tournament)",
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
    """Emit direct summaries for newly due T-72h/T-24h/T-60m windows.

    The scanner is deterministic and no-agent. It does not fabricate reports.
    If a due window lacks a guarded manifest/report, it emits a one-time
    BLOCKED notice so the missing analysis cannot stay hidden.
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
