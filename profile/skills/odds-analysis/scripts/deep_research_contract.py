#!/usr/bin/env python3
"""Validate WC26 Deep Research post-report artifacts and Telegram sections.

This contract is deliberately scoped to the Deep Research finalizer. It does
not validate or mutate the main odds report, manifest, p_adj, EV, Kelly, or
relay actionability. Its job is to stop the LLM research layer from presenting
old news as a fresh market-pricing edge.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTRACT_VERSION = "wc26.deep_research_contract.v1.2"
NEWS_CLASSES = {"injury_news", "lineup_news", "squad_news", "coach_quote", "market_news"}
PRICING_ENUMS = {"pre_snapshot", "post_snapshot", "unknown"}
RECENCY_ENUMS = {"fresh_0_24h", "recent_24_72h", "stale_gt_72h", "unknown"}

NEWS_HINT_RE = re.compile(
    r"(injur|伤|ruled out|缺阵|acl|hamstring|squad|名单|lineup|首发|coach|教练|quote|market news|盘口新闻)",
    re.IGNORECASE,
)
RISKY_FRESHNESS_PATTERNS = [
    r"尚未.{0,12}(?:price\s*in|priced\s*in|定价|吸收|消化)",
    r"(?:市场|盘口).{0,12}(?:尚未|还没|没有|未).{0,12}(?:消化|吸收|price)",
    r"旧快照.{0,16}(?:官宣|announcement|announcements|前)",
    r"(?:squad|lineup).{0,4}announcements?",
    r"new\s+(?:squad|lineup|injury|market)\s+(?:announcement|news)",
    r"可能.{0,12}(?:未|尚未).{0,12}(?:price|定价|消化|吸收)",
    r"未充分.{0,8}(?:消化|price|定价)",
    r"not\s+(?:yet\s+)?priced\s+in",
    r"market.{0,20}(?:not|hasn['’]?t|have\s+not).{0,20}(?:price|digest|absorb)",
]


def parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return payload


def resolve_path(raw: Any, base: Path | None = None, workspace: Path | None = None) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    normalized = raw.strip()
    path = Path(normalized)
    if path.is_absolute():
        return path
    if workspace and normalized.replace("\\", "/").startswith("reports/"):
        return workspace / normalized
    if base:
        return (base.parent / path).resolve()
    return path


def timestamp_from_snapshot_name(raw: Any) -> datetime | None:
    text = str(raw or "")
    match = re.search(r"(\d{8}T\d{6})Z?", text)
    if not match:
        return None
    stamp = match.group(1)
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_artifact_payload(artifact: dict[str, Any], manifest_path: Path, workspace: Path) -> dict[str, Any] | None:
    path = resolve_path(artifact.get("path"), manifest_path, workspace)
    if not path or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def artifact_caps(artifact: dict[str, Any]) -> set[str]:
    caps: set[str] = set()
    provides = artifact.get("provides")
    if isinstance(provides, list):
        caps.update(str(item) for item in provides)
    for key in ("artifact_type", "artifact_kind", "script"):
        raw = artifact.get(key)
        if raw:
            caps.add(str(raw))
    return caps


def snapshot_from_manifest(manifest: dict[str, Any], manifest_path: Path | None, workspace: Path) -> tuple[datetime | None, str]:
    for key in ("snapshot_at_utc", "source_snapshot_at_utc", "odds_snapshot_at_utc"):
        parsed = parse_time(manifest.get(key))
        if parsed:
            return parsed, key
    for key in ("snapshot_id", "source_snapshot_id"):
        parsed = timestamp_from_snapshot_name(manifest.get(key))
        if parsed:
            return parsed, str(manifest.get(key))
    if manifest_path:
        for artifact in manifest.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            caps = artifact_caps(artifact)
            if "path_a_crossbook" not in caps and "crossbook_scan" not in caps:
                continue
            payload = load_artifact_payload(artifact, manifest_path, workspace)
            if not payload:
                continue
            for key in ("snapshot_at_utc", "captured_at_utc", "source_snapshot_at_utc"):
                parsed = parse_time(payload.get(key))
                if parsed:
                    return parsed, f"crossbook.{key}"
            for key in ("source_snapshot_id", "input_snapshot", "input_snapshot_path", "source_snapshot_path"):
                parsed = timestamp_from_snapshot_name(payload.get(key))
                if parsed:
                    return parsed, f"crossbook.{key}"
    generated = parse_time(manifest.get("generated_at_utc") or manifest.get("created_at_utc"))
    return generated, "manifest.generated_at_utc_fallback" if generated else ""


def generated_from_artifact(artifact: dict[str, Any], manifest: dict[str, Any] | None = None) -> datetime | None:
    baseline = artifact.get("baseline") if isinstance(artifact.get("baseline"), dict) else {}
    for raw in (
        artifact.get("generated_utc"),
        artifact.get("generated_at_utc"),
        baseline.get("direct_request_created_at_utc"),
        baseline.get("baseline_report_generated_at_utc"),
        (manifest or {}).get("generated_at_utc"),
    ):
        parsed = parse_time(raw)
        if parsed:
            return parsed
    return datetime.now(timezone.utc)


def infer_source_class(source: dict[str, Any]) -> str:
    raw = str(source.get("source_class") or "").strip()
    if raw:
        return raw
    haystack = " ".join(
        str(source.get(key) or "")
        for key in ("title", "what_it_supports", "limitations", "url")
    )
    if NEWS_HINT_RE.search(haystack):
        lowered = haystack.lower()
        if any(token in lowered for token in ("squad", "名单")):
            return "squad_news"
        if any(token in lowered for token in ("lineup", "首发")):
            return "lineup_news"
        if any(token in lowered for token in ("coach", "教练", "quote")):
            return "coach_quote"
        return "injury_news"
    return "context"


def classify_pricing(published: datetime | None, snapshot: datetime | None) -> tuple[str, float | None]:
    if not published or not snapshot:
        return "unknown", None
    hours_after = (published - snapshot).total_seconds() / 3600.0
    return ("post_snapshot" if hours_after > 0 else "pre_snapshot"), round(hours_after, 2)


def classify_recency(published: datetime | None, generated: datetime | None) -> tuple[str, float | None]:
    if not published or not generated:
        return "unknown", None
    hours_before = (generated - published).total_seconds() / 3600.0
    if hours_before < 0:
        return "unknown", round(hours_before, 2)
    if hours_before <= 24:
        return "fresh_0_24h", round(hours_before, 2)
    if hours_before <= 72:
        return "recent_24_72h", round(hours_before, 2)
    return "stale_gt_72h", round(hours_before, 2)


def has_risky_freshness_claim(text: str) -> bool:
    return any(re.search(pattern, text or "", flags=re.IGNORECASE | re.DOTALL) for pattern in RISKY_FRESHNESS_PATTERNS)


def text_references_source(text: str, source_id: str) -> bool:
    if not text or not source_id:
        return False
    return bool(re.search(rf"(?<![A-Za-z0-9_-]){re.escape(source_id)}(?![A-Za-z0-9_-])", text))


def validate_deep_research(
    artifact: dict[str, Any],
    *,
    artifact_path: Path | None = None,
    manifest: dict[str, Any] | None = None,
    manifest_path: Path | None = None,
    section_text: str = "",
    workspace: Path | None = None,
) -> dict[str, Any]:
    workspace = workspace or Path("/hermesdata/worldcup-2026-handicap")
    errors: list[str] = []
    warnings: list[str] = []
    normalized_sources: list[dict[str, Any]] = []

    baseline = artifact.get("baseline") if isinstance(artifact.get("baseline"), dict) else {}
    snapshot = parse_time(baseline.get("snapshot_at_utc"))
    snapshot_source = str(baseline.get("snapshot_source") or "")
    if not snapshot and manifest:
        snapshot, snapshot_source = snapshot_from_manifest(manifest, manifest_path, workspace)
    if not snapshot:
        errors.append("baseline.snapshot_at_utc missing and could not be derived from manifest/crossbook snapshot")

    generated = generated_from_artifact(artifact, manifest)
    if not artifact.get("artifact_version"):
        warnings.append("artifact_version missing")
    elif str(artifact.get("artifact_version")) < "1.2":
        warnings.append("artifact_version older than 1.2; validating with inferred source classes")

    sources = artifact.get("sources")
    if not isinstance(sources, list):
        errors.append("sources must be a list")
        sources = []

    has_post_snapshot_news = False
    news_sources = 0
    text = section_text or json.dumps(artifact.get("final_view") or artifact, ensure_ascii=False)

    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            errors.append(f"sources[{index}] must be object")
            continue
        source_id = str(source.get("source_id") or f"source[{index}]")
        source_class = infer_source_class(source)
        published = parse_time(source.get("published_at_utc"))
        fetched = parse_time(source.get("fetched_at_utc"))
        pricing, hours_after = classify_pricing(published, snapshot)
        recency, hours_before = classify_recency(published, generated)
        source_is_used = text_references_source(text, source_id)
        source_errors: list[str] = []
        if source_class in NEWS_CLASSES:
            news_sources += 1
            if not source.get("source_class"):
                source_errors.append(f"{source_id} news source missing source_class")
            if not published:
                source_errors.append(f"{source_id} {source_class} missing published_at_utc")
            if pricing == "post_snapshot":
                has_post_snapshot_news = True
        if source.get("pricing_freshness") and source.get("pricing_freshness") != pricing:
            source_errors.append(
                f"{source_id} pricing_freshness={source.get('pricing_freshness')} does not match computed {pricing}"
            )
        if source.get("recency_bucket") and source.get("recency_bucket") != recency:
            source_errors.append(f"{source_id} recency_bucket={source.get('recency_bucket')} does not match computed {recency}")
        # A source may remain in an older/cached artifact but not appear in the
        # Telegram final text after sanitization. Treat unused bad sources as
        # warnings so the projector can ignore them instead of dropping the
        # whole Deep Research section.
        if source_errors:
            if section_text and not source_is_used:
                warnings.extend(source_errors)
            else:
                errors.extend(source_errors)
        normalized_sources.append(
            {
                "source_id": source_id,
                "source_class": source_class,
                "published_at_utc": format_time(published),
                "fetched_at_utc": format_time(fetched),
                "pricing_freshness": pricing,
                "hours_after_snapshot": hours_after,
                "hours_before_finalizer": hours_before,
                "recency_bucket": recency,
                "used_in_section": source_is_used,
            }
        )

    risky = has_risky_freshness_claim(text)
    if risky and not has_post_snapshot_news:
        errors.append("fresh-pricing language requires at least one post_snapshot news source")

    return {
        "contract": CONTRACT_VERSION,
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "baseline": {
            "snapshot_at_utc": format_time(snapshot),
            "snapshot_source": snapshot_source or None,
            "baseline_report_generated_at_utc": format_time(parse_time(baseline.get("baseline_report_generated_at_utc")) or parse_time((manifest or {}).get("generated_at_utc"))),
            "direct_request_created_at_utc": baseline.get("direct_request_created_at_utc"),
        },
        "generated_utc": format_time(generated),
        "news_source_count": news_sources,
        "has_post_snapshot_news": has_post_snapshot_news,
        "has_risky_freshness_claim": risky,
        "normalized_sources": normalized_sources,
        "artifact_path": str(artifact_path) if artifact_path else None,
        "manifest_path": str(manifest_path) if manifest_path else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate WC26 Deep Research artifact freshness contract")
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path("/hermesdata/worldcup-2026-handicap"))
    parser.add_argument("--text-file", type=Path)
    parser.add_argument("--text-stdin", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    artifact = read_json(args.artifact) if args.artifact else {}
    manifest = read_json(args.manifest) if args.manifest and args.manifest.exists() else None
    section_text = ""
    if args.text_stdin:
        section_text = sys.stdin.read()
    elif args.text_file:
        section_text = args.text_file.read_text(encoding="utf-8")

    # Text-only validation: allow harmless sections, block fresh-pricing claims
    # that have no artifact provenance.
    if not artifact:
        result = {
            "contract": CONTRACT_VERSION,
            "status": "fail" if has_risky_freshness_claim(section_text) else "pass",
            "errors": ["fresh-pricing language requires deep_research artifact"] if has_risky_freshness_claim(section_text) else [],
            "warnings": ["no artifact supplied; text-only validation"],
        }
    else:
        result = validate_deep_research(
            artifact,
            artifact_path=args.artifact,
            manifest=manifest,
            manifest_path=args.manifest,
            section_text=section_text,
            workspace=args.workspace,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
