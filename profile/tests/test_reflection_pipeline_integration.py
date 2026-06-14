from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_module(name: str, filename: str):
    path = ROOT.parent / "skills" / "odds-analysis" / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_cache(path: Path, matchday: int = 1):
    payload = {
        "matches": [
            {
                "id": 9001,
                "utcDate": "2026-06-11T19:00:00Z",
                "stage": "GROUP_STAGE",
                "group": "GROUP_A",
                "matchday": matchday,
                "status": "TIMED",
                "homeTeam": {"name": "Alpha", "tla": "ALP"},
                "awayTeam": {"name": "Beta", "tla": "BET"},
            }
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def pipeline_args(tmp_path: Path, fixture_path: Path):
    return argparse.Namespace(
        workspace=tmp_path,
        fixture_path=fixture_path,
        match_id="M001",
        mode="simulation",
        source_quality="C",
        final_status="watch",
        window="early_structural",
        timing_class="early_structural",
        information_event="structural",
        market_set="handicap",
        as_of_utc="2026-05-01T00:00:00Z",
        home_odds=1.5,
        draw_odds=4.0,
        away_odds=6.0,
        odds_format="decimal",
        home_xg=1.6,
        away_xg=0.8,
        max_goals=8,
        ah_line=-1.0,
        ah_price=1.9,
        ah_price_format="decimal",
        standings_path=None,
        remaining_fixtures_path=None,
        advancement_rules_path=None,
        settled_ledger_path=None,
        deep_research_path=None,
    )


def test_reflection_layer_in_manifest_and_report_section10(tmp_path: Path):
    pipeline = load_module("wc26_match_pipeline_reflection", "wc26_match_pipeline.py")
    fixture_path = tmp_path / "fixtures.json"
    fixture_cache(fixture_path, matchday=1)
    ledger_path = tmp_path / "settled-ledger.json"
    ledger_path.write_text(json.dumps([
        {"phase": "opener", "actual_over25": False, "market_over25_implied": 0.49, "favorite_covered_main_handicap": False}
        for _ in range(8)
    ]), encoding="utf-8")
    args = pipeline_args(tmp_path, fixture_path)
    args.settled_ledger_path = ledger_path

    result = pipeline.compile_report(args)

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    reflection = manifest["reflection_layer"]
    assert reflection["contract"] == "wc26.reflection_layer.v1"
    assert reflection["phase_context"]["contract"] == "wc26.phase_context.v1"
    assert reflection["bias_mirror"]["contract"] == "wc26.bias_mirror.v1"
    assert reflection["no_play_classification"]["contract"] == "wc26.no_play_classification.v1"
    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "## 10. 复盘诊断附录(描述性)" in report
    assert "阶段先验" in report
    assert "偏差校正镜" in report
    assert "NO PLAY分类" in report


def test_pipeline_wiring_covers_reflection_producers() -> None:
    wiring = json.loads((ROOT.parent / "config" / "pipeline-wiring.json").read_text(encoding="utf-8"))
    generated = {item["capability"]: item for item in wiring["generated_capabilities"]}
    for capability, marker in {
        "phase_context": "build_reflection_layer(",
        "bias_mirror": "build_reflection_layer(",
        "no_play_classification": "build_reflection_layer(",
    }.items():
        assert capability in generated
        orchestrators = generated[capability]["orchestrators"]
        assert any(o["path"] == "profile/skills/odds-analysis/scripts/wc26_match_pipeline.py" for o in orchestrators)
        assert any(marker in ''.join(o.get("generation_call_markers", [])) for o in orchestrators)
