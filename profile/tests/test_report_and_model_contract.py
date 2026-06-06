#!/usr/bin/env python3
"""Contract checks for report provenance and margin distributions."""

from __future__ import annotations

import importlib.util
import json
from types import SimpleNamespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, rel: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


report_contract = load_module("report_contract", "skills/odds-analysis/scripts/report_contract.py")
report_guard = load_module("report_guard", "skills/odds-analysis/scripts/report_guard.py")
direct_request_record = load_module("direct_request_record", "skills/odds-analysis/scripts/direct_request_record.py")
direct_report_bind = load_module("direct_report_bind", "skills/odds-analysis/scripts/direct_report_bind.py")
cross_book_scan = load_module("cross_book_scan", "skills/odds-analysis/scripts/cross_book_scan.py")
numeric_artifact = load_module("numeric_artifact", "skills/odds-analysis/scripts/numeric_artifact.py")
model_margin = load_module("model_margin", "skills/odds-analysis/scripts/model_margin.py")


def valid_manifest() -> dict:
    return {
        "schema_version": "wc26.numeric_artifact.v1",
        "mode": "live",
        "source_quality": "A",
        "final_status": "qualified_play",
        "adjustment_ledger_id": "ledger:M001:20260604:001",
        "numbers": [
            {
                "name": "selected_side_ah_ev",
                "kind": "asian_handicap_ev",
                "value": 0.041,
                "snapshot_id": "odds:pinnacle:M001:20260604T010000Z",
                "artifact_id": "devig:M001:ah:-0.25:20260604T010001Z",
                "artifact_type": "devig",
                "probability_source": "adjustment_ledger",
                "uses_p_model_directly": False,
            }
        ],
    }


def valid_crossbook_payload() -> dict:
    return {
        "artifact_type": "crossbook_scan",
        "artifact_kind": "cross_book_scan",
        "script": "cross_book_scan.py",
        "provides": ["path_a_crossbook"],
        "input_snapshot": "the-odds-api-multibook-test.json",
        "source_snapshot_id": "the-odds-api-multibook-test.json",
        "summary": {
            "markets_scanned": ["h2h", "spreads", "totals"],
            "quotes_scanned": 3,
            "edge_count": 1,
            "noise_edge_count": 1,
            "actionable_count": 0,
            "qualified_play_count": 0,
            "qualified_count": 0,
            "best_ev": 0.023,
            "best_edge": {
                "book": "marathonbet",
                "market_key": "h2h",
                "outcome": "japan",
                "offered_odds": 4.05,
                "fair_odds": 3.959,
                "ev_shin": 0.023,
                "survives_all_methods": True,
                "suspect": False,
                "actionable": False,
                "qualifies": False,
                "ev_band": "noise_lt_5pp",
            },
            "best_noise_edge": {
                "book": "marathonbet",
                "market_key": "h2h",
                "outcome": "japan",
                "offered_odds": 4.05,
                "fair_odds": 3.959,
                "ev_shin": 0.023,
                "survives_all_methods": True,
                "suspect": False,
                "actionable": False,
                "qualifies": False,
                "ev_band": "noise_lt_5pp",
            },
            "best_actionable_edge": None,
            "best_qualified_edge": None,
        },
        "markets": {
            "h2h": {
                "status": "ok",
                "sharp_anchor": "pinnacle",
                "devig_primary": "shin",
                "outcomes_scanned": ["netherlands", "draw", "japan"],
                "quotes_scanned": 3,
                "fair_probs": {
                    "shin": {"netherlands": 0.4899, "draw": 0.2575, "japan": 0.2526},
                    "power": {"netherlands": 0.4913, "draw": 0.2568, "japan": 0.2519},
                    "multiplicative": {"netherlands": 0.4861, "draw": 0.2593, "japan": 0.2546},
                },
                "quotes": [
                    {
                        "book": "marathonbet",
                        "market_key": "h2h",
                        "outcome": "japan",
                        "offered_odds": 4.05,
                        "sharp_fair_prob": 0.2526,
                        "fair_odds": 3.959,
                        "ev_shin": 0.023,
                        "ev_power": 0.0202,
                        "ev_multiplicative": 0.031,
                        "survives_all_methods": True,
                        "suspect": False,
                        "edge_candidate": True,
                        "actionable": False,
                        "qualifies": False,
                        "ev_band": "noise_lt_5pp",
                    },
                    {
                        "book": "marathonbet",
                        "market_key": "h2h",
                        "outcome": "netherlands",
                        "offered_odds": 1.95,
                        "sharp_fair_prob": 0.4899,
                        "fair_odds": 2.041,
                        "ev_shin": -0.0447,
                        "ev_power": -0.041,
                        "ev_multiplicative": -0.052,
                        "survives_all_methods": False,
                        "suspect": False,
                        "edge_candidate": False,
                        "actionable": False,
                        "qualifies": False,
                        "ev_band": "noise_lt_5pp",
                    },
                    {
                        "book": "marathonbet",
                        "market_key": "h2h",
                        "outcome": "draw",
                        "offered_odds": 3.70,
                        "sharp_fair_prob": 0.2575,
                        "fair_odds": 3.883,
                        "ev_shin": -0.0473,
                        "ev_power": -0.05,
                        "ev_multiplicative": -0.0404,
                        "survives_all_methods": False,
                        "suspect": False,
                        "edge_candidate": False,
                        "actionable": False,
                        "qualifies": False,
                        "ev_band": "noise_lt_5pp",
                    },
                ],
                "edges": [
                    {
                        "book": "marathonbet",
                        "market_key": "h2h",
                        "outcome": "japan",
                        "offered_odds": 4.05,
                        "sharp_fair_prob": 0.2526,
                        "fair_odds": 3.959,
                        "ev_shin": 0.023,
                        "ev_power": 0.0202,
                        "ev_multiplicative": 0.031,
                        "survives_all_methods": True,
                        "suspect": False,
                        "edge_candidate": True,
                        "actionable": False,
                        "qualifies": False,
                        "ev_band": "noise_lt_5pp",
                    }
                ],
            },
            "spreads": {"status": "no_market_data"},
            "totals": {"status": "no_market_data"},
        },
    }


