#!/usr/bin/env python3
"""Mechanism audit artifact generation tests."""

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


mechanism_audit = load_module("mechanism_audit", "skills/odds-analysis/scripts/mechanism_audit.py")


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def crossbook_payload() -> dict:
    edge = {
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
    return {
        "artifact_type": "crossbook_scan",
        "artifact_kind": "cross_book_scan",
        "script": "cross_book_scan.py",
        "summary": {
            "markets_scanned": ["h2h"],
            "quotes_scanned": 3,
            "edge_count": 1,
            "noise_edge_count": 1,
            "actionable_count": 0,
            "qualified_play_count": 0,
            "best_edge": edge,
            "best_noise_edge": edge,
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
                "quotes": [edge],
                "edges": [edge],
            }
        },
    }


def test_mechanism_audit_generates_noise_decision_from_path_a(tmp_path: Path) -> None:
    crossbook = write_json(tmp_path / "crossbook.json", crossbook_payload())
    path_c = write_json(
        tmp_path / "path-c.json",
        {
            "artifact_type": "consistency_triangle",
            "artifact_kind": "consistency_triangle",
            "signal": {"type": None, "strength": "none"},
            "discrepancy": {"pp": 0.0},
        },
    )
    manifest = {
        "manifest_id": "manifest:m010",
        "match_id": "M010",
        "final_status": "pass",
        "analysis_gates": {"path_b_model_diagnostic": "diagnostic"},
        "artifacts": [
            {"artifact_id": "crossbook:m010", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook), "provides": ["path_a_crossbook"]},
            {"artifact_id": "pathc:m010", "artifact_type": "consistency_triangle", "script": "consistency_triangle.py", "path": str(path_c), "provides": ["path_c_consistency"]},
        ],
    }
    manifest_path = write_json(tmp_path / "manifest.json", manifest)

    audit = mechanism_audit.build_audit(manifest, manifest_path)

    assert audit["mechanism_audit_status"] == "complete"
    assert audit["required_final_status"] == "pass"
    assert audit["mechanisms"]["path_a_crossbook"]["quotes_scanned"] == 3
    assert any(
        decision["decision"] == "CONFIRMED_NOISE" and decision["subject"] == "marathonbet h2h japan"
        for decision in audit["hypothesis_decisions"]
    )


def test_mechanism_audit_downgrades_pass_when_core_mechanism_blocked(tmp_path: Path) -> None:
    crossbook = write_json(tmp_path / "crossbook.json", crossbook_payload())
    manifest = {
        "manifest_id": "manifest:m010",
        "match_id": "M010",
        "final_status": "pass",
        "analysis_gates": {"path_b_model_diagnostic": "diagnostic"},
        "artifacts": [
            {"artifact_id": "crossbook:m010", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook), "provides": ["path_a_crossbook"]},
        ],
    }
    manifest_path = write_json(tmp_path / "manifest.json", manifest)

    audit = mechanism_audit.build_audit(manifest, manifest_path)

    assert audit["mechanism_audit_status"] == "pass_incomplete"
    assert audit["required_final_status"] == "pass_incomplete"
    assert audit["blocking_mechanisms"] == ["path_c_consistency"]


def test_mechanism_audit_treats_path_b_not_required_as_exempt(tmp_path: Path) -> None:
    crossbook = write_json(tmp_path / "crossbook.json", crossbook_payload())
    path_c = write_json(
        tmp_path / "path-c.json",
        {
            "artifact_type": "consistency_triangle",
            "artifact_kind": "consistency_triangle",
            "signal": {"type": None, "strength": "none"},
            "discrepancy": {"pp": 0.0},
        },
    )
    manifest = {
        "manifest_id": "manifest:m010",
        "match_id": "M010",
        "final_status": "pass",
        "analysis_gates": {"path_b_model_diagnostic": "not_required"},
        "artifacts": [
            {"artifact_id": "crossbook:m010", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook), "provides": ["path_a_crossbook"]},
            {"artifact_id": "pathc:m010", "artifact_type": "consistency_triangle", "script": "consistency_triangle.py", "path": str(path_c), "provides": ["path_c_consistency"]},
        ],
    }
    manifest_path = write_json(tmp_path / "manifest.json", manifest)

    audit = mechanism_audit.build_audit(manifest, manifest_path)

    path_b = audit["mechanisms"]["path_b_model_diagnostic"]
    assert path_b["status"] == "COMPLETE"
    assert path_b["required_for_complete"] is False
    assert audit["mechanism_audit_status"] == "complete"
    assert audit["blocking_mechanisms"] == []


