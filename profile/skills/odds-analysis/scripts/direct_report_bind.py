#!/usr/bin/env python3
"""Bind a cached WC26 report/manifest to the current direct Telegram request."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DIRECT_REQUEST_RECORD_PATH = SCRIPT_DIR / "direct_request_record.py"
spec = importlib.util.spec_from_file_location("direct_request_record", DIRECT_REQUEST_RECORD_PATH)
direct_request_record = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(direct_request_record)


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def replace_header_value(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    replaced = False
    insert_at = 1 if lines else 0
    for index, line in enumerate(lines[:80]):
        stripped = line.strip()
        if stripped.startswith("workflow_contract:"):
            insert_at = index + 1
        if stripped.startswith(f"{key}:"):
            lines[index] = f"{key}: {value}"
            replaced = True
    if not replaced:
        lines.insert(insert_at, f"{key}: {value}")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def update_manifest(manifest_path: Path, direct_request_id: str, direct_request_path: Path, report_path: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    manifest["direct_request_id"] = direct_request_id
    manifest["direct_request_path"] = str(direct_request_path)
    manifest["report_path"] = str(report_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def update_report_header(report_path: Path, direct_request_id: str, direct_request_path: Path, manifest_path: Path) -> None:
    text = report_path.read_text(encoding="utf-8")
    text = replace_header_value(text, "direct_request_id", direct_request_id)
    text = replace_header_value(text, "direct_request_path", str(direct_request_path))
    text = replace_header_value(text, "artifact_manifest_path", str(manifest_path))
    report_path.write_text(text, encoding="utf-8")


def bind(args: argparse.Namespace) -> dict[str, Any]:
    direct_request_path = args.direct_request_path.resolve()
    manifest_path = args.manifest.resolve()
    report_path = args.report.resolve()
    if not direct_request_record.is_valid_manifest_path(str(manifest_path), args.workspace):
        raise ValueError(f"--manifest is not a valid manifest JSON: {manifest_path}")
    if not direct_request_record.is_valid_report_path(str(report_path), args.workspace):
        raise ValueError(f"--report is not a markdown report: {report_path}")
    request_payload = load_json(direct_request_path)
    direct_request_id = str(request_payload.get("direct_request_id", "")).strip()
    if not direct_request_id:
        raise ValueError("direct request record missing direct_request_id")

    update_manifest(manifest_path, direct_request_id, direct_request_path, report_path)
    update_report_header(report_path, direct_request_id, direct_request_path, manifest_path)

    update_args = argparse.Namespace(
        workspace=args.workspace,
        sessions_path=args.sessions_path,
        from_latest_session=False,
        update_path=direct_request_path,
        platform="telegram",
        chat_id="",
        message_id="",
        user_id="",
        user_name="",
        request_text="",
        match_id=args.match_id,
        match_label=args.match_label,
        created_at_utc=None,
        direct_request_id=direct_request_id,
        report_path=str(report_path),
        manifest_path=str(manifest_path),
        status=args.status,
        cache_mode=args.cache_mode,
        source_snapshot_id=args.source_snapshot_id,
        report_id=args.report_id,
        api_refresh_performed=args.api_refresh_performed,
        header_lines=False,
    )
    result = direct_request_record.update_record(update_args)
    return {
        "direct_request_id": direct_request_id,
        "direct_request_path": str(direct_request_path),
        "manifest_path": str(manifest_path),
        "report_path": str(report_path),
        "record": result["record"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=direct_request_record.DEFAULT_WORKSPACE)
    parser.add_argument("--sessions-path", type=Path, default=direct_request_record.DEFAULT_SESSIONS_PATH)
    parser.add_argument("--direct-request-path", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--status", default="completed_cached")
    parser.add_argument("--cache-mode", default="reuse_existing_report")
    parser.add_argument("--source-snapshot-id", default="")
    parser.add_argument("--report-id", default="")
    parser.add_argument("--match-id", default="")
    parser.add_argument("--match-label", default="")
    parser.add_argument("--api-refresh-performed", default="false")
    args = parser.parse_args()
    print(json.dumps(bind(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
