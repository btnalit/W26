#!/usr/bin/env python3
"""Deterministic role-engine artifact tests."""

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


role_engine = load_module("role_engine", "skills/odds-analysis/scripts/role_engine.py")


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def crossbook_payload() -> dict:
    japan_edge = {
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
        "book_tier": "soft",
        "edge_candidate": True,
        "actionable": False,
        "qualifies": False,
        "ev_band": "noise_lt_5pp",
    }
    return {
        "artifact_id": "crossbook:m010",
        "artifact_type": "crossbook_scan",
        "artifact_kind": "cross_book_scan",
        "script": "cross_book_scan.py",
        "provides": ["path_a_crossbook"],
        "summary": {
            "markets_scanned": ["h2h", "spreads", "totals"],
            "quotes_scanned": 95,
            "edge_count": 1,
            "noise_edge_count": 1,
            "actionable_count": 0,
            "qualified_play_count": 0,
            "best_edge": japan_edge,
            "best_noise_edge": japan_edge,
            "best_actionable_edge": None,
        },
        "markets": {
            "h2h": {
                "status": "ok",
                "sharp_anchor": "pinnacle",
                "devig_primary": "shin",
                "outcomes_scanned": ["netherlands", "draw", "japan"],
                "quotes_scanned": 63,
                "fair_probs": {
                    "shin": {"netherlands": 0.4899, "draw": 0.2575, "japan": 0.2526},
                    "power": {"netherlands": 0.4913, "draw": 0.2568, "japan": 0.2519},
                    "multiplicative": {"netherlands": 0.4861, "draw": 0.2593, "japan": 0.2546},
                },
                "quotes": [
                    japan_edge,
                    {
                        "book": "everygame",
                        "market_key": "h2h",
                        "outcome": "netherlands",
                        "offered_odds": 1.91,
                        "sharp_fair_prob": 0.4899,
                        "fair_odds": 2.041,
                        "ev_shin": -0.064,
                        "book_tier": "soft",
                    },
                    {
                        "book": "sport888",
                        "market_key": "h2h",
                        "outcome": "netherlands",
                        "offered_odds": 1.91,
                        "sharp_fair_prob": 0.4899,
                        "fair_odds": 2.041,
                        "ev_shin": -0.064,
                        "book_tier": "soft",
                    },
                ],
                "edges": [japan_edge],
            },
            "spreads": {"status": "ok", "quotes_scanned": 8, "edges": []},
            "totals": {"status": "ok", "quotes_scanned": 24, "edges": []},
        },
    }


def model_payload() -> dict:
    return {
        "artifact_id": "model:m010",
        "artifact_type": "model",
        "p_model": {"home": 0.4004, "draw": 0.287, "away": 0.3126},
        "calibration": {"calibration_status": "holdout_pass"},
    }


def path_c_payload() -> dict:
    return {
        "artifact_id": "consistency:m010",
        "artifact_type": "consistency_triangle",
        "artifact_kind": "consistency_triangle",
        "analysis": {
            "actual_1x2_no_vig": {"home": 0.4861, "draw": 0.2593, "away": 0.2546},
            "spread_cover_prob": 0.4885,
        },
        "discrepancy": {"pp": -3.5, "direction": "under_cheap"},
        "signal": {"type": None, "strength": "无", "action": "忽略"},
    }


def devig_payload() -> dict:
    return {
        "artifact_id": "devig:m010",
        "artifact_type": "devig",
        "artifact_kind": "scalar_market",
        "decimal_odds": [1.99, 3.73, 3.80],
        "no_vig_probabilities": [0.4899, 0.2575, 0.2526],
        "devig_primary": "shin",
        "survives_all_methods": True,
    }


