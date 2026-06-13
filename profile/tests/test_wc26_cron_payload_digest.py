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


def _write_fixture_file(workspace: Path, match_id: int, kickoff_utc: str, home: str = "TestHome", away: str = "TestAway") -> Path:
    """Helper: write a minimal fixture snapshot with one match."""
    fixture_path = workspace / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps({
        "data": {
            "matches": [{
                "id": match_id,
                "utcDate": kickoff_utc,
                "status": "TIMED",
                "stage": "GROUP_STAGE",
                "group": "GROUP_A",
                "matchday": 1,
                "homeTeam": {"name": home, "tla": home[:3].upper()},
                "awayTeam": {"name": away, "tla": away[:3].upper()},
            }]
        }
    }), encoding="utf-8")
    return fixture_path


def test_has_late_window_fixtures_true_when_match_is_in_range(tmp_path: Path, monkeypatch) -> None:
    """Fixture at T-45m (0.75h to KO) → has_late_window_fixtures returns True."""
    module = load_payload()
    configure_workspace(module, tmp_path)
    monkeypatch.setenv("WC26_NOW_UTC", "2026-06-15T18:15:00Z")
    _write_fixture_file(tmp_path, 537333, "2026-06-15T19:00:00Z")  # 0.75h = T-45m

    assert module.has_late_window_fixtures() is True


def test_has_late_window_fixtures_true_at_boundary_t60m(tmp_path: Path, monkeypatch) -> None:
    """Fixture at T-60m (1.0h) → still True (upper boundary exclusive not hit)."""
    module = load_payload()
    configure_workspace(module, tmp_path)
    monkeypatch.setenv("WC26_NOW_UTC", "2026-06-15T18:00:00Z")
    _write_fixture_file(tmp_path, 537333, "2026-06-15T19:00:00Z")  # 1.0h = T-60m

    assert module.has_late_window_fixtures() is True


def test_has_late_window_fixtures_true_at_t20m_ad_hoc(tmp_path: Path, monkeypatch) -> None:
    """LO=0: fixture at T-20m → still True (covers ad-hoc analysis gap)."""
    module = load_payload()
    configure_workspace(module, tmp_path)
    monkeypatch.setenv("WC26_NOW_UTC", "2026-06-15T18:40:00Z")
    _write_fixture_file(tmp_path, 537333, "2026-06-15T19:00:00Z")  # 0.33h = T-20m

    assert module.has_late_window_fixtures() is True


def test_has_late_window_fixtures_false_when_match_is_too_far(tmp_path: Path, monkeypatch) -> None:
    """Fixture at T-90m (1.5h) → False (outside late window)."""
    module = load_payload()
    configure_workspace(module, tmp_path)
    monkeypatch.setenv("WC26_NOW_UTC", "2026-06-15T17:30:00Z")
    _write_fixture_file(tmp_path, 537333, "2026-06-15T19:00:00Z")  # 1.5h > 1.25

    assert module.has_late_window_fixtures() is False


def test_has_late_window_fixtures_false_when_no_fixtures(tmp_path: Path, monkeypatch) -> None:
    """No fixture file → False."""
    module = load_payload()
    configure_workspace(module, tmp_path)
    monkeypatch.setenv("WC26_NOW_UTC", "2026-06-15T18:15:00Z")

    assert module.has_late_window_fixtures() is False


def test_odds_broad_scan_force_flag_when_late_window_fixture_exists(tmp_path: Path, monkeypatch, capsys) -> None:
    """When has_late_window_fixtures() is True, odds_broad_scan skips
    cache_reuse and proceeds to fetch.  The critical assertion is that
    the output must NOT contain 'reused_cache' — the force flag must
    have bypassed TTL reuse, regardless of whether the subsequent API
    call succeeds or blocks on a missing key."""
    module = load_payload()
    configure_workspace(module, tmp_path)
    monkeypatch.setenv("WC26_NOW_UTC", "2026-06-15T18:15:00Z")
    # Remove ODDS_API_KEY so we test the force branch without a live API call
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    _write_fixture_file(tmp_path, 537333, "2026-06-15T19:00:00Z")  # T-45m, in range

    # Write a fake recent snapshot — if the function wrongly uses TTL reuse
    # it would return cache_reuse_manifest, which contains "reused_cache".
    snap_dir = tmp_path / "snapshots" / "odds"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / "the-odds-api-multibook-20260615T181000Z.json"
    snap_path.write_text(json.dumps({
        "captured_at_utc": "2026-06-15T18:10:00Z",
        "source": "the-odds-api",
        "data": [],
    }), encoding="utf-8")

    exit_code = module.odds_broad_scan()
    out = capsys.readouterr().out

    # With late-window fixture, force=True → skip cache_reuse → go to API.
    # No ODDS_API_KEY set, so it should block with "ODDS_API_KEY missing".
    assert "reused_cache" not in out
    assert "ODDS_API_KEY missing" in out


def test_odds_broad_scan_ttl_reuse_when_no_late_window_fixture(tmp_path: Path, monkeypatch, capsys) -> None:
    """Without late-window fixture, force=False → TTL reuse is allowed."""
    module = load_payload()
    configure_workspace(module, tmp_path)
    monkeypatch.setenv("WC26_NOW_UTC", "2026-06-15T17:00:00Z")
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    _write_fixture_file(tmp_path, 537333, "2026-06-15T19:00:00Z")  # 2h, outside range

    snap_dir = tmp_path / "snapshots" / "odds"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / "the-odds-api-multibook-20260615T165500Z.json"
    snap_path.write_text(json.dumps({
        "captured_at_utc": "2026-06-15T16:55:00Z",
        "source": "the-odds-api",
        "data": [],
    }), encoding="utf-8")

    exit_code = module.odds_broad_scan()
    out = capsys.readouterr().out

    # Outside late window, no force refresh, snapshot is 5 min old < 120 min TTL.
    # Should return cache_reuse.
    assert exit_code == 0
    assert "reused_cache" in out
