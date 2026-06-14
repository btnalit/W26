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


def standing(pts, gd=0, gf=0, played=2, w=0, d=0, l=0):
    ga = gf - gd
    return {"played": played, "W": w, "D": d, "L": l, "GF": gf, "GA": ga, "GD": gd, "Pts": pts}


def base_match(matchday=3, home="Alpha", away="Beta"):
    return {"match_id": "M099", "home": home, "away": away, "group": "GROUP_X", "matchday": matchday}


def rules():
    return {"direct_slots": 2, "best_third_slots": 0, "tiebreakers": ["Pts", "GD", "GF"]}


def test_matchday_1_2_returns_none():
    mc = load_module("motivation_context", "motivation_context.py")
    standings = {"Alpha": standing(0), "Beta": standing(0), "Gamma": standing(0), "Delta": standing(0)}

    for matchday in (1, 2):
        artifact = mc.analyze_motivation_context(
            standings=standings,
            group_remaining_fixtures=[{"home": "Alpha", "away": "Beta"}, {"home": "Gamma", "away": "Delta"}],
            match_under_analysis=base_match(matchday=matchday),
            advancement_rules=rules(),
        )

        assert artifact["status"] == "none"
        assert artifact["situation_tag"] == "NONE"
        assert artifact["team_motivation"] == {}
        assert "首轮/次轮" in artifact["footnote_zh"] or "无动机变量" in artifact["footnote_zh"]


def test_mutual_draw_incentive_detected():
    mc = load_module("motivation_context_mutual", "motivation_context.py")
    # Alpha/Beta both on 4 pts; Gamma can reach only 5 but cannot pass either on GD.
    # A draw puts both on 5 and locks the two direct slots.
    standings = {
        "Alpha": standing(4, gd=2, gf=3),
        "Beta": standing(4, gd=1, gf=2),
        "Gamma": standing(2, gd=-3, gf=1),
        "Delta": standing(0, gd=-3, gf=0),
    }

    artifact = mc.analyze_motivation_context(
        standings=standings,
        group_remaining_fixtures=[{"home": "Alpha", "away": "Beta"}, {"home": "Gamma", "away": "Delta"}],
        match_under_analysis=base_match(),
        advancement_rules=rules(),
    )

    assert artifact["status"] == "active"
    assert artifact["team_motivation"]["Alpha"]["tag"] == "draw_enough"
    assert artifact["team_motivation"]["Beta"]["tag"] == "draw_enough"
    assert artifact["situation_tag"] == "MUTUAL_DRAW_INCENTIVE"
    assert artifact["priority"] == "high"
    assert artifact["model_hint"]["draw_prob"] == "underestimated_by_model"
    assert artifact["model_hint"]["magnitude"] == "qualitative_only"


def test_dead_rubber_detected():
    mc = load_module("motivation_context_dead", "motivation_context.py")
    standings = {
        "Alpha": standing(3, gd=0, gf=2),
        "Beta": standing(0, gd=-4, gf=0),
        "Gamma": standing(6, gd=4, gf=5),
        "Delta": standing(3, gd=0, gf=2),
    }

    artifact = mc.analyze_motivation_context(
        standings=standings,
        group_remaining_fixtures=[{"home": "Alpha", "away": "Beta"}, {"home": "Gamma", "away": "Delta"}],
        match_under_analysis=base_match(),
        advancement_rules=rules(),
    )

    assert artifact["team_motivation"]["Beta"]["tag"] == "dead_rubber"
    assert artifact["situation_tag"] == "DEAD_RUBBER_ASYMMETRY"
    assert artifact["model_hint"]["direction"] == "demand_side_cover↑"


def test_already_through_detected():
    mc = load_module("motivation_context_through", "motivation_context.py")
    standings = {
        "Alpha": standing(6, gd=5, gf=6),
        "Beta": standing(3, gd=0, gf=2),
        "Gamma": standing(3, gd=-1, gf=1),
        "Delta": standing(0, gd=-4, gf=0),
    }

    artifact = mc.analyze_motivation_context(
        standings=standings,
        group_remaining_fixtures=[{"home": "Alpha", "away": "Beta"}, {"home": "Gamma", "away": "Delta"}],
        match_under_analysis=base_match(),
        advancement_rules=rules(),
    )

    assert artifact["team_motivation"]["Alpha"]["tag"] == "already_through"
    assert artifact["team_motivation"]["Beta"]["tag"] in {"must_win", "win_to_control", "complex"}
    assert artifact["situation_tag"] in {"ROTATION_VS_DESPERATION", "COMPLEX_MOTIVATION"}


def test_tiebreak_goal_difference():
    mc = load_module("motivation_context_tiebreak", "motivation_context.py")
    standings = {
        "Alpha": standing(3, gd=3, gf=4),
        "Beta": standing(3, gd=0, gf=2),
        "Gamma": standing(3, gd=0, gf=2),
        "Delta": standing(3, gd=-3, gf=1),
    }

    final_table = mc.final_table_after_results(
        standings,
        fixtures=[{"home": "Alpha", "away": "Beta"}, {"home": "Gamma", "away": "Delta"}],
        result_by_fixture={0: "D", 1: "H"},
        advancement_rules=rules(),
    )

    assert [row["team"] for row in final_table[:2]] == ["Gamma", "Alpha"]
    assert final_table[1]["team"] == "Alpha"
    assert final_table[1]["GD"] > final_table[2]["GD"]


def test_market_priced_in_returns_noplay():
    mc = load_module("motivation_context_market", "motivation_context.py")

    check = mc.market_reflection_check(
        direction="draw↑ / total↓ / cover↓",
        market_profile={"draw_prob_state": "elevated_vs_baseline"},
        neutral_baseline={"draw_prob_state": "normal"},
        path_a_scan={"soft_vs_sharp_lag": False, "passes_5pct_gate": False},
    )

    assert check["status"] == "priced_in"
    assert check["decision"] == "NO_PLAY"
    assert check["actionability"] == "diagnostic_only"


def test_no_specific_pp_in_output():
    mc = load_module("motivation_context_no_pp", "motivation_context.py")
    standings = {
        "Alpha": standing(4, gd=2, gf=3),
        "Beta": standing(4, gd=1, gf=2),
        "Gamma": standing(3, gd=0, gf=2),
        "Delta": standing(0, gd=-3, gf=0),
    }

    artifact = mc.analyze_motivation_context(
        standings=standings,
        group_remaining_fixtures=[{"home": "Alpha", "away": "Beta"}, {"home": "Gamma", "away": "Delta"}],
        match_under_analysis=base_match(),
        advancement_rules=rules(),
    )

    rendered = json.dumps(artifact, ensure_ascii=False)
    forbidden = ["pp", "percentage_point", "probability_delta", "magnitude_pp"]
    assert not any(token in rendered for token in forbidden)
    assert artifact["model_hint"]["magnitude"] == "qualitative_only"