def valid_mechanism_audit_payload(final_status: str = "watch") -> dict:
    path_c_blocked = final_status in {"watch", "pass_incomplete"}
    return {
        "artifact_type": "mechanism_audit",
        "artifact_kind": "mechanism_audit",
        "audit_contract": "wc26.mechanism_audit.v1",
        "script": "mechanism_audit.py",
        "source_manifest_id": "manifest:test",
        "match_id": "M009",
        "manifest_final_status": final_status,
        "mechanism_audit_status": "pass_incomplete" if path_c_blocked else "complete",
        "required_final_status": final_status,
        "review_required": path_c_blocked,
        "blocking_mechanisms": ["path_c_consistency"] if path_c_blocked else [],
        "mechanisms": {
            "path_a_crossbook": {
                "status": "COMPLETE",
                "required_for_complete": True,
                "artifact_id": "crossbook:m009",
                "quotes_scanned": 3,
                "edge_count": 1,
                "noise_edge_count": 1,
                "actionable_count": 0,
                "qualified_play_count": 0,
            },
            "path_b_model_diagnostic": {
                "status": "COMPLETE",
                "required_for_complete": True,
                "gate_status": "diagnostic",
            },
            "path_c_consistency": {
                "status": "BLOCKED" if path_c_blocked else "COMPLETE",
                "required_for_complete": True,
                "reason": "missing consistency_triangle artifact" if path_c_blocked else "",
            },
            "role_engine": {
                "status": "BLOCKED",
                "required_for_complete": False,
                "reason": "dynamic role engine is not implemented",
            },
            "artifact_hypothesis_engine_v0": {
                "status": "COMPLETE",
                "required_for_complete": False,
            },
        },
        "hypothesis_decisions": [
            {
                "source": "path_a_crossbook",
                "subject": "marathonbet h2h japan",
                "decision": "CONFIRMED_NOISE",
                "book": "marathonbet",
                "market_key": "h2h",
                "outcome": "japan",
                "evidence": "cross_book_scan edge row",
                "ev_shin": 0.023,
            },
            {
                "source": "path_b_model_diagnostic",
                "subject": "model probability vs market",
                "decision": "DIAGNOSTIC_ONLY",
                "evidence": "calibration_status=holdout_pass",
            },
        ],
    }


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_multibook_snapshot(path: Path, japan_price: float = 4.20) -> Path:
    return write_json(
        path,
        [
            {
                "home_team": "Netherlands",
                "away_team": "Japan",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Netherlands", "price": 1.99},
                                    {"name": "Draw", "price": 3.73},
                                    {"name": "Japan", "price": 3.80},
                                ],
                            }
                        ],
                    },
                    {
                        "key": "marathonbet",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Netherlands", "price": 1.95},
                                    {"name": "Draw", "price": 3.70},
                                    {"name": "Japan", "price": japan_price},
                                ],
                            }
                        ],
                    },
                ],
            }
        ],
    )


def build_crossbook_from_snapshot(snapshot_path: Path) -> dict:
    board = cross_book_scan.parse_odds_snapshot(str(snapshot_path), "Netherlands", "Japan")
    h2h = cross_book_scan.scan_market(board, "h2h", ["netherlands", "draw", "japan"])
    payload = {
        "artifact_type": "crossbook_scan",
        "artifact_kind": "cross_book_scan",
        "script": "cross_book_scan.py",
        "provides": ["path_a_crossbook"],
        "input_snapshot": str(snapshot_path),
        "source_snapshot_id": snapshot_path.name,
        "match_home": "Netherlands",
        "match_away": "Japan",
        "edge_threshold": 0.02,
        "actionable_threshold": 0.05,
        "suspect_threshold": 0.08,
        "markets": {
            "h2h": h2h,
            "spreads": {"status": "no_market_data"},
            "totals": {"status": "no_market_data"},
        },
    }
    payload["summary"] = cross_book_scan.build_summary(payload)
    return payload


def write_relay_ready_direct_fixture(tmp_path: Path, request_status: str = "completed_cached") -> tuple[Path, Path, Path]:
    workspace = tmp_path
    artifacts_dir = workspace / "reports" / "artifacts"
    report_path = workspace / "reports" / "match" / "M010-report.md"
    manifest_path = artifacts_dir / "manifest-M010.json"
    request_path = workspace / "direct_requests" / "2026-06-05" / "direct-m010.json"

    devig_path = write_json(
        artifacts_dir / "devig.json",
        {
            "artifact_type": "devig",
            "artifact_kind": "scalar_market",
            "odds_unit_contract": "all probability and EV math uses normalized decimal odds > 1.0",
            "devig_primary": "shin",
            "devig_methods": {
                "shin": [0.49, 0.26, 0.25],
                "power": [0.49, 0.26, 0.25],
                "multiplicative": [0.49, 0.26, 0.25],
            },
            "survives_all_methods": True,
        },
    )
    crossbook_path = write_json(artifacts_dir / "crossbook.json", valid_crossbook_payload())
    ah_path = write_json(artifacts_dir / "ah.json", {"artifact_kind": "asian_handicap_market"})
    totals_path = write_json(artifacts_dir / "totals.json", {"artifact_kind": "totals_market"})
    path_c_path = write_json(
        artifacts_dir / "path-c.json",
        {
            "artifact_kind": "consistency_triangle",
            "signal": {"type": None, "strength": "无"},
            "discrepancy": {"pp": -3.5},
        },
    )
    audit_path = write_json(artifacts_dir / "mechanism.json", valid_mechanism_audit_payload("pass"))
    write_json(
        request_path,
        {
            "schema_version": "wc26.direct_request.v1",
            "direct_request_id": "direct:m010",
            "platform": "telegram",
            "chat_id": "6808688675",
            "message_id": "61",
            "user_id": "6808688675",
            "user_name": "菸草",
            "request_text": "分析 M010 荷兰 vs 日本",
            "match_id": "M010",
            "match_label": "Netherlands vs Japan",
            "created_at_utc": "2026-06-05T13:51:00Z",
            "status": request_status,
            "report_path": str(report_path),
            "manifest_path": str(manifest_path),
            "cache_mode": "reuse_existing_report",
            "source_snapshot_id": "the-odds-api-multibook-test.json",
            "api_refresh_performed": False,
            "completed_at_utc": "2026-06-05T13:55:00Z",
        },
    )
    write_json(
        manifest_path,
        {
            "workflow_contract": "wc26.direct_report.v1",
            "direct_request_id": "direct:m010",
            "direct_request_path": str(request_path),
            "match_id": "M010",
            "match": {"match_id": "M010", "home": "Netherlands", "away": "Japan"},
            "mode": "live",
            "source_quality": "B",
            "final_status": "pass",
            "source_freshness": {"sources": [{"name": "the-odds-api", "snapshot_id": "the-odds-api-multibook-test.json"}]},
            "analysis_gates": {
                "devig_three_method": "pass",
                "path_a_crossbook": "pass",
                "asian_handicap": "pass",
                "totals": "pass",
                "path_b_model_diagnostic": "diagnostic",
                "path_c_consistency": "pass",
                "mechanism_audit": "pass",
                "source_freshness": "pass",
            },
            "artifacts": [
                {"artifact_id": "devig:m010", "artifact_type": "devig", "script": "devig.py", "path": str(devig_path), "provides": ["devig_1x2"]},
                {"artifact_id": "crossbook:m010", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook_path), "provides": ["path_a_crossbook"]},
                {"artifact_id": "ah:m010", "artifact_type": "asian_handicap_market", "script": "devig.py", "path": str(ah_path), "provides": ["asian_handicap"]},
                {"artifact_id": "totals:m010", "artifact_type": "totals_market", "script": "devig.py", "path": str(totals_path), "provides": ["totals"]},
                {"artifact_id": "path-c:m010", "artifact_type": "consistency_triangle", "script": "consistency_triangle.py", "path": str(path_c_path), "provides": ["path_c_consistency"]},
                {"artifact_id": "mechanism:m010", "artifact_type": "mechanism_audit", "script": "mechanism_audit.py", "path": str(audit_path), "provides": ["mechanism_audit"]},
            ],
        },
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "\n".join(
            [
                "# WC26 M010 Netherlands vs Japan - manual_now Handicap Report",
                "",
                "workflow_contract: wc26.direct_report.v1",
                "direct_request_id: direct:m010",
                f"direct_request_path: {request_path}",
                "mode: live",
                "source_quality: B",
                "final_status: pass",
                f"artifact_manifest_path: {manifest_path}",
                "artifact_contract_status: pass",
                "report_guard_status: pass",
                "",
                "## 1. One-Line View",
                "PASS.",
            ]
        ),
        encoding="utf-8",
    )
    return request_path, manifest_path, report_path


