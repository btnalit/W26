#!/usr/bin/env python3
"""Validate WC26 report numeric provenance before direct report relay.

The worker report can be prose, but every actionable numeric market claim must
be backed by a deterministic artifact and a source snapshot. This script checks
the sidecar JSON artifact manifest that the worker must write next to the
Markdown report.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACTIONABLE_STATUSES = {"lean", "qualified_play"}
ALLOWED_STATUSES = {"pass", "pass_incomplete", "watch", "lean", "qualified_play", "simulation_only"}
ALLOWED_MODES = {"live", "simulation"}
DIRECT_WORKFLOW_CONTRACT = "wc26.direct_report.v1"
DIRECT_REQUIRED_GATES = {
    "devig_three_method",
    "path_a_crossbook",
    "asian_handicap",
    "totals",
    "path_b_model_diagnostic",
    "path_c_consistency",
    "mechanism_audit",
    "source_freshness",
}
DIRECT_REQUIRED_ARTIFACT_CAPABILITIES = {
    "devig_1x2",
    "path_a_crossbook",
    "asian_handicap",
    "totals",
    "path_c_consistency",
    "mechanism_audit",
}
DIRECT_OK_GATE_STATUSES = {"pass", "ok", "complete", "no_signal", "diagnostic"}
DIRECT_SKIPPED_GATE_STATUSES = {"skipped_missing_source", "skipped_not_applicable", "skipped_partial"}
DIRECT_CAPABILITY_TO_GATE = {
    "devig_1x2": "devig_three_method",
    "path_a_crossbook": "path_a_crossbook",
    "asian_handicap": "asian_handicap",
    "totals": "totals",
    "path_c_consistency": "path_c_consistency",
    "mechanism_audit": "mechanism_audit",
}
MECHANISM_AUDIT_CONTRACT = "wc26.mechanism_audit.v1"
ROLE_ENGINE_CONTRACT = "wc26.role_engine.v1"
ROLE_ENGINE_DECISION_ENUMS = {"CONFIRMED", "REFUTED", "DIAGNOSTIC_ONLY", "BLOCKED", "SUSPECT"}
ROLE_ENGINE_ACTIONABILITY_ENUMS = {"never_actionable", "supports_path_a", "contradicts_path_a"}
ROLE_ENGINE_ROLES = {"bookmaker_intent", "public_bias", "ai_lag", "trap_risk", "market_efficiency"}
PATH_A_ACTIONABLE_EV_THRESHOLD = 0.05
PATH_A_RECOMPUTE_TOLERANCE = 0.0015
MECHANISM_DECISION_ENUMS = {
    "CONFIRMED_ACTIONABLE",
    "CONFIRMED_NOISE",
    "REFUTED",
    "DIAGNOSTIC_ONLY",
    "SUSPECT",
    "BLOCKED",
}
CRITICAL_NUMBER_KINDS = {
    "no_vig",
    "scalar_ev",
    "p_adj_edge",
    "kelly",
    "asian_handicap_ev",
    "asian_handicap_kelly",
    "robust_ev",
}

WINDOW_HOUR_RANGES = {
    "T-72h_early": (60.0, 84.0),
    "T-48h_early_update": (36.0, 60.0),
    "T-24h_confirm": (18.0, 30.0),
    "T-6h_preflight": (3.0, 9.0),
    "T-90m_lineup_probe": (1.25, 1.75),
    "T-75m_team_sheet_checkpoint": (1.0, 1.5),
    "T-60m_lineup_final": (0.75, 1.25),
    "T-45m_price_guard": (0.5, 1.0),
}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def parse_utc(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value or value.upper().startswith("N/A"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _first_datetime(*values: Any) -> datetime | None:
    for value in values:
        parsed = parse_utc(value)
        if parsed is not None:
            return parsed
    return None


def _match_field(payload: dict[str, Any], *keys: str) -> Any:
    match = payload.get("match")
    for key in keys:
        if key in payload:
            return payload.get(key)
    if isinstance(match, dict):
        for key in keys:
            if key in match:
                return match.get(key)
    return None


def _window_display(hours_to_kickoff: float) -> str:
    if hours_to_kickoff >= 96:
        days = max(1, round(hours_to_kickoff / 24))
        return f"T-{days}d"
    return f"T-{hours_to_kickoff:.1f}h"


def _validate_timing_window(payload: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    kickoff = _first_datetime(_match_field(payload, "kickoff_utc"))
    entry = _first_datetime(
        payload.get("entry_time_utc"),
        payload.get("captured_at_utc"),
        payload.get("cutoff_utc"),
        payload.get("created_at_utc"),
        payload.get("updated_at_utc"),
    )
    if kickoff is None or entry is None:
        return
    hours = (kickoff - entry).total_seconds() / 3600.0
    if hours < 0:
        errors.append("timing window invalid: entry_time_utc is after kickoff_utc")
        return
    declared_window = str(payload.get("window", "")).strip()
    declared_timing = str(payload.get("timing_class", "")).strip()
    display = _window_display(hours)

    if hours > 84.0 and declared_window in WINDOW_HOUR_RANGES:
        errors.append(
            f"window {declared_window} inconsistent with hours_to_kickoff={hours:.1f}; "
            f"use early_structural / {display} before the scheduled T-72h window"
        )
    if declared_window in WINDOW_HOUR_RANGES:
        lo, hi = WINDOW_HOUR_RANGES[declared_window]
        if not (lo <= hours <= hi):
            errors.append(
                f"window {declared_window} requires {lo:.1f}-{hi:.1f} hours_to_kickoff, got {hours:.1f}"
            )
    elif declared_window == "early_structural":
        if hours <= 84.0:
            warnings.append(f"early_structural window used at {hours:.1f} hours_to_kickoff; a scheduled timing window may be more precise")
    elif not declared_window:
        warnings.append("manifest missing window; direct summary will use timing_class only")

    expected_display = display if hours >= 84.0 else None
    declared_display = str(payload.get("window_display", "")).strip()
    if expected_display and declared_display and declared_display != expected_display:
        errors.append(f"window_display {declared_display} inconsistent with computed {expected_display}")
    if hours > 84.0 and declared_timing and declared_timing != "early_structural":
        errors.append(f"timing_class {declared_timing} inconsistent with {hours:.1f} hours_to_kickoff; expected early_structural")


def resolve_artifact_path(raw_path: str, manifest_path: Path | None) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    if manifest_path is not None:
        return (manifest_path.parent / path).resolve()
    return path


def _load_artifact_payload(artifact: dict[str, Any], manifest_path: Path | None) -> dict[str, Any] | None:
    raw_path = str(artifact.get("path", "")).strip()
    if not raw_path:
        return None
    path = resolve_artifact_path(raw_path, manifest_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _load_cross_book_scan_module() -> Any | None:
    path = Path(__file__).resolve().parent / "cross_book_scan.py"
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_wc26_cross_book_scan_contract", str(path))
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _manifest_team(manifest: dict[str, Any], side: str) -> str | None:
    keys = {
        "home": ("home_team", "home"),
        "away": ("away_team", "away"),
    }[side]
    for key in keys:
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    match = manifest.get("match")
    if isinstance(match, dict):
        for key in keys:
            value = match.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _resolve_input_snapshot_path(raw_path: str, manifest_path: Path | None) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    candidates = [path]
    if not path.is_absolute() and manifest_path is not None:
        candidates.append((manifest_path.parent / path).resolve())
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return path if path.is_absolute() else candidates[-1]


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _close_enough(reported: Any, expected: Any, tolerance: float = PATH_A_RECOMPUTE_TOLERANCE) -> bool:
    left = _as_float(reported)
    right = _as_float(expected)
    if left is None or right is None:
        return False
    return abs(left - right) <= tolerance


def _path_a_edge_is_actionable(edge: dict[str, Any]) -> bool:
    ev = _as_float(edge.get("ev_shin")) or 0.0
    return (
        edge.get("actionable") is True
        or edge.get("qualifies") is True
        or (edge.get("survives_all_methods") is True and not edge.get("suspect") and ev >= PATH_A_ACTIONABLE_EV_THRESHOLD)
    )


def _validate_path_a_recompute(
    artifact_id: Any,
    artifact_payload: dict[str, Any],
    manifest: dict[str, Any],
    manifest_path: Path | None,
    actionable_edges: list[tuple[str, dict[str, Any]]],
    errors: list[str],
) -> None:
    if not actionable_edges:
        return
    raw_snapshot = str(artifact_payload.get("input_snapshot") or "").strip()
    snapshot_path = _resolve_input_snapshot_path(raw_snapshot, manifest_path)
    if snapshot_path is None or not snapshot_path.exists():
        errors.append(f"crossbook artifact {artifact_id} actionable edge requires readable input_snapshot for contract recompute")
        return

    module = _load_cross_book_scan_module()
    if module is None:
        errors.append(f"crossbook artifact {artifact_id} actionable edge requires cross_book_scan.py for contract recompute")
        return

    match_home = artifact_payload.get("match_home") or _manifest_team(manifest, "home")
    match_away = artifact_payload.get("match_away") or _manifest_team(manifest, "away")
    try:
        board = module.parse_odds_snapshot(str(snapshot_path), match_home, match_away)
    except Exception as exc:
        errors.append(f"crossbook artifact {artifact_id} input_snapshot recompute failed: {exc}")
        return

    recomputed_markets: dict[str, dict[str, Any]] = {}
    edge_threshold = _as_float(artifact_payload.get("edge_threshold")) or 0.02
    actionable_threshold = _as_float(artifact_payload.get("actionable_threshold")) or PATH_A_ACTIONABLE_EV_THRESHOLD
    suspect_threshold = _as_float(artifact_payload.get("suspect_threshold")) or 0.08
    for market_name, market_result in (artifact_payload.get("markets") or {}).items():
        if not isinstance(market_result, dict) or market_result.get("status") != "ok":
            continue
        outcomes = market_result.get("outcomes_scanned")
        if not isinstance(outcomes, list) or not outcomes:
            continue
        try:
            recomputed_markets[market_name] = module.scan_market(
                board,
                market_name,
                list(outcomes),
                edge_threshold=edge_threshold,
                actionable_threshold=actionable_threshold,
                suspect_threshold=suspect_threshold,
            )
        except Exception as exc:
            errors.append(f"crossbook artifact {artifact_id} market {market_name} recompute failed: {exc}")

    try:
        recomputed_summary = module.build_summary({"markets": recomputed_markets})
    except Exception as exc:
        errors.append(f"crossbook artifact {artifact_id} summary recompute failed: {exc}")
        recomputed_summary = {}

    summary = artifact_payload.get("summary") if isinstance(artifact_payload.get("summary"), dict) else {}
    for key in ("actionable_count", "qualified_play_count"):
        if isinstance(summary.get(key), int) and summary.get(key) != recomputed_summary.get(key):
            errors.append(f"crossbook artifact {artifact_id} summary {key} does not match input_snapshot recompute")

    for market_name, edge in actionable_edges:
        market_result = recomputed_markets.get(market_name)
        if not isinstance(market_result, dict):
            errors.append(f"crossbook artifact {artifact_id} actionable edge {market_name} missing recomputed market")
            continue
        expected_quote = None
        for quote in market_result.get("quotes", []):
            if not isinstance(quote, dict):
                continue
            if quote.get("book") == edge.get("book") and quote.get("outcome") == edge.get("outcome"):
                expected_quote = quote
                break
        if expected_quote is None:
            errors.append(f"crossbook artifact {artifact_id} actionable edge {edge.get('book')} {market_name} {edge.get('outcome')} missing in input_snapshot recompute")
            continue
        for key in ("offered_odds", "sharp_fair_prob", "fair_odds", "ev_shin", "ev_power", "ev_multiplicative"):
            if not _close_enough(edge.get(key), expected_quote.get(key)):
                errors.append(f"crossbook artifact {artifact_id} actionable edge {edge.get('book')} {market_name} {edge.get('outcome')} {key} does not match input_snapshot recompute")
        for key in ("survives_all_methods", "suspect", "qualifies", "actionable"):
            if key in edge and key in expected_quote and edge.get(key) != expected_quote.get(key):
                errors.append(f"crossbook artifact {artifact_id} actionable edge {edge.get('book')} {market_name} {edge.get('outcome')} {key} does not match input_snapshot recompute")


def _artifact_capabilities(
    artifact: dict[str, Any],
    artifact_payload: dict[str, Any] | None,
) -> set[str]:
    provides = artifact.get("provides", [])
    if not isinstance(provides, list):
        provides = []
    raw_parts = [
        str(artifact.get("artifact_type", "")),
        str(artifact.get("script", "")),
        str(artifact.get("path", "")),
        " ".join(str(item) for item in provides),
    ]
    if artifact_payload:
        raw_parts.extend(
            [
                str(artifact_payload.get("artifact_kind", "")),
                str(artifact_payload.get("artifact_type", "")),
                str(artifact_payload.get("script", "")),
                " ".join(str(item) for item in artifact_payload.get("provides", []) if isinstance(artifact_payload.get("provides", []), list)),
            ]
        )
    raw = " ".join(raw_parts).lower()
    caps: set[str] = set()

    if "no_vig" in raw or "scalar_market" in raw or ("devig" in raw and "1x2" in raw):
        caps.add("devig_1x2")
    if "cross_book" in raw or "crossbook" in raw:
        caps.add("path_a_crossbook")
    if "asian_handicap" in raw or " ah" in raw or "-ah-" in raw:
        caps.add("asian_handicap")
    if "totals" in raw or "over_under" in raw or "total_goals" in raw:
        caps.add("totals")
    if "consistency_triangle" in raw or "path_c" in raw:
        caps.add("path_c_consistency")
    if "mechanism_audit" in raw or "mechanism audit" in raw:
        caps.add("mechanism_audit")
    if "role_engine" in raw:
        caps.add("role_engine")
    return caps


def _validate_crossbook_artifact(
    artifact: dict[str, Any],
    artifact_payload: dict[str, Any] | None,
    manifest: dict[str, Any],
    manifest_path: Path | None,
    errors: list[str],
) -> None:
    artifact_id = artifact.get("artifact_id")
    if not artifact_payload:
        errors.append(f"crossbook artifact {artifact_id} must be readable JSON")
        return

    raw_type = " ".join(
        str(value)
        for value in [
            artifact.get("artifact_type", ""),
            artifact.get("script", ""),
            artifact_payload.get("artifact_type", ""),
            artifact_payload.get("artifact_kind", ""),
            artifact_payload.get("script", ""),
        ]
    ).lower()
    if "crossbook" not in raw_type and "cross_book_scan" not in raw_type:
        errors.append(f"path_a_crossbook artifact {artifact_id} must be a cross_book_scan artifact, not {artifact_payload.get('artifact_type') or artifact.get('artifact_type')}")
        return

    if not str(artifact_payload.get("input_snapshot") or artifact_payload.get("source_snapshot_id") or "").strip():
        errors.append(f"crossbook artifact {artifact_id} missing input_snapshot/source_snapshot_id")

    markets = artifact_payload.get("markets")
    if not isinstance(markets, dict) or not markets:
        errors.append(f"crossbook artifact {artifact_id} requires non-empty markets")
        return

    summary = artifact_payload.get("summary") or artifact_payload.get("scan_summary")
    if not isinstance(summary, dict):
        errors.append(f"crossbook artifact {artifact_id} requires summary")
    else:
        for key in ("quotes_scanned", "edge_count", "noise_edge_count", "actionable_count"):
            if not isinstance(summary.get(key), int):
                errors.append(f"crossbook artifact {artifact_id} summary missing integer {key}")
        for key in ("raw_actionable_count", "relay_actionable_count", "qualified_play_count"):
            if key in summary and not isinstance(summary.get(key), int):
                errors.append(f"crossbook artifact {artifact_id} summary {key} must be integer when present")
        best_edge = summary.get("best_edge") or summary.get("best_qualified_edge")
        if summary.get("edge_count", 0) and not isinstance(best_edge, dict):
            errors.append(f"crossbook artifact {artifact_id} summary missing best_edge")
        if "raw_actionable_count" in summary and summary.get("raw_actionable_count") != summary.get("actionable_count"):
            errors.append(f"crossbook artifact {artifact_id} summary raw_actionable_count must equal actionable_count")
        if "relay_actionable_count" in summary and summary.get("relay_actionable_count", 0) > summary.get("actionable_count", 0):
            errors.append(f"crossbook artifact {artifact_id} summary relay_actionable_count cannot exceed actionable_count")
        if "qualified_play_count" in summary and summary.get("qualified_play_count") != summary.get("relay_actionable_count", 0):
            errors.append(f"crossbook artifact {artifact_id} summary qualified_play_count must equal relay_actionable_count")
        if summary.get("actionable_count", 0) and not isinstance(summary.get("best_actionable_edge") or summary.get("best_qualified_edge"), dict):
            errors.append(f"crossbook artifact {artifact_id} summary actionable_count requires best_actionable_edge")

    ok_markets = 0
    edge_count = 0
    noise_edge_count = 0
    actionable_count = 0
    quotes_total = 0
    actionable_edges: list[tuple[str, dict[str, Any]]] = []
    for market_name, market_result in markets.items():
        if not isinstance(market_result, dict):
            errors.append(f"crossbook artifact {artifact_id} market {market_name} must be an object")
            continue
        status = str(market_result.get("status", "")).strip()
        if not status:
            errors.append(f"crossbook artifact {artifact_id} market {market_name} missing status")
            continue
        if status != "ok":
            continue
        ok_markets += 1
        for required_key in ("sharp_anchor", "devig_primary"):
            if not str(market_result.get(required_key, "")).strip():
                errors.append(f"crossbook artifact {artifact_id} market {market_name} missing {required_key}")
        outcomes = market_result.get("outcomes_scanned")
        if not isinstance(outcomes, list) or not outcomes:
            errors.append(f"crossbook artifact {artifact_id} market {market_name} missing outcomes_scanned")
        fair_probs = market_result.get("fair_probs")
        if not isinstance(fair_probs, dict):
            errors.append(f"crossbook artifact {artifact_id} market {market_name} missing fair_probs")
        else:
            for method in ("shin", "power", "multiplicative"):
                if not isinstance(fair_probs.get(method), dict) or not fair_probs.get(method):
                    errors.append(f"crossbook artifact {artifact_id} market {market_name} missing fair_probs.{method}")
        quotes = market_result.get("quotes")
        quotes_scanned = market_result.get("quotes_scanned")
        if not isinstance(quotes_scanned, int):
            errors.append(f"crossbook artifact {artifact_id} market {market_name} missing integer quotes_scanned")
        if not isinstance(quotes, list):
            errors.append(f"crossbook artifact {artifact_id} market {market_name} missing quotes list")
            quotes = []
        elif isinstance(quotes_scanned, int) and len(quotes) != quotes_scanned:
            errors.append(f"crossbook artifact {artifact_id} market {market_name} quotes length does not match quotes_scanned")
        if isinstance(quotes_scanned, int):
            quotes_total += quotes_scanned
        for quote_index, quote in enumerate(quotes):
            if not isinstance(quote, dict):
                errors.append(f"crossbook artifact {artifact_id} market {market_name} quotes[{quote_index}] must be an object")
                continue
            for key in ("book", "outcome", "offered_odds", "sharp_fair_prob", "fair_odds", "ev_shin", "ev_power", "ev_multiplicative"):
                if key not in quote:
                    errors.append(f"crossbook artifact {artifact_id} market {market_name} quotes[{quote_index}] missing {key}")
            for key in ("survives_all_methods", "suspect", "qualifies", "actionable"):
                if not isinstance(quote.get(key), bool):
                    errors.append(f"crossbook artifact {artifact_id} market {market_name} quotes[{quote_index}] missing boolean {key}")
            if quote.get("qualifies") is True and not _path_a_edge_is_actionable(quote):
                errors.append(f"crossbook artifact {artifact_id} market {market_name} quotes[{quote_index}] qualifies true but is not actionable")
        for edge_index, edge in enumerate(market_result.get("edges", [])):
            if not isinstance(edge, dict):
                errors.append(f"crossbook artifact {artifact_id} market {market_name} edges[{edge_index}] must be an object")
                continue
            edge_count += 1
            if not isinstance(edge.get("survives_all_methods"), bool):
                errors.append(f"crossbook artifact {artifact_id} market {market_name} edges[{edge_index}] missing boolean survives_all_methods")
            for key in ("survives_all_methods", "suspect", "qualifies", "actionable"):
                if not isinstance(edge.get(key), bool):
                    errors.append(f"crossbook artifact {artifact_id} market {market_name} edges[{edge_index}] missing boolean {key}")
            for key in ("ev_shin", "ev_power", "ev_multiplicative"):
                if key not in edge:
                    errors.append(f"crossbook artifact {artifact_id} market {market_name} edges[{edge_index}] missing {key}")
            ev_shin = _as_float(edge.get("ev_shin")) or 0.0
            if edge.get("ev_band") == "noise_lt_5pp":
                noise_edge_count += 1
            if edge.get("actionable") is True or edge.get("qualifies") is True:
                actionable_count += 1
                actionable_edges.append((market_name, edge))
                if edge.get("survives_all_methods") is not True:
                    errors.append(f"crossbook artifact {artifact_id} market {market_name} edges[{edge_index}] actionable edge must survive all methods")
                if edge.get("suspect") is True:
                    errors.append(f"crossbook artifact {artifact_id} market {market_name} edges[{edge_index}] actionable edge cannot be suspect")
                if ev_shin < PATH_A_ACTIONABLE_EV_THRESHOLD:
                    errors.append(f"crossbook artifact {artifact_id} market {market_name} edges[{edge_index}] actionable edge below 5pp threshold")

    if ok_markets == 0:
        errors.append(f"crossbook artifact {artifact_id} has no ok market scans")
    if isinstance(summary, dict):
        if isinstance(summary.get("quotes_scanned"), int) and summary.get("quotes_scanned") != quotes_total:
            errors.append(f"crossbook artifact {artifact_id} summary quotes_scanned does not match market totals")
        if isinstance(summary.get("edge_count"), int) and summary.get("edge_count") != edge_count:
            errors.append(f"crossbook artifact {artifact_id} summary edge_count does not match market edges")
        if isinstance(summary.get("noise_edge_count"), int) and summary.get("noise_edge_count") != noise_edge_count:
            errors.append(f"crossbook artifact {artifact_id} summary noise_edge_count does not match market edges")
        if isinstance(summary.get("actionable_count"), int) and summary.get("actionable_count") != actionable_count:
            errors.append(f"crossbook artifact {artifact_id} summary actionable_count does not match market edges")
        if isinstance(summary.get("raw_actionable_count"), int) and summary.get("raw_actionable_count") != actionable_count:
            errors.append(f"crossbook artifact {artifact_id} summary raw_actionable_count does not match actionable edges")
        if isinstance(summary.get("relay_actionable_count"), int) and summary.get("relay_actionable_count") > actionable_count:
            errors.append(f"crossbook artifact {artifact_id} summary relay_actionable_count exceeds actionable edges")
        if isinstance(summary.get("qualified_play_count"), int) and summary.get("qualified_play_count") != int(summary.get("relay_actionable_count") or 0):
            errors.append(f"crossbook artifact {artifact_id} summary qualified_play_count does not match relay_actionable_count")
    _validate_path_a_recompute(artifact_id, artifact_payload, manifest, manifest_path, actionable_edges, errors)


def _strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(ch)
    )


def _report_path_from_manifest(manifest: dict[str, Any], manifest_path: Path | None) -> Path | None:
    raw_path = str(manifest.get("report_path") or "").strip()
    if not raw_path:
        metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
        raw_path = str(metadata.get("report_path") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute() and manifest_path is not None:
        path = (manifest_path.parent / path).resolve()
    return path


def _report_market_lines(report_text: str, market_key: str) -> list[str]:
    needles = ("ah", "asian") if market_key == "spreads" else ("totals", "total")
    lines = []
    for raw_line in report_text.splitlines():
        line = _strip_accents(raw_line).lower()
        if "pinnacle" not in line:
            continue
        if not any(needle in line for needle in needles):
            continue
        if "%" not in line:
            continue
        lines.append(raw_line)
    return lines


def _outcome_aliases(outcome: str) -> set[str]:
    base = str(outcome).split("@", 1)[0].strip().lower()
    normalized = _strip_accents(base)
    aliases = {base, normalized}
    if len(normalized) >= 3:
        aliases.add(normalized[:3])
    if normalized == "curacao":
        aliases.update({"cur", "cuw"})
    return {alias for alias in aliases if alias}


def _extract_report_pct(line: str, aliases: set[str]) -> float | None:
    normalized = _strip_accents(line).lower()
    for alias in sorted(aliases, key=len, reverse=True):
        pattern = rf"\b{re.escape(alias)}\b[^%\n]{{0,50}}?([0-9]+(?:\.[0-9]+)?)%"
        match = re.search(pattern, normalized)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


def _validate_report_text_market_probabilities(
    manifest: dict[str, Any],
    artifact_payloads_by_cap: dict[str, dict[str, Any]],
    manifest_path: Path | None,
    errors: list[str],
) -> None:
    report_path = _report_path_from_manifest(manifest, manifest_path)
    if report_path is None or not report_path.exists():
        return
    crossbook = artifact_payloads_by_cap.get("path_a_crossbook")
    if not isinstance(crossbook, dict):
        return
    markets = crossbook.get("markets") if isinstance(crossbook.get("markets"), dict) else {}
    try:
        report_text = report_path.read_text(encoding="utf-8")
    except Exception as exc:
        errors.append(f"report text probability audit could not read report_path: {exc}")
        return

    for market_key, label in (("spreads", "spreads"), ("totals", "totals")):
        market = markets.get(market_key)
        if not isinstance(market, dict) or market.get("status") != "ok":
            continue
        method = str(market.get("devig_primary") or "shin")
        fair_by_method = market.get("fair_probs") if isinstance(market.get("fair_probs"), dict) else {}
        probs = fair_by_method.get(method)
        if not isinstance(probs, dict) or not probs:
            continue
        lines = _report_market_lines(report_text, market_key)
        if not lines:
            continue
        joined = " ".join(lines)
        for outcome, probability in probs.items():
            expected_pct = float(probability) * 100.0
            actual_pct = _extract_report_pct(joined, _outcome_aliases(str(outcome)))
            if actual_pct is None:
                continue
            if abs(actual_pct - expected_pct) > 0.15:
                errors.append(
                    f"report text {label} probability for {outcome}={actual_pct:.1f}% "
                    f"does not match artifact {method}={expected_pct:.1f}%"
                )


def _validate_mechanism_audit_artifact(
    payload: dict[str, Any],
    artifact_payloads_by_cap: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    artifact_id = payload.get("artifact_id") or payload.get("source_manifest_id") or "mechanism_audit"
    if str(payload.get("audit_contract", "")).strip() != MECHANISM_AUDIT_CONTRACT:
        errors.append(f"mechanism_audit artifact {artifact_id} requires audit_contract={MECHANISM_AUDIT_CONTRACT}")

    manifest_final_status = str(payload.get("manifest_final_status", "")).strip().lower()
    required_final_status = str(payload.get("required_final_status", "")).strip().lower()
    if not manifest_final_status:
        errors.append(f"mechanism_audit artifact {artifact_id} missing manifest_final_status")
    if not required_final_status:
        errors.append(f"mechanism_audit artifact {artifact_id} missing required_final_status")
    elif manifest_final_status and manifest_final_status != required_final_status:
        errors.append(
            f"mechanism_audit requires final_status={required_final_status}, "
            f"but manifest has final_status={manifest_final_status}"
        )

    mechanisms = payload.get("mechanisms")
    if not isinstance(mechanisms, dict) or not mechanisms:
        errors.append(f"mechanism_audit artifact {artifact_id} requires mechanisms object")
        mechanisms = {}
    for required_name in ("path_a_crossbook", "path_b_model_diagnostic", "path_c_consistency"):
        mechanism = mechanisms.get(required_name)
        if not isinstance(mechanism, dict):
            errors.append(f"mechanism_audit missing mechanism {required_name}")
            continue
        status = str(mechanism.get("status", "")).strip()
        if status not in {"COMPLETE", "BLOCKED", "PARTIAL"}:
            errors.append(f"mechanism_audit mechanism {required_name} has invalid status {status}")
        if not isinstance(mechanism.get("required_for_complete"), bool):
            errors.append(f"mechanism_audit mechanism {required_name} missing boolean required_for_complete")

    path_a = mechanisms.get("path_a_crossbook") if isinstance(mechanisms.get("path_a_crossbook"), dict) else {}
    crossbook_payload = artifact_payloads_by_cap.get("path_a_crossbook")
    if path_a.get("status") == "COMPLETE":
        if not crossbook_payload:
            errors.append("mechanism_audit marks path_a_crossbook COMPLETE without crossbook artifact")
        else:
            summary = crossbook_payload.get("summary") or crossbook_payload.get("scan_summary") or {}
            if path_a.get("quotes_scanned") != summary.get("quotes_scanned"):
                errors.append("mechanism_audit path_a quotes_scanned does not match crossbook summary")
            for key in ("edge_count", "noise_edge_count", "actionable_count", "raw_actionable_count", "relay_actionable_count", "qualified_play_count"):
                if path_a.get(key) != summary.get(key):
                    errors.append(f"mechanism_audit path_a {key} does not match crossbook summary")
    elif crossbook_payload and path_a.get("status") == "BLOCKED":
        errors.append("mechanism_audit marks path_a_crossbook BLOCKED while crossbook artifact exists")

    path_c = mechanisms.get("path_c_consistency") if isinstance(mechanisms.get("path_c_consistency"), dict) else {}
    has_path_c = "path_c_consistency" in artifact_payloads_by_cap
    if path_c.get("status") == "COMPLETE" and not has_path_c:
        errors.append("mechanism_audit marks path_c_consistency COMPLETE without consistency artifact")
    if path_c.get("status") == "BLOCKED" and has_path_c:
        errors.append("mechanism_audit marks path_c_consistency BLOCKED while consistency artifact exists")

    role_engine = mechanisms.get("role_engine") if isinstance(mechanisms.get("role_engine"), dict) else {}
    role_engine_payload = artifact_payloads_by_cap.get("role_engine")
    role_status = str(role_engine.get("status", "")).strip()
    if role_status.startswith("COMPLETE") and not role_engine_payload:
        errors.append("mechanism_audit marks role_engine COMPLETE without role_engine artifact")
    if role_engine_payload and role_status == "BLOCKED":
        errors.append("mechanism_audit marks role_engine BLOCKED while role_engine artifact exists")
    if role_status.startswith("COMPLETE") and role_engine_payload:
        conclusions = role_engine_payload.get("role_conclusions") if isinstance(role_engine_payload.get("role_conclusions"), list) else []
        if role_engine.get("conclusion_count") != len(conclusions):
            errors.append("mechanism_audit role_engine conclusion_count does not match role_engine artifact")

    decisions = payload.get("hypothesis_decisions")
    if not isinstance(decisions, list):
        errors.append(f"mechanism_audit artifact {artifact_id} requires hypothesis_decisions list")
        decisions = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            errors.append(f"mechanism_audit hypothesis_decisions[{index}] must be an object")
            continue
        enum = str(decision.get("decision", "")).strip()
        if enum not in MECHANISM_DECISION_ENUMS:
            errors.append(f"mechanism_audit hypothesis_decisions[{index}] invalid decision enum {enum}")
        for key in ("source", "subject", "evidence"):
            if not str(decision.get(key, "")).strip():
                errors.append(f"mechanism_audit hypothesis_decisions[{index}] missing {key}")

    if crossbook_payload:
        expected_path_a_decisions = []
        for market_result in (crossbook_payload.get("markets") or {}).values():
            if not isinstance(market_result, dict):
                continue
            for edge in market_result.get("edges", []):
                if isinstance(edge, dict):
                    expected_path_a_decisions.append((edge.get("book"), edge.get("market_key"), edge.get("outcome")))
        actual_path_a_decisions = {
            (decision.get("book"), decision.get("market_key"), decision.get("outcome"))
            for decision in decisions
            if isinstance(decision, dict) and decision.get("source") == "path_a_crossbook"
        }
        for expected in expected_path_a_decisions:
            if expected not in actual_path_a_decisions:
                errors.append(
                    "mechanism_audit missing Path A decision for "
                    f"{expected[0]} {expected[1]} {expected[2]}"
                )


def _validate_role_engine_artifact(payload: dict[str, Any], errors: list[str]) -> None:
    artifact_id = payload.get("artifact_id") or "role_engine"
    if str(payload.get("engine_contract", "")).strip() != ROLE_ENGINE_CONTRACT:
        errors.append(f"role_engine artifact {artifact_id} requires engine_contract={ROLE_ENGINE_CONTRACT}")
    if str(payload.get("engine_version", "")).strip() != "deterministic_v1":
        errors.append(f"role_engine artifact {artifact_id} requires engine_version=deterministic_v1")
    conclusions = payload.get("role_conclusions")
    if not isinstance(conclusions, list) or not conclusions:
        errors.append(f"role_engine artifact {artifact_id} requires non-empty role_conclusions")
        return
    seen_roles: set[str] = set()
    for index, conclusion in enumerate(conclusions):
        if not isinstance(conclusion, dict):
            errors.append(f"role_engine role_conclusions[{index}] must be an object")
            continue
        role = str(conclusion.get("role", "")).strip()
        seen_roles.add(role)
        if role not in ROLE_ENGINE_ROLES:
            errors.append(f"role_engine role_conclusions[{index}] invalid role {role}")
        evidence_id = str(conclusion.get("evidence_id", "")).strip()
        if not evidence_id.startswith(f"role:{role}:"):
            errors.append(f"role_engine role_conclusions[{index}] invalid evidence_id {evidence_id}")
        decision = str(conclusion.get("decision", "")).strip()
        if decision not in ROLE_ENGINE_DECISION_ENUMS:
            errors.append(f"role_engine role_conclusions[{index}] invalid decision {decision}")
        actionability = str(conclusion.get("actionability", "")).strip()
        if actionability not in ROLE_ENGINE_ACTIONABILITY_ENUMS:
            errors.append(f"role_engine role_conclusions[{index}] invalid actionability {actionability}")
        if actionability == "supports_path_a" and decision not in {"CONFIRMED", "DIAGNOSTIC_ONLY"}:
            errors.append(f"role_engine role_conclusions[{index}] supports_path_a requires confirmed/diagnostic decision")
        if decision == "BLOCKED" and actionability != "never_actionable":
            errors.append(f"role_engine role_conclusions[{index}] BLOCKED cannot affect actionability")
        for key in ("hypothesis_zh", "interpretation_zh", "role_label_zh"):
            if not str(conclusion.get(key, "")).strip():
                errors.append(f"role_engine role_conclusions[{index}] missing {key}")
        trigger_artifacts = conclusion.get("trigger_artifacts")
        if not isinstance(trigger_artifacts, list) or not trigger_artifacts:
            errors.append(f"role_engine role_conclusions[{index}] requires trigger_artifacts")
        artifact_sources = conclusion.get("artifact_sources")
        if not isinstance(artifact_sources, list) or not artifact_sources:
            errors.append(f"role_engine role_conclusions[{index}] requires artifact_sources")
        else:
            for source_index, source in enumerate(artifact_sources):
                if not isinstance(source, dict):
                    errors.append(f"role_engine role_conclusions[{index}] artifact_sources[{source_index}] must be an object")
                    continue
                if not str(source.get("capability", "")).strip():
                    errors.append(f"role_engine role_conclusions[{index}] artifact_sources[{source_index}] missing capability")
                if not str(source.get("artifact_id", "")).strip() and not str(source.get("path", "")).strip():
                    errors.append(f"role_engine role_conclusions[{index}] artifact_sources[{source_index}] missing artifact_id/path")
        evidence_numbers = conclusion.get("evidence_numbers")
        if not isinstance(evidence_numbers, list) or not evidence_numbers:
            errors.append(f"role_engine role_conclusions[{index}] requires evidence_numbers")
        else:
            for number_index, number in enumerate(evidence_numbers):
                if not isinstance(number, dict):
                    errors.append(f"role_engine role_conclusions[{index}] evidence_numbers[{number_index}] must be an object")
                    continue
                if not str(number.get("name", "")).strip():
                    errors.append(f"role_engine role_conclusions[{index}] evidence_numbers[{number_index}] missing name")
                if "value" not in number:
                    errors.append(f"role_engine role_conclusions[{index}] evidence_numbers[{number_index}] missing value")
    missing_roles = ROLE_ENGINE_ROLES - seen_roles
    if missing_roles:
        errors.append(f"role_engine artifact {artifact_id} missing roles: {', '.join(sorted(missing_roles))}")


def _gate_status(raw_gate: Any) -> str:
    if isinstance(raw_gate, str):
        return raw_gate.strip().lower()
    if isinstance(raw_gate, dict):
        return str(raw_gate.get("status", "")).strip().lower()
    return ""


def _validate_direct_live_contract(
    payload: dict[str, Any],
    artifacts: list[Any],
    manifest_path: Path | None,
    errors: list[str],
    warnings: list[str],
) -> None:
    completeness = str(payload.get("report_completeness", "complete")).strip().lower()
    if completeness not in {"complete", "partial"}:
        errors.append("report_completeness must be complete or partial")
    is_partial = completeness == "partial"
    final_status = str(payload.get("final_status", "")).strip().lower()
    is_incomplete = final_status == "pass_incomplete"
    if is_partial and final_status != "watch":
        errors.append("partial direct report must use final_status=watch")

    workflow_contract = str(payload.get("workflow_contract", "")).strip()
    if workflow_contract != DIRECT_WORKFLOW_CONTRACT:
        errors.append(f"live direct manifest requires workflow_contract={DIRECT_WORKFLOW_CONTRACT}")

    direct_request_id = str(payload.get("direct_request_id", "")).strip()
    direct_request_path_raw = str(payload.get("direct_request_path", "")).strip()
    if not direct_request_id:
        errors.append("live direct manifest requires direct_request_id")
    if not direct_request_path_raw:
        errors.append("live direct manifest requires direct_request_path")
    else:
        direct_request_path = resolve_artifact_path(direct_request_path_raw, manifest_path)
        if not direct_request_path.exists():
            errors.append(f"direct_request_path does not exist: {direct_request_path}")
        else:
            try:
                request_payload = json.loads(direct_request_path.read_text(encoding="utf-8"))
            except Exception as exc:
                errors.append(f"direct_request_path is not readable JSON: {exc}")
            else:
                if not isinstance(request_payload, dict):
                    errors.append("direct request record root must be an object")
                else:
                    if direct_request_id and str(request_payload.get("direct_request_id", "")).strip() != direct_request_id:
                        errors.append("direct_request_id does not match direct request record")
                    for required_key in ("platform", "chat_id", "request_text", "created_at_utc"):
                        if not str(request_payload.get(required_key, "")).strip():
                            errors.append(f"direct request record missing {required_key}")

    source_freshness = payload.get("source_freshness")
    if not isinstance(source_freshness, dict):
        errors.append("live direct manifest requires source_freshness object")
    else:
        sources = source_freshness.get("sources") or source_freshness.get("snapshots")
        if not isinstance(sources, list) or not sources:
            errors.append("source_freshness requires non-empty sources/snapshots list")

    gates = payload.get("analysis_gates")
    skipped_by_gate: dict[str, dict[str, Any]] = {}
    skipped_sections = payload.get("skipped_sections", [])
    if is_partial:
        if not isinstance(skipped_sections, list) or not skipped_sections:
            errors.append("partial direct report requires non-empty skipped_sections")
        elif isinstance(skipped_sections, list):
            for index, section in enumerate(skipped_sections):
                if not isinstance(section, dict):
                    errors.append(f"skipped_sections[{index}] must be an object")
                    continue
                gate = str(section.get("gate", "")).strip()
                reason = str(section.get("reason", "")).strip()
                impact = str(section.get("impact", "")).strip()
                if not gate:
                    errors.append(f"skipped_sections[{index}] missing gate")
                if not reason:
                    errors.append(f"skipped_sections[{index}] missing reason")
                if not impact:
                    errors.append(f"skipped_sections[{index}] missing impact")
                if gate:
                    skipped_by_gate[gate] = section
    elif skipped_sections:
        errors.append("complete direct report must not include skipped_sections")

    if not isinstance(gates, dict):
        errors.append("live direct manifest requires analysis_gates object")
    else:
        for gate_name in sorted(DIRECT_REQUIRED_GATES):
            status = _gate_status(gates.get(gate_name))
            if not status:
                errors.append(f"analysis_gates missing {gate_name}")
            elif status in DIRECT_SKIPPED_GATE_STATUSES:
                if not is_partial and not is_incomplete:
                    errors.append(f"analysis_gates.{gate_name} cannot be skipped in complete report")
                elif is_partial and gate_name not in skipped_by_gate:
                    errors.append(f"analysis_gates.{gate_name} skipped but missing skipped_sections entry")
            elif status not in DIRECT_OK_GATE_STATUSES:
                errors.append(f"analysis_gates.{gate_name} has non-pass status: {status}")

    capabilities: set[str] = set()
    artifact_payloads_by_cap: dict[str, dict[str, Any]] = {}
    mechanism_audit_payloads: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_payload = _load_artifact_payload(artifact, manifest_path)
        caps = _artifact_capabilities(artifact, artifact_payload)
        capabilities.update(caps)
        if artifact_payload:
            for cap in caps:
                artifact_payloads_by_cap.setdefault(cap, artifact_payload)
        if "path_a_crossbook" in caps:
            _validate_crossbook_artifact(artifact, artifact_payload, payload, manifest_path, errors)
        if "devig_1x2" in caps:
            if not artifact_payload:
                errors.append(f"devig artifact {artifact.get('artifact_id')} must be readable for three-method audit")
                continue
            methods = artifact_payload.get("devig_methods")
            if not isinstance(methods, dict) or not {"shin", "power", "multiplicative"}.issubset(methods):
                errors.append(f"devig artifact {artifact.get('artifact_id')} missing shin/power/multiplicative devig_methods")
            if not isinstance(artifact_payload.get("survives_all_methods"), bool):
                errors.append(f"devig artifact {artifact.get('artifact_id')} missing boolean survives_all_methods")
        if "mechanism_audit" in caps:
            if not artifact_payload:
                errors.append(f"mechanism_audit artifact {artifact.get('artifact_id')} must be readable JSON")
            else:
                mechanism_audit_payloads.append(artifact_payload)
        if "role_engine" in caps:
            if not artifact_payload:
                errors.append(f"role_engine artifact {artifact.get('artifact_id')} must be readable JSON")
            else:
                _validate_role_engine_artifact(artifact_payload, errors)

    for audit_payload in mechanism_audit_payloads:
        _validate_mechanism_audit_artifact(audit_payload, artifact_payloads_by_cap, errors)

    _validate_report_text_market_probabilities(payload, artifact_payloads_by_cap, manifest_path, errors)

    def audit_blocks_gate(gate: str) -> bool:
        for audit_payload in mechanism_audit_payloads:
            mechanisms = audit_payload.get("mechanisms")
            if not isinstance(mechanisms, dict):
                continue
            mechanism = mechanisms.get(gate)
            if isinstance(mechanism, dict) and mechanism.get("status") == "BLOCKED":
                return True
        return False

    missing_caps = []
    for cap in sorted(DIRECT_REQUIRED_ARTIFACT_CAPABILITIES - capabilities):
        gate = DIRECT_CAPABILITY_TO_GATE.get(cap, cap)
        status = _gate_status(gates.get(gate)) if isinstance(gates, dict) else ""
        if is_partial and status in DIRECT_SKIPPED_GATE_STATUSES and gate in skipped_by_gate:
            continue
        if is_incomplete and audit_blocks_gate(gate):
            continue
        missing_caps.append(cap)
    if missing_caps:
        errors.append(f"live direct manifest missing artifact capabilities: {', '.join(missing_caps)}")


def validate_manifest(payload: dict[str, Any], manifest_path: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    mode = str(payload.get("mode", "")).lower()
    source_quality = str(payload.get("source_quality", "")).upper()
    final_status = str(payload.get("final_status", "")).lower()
    numbers = payload.get("numbers", [])
    artifacts = payload.get("artifacts", [])

    if mode not in ALLOWED_MODES:
        errors.append("mode must be live or simulation")
    if final_status not in ALLOWED_STATUSES:
        errors.append(f"final_status must be one of {sorted(ALLOWED_STATUSES)}")
    if source_quality not in {"A", "B", "C", "D"}:
        errors.append("source_quality must be A/B/C/D")
    if not isinstance(numbers, list):
        errors.append("numbers must be a list")
        numbers = []
    if not isinstance(artifacts, list):
        errors.append("artifacts must be a list")
        artifacts = []

    artifact_index: dict[str, dict[str, Any]] = {}
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            errors.append(f"artifacts[{index}] must be an object")
            continue
        artifact_id = str(artifact.get("artifact_id", "")).strip()
        if not artifact_id:
            errors.append(f"artifacts[{index}] missing artifact_id")
            continue
        artifact_index[artifact_id] = artifact

    if mode == "simulation":
        if final_status != "simulation_only":
            errors.append("simulation mode must use final_status=simulation_only")
        if source_quality in {"A", "B"}:
            warnings.append("simulation mode should not advertise source_quality A/B")
    elif mode == "live":
        _validate_direct_live_contract(payload, artifacts, manifest_path, errors, warnings)

    _validate_timing_window(payload, errors, warnings)

    if final_status in ACTIONABLE_STATUSES and source_quality in {"C", "D"}:
        errors.append("source_quality C/D cannot support actionable final_status")

    actionable_missing_artifact = False
    uses_raw_model = False
    has_p_adj_ledger = bool(payload.get("adjustment_ledger_id"))

    for index, number in enumerate(numbers):
        if not isinstance(number, dict):
            errors.append(f"numbers[{index}] must be an object")
            continue
        kind = str(number.get("kind", "")).lower()
        if _truthy(number.get("uses_p_model_directly")):
            uses_raw_model = True
            errors.append(f"numbers[{index}] uses raw model probability directly")
        if kind in CRITICAL_NUMBER_KINDS:
            snapshot_id = str(number.get("snapshot_id", "")).strip()
            artifact_id = str(number.get("artifact_id", "")).strip()
            artifact_type = str(number.get("artifact_type", "")).strip().lower()
            if not snapshot_id:
                actionable_missing_artifact = True
                errors.append(f"numbers[{index}] {kind} missing snapshot_id")
            if not artifact_id:
                actionable_missing_artifact = True
                errors.append(f"numbers[{index}] {kind} missing artifact_id")
            if artifact_type != "devig":
                actionable_missing_artifact = True
                errors.append(f"numbers[{index}] {kind} must cite artifact_type=devig")
            if artifact_id:
                artifact = artifact_index.get(artifact_id)
                if artifact is None:
                    actionable_missing_artifact = True
                    errors.append(f"numbers[{index}] {kind} artifact_id not found in artifacts: {artifact_id}")
                else:
                    actual_type = str(artifact.get("artifact_type", "")).strip().lower()
                    script = str(artifact.get("script", "")).strip()
                    artifact_path_raw = str(artifact.get("path", "")).strip()
                    if actual_type != "devig":
                        actionable_missing_artifact = True
                        errors.append(f"artifact {artifact_id} must have artifact_type=devig")
                    if "devig.py" not in script:
                        actionable_missing_artifact = True
                        errors.append(f"artifact {artifact_id} must cite script=devig.py")
                    if not artifact_path_raw:
                        actionable_missing_artifact = True
                        errors.append(f"artifact {artifact_id} missing path")
                    else:
                        artifact_path = resolve_artifact_path(artifact_path_raw, manifest_path)
                        if not artifact_path.exists():
                            actionable_missing_artifact = True
                            errors.append(f"artifact {artifact_id} path does not exist: {artifact_path}")
                        else:
                            try:
                                artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
                            except Exception as exc:
                                actionable_missing_artifact = True
                                errors.append(f"artifact {artifact_id} is not readable JSON: {exc}")
                            else:
                                if not isinstance(artifact_payload, dict):
                                    actionable_missing_artifact = True
                                    errors.append(f"artifact {artifact_id} JSON root must be an object")
                                elif "odds_unit_contract" not in artifact_payload:
                                    actionable_missing_artifact = True
                                    errors.append(f"artifact {artifact_id} missing odds_unit_contract from devig.py output")
        if kind in {"scalar_ev", "p_adj_edge", "kelly", "robust_ev"}:
            probability_source = str(number.get("probability_source", "")).lower()
            if probability_source not in {"p_adj", "adjustment_ledger"}:
                errors.append(f"numbers[{index}] {kind} must use probability_source=p_adj or adjustment_ledger")

    if final_status in ACTIONABLE_STATUSES and not has_p_adj_ledger:
        errors.append("actionable final_status requires adjustment_ledger_id")
    if uses_raw_model and final_status in ACTIONABLE_STATUSES:
        errors.append("raw model edge cannot support actionable final_status")

    # ── sigma_total provenance ──────────────────────────────────────────
    # sigma_total must be dynamically computed from components, never hardcoded.
    # adjustment_uncertainty_pct must be derived from the adjustment_ledger.
    adjustment_ledger = payload.get("adjustment_ledger", [])
    if isinstance(adjustment_ledger, list):
        ledger_adj_uncertainty = math.sqrt(
            sum(float(e.get("uncertainty_pct", 0.0)) ** 2 for e in adjustment_ledger)
        )
    else:
        ledger_adj_uncertainty = None

    for number in numbers:
        if not isinstance(number, dict):
            continue
        if str(number.get("kind", "")).lower() != "uncertainty":
            continue
        components = number.get("components")
        if not isinstance(components, dict):
            errors.append("sigma_total entry missing components dict")
            continue
        declared_model = float(components.get("model_uncertainty_pct", 0.0))
        declared_source = float(components.get("source_uncertainty_pct", 0.0))
        declared_adj = float(components.get("adjustment_uncertainty_pct", 0.0))
        declared_sigma = float(number.get("value", 0.0))

        # 1. adjustment_uncertainty must match adjustment_ledger
        if ledger_adj_uncertainty is not None and abs(declared_adj - ledger_adj_uncertainty) > 1e-4:
            errors.append(
                f"adjustment_uncertainty_pct {declared_adj:.4f} "
                f"does not match adjustment_ledger sqrt(Σ(uncertainty_pct²)) = "
                f"{ledger_adj_uncertainty:.4f}"
            )

        # 2. sigma_total.value must equal sqrt(Σ(component²))
        expected_sigma = math.sqrt(
            declared_model ** 2 + declared_source ** 2 + declared_adj ** 2
        )
        if abs(declared_sigma - expected_sigma) > 1e-4:
            errors.append(
                f"sigma_total {declared_sigma:.4f} does not match "
                f"sqrt(model²+source²+adj²) = {expected_sigma:.4f} "
                f"(model={declared_model}, source={declared_source}, adj={declared_adj})"
            )
        break  # only one uncertainty entry expected

    source_quality_cap = source_quality
    actionable_allowed = True
    if actionable_missing_artifact:
        source_quality_cap = "C"
        actionable_allowed = False
    if mode == "live" and str(payload.get("report_completeness", "complete")).strip().lower() == "partial":
        source_quality_cap = "C"
        actionable_allowed = False
    if mode == "simulation":
        actionable_allowed = False

    valid = not errors
    return {
        "valid": valid,
        "actionable_allowed": actionable_allowed and final_status in ACTIONABLE_STATUSES,
        "source_quality_cap": source_quality_cap,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        print(json.dumps({"valid": False, "errors": ["manifest root must be an object"]}, ensure_ascii=False))
        return 1
    result = validate_manifest(payload, args.manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
