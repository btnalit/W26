import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load_module(name: str, filename: str):
    candidates = [
        ROOT / filename,
        ROOT.parent / "skills" / "odds-analysis" / "scripts" / filename,
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def m010_pinnacle():
    return {
        "match": "Netherlands vs Japan",
        "bookmaker": "pinnacle",
        "h2h": {"Netherlands": 1.99, "Draw": 3.73, "Japan": 3.80},
        "spreads": {
            "Netherlands": {"price": 2.00, "point": -0.5},
            "Japan": {"price": 1.91, "point": 0.5},
        },
        "totals": {
            "Over": {"price": 1.97, "point": 2.5},
            "Under": {"price": 1.91, "point": 2.5},
        },
    }


def test_market_profile_generated_and_sorted():
    ct = load_module("consistency_triangle", "consistency_triangle.py")
    result = ct.analyze_consistency(m010_pinnacle())
    profile = result["market_profile"]

    assert profile["contract"] == "wc26.market_profile.v1"
    assert profile["status"] == "ok"
    assert profile["fit"]["max_abs_residual_pp"] <= 8.0
    assert "非下注信号" in profile["footnote_zh"]
    assert len(profile["top_scores"]) == 6

    sortable = [
        (-row["prob"], row["home_goals"], row["away_goals"])
        for row in profile["top_scores"]
    ]
    assert sortable == sorted(sortable)


def test_quarter_total_profile_uses_settlement_equiv():
    ct = load_module("consistency_triangle_q", "consistency_triangle.py")
    data = m010_pinnacle()
    data["totals"] = {
        "Over": {"price": 1.91, "point": 2.75},
        "Under": {"price": 1.97, "point": 2.75},
    }
    result = ct.analyze_consistency(data)
    lean = result["market_profile"]["total_line_lean"]

    assert lean["line"] == 2.75
    assert 0.0 <= lean["over_settlement_equiv"] <= 1.0
    assert 0.0 <= lean["under_settlement_equiv"] <= 1.0
    assert abs((lean["over_settlement_equiv"] + lean["under_settlement_equiv"]) - 1.0) < 0.01


def test_suppressed_profile_has_reason():
    ct = load_module("consistency_triangle_s", "consistency_triangle.py")
    profile = ct.build_market_profile(
        {
            "suppressed": True,
            "confidence": "suppressed",
            "max_abs_residual_pp": 9.2,
            "lambda_home": 1.0,
            "lambda_away": 1.0,
            "rho": 0.0,
        },
        "Home",
        "Away",
        2.5,
    )
    assert profile["status"] == "suppressed"
    assert profile["reason"] == "fit_residual_gt_8pp"


def test_summary_market_profile_projection_is_descriptive():
    ct = load_module("consistency_triangle_for_summary", "consistency_triangle.py")
    direct = load_module("direct_summary", "direct_summary.py")
    rich = load_module("rich_summary", "rich_summary.py")
    profile = ct.analyze_consistency(m010_pinnacle())["market_profile"]

    direct_lines = direct.market_profile_lines(profile)
    rich_lines = rich.market_profile_lines(profile)

    assert any("市场画像" in line for line in direct_lines)
    assert any("非下注信号" in line for line in direct_lines)
    assert direct_lines == rich_lines
    assert not any("value" in line.lower() or "edge" in line.lower() for line in direct_lines)


def test_match_filter_accepts_vs_separator(tmp_path: Path):
    ct = load_module("consistency_triangle_match_filter", "consistency_triangle.py")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "data": [
                    {
                        "home_team": "Haiti",
                        "away_team": "Scotland",
                        "bookmakers": [
                            {
                                "key": "pinnacle",
                                "markets": [
                                    {
                                        "key": "h2h",
                                        "outcomes": [
                                            {"name": "Haiti", "price": 6.79},
                                            {"name": "Draw", "price": 4.39},
                                            {"name": "Scotland", "price": 1.51},
                                        ],
                                    },
                                    {
                                        "key": "spreads",
                                        "outcomes": [
                                            {"name": "Haiti", "price": 2.02, "point": 1.0},
                                            {"name": "Scotland", "price": 1.88, "point": -1.0},
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
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    results = ct.analyze_snapshot(str(snapshot), "Haiti vs Scotland")

    assert len(results) == 1
    assert results[0]["match"] == "Haiti vs Scotland"
    assert "market_profile" in results[0]


def test_wide_spread_suppresses_path_c_signal_but_keeps_market_profile():
    ct = load_module("consistency_triangle_wide_spread", "consistency_triangle.py")
    data = {
        "match": "Haiti vs Scotland",
        "bookmaker": "pinnacle",
        "h2h": {"Haiti": 6.79, "Draw": 4.39, "Scotland": 1.51},
        "spreads": {
            "Haiti": {"price": 2.02, "point": 1.0},
            "Scotland": {"price": 1.88, "point": -1.0},
        },
        "totals": {
            "Over": {"price": 1.98, "point": 2.5},
            "Under": {"price": 1.91, "point": 2.5},
        },
    }

    result = ct.analyze_consistency(data)

    assert result["analysis"]["spread_warning"]
    assert result["signal"]["suppressed"] is True
    assert result["signal"]["type"] is None
    assert result["discrepancy"]["pp"] is None
    assert result["discrepancy"]["raw_pp"] is not None
    assert result["market_profile"]["status"] == "ok"