def test_direct_request_record_uses_latest_telegram_session_metadata(tmp_path: Path) -> None:
    sessions_path = write_json(
        tmp_path / "sessions.json",
        {
            "agent:main:telegram:dm:old": {
                "updated_at": "2026-06-05T13:00:00",
                "origin": {
                    "platform": "telegram",
                    "chat_id": "old",
                    "user_id": "old",
                    "message_id": "1",
                },
            },
            "agent:main:telegram:dm:6808688675": {
                "updated_at": "2026-06-05T13:55:00",
                "origin": {
                    "platform": "telegram",
                    "chat_id": "6808688675",
                    "chat_name": "菸草",
                    "user_id": "6808688675",
                    "user_name": "菸草",
                    "message_id": "61",
                },
            },
        },
    )
    args = SimpleNamespace(
        workspace=tmp_path,
        sessions_path=sessions_path,
        from_latest_session=True,
        update_path=None,
        platform="telegram",
        chat_id="",
        message_id="",
        user_id="",
        user_name="",
        request_text="分析 M010 荷兰 vs 日本",
        match_id="M010",
        match_label="Netherlands vs Japan",
        created_at_utc="2026-06-05T13:56:00Z",
        direct_request_id=None,
        report_path="",
        manifest_path="",
        status="received",
        cache_mode="",
        source_snapshot_id="",
        report_id="",
        api_refresh_performed=None,
        header_lines=False,
    )

    result = direct_request_record.write_record(args)

    record = result["record"]
    assert record["chat_id"] == "6808688675"
    assert record["message_id"] == "unknown"
    assert record["message_id_source"] == "session_unreliable"
    assert record["message_id_exact"] is False
    assert record["user_id"] == "6808688675"
    assert record["status"] == "received"


def test_direct_request_record_keeps_explicit_message_id_exact(tmp_path: Path) -> None:
    sessions_path = write_json(
        tmp_path / "sessions.json",
        {
            "agent:main:telegram:dm:6808688675": {
                "updated_at": "2026-06-05T13:55:00",
                "origin": {
                    "platform": "telegram",
                    "chat_id": "6808688675",
                    "chat_name": "菸草",
                    "user_id": "6808688675",
                    "user_name": "菸草",
                    "message_id": "61",
                },
            },
        },
    )
    args = SimpleNamespace(
        workspace=tmp_path,
        sessions_path=sessions_path,
        from_latest_session=True,
        update_path=None,
        platform="telegram",
        chat_id="",
        message_id="2169",
        user_id="",
        user_name="",
        request_text="分析 M010 荷兰 vs 日本",
        match_id="M010",
        match_label="Netherlands vs Japan",
        created_at_utc="2026-06-05T13:56:00Z",
        direct_request_id=None,
        report_path="",
        manifest_path="",
        status="received",
        cache_mode="",
        source_snapshot_id="",
        report_id="",
        api_refresh_performed=None,
        header_lines=False,
    )

    result = direct_request_record.write_record(args)

    record = result["record"]
    assert record["message_id"] == "2169"
    assert record["message_id_source"] == "explicit"
    assert record["message_id_exact"] is True


def test_direct_request_record_rejects_invalid_completed_manifest_path(tmp_path: Path) -> None:
    bad_manifest = tmp_path / "reports" / "artifacts" / "numeric-M010.json"
    report = tmp_path / "reports" / "match" / "M010.md"
    bad_manifest.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    bad_manifest.write_text("{not json", encoding="utf-8")
    report.write_text("# report\n", encoding="utf-8")
    args = SimpleNamespace(
        workspace=tmp_path,
        sessions_path=tmp_path / "sessions.json",
        from_latest_session=False,
        update_path=None,
        platform="telegram",
        chat_id="6808688675",
        message_id="2169",
        user_id="6808688675",
        user_name="菸草",
        request_text="分析 荷兰 vs 日本",
        match_id="M010",
        match_label="Netherlands vs Japan",
        created_at_utc="2026-06-06T12:00:00Z",
        direct_request_id="direct:badmanifest",
        report_path=str(report),
        manifest_path=str(bad_manifest),
        status="completed_cached",
        cache_mode="reuse_existing_report",
        source_snapshot_id="",
        report_id="",
        api_refresh_performed="false",
    )

    with pytest.raises(ValueError, match="valid manifest JSON"):
        direct_request_record.write_record(args)


