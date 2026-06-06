#!/usr/bin/env python3
"""Fixture identity registry tests.

`Mxxx` is a display ordinal, not the canonical match identity. These tests keep
the cache mapping from silently assigning the wrong teams to an M id.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, rel: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


fixture_registry = load_module("fixture_registry", "skills/odds-analysis/scripts/fixture_registry.py")
report_guard = load_module("report_guard", "skills/odds-analysis/scripts/report_guard.py")


def write_fixture_cache(path: Path) -> Path:
    matches = [
        {
            "id": 537333,
            "utcDate": "2026-06-12T19:00:00Z",
            "stage": "GROUP_STAGE",
            "group": "GROUP_B",
            "status": "TIMED",
            "homeTeam": {"name": "Canada", "tla": "CAN"},
            "awayTeam": {"name": "Bosnia-Herzegovina", "tla": "BIH"},
        },
        {
            "id": 537334,
            "utcDate": "2026-06-13T19:00:00Z",
            "stage": "GROUP_STAGE",
            "group": "GROUP_B",
            "status": "TIMED",
            "homeTeam": {"name": "Qatar", "tla": "QAT"},
            "awayTeam": {"name": "Switzerland", "tla": "SUI"},
        },
        {
            "id": 537346,
            "utcDate": "2026-06-14T04:00:00Z",
            "stage": "GROUP_STAGE",
            "group": "GROUP_D",
            "status": "TIMED",
            "homeTeam": {"name": "Australia", "tla": "AUS"},
            "awayTeam": {"name": "Turkey", "tla": "TUR"},
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"data": {"matches": matches}}, ensure_ascii=False), encoding="utf-8")
    return path


def test_fixture_registry_uses_canonical_football_data_id(tmp_path: Path) -> None:
    cache = write_fixture_cache(tmp_path / "fixtures.json")

    registry = fixture_registry.load_registry(cache)
    match = fixture_registry.resolve_fixture(registry, home="Qatar", away="Switzerland")

    assert match["canonical_id"] == "fd:537334"
    assert match["football_data_id"] == 537334
    assert match["home"] == "Qatar"
    assert match["away"] == "Switzerland"


def test_fixture_registry_applies_external_venue_override(tmp_path: Path) -> None:
    override_path = tmp_path / "venue-overrides.json"
    override_path.write_text(
        json.dumps({"venues": {"fd:537351": "Official Stadium, City"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    old_value = os.environ.get("WC26_VENUE_OVERRIDES_PATH")
    os.environ["WC26_VENUE_OVERRIDES_PATH"] = str(override_path)
    try:
        venue_overrides = fixture_registry.load_venue_overrides(override_path)
        entry = fixture_registry.build_entry(
            9,
            {
                "id": 537351,
                "utcDate": "2026-06-14T17:00:00Z",
                "stage": "GROUP_STAGE",
                "group": "GROUP_E",
                "status": "TIMED",
                "homeTeam": {"name": "Team A", "tla": "AAA"},
                "awayTeam": {"name": "Team B", "tla": "BBB"},
            },
            venue_overrides,
        )
    finally:
        if old_value is None:
            os.environ.pop("WC26_VENUE_OVERRIDES_PATH", None)
        else:
            os.environ["WC26_VENUE_OVERRIDES_PATH"] = old_value

    assert entry["local_ordinal_id"] == "M009"
    assert entry["venue"] == "Official Stadium, City"


def test_fixture_registry_detects_m_id_team_mismatch(tmp_path: Path) -> None:
    cache = write_fixture_cache(tmp_path / "fixtures.json")
    registry = fixture_registry.load_registry(cache)

    result = fixture_registry.validate_identity(
        registry,
        {
            "match_id": "M003",
            "match": {"home": "Qatar", "away": "Switzerland"},
        },
    )

    assert result["valid"] is False
    assert "M003 maps to Australia vs Turkey" in " ".join(result["errors"])


def test_report_guard_rejects_fixture_identity_mismatch(tmp_path: Path) -> None:
    workspace = tmp_path
    cache = write_fixture_cache(workspace / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json")
    request = workspace / "direct-request.json"
    request.write_text(
        json.dumps(
            {
                "direct_request_id": "direct:mismatch",
                "platform": "telegram",
                "chat_id": "6808688675",
                "request_text": "分析 M003 卡塔尔 vs 瑞士",
                "created_at_utc": "2026-06-05T11:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    manifest = workspace / "reports" / "artifacts" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "workflow_contract": "wc26.direct_report.v1",
                "report_completeness": "partial",
                "direct_request_id": "direct:mismatch",
                "direct_request_path": str(request),
                "match_id": "M003",
                "match": {"home": "Qatar", "away": "Switzerland"},
                "mode": "live",
                "source_quality": "C",
                "final_status": "watch",
                "source_freshness": {"sources": [{"name": "fixture"}]},
                "analysis_gates": {
                    "devig_three_method": "skipped_missing_source",
                    "path_a_crossbook": "skipped_missing_source",
                    "asian_handicap": "skipped_missing_source",
                    "totals": "skipped_missing_source",
                    "path_b_model_diagnostic": "diagnostic",
                    "path_c_consistency": "skipped_missing_source",
                    "source_freshness": "pass",
                },
                "skipped_sections": [
                    {"gate": gate, "reason": "source missing", "impact": "section unavailable"}
                    for gate in [
                        "devig_three_method",
                        "path_a_crossbook",
                        "asian_handicap",
                        "totals",
                        "path_c_consistency",
                    ]
                ],
                "numbers": [],
                "artifacts": [],
            }
        ),
        encoding="utf-8",
    )
    report = workspace / "reports" / "match" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        "\n".join(
            [
                "# WC26 M003 Qatar vs Switzerland",
                "",
                "mode: live",
                "source_quality: C",
                "final_status: watch",
                "direct_request_id: direct:mismatch",
                f"direct_request_path: {request}",
                f"artifact_manifest_path: {manifest}",
                "artifact_contract_status: pass",
                "report_guard_status: pass",
            ]
        ),
        encoding="utf-8",
    )

    result = report_guard.validate_report(report)

    assert result["valid"] is False
    assert "maps to Australia vs Turkey" in " ".join(result["errors"])
