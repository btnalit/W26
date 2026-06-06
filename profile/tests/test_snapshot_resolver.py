#!/usr/bin/env python3
"""Snapshot resolver window freshness contract."""

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


snapshot_resolver = load_module("snapshot_resolver", "skills/odds-analysis/scripts/snapshot_resolver.py")


def write_snapshot(path: Path, captured_at_utc: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"captured_at_utc": captured_at_utc, "data": []}), encoding="utf-8")
    return path


def by_source(payload: dict) -> dict:
    return {item["source"]: item for item in payload["sources"]}


def test_t72_does_not_require_optional_lineups_or_weather(tmp_path: Path) -> None:
    write_snapshot(tmp_path / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json", "2026-06-05T08:00:00Z")
    write_snapshot(tmp_path / "snapshots" / "odds" / "the-odds-api-multibook-20260605T080000Z.json", "2026-06-05T08:00:00Z")
    write_snapshot(tmp_path / "snapshots" / "odds" / "oddspapi-t16-20260605T080000Z.json", "2026-06-05T08:00:00Z")
    write_snapshot(tmp_path / "snapshots" / "weather" / "old-weather.json", "2026-06-04T08:00:00Z")
    now = snapshot_resolver.parse_utc("2026-06-05T14:00:00Z")

    payload = snapshot_resolver.resolve_all(tmp_path, "T-72h_early", now)
    sources = by_source(payload)

    assert payload["must_refresh"] == []
    assert sources["lineups"]["required"] is False
    assert sources["lineups"]["reason"] == "optional_source_missing"
    assert sources["weather"]["required"] is False
    assert sources["weather"]["reason"] == "optional_source_stale"


def test_early_structural_uses_t72_freshness_without_lineups(tmp_path: Path) -> None:
    write_snapshot(tmp_path / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json", "2026-06-05T08:00:00Z")
    write_snapshot(tmp_path / "snapshots" / "odds" / "the-odds-api-multibook-20260605T080000Z.json", "2026-06-05T08:00:00Z")
    write_snapshot(tmp_path / "snapshots" / "odds" / "oddspapi-t16-20260605T080000Z.json", "2026-06-05T08:00:00Z")
    now = snapshot_resolver.parse_utc("2026-06-05T14:00:00Z")

    payload = snapshot_resolver.resolve_all(tmp_path, "early_structural", now)
    sources = by_source(payload)

    assert payload["window"] == "early_structural"
    assert payload["must_refresh"] == []
    assert sources["lineups"]["required"] is False
    assert sources["weather"]["required"] is False


def test_late_lineup_window_requires_lineups(tmp_path: Path) -> None:
    write_snapshot(tmp_path / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json", "2026-06-05T13:45:00Z")
    write_snapshot(tmp_path / "snapshots" / "odds" / "the-odds-api-multibook-20260605T134500Z.json", "2026-06-05T13:45:00Z")
    write_snapshot(tmp_path / "snapshots" / "odds" / "oddspapi-t16-20260605T134500Z.json", "2026-06-05T13:45:00Z")
    write_snapshot(tmp_path / "snapshots" / "weather" / "weather.json", "2026-06-05T13:45:00Z")
    now = snapshot_resolver.parse_utc("2026-06-05T14:00:00Z")

    payload = snapshot_resolver.resolve_all(tmp_path, "T-60m_lineup_final", now)
    sources = by_source(payload)

    assert "lineups" in payload["must_refresh"]
    assert sources["lineups"]["required"] is True
    assert sources["lineups"]["reason"] == "no_snapshot_available"


def test_odds_broad_ignores_single_book_snapshots(tmp_path: Path) -> None:
    write_snapshot(tmp_path / "snapshots" / "odds" / "the-odds-api-multibook-20260605T120000Z.json", "2026-06-05T12:00:00Z")
    write_snapshot(tmp_path / "snapshots" / "odds" / "the-odds-api-20260605T140000Z.json", "2026-06-05T14:00:00Z")
    now = snapshot_resolver.parse_utc("2026-06-05T14:30:00Z")

    result = snapshot_resolver.resolve_source(tmp_path, "odds_broad", "T-72h_early", now)

    assert result["selected_snapshot_path"].endswith("the-odds-api-multibook-20260605T120000Z.json")
