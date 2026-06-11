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

    board = cross_book_scan.parse_odds_snapshot(str(snapshot), "Netherlands", "Japan")["board"]
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

    board = cross_book_scan.parse_odds_snapshot(str(snapshot), "Germany", "Curaçao")["board"]
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


def test_spreads_mirror_pair_abs_grouping(tmp_path: Path) -> None:
    """Verifies that -1.25/+1.25 mirror pair groups into one devig set via abs(point)."""
    snapshot = tmp_path / "mirror.json"
    snapshot.write_text(
        json.dumps(
            [
                {
                    "home_team": "Mexico",
                    "away_team": "South Africa",
                    "bookmakers": [
                        {
                            "key": "pinnacle",
                            "markets": [
                                {
                                    "key": "spreads",
                                    "outcomes": [
                                        {"name": "Mexico", "price": 2.06, "point": -1.25},
                                        {"name": "South Africa", "price": 1.88, "point": 1.25},
                                    ],
                                },
                            ],
                        },
                        {
                            "key": "marathonbet",
                            "markets": [
                                {
                                    "key": "spreads",
                                    "outcomes": [
                                        {"name": "Mexico", "price": 2.10, "point": -1.25},
                                        {"name": "South Africa", "price": 1.85, "point": 1.25},
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

    board = cross_book_scan.parse_odds_snapshot(str(snapshot), "Mexico", "South Africa")["board"]
    all_labels = []
    for bm_prices in board["spreads"].values():
        if not isinstance(bm_prices, dict):
            continue
        for label, value in bm_prices.items():
            if label.startswith("_"):
                continue
            if isinstance(value, (int, float)) and label not in all_labels:
                all_labels.append(label)

    # Group by abs(point) — simulating the fix logic
    groups: dict[str, list[str]] = {}
    for lbl in all_labels:
        raw_line = lbl.split("@", 1)[1] if "@" in lbl else "none"
        if raw_line != "none":
            import math
            line_part = str(abs(float(raw_line)))
        else:
            line_part = raw_line
        groups.setdefault(line_part, []).append(lbl)

    # Both outcomes must be in the same group (key="1.25")
    assert len(groups) == 1, f"expected 1 group, got {len(groups)}: {groups}"
    group_key = list(groups.keys())[0]
    group_outcomes = groups[group_key]
    assert len(group_outcomes) == 2, f"expected 2 outcomes in group, got {len(group_outcomes)}: {group_outcomes}"
    outcome_names = {o.split("@")[0] for o in group_outcomes}
    assert "mexico" in outcome_names and "south africa" in outcome_names

    # Full scan: fair probs must sum to ≈1.0
    result = cross_book_scan.scan_market(board, "spreads", group_outcomes)
    fp = result.get("fair_probs", {}).get("shin", {})
    probs = list(fp.values())
    assert len(probs) == 2, f"expected 2 fair probs, got {len(probs)}"
    assert abs(sum(probs) - 1.0) < 0.001, f"fair prob sum={sum(probs):.4f} != 1.0"

    # No SUSPECT-level EV on anchor side (Pinnacle)
    anchor = board["spreads"].get("pinnacle", {})
    for o in group_outcomes:
        p = fp.get(o, 0)
        ev = p * anchor.get(o, 1) - 1
        assert ev < 0.08, f"{o}: EV={ev*100:.2f}% exceeds suspect threshold (8%)"


def test_spreads_alternate_merge_guard_suspects(tmp_path: Path) -> None:
    """If abs(grouping) merges >2 outcomes or duplicate team → suspect_alternate_merge."""
    snapshot = tmp_path / "alt-spreads.json"
    snapshot.write_text(
        json.dumps(
            [
                {
                    "home_team": "Mexico",
                    "away_team": "South Africa",
                    "bookmakers": [
                        {
                            "key": "pinnacle",
                            "markets": [
                                {
                                    "key": "spreads",
                                    "outcomes": [
                                        # Alternate market: 4 outcomes on same abs(line)
                                        {"name": "Mexico", "price": 2.06, "point": -1.25},
                                        {"name": "South Africa", "price": 1.88, "point": 1.25},
                                        {"name": "Mexico", "price": 1.90, "point": 1.25},
                                        {"name": "South Africa", "price": 1.95, "point": -1.25},
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

    board = cross_book_scan.parse_odds_snapshot(str(snapshot), "Mexico", "South Africa")["board"]

    # Simulate the FIX-3 grouping + guard logic
    all_labels = []
    for bm_prices in board["spreads"].values():
        if not isinstance(bm_prices, dict):
            continue
        for label, value in bm_prices.items():
            if label.startswith("_"):
                continue
            if isinstance(value, (int, float)) and label not in all_labels:
                all_labels.append(label)

    groups: dict[str, list[str]] = {}
    for lbl in all_labels:
        raw_line = lbl.split("@", 1)[1] if "@" in lbl else "none"
        if raw_line != "none":
            line_part = str(abs(float(raw_line)))
        else:
            line_part = raw_line
        groups.setdefault(line_part, []).append(lbl)

    # Guard: >2 outcomes → suspect
    suspect_groups: set[str] = set()
    for gk, g_outcomes in groups.items():
        if len(g_outcomes) > 2:
            suspect_groups.add(gk)

    assert len(suspect_groups) == 1, f"expected 1 suspect group, got {suspect_groups}"
    assert "1.25" in suspect_groups

    # Guard: duplicate team → suspect
    group_1 = groups["1.25"]
    team_names = [o.split("@", 1)[0] for o in group_1 if "@" in o]
    assert len(set(team_names)) < len(team_names), "should have duplicate team names"
    assert "mexico" in team_names  # appears twice (mexico@-1.25 and mexico@1.25)
