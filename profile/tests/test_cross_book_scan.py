#!/usr/bin/env python3
"""Path A cross-book scan contract tests."""

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


cross_book_scan = load_module("cross_book_scan", "skills/odds-analysis/scripts/cross_book_scan.py")


def test_cross_book_scan_records_all_quotes_and_cold_side_edge(tmp_path: Path) -> None:
    snapshot = tmp_path / "multibook.json"
    snapshot.write_text(
        json.dumps(
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
                                },
                                {
                                    "key": "spreads",
                                    "outcomes": [
                                        {"name": "Netherlands", "price": 2.00, "point": -0.5},
                                        {"name": "Japan", "price": 1.91, "point": 0.5},
                                    ],
                                },
                                {
                                    "key": "totals",
                                    "outcomes": [
                                        {"name": "Over", "price": 1.98, "point": 2.5},
                                        {"name": "Under", "price": 1.91, "point": 2.5},
                                    ],
                                },
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
                                        {"name": "Japan", "price": 4.05},
                                    ],
                                }
                            ],
                        },
                        {
                            "key": "softbook",
                            "markets": [
                                {
                                    "key": "spreads",
                                    "outcomes": [
                                        {"name": "Netherlands", "price": 1.98, "point": -0.5},
                                        {"name": "Japan", "price": 1.90, "point": 0.5},
                                    ],
                                },
                                {
                                    "key": "totals",
                                    "outcomes": [
                                        {"name": "Over", "price": 1.97, "point": 2.5},
                                        {"name": "Under", "price": 1.90, "point": 2.5},
                                    ],
                                },
                            ],
                        },
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    board = cross_book_scan.parse_odds_snapshot(str(snapshot), "Netherlands", "Japan")
    h2h = cross_book_scan.scan_market(board, "h2h", ["netherlands", "draw", "japan"])

    assert h2h["status"] == "ok"
    assert h2h["sharp_anchor"] == "pinnacle"
    assert h2h["quotes_scanned"] == 3
    assert {quote["outcome"] for quote in h2h["quotes"]} == {"netherlands", "draw", "japan"}
    assert h2h["edges"][0]["book"] == "marathonbet"
    assert h2h["edges"][0]["outcome"] == "japan"
    assert h2h["edges"][0]["survives_all_methods"] is True
    assert h2h["edges"][0]["ev_band"] == "noise_lt_5pp"

    results = {
        "markets": {
            "h2h": h2h,
            "spreads": cross_book_scan.scan_market(board, "spreads", ["netherlands@-0.5", "japan@0.5"]),
            "totals": cross_book_scan.scan_market(board, "totals", ["over@2.5", "under@2.5"]),
        }
    }
    summary = cross_book_scan.build_summary(results)
    assert summary["edge_count"] == 1
    assert summary["noise_edge_count"] == 1
    assert summary["actionable_count"] == 0
    assert summary["raw_actionable_count"] == 0
    assert summary["relay_actionable_count"] == 0
    assert summary["qualified_play_count"] == 0
    assert summary["best_edge"]["outcome"] == "japan"
    assert summary["best_actionable_edge"] is None
    assert summary["quotes_scanned"] == 7


def test_cross_book_scan_normalizes_betfair_exchange_regional_key(tmp_path: Path) -> None:
    snapshot = tmp_path / "m009-multibook.json"
    snapshot.write_text(
        json.dumps(
            [
                {
                    "home_team": "Germany",
                    "away_team": "Curaçao",
                    "bookmakers": [
                        {
                            "key": "betfair_ex_eu",
                            "title": "Betfair",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "outcomes": [
                                        {"name": "Curaçao", "price": 55.0},
                                        {"name": "Germany", "price": 1.06},
                                        {"name": "Draw", "price": 24.0},
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
                                        {"name": "Curaçao", "price": 61.0},
                                        {"name": "Germany", "price": 1.04},
                                        {"name": "Draw", "price": 17.25},
                                    ],
                                }
                            ],
                        },
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    board = cross_book_scan.parse_odds_snapshot(str(snapshot), "Germany", "Curaçao")
    h2h = cross_book_scan.scan_market(board, "h2h", ["curaçao", "germany", "draw"])

    assert "betfair_ex" in board["h2h"]
    assert "betfair_ex_eu" not in board["h2h"]
    assert h2h["status"] == "ok"
    assert h2h["sharp_anchor"] == "betfair_ex"
    assert h2h["quotes_scanned"] == 3
    assert h2h["edges"][0]["book"] == "marathonbet"
    assert h2h["edges"][0]["outcome"] == "curaçao"
    assert h2h["actionable_count"] == 1
    assert h2h["raw_actionable_count"] == 1
    assert h2h["relay_actionable_count"] == 0
    assert h2h["qualified_play_count"] == 0
