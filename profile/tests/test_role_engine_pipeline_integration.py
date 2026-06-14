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


def fixture_cache(path: Path):
    path.write_text(
        json.dumps(
            {
                "matches": [
                    {
                        "id": 9001,
                        "utcDate": "2026-06-11T19:00:00Z",
                        "stage": "GROUP_STAGE",
                        "group": "GROUP_A",
                        "matchday": 1,
                        "status": "TIMED",
                        "homeTeam": {"name": "Alpha", "tla": "ALP"},
                        "awayTeam": {"name": "Beta", "tla": "BET"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


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


def test_pipeline_generates_role_engine_before_mechanism_audit(tmp_path: Path):
    pipeline = load_module("wc26_match_pipeline_role", "wc26_match_pipeline.py")
    fixture_path = tmp_path / "fixtures.json"
    fixture_cache(fixture_path)

    result = pipeline.compile_report(pipeline_args(tmp_path, fixture_path))

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    role_entries = [a for a in manifest["artifacts"] if a.get("artifact_type") == "role_engine"]
    mechanism_entries = [a for a in manifest["artifacts"] if a.get("artifact_type") == "mechanism_audit"]
    assert len(role_entries) == 1
    assert "role_engine" in role_entries[0].get("provides", [])
    assert manifest["analysis_gates"]["role_engine"] == "pass"
    assert len(mechanism_entries) == 1

    role_payload = json.loads(Path(role_entries[0]["path"]).read_text(encoding="utf-8"))
    assert role_payload["engine_contract"] == "wc26.role_engine.v1"
    assert len(role_payload["role_conclusions"]) == 5
    assert {row["actionability"] for row in role_payload["role_conclusions"]} <= {
        "never_actionable",
        "supports_path_a",
        "contradicts_path_a",
    }

    mechanism_payload = json.loads(Path(mechanism_entries[0]["path"]).read_text(encoding="utf-8"))
    assert mechanism_payload["mechanisms"]["role_engine"]["status"].startswith("COMPLETE")
    assert mechanism_payload["mechanisms"]["role_engine"]["engine_version"] != "N/A"


def test_report_contains_role_engine_section(tmp_path: Path):
    pipeline = load_module("wc26_match_pipeline_role_report", "wc26_match_pipeline.py")
    fixture_path = tmp_path / "fixtures.json"
    fixture_cache(fixture_path)

    result = pipeline.compile_report(pipeline_args(tmp_path, fixture_path))

    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "## 9B. 博弈读盘" in report
    assert "role_engine_contract: wc26.role_engine.v1" in report
    assert "deterministic_v1 only reads artifacts" in report
