#!/usr/bin/env python3
"""Regression tests for review_tracker.py uncarded detection.

Gate: list_finished_uncarded MUST use fixture status ("FINISHED"),
NOT manifest existence. These four scenarios prevent silent regression
if someone refactors the criterion.

Scenarios:
  1. TIMED fixture, no card  → NOT in pending_uncarded
  2. IN_PLAY fixture, no card → NOT in pending_uncarded
  3. FINISHED, has card       → NOT in pending_uncarded
  4. FINISHED, no card        → IN pending_uncarded
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_review_tracker():
    path = ROOT / "skills" / "odds-analysis" / "scripts" / "review_tracker.py"
    spec = importlib.util.spec_from_file_location("review_tracker_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── Fixture helpers ──────────────────────────────────────────────

def _make_fixture(fid: int, ordinal: str, home: str, away: str, status: str) -> dict:
    """Build a review_tracker-compatible fixture entry."""
    return {
        "football_data_id": fid,
        "local_ordinal_id": ordinal,
        "home": home,
        "away": away,
        "home_tla": home[:3].upper(),
        "away_tla": away[:3].upper(),
        "kickoff_utc": "2026-06-15T00:00:00Z",
        "stage": "GROUP_STAGE",
        "group": "GROUP_Z",
        "matchday": 1,
        "status": status,
        "venue": "Test Stadium",
    }


def _make_grading_card(cards_dir: Path, fid: int, home: str, away: str, result: str = "1-0") -> Path:
    """Write a minimal grading card JSON and return its path."""
    cards_dir.mkdir(parents=True, exist_ok=True)
    path = cards_dir / f"grade-{fid:016x}.json"
    path.write_text(json.dumps({
        "schema_version": "wc26.grading_card.v1",
        "football_data_id": fid,
        "home": home,
        "away": away,
        "result": result,
        "card_id": path.name,
    }), encoding="utf-8")
    return path


# ── Tests ────────────────────────────────────────────────────────

def test_timed_not_in_uncarded(tmp_path: Path) -> None:
    """TIMED fixture without card → NOT in list_finished_uncarded."""
    rt = load_review_tracker()
    fixtures = [_make_fixture(999001, "T001", "TestHome", "TestAway", "TIMED")]
    result = rt.list_finished_uncarded(str(tmp_path), fixtures=fixtures)
    ids = {str(u["football_data_id"]) for u in result}
    assert "999001" not in ids, f"TIMED fixture leaked into uncarded: {result}"


def test_in_play_not_in_uncarded(tmp_path: Path) -> None:
    """IN_PLAY fixture without card → NOT in list_finished_uncarded."""
    rt = load_review_tracker()
    fixtures = [_make_fixture(999002, "T002", "TestHome", "TestAway", "IN_PLAY")]
    result = rt.list_finished_uncarded(str(tmp_path), fixtures=fixtures)
    ids = {str(u["football_data_id"]) for u in result}
    assert "999002" not in ids, f"IN_PLAY fixture leaked into uncarded: {result}"


def test_finished_with_card_not_in_uncarded(tmp_path: Path) -> None:
    """FINISHED fixture WITH grading card → NOT in list_finished_uncarded."""
    rt = load_review_tracker()
    fid = 999003
    fixtures = [_make_fixture(fid, "T003", "CardHome", "CardAway", "FINISHED")]
    _make_grading_card(tmp_path / "grading" / "cards", fid, "CardHome", "CardAway")
    result = rt.list_finished_uncarded(str(tmp_path), fixtures=fixtures)
    ids = {str(u["football_data_id"]) for u in result}
    assert str(fid) not in ids, f"FINISHED+carded fixture leaked into uncarded: {result}"


def test_finished_no_card_in_uncarded(tmp_path: Path) -> None:
    """FINISHED fixture without grading card → IN list_finished_uncarded."""
    rt = load_review_tracker()
    fid = 999004
    fixtures = [_make_fixture(fid, "T004", "NoCardHome", "NoCardAway", "FINISHED")]
    # No grading card written
    result = rt.list_finished_uncarded(str(tmp_path), fixtures=fixtures)
    ids = {str(u["football_data_id"]) for u in result}
    assert str(fid) in ids, (
        f"FINISHED+uncarded fixture MISSING from result.\n"
        f"Expected {fid} in uncarded, got: {ids}"
    )
    # Verify reason field
    match = [u for u in result if u["football_data_id"] == fid][0]
    assert match["reason"] == "FINISHED but no grading card exists"


def test_mixed_batch_only_finished_uncarded_returned(tmp_path: Path) -> None:
    """Mixed batch: TIMED, IN_PLAY, FINISHED+carded, FINISHED+uncarded.
    Only the last should appear in uncarded."""
    rt = load_review_tracker()
    fixtures = [
        _make_fixture(999001, "T001", "A", "B", "TIMED"),
        _make_fixture(999002, "T002", "C", "D", "IN_PLAY"),
        _make_fixture(999003, "T003", "E", "F", "FINISHED"),
        _make_fixture(999004, "T004", "G", "H", "FINISHED"),
    ]
    _make_grading_card(tmp_path / "grading" / "cards", 999003, "E", "F")

    result = rt.list_finished_uncarded(str(tmp_path), fixtures=fixtures)
    ids = {str(u["football_data_id"]) for u in result}

    assert "999001" not in ids, "TIMED leaked"
    assert "999002" not in ids, "IN_PLAY leaked"
    assert "999003" not in ids, "FINISHED+carded leaked"
    assert "999004" in ids, "FINISHED+uncarded missing"
    assert len(result) == 1, f"Expected exactly 1 uncarded, got {len(result)}: {ids}"
