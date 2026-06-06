import importlib.util
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
