"""Force WC26 Telegram final replies through artifact-backed WC26 summaries.

This plugin is intentionally profile-local. It does not analyze football and
does not create report data. It only replaces a final freeform Telegram reply
with the artifact-backed projection when the reply references a WC26 report,
manifest, or direct request id.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


WORKSPACE = Path(os.environ.get("WC26_WORKSPACE", "/hermesdata/worldcup-2026-handicap"))
MAX_CHARS = os.environ.get("WC26_DIRECT_SUMMARY_MAX_CHARS", "3900")
BASE_MAX_CHARS_WITH_DEEP_RESEARCH = os.environ.get("WC26_DIRECT_SUMMARY_BASE_MAX_CHARS", "2300")
TOTAL_MAX_CHARS = int(os.environ.get("WC26_TELEGRAM_TOTAL_MAX_CHARS", "3900"))
REPORT_HEADER_SCAN_LINES = 80
RECENT_DIRECT_CONTEXT_MINUTES = int(os.environ.get("WC26_RECENT_DIRECT_CONTEXT_MINUTES", "180"))
TRAILING_PUNCTUATION = ".,;:，。；：)）]】}>"
DEEP_RESEARCH_MARKER = "WC26_DEEP_RESEARCH_FINALIZER:"
DEEP_RESEARCH_FORBIDDEN_PATTERNS = (
    r"\bp_adj\b\s*(?:改|修改|updated|override|:=|=)\s*(?!defaults|cannot)",
    r"\brelay_actionable\b\s*(?:改|修改|updated|override|:=|=)\s*1\b",
    r"\bqualified_play_count\b\s*(?:改|修改|updated|override|:=|=)\s*[1-9]\d*",
    r"(?:直接|现在|立刻).{0,8}(?:下注|下单)",
)
DEEP_RESEARCH_FRESHNESS_PATTERNS = (
    r"尚未.{0,12}(?:price\s*in|priced\s*in|定价|吸收|消化)",
    r"(?:市场|盘口).{0,12}(?:尚未|还没|没有|未).{0,12}(?:消化|吸收|price)",
    r"旧快照.{0,16}(?:官宣|announcement|announcements|前)",
    r"(?:squad|lineup).{0,4}announcements?",
    r"new\s+(?:squad|lineup|injury|market)\s+(?:announcement|news)",
    r"可能.{0,12}(?:未|尚未).{0,12}(?:price|定价|消化|吸收)",
    r"未充分.{0,8}(?:消化|price|定价)",
    r"not\s+(?:yet\s+)?priced\s+in",
    r"market.{0,20}(?:not|hasn['’]?t|have\s+not).{0,20}(?:price|digest|absorb)",
)
DEEP_RESEARCH_NEWS_CLASSES = {"injury_news", "lineup_news", "squad_news", "coach_quote", "market_news"}
WC26_REPORT_SIGNALS = (
    r"\bWC26\b",
    r"\bPath\s*A\b",
    r"\bPinnacle\b",
    r"\breport_contract\b",
    r"\breport_guard\b",
    r"\bdirect_request\b",
    r"\bNO PLAY\b",
    r"\bPASS\s*/\s*NO PLAY\b",
    r"盘口",
    r"跨书商",
    r"博弈读盘",
    r"WC26_DEEP_RESEARCH_FINALIZER:",
)


def register(ctx) -> None:
    ctx.register_hook("transform_llm_output", transform_llm_output)


def transform_llm_output(**kwargs) -> Optional[str]:
    platform = str(kwargs.get("platform") or "").lower()
    if platform and platform != "telegram":
        _log("skip_non_telegram", session_id=kwargs.get("session_id"), platform=platform)
        return None

    if _env_false("WC26_DIRECT_SUMMARY_ENFORCER_ENABLED"):
        _log("skip_disabled", session_id=kwargs.get("session_id"), platform=platform)
        return None

    response_text = str(kwargs.get("response_text") or "")
    manifest_path, report_path = resolve_summary_inputs(response_text)
    if not _is_valid_manifest_path(manifest_path) and looks_like_wc26_report(response_text):
        manifest_path, report_path = _paths_from_recent_direct_context(response_text)
    if not _is_valid_manifest_path(manifest_path):
        if looks_like_wc26_report(response_text):
            _log("block_wc26_report_without_manifest", session_id=kwargs.get("session_id"), platform=platform)
            _queue_blocked_recovery(
                "safety_block",
                source="wc26-direct-summary-enforcer",
                session_id=kwargs.get("session_id"),
                platform=platform,
                reason="wc26 report-like Telegram output missing guarded manifest/report binding",
                direct_request_ids=_direct_request_ids(response_text),
                response_excerpt=response_text[:1200],
            )
            return (
                "BLOCKED — WC26 Telegram report missing artifact manifest/report binding.\n\n"
                "这条回复看起来是 WC26 盘口报告，但没有可校验的 manifest/report/direct_request 绑定，"
                "不能 relay 自由总结。\n"
                "required: generate/bind guarded manifest + report, then re-run rich_summary.py."
            )
        _log("skip_no_manifest", session_id=kwargs.get("session_id"), platform=platform)
        return None
    if report_path is not None and not _is_existing_file(report_path):
        _log(
            "drop_invalid_report_path",
            session_id=kwargs.get("session_id"),
            platform=platform,
            manifest_path=str(manifest_path),
            report_path=str(report_path),
        )
        report_path = None

    expects_deep_research = looks_like_wc26_report(response_text)
    deep_research_section = extract_deep_research_section(response_text)
    had_deep_research_section = deep_research_section is not None
    if deep_research_section and deep_research_has_forbidden_boundary(deep_research_section):
        _log(
            "drop_deep_research_forbidden_boundary",
            session_id=kwargs.get("session_id"),
            platform=platform,
            manifest_path=str(manifest_path),
            report_path=str(report_path) if report_path else "",
        )
        deep_research_section = None
    if deep_research_section:
        contract_result = run_deep_research_contract(deep_research_section, manifest_path)
        if not contract_result["ok"]:
            sanitized_section = contract_result.get("sanitized_section")
            if isinstance(sanitized_section, str) and sanitized_section.strip():
                _log(
                    "sanitize_deep_research_contract_failed",
                    session_id=kwargs.get("session_id"),
                    platform=platform,
                    manifest_path=str(manifest_path),
                    report_path=str(report_path) if report_path else "",
                    artifact_path=contract_result.get("artifact_path") or "",
                    error=str(contract_result.get("error") or "")[:500],
                )
                deep_research_section = sanitized_section
            else:
                _log(
                    "drop_deep_research_contract_failed",
                    session_id=kwargs.get("session_id"),
                    platform=platform,
                    manifest_path=str(manifest_path),
                    report_path=str(report_path) if report_path else "",
                    artifact_path=contract_result.get("artifact_path") or "",
                    error=str(contract_result.get("error") or "")[:500],
                )
                deep_research_section = None
    elif expects_deep_research and not had_deep_research_section:
        deep_research_section = deep_research_section_from_latest_artifact(manifest_path)
        if deep_research_section:
            contract_result = run_deep_research_contract(deep_research_section, manifest_path)
            if not contract_result["ok"]:
                _log(
                    "drop_cached_deep_research_contract_failed",
                    session_id=kwargs.get("session_id"),
                    platform=platform,
                    manifest_path=str(manifest_path),
                    report_path=str(report_path) if report_path else "",
                    artifact_path=contract_result.get("artifact_path") or "",
                    error=str(contract_result.get("error") or "")[:500],
                )
                deep_research_section = deep_research_failed_section(
                    manifest_path,
                    reason="cached deep-research artifact failed contract validation",
                )
        else:
            deep_research_section = deep_research_failed_section(
                manifest_path,
                reason="no deep-research artifact found for manifest match_id",
            )

    result = run_direct_summary(
        manifest_path,
        report_path,
        max_chars=BASE_MAX_CHARS_WITH_DEEP_RESEARCH if deep_research_section else MAX_CHARS,
    )
    if result.returncode == 0 and result.stdout.strip():
        output = result.stdout.strip()
        if deep_research_section:
            output = append_deep_research_section(output, deep_research_section)
        _log(
            "replace_with_deep_research" if deep_research_section else "replace",
            session_id=kwargs.get("session_id"),
            platform=platform,
            manifest_path=str(manifest_path),
            report_path=str(report_path) if report_path else "",
        )
        return output

    reason = (result.stderr or result.stdout or "direct_summary.py failed").strip()
    _log(
        "block_summary_failed",
        session_id=kwargs.get("session_id"),
        platform=platform,
        manifest_path=str(manifest_path),
        report_path=str(report_path) if report_path else "",
        error=reason[:500],
    )
    _queue_blocked_recovery(
        "contract_mismatch",
        source="wc26-direct-summary-enforcer",
        session_id=kwargs.get("session_id"),
        platform=platform,
        reason="deterministic summary failed",
        manifest_path=str(manifest_path),
        report_path=str(report_path) if report_path else "",
        error=reason[:800],
    )
    return (
        "BLOCKED — deterministic Telegram summary failed.\n\n"
        f"manifest: {manifest_path}\n"
        f"report: {report_path or 'N/A'}\n"
        f"error: {reason[:900]}"
    )


def resolve_summary_inputs(text: str) -> tuple[Optional[Path], Optional[Path]]:
    manifest_path = _first_valid_manifest_path(_candidate_tokens(text, "manifest-", ".json"))
    report_path = _first_existing_path(_candidate_tokens(text, "", ".md", required_fragment="reports/match"))

    if not manifest_path and report_path:
        manifest_path = _manifest_from_report(report_path)
    if not manifest_path:
        manifest_path, report_path = _paths_from_direct_request(text)
    if not manifest_path:
        manifest_path = _manifest_from_match_id(text)
        if manifest_path:
            report_path = _report_from_manifest(manifest_path)

    return manifest_path, report_path


def run_direct_summary(
    manifest_path: Path,
    report_path: Optional[Path],
    *,
    max_chars: str = MAX_CHARS,
) -> subprocess.CompletedProcess[str]:
    script_dir = _profile_root() / "skills" / "odds-analysis" / "scripts"
    script = script_dir / "rich_summary.py"
    if not script.exists():
        script = script_dir / "direct_summary.py"
    cmd = [
        sys.executable,
        str(script),
        "--manifest",
        str(manifest_path),
        "--max-chars",
        str(max_chars),
    ]
    if report_path:
        cmd.extend(["--report", str(report_path)])
    return subprocess.run(cmd, text=True, capture_output=True, timeout=30)


def extract_deep_research_section(text: str) -> Optional[str]:
    if not text:
        return None
    index = text.find(DEEP_RESEARCH_MARKER)
    if index < 0:
        return None
    section = text[index:].strip()
    return section or None


def deep_research_has_forbidden_boundary(section: str) -> bool:
    for pattern in DEEP_RESEARCH_FORBIDDEN_PATTERNS:
        if re.search(pattern, section, flags=re.IGNORECASE | re.DOTALL):
            return True
    return False


def run_deep_research_contract(section: str, manifest_path: Path) -> dict[str, object]:
    artifact_path = _first_existing_path(
        _candidate_tokens(section, "deep-research", ".json", required_fragment="reports/artifacts")
    )
    if deep_research_is_completed(section) and not artifact_path:
        return {
            "ok": False,
            "artifact_path": "",
            "error": "completed Deep Research section missing deep-research artifact path",
        }
    script = _profile_root() / "skills" / "odds-analysis" / "scripts" / "deep_research_contract.py"
    if not script.exists():
        # Fail closed only for freshness-sensitive language. Harmless research
        # prose may still be appended if the validator has not been deployed.
        if deep_research_has_fresh_pricing_claim(section):
            return {
                "ok": False,
                "artifact_path": str(artifact_path) if artifact_path else "",
                "error": "deep_research_contract.py missing and section contains freshness-sensitive pricing claim",
            }
        return {"ok": True, "artifact_path": str(artifact_path) if artifact_path else "", "error": ""}

    cmd = [
        sys.executable,
        str(script),
        "--manifest",
        str(manifest_path),
        "--text-stdin",
        "--json",
    ]
    if artifact_path:
        cmd.extend(["--artifact", str(artifact_path)])
    try:
        result = subprocess.run(cmd, input=section, text=True, capture_output=True, timeout=30)
    except Exception as exc:
        return {
            "ok": False,
            "artifact_path": str(artifact_path) if artifact_path else "",
            "error": str(exc),
        }
    if result.returncode == 0:
        return {"ok": True, "artifact_path": str(artifact_path) if artifact_path else "", "error": ""}
    error = (result.stderr or result.stdout or "deep_research_contract.py failed").strip()
    sanitized_section = sanitize_deep_research_section(section, result.stdout)
    if sanitized_section:
        retry_cmd = list(cmd)
        try:
            retry = subprocess.run(retry_cmd, input=sanitized_section, text=True, capture_output=True, timeout=30)
        except Exception:
            retry = None
        if retry is not None and retry.returncode == 0:
            return {
                "ok": False,
                "artifact_path": str(artifact_path) if artifact_path else "",
                "error": error,
                "sanitized_section": sanitized_section,
            }
    return {
        "ok": False,
        "artifact_path": str(artifact_path) if artifact_path else "",
        "error": error,
    }


def deep_research_is_completed(section: str) -> bool:
    return bool(re.search(r"WC26_DEEP_RESEARCH_FINALIZER:\s*completed", section or "", flags=re.IGNORECASE))


def deep_research_has_fresh_pricing_claim(section: str) -> bool:
    return any(re.search(pattern, section or "", flags=re.IGNORECASE | re.DOTALL) for pattern in DEEP_RESEARCH_FRESHNESS_PATTERNS)


def sanitize_deep_research_section(section: str, contract_stdout: str) -> Optional[str]:
    try:
        payload = json.loads(contract_stdout or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    invalid_ids = _invalid_deep_research_source_ids(payload)
    if not invalid_ids and not payload.get("has_risky_freshness_claim"):
        return None

    kept: list[str] = []
    removed = 0
    for line in (section or "").splitlines():
        if _line_uses_any_source(line, invalid_ids) or deep_research_has_fresh_pricing_claim(line):
            removed += 1
            continue
        kept.append(line)

    material = [
        line.strip()
        for line in kept
        if line.strip()
        and not line.strip().startswith("WC26_DEEP_RESEARCH_FINALIZER:")
        and not re.search(r"deep[-_ ]research.*\.json", line, flags=re.IGNORECASE)
    ]
    if not material:
        return None

    note_scope = f"{len(invalid_ids)} 条新闻型 finding" if invalid_ids else "未定价新鲜度判断"
    note = (
        "⚠️ Deep Research 过滤: 已忽略未通过 v1.2 时间证据的新闻/未定价判断"
        f"({note_scope})；这些信息不用于判断是否已被盘口吸收。"
    )
    sanitized = "\n".join(kept).strip()
    if DEEP_RESEARCH_MARKER in sanitized:
        lines = sanitized.splitlines()
        for index, line in enumerate(lines):
            if line.startswith(DEEP_RESEARCH_MARKER):
                lines.insert(index + 1, note)
                sanitized = "\n".join(lines)
                break
    else:
        sanitized = f"{DEEP_RESEARCH_MARKER} completed\n{note}\n{sanitized}"
    return sanitized


def _invalid_deep_research_source_ids(contract_payload: dict) -> list[str]:
    invalid: list[str] = []
    sources = contract_payload.get("normalized_sources")
    if not isinstance(sources, list):
        return invalid
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id") or "")
        source_class = str(source.get("source_class") or "")
        if not source_id:
            continue
        if source_class in DEEP_RESEARCH_NEWS_CLASSES and source.get("pricing_freshness") != "post_snapshot":
            invalid.append(source_id)
    return invalid


def _line_uses_any_source(line: str, source_ids: list[str]) -> bool:
    return any(re.search(rf"(?<![A-Za-z0-9_-]){re.escape(source_id)}(?![A-Za-z0-9_-])", line or "") for source_id in source_ids)


def looks_like_wc26_report(text: str) -> bool:
    raw = text or ""
    if not raw.strip():
        return False
    if DEEP_RESEARCH_MARKER in raw:
        return True
    signal_count = 0
    for pattern in WC26_REPORT_SIGNALS:
        if re.search(pattern, raw, flags=re.IGNORECASE):
            signal_count += 1
    if signal_count >= 2:
        return True
    if re.search(r"\bWC26\s+M?\d{3}\b", raw, flags=re.IGNORECASE) and re.search(
        r"(Pinnacle|盘口|Path|NO PLAY|PASS|WATCH|跨书商|去水|亚盘)",
        raw,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def append_deep_research_section(summary: str, section: str) -> str:
    separator = "\n\n---\n\n"
    available = TOTAL_MAX_CHARS - len(summary) - len(separator)
    if available <= 80:
        return summary
    clean_section = section.strip()
    if len(clean_section) > available:
        clean_section = clean_section[: max(0, available - 16)].rstrip() + "\n...(截断)"
    return summary + separator + clean_section


def deep_research_section_from_latest_artifact(manifest_path: Path) -> Optional[str]:
    match_id = _match_id_from_manifest(manifest_path)
    if not match_id:
        return None
    artifact = _latest_deep_research_artifact(match_id)
    if not artifact:
        return None
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("match_id") or "") != match_id:
        return None
    final_view = payload.get("final_view") if isinstance(payload.get("final_view"), dict) else {}
    direction = str(final_view.get("direction_label_zh") or "").strip()
    action = str(final_view.get("action") or "").strip()
    why = str(final_view.get("why") or "").strip()
    triggers = _string_list(final_view.get("upgrade_triggers"))
    falsifiers = _string_list(final_view.get("falsifiers"))

    lines = [
        "WC26_DEEP_RESEARCH_FINALIZER: completed",
        "",
        "🔬 Deep Research 深度解读",
    ]
    if direction:
        lines.extend(["", direction])
    if action:
        lines.extend(["", f"当前动作: {action}"])
    if why:
        lines.extend(["", why])
    market_profile = _market_profile_from_manifest(manifest_path)
    profile_lines = _deep_research_market_profile_lines(market_profile)
    if profile_lines:
        lines.extend(["", "市场画像（Path C 描述性，不是下注信号）:"])
        lines.extend(profile_lines)
    if triggers:
        lines.extend(["", "升级触发:"])
        lines.extend(f"- {item}" for item in triggers[:4])
    if falsifiers:
        lines.extend(["", "撤回条件:"])
        lines.extend(f"- {item}" for item in falsifiers[:4])
    lines.extend(["", f"📁 Deep Research: {artifact}"])
    return "\n".join(lines).strip()


def deep_research_failed_section(manifest_path: Path, *, reason: str) -> str:
    match_id = _match_id_from_manifest(manifest_path) or "unknown"
    return (
        "WC26_DEEP_RESEARCH_FINALIZER: failed\n"
        f"reason: {reason}; match_id={match_id}. "
        "已发送 artifact-backed 主摘要，后置研究未静默补写。"
    )


def _match_id_from_manifest(manifest_path: Path) -> Optional[str]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("match_id", "local_match_id"):
        value = str(payload.get(key) or "").strip()
        if re.fullmatch(r"M\d{3}", value, flags=re.IGNORECASE):
            return value.upper()
    report_path = _report_from_manifest(manifest_path)
    if report_path:
        for match_id in _match_ids(report_path.name):
            return match_id
    for match_id in _match_ids(manifest_path.name):
        return match_id
    return None


def _latest_deep_research_artifact(match_id: str) -> Optional[Path]:
    artifact_dir = WORKSPACE / "reports" / "artifacts"
    if not artifact_dir.exists():
        return None
    candidates = sorted(
        artifact_dir.glob(f"deep-research-{match_id}-*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if _is_existing_file(candidate):
            return candidate
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _market_profile_from_manifest(manifest_path: Path) -> Optional[dict[str, Any]]:
    match_id = _match_id_from_manifest(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(manifest, dict):
        return None
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        raw = json.dumps(artifact, ensure_ascii=False).lower()
        if "path_c_consistency" not in raw and "consistency_triangle" not in raw:
            continue
        path = _resolve_path(str(artifact.get("path") or ""))
        if not _is_existing_file(path):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        profile = payload.get("market_profile")
        if isinstance(profile, dict):
            return profile
    return _latest_market_profile_artifact(match_id)


def _latest_market_profile_artifact(match_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not match_id:
        return None
    artifact_dir = WORKSPACE / "reports" / "artifacts"
    if not artifact_dir.exists():
        return None
    candidates = sorted(
        artifact_dir.glob(f"consistency*{match_id}*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        profile = payload.get("market_profile")
        if isinstance(profile, dict):
            return profile
    return None


def _deep_research_market_profile_lines(profile: Optional[dict[str, Any]]) -> list[str]:
    if not isinstance(profile, dict):
        return []
    status = str(profile.get("status") or "")
    confidence = str(profile.get("confidence") or "unknown")
    fit = profile.get("fit") if isinstance(profile.get("fit"), dict) else {}
    residual = fit.get("max_abs_residual_pp", "N/A")
    if status == "suppressed":
        return [
            f"- 市场画像未生成/低可信: confidence={confidence} residual={residual}pp reason={profile.get('reason', 'fit_residual')}"
        ]

    lines = [f"- confidence={confidence} residual={residual}pp"]
    most_likely = profile.get("most_likely_1x2") if isinstance(profile.get("most_likely_1x2"), dict) else {}
    if most_likely:
        lines.append(f"- 胜平负最可能: {most_likely.get('label')} {most_likely.get('prob_pct')}%")
    total_lean = profile.get("total_line_lean") if isinstance(profile.get("total_line_lean"), dict) else {}
    if total_lean:
        label = total_lean.get("label")
        pct = total_lean.get("over_pct") if total_lean.get("lean") == "over" else total_lean.get("under_pct")
        lines.append(f"- 大小球倾向: {label} {pct}%")
    top_scores = profile.get("top_scores") if isinstance(profile.get("top_scores"), list) else []
    if top_scores:
        scores = ", ".join(f"{row.get('score')} {row.get('prob_pct')}%" for row in top_scores[:3] if isinstance(row, dict))
        if scores:
            lines.append(f"- 最可能比分: {scores}")
    top_margin = profile.get("top_margin") if isinstance(profile.get("top_margin"), dict) else {}
    btts = profile.get("btts") if isinstance(profile.get("btts"), dict) else {}
    extras: list[str] = []
    if top_margin:
        extras.append(f"净胜球: {top_margin.get('label')} {top_margin.get('prob_pct')}%")
    if btts:
        btts_label = "是" if btts.get("lean") == "yes" else "否"
        btts_pct = btts.get("yes_pct") if btts.get("lean") == "yes" else btts.get("no_pct")
        extras.append(f"BTTS: {btts_label} {btts_pct}%")
    if extras:
        lines.append("- " + " | ".join(extras))
    return lines


def _paths_from_direct_request(text: str) -> tuple[Optional[Path], Optional[Path]]:
    for direct_id in _direct_request_ids(text):
        record_path = _direct_request_record_path(direct_id)
        if not record_path or not record_path.exists():
            continue
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        manifest_path = _resolve_path(str(record.get("manifest_path") or ""))
        report_path = _resolve_path(str(record.get("report_path") or ""))
        if _is_valid_manifest_path(manifest_path):
            if report_path and not _is_existing_file(report_path):
                report_path = None
            return manifest_path, report_path
        fallback_manifest = _latest_manifest_for_match(str(record.get("match_id") or ""))
        if fallback_manifest:
            fallback_report = _report_from_manifest(fallback_manifest)
            return fallback_manifest, fallback_report
    return None, None


def _paths_from_recent_direct_context(text: str) -> tuple[Optional[Path], Optional[Path]]:
    """Resolve a report-like tail message to the only recent matching request.

    This is deliberately conservative. It is only used after explicit manifest,
    report, direct id, and M-id resolution failed, and only for WC26 report-like
    text. The fallback requires a completed direct request whose match label or
    original request text matches both teams in the outgoing text. If more than
    one distinct manifest matches, it fails closed.
    """

    direct_dir = WORKSPACE / "direct_requests"
    if not direct_dir.exists():
        return None, None
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, RECENT_DIRECT_CONTEXT_MINUTES))
    candidates: list[tuple[int, float, str, Path, Optional[Path], Path]] = []
    for record_path in direct_dir.rglob("direct-*.json"):
        try:
            mtime = datetime.fromtimestamp(record_path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            continue
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "")
        if not status.startswith("completed"):
            continue
        manifest_path = _resolve_path(str(record.get("manifest_path") or ""))
        if not _is_valid_manifest_path(manifest_path):
            continue
        report_path = _resolve_path(str(record.get("report_path") or ""))
        if report_path and not _is_existing_file(report_path):
            report_path = None
        score = _direct_context_score(text, record)
        if score <= 0:
            continue
        match_key = _direct_context_key(record)
        candidates.append((score, record_path.stat().st_mtime, match_key, manifest_path, report_path, record_path))

    if not candidates:
        return None, None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    top_score = candidates[0][0]
    top = [candidate for candidate in candidates if candidate[0] == top_score]
    distinct_manifests = {str(candidate[3]) for candidate in top}
    if len(distinct_manifests) != 1:
        distinct_match_keys = {candidate[2] for candidate in top if candidate[2]}
        if len(distinct_match_keys) != 1:
            _log(
                "recent_direct_context_ambiguous",
                candidate_count=len(top),
                manifest_count=len(distinct_manifests),
                match_key_count=len(distinct_match_keys),
            )
            return None, None
        # Same match asked repeatedly. Use the newest completed direct request
        # for that match instead of blocking on multiple per-request bound
        # manifests.
        top.sort(key=lambda item: item[1], reverse=True)
    return top[0][3], top[0][4]


def _direct_context_score(text: str, record: dict) -> int:
    score = 0
    for field, weight in (("match_label", 100), ("request_text", 80), ("match_id", 60)):
        value = str(record.get(field) or "")
        if not value:
            continue
        if _context_pair_matches(text, value):
            score = max(score, weight)
        elif field == "match_id" and value and value in (text or ""):
            score = max(score, weight)
    return score


def _direct_context_key(record: dict) -> str:
    for field in ("match_label", "request_text", "match_id"):
        value = str(record.get(field) or "")
        if not value:
            continue
        terms = _context_terms(value)
        if len(terms) >= 2:
            return "|".join(terms[:2])
        if field == "match_id":
            return _normalize_context(value)
    return ""


def _context_pair_matches(text: str, label: str) -> bool:
    terms = _context_terms(label)
    if len(terms) < 2:
        return False
    normalized_text = _normalize_context(text)
    return all(term in normalized_text for term in terms[:2])


def _context_terms(value: str) -> list[str]:
    cleaned = _normalize_context(value)
    cleaned = re.sub(r"^(?:分析|analyse|analyze)\s*", "", cleaned, flags=re.IGNORECASE)
    parts = re.split(r"\s+(?:vs|v|versus|对|對)\s+|(?:\s+|-|_)+vs(?:\s+|-|_)+", cleaned, flags=re.IGNORECASE)
    if len(parts) < 2:
        parts = re.split(r"\s+", cleaned)
    terms: list[str] = []
    for part in parts:
        term = part.strip()
        if len(term) < 2:
            continue
        if term in {"分析", "盘口", "watch", "pass", "play", "no", "wc26"}:
            continue
        terms.append(term)
    return terms[:2]


def _normalize_context(value: str) -> str:
    normalized = str(value or "").lower()
    normalized = normalized.replace("—", " ").replace("–", " ")
    normalized = re.sub(r"[：:，,。.!！?？()\[\]【】{}<>`\"'“”‘’/\\\\|]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _direct_request_ids(text: str) -> list[str]:
    ids: list[str] = []
    for match in re.findall(r"\bdirect:[A-Za-z0-9_-]+\b", text or ""):
        if match not in ids:
            ids.append(match)
    return ids


def _match_ids(text: str) -> list[str]:
    ids: list[str] = []
    for raw in re.findall(r"\b([MW]\d{3})\b", text or "", flags=re.IGNORECASE):
        match = re.fullmatch(r"([MW])(\d{3})", raw.upper())
        if not match:
            continue
        normalized = "M" + match.group(2)
        if normalized not in ids:
            ids.append(normalized)
    return ids


def _direct_request_record_path(direct_id: str) -> Optional[Path]:
    suffix = direct_id.split(":", 1)[1] if ":" in direct_id else direct_id
    if not suffix:
        return None
    direct_dir = WORKSPACE / "direct_requests"
    if not direct_dir.exists():
        return None
    matches = sorted(direct_dir.rglob(f"direct-{suffix}.json"), reverse=True)
    return matches[0] if matches else None


def _manifest_from_match_id(text: str) -> Optional[Path]:
    for match_id in _match_ids(text):
        manifest = _latest_manifest_for_match(match_id)
        if manifest:
            return manifest
    return None


def _candidate_tokens(
    text: str,
    prefix_fragment: str,
    suffix: str,
    *,
    required_fragment: str = "reports/artifacts",
) -> list[str]:
    tokens: list[str] = []
    normalized_required = required_fragment.replace("\\", "/")
    for raw in re.findall(r"[^\s`\"'<>]+", text or ""):
        token = _clean_token(raw)
        normalized = token.replace("\\", "/")
        if normalized_required not in normalized:
            continue
        if prefix_fragment and prefix_fragment not in normalized:
            continue
        if not normalized.endswith(suffix):
            continue
        tokens.append(token)
    return tokens


def _clean_token(raw: str) -> str:
    token = str(raw or "").strip().strip("`\"'<>")
    while token and token[-1] in TRAILING_PUNCTUATION:
        token = token[:-1]
    return token


def _first_existing_path(tokens: list[str]) -> Optional[Path]:
    for token in tokens:
        path = _resolve_path(token)
        if _is_existing_file(path):
            return path
    return None


def _first_valid_manifest_path(tokens: list[str]) -> Optional[Path]:
    for token in tokens:
        path = _resolve_path(token)
        if _is_valid_manifest_path(path):
            return path
    return None


def _resolve_path(token: str) -> Optional[Path]:
    if not token:
        return None
    normalized = token.replace("\\", "/")
    if normalized.startswith("/"):
        return Path(normalized)
    if re.match(r"^[A-Za-z]:/", normalized):
        return Path(normalized)
    if normalized.startswith("reports/"):
        return WORKSPACE / normalized
    return Path(normalized)


def _is_existing_file(path: Optional[Path]) -> bool:
    try:
        return bool(path and path.exists() and path.is_file())
    except OSError:
        return False


def _is_valid_manifest_path(path: Optional[Path]) -> bool:
    if not _is_existing_file(path):
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


def _latest_manifest_for_match(match_id: str) -> Optional[Path]:
    match_id = str(match_id or "").strip()
    if not match_id:
        return None
    artifact_dir = WORKSPACE / "reports" / "artifacts"
    if not artifact_dir.exists():
        return None
    candidates = sorted(artifact_dir.glob(f"manifest-{match_id}*.json"), reverse=True)
    for candidate in candidates:
        if _is_valid_manifest_path(candidate):
            return candidate
    return None


def _report_from_manifest(manifest_path: Path) -> Optional[Path]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    report = _resolve_path(str(payload.get("report_path") or ""))
    return report if _is_existing_file(report) else None


def _manifest_from_report(report_path: Path) -> Optional[Path]:
    try:
        lines = report_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    for line in lines[:REPORT_HEADER_SCAN_LINES]:
        if not line.strip().startswith("artifact_manifest_path:"):
            continue
        _, _, raw_value = line.partition(":")
        path = _resolve_path(_clean_token(raw_value.strip()))
        if _is_valid_manifest_path(path):
            return path
    return None


def _profile_root() -> Path:
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2]


def _env_false(name: str) -> bool:
    value = str(os.environ.get(name, "")).strip().lower()
    return value in {"0", "false", "no", "off"}


def _log(action: str, **fields) -> None:
    try:
        log_dir = WORKSPACE / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "plugin": "wc26-direct-summary-enforcer",
            "action": action,
            **{k: v for k, v in fields.items() if v is not None},
        }
        with (log_dir / "direct-summary-enforcer.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _queue_blocked_recovery(category: str, **fields) -> None:
    try:
        queue_dir = WORKSPACE / "blocked_recovery" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)
        seed = {
            "category": category,
            "source": fields.get("source"),
            "session_id": fields.get("session_id"),
            "manifest_path": fields.get("manifest_path"),
            "report_path": fields.get("report_path"),
            "direct_request_ids": fields.get("direct_request_ids"),
            "reason": str(fields.get("reason") or "")[:240],
        }
        recovery_id = "br:" + _stable_hash(seed)
        path = queue_dir / f"{recovery_id.replace(':', '-')}.json"
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(current, dict):
                current["last_seen_at_utc"] = datetime.now(timezone.utc).isoformat()
                current["seen_count"] = int(current.get("seen_count") or 1) + 1
                path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
                return
        payload = {
            "schema_version": "wc26.blocked_recovery.event.v1",
            "recovery_id": recovery_id,
            "category": category,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "last_seen_at_utc": datetime.now(timezone.utc).isoformat(),
            "seen_count": 1,
            **{k: v for k, v in fields.items() if v not in (None, "", [])},
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        _log("blocked_recovery_enqueue_failed", category=category, error=str(exc)[:300])


def _stable_hash(payload) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    import hashlib

    return hashlib.sha256(encoded).hexdigest()[:16]
