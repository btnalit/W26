#!/usr/bin/env python3
"""Direct Telegram summary contract for WC26 reports."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, rel: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


direct_summary = load_module("direct_summary", "skills/odds-analysis/scripts/direct_summary.py")


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_match_label_accepts_top_level_home_away() -> None:
    match_id, home, away = direct_summary.match_label({"match_id": "M010", "home": "Netherlands", "away": "Japan"})

    assert match_id == "M010"
    assert home == "Netherlands"
    assert away == "Japan"


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


def valid_mechanism_audit_payload(final_status: str = "pass") -> dict:
    path_c_blocked = final_status in {"watch", "pass_incomplete"}
    return {
        "artifact_type": "mechanism_audit",
        "artifact_kind": "mechanism_audit",
        "audit_contract": "wc26.mechanism_audit.v1",
        "script": "mechanism_audit.py",
        "source_manifest_id": "manifest:test",
        "match_id": "M008",
        "manifest_final_status": final_status,
        "mechanism_audit_status": "pass_incomplete" if path_c_blocked else "complete",
        "required_final_status": final_status,
        "review_required": path_c_blocked,
        "blocking_mechanisms": ["path_c_consistency"] if path_c_blocked else [],
        "mechanisms": {
            "path_a_crossbook": {
                "status": "COMPLETE",
                "required_for_complete": True,
                "artifact_id": "crossbook:scan",
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
                "artifact_id": None if path_c_blocked else "consistency:M008",
                "signal_type": None,
                "discrepancy_pp": None if path_c_blocked else 0.0,
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


def test_invalid_thin_manifest_summary_blocks_instead_of_pass(tmp_path: Path) -> None:
    artifact = write_json(
        tmp_path / "devig-M008-1x2.json",
        {
            "artifact_id": "devig-M008-1x2-20260605T120000Z",
            "artifact_type": "devig",
            "odds_unit_contract": "all probability and EV math uses normalized decimal odds > 1.0",
            "method": "multiplicative",
            "no_vig_probabilities": [0.0812, 0.1447, 0.7741],
        },
    )
    manifest = write_json(
        tmp_path / "manifest-M008.json",
        {
            "match_id": "M008",
            "teams": ["Qatar", "Switzerland"],
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
                    "label": "Switzerland win no-vig probability",
                }
            ],
            "artifacts": [
                {
                    "artifact_id": "devig-M008-1x2-20260605T120000Z",
                    "artifact_type": "devig",
                    "script": "devig.py",
                    "path": str(artifact),
                    "provides": ["no_vig"],
                }
            ],
        },
    )

    summary = direct_summary.build_summary(manifest)

    assert "report_contract: FAIL" in summary
    assert "不能按完整盘口报告发送" in summary
    assert "direct_request_id" in summary
    assert "Path A" in summary
    assert "亚盘" in summary
    assert "大小球" in summary
    assert "Path C" in summary
    assert "PASS — NO PLAY" not in summary


def test_complete_manifest_summary_contains_required_direct_sections(tmp_path: Path) -> None:
    request_path = write_json(
        tmp_path / "direct_requests" / "2026-06-05" / "direct-abc.json",
        {
            "schema_version": "wc26.direct_request.v1",
            "direct_request_id": "direct:abc",
            "platform": "telegram",
            "chat_id": "6808688675",
            "request_text": "分析卡塔尔 vs 瑞士",
            "created_at_utc": "2026-06-05T10:57:12Z",
        },
    )
    devig_artifact = write_json(
        tmp_path / "devig-1x2.json",
        {
            "artifact_id": "devig:1x2",
            "artifact_type": "devig",
            "artifact_kind": "scalar_market",
            "odds_unit_contract": "all probability and EV math uses normalized decimal odds > 1.0",
            "devig_primary": "shin",
            "devig_methods": {
                "shin": [0.081, 0.145, 0.774],
                "power": [0.082, 0.146, 0.772],
                "multiplicative": [0.0812, 0.1447, 0.7741],
            },
            "survives_all_methods": True,
            "no_vig_probabilities": [0.081, 0.145, 0.774],
        },
    )
    crossbook_artifact = write_json(
        tmp_path / "crossbook.json",
        valid_crossbook_payload(),
    )
    ah_artifact = write_json(
        tmp_path / "ah.json",
        {
            "artifact_id": "devig:ah",
            "artifact_kind": "asian_handicap_market",
            "line": -1.75,
            "price": 1.943,
            "ev": -0.012,
            "kelly_fraction_full": 0.0,
            "settlement_contract": "asian_handicap_by_legs",
        },
    )
    totals_artifact = write_json(
        tmp_path / "totals.json",
        {
            "artifact_id": "totals:main",
            "artifact_kind": "totals_market",
            "line": 2.75,
            "over_price": 1.869,
            "under_price": 2.02,
            "no_vig_over": 0.519,
        },
    )
    path_c_artifact = write_json(
        tmp_path / "path-c.json",
        {
            "artifact_id": "consistency:M008",
            "artifact_kind": "consistency_triangle",
            "signal": {"type": None, "strength": "无"},
            "discrepancy": {"pp": 2.1, "direction": "noise"},
        },
    )
    mechanism_audit = write_json(
        tmp_path / "mechanism-audit.json",
        valid_mechanism_audit_payload("pass"),
    )
    manifest = write_json(
        tmp_path / "manifest.json",
        {
            "workflow_contract": "wc26.direct_report.v1",
            "direct_request_id": "direct:abc",
            "direct_request_path": str(request_path),
            "match_id": "M008",
            "match": {
                "match_id": "M008",
                "home": "Qatar",
                "away": "Switzerland",
                "kickoff_utc": "2026-06-13T19:00:00Z",
                "venue": "Levi's Stadium",
            },
            "mode": "live",
            "source_quality": "B",
            "final_status": "pass",
            "source_freshness": {"sources": [{"name": "oddspapi", "snapshot_id": "oddspapi-t16-20260605T075849Z"}]},
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
            "numbers": [
                {
                    "name": "switzerland_no_vig",
                    "kind": "no_vig",
                    "value": 0.774,
                    "snapshot_id": "snapshot:M008:1x2",
                    "artifact_id": "devig:1x2",
                    "artifact_type": "devig",
                    "label": "Switzerland win no-vig probability",
                }
            ],
            "artifacts": [
                {"artifact_id": "devig:1x2", "artifact_type": "devig", "script": "devig.py", "path": str(devig_artifact), "provides": ["devig_1x2"]},
                {"artifact_id": "crossbook:scan", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook_artifact), "provides": ["path_a_crossbook"]},
                {"artifact_id": "devig:ah", "artifact_type": "devig", "script": "devig.py", "path": str(ah_artifact), "provides": ["asian_handicap"]},
                {"artifact_id": "totals:main", "artifact_type": "totals", "script": "numeric_artifact.py", "path": str(totals_artifact), "provides": ["totals"]},
                {"artifact_id": "consistency:M008", "artifact_type": "consistency_triangle", "script": "consistency_triangle.py", "path": str(path_c_artifact), "provides": ["path_c_consistency"]},
                {"artifact_id": "mechanism:M008", "artifact_type": "mechanism_audit", "script": "mechanism_audit.py", "path": str(mechanism_audit), "provides": ["mechanism_audit"]},
            ],
        },
    )

    summary = direct_summary.build_summary(manifest)

    for required in [
        "① 比赛基本信息",
        "② 数据与契约",
        "③ 1X2 去水",
        "④ Path A 跨书商",
        "⑤ 亚盘与大小球",
        "⑥ Path B 模型纪律",
        "⑦ Path C 一致性",
        "⑨ 博弈裁决 / 机制审计",
        "⑩ 结论与复盘",
    ]:
        assert required in summary
    assert "report_contract: PASS" in summary
    assert "direct:abc" in summary
    assert "瑞士" in summary or "Switzerland" in summary
    assert "marathonbet h2h japan" in summary
    assert "CONFIRMED_NOISE" in summary
    assert "odds=4.05" in summary
    assert "QP数量" not in summary
    assert "QP=" not in summary
    assert "actionable=0" in summary
    assert "noise=1" in summary
    assert "不自动下注" in summary


def test_partial_manifest_summary_shows_skipped_sections(tmp_path: Path) -> None:
    request_path = write_json(
        tmp_path / "direct-request.json",
        {
            "direct_request_id": "direct:m009",
            "platform": "telegram",
            "chat_id": "6808688675",
            "request_text": "分析 M009 德国 vs 库拉索",
            "created_at_utc": "2026-06-05T11:40:00Z",
        },
    )
    crossbook = write_json(tmp_path / "crossbook.json", valid_crossbook_payload())
    ah = write_json(tmp_path / "ah.json", {"artifact_kind": "asian_handicap_market", "line": -2.5, "ev": 0.0})
    totals = write_json(tmp_path / "totals.json", {"artifact_kind": "totals_market", "line": 3.5})
    mechanism_audit = write_json(tmp_path / "mechanism-audit.json", valid_mechanism_audit_payload("watch"))
    manifest = write_json(
        tmp_path / "partial-manifest.json",
        {
            "workflow_contract": "wc26.direct_report.v1",
            "report_completeness": "partial",
            "direct_request_id": "direct:m009",
            "direct_request_path": str(request_path),
            "match_id": "M009",
            "match": {"home": "Germany", "away": "Curaçao", "kickoff_utc": "2026-06-14T17:00:00Z"},
            "mode": "live",
            "source_quality": "C",
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
                {
                    "gate": "devig_three_method",
                    "reason": "Pinnacle h2h missing in snapshot",
                    "impact": "1X2 no-vig unavailable",
                },
                {
                    "gate": "path_c_consistency",
                    "reason": "Path C requires h2h",
                    "impact": "consistency triangle unavailable",
                },
            ],
            "numbers": [],
            "artifacts": [
                {"artifact_id": "crossbook:m009", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook), "provides": ["path_a_crossbook"]},
                {"artifact_id": "ah:m009", "artifact_type": "devig", "script": "devig.py", "path": str(ah), "provides": ["asian_handicap"]},
                {"artifact_id": "totals:m009", "artifact_type": "totals", "script": "numeric_artifact.py", "path": str(totals), "provides": ["totals"]},
                {"artifact_id": "mechanism:m009", "artifact_type": "mechanism_audit", "script": "mechanism_audit.py", "path": str(mechanism_audit), "provides": ["mechanism_audit"]},
            ],
        },
    )

    summary = direct_summary.build_summary(manifest)

    assert "PARTIAL / WATCH" in summary
    assert "report_contract: PASS" in summary
    assert "Pinnacle h2h missing" in summary
    assert "Path C requires h2h" in summary
    assert "blocking=path_c_consistency" in summary
    assert summary.startswith("WC26 M009 Germany vs Curaçao — PARTIAL / WATCH")


def test_summary_uses_contract_cap_home_team_and_uncertain_message_id(tmp_path: Path) -> None:
    request_path = write_json(
        tmp_path / "direct-request.json",
        {
            "direct_request_id": "direct:m010",
            "platform": "telegram",
            "chat_id": "6808688675",
            "message_id": "unknown",
            "message_id_source": "session_unreliable",
            "message_id_exact": False,
            "request_text": "分析 M010 荷兰 vs 日本",
            "created_at_utc": "2026-06-05T14:34:08Z",
            "status": "completed_cached",
        },
    )
    crossbook = write_json(tmp_path / "crossbook.json", valid_crossbook_payload())
    ah = write_json(tmp_path / "ah.json", {"artifact_kind": "asian_handicap_market", "line": -0.5, "ev": 0.0})
    totals = write_json(tmp_path / "totals.json", {"artifact_kind": "totals_market", "line": 2.5})
    mechanism_audit = write_json(tmp_path / "mechanism-audit.json", valid_mechanism_audit_payload("watch"))
    manifest = write_json(
        tmp_path / "partial-m010-manifest.json",
        {
            "workflow_contract": "wc26.direct_report.v1",
            "report_completeness": "partial",
            "direct_request_id": "direct:m010",
            "direct_request_path": str(request_path),
            "match_id": "M010",
            "match": {
                "match_id": "M010",
                "home_team": "Netherlands",
                "away_team": "Japan",
                "kickoff_utc": "2026-06-14T20:00:00Z",
                "venue": "AT&T Stadium",
            },
            "entry_time_utc": "2026-06-05T14:34:08Z",
            "window": "early_structural",
            "window_display": "T-9d",
            "timing_class": "early_structural",
            "mode": "live",
            "source_quality": "B",
            "final_status": "watch",
            "source_freshness": {"sources": [{"name": "the-odds-api", "snapshot_id": "snapshot:m010"}]},
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
                    "reason": "Path C requires h2h",
                    "impact": "consistency triangle unavailable",
                }
            ],
            "numbers": [],
            "artifacts": [
                {"artifact_id": "crossbook:m010", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook), "provides": ["path_a_crossbook"]},
                {"artifact_id": "ah:m010", "artifact_type": "devig", "script": "devig.py", "path": str(ah), "provides": ["asian_handicap"]},
                {"artifact_id": "totals:m010", "artifact_type": "totals", "script": "numeric_artifact.py", "path": str(totals), "provides": ["totals"]},
                {"artifact_id": "mechanism:m010", "artifact_type": "mechanism_audit", "script": "mechanism_audit.py", "path": str(mechanism_audit), "provides": ["mechanism_audit"]},
            ],
        },
    )

    summary = direct_summary.build_summary(manifest)

    assert summary.startswith("WC26 M010 Netherlands vs Japan — PARTIAL / WATCH")
    assert "窗口: early_structural / T-9d" in summary
    assert "source_quality=B cap=C" in summary
    assert "msg=unknown (session_unreliable, exact=False)" in summary


def test_summary_surfaces_role_engine_game_theory_reading(tmp_path: Path) -> None:
    request_path = write_json(
        tmp_path / "direct-request.json",
        {
            "direct_request_id": "direct:m010",
            "platform": "telegram",
            "chat_id": "6808688675",
            "request_text": "分析 M010 荷兰 vs 日本",
            "created_at_utc": "2026-06-05T14:34:08Z",
            "status": "completed_cached",
        },
    )
    crossbook = write_json(tmp_path / "crossbook.json", valid_crossbook_payload())
    ah = write_json(tmp_path / "ah.json", {"artifact_kind": "asian_handicap_market", "line": -0.5, "ev": 0.0})
    totals = write_json(tmp_path / "totals.json", {"artifact_kind": "totals_market", "line": 2.5})
    path_c = write_json(
        tmp_path / "path-c.json",
        {"artifact_kind": "consistency_triangle", "signal": {"type": None}, "discrepancy": {"pp": -3.5}},
    )
    role_engine = write_json(
        tmp_path / "role-engine.json",
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
                    "interpretation_zh": "soft 书压低热门侧，热门叙事拥挤。",
                    "trigger_artifacts": ["path_a_crossbook", "devig_1x2"],
                    "artifact_sources": [{"capability": "path_a_crossbook", "artifact_id": "crossbook:m010"}],
                    "evidence_numbers": [{"name": "soft_favorite_discount_vs_fair", "value": -0.08}],
                },
                {
                    "evidence_id": "role:ai_lag:001",
                    "role": "ai_lag",
                    "role_label_zh": "AI 滞后",
                    "decision": "DIAGNOSTIC_ONLY",
                    "actionability": "never_actionable",
                    "hypothesis_zh": "模型是否发现市场慢半拍",
                    "interpretation_zh": "模型偏 Japan，但 Path A/Path C 不支持。",
                    "trigger_artifacts": ["path_b_model_diagnostic", "path_a_crossbook", "path_c_consistency"],
                    "artifact_sources": [{"capability": "path_b_model_diagnostic", "artifact_id": "model:m010"}],
                    "evidence_numbers": [{"name": "model_market_delta", "value": 0.06}],
                },
            ],
        },
    )
    mechanism_audit = write_json(
        tmp_path / "mechanism-audit.json",
        {
            **valid_mechanism_audit_payload("pass"),
            "mechanisms": {
                **valid_mechanism_audit_payload("pass")["mechanisms"],
                "role_engine": {
                    "status": "COMPLETE(deterministic_v1)",
                    "required_for_complete": False,
                    "artifact_id": "role_engine:m010",
                    "engine_version": "deterministic_v1",
                    "conclusion_count": 2,
                },
            },
        },
    )
    manifest = write_json(
        tmp_path / "role-manifest.json",
        {
            "workflow_contract": "wc26.direct_report.v1",
            "direct_request_id": "direct:m010",
            "direct_request_path": str(request_path),
            "match_id": "M010",
            "match": {"home": "Netherlands", "away": "Japan", "kickoff_utc": "2026-06-14T20:00:00Z"},
            "mode": "live",
            "source_quality": "B",
            "final_status": "pass",
            "source_freshness": {"sources": [{"name": "the-odds-api", "snapshot_id": "snapshot:m010"}]},
            "analysis_gates": {
                "devig_three_method": "pass",
                "path_a_crossbook": "pass",
                "asian_handicap": "pass",
                "totals": "pass",
                "path_b_model_diagnostic": "diagnostic",
                "path_c_consistency": "pass",
                "mechanism_audit": "pass",
                "source_freshness": "pass",
                "role_engine": "pass",
            },
            "artifacts": [
                {"artifact_id": "crossbook:m010", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook), "provides": ["path_a_crossbook"]},
                {"artifact_id": "ah:m010", "artifact_type": "devig", "script": "devig.py", "path": str(ah), "provides": ["asian_handicap"]},
                {"artifact_id": "totals:m010", "artifact_type": "totals", "script": "numeric_artifact.py", "path": str(totals), "provides": ["totals"]},
                {"artifact_id": "path-c:m010", "artifact_type": "consistency_triangle", "script": "consistency_triangle.py", "path": str(path_c), "provides": ["path_c_consistency"]},
                {"artifact_id": "role_engine:m010", "artifact_type": "role_engine", "script": "role_engine.py", "path": str(role_engine), "provides": ["role_engine"]},
                {"artifact_id": "mechanism:m010", "artifact_type": "mechanism_audit", "script": "mechanism_audit.py", "path": str(mechanism_audit), "provides": ["mechanism_audit"]},
            ],
        },
    )

    summary = direct_summary.build_summary(manifest)

    assert "🎭 博弈读盘" in summary
    assert "散户心理" in summary
    assert "soft 书压低热门侧" in summary
    assert "role:public_bias:001" in summary
    assert "AI 滞后" in summary


def test_summary_projects_ah_and_totals_from_crossbook_fair_probs(tmp_path: Path) -> None:
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
    crossbook = write_json(
        tmp_path / "crossbook-m009.json",
        {
            "artifact_type": "crossbook_scan",
            "artifact_kind": "cross_book_scan",
            "script": "cross_book_scan.py",
            "provides": ["path_a_crossbook"],
            "input_snapshot": "the-odds-api-multibook-20260605T143408Z.json",
            "source_snapshot_id": "the-odds-api-multibook-20260605T143408Z.json",
            "summary": {
                "markets_scanned": ["h2h", "spreads", "totals"],
                "quotes_scanned": 0,
                "edge_count": 0,
                "noise_edge_count": 0,
                "actionable_count": 0,
                "qualified_play_count": 0,
                "best_ev": None,
                "best_edge": None,
                "best_actionable_edge": None,
            },
            "markets": {
                "h2h": {"status": "no_sharp_anchor", "quotes": [], "edges": [], "quotes_scanned": 0},
                "spreads": {
                    "status": "ok",
                    "sharp_anchor": "pinnacle",
                    "sharp_overround": 0.039,
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
                    "sharp_overround": 0.0445,
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
    mechanism_audit = write_json(tmp_path / "mechanism-audit.json", valid_mechanism_audit_payload("watch"))
    manifest = write_json(
        tmp_path / "manifest-m009.json",
        {
            "workflow_contract": "wc26.direct_report.v1",
            "report_completeness": "partial",
            "direct_request_id": "direct:m009",
            "direct_request_path": str(request_path),
            "match_id": "M009",
            "match": {"home": "Germany", "away": "Curaçao", "kickoff_utc": "2026-06-14T17:00:00Z"},
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
                {"gate": "devig_three_method", "reason": "Pinnacle h2h missing", "impact": "1X2 unavailable"},
                {"gate": "path_c_consistency", "reason": "Path C requires H2H", "impact": "triangle unavailable"},
            ],
            "artifacts": [
                {
                    "artifact_id": "crossbook:m009",
                    "artifact_type": "crossbook_scan",
                    "script": "cross_book_scan.py",
                    "path": str(crossbook),
                    "provides": ["path_a_crossbook", "asian_handicap", "totals"],
                },
                {"artifact_id": "mechanism:m009", "artifact_type": "mechanism_audit", "script": "mechanism_audit.py", "path": str(mechanism_audit), "provides": ["mechanism_audit"]},
            ],
        },
    )

    summary = direct_summary.build_summary(manifest)

    assert "亚盘: anchor=pinnacle" in summary
    assert "germany@-3.5 50.4%" in summary
    assert "curaçao@3.5 49.6%" in summary
    assert "大小球: anchor=pinnacle" in summary
    assert "over@4.25 50.4%" in summary
    assert "under@4.25 49.6%" in summary
    assert "line=N/A" not in summary
    assert "P(Over)=N/A" not in summary