def test_mechanism_audit_marks_suppressed_path_c_as_diagnostic_only(tmp_path: Path) -> None:
    crossbook = write_json(tmp_path / "crossbook.json", crossbook_payload())
    path_c = write_json(
        tmp_path / "path-c.json",
        {
            "artifact_type": "consistency_triangle",
            "artifact_kind": "consistency_triangle",
            "signal": {"type": None, "strength": "diagnostic_suppressed", "suppressed": True, "raw_type": "under_cheap"},
            "discrepancy": {"pp": None, "raw_pp": -15.6, "suppressed": True},
        },
    )
    manifest = {
        "manifest_id": "manifest:m010",
        "match_id": "M010",
        "final_status": "pass",
        "analysis_gates": {"path_b_model_diagnostic": "diagnostic"},
        "artifacts": [
            {"artifact_id": "crossbook:m010", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook), "provides": ["path_a_crossbook"]},
            {"artifact_id": "pathc:m010", "artifact_type": "consistency_triangle", "script": "consistency_triangle.py", "path": str(path_c), "provides": ["path_c_consistency"]},
        ],
    }
    manifest_path = write_json(tmp_path / "manifest.json", manifest)

    audit = mechanism_audit.build_audit(manifest, manifest_path)

    assert any(decision["source"] == "path_c_consistency" and decision["decision"] == "DIAGNOSTIC_ONLY" for decision in audit["hypothesis_decisions"])

def test_mechanism_audit_marks_deterministic_role_engine_complete(tmp_path: Path) -> None:
    crossbook = write_json(tmp_path / "crossbook.json", crossbook_payload())
    path_c = write_json(
        tmp_path / "path-c.json",
        {
            "artifact_type": "consistency_triangle",
            "artifact_kind": "consistency_triangle",
            "signal": {"type": None, "strength": "none"},
            "discrepancy": {"pp": 0.0},
        },
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
                    "interpretation_zh": "soft 书压低热门侧。",
                    "trigger_artifacts": ["path_a_crossbook", "devig_1x2"],
                    "artifact_sources": [{"capability": "path_a_crossbook", "artifact_id": "crossbook:m010"}],
                    "evidence_numbers": [{"name": "soft_favorite_discount_vs_fair", "value": -0.08}],
                }
            ],
        },
    )
    manifest = {
        "manifest_id": "manifest:m010",
        "match_id": "M010",
        "final_status": "pass",
        "analysis_gates": {"path_b_model_diagnostic": "diagnostic"},
        "artifacts": [
            {"artifact_id": "crossbook:m010", "artifact_type": "crossbook_scan", "script": "cross_book_scan.py", "path": str(crossbook), "provides": ["path_a_crossbook"]},
            {"artifact_id": "pathc:m010", "artifact_type": "consistency_triangle", "script": "consistency_triangle.py", "path": str(path_c), "provides": ["path_c_consistency"]},
            {"artifact_id": "role_engine:m010", "artifact_type": "role_engine", "script": "role_engine.py", "path": str(role_engine), "provides": ["role_engine"]},
        ],
    }
    manifest_path = write_json(tmp_path / "manifest.json", manifest)

    audit = mechanism_audit.build_audit(manifest, manifest_path)

    role = audit["mechanisms"]["role_engine"]
    assert role["status"] == "COMPLETE(deterministic_v1)"
    assert role["required_for_complete"] is False
    assert role["conclusion_count"] == 1
    assert role["artifact_id"] == "role_engine:m010"