def test_report_guard_rejects_received_direct_request_record(tmp_path: Path) -> None:
    _request_path, _manifest_path, report_path = write_relay_ready_direct_fixture(tmp_path, "received")

    result = report_guard.validate_report(report_path)

    assert result["valid"] is False
    assert "status must be completed or completed_cached" in " ".join(result["errors"])


def test_report_guard_accepts_completed_cached_direct_request_backlink(tmp_path: Path) -> None:
    _request_path, _manifest_path, report_path = write_relay_ready_direct_fixture(tmp_path, "completed_cached")

    result = report_guard.validate_report(report_path)

    assert result["valid"] is True
    assert result["direct_request_record"]["status"] == "completed_cached"
    assert result["direct_request_record"]["api_refresh_performed"] is False


def test_report_contract_rejects_role_engine_conclusion_without_evidence_numbers(tmp_path: Path) -> None:
    _request_path, manifest_path, _report_path = write_relay_ready_direct_fixture(tmp_path, "completed_cached")
    role_path = write_json(
        tmp_path / "reports" / "artifacts" / "role-engine.json",
        {
            "artifact_id": "role_engine:m010",
            "artifact_type": "role_engine",
            "artifact_kind": "role_engine",
            "engine_contract": "wc26.role_engine.v1",
            "engine_version": "deterministic_v1",
            "role_conclusions": [
                {
                    "evidence_id": "role:public_bias:001",
                    "role": "public_bias",
                    "role_label_zh": "散户心理",
                    "decision": "CONFIRMED",
                    "actionability": "never_actionable",
                    "hypothesis_zh": "判断大众叙事是否挤向热门侧",
                    "interpretation_zh": "soft 书压低热门侧。",
                    "trigger_artifacts": ["path_a_crossbook", "devig_1x2"],
                    "artifact_sources": [{"capability": "path_a_crossbook", "artifact_id": "crossbook:m010"}],
                    "evidence_numbers": [],
                }
            ],
        },
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"].append(
        {
            "artifact_id": "role_engine:m010",
            "artifact_type": "role_engine",
            "script": "role_engine.py",
            "path": str(role_path),
            "provides": ["role_engine"],
        }
    )

    result = report_contract.validate_manifest(manifest, manifest_path)

    assert result["valid"] is False
    assert "role_engine" in " ".join(result["errors"])
    assert "evidence_numbers" in " ".join(result["errors"])


def test_report_contract_rejects_t72_window_too_early(tmp_path: Path) -> None:
    _request_path, manifest_path, _report_path = write_relay_ready_direct_fixture(tmp_path, "completed_cached")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["window"] = "T-72h_early"
    manifest["timing_class"] = "early_structural"
    manifest["entry_time_utc"] = "2026-06-05T14:34:08Z"
    manifest["match"]["kickoff_utc"] = "2026-06-14T20:00:00Z"

    result = report_contract.validate_manifest(manifest, manifest_path)

    assert result["valid"] is False
    assert "T-72h_early inconsistent" in " ".join(result["errors"])
    assert "T-9d" in " ".join(result["errors"])


def test_report_contract_accepts_early_structural_before_t72(tmp_path: Path) -> None:
    _request_path, manifest_path, _report_path = write_relay_ready_direct_fixture(tmp_path, "completed_cached")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["window"] = "early_structural"
    manifest["window_display"] = "T-9d"
    manifest["timing_class"] = "early_structural"
    manifest["entry_time_utc"] = "2026-06-05T14:34:08Z"
    manifest["match"]["kickoff_utc"] = "2026-06-14T20:00:00Z"

    result = report_contract.validate_manifest(manifest, manifest_path)

    assert result["valid"] is True
    assert "window" not in " ".join(result["errors"])


def test_report_guard_rejects_partial_source_quality_cap_mismatch(tmp_path: Path) -> None:
    _request_path, manifest_path, report_path = write_relay_ready_direct_fixture(tmp_path, "completed_cached")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["report_completeness"] = "partial"
    manifest["source_quality"] = "B"
    manifest["source_quality_cap"] = "B"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report_text = report_path.read_text(encoding="utf-8").replace(
        "source_quality: B\n",
        "source_quality: B\nsource_quality_cap: B\nreport_completeness: partial\n",
    )
    report_path.write_text(report_text, encoding="utf-8")

    result = report_guard.validate_report(report_path)

    assert result["valid"] is False
    assert "source_quality_cap header B does not match report_contract C" in result["errors"]
    assert "source_quality_cap manifest B does not match report_contract C" in result["errors"]


def test_direct_report_bind_marks_cached_report_relay_ready(tmp_path: Path) -> None:
    request_path, manifest_path, report_path = write_relay_ready_direct_fixture(tmp_path, "received")
    args = SimpleNamespace(
        workspace=tmp_path,
        sessions_path=tmp_path / "sessions.json",
        direct_request_path=request_path,
        manifest=manifest_path,
        report=report_path,
        status="completed_cached",
        cache_mode="reuse_existing_report",
        source_snapshot_id="the-odds-api-multibook-test.json",
        report_id="report:m010",
        match_id="M010",
        match_label="Netherlands vs Japan",
        api_refresh_performed="false",
    )

    bind_result = direct_report_bind.bind(args)
    guard_result = report_guard.validate_report(report_path)

    assert bind_result["record"]["status"] == "completed_cached"
    assert bind_result["record"]["api_refresh_performed"] is False
    assert guard_result["valid"] is True


def test_direct_report_bind_rejects_numeric_artifact_as_manifest(tmp_path: Path) -> None:
    request_path, _manifest_path, report_path = write_relay_ready_direct_fixture(tmp_path, "received")
    numeric_path = tmp_path / "reports" / "artifacts" / "numeric-M010.json"
    numeric_path.parent.mkdir(parents=True, exist_ok=True)
    numeric_path.write_text("{not json", encoding="utf-8")
    args = SimpleNamespace(
        workspace=tmp_path,
        sessions_path=tmp_path / "sessions.json",
        direct_request_path=request_path,
        manifest=numeric_path,
        report=report_path,
        status="completed_cached",
        cache_mode="reuse_existing_report",
        source_snapshot_id="",
        report_id="",
        match_id="M010",
        match_label="Netherlands vs Japan",
        api_refresh_performed="false",
    )

    with pytest.raises(ValueError, match="valid manifest JSON"):
        direct_report_bind.bind(args)


