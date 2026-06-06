#!/usr/bin/env python3
"""Guard a WC26 Markdown report before worker completion or main relay."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPORT_CONTRACT_PATH = SCRIPT_DIR / "report_contract.py"
spec = importlib.util.spec_from_file_location("report_contract", REPORT_CONTRACT_PATH)
report_contract = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(report_contract)

FIXTURE_REGISTRY_PATH = SCRIPT_DIR / "fixture_registry.py"
fixture_registry_spec = importlib.util.spec_from_file_location("fixture_registry", FIXTURE_REGISTRY_PATH)
fixture_registry = importlib.util.module_from_spec(fixture_registry_spec)
assert fixture_registry_spec.loader is not None
fixture_registry_spec.loader.exec_module(fixture_registry)


HEADER_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")
YAML_BLOCK_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
DIRECT_COMPLETED_STATUSES = {"completed", "completed_cached"}


def parse_report_header(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw in text.splitlines()[:80]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "---" and fields:
            break
        match = HEADER_RE.match(line)
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


def _load_json_safe(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _venue_matches(report_venue: str, registry_venue: str) -> bool:
    report_norm = fixture_registry.normalize_name(report_venue)
    registry_norm = fixture_registry.normalize_name(registry_venue)
    if not report_norm or not registry_norm:
        return False
    return (
        report_norm == registry_norm
        or report_norm in registry_norm
        or registry_norm in report_norm
    )


def _resolve_workspace_path(raw_path: str, workspace_root: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (workspace_root / path).resolve()


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve(strict=False) == right.resolve(strict=False)


def _validate_direct_request_backlink(
    record_path: Path,
    direct_request_id: str,
    report_path: Path,
    manifest_path: Path | None,
    workspace_root: Path,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any] | None:
    if not record_path.exists():
        errors.append(f"direct request record does not exist: {record_path}")
        return None
    request_payload = _load_json_safe(record_path)
    if not isinstance(request_payload, dict):
        errors.append(f"direct request record is not readable JSON: {record_path}")
        return None

    if str(request_payload.get("direct_request_id", "")).strip() != direct_request_id:
        errors.append("direct_request_id header does not match direct request record")

    platform = str(request_payload.get("platform", "")).strip().lower()
    required_keys = ["platform", "chat_id", "request_text", "created_at_utc"]
    if platform == "telegram":
        required_keys.extend(["message_id", "user_id"])
    for key in required_keys:
        if not str(request_payload.get(key, "")).strip():
            errors.append(f"direct request record missing {key}")
    if platform == "telegram":
        message_id = str(request_payload.get("message_id", "")).strip().lower()
        message_source = str(request_payload.get("message_id_source", "")).strip()
        if message_id == "unknown":
            warnings.append(f"telegram direct request message_id is unknown ({message_source or 'no source'}); exact Telegram message binding unavailable")
        elif request_payload.get("message_id_exact") is False:
            warnings.append(f"telegram direct request message_id is not marked exact ({message_source or 'unknown source'})")

    status = str(request_payload.get("status", "")).strip().lower()
    if status not in DIRECT_COMPLETED_STATUSES:
        errors.append("direct request record status must be completed or completed_cached before relay")

    report_raw = str(request_payload.get("report_path", "")).strip()
    if not report_raw:
        errors.append("direct request record missing report_path")
    else:
        linked_report = _resolve_workspace_path(report_raw, workspace_root)
        if not _same_path(linked_report, report_path):
            errors.append("direct request record report_path does not match report")
        if not linked_report.exists():
            errors.append(f"direct request record report_path does not exist: {linked_report}")

    manifest_raw = str(request_payload.get("manifest_path", "")).strip()
    if not manifest_raw:
        errors.append("direct request record missing manifest_path")
    else:
        linked_manifest = _resolve_workspace_path(manifest_raw, workspace_root)
        if manifest_path is not None and not _same_path(linked_manifest, manifest_path):
            errors.append("direct request record manifest_path does not match manifest")
        if not linked_manifest.exists():
            errors.append(f"direct request record manifest_path does not exist: {linked_manifest}")

    if status == "completed_cached" and not str(request_payload.get("cache_mode", "")).strip():
        errors.append("completed_cached direct request record requires cache_mode")
    if "api_refresh_performed" not in request_payload:
        errors.append("direct request record missing api_refresh_performed")
    elif not isinstance(request_payload.get("api_refresh_performed"), bool):
        errors.append("direct request record api_refresh_performed must be boolean")

    return request_payload


def _extract_official_facts_yaml(text: str) -> dict[str, str]:
    """Extract key:value pairs from the Official Match Facts ```yaml block."""
    match = YAML_BLOCK_RE.search(text)
    if not match:
        return {}
    facts: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        facts[key.strip()] = val.strip().strip("'\"").strip()
    return facts


def validate_report(report_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    text = report_path.read_text(encoding="utf-8", errors="ignore")
    header = parse_report_header(text)
    mode = header.get("mode", "").strip().lower()
    final_status = header.get("final_status", "").strip().lower()
    artifact_status = header.get("artifact_contract_status", "").strip().lower()
    guard_status = header.get("report_guard_status", "").strip().lower()
    manifest_raw = header.get("artifact_manifest_path", "").strip()
    direct_request_id = header.get("direct_request_id", "").strip()
    direct_request_path = header.get("direct_request_path", "").strip()

    # Workspace root = reports/match/ → reports/ → workspace/
    workspace_root = report_path.parent.parent.parent

    if mode not in {"live", "simulation"}:
        errors.append("report mode must be live or simulation")
    if mode == "simulation" and final_status != "simulation_only":
        errors.append("simulation report must use final_status=simulation_only")
    if artifact_status != "pass":
        errors.append("artifact_contract_status must be pass before report relay/completion")
    if guard_status != "pass":
        errors.append("report_guard_status must be pass in a relay-ready report header")
    if not manifest_raw:
        errors.append("artifact_manifest_path is required")
    if mode == "live":
        if not direct_request_id:
            errors.append("live report header requires direct_request_id")
        if not direct_request_path:
            errors.append("live report header requires direct_request_path")

    for match in re.finditer(r"(?im)^report_guard_status:\s*([^\s]+)", text):
        if match.group(1).strip().lower() != "pass":
            errors.append("all report_guard_status entries in report body/header must be pass")
            break
    for match in re.finditer(r"(?im)^artifact_contract_status:\s*([^\s]+)", text):
        if match.group(1).strip().lower() != "pass":
            errors.append("all artifact_contract_status entries in report body/header must be pass")
            break

    manifest_result = None
    manifest_payload: dict[str, Any] | None = None
    manifest_path_resolved: Path | None = None
    if manifest_raw:
        manifest_path = Path(manifest_raw)
        if not manifest_path.is_absolute():
            # frontmatter artifact_manifest_path is relative to workspace root
            manifest_path = (workspace_root / manifest_path).resolve()
        manifest_path_resolved = manifest_path
        if not manifest_path.exists():
            errors.append(f"artifact manifest does not exist: {manifest_path}")
        else:
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as exc:
                errors.append(f"artifact manifest is not readable JSON: {exc}")
            else:
                if not isinstance(payload, dict):
                    errors.append("artifact manifest root must be an object")
                else:
                    manifest_payload = payload
                    manifest_result = report_contract.validate_manifest(payload, manifest_path)
                    if not manifest_result.get("valid"):
                        errors.extend(f"manifest: {err}" for err in manifest_result.get("errors", []))
                    warnings.extend(f"manifest: {warn}" for warn in manifest_result.get("warnings", []))
                    contract_cap = str(manifest_result.get("source_quality_cap", "")).strip().upper()
                    header_cap = str(header.get("source_quality_cap", "")).strip().upper()
                    manifest_cap = str(payload.get("source_quality_cap", "")).strip().upper()
                    completeness = str(payload.get("report_completeness", header.get("report_completeness", "complete"))).strip().lower()
                    if completeness == "partial" and not header_cap:
                        errors.append("partial report header requires source_quality_cap from report_contract")
                    if header_cap and contract_cap and header_cap != contract_cap:
                        errors.append(f"source_quality_cap header {header_cap} does not match report_contract {contract_cap}")
                    if manifest_cap and contract_cap and manifest_cap != contract_cap:
                        errors.append(f"source_quality_cap manifest {manifest_cap} does not match report_contract {contract_cap}")

    if manifest_payload is not None and direct_request_id:
        manifest_direct_id = str(manifest_payload.get("direct_request_id", "")).strip()
        if manifest_direct_id and manifest_direct_id != direct_request_id:
            errors.append("direct_request_id header does not match manifest")
        manifest_direct_path = str(manifest_payload.get("direct_request_path", "")).strip()
        if direct_request_path and manifest_direct_path and manifest_direct_path != direct_request_path:
            errors.append("direct_request_path header does not match manifest")

    direct_request_payload = None
    if mode == "live" and direct_request_path:
        direct_request_record_path = _resolve_workspace_path(direct_request_path, workspace_root)
        direct_request_payload = _validate_direct_request_backlink(
            direct_request_record_path,
            direct_request_id,
            report_path,
            manifest_path_resolved,
            workspace_root,
            errors,
            warnings,
        )

    if manifest_payload is not None:
        fixture_path = workspace_root / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json"
        if fixture_path.exists():
            try:
                registry = fixture_registry.load_registry(fixture_path)
                identity_result = fixture_registry.validate_identity(registry, manifest_payload)
            except Exception as exc:
                errors.append(f"fixture identity registry check failed: {exc}")
            else:
                if not identity_result.get("valid"):
                    errors.extend(f"fixture identity: {err}" for err in identity_result.get("errors", []))
                warnings.extend(f"fixture identity: {warn}" for warn in identity_result.get("warnings", []))

    # ── Fact-lock: venue must come from official fixture snapshot ──────
    facts = _extract_official_facts_yaml(text)
    report_venue = facts.get("venue", "").strip()
    report_match_id = facts.get("football_data_id", "").strip()
    # Treat any value starting with "TBD" (case-insensitive) as truthfully unknown
    is_tbd = report_venue.upper().startswith("TBD")
    if report_venue and not is_tbd and report_match_id:
        # Search the fixture registry for this match. The registry may include
        # official FIFA venue overrides when football-data's early cache omits
        # venue; summaries and reports must not free-infer outside this seam.
        fixture_root = workspace_root / "snapshots" / "fixtures"
        fixture_path = (
            fixture_root / "football-data-wc-matches-latest.json"
            if fixture_root.exists()
            else None
        )
        if fixture_path and fixture_path.exists():
            try:
                registry = fixture_registry.load_registry(fixture_path)
                found_match = fixture_registry.resolve_fixture(registry, football_data_id=report_match_id)
                fixture_venue = str(found_match.get("venue") or "").strip()
            except Exception:
                fixture_venue = ""
            if not fixture_venue:
                errors.append(
                    f"venue fact-lock: report says '{report_venue}' but "
                    f"fixture registry has no venue for match {report_match_id}. "
                    "Both live and simulation modes must use 'TBD' when "
                    "the official fixture source lacks venue data."
                )
            elif not _venue_matches(report_venue, fixture_venue):
                errors.append(
                    f"venue fact-lock: report says '{report_venue}' but "
                    f"fixture registry says '{fixture_venue}' for match {report_match_id}."
                )

    safe_to_relay = not errors
    return {
        "valid": safe_to_relay,
        "safe_to_relay": safe_to_relay,
        "report_path": str(report_path),
        "header": header,
        "manifest_result": manifest_result,
        "direct_request_record": direct_request_payload,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()

    result = validate_report(args.report)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"valid": False, "safe_to_relay": False, "errors": [str(exc)]}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