def test_role_engine_generates_artifact_driven_game_theory_reading(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    crossbook = write_json(artifacts_dir / "crossbook.json", crossbook_payload())
    model = write_json(artifacts_dir / "model.json", model_payload())
    path_c = write_json(artifacts_dir / "path-c.json", path_c_payload())
    devig = write_json(artifacts_dir / "devig.json", devig_payload())
    manifest = {
        "manifest_id": "manifest:m010",
        "match_id": "M010",
        "home": "Netherlands",
        "away": "Japan",
        "final_status": "watch",
        "artifacts": [
            {"artifact_id": "devig:m010", "artifact_type": "devig", "path": str(devig), "provides": ["devig_1x2"]},
            {"artifact_id": "crossbook:m010", "artifact_type": "crossbook_scan", "path": str(crossbook), "provides": ["path_a_crossbook"]},
            {"artifact_id": "model:m010", "artifact_type": "model", "path": str(model), "provides": ["path_b_model_diagnostic"]},
            {"artifact_id": "consistency:m010", "artifact_type": "consistency_triangle", "path": str(path_c), "provides": ["path_c_consistency"]},
        ],
    }
    manifest_path = write_json(tmp_path / "manifest.json", manifest)

    artifact = role_engine.build_role_artifact(manifest, manifest_path)

    assert artifact["engine_contract"] == "wc26.role_engine.v1"
    assert artifact["engine_version"] == "deterministic_v1"
    by_role = {item["role"]: item for item in artifact["role_conclusions"]}
    assert set(by_role) == {"bookmaker_intent", "public_bias", "ai_lag", "trap_risk", "market_efficiency"}
    assert by_role["public_bias"]["decision"] == "CONFIRMED"
    assert by_role["public_bias"]["actionability"] == "never_actionable"
    assert by_role["ai_lag"]["decision"] == "DIAGNOSTIC_ONLY"
    assert by_role["trap_risk"]["decision"] == "REFUTED"
    assert by_role["market_efficiency"]["decision"] == "CONFIRMED"
    assert "散户" in by_role["public_bias"]["interpretation_zh"]
    assert "Japan" in by_role["ai_lag"]["interpretation_zh"]

    for conclusion in artifact["role_conclusions"]:
        assert conclusion["evidence_id"].startswith("role:")
        assert conclusion["trigger_artifacts"]
        assert conclusion["evidence_numbers"]
        assert conclusion["artifact_sources"]


def test_role_engine_uses_crossbook_h2h_anchor_when_devig_is_missing(tmp_path: Path) -> None:
    curacao_edge = {
        "book": "marathonbet",
        "market_key": "h2h",
        "outcome": "curaçao",
        "offered_odds": 61.0,
        "sharp_fair_prob": 0.0174,
        "fair_odds": 57.599,
        "ev_shin": 0.059,
        "ev_power": 0.0541,
        "ev_multiplicative": 0.1055,
        "survives_all_methods": True,
        "suspect": False,
        "book_tier": "soft",
        "edge_candidate": True,
        "actionable": True,
        "qualifies": True,
        "ev_band": "weak_5_8pp",
    }
    crossbook = write_json(
        tmp_path / "crossbook.json",
        {
            "artifact_id": "crossbook:m009",
            "artifact_type": "crossbook_scan",
            "artifact_kind": "cross_book_scan",
            "script": "cross_book_scan.py",
            "provides": ["path_a_crossbook"],
            "summary": {
                "markets_scanned": ["h2h", "spreads", "totals"],
                "quotes_scanned": 63,
                "edge_count": 4,
                "noise_edge_count": 1,
                "actionable_count": 1,
                "raw_actionable_count": 1,
                "relay_actionable_count": 0,
                "qualified_play_count": 0,
                "best_edge": curacao_edge,
                "best_actionable_edge": curacao_edge,
            },
            "markets": {
                "h2h": {
                    "status": "ok",
                    "sharp_anchor": "betfair_ex",
                    "devig_primary": "shin",
                    "outcomes_scanned": ["curaçao", "germany", "draw"],
                    "quotes_scanned": 51,
                    "fair_probs": {
                        "shin": {"germany": 0.9418, "draw": 0.0408, "curaçao": 0.0174},
                    },
                    "quotes": [
                        curacao_edge,
                        {"book": "marathonbet", "outcome": "germany", "offered_odds": 1.04, "book_tier": "soft"},
                        {"book": "sport888", "outcome": "germany", "offered_odds": 1.04, "book_tier": "soft"},
                    ],
                    "edges": [curacao_edge],
                },
                "spreads": {"status": "ok", "quotes_scanned": 10, "edges": []},
                "totals": {"status": "ok", "quotes_scanned": 2, "edges": []},
            },
        },
    )
    model = write_json(tmp_path / "model.json", {"artifact_id": "model:m009", "artifact_type": "model", "p_model": {"home": 0.922, "draw": 0.060, "away": 0.018}})
    manifest = {
        "manifest_id": "manifest:m009",
        "match_id": "M009",
        "home": "Germany",
        "away": "Curaçao",
        "source_quality_cap": "C",
        "report_completeness": "partial",
        "artifacts": [
            {"artifact_id": "crossbook:m009", "artifact_type": "crossbook_scan", "path": str(crossbook), "provides": ["path_a_crossbook"]},
            {"artifact_id": "model:m009", "artifact_type": "model", "path": str(model), "provides": ["path_b_model_diagnostic"]},
        ],
    }
    manifest_path = write_json(tmp_path / "manifest.json", manifest)

    artifact = role_engine.build_role_artifact(manifest, manifest_path)
    by_role = {item["role"]: item for item in artifact["role_conclusions"]}

    assert by_role["bookmaker_intent"]["decision"] == "DIAGNOSTIC_ONLY"
    assert "sharp H2H anchor" in by_role["bookmaker_intent"]["interpretation_zh"]
    assert by_role["public_bias"]["decision"] == "CONFIRMED"
    assert by_role["ai_lag"]["decision"] == "REFUTED"
    assert by_role["trap_risk"]["decision"] == "BLOCKED"
    assert by_role["market_efficiency"]["decision"] == "DIAGNOSTIC_ONLY"


def test_role_engine_renders_markdown_game_theory_section(tmp_path: Path) -> None:
    artifact = {
        "engine_version": "deterministic_v1",
        "role_conclusions": [
            {
                "evidence_id": "role:public_bias:001",
                "role_label_zh": "散户心理",
                "decision": "CONFIRMED",
                "actionability": "never_actionable",
                "interpretation_zh": "soft 书压低热门侧，热门叙事拥挤。",
            }
        ],
    }
    report = tmp_path / "report.md"
    report.write_text("## 9. Adjustment Ledger\n\nledger\n\n## 10. Final Decision\n\nWATCH\n", encoding="utf-8")

    role_engine.patch_report(report, artifact)

    text = report.read_text(encoding="utf-8")
    assert "## 9B. 博弈读盘" in text
    assert "deterministic_v1" in text
    assert "role:public_bias:001" in text
    assert "## 10. Final Decision" in text