def test_report_contract_rejects_unprovenanced_actionable_number() -> None:
    manifest = valid_manifest()
    manifest["numbers"][0].pop("artifact_id")
    result = report_contract.validate_manifest(manifest)
    assert result["valid"] is False
    assert result["actionable_allowed"] is False
    assert result["source_quality_cap"] == "C"


def test_report_contract_rejects_raw_model_edge() -> None:
    manifest = valid_manifest()
    manifest["numbers"][0]["uses_p_model_directly"] = True
    result = report_contract.validate_manifest(manifest)
    assert result["valid"] is False
    assert "raw model" in " ".join(result["errors"])


def test_simulation_can_never_be_actionable() -> None:
    manifest = valid_manifest()
    manifest["mode"] = "simulation"
    manifest["final_status"] = "simulation_only"
    result = report_contract.validate_manifest(manifest)
    assert result["valid"] is False
    assert result["actionable_allowed"] is False
    assert result["source_quality_cap"] == "C"


def test_live_direct_manifest_rejects_thin_m008_shape(tmp_path: Path) -> None:
    artifact_path = tmp_path / "devig-M008-1x2.json"
    artifact_path.write_text(
        json.dumps(
            {
                "artifact_id": "devig-M008-1x2-20260605T120000Z",
                "artifact_type": "devig",
                "odds_unit_contract": "all probability and EV math uses normalized decimal odds > 1.0",
                "method": "multiplicative",
                "no_vig_probabilities": [0.0812, 0.1447, 0.7741],
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "manifest_id": "manifest-M008-20260605T120000Z",
        "match_id": "M008",
        "mode": "live",
        "source_quality": "B",
        "final_status": "pass",
        "numbers": [
            {
                "id": "p_market_switzerland_win",
                "kind": "no_vig",
                "value": 0.7741,
                "snapshot_id": "oddspapi-t16-20260605T075849Z",
                "artifact_id": "devig-M008-1x2-20260605T120000Z",
                "artifact_type": "devig",
            }
        ],
        "artifacts": [
            {
                "artifact_id": "devig-M008-1x2-20260605T120000Z",
                "artifact_type": "devig",
                "script": "devig.py",
                "path": str(artifact_path),
                "provides": ["no_vig"],
            }
        ],
    }

    result = report_contract.validate_manifest(manifest, tmp_path / "manifest.json")

    assert result["valid"] is False
    assert "workflow_contract" in " ".join(result["errors"])


def test_live_direct_partial_manifest_allows_declared_missing_h2h(tmp_path: Path) -> None:
    request_path = tmp_path / "direct-request.json"
    request_path.write_text(
        json.dumps(
            {
                "direct_request_id": "direct:m009",
                "platform": "telegram",
                "chat_id": "6808688675",
                "request_text": "分析 M009 德国 vs 库拉索",
                "created_at_utc": "2026-06-05T11:40:00Z",
            }
        ),
        encoding="utf-8",
    )
    artifacts = []
    for artifact_id, artifact_kind, provides in [
        ("crossbook:m009", "cross_book_scan", ["path_a_crossbook"]),
        ("ah:m009", "asian_handicap_market", ["asian_handicap"]),
        ("totals:m009", "totals_market", ["totals"]),
        ("mechanism:m009", "mechanism_audit", ["mechanism_audit"]),
    ]:
        path = tmp_path / f"{artifact_id.replace(':', '-')}.json"
        if provides == ["path_a_crossbook"]:
            payload = valid_crossbook_payload()
        elif provides == ["mechanism_audit"]:
            payload = valid_mechanism_audit_payload("watch")
        else:
            payload = {"artifact_id": artifact_id, "artifact_kind": artifact_kind}
        path.write_text(json.dumps(payload), encoding="utf-8")
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact_kind,
                "script": "fixture",
                "path": str(path),
                "provides": provides,
            }
        )
    manifest = {
        "workflow_contract": "wc26.direct_report.v1",
        "report_completeness": "partial",
        "direct_request_id": "direct:m009",
        "direct_request_path": str(request_path),
        "mode": "live",
        "source_quality": "C",
        "final_status": "watch",
        "source_freshness": {
            "sources": [
                {"name": "the-odds-api", "snapshot_id": "the-odds-api-20260605T075844Z"}
            ]
        },
        "analysis_gates": {
            "devig_three_method": "skipped_missing_source",
            "path_a_crossbook": "pass",
            "asian_handicap": "pass",
            "totals": "pass",
            "path_b_model_diagnostic": "diagnostic",
            "path_c_consistency": "skipped_missing_source",
            "mechanism_audit": "pass",
            "source_freshness": "pass",
        },
        "skipped_sections": [
            {
                "gate": "devig_three_method",
                "reason": "Pinnacle h2h missing in snapshot",
                "impact": "1X2 no-vig unavailable",
            },
            {
                "gate": "path_c_consistency",
                "reason": "Path C requires 1X2 + AH + totals in the same snapshot",
                "impact": "consistency triangle unavailable",
            },
        ],
        "numbers": [],
        "artifacts": artifacts,
    }

    result = report_contract.validate_manifest(manifest, tmp_path / "manifest.json")

    assert result["valid"] is True
    assert result["actionable_allowed"] is False
    assert result["source_quality_cap"] == "C"


