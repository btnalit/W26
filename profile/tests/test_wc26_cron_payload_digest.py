from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_payload():
    path = ROOT / "scripts" / "wc26_cron_payload.py"
    spec = importlib.util.spec_from_file_location("wc26_cron_payload_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def configure_workspace(module, tmp_path: Path) -> None:
    module.WORKSPACE = tmp_path
    module.STATE_DIR = tmp_path / "state"
    module.GRADING_DIR = tmp_path / "grading"
    module.GRADING_CARDS_DIR = tmp_path / "grading" / "cards"


def test_daily_reflect_reads_grading_ledger_path_a_and_window_gaps(tmp_path: Path, monkeypatch, capsys) -> None:
    module = load_payload()
    configure_workspace(module, tmp_path)
    monkeypatch.setenv("WC26_NOW_UTC", "2026-06-08T19:00:00Z")

    report = tmp_path / "reports" / "match" / "M001-report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("match_id: M001\n", encoding="utf-8")
    fixed_ts = 1_781_140_800  # 2026-06-08T00:00:00Z
    os.utime(report, (fixed_ts, fixed_ts))

    write_json(
        tmp_path / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json",
        {
            "data": {
                "matches": [
                    {
                        "id": 537327,
                        "utcDate": "2026-06-11T19:00:00Z",
                        "status": "TIMED",
                        "stage": "GROUP_STAGE",
                        "group": "GROUP_A",
                        "matchday": 1,
                        "homeTeam": {"name": "Mexico", "tla": "MEX"},
                        "awayTeam": {"name": "South Africa", "tla": "RSA"},
                    }
                ]
            }
        },
    )
    crossbook = write_json(
        tmp_path / "reports" / "artifacts" / "crossbook-M001.json",
        {
            "artifact_type": "crossbook_scan",
            "markets": {
                "h2h": {
                    "quotes_scanned": 3,
                    "edge_count": 2,
                    "noise_edge_count": 1,
                    "actionable_count": 1,
                    "qualified_play_count": 1,
                    "quotes": [{"suspect": True}, {"suspect": False}],
                }
            },
        },
    )
    write_json(
        tmp_path / "reports" / "artifacts" / "manifest-M001.json",
        {
            "match_id": "M001",
            "window": "T-72h_early",
            "final_status": "watch",
            "source_quality_cap": "C",
            "created_at_utc": "2026-06-08T18:00:00Z",
            "artifacts": [
                {
                    "artifact_type": "crossbook_scan",
                    "provides": ["path_a_crossbook"],
                    "path": str(crossbook),
                }
            ],
            "analysis_gates": {"role_engine": "missing", "mechanism_audit": "missing"},
        },
    )
    write_json(
        tmp_path / "grading" / "cards" / "grade-1.json",
        {
            "schema_version": "wc26.grading_card.v1",
            "card_id": "grade-1",
            "content_hash": "abc",
            "graded_at_utc": "2026-06-08T20:00:00Z",
            "match_id": "M001",
            "home": "Mexico",
            "away": "South Africa",
            "result": "2-1",
            "final_status": "watch",
            "clv_raw": 0.012,
            "brier": 0.44,
            "log_loss": 0.69,
            "p_adj_actual_outcome": 0.55,
        },
    )

    assert module.daily_reflect() == 0
    out = capsys.readouterr().out
    assert "# WC26 Daily Review Digest" in out
    assert "new_graded_today: 1" in out
    assert "missing_guarded_report_now: 1" in out
    assert "'noise_edge_count': 1" in out
    assert "'suspect_count': 1" in out
    assert "calibration buckets by p_adj" in out
    assert "no proposal today" in out


def test_postmatch_notify_sends_each_grading_card_once(tmp_path: Path, capsys) -> None:
    module = load_payload()
    configure_workspace(module, tmp_path)
    write_json(
        tmp_path / "grading" / "cards" / "grade-1.json",
        {
            "schema_version": "wc26.grading_card.v1",
            "card_id": "grade-1",
            "content_hash": "abc",
            "graded_at_utc": "2026-06-08T20:00:00Z",
            "match_id": "M001",
            "home": "Mexico",
            "away": "South Africa",
            "result": "2-1",
            "final_status": "watch",
            "clv_raw": 0.012,
            "brier": 0.44,
            "report_path": "/tmp/report.md",
        },
    )

    assert module.postmatch_notify() == 0
    first = capsys.readouterr().out
    assert "# WC26 Postmatch Grade Digest" in first
    assert "M001" in first

    assert module.postmatch_notify() == 0
    second = capsys.readouterr().out
    assert second == ""
