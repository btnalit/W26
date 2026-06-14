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
    )


def test_pipeline_manifest_includes_fixture_matchday_and_motivation_none(tmp_path: Path):
    pipeline = load_module("wc26_match_pipeline_motivation", "wc26_match_pipeline.py")
    fixture_path = tmp_path / "fixtures.json"
    fixture_cache(fixture_path, matchday=1)

    result = pipeline.compile_report(pipeline_args(tmp_path, fixture_path))

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["match"]["matchday"] == 1
    motivation = manifest["motivation_context"]
    assert motivation["contract"] == "wc26.motivation_context.v1"
    assert motivation["status"] == "none"
    assert motivation["situation_tag"] == "NONE"
    assert any(entry.get("artifact_type") == "motivation_context" for entry in manifest["artifacts"])
    assert "motivation_context" in Path(result["report_path"]).read_text(encoding="utf-8")