def test_report_contract_recomputes_actionable_path_a_edge_from_snapshot(tmp_path: Path) -> None:
    request_path = write_json(
        tmp_path / "direct-request.json",
        {
            "direct_request_id": "direct:m010",
            "platform": "telegram",
            "chat_id": "6808688675",
            "request_text": "分析 M010 荷兰 vs 日本",
            "created_at_utc": "2026-06-05T11:40:00Z",
        },
    )
    snapshot_path = write_multibook_snapshot(tmp_path / "the-odds-api-multibook-test.json", japan_price=4.20)
    crossbook_payload = build_crossbook_from_snapshot(snapshot_path)
    assert crossbook_payload["summary"]["actionable_count"] == 1
    crossbook_payload["markets"]["h2h"]["edges"][0]["ev_shin"] = 0.20
    crossbook_payload["summary"]["best_edge"]["ev_shin"] = 0.20
    crossbook_payload["summary"]["best_actionable_edge"]["ev_shin"] = 0.20
    crossbook_path = write_json(tmp_path / "crossbook.json", crossbook_payload)
    devig_path = write_json(
        tmp_path / "devig.json",
        {
            "artifact_type": "devig",
            "artifact_kind": "scalar_market",
            "odds_unit_contract": "all probability and EV math uses normalized decimal odds > 1.0",
            "devig_methods": {
                "shin": [0.49, 0.26, 0.25],
                "power": [0.49, 0.26, 0.25],
                "multiplicative": [0.49, 0.26, 0.25],
            },
            "survives_all_methods": True,
        },
    )
    ah_path = write_json(tmp_path / "ah.json", {"artifact_kind": "asian_handicap_market"})
    totals_path = write_json(tmp_path / "totals.json", {"artifact_kind": "totals_market"})
    path_c_path = write_json(
        tmp_path / "path-c.json",
        {"artifact_kind": "consistency_triangle", "signal": {"type": None}, "discrepancy": {"pp": 0.0}},
    )
    audit_path = write_json(
        tmp_path / "mechanism-audit.json",
        {
            "artifact_type": "mechanism_audit",
            "artifact_kind": "mechanism_audit",
            "audit_contract": "wc26.mechanism_audit.v1",
            "mechanism_audit_status": "complete",
            "required_final_status": "pass",
            "blocking_mechanisms": [],
            "mechanisms": {
                "path_a_crossbook": {
                    "status": "COMPLETE",
                    "required_for_complete": True,
                    "quotes_scanned": 3,
                    "edge_count": 1,
                    "noise_edge_count": 0,
                    "actionable_count": 1,
                    "qualified_play_count": 1,
                },
                "path_b_model_diagnostic": {"status": "COMPLETE", "required_for_complete": True},
                "path_c_consistency": {"status": "COMPLETE", "required_for_complete": True},
            },
            "hypothesis_decisions": [
                {
                    "source": "path_a_crossbook",
                    "subject": "marathonbet h2h japan",
                    "decision": "CONFIRMED_ACTIONABLE",
                    "book": "marathonbet",
                    "market_key": "h2h",
                    "outcome": "japan",
                    "evidence": "cross_book_scan edge row",
                }
            ],
        },
    )
    manifest = {
        "workflow_contract": "wc26.direct_report.v1",
        "direct_request_id": "direct:m010",
        "direct_request_path": str(request_path),
        "match_id": "M010",
        "match": {"match_id": "M010", "home": "Netherlands", "away": "Japan"},
        "mode": "live",
        "source_quality": "B",
        "final_status": "pass",
        "source_freshness": {"sources": [{"name": "the-odds-api", "snapshot_id": snapshot_path.name}]},
        "analysis_gates": {
            "devig_three_method": "pass",
            "path_a_crossbook": "pass",
            "asian_handicap": "pass",
            "totals": "pass",
            "path_b_model_diagnostic": "diagnostic",
            "path_c_consistency": "pass",
            "mechanism_audit": "pass",
            "source_freshness": "pass",
        },
        "numbers": [],
        "artifacts": [
            {"artifact_id": "devig:m010", "artifact_type": "devig", "script": "devig.py", "path": str(devig_path), "provides": ["devig_1x2"]},
            {"artifact_id": "crossbook:m010", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook_path), "provides": ["path_a_crossbook"]},
            {"artifact_id": "ah:m010", "artifact_type": "devig", "script": "devig.py", "path": str(ah_path), "provides": ["asian_handicap"]},
            {"artifact_id": "totals:m010", "artifact_type": "totals", "script": "numeric_artifact.py", "path": str(totals_path), "provides": ["totals"]},
            {"artifact_id": "pathc:m010", "artifact_type": "consistency_triangle", "script": "consistency_triangle.py", "path": str(path_c_path), "provides": ["path_c_consistency"]},
            {"artifact_id": "mechanism:m010", "artifact_type": "mechanism_audit", "script": "mechanism_audit.py", "path": str(audit_path), "provides": ["mechanism_audit"]},
        ],
    }

    result = report_contract.validate_manifest(manifest, tmp_path / "manifest.json")

    assert result["valid"] is False
    assert "input_snapshot recompute" in " ".join(result["errors"])


