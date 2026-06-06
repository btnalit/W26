#!/usr/bin/env python3
"""Create or update a WC26 direct Telegram request record.

The direct gateway does not create Kanban tasks. This record is the replacement
join key for report audit, Telegram reply traceability, and post-match grading.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_WORKSPACE = Path("/hermesdata/worldcup-2026-handicap")
PROFILE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SESSIONS_PATH = PROFILE_ROOT / "sessions" / "sessions.json"
COMPLETED_STATUSES = {"completed", "completed_cached"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_request_id(platform: str, chat_id: str, message_id: str, request_text: str, created_at_utc: str) -> str:
    raw = "|".join([platform, chat_id, message_id, request_text, created_at_utc])
    return "direct:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


def latest_session_origin(sessions_path: Path, platform: str, chat_id: str = "") -> dict[str, Any]:
    payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"sessions root must be an object: {sessions_path}")

    candidates: list[dict[str, Any]] = []
    for session in payload.values():
        if not isinstance(session, dict):
            continue
        origin = session.get("origin")
        if not isinstance(origin, dict):
            continue
        if platform and str(origin.get("platform", "")).strip() != platform:
            continue
        if chat_id and str(origin.get("chat_id", "")).strip() != chat_id:
            continue
        candidates.append(session)
    if not candidates:
        raise ValueError(f"no matching {platform} session found in {sessions_path}")
    candidates.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    origin = candidates[0].get("origin")
    return origin if isinstance(origin, dict) else {}


def apply_session_origin(args: argparse.Namespace) -> None:
    if not args.from_latest_session:
        return
    explicit_message_id = bool(str(args.message_id or "").strip())
    origin = latest_session_origin(args.sessions_path, args.platform, args.chat_id or "")
    args.platform = str(origin.get("platform") or args.platform or "telegram")
    args.chat_id = str(origin.get("chat_id") or args.chat_id or "")
    if explicit_message_id:
        args.message_id = str(args.message_id)
        args.message_id_source = "explicit"
        args.message_id_exact = True
    else:
        args.message_id = "unknown"
        args.message_id_source = "session_unreliable"
        args.message_id_exact = False
    args.user_id = str(origin.get("user_id") or args.user_id or "")
    args.user_name = str(origin.get("user_name") or args.user_name or origin.get("chat_name") or "")


def normalize_message_id(args: argparse.Namespace) -> None:
    raw = str(args.message_id or "").strip()
    if raw:
        args.message_id = raw
        if not hasattr(args, "message_id_source"):
            args.message_id_source = "explicit"
        if not hasattr(args, "message_id_exact"):
            args.message_id_exact = args.message_id != "unknown" and args.message_id_source == "explicit"
        return
    args.message_id = "unknown"
    args.message_id_source = getattr(args, "message_id_source", "") or "unavailable"
    args.message_id_exact = False


def enrich_record(record: dict[str, Any], args: argparse.Namespace, *, created: bool) -> dict[str, Any]:
    status = args.status or record.get("status") or "received"
    record["status"] = status
    api_refresh = parse_bool(args.api_refresh_performed)
    optional_fields = {
        "cache_mode": args.cache_mode,
        "source_snapshot_id": args.source_snapshot_id,
        "report_id": args.report_id,
    }
    for key, value in optional_fields.items():
        if value not in (None, ""):
            record[key] = value
    if api_refresh is not None:
        record["api_refresh_performed"] = api_refresh
    if status in COMPLETED_STATUSES and not record.get("completed_at_utc"):
        record["completed_at_utc"] = utc_now()
    if not created:
        record["updated_at_utc"] = utc_now()
    return record


def resolve_output_path(raw_path: str, workspace: Path) -> Path:
    raw = str(raw_path or "").strip()
    path = Path(raw)
    if path.is_absolute():
        return path
    if raw.replace("\\", "/").startswith("reports/"):
        return workspace / raw
    return path


def is_valid_manifest_path(raw_path: str, workspace: Path) -> bool:
    if not str(raw_path or "").strip():
        return True
    path = resolve_output_path(raw_path, workspace)
    if not path.exists() or not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    if path.name.startswith("manifest-"):
        return True
    return any(
        payload.get(key)
        for key in ("manifest_id", "workflow_contract", "artifacts", "analysis_gates")
    )


def is_valid_report_path(raw_path: str, workspace: Path) -> bool:
    if not str(raw_path or "").strip():
        return True
    path = resolve_output_path(raw_path, workspace)
    return path.exists() and path.is_file() and path.suffix.lower() == ".md"


def validate_output_paths(args: argparse.Namespace) -> None:
    if not is_valid_manifest_path(args.manifest_path, args.workspace):
        raise ValueError(f"--manifest-path is not a valid manifest JSON: {args.manifest_path}")
    if not is_valid_report_path(args.report_path, args.workspace):
        raise ValueError(f"--report-path is not a markdown report: {args.report_path}")


def write_record(args: argparse.Namespace) -> dict[str, Any]:
    apply_session_origin(args)
    normalize_message_id(args)
    validate_output_paths(args)
    if not args.chat_id:
        raise ValueError("--chat-id is required unless --from-latest-session can resolve it")
    if not args.request_text:
        raise ValueError("--request-text is required when creating a direct request record")
    created_at_utc = args.created_at_utc or utc_now()
    direct_request_id = args.direct_request_id or stable_request_id(
        args.platform,
        args.chat_id,
        args.message_id or "",
        args.request_text,
        created_at_utc,
    )
    record = {
        "schema_version": "wc26.direct_request.v1",
        "direct_request_id": direct_request_id,
        "platform": args.platform,
        "chat_id": args.chat_id,
        "message_id": args.message_id,
        "message_id_source": getattr(args, "message_id_source", "unknown"),
        "message_id_exact": bool(getattr(args, "message_id_exact", False)),
        "user_id": args.user_id,
        "user_name": args.user_name,
        "request_text": args.request_text,
        "match_id": args.match_id,
        "match_label": args.match_label,
        "created_at_utc": created_at_utc,
        "report_path": args.report_path,
        "manifest_path": args.manifest_path,
    }
    record = enrich_record(record, args, created=True)
    date_key = created_at_utc[:10]
    out_dir = args.workspace / "direct_requests" / date_key
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{direct_request_id.replace(':', '-')}.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"direct_request_id": direct_request_id, "direct_request_path": str(out_path), "record": record}


def update_record(args: argparse.Namespace) -> dict[str, Any]:
    should_update_message = bool(getattr(args, "from_latest_session", False) or str(args.message_id or "").strip())
    apply_session_origin(args)
    if should_update_message:
        normalize_message_id(args)
    validate_output_paths(args)
    out_path = args.update_path
    record = json.loads(out_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError(f"direct request record root must be an object: {out_path}")
    if args.direct_request_id and record.get("direct_request_id") != args.direct_request_id:
        raise ValueError("direct_request_id does not match update record")

    updates = {
        "platform": args.platform,
        "chat_id": args.chat_id,
        "user_id": args.user_id,
        "user_name": args.user_name,
        "request_text": args.request_text,
        "match_id": args.match_id,
        "match_label": args.match_label,
        "report_path": args.report_path,
        "manifest_path": args.manifest_path,
    }
    if should_update_message:
        updates["message_id"] = args.message_id
        updates["message_id_source"] = getattr(args, "message_id_source", "unknown")
        updates["message_id_exact"] = bool(getattr(args, "message_id_exact", False))
    for key, value in updates.items():
        if value not in (None, ""):
            record[key] = value
    record = enrich_record(record, args, created=False)
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "direct_request_id": str(record.get("direct_request_id", "")),
        "direct_request_path": str(out_path),
        "record": record,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--sessions-path", type=Path, default=DEFAULT_SESSIONS_PATH)
    parser.add_argument("--from-latest-session", action="store_true")
    parser.add_argument("--update-path", type=Path)
    parser.add_argument("--platform", default="telegram")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--message-id", default="")
    parser.add_argument("--user-id", default="")
    parser.add_argument("--user-name", default="")
    parser.add_argument("--request-text", default="")
    parser.add_argument("--match-id", default="")
    parser.add_argument("--match-label", default="")
    parser.add_argument("--created-at-utc")
    parser.add_argument("--direct-request-id")
    parser.add_argument("--report-path", default="")
    parser.add_argument("--manifest-path", default="")
    parser.add_argument("--status")
    parser.add_argument("--cache-mode", default="")
    parser.add_argument("--source-snapshot-id", default="")
    parser.add_argument("--report-id", default="")
    parser.add_argument("--api-refresh-performed")
    parser.add_argument("--header-lines", action="store_true")
    args = parser.parse_args()

    result = update_record(args) if args.update_path else write_record(args)
    if args.header_lines:
        print(f"direct_request_id: {result['direct_request_id']}")
        print(f"direct_request_path: {result['direct_request_path']}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
