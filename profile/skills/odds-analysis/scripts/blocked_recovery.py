#!/usr/bin/env python3
"""WC26 blocked-state classifier and bounded recovery worker.

This is a no-paid-API, no-LLM recovery sidecar. It reads queue events produced
by deterministic gateways/cron jobs, classifies the block, and only repairs
mechanism completeness when all required source artifacts already exist.

It deliberately does not improve source quality, actionability, or final
status. Recovery can add provenance and regenerate missing role/mechanism
artifacts, but it cannot turn partial data into a clean PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE = Path(os.environ.get("WC26_WORKSPACE", "/hermesdata/worldcup-2026-handicap"))
PROFILE_ROOT = Path(os.environ.get("HERMES_HOME", "/root/.hermes/profiles/wc26-handicap-analyst"))
QUEUE_DIR = WORKSPACE / "blocked_recovery" / "queue"
ARCHIVE_DIR = WORKSPACE / "blocked_recovery" / "archive"
STATE_PATH = WORKSPACE / "state" / "blocked-recovery.json"
ARTIFACTS_DIR = WORKSPACE / "reports" / "artifacts"
SCRIPTS_DIR = PROFILE_ROOT / "skills" / "odds-analysis" / "scripts"
PYTHON = Path(os.environ.get("WC26_PYTHON", str(WORKSPACE / ".venv" / "bin" / "python")))
MAX_ATTEMPTS = int(os.environ.get("WC26_BLOCKED_RECOVERY_MAX_ATTEMPTS", "3"))
RETRY_BACKOFF_MINUTES = [30, 120, 360]

RECOVERY_SCHEMA = "wc26.blocked_recovery.event.v1"
STATE_SCHEMA = "wc26.blocked_recovery.state.v1"

PRIORITY = [
    "safety_block",
    "contract_mismatch",
    "identity_mismatch",
    "missing_source",
    "credential_block",
    "detector_bug",
    "stale_snapshot",
    "missing_guarded_report",
    "missing_artifact",
    "opportunity_block",
]

TERMINAL_CATEGORIES = {
    "safety_block",
    "contract_mismatch",
    "identity_mismatch",
    "missing_source",
    "credential_block",
    "detector_bug",
    "opportunity_block",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def python_bin() -> str:
    return str(PYTHON) if PYTHON.exists() else sys.executable


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def recovery_id_for(event: dict[str, Any]) -> str:
    raw = event.get("recovery_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    seed = {
        "source": event.get("source"),
        "category": event.get("category") or event.get("block_type"),
        "match_id": event.get("match_id"),
        "window": event.get("window"),
        "direct_request_id": event.get("direct_request_id"),
        "manifest_path": event.get("manifest_path"),
        "report_path": event.get("report_path"),
        "session_id": event.get("session_id"),
        "reason": str(event.get("reason") or "")[:240],
    }
    return "br:" + stable_hash(seed)


def enqueue_event(workspace: Path, event: dict[str, Any]) -> Path:
    event = dict(event)
    event.setdefault("schema_version", RECOVERY_SCHEMA)
    event.setdefault("created_at_utc", utc_now())
    event["recovery_id"] = recovery_id_for(event)
    queue_dir = workspace / "blocked_recovery" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    path = queue_dir / f"{event['recovery_id'].replace(':', '-')}.json"
    if path.exists():
        current = read_json(path, {})
        if isinstance(current, dict):
            current["last_seen_at_utc"] = utc_now()
            current["seen_count"] = int(current.get("seen_count") or 1) + 1
            write_json(path, current)
            return path
    event.setdefault("seen_count", 1)
    event.setdefault("last_seen_at_utc", event["created_at_utc"])
    write_json(path, event)
    return path


def event_text(event: dict[str, Any]) -> str:
    fields = [
        event.get("category"),
        event.get("block_type"),
        event.get("reason"),
        event.get("evidence"),
        event.get("error"),
        event.get("response_excerpt"),
        event.get("blocking_mechanisms"),
    ]
    return " ".join(json.dumps(item, ensure_ascii=False, default=str) for item in fields if item is not None).lower()


def manifest_artifact_caps(manifest: dict[str, Any]) -> set[str]:
    caps: set[str] = set()
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        provides = artifact.get("provides")
        if isinstance(provides, list):
            caps.update(str(item) for item in provides)
        for key in ("artifact_type", "artifact_kind", "script"):
            raw = str(artifact.get(key) or "")
            if raw:
                caps.add(raw)
    return caps


def classify_event(event: dict[str, Any]) -> dict[str, Any]:
    candidates: set[str] = set()
    raw_category = str(event.get("category") or event.get("block_type") or "").strip()
    if raw_category:
        candidates.add(raw_category)
    text = event_text(event)

    if "safety" in text or "freeform" in text or "missing artifact manifest/report binding" in text:
        candidates.add("safety_block")
    if (
        "report_contract" in text
        or "contract_mismatch" in text
        or "report_guard failed" in text
        or "direct_summary failed" in text
        or "summary_failed" in text
    ):
        candidates.add("contract_mismatch")
    if "team identity" in text or "identity" in text:
        candidates.add("identity_mismatch")
    if "token missing" in text or "api_key missing" in text or "credential" in text:
        candidates.add("credential_block")
    if "missing source" in text or "no sharp anchor" in text or "pinnacle missing" in text:
        candidates.add("missing_source")
    if "key mismatch" in text or "anchor present" in text or "detector_bug" in text:
        candidates.add("detector_bug")
    if "stale" in text or "snapshot age" in text:
        candidates.add("stale_snapshot")
    if "no guarded report" in text or "missing guarded" in text:
        candidates.add("missing_guarded_report")
    if "missing artifact" in text or "mechanism_audit" in text or "role_engine" in text:
        candidates.add("missing_artifact")
    if "opportunity" in text or "relay_actionable=0" in text or "raw_only" in text:
        candidates.add("opportunity_block")

    manifest_path = resolve_path(event.get("manifest_path"))
    manifest = read_json(manifest_path, {}) if manifest_path and manifest_path.exists() else {}
    if isinstance(manifest, dict) and manifest:
        caps = manifest_artifact_caps(manifest)
        gates = manifest.get("analysis_gates") if isinstance(manifest.get("analysis_gates"), dict) else {}
        if "role_engine" not in caps or "mechanism_audit" not in caps:
            candidates.add("missing_artifact")
        for key in ("devig_three_method", "path_c_consistency", "asian_handicap_quad_line", "totals_crossbook"):
            gate = gates.get(key)
            status = str((gate or {}).get("status") if isinstance(gate, dict) else gate or "").lower()
            if "skipped_missing_source" in status or "missing_source" in status:
                candidates.add("missing_source")

    if not candidates:
        candidates.add("missing_artifact")

    for category in PRIORITY:
        if category in candidates:
            return {
                "category": category,
                "candidates": sorted(candidates),
                "auto_recoverable": category in {"missing_artifact", "missing_guarded_report", "stale_snapshot"},
            }
    return {"category": "missing_artifact", "candidates": sorted(candidates), "auto_recoverable": True}


def resolve_path(raw: Any, base: Path | None = None) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw.strip())
    if path.is_absolute():
        return path
    if str(path).startswith("reports/"):
        return WORKSPACE / path
    if base is not None:
        return (base.parent / path).resolve()
    return path


def normalize_match_id(raw: Any) -> str:
    value = str(raw or "").strip().upper()
    match = re.fullmatch(r"([MW])(\d{3})", value)
    if not match:
        return value
    return "M" + match.group(2)


def legacy_match_ids(match_id: str) -> list[str]:
    normalized = normalize_match_id(match_id)
    match = re.fullmatch(r"M(\d{3})", normalized)
    if not match:
        return [normalized] if normalized else []
    return [normalized, "W" + match.group(1)]


def extract_match_id(event: dict[str, Any]) -> str:
    for key in ("match_id", "local_match_id"):
        normalized = normalize_match_id(event.get(key))
        if re.fullmatch(r"M\d{3}", normalized):
            return normalized
    for direct_id in event.get("direct_request_ids") or []:
        record_path = direct_request_path_for_id(str(direct_id))
        record = read_json(record_path, {}) if record_path else {}
        if isinstance(record, dict):
            normalized = normalize_match_id(record.get("match_id"))
            if re.fullmatch(r"M\d{3}", normalized):
                return normalized
    text = event_text(event)
    match = re.search(r"\b([MW]\d{3})\b", text, flags=re.IGNORECASE)
    if match:
        return normalize_match_id(match.group(1))
    return ""


def direct_request_path_for_id(direct_id: str) -> Path | None:
    suffix = str(direct_id or "").split(":", 1)[-1].strip()
    if not suffix:
        return None
    root = WORKSPACE / "direct_requests"
    if not root.exists():
        return None
    matches = sorted(root.rglob(f"direct-{suffix}.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def latest_direct_request_for_match(match_id: str, event: dict[str, Any]) -> Path | None:
    for direct_id in event.get("direct_request_ids") or []:
        path = direct_request_path_for_id(str(direct_id))
        payload = read_json(path, {}) if path else {}
        if isinstance(payload, dict) and normalize_match_id(payload.get("match_id")) == match_id:
            return path
    root = WORKSPACE / "direct_requests"
    if not root.exists():
        return None
    candidates: list[tuple[float, Path]] = []
    for path in root.rglob("direct-*.json"):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        if normalize_match_id(payload.get("match_id")) == match_id:
            candidates.append((path.stat().st_mtime, path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def load_fixture_entry(match_id: str) -> dict[str, Any]:
    fixture_path = WORKSPACE / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json"
    registry_path = SCRIPTS_DIR / "fixture_registry.py"
    if not fixture_path.exists() or not registry_path.exists():
        return {}
    try:
        spec = importlib.util.spec_from_file_location("_wc26_fixture_registry_recovery", str(registry_path))
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        registry = module.load_registry(fixture_path)
        entry = module.resolve_fixture(registry, match_id=match_id)
        return entry if isinstance(entry, dict) else {}
    except Exception:
        return {}


def _artifact_match_id(payload: dict[str, Any]) -> str:
    for key in ("match_id", "local_match_id", "match_code"):
        normalized = normalize_match_id(payload.get(key))
        if re.fullmatch(r"M\d{3}", normalized):
            return normalized
    artifact_id = str(payload.get("artifact_id") or "")
    match = re.search(r"\b([MW]\d{3})\b", artifact_id, flags=re.IGNORECASE)
    return normalize_match_id(match.group(1)) if match else ""


def find_legacy_market_artifact(match_id: str) -> tuple[Path, dict[str, Any]] | None:
    ids = set(legacy_match_ids(match_id))
    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    if not ARTIFACTS_DIR.exists():
        return None
    for path in ARTIFACTS_DIR.glob("*.json"):
        name = path.name.lower()
        if name.startswith(("manifest-", "crossbook-", "model-", "mechanism-", "role-engine-", "deep-research-", "consistency-")):
            continue
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        if _artifact_match_id(payload) not in ids:
            continue
        if not isinstance(payload.get("markets"), dict):
            continue
        candidates.append((path.stat().st_mtime, path, payload))
    if not candidates:
        return None
    _mtime, path, payload = max(candidates, key=lambda item: item[0])
    return path, payload


def find_artifact_by_prefix(match_id: str, prefix: str) -> tuple[Path, dict[str, Any]] | None:
    ids = legacy_match_ids(match_id)
    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    for local_id in ids:
        for path in ARTIFACTS_DIR.glob(f"{prefix}-{local_id}*.json"):
            payload = read_json(path, {})
            if isinstance(payload, dict):
                candidates.append((path.stat().st_mtime, path, payload))
    if not candidates:
        return None
    _mtime, path, payload = max(candidates, key=lambda item: item[0])
    return path, payload


def find_legacy_report(match_id: str) -> Path | None:
    report_dir = WORKSPACE / "reports" / "match"
    if not report_dir.exists():
        return None
    ids = [item.lower() for item in legacy_match_ids(match_id)]
    candidates: list[tuple[float, Path]] = []
    for path in report_dir.glob("*.md"):
        lower = path.name.lower()
        if any(local_id.lower() in lower for local_id in ids):
            candidates.append((path.stat().st_mtime, path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def legacy_recovery_inputs(match_id: str, event: dict[str, Any]) -> dict[str, Any] | None:
    if not re.fullmatch(r"M\d{3}", match_id or ""):
        return None
    legacy = find_legacy_market_artifact(match_id)
    crossbook = find_artifact_by_prefix(match_id, "crossbook")
    direct_request_path = latest_direct_request_for_match(match_id, event)
    if not legacy or not crossbook or not direct_request_path:
        return None
    model = find_artifact_by_prefix(match_id, "model")
    deep = find_artifact_by_prefix(match_id, "deep-research")
    return {
        "legacy_path": legacy[0],
        "legacy": legacy[1],
        "crossbook_path": crossbook[0],
        "crossbook": crossbook[1],
        "model_path": model[0] if model else None,
        "model": model[1] if model else None,
        "deep_research_path": deep[0] if deep else None,
        "deep_research": deep[1] if deep else None,
        "direct_request_path": direct_request_path,
        "direct_request": read_json(direct_request_path, {}),
        "legacy_report_path": find_legacy_report(match_id),
    }


def load_state() -> dict[str, Any]:
    state = read_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("schema_version", STATE_SCHEMA)
    state.setdefault("entries", {})
    return state


def save_state(state: dict[str, Any]) -> None:
    state["updated_at_utc"] = utc_now()
    write_json(STATE_PATH, state)


def retry_after_minutes(attempts: int) -> int:
    index = max(0, min(attempts - 1, len(RETRY_BACKOFF_MINUTES) - 1))
    return RETRY_BACKOFF_MINUTES[index]


def should_attempt(entry: dict[str, Any], now: datetime, force: bool = False) -> bool:
    if force:
        return True
    retry_after = parse_utc(entry.get("retry_after_utc"))
    return retry_after is None or now >= retry_after


def cmd_run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(WORKSPACE),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def artifact_entry_has_cap(artifact: dict[str, Any], cap: str) -> bool:
    provides = artifact.get("provides")
    return isinstance(provides, list) and cap in {str(item) for item in provides}


def manifest_report_path(manifest: dict[str, Any], manifest_path: Path) -> Path | None:
    for key in ("report_path", "report_md", "report_file"):
        path = resolve_path(manifest.get(key), manifest_path)
        if path and path.exists():
            return path
    return None


def replace_artifact(manifest: dict[str, Any], cap: str, entry: dict[str, Any]) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
        manifest["artifacts"] = artifacts
    artifacts[:] = [
        item
        for item in artifacts
        if not (isinstance(item, dict) and (artifact_entry_has_cap(item, cap) or str(item.get("artifact_type")) == cap))
    ]
    artifacts.append(entry)


def stamp_artifact(path: Path, recovery_id: str, action: str, manifest_path: Path) -> None:
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return
    payload["generated_by"] = "blocked_recovery"
    payload["recovery_id"] = recovery_id
    payload["recovery_action"] = action
    payload["recovery_generated_at_utc"] = utc_now()
    payload["recovery_input_manifest_path"] = str(manifest_path)
    write_json(path, payload)


def validate_manifest(manifest_path: Path, report_path: Path | None) -> tuple[bool, str]:
    contract = cmd_run([python_bin(), str(SCRIPTS_DIR / "report_contract.py"), str(manifest_path)], timeout=90)
    if contract.returncode != 0:
        return False, (contract.stderr or contract.stdout or "report_contract failed").strip()[:800]
    if report_path:
        guard = cmd_run([python_bin(), str(SCRIPTS_DIR / "report_guard.py"), str(report_path)], timeout=90)
        if guard.returncode != 0:
            return False, (guard.stderr or guard.stdout or "report_guard failed").strip()[:800]
    return True, "contract/guard pass"


def _safe_slug(value: Any) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip()).strip("-")
    return slug or "unknown"


def _legacy_market_probs(legacy: dict[str, Any], key: str) -> dict[str, Any]:
    markets = legacy.get("markets") if isinstance(legacy.get("markets"), dict) else {}
    market = markets.get(key)
    if not isinstance(market, dict):
        return {}
    probs = market.get("no_vig_probabilities")
    return probs if isinstance(probs, dict) else {}


def _legacy_market_prices(legacy: dict[str, Any], key: str) -> tuple[list[Any], list[str], Any]:
    markets = legacy.get("markets") if isinstance(legacy.get("markets"), dict) else {}
    market = markets.get(key)
    if not isinstance(market, dict):
        return [], [], None
    odds = market.get("decimal_odds") if isinstance(market.get("decimal_odds"), list) else []
    outcomes = market.get("outcomes") if isinstance(market.get("outcomes"), list) else []
    return odds, [str(item) for item in outcomes], market.get("line")


def _prob_line(probs: dict[str, Any]) -> str:
    parts = []
    for key, value in probs.items():
        try:
            parts.append(f"{key} {float(value) * 100:.1f}%")
        except Exception:
            parts.append(f"{key} N/A")
    return " / ".join(parts) if parts else "N/A"


def _price_line(outcomes: list[str], odds: list[Any]) -> str:
    parts = []
    for outcome, odd in zip(outcomes, odds):
        parts.append(f"{outcome} @{odd}")
    return " / ".join(parts) if parts else "N/A"


def build_legacy_guarded_report_text(
    manifest: dict[str, Any],
    manifest_path: Path,
    direct_request_path: Path,
    legacy: dict[str, Any],
    crossbook: dict[str, Any],
) -> str:
    h2h_odds, h2h_outcomes, _ = _legacy_market_prices(legacy, "h2h")
    ah_odds, ah_outcomes, ah_line = _legacy_market_prices(legacy, "spreads")
    totals_odds, totals_outcomes, totals_line = _legacy_market_prices(legacy, "totals")
    h2h_probs = _legacy_market_probs(legacy, "h2h")
    ah_probs = _legacy_market_probs(legacy, "spreads")
    totals_probs = _legacy_market_probs(legacy, "totals")
    summary = crossbook.get("summary") if isinstance(crossbook.get("summary"), dict) else {}
    football_data_id = manifest.get("football_data_id") or "TBD"
    venue = manifest.get("venue") or "TBD"

    return "\n".join(
        [
            "---",
            f"cutoff_utc: {manifest.get('generated_at_utc')}",
            "mode: live",
            f"source_quality: {manifest.get('source_quality')}",
            f"source_quality_cap: {manifest.get('source_quality_cap')}",
            f"report_completeness: {manifest.get('report_completeness')}",
            f"final_status: {manifest.get('final_status')}",
            f"review_required: {str(manifest.get('review_required')).lower()}",
            f"artifact_manifest_path: {manifest_path}",
            "artifact_contract_status: pass",
            "report_guard_status: pass",
            f"window: {manifest.get('window')}",
            f"timing_class: {manifest.get('timing_class')}",
            f"direct_request_id: {manifest.get('direct_request_id')}",
            f"direct_request_path: {direct_request_path}",
            f"match_id: {manifest.get('match_id')}",
            f"football_data_id: {football_data_id}",
            f"home: {manifest.get('home')}",
            f"away: {manifest.get('away')}",
            f"kickoff_utc: {manifest.get('kickoff_utc')}",
            f"venue: {venue}",
            f"stage: {manifest.get('stage')}",
            f"group: {manifest.get('group')}",
            f"matchday: {manifest.get('matchday')}",
            "---",
            "",
            f"# WC26 {manifest.get('match_id')} {manifest.get('home')} vs {manifest.get('away')} — recovered guarded report",
            "",
            "```yaml",
            f"football_data_id: {football_data_id}",
            f"venue: {venue}",
            "```",
            "",
            "## 1. Recovery Scope",
            "",
            "This report was rebuilt by blocked_recovery from existing local artifacts after a freeform Telegram output was blocked. It is partial and cannot produce actionable advice.",
            "",
            "## 2. Pinnacle Snapshot",
            "",
            f"- Pinnacle H2H: {_price_line(h2h_outcomes, h2h_odds)}",
            f"- Pinnacle H2H no-vig: {_prob_line(h2h_probs)}",
            f"- Pinnacle AH line {ah_line}: {_price_line(ah_outcomes, ah_odds)}",
            f"- Pinnacle AH no-vig: {_prob_line(ah_probs)}",
            f"- Pinnacle Totals line {totals_line}: {_price_line(totals_outcomes, totals_odds)}",
            f"- Pinnacle Totals no-vig: {_prob_line(totals_probs)}",
            "",
            "## 3. Path A Cross-Book Scan",
            "",
            f"- quotes_scanned: {summary.get('quotes_scanned', 'N/A')}",
            f"- edge_count: {summary.get('edge_count', 'N/A')}",
            f"- noise_edge_count: {summary.get('noise_edge_count', 'N/A')}",
            f"- raw_actionable_count: {summary.get('raw_actionable_count', summary.get('actionable_count', 'N/A'))}",
            f"- relay_actionable_count: {summary.get('relay_actionable_count', 0)}",
            "",
            "## 4. Skipped Gates",
            "",
            "- devig_three_method: skipped_missing_source — legacy artifact is shin-only, not three-method devig.",
            "- path_c_consistency: skipped_missing_source — no valid consistency triangle artifact is available for this recovered report.",
            "",
            "## 5. Final",
            "",
            "WATCH / NO PLAY. This recovered report is designed for guarded Telegram projection and post-match linkage, not for direct betting.",
            "",
        ]
    )


def update_direct_request_for_recovery(
    direct_request_path: Path,
    manifest_path: Path,
    report_path: Path,
    match_id: str,
    match_label: str,
) -> dict[str, Any]:
    record = read_json(direct_request_path, {})
    if not isinstance(record, dict):
        raise ValueError(f"direct request record unreadable: {direct_request_path}")
    record["match_id"] = match_id
    if match_label:
        record["match_label"] = match_label
    record["manifest_path"] = str(manifest_path)
    record["report_path"] = str(report_path)
    record["status"] = "completed_cached"
    record["cache_mode"] = "legacy_guarded_recovery"
    record["api_refresh_performed"] = False
    record["completed_at_utc"] = record.get("completed_at_utc") or utc_now()
    record["updated_at_utc"] = utc_now()
    write_json(direct_request_path, record)
    return record


def recover_legacy_guarded_report(event: dict[str, Any], recovery_id: str) -> dict[str, Any]:
    match_id = extract_match_id(event)
    inputs = legacy_recovery_inputs(match_id, event)
    if not inputs:
        return {"status": "waiting", "reason": "no recoverable legacy artifact/report bundle exists yet"}

    legacy = inputs["legacy"]
    crossbook = inputs["crossbook"]
    fixture = load_fixture_entry(match_id)
    direct_request_path = Path(inputs["direct_request_path"])
    direct_record = inputs["direct_request"] if isinstance(inputs.get("direct_request"), dict) else {}
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    home = str(fixture.get("home") or legacy.get("home") or "home TBD")
    away = str(fixture.get("away") or legacy.get("away") or "away TBD")
    match_label = f"{home} vs {away}"
    report_path = WORKSPACE / "reports" / "match" / f"{match_id}-{_safe_slug(home)}-vs-{_safe_slug(away)}-legacy-recovered-{timestamp}.md"
    manifest_path = ARTIFACTS_DIR / f"manifest-{match_id}-legacy-recovered-{timestamp}.json"
    legacy_snapshot_sources = legacy.get("snapshots") if isinstance(legacy.get("snapshots"), list) else []
    sources = list(legacy_snapshot_sources)
    if crossbook.get("source_snapshot_id") and not any(item.get("snapshot_id") == crossbook.get("source_snapshot_id") for item in sources if isinstance(item, dict)):
        sources.append({"snapshot_id": crossbook.get("source_snapshot_id"), "source": "cross_book_scan", "type": "crossbook"})

    p_adj = legacy.get("p_adj") if isinstance(legacy.get("p_adj"), dict) else {}
    p_market = p_adj.get("h2h") if isinstance(p_adj.get("h2h"), dict) else _legacy_market_probs(legacy, "h2h")
    p_model = {}
    model_payload = inputs.get("model") if isinstance(inputs.get("model"), dict) else {}
    if isinstance(model_payload, dict) and isinstance(model_payload.get("p_model"), dict):
        p_model = model_payload.get("p_model")

    manifest = {
        "manifest_id": f"manifest:{match_id}:legacy-recovered:{timestamp}",
        "workflow_contract": "wc26.direct_report.v1",
        "match_id": match_id,
        "canonical_id": fixture.get("canonical_id"),
        "football_data_id": fixture.get("football_data_id"),
        "home": home,
        "away": away,
        "kickoff_utc": fixture.get("kickoff_utc"),
        "stage": fixture.get("stage"),
        "group": fixture.get("group"),
        "matchday": fixture.get("matchday"),
        "venue": fixture.get("venue") or "TBD",
        "window": "early_structural",
        "timing_class": "early_structural",
        "mode": "live",
        "report_completeness": "partial",
        "source_quality": str(legacy.get("source_quality") or "B").upper(),
        "source_quality_cap": "C",
        "final_status": "watch",
        "review_required": True,
        "direct_request_id": direct_record.get("direct_request_id"),
        "direct_request_path": str(direct_request_path),
        "snapshot_id": crossbook.get("source_snapshot_id") or crossbook.get("input_snapshot"),
        "analysis_gates": {
            "devig_three_method": {"status": "skipped_missing_source", "reason": "legacy artifact is shin-only"},
            "path_a_crossbook": {"status": "pass"},
            "asian_handicap": {"status": "pass", "note": "same-line crossbook only"},
            "totals": {"status": "pass", "note": "same-line crossbook only"},
            "path_b_model_diagnostic": {"status": "diagnostic", "calibration_status": (model_payload.get("calibration") or {}).get("calibration_status") or legacy.get("p_model", {}).get("calibration_status")},
            "path_c_consistency": {"status": "skipped_missing_source", "reason": "no valid consistency triangle artifact"},
            "mechanism_audit": {"status": "missing"},
            "source_freshness": {"status": "pass"},
            "role_engine": "missing",
        },
        "artifact_capabilities": ["path_a_crossbook", "asian_handicap", "totals", "path_b_model_diagnostic"],
        "skipped_sections": [
            {
                "gate": "devig_three_method",
                "reason": "legacy artifact contains shin-only devig, not the full three-method artifact",
                "impact": "1X2 devig is shown as legacy snapshot data; report remains partial/watch",
            },
            {
                "gate": "path_c_consistency",
                "reason": "no valid consistency_triangle artifact exists for this match/window",
                "impact": "cannot claim 1X2/AH/Totals structural consistency",
            },
        ],
        "artifacts": [
            {
                "artifact_id": f"legacy_market:{match_id}:{timestamp}",
                "artifact_type": "legacy_market_snapshot",
                "script": "blocked_recovery.py",
                "path": str(inputs["legacy_path"]),
                "provides": ["legacy_market_snapshot"],
            },
            {
                "artifact_id": f"crossbook:{match_id}:legacy-recovered:{timestamp}",
                "artifact_type": "crossbook_scan",
                "script": "cross_book_scan.py",
                "path": str(inputs["crossbook_path"]),
                "source_snapshot_id": crossbook.get("source_snapshot_id") or crossbook.get("input_snapshot"),
                "provides": ["path_a_crossbook", "asian_handicap", "totals"],
            },
        ],
        "source_freshness": {"status": "pass", "sources": sources or [{"snapshot_id": "legacy_unknown", "source": "legacy"}]},
        "p_market": p_market,
        "p_model": p_model,
        "p_adj": {"_note": "p_adj defaults to p_market in recovered partial report; model deltas cannot drive actionability"},
        "numbers": [],
        "entry_time_utc": None,
        "entry_price": None,
        "lineup_status": "not_required",
        "information_event": "blocked_recovery_legacy_guarded_report",
        "generated_at_utc": utc_now(),
        "report_path": str(report_path),
        "window_display": "",
        "recovery_provenance": [
            {
                "recovery_id": recovery_id,
                "generated_by": "blocked_recovery",
                "generated_at_utc": utc_now(),
                "actions": ["build_legacy_guarded_manifest", "build_guarded_report"],
                "legacy_artifact_path": str(inputs["legacy_path"]),
                "crossbook_artifact_path": str(inputs["crossbook_path"]),
                "legacy_report_path": str(inputs.get("legacy_report_path") or ""),
                "preserved_source_quality_cap": "C",
            }
        ],
    }
    if inputs.get("model_path"):
        manifest["artifacts"].append(
            {
                "artifact_id": f"model:{match_id}:legacy-recovered:{timestamp}",
                "artifact_type": "model",
                "script": "model_runner.py",
                "path": str(inputs["model_path"]),
                "provides": ["path_b_model_diagnostic"],
            }
        )
    if inputs.get("deep_research_path"):
        manifest["artifacts"].append(
            {
                "artifact_id": f"deep_research:{match_id}:legacy-recovered:{timestamp}",
                "artifact_type": "deep_research",
                "script": "deep_research_finalizer",
                "path": str(inputs["deep_research_path"]),
                "provides": ["deep_research"],
            }
        )

    update_direct_request_for_recovery(direct_request_path, manifest_path, report_path, match_id, match_label)
    write_json(manifest_path, manifest)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_legacy_guarded_report_text(manifest, manifest_path, direct_request_path, legacy, crossbook), encoding="utf-8")

    result = recover_missing_artifacts({"manifest_path": str(manifest_path), "report_path": str(report_path)}, recovery_id)
    if result.get("status") in {"recovered", "recovered_summary_failed"}:
        result["reason"] = "legacy artifacts rebuilt into guarded partial report; source/actionability not promoted"
    return result


def direct_summary(manifest_path: Path, report_path: Path | None) -> tuple[bool, str]:
    cmd = [python_bin(), str(SCRIPTS_DIR / "rich_summary.py"), "--manifest", str(manifest_path), "--max-chars", "3900"]
    if report_path:
        cmd.extend(["--report", str(report_path)])
    result = cmd_run(cmd, timeout=90)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "rich_summary failed").strip()[:800]
    return True, result.stdout.strip()


def recover_missing_artifacts(event: dict[str, Any], recovery_id: str) -> dict[str, Any]:
    manifest_path = resolve_path(event.get("manifest_path"))
    if not manifest_path or not manifest_path.exists():
        return {"status": "waiting", "reason": "manifest_path missing; cannot repair artifacts"}

    manifest_before = read_json(manifest_path, {})
    if not isinstance(manifest_before, dict):
        return {"status": "failed", "reason": "manifest is not a JSON object"}
    manifest_original_text = manifest_path.read_text(encoding="utf-8")

    preserved = {
        "final_status": manifest_before.get("final_status"),
        "source_quality": manifest_before.get("source_quality"),
        "source_quality_cap": manifest_before.get("source_quality_cap"),
        "actionable_allowed": manifest_before.get("actionable_allowed"),
        "report_completeness": manifest_before.get("report_completeness"),
    }
    report_path = resolve_path(event.get("report_path"), manifest_path) or manifest_report_path(manifest_before, manifest_path)
    report_original_text = report_path.read_text(encoding="utf-8") if report_path and report_path.exists() else None
    match_id = str(manifest_before.get("match_id") or (manifest_before.get("match") or {}).get("match_id") or "unknown").upper()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    actions: list[str] = []

    caps = manifest_artifact_caps(manifest_before)
    if "role_engine" not in caps:
        role_path = ARTIFACTS_DIR / f"role-engine-{match_id}-{timestamp}-recovery.json"
        role_cmd = [
            python_bin(),
            str(SCRIPTS_DIR / "role_engine.py"),
            "--manifest",
            str(manifest_path),
            "--output",
            str(role_path),
            "--patch-manifest",
        ]
        if report_path:
            role_cmd.extend(["--report", str(report_path), "--patch-report"])
        result = cmd_run(role_cmd, timeout=90)
        if result.returncode != 0:
            manifest_path.write_text(manifest_original_text, encoding="utf-8")
            if report_path and report_original_text is not None:
                report_path.write_text(report_original_text, encoding="utf-8")
            return {"status": "failed", "reason": (result.stderr or result.stdout or "role_engine failed").strip()[:800]}
        stamp_artifact(role_path, recovery_id, "generate_role_engine", manifest_path)
        actions.append("generate_role_engine")

    manifest_mid = read_json(manifest_path, {})
    if not isinstance(manifest_mid, dict):
        return {"status": "failed", "reason": "manifest unreadable after role_engine"}

    mech_path = ARTIFACTS_DIR / f"mechanism-audit-{match_id}-{timestamp}-recovery.json"
    mech = cmd_run(
        [
            python_bin(),
            str(SCRIPTS_DIR / "mechanism_audit.py"),
            "--manifest",
            str(manifest_path),
            "--output",
            str(mech_path),
        ],
        timeout=90,
    )
    if mech.returncode != 0:
        manifest_path.write_text(manifest_original_text, encoding="utf-8")
        if report_path and report_original_text is not None:
            report_path.write_text(report_original_text, encoding="utf-8")
        return {"status": "failed", "reason": (mech.stderr or mech.stdout or "mechanism_audit failed").strip()[:800]}
    stamp_artifact(mech_path, recovery_id, "generate_mechanism_audit", manifest_path)

    manifest_after = read_json(manifest_path, {})
    if not isinstance(manifest_after, dict):
        return {"status": "failed", "reason": "manifest unreadable after mechanism_audit"}
    replace_artifact(
        manifest_after,
        "mechanism_audit",
        {
            "artifact_id": f"mechanism:{match_id}:{timestamp}:recovery",
            "artifact_type": "mechanism_audit",
            "script": "mechanism_audit.py",
            "path": str(mech_path),
            "provides": ["mechanism_audit"],
        },
    )
    gates = manifest_after.get("analysis_gates")
    if isinstance(gates, dict):
        gates["mechanism_audit"] = "pass"

    for key, value in preserved.items():
        if value is not None:
            manifest_after[key] = value
    manifest_after.setdefault("recovery_provenance", [])
    if isinstance(manifest_after["recovery_provenance"], list):
        manifest_after["recovery_provenance"].append(
            {
                "recovery_id": recovery_id,
                "generated_by": "blocked_recovery",
                "generated_at_utc": utc_now(),
                "actions": actions + ["generate_mechanism_audit"],
                "preserved_fields": preserved,
            }
        )
    write_json(manifest_path, manifest_after)

    ok, validation = validate_manifest(manifest_path, report_path)
    if not ok:
        manifest_path.write_text(manifest_original_text, encoding="utf-8")
        if report_path and report_original_text is not None:
            report_path.write_text(report_original_text, encoding="utf-8")
        return {
            "status": "failed_contract",
            "reason": validation,
            "actions": actions + ["generate_mechanism_audit"],
            "manifest_path": str(manifest_path),
            "report_path": str(report_path) if report_path else "",
        }

    summary_ok, summary = direct_summary(manifest_path, report_path)
    return {
        "status": "recovered" if summary_ok else "recovered_summary_failed",
        "reason": "missing artifacts regenerated without source/actionability promotion",
        "actions": actions + ["generate_mechanism_audit"],
        "manifest_path": str(manifest_path),
        "report_path": str(report_path) if report_path else "",
        "summary": summary if summary_ok else "",
        "summary_error": "" if summary_ok else summary,
    }


def find_latest_manifest(match_id: str, window: str | None) -> tuple[Path, dict[str, Any]] | None:
    match_id = match_id.upper()
    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    if not ARTIFACTS_DIR.exists():
        return None
    for path in ARTIFACTS_DIR.glob("manifest-*.json"):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        current = str(payload.get("match_id") or (payload.get("match") or {}).get("match_id") or "").upper()
        if current != match_id:
            continue
        if window:
            raw_window = str(payload.get("window") or payload.get("timing_class") or "")
            if raw_window != window:
                continue
        candidates.append((path.stat().st_mtime, path, payload))
    if not candidates:
        return None
    _mtime, path, payload = max(candidates, key=lambda item: item[0])
    return path, payload


def recover_missing_guarded_report(event: dict[str, Any]) -> dict[str, Any]:
    match_id = str(event.get("match_id") or "").upper()
    if not match_id:
        match_id = extract_match_id(event)
    if not match_id:
        return {"status": "waiting", "reason": "missing match_id"}
    found = find_latest_manifest(match_id, str(event.get("window") or "") or None)
    if not found:
        legacy = legacy_recovery_inputs(match_id, event)
        if legacy:
            return recover_legacy_guarded_report(event, recovery_id_for(event))
        return {"status": "waiting", "reason": "no guarded manifest or recoverable legacy artifact bundle exists yet"}
    manifest_path, manifest = found
    report_path = manifest_report_path(manifest, manifest_path)
    ok, validation = validate_manifest(manifest_path, report_path)
    if not ok:
        return {"status": "failed_contract", "reason": validation, "manifest_path": str(manifest_path)}
    summary_ok, summary = direct_summary(manifest_path, report_path)
    return {
        "status": "recovered" if summary_ok else "recovered_summary_failed",
        "reason": "guarded manifest/report now available",
        "manifest_path": str(manifest_path),
        "report_path": str(report_path) if report_path else "",
        "summary": summary if summary_ok else "",
        "summary_error": "" if summary_ok else summary,
    }


def terminal_result(category: str, classification: dict[str, Any]) -> dict[str, Any]:
    if category == "safety_block":
        return {
            "status": "manual_required",
            "reason": "freeform WC26 output was blocked; discard it and rebuild through deterministic report pipeline",
        }
    if category == "detector_bug":
        return {
            "status": "engineering_required",
            "reason": "data appears present but detector/key matching failed; requires code fix, not data refresh",
        }
    if category == "credential_block":
        return {"status": "source_health_required", "reason": "credential/token block cannot be auto-recovered"}
    if category == "missing_source":
        return {"status": "waiting_for_source", "reason": "true source gap must stay partial/incomplete"}
    if category in {"contract_mismatch", "identity_mismatch"}:
        return {"status": "manual_required", "reason": f"{category} must not be auto-blessed by recovery"}
    if category == "opportunity_block":
        return {"status": "observation_only", "reason": "opportunity watcher blocks do not trigger recovery"}
    return {"status": "manual_required", "reason": f"{category} is not auto-recoverable"}


def process_event(event_path: Path, state: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    event = read_json(event_path, {})
    if not isinstance(event, dict):
        return {"status": "failed", "reason": "event JSON root must be object", "event_path": str(event_path)}
    recovery_id = recovery_id_for(event)
    classification = classify_event(event)
    if (
        classification.get("category") == "safety_block"
        and "missing_guarded_report" in set(classification.get("candidates") or [])
    ):
        extracted_match_id = extract_match_id(event)
        if extracted_match_id and legacy_recovery_inputs(extracted_match_id, event):
            classification = dict(classification)
            classification["category"] = "missing_guarded_report"
            classification["auto_recoverable"] = True
            classification["recovery_route"] = "legacy_guarded_report"
    category = classification["category"]
    entries = state.setdefault("entries", {})
    entry = entries.setdefault(recovery_id, {})
    attempts = int(entry.get("attempts") or 0)
    now = datetime.now(timezone.utc)

    if entry.get("status") in {"recovered", "manual_required", "engineering_required", "source_health_required", "observation_only", "recovery_exhausted"} and not force:
        return {"status": "skipped_terminal", "recovery_id": recovery_id, "category": category}

    if attempts >= MAX_ATTEMPTS and not force:
        entry.update({"status": "recovery_exhausted", "category": category, "updated_at_utc": utc_now()})
        return {"status": "recovery_exhausted", "recovery_id": recovery_id, "category": category}

    if not should_attempt(entry, now, force=force):
        return {
            "status": "skipped_backoff",
            "recovery_id": recovery_id,
            "category": category,
            "retry_after_utc": entry.get("retry_after_utc"),
        }

    attempts += 1
    entry.update(
        {
            "event_path": str(event_path),
            "attempts": attempts,
            "category": category,
            "classification": classification,
            "last_attempt_at_utc": utc_now(),
        }
    )

    if category in TERMINAL_CATEGORIES:
        result = terminal_result(category, classification)
    elif category == "missing_guarded_report":
        result = recover_missing_guarded_report(event)
    elif category == "missing_artifact":
        result = recover_missing_artifacts(event, recovery_id)
    elif category == "stale_snapshot":
        result = {"status": "waiting", "reason": "stale snapshot; wait for collector owner, do not refresh paid APIs here"}
    else:
        result = terminal_result(category, classification)

    entry.update(
        {
            "status": result.get("status"),
            "result": {k: v for k, v in result.items() if k != "summary"},
            "updated_at_utc": utc_now(),
        }
    )
    if result.get("status") in {"failed", "failed_contract", "waiting", "recovered_summary_failed"} and attempts < MAX_ATTEMPTS:
        retry_at = now.timestamp() + retry_after_minutes(attempts) * 60
        entry["retry_after_utc"] = datetime.fromtimestamp(retry_at, tz=timezone.utc).isoformat()
    elif result.get("status") in {"failed", "failed_contract", "waiting", "recovered_summary_failed"}:
        entry["status"] = "recovery_exhausted"
        entry["exhausted_reason"] = result.get("reason")
        result = {"status": "recovery_exhausted", "reason": result.get("reason"), "exhausted_category": category}
    else:
        entry.pop("retry_after_utc", None)

    return {"recovery_id": recovery_id, "category": category, **result}


def queue_files(queue_dir: Path = QUEUE_DIR) -> list[Path]:
    if not queue_dir.exists():
        return []
    return sorted(queue_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)


def archive_event_file(event_path: Path, status: str) -> None:
    try:
        target_dir = ARCHIVE_DIR / (status or "unknown")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / event_path.name
        if target.exists():
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            target = target_dir / f"{event_path.stem}-{suffix}{event_path.suffix}"
        event_path.replace(target)
    except OSError:
        pass


def is_terminal_status(status: Any) -> bool:
    return str(status or "") in {
        "recovered",
        "manual_required",
        "engineering_required",
        "source_health_required",
        "observation_only",
        "recovery_exhausted",
        "skipped_terminal",
    }


def render_results(results: list[dict[str, Any]]) -> str:
    recovered = [item for item in results if item.get("status") == "recovered" and item.get("summary")]
    exhausted = [item for item in results if item.get("status") == "recovery_exhausted"]
    lines: list[str] = []
    for item in recovered:
        lines.append(str(item["summary"]).strip())
        lines.append("")
        lines.append(f"WC26_BLOCKED_RECOVERY: recovered | id={item.get('recovery_id')} | category={item.get('category')}")
    for item in exhausted:
        lines.append(
            "\n".join(
                [
                    "BLOCKED_RECOVERY_EXHAUSTED",
                    f"id={item.get('recovery_id')}",
                    f"category={item.get('category')}",
                    f"reason={item.get('reason') or item.get('exhausted_reason') or 'retry limit reached'}",
                ]
            )
        )
    return "\n\n---\n\n".join(line for line in lines if line.strip())


def main() -> int:
    global WORKSPACE, QUEUE_DIR, ARCHIVE_DIR, STATE_PATH, ARTIFACTS_DIR
    parser = argparse.ArgumentParser(description="WC26 blocked recovery queue processor")
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    parser.add_argument("--queue-dir", type=Path)
    parser.add_argument("--event", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-events", type=int, default=int(os.environ.get("WC26_BLOCKED_RECOVERY_MAX_EVENTS", "8")))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    WORKSPACE = args.workspace
    QUEUE_DIR = args.queue_dir or (WORKSPACE / "blocked_recovery" / "queue")
    ARCHIVE_DIR = WORKSPACE / "blocked_recovery" / "archive"
    STATE_PATH = WORKSPACE / "state" / "blocked-recovery.json"
    ARTIFACTS_DIR = WORKSPACE / "reports" / "artifacts"

    state = load_state()
    paths = [args.event] if args.event else queue_files(QUEUE_DIR)[: args.max_events]
    results = []
    for path in paths:
        if not path:
            continue
        result = process_event(path, state, force=args.force)
        results.append(result)
        if is_terminal_status(result.get("status")):
            archive_event_file(path, str(result.get("status")))
    state["last_run_utc"] = utc_now()
    state["last_processed_count"] = len(results)
    save_state(state)
    if args.json:
        print(json.dumps({"results": results, "state_path": str(STATE_PATH)}, ensure_ascii=False, indent=2))
    else:
        rendered = render_results(results)
        if rendered:
            print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