def test_report_contract_rejects_report_text_with_reversed_ah_totals_probs(tmp_path: Path) -> None:
    request_path = write_json(
        tmp_path / "direct-request.json",
        {
            "direct_request_id": "direct:m009",
            "platform": "telegram",
            "chat_id": "6808688675",
            "request_text": "分析 M009 德国 vs 库拉索",
            "created_at_utc": "2026-06-05T17:45:08Z",
        },
    )
    report_path = tmp_path / "M009-report.md"
    crossbook_path = write_json(
        tmp_path / "crossbook-m009.json",
        {
            "artifact_type": "crossbook_scan",
            "artifact_kind": "cross_book_scan",
            "script": "cross_book_scan.py",
            "provides": ["path_a_crossbook"],
            "input_snapshot": "snapshot.json",
            "source_snapshot_id": "snapshot.json",
            "summary": {
                "markets_scanned": ["spreads", "totals"],
                "quotes_scanned": 0,
                "edge_count": 0,
                "noise_edge_count": 0,
                "actionable_count": 0,
                "qualified_play_count": 0,
                "best_edge": None,
            },
            "markets": {
                "spreads": {
                    "status": "ok",
                    "sharp_anchor": "pinnacle",
                    "devig_primary": "shin",
                    "outcomes_scanned": ["curaçao@3.5", "germany@-3.5"],
                    "quotes_scanned": 0,
                    "fair_probs": {
                        "shin": {"curaçao@3.5": 0.496, "germany@-3.5": 0.504},
                        "power": {"curaçao@3.5": 0.4959, "germany@-3.5": 0.5041},
                        "multiplicative": {"curaçao@3.5": 0.4961, "germany@-3.5": 0.5039},
                    },
                    "quotes": [],
                    "edges": [],
                },
                "totals": {
                    "status": "ok",
                    "sharp_anchor": "pinnacle",
                    "devig_primary": "shin",
                    "outcomes_scanned": ["over@4.25", "under@4.25"],
                    "quotes_scanned": 0,
                    "fair_probs": {
                        "shin": {"over@4.25": 0.5041, "under@4.25": 0.4959},
                        "power": {"over@4.25": 0.5042, "under@4.25": 0.4958},
                        "multiplicative": {"over@4.25": 0.5039, "under@4.25": 0.4961},
                    },
                    "quotes": [],
                    "edges": [],
                },
            },
        },
    )
    mechanism_payload = valid_mechanism_audit_payload("watch")
    mechanism_payload["mechanisms"]["path_a_crossbook"].update(
        {
            "quotes_scanned": 0,
            "edge_count": 0,
            "noise_edge_count": 0,
            "actionable_count": 0,
            "qualified_play_count": 0,
        }
    )
    mechanism_path = write_json(tmp_path / "mechanism.json", mechanism_payload)
    manifest = {
        "workflow_contract": "wc26.direct_report.v1",
        "report_completeness": "partial",
        "direct_request_id": "direct:m009",
        "direct_request_path": str(request_path),
        "report_path": str(report_path),
        "match_id": "M009",
        "match": {"match_id": "M009", "home": "Germany", "away": "Curaçao"},
        "mode": "live",
        "source_quality": "B",
        "source_quality_cap": "C",
        "final_status": "watch",
        "source_freshness": {"sources": [{"name": "the-odds-api", "snapshot_id": "snapshot:m009"}]},
        "analysis_gates": {
            "devig_three_method": "skipped_missing_source",
            "path_a_crossbook": "pass",
            "asian_handicap": "pass",
            "totals": "pass",
            "path_b_model_diagnostic": "diagnostic",
            "path_c_consistency": "skipped_missing_source",
            "mechanism_audit": "pass",
            "source_freshness": "pass",
        },
        "skipped_sections": [
            {"gate": "devig_three_method", "reason": "Pinnacle H2H missing", "impact": "1X2 unavailable"},
            {"gate": "path_c_consistency", "reason": "H2H missing", "impact": "triangle unavailable"},
        ],
        "artifacts": [
            {"artifact_id": "crossbook:m009", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook_path), "provides": ["path_a_crossbook", "asian_handicap", "totals"]},
            {"artifact_id": "mechanism:m009", "artifact_type": "mechanism_audit", "script": "mechanism_audit.py", "path": str(mechanism_path), "provides": ["mechanism_audit"]},
        ],
    }
    report_path.write_text(
        "\n".join(
            [
                "# WC26 M009 Germany vs Curaçao",
                "Pinnacle AH -3.5 去水: GER cover 49.6% / CUR cover 50.4%",
                "Pinnacle Totals 4.25 去水: Over 49.6% / Under 50.4%",
            ]
        ),
        encoding="utf-8",
    )

    result = report_contract.validate_manifest(manifest, tmp_path / "manifest.json")

    assert result["valid"] is False
    assert "report text spreads probability" in " ".join(result["errors"])
    assert "report text totals probability" in " ".join(result["errors"])


def test_live_direct_pass_incomplete_allows_audit_blocked_mechanism(tmp_path: Path) -> None:
    request_path = tmp_path / "direct-request.json"
    request_path.write_text(
        json.dumps(
            {
                "direct_request_id": "direct:m010",
                "platform": "telegram",
                "chat_id": "6808688675",
                "request_text": "分析 M010 荷兰 vs 日本",
                "created_at_utc": "2026-06-05T11:40:00Z",
            }
        ),
        encoding="utf-8",
    )
    devig_path = tmp_path / "devig.json"
    devig_path.write_text(
        json.dumps(
            {
                "artifact_type": "devig",
                "artifact_kind": "scalar_market",
                "odds_unit_contract": "all probability and EV math uses normalized decimal odds > 1.0",
                "devig_methods": {
                    "shin": [0.49, 0.26, 0.25],
                    "power": [0.49, 0.26, 0.25],
                    "multiplicative": [0.49, 0.26, 0.25],
                },
                "survives_all_methods": True,
            }
        ),
        encoding="utf-8",
    )
    crossbook_path = tmp_path / "crossbook.json"
    crossbook_path.write_text(json.dumps(valid_crossbook_payload()), encoding="utf-8")
    ah_path = tmp_path / "ah.json"
    ah_path.write_text(json.dumps({"artifact_kind": "asian_handicap_market"}), encoding="utf-8")
    totals_path = tmp_path / "totals.json"
    totals_path.write_text(json.dumps({"artifact_kind": "totals_market"}), encoding="utf-8")
    audit_path = tmp_path / "mechanism-audit.json"
    audit_path.write_text(json.dumps(valid_mechanism_audit_payload("pass_incomplete")), encoding="utf-8")
    manifest = {
        "workflow_contract": "wc26.direct_report.v1",
        "direct_request_id": "direct:m010",
        "direct_request_path": str(request_path),
        "mode": "live",
        "source_quality": "B",
        "final_status": "pass_incomplete",
        "source_freshness": {"sources": [{"name": "the-odds-api"}]},
        "analysis_gates": {
            "devig_three_method": "pass",
            "path_a_crossbook": "pass",
            "asian_handicap": "pass",
            "totals": "pass",
            "path_b_model_diagnostic": "diagnostic",
            "path_c_consistency": "skipped_missing_source",
            "mechanism_audit": "pass",
            "source_freshness": "pass",
        },
        "numbers": [],
        "artifacts": [
            {"artifact_id": "devig:m010", "artifact_type": "devig", "script": "devig.py", "path": str(devig_path), "provides": ["devig_1x2"]},
            {"artifact_id": "crossbook:m010", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook_path), "provides": ["path_a_crossbook"]},
            {"artifact_id": "ah:m010", "artifact_type": "devig", "script": "devig.py", "path": str(ah_path), "provides": ["asian_handicap"]},
            {"artifact_id": "totals:m010", "artifact_type": "totals", "script": "numeric_artifact.py", "path": str(totals_path), "provides": ["totals"]},
            {"artifact_id": "mechanism:m010", "artifact_type": "mechanism_audit", "script": "mechanism_audit.py", "path": str(audit_path), "provides": ["mechanism_audit"]},
        ],
    }

    result = report_contract.validate_manifest(manifest, tmp_path / "manifest.json")

    assert result["valid"] is True
    assert result["actionable_allowed"] is False


