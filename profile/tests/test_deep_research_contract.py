"""Tests for the WC26 Deep Research freshness contract."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "odds-analysis"
    / "scripts"
    / "deep_research_contract.py"
)
if not CONTRACT_PATH.exists():
    CONTRACT_PATH = Path(__file__).resolve().parent / "deep_research_contract.py"


def load_contract():
    spec = importlib.util.spec_from_file_location("deep_research_contract_test", CONTRACT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_manifest_with_crossbook(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    artifact_dir = workspace / "reports" / "artifacts"
    artifact_dir.mkdir(parents=True)
    crossbook = artifact_dir / "crossbook-M010.json"
    crossbook.write_text(
        json.dumps(
            {
                "artifact_type": "cross_book_scan",
                "source_snapshot_id": "the-odds-api-multibook-20260605T143408Z.json",
            }
        ),
        encoding="utf-8",
    )
    manifest = artifact_dir / "manifest-M010.json"
    manifest.write_text(
        json.dumps(
            {
                "match_id": "M010",
                "generated_at_utc": "2026-06-05T15:30:00Z",
                "artifacts": [
                    {
                        "artifact_type": "crossbook_scan",
                        "provides": ["path_a_crossbook"],
                        "path": str(crossbook),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return workspace, manifest


def artifact_with_source(published_at: str | None) -> dict:
    source = {
        "source_id": "DR-C1",
        "source_class": "squad_news",
        "tool": "jina",
        "url": "https://example.test/squad",
        "title": "Japan squad news",
        "fetched_at_utc": "2026-06-06T05:10:00Z",
        "what_it_supports": "Mitoma injury and squad availability",
    }
    if published_at is not None:
        source["published_at_utc"] = published_at
    return {
        "artifact_type": "deep_research",
        "artifact_version": "1.2",
        "generated_utc": "2026-06-06T05:15:00Z",
        "baseline": {"baseline_report_generated_at_utc": "2026-06-05T15:30:00Z"},
        "sources": [source],
        "final_view": {"why": "需新盘口确认。"},
    }


def validate(tmp_path: Path, artifact: dict, text: str):
    contract = load_contract()
    workspace, manifest_path = write_manifest_with_crossbook(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return contract.validate_deep_research(
        artifact,
        manifest=manifest,
        manifest_path=manifest_path,
        section_text=text,
        workspace=workspace,
    )


def test_pre_snapshot_news_cannot_claim_unpriced(tmp_path):
    result = validate(
        tmp_path,
        artifact_with_source("2026-05-15T00:00:00Z"),
        "旧快照在官宣前，市场可能尚未消化。",
    )

    assert result["status"] == "fail"
    assert result["baseline"]["snapshot_at_utc"] == "2026-06-05T14:34:08Z"
    assert result["normalized_sources"][0]["pricing_freshness"] == "pre_snapshot"
    assert any("post_snapshot" in error for error in result["errors"])


def test_post_snapshot_news_can_claim_unpriced(tmp_path):
    result = validate(
        tmp_path,
        artifact_with_source("2026-06-06T04:00:00Z"),
        "旧快照在官宣前，市场可能尚未消化。",
    )

    assert result["status"] == "pass"
    assert result["has_post_snapshot_news"] is True
    assert result["normalized_sources"][0]["pricing_freshness"] == "post_snapshot"
    assert result["normalized_sources"][0]["recency_bucket"] == "fresh_0_24h"


def test_missing_news_publication_time_fails_when_source_is_used(tmp_path):
    result = validate(
        tmp_path,
        artifact_with_source(None),
        "[DR-C1] 发布时间不明，只能等待新盘口确认。",
    )

    assert result["status"] == "fail"
    assert result["normalized_sources"][0]["pricing_freshness"] == "unknown"
    assert any("missing published_at_utc" in error for error in result["errors"])


def test_missing_news_publication_time_is_warning_when_source_is_filtered(tmp_path):
    result = validate(
        tmp_path,
        artifact_with_source(None),
        "Deep Research 过滤后只保留历史背景。",
    )

    assert result["status"] == "pass"
    assert result["normalized_sources"][0]["pricing_freshness"] == "unknown"
    assert any("missing published_at_utc" in warning for warning in result["warnings"])


def test_context_source_does_not_require_publication_time(tmp_path):
    artifact = {
        "artifact_type": "deep_research",
        "artifact_version": "1.2",
        "generated_utc": "2026-06-06T05:15:00Z",
        "sources": [
            {
                "source_id": "DR-A1",
                "source_class": "historical_context",
                "tool": "exa",
                "url": "https://example.test/history",
                "title": "World Cup handicap history",
                "fetched_at_utc": "2026-06-06T05:10:00Z",
            }
        ],
    }

    result = validate(tmp_path, artifact, "历史样本仅作背景，需新盘口确认。")

    assert result["status"] == "pass"
    assert result["normalized_sources"][0]["source_class"] == "historical_context"
