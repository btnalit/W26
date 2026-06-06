from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_watcher():
    path = ROOT / "skills" / "odds-analysis" / "scripts" / "opportunity_watch.py"
    spec = importlib.util.spec_from_file_location("opportunity_watch_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_manifest_with_crossbook(tmp_path: Path, *, edge: dict, summary: dict, cap: str = "B", scan_time: str = "2026-06-05T11:50:00Z") -> Path:
    crossbook = write_json(
        tmp_path / "reports" / "artifacts" / "crossbook-M999.json",
        {
            "artifact_type": "crossbook_scan",
            "artifact_kind": "crossbook_scan",
            "scan_timestamp_utc": scan_time,
            "input_snapshot": str(tmp_path / "snapshots" / "odds" / "snapshot.json"),
            "source_snapshot_id": "test-snapshot",
            "summary": summary,
            "markets": {
                "spreads": {
                    "status": "ok",
                    "quotes_scanned": 1,
                    "edges": [edge],
                }
            },
        },
    )
    write_json(
        tmp_path / "snapshots" / "odds" / "snapshot.json",
        {"captured_at_utc": scan_time},
    )
    manifest = write_json(
        tmp_path / "reports" / "artifacts" / "manifest-M999.json",
        {
            "workflow_contract": "wc26.direct_report.v1",
            "match_id": "M999",
            "home": "Ecuador",
            "away": "Test",
            "window": "T-72h_early",
            "final_status": "qualified_play",
            "source_quality_cap": cap,
            "artifacts": [
                {
                    "artifact_type": "crossbook_scan",
                    "provides": ["path_a_crossbook"],
                    "path": str(crossbook),
                }
            ],
        },
    )
    fixed = 1_780_000_000
    os.utime(manifest, (fixed, fixed))
    return manifest


def test_raw_actionable_without_relay_is_observation_only(tmp_path: Path, monkeypatch) -> None:
    module = load_watcher()
    monkeypatch.setenv("WC26_NOW_UTC", "2026-06-05T12:00:00Z")
    monkeypatch.setattr(module.report_contract, "validate_manifest", lambda payload, path: {"valid": True, "actionable_allowed": False})
    edge = {
        "book": "marathonbet",
        "market_key": "h2h",
        "outcome": "curaçao",
        "offered_odds": 61.0,
        "sharp_fair_prob": 0.0174,
        "fair_odds": 57.599,
        "ev_shin": 0.059,
        "survives_all_methods": True,
        "suspect": False,
        "actionable": True,
        "qualifies": False,
        "ev_band": "weak_5_8pp",
    }
    write_manifest_with_crossbook(tmp_path, edge=edge, summary={"relay_actionable_count": 0, "qualified_play_count": 0})

    board = module.build_board(tmp_path, lookback_hours=999999, now=module.parse_time("2026-06-05T12:00:00Z"))

    assert board["opportunities"] == []
    assert board["stats"]["raw_only_candidates"] == 1
    assert board["observations"][0]["type"] == "raw_only"


def test_fresh_relay_edge_becomes_opportunity(tmp_path: Path, monkeypatch) -> None:
    module = load_watcher()
    monkeypatch.setattr(module.report_contract, "validate_manifest", lambda payload, path: {"valid": True, "actionable_allowed": True})
    edge = {
        "book": "bet365",
        "market_key": "spreads",
        "outcome": "ecuador +0.5",
        "offered_odds": 2.30,
        "sharp_fair_prob": 0.47,
        "fair_odds": 2.128,
        "ev_shin": 0.081,
        "survives_all_methods": True,
        "suspect": False,
        "actionable": True,
        "qualifies": True,
        "ev_band": "medium_8_13pp",
    }
    write_manifest_with_crossbook(tmp_path, edge=edge, summary={"relay_actionable_count": 1, "qualified_play_count": 1})

    board = module.build_board(tmp_path, lookback_hours=999999, now=module.parse_time("2026-06-05T12:00:00Z"))

    assert len(board["opportunities"]) == 1
    card = board["opportunities"][0]
    assert card["book"] == "bet365"
    assert card["confidence_quality"] == "liquid_main"
    assert card["freshness_status"] == "fresh"
    assert card["suggested_stake_pct"] > 0.05


def test_stale_relay_edge_does_not_alert(tmp_path: Path, monkeypatch) -> None:
    module = load_watcher()
    monkeypatch.setattr(module.report_contract, "validate_manifest", lambda payload, path: {"valid": True, "actionable_allowed": True})
    edge = {
        "book": "bet365",
        "market_key": "spreads",
        "outcome": "ecuador +0.5",
        "offered_odds": 2.30,
        "sharp_fair_prob": 0.47,
        "fair_odds": 2.128,
        "ev_shin": 0.081,
        "survives_all_methods": True,
        "suspect": False,
        "actionable": True,
        "qualifies": True,
        "ev_band": "medium_8_13pp",
    }
    write_manifest_with_crossbook(
        tmp_path,
        edge=edge,
        summary={"relay_actionable_count": 1, "qualified_play_count": 1},
        scan_time="2026-06-05T09:00:00Z",
    )

    board = module.build_board(tmp_path, lookback_hours=999999, now=module.parse_time("2026-06-05T12:00:00Z"))

    assert board["opportunities"] == []
    assert board["stats"]["stale_candidates"] == 1
    assert board["observations"][0]["type"] == "stale_relay_candidate"