def test_live_direct_complete_rejects_fake_crossbook_capability(tmp_path: Path) -> None:
    request_path = tmp_path / "direct-request.json"
    request_path.write_text(
        json.dumps(
            {
                "direct_request_id": "direct:m010",
                "platform": "telegram",
                "chat_id": "6808688675",
                "request_text": "分析 M010 荷兰 vs 日本",
                "created_at_utc": "2026-06-05T11:40:00Z",
            }
        ),
        encoding="utf-8",
    )
    devig_path = tmp_path / "devig-fake-crossbook.json"
    devig_path.write_text(
        json.dumps(
            {
                "artifact_id": "devig:m010",
                "artifact_type": "devig",
                "devig_methods": {
                    "shin": [0.49, 0.26, 0.25],
                    "power": [0.49, 0.26, 0.25],
                    "multiplicative": [0.49, 0.26, 0.25],
                },
                "survives_all_methods": True,
                "provides": ["no_vig", "cross_book"],
            }
        ),
        encoding="utf-8",
    )
    ah_path = tmp_path / "ah.json"
    ah_path.write_text(json.dumps({"artifact_kind": "asian_handicap_market"}), encoding="utf-8")
    totals_path = tmp_path / "totals.json"
    totals_path.write_text(json.dumps({"artifact_kind": "totals_market"}), encoding="utf-8")
    path_c_path = tmp_path / "path-c.json"
    path_c_path.write_text(json.dumps({"artifact_kind": "consistency_triangle"}), encoding="utf-8")
    manifest = {
        "workflow_contract": "wc26.direct_report.v1",
        "direct_request_id": "direct:m010",
        "direct_request_path": str(request_path),
        "mode": "live",
        "source_quality": "B",
        "final_status": "pass",
        "source_freshness": {"sources": [{"name": "the-odds-api"}]},
        "analysis_gates": {
            "devig_three_method": "pass",
            "path_a_crossbook": "pass",
            "asian_handicap": "pass",
            "totals": "pass",
            "path_b_model_diagnostic": "diagnostic",
            "path_c_consistency": "pass",
            "source_freshness": "pass",
        },
        "numbers": [],
        "artifacts": [
            {"artifact_id": "devig:m010", "artifact_type": "devig", "script": "devig.py", "path": str(devig_path), "provides": ["devig_1x2", "path_a_crossbook"]},
            {"artifact_id": "ah:m010", "artifact_type": "devig", "script": "devig.py", "path": str(ah_path), "provides": ["asian_handicap"]},
            {"artifact_id": "totals:m010", "artifact_type": "totals", "script": "numeric_artifact.py", "path": str(totals_path), "provides": ["totals"]},
            {"artifact_id": "pathc:m010", "artifact_type": "consistency_triangle", "script": "consistency_triangle.py", "path": str(path_c_path), "provides": ["path_c_consistency"]},
        ],
    }

    result = report_contract.validate_manifest(manifest, tmp_path / "manifest.json")

    assert result["valid"] is False
    assert "cross_book_scan" in " ".join(result["errors"])


def test_live_direct_partial_manifest_cannot_claim_pass(tmp_path: Path) -> None:
    request_path = tmp_path / "direct-request.json"
    request_path.write_text(
        json.dumps(
            {
                "direct_request_id": "direct:m009",
                "platform": "telegram",
                "chat_id": "6808688675",
                "request_text": "分析 M009 德国 vs 库拉索",
                "created_at_utc": "2026-06-05T11:40:00Z",
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "workflow_contract": "wc26.direct_report.v1",
        "report_completeness": "partial",
        "direct_request_id": "direct:m009",
        "direct_request_path": str(request_path),
        "mode": "live",
        "source_quality": "C",
        "final_status": "pass",
        "source_freshness": {"sources": [{"name": "the-odds-api"}]},
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

    result = report_contract.validate_manifest(manifest, tmp_path / "manifest.json")

    assert result["valid"] is False
    assert "partial" in " ".join(result["errors"])


def test_report_guard_rejects_pending_guard_status(tmp_path: Path) -> None:
    artifact_path = tmp_path / "devig.json"
    artifact_path.write_text(
        json.dumps(
            {
                "artifact_id": "devig:M008:1x2",
                "artifact_type": "devig",
                "odds_unit_contract": "all probability and EV math uses normalized decimal odds > 1.0",
                "no_vig_probabilities": [0.08, 0.15, 0.77],
            }
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "mode": "live",
                "source_quality": "B",
                "final_status": "pass",
                "numbers": [
                    {
                        "name": "no_vig_away",
                        "kind": "no_vig",
                        "value": 0.77,
                        "snapshot_id": "snapshot:M008:1x2",
                        "artifact_id": "devig:M008:1x2",
                        "artifact_type": "devig",
                    }
                ],
                "artifacts": [
                    {
                        "artifact_id": "devig:M008:1x2",
                        "artifact_type": "devig",
                        "script": "devig.py",
                        "path": str(artifact_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "report.md"
    report_path.write_text(
        "\n".join(
            [
                "# WC26 M008 Qatar vs Switzerland",
                "",
                "mode: live",
                "source_quality: B",
                "final_status: pass",
                "artifact_manifest_path: " + str(manifest_path),
                "artifact_contract_status: pass",
                "report_guard_status: pending",
                "",
                "## 1. One-Line View",
                "PASS.",
            ]
        ),
        encoding="utf-8",
    )

    result = report_guard.validate_report(report_path)

    assert result["valid"] is False
    assert "report_guard_status" in " ".join(result["errors"])


def test_scalar_payload_records_three_devig_methods() -> None:
    payload, _numbers = numeric_artifact.scalar_payload(
        SimpleNamespace(
            snapshot_id="snapshot:M008:1x2",
            odds=[11.87, 6.66, 1.245],
            odds_format="decimal",
            prob=None,
            price=None,
            price_format="decimal",
            created_at_utc="2026-06-05T12:00:00Z",
        )
    )

    assert set(payload["devig_methods"]) == {"shin", "power", "multiplicative"}
    assert payload["devig_primary"] == "shin"
    assert isinstance(payload["survives_all_methods"], bool)


def test_poisson_score_matrix_exports_margin_distribution() -> None:
    matrix = model_margin.poisson_score_matrix(1.8, 0.7, max_goals=8)
    margins = model_margin.margin_distribution_from_score_matrix(matrix)
    assert abs(sum(margins.values()) - 1.0) < 1e-9
    assert len(margins) > 5
    assert sum(prob for margin, prob in margins.items() if margin > 0) > 0.5
