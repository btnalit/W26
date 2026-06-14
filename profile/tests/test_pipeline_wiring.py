from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def load_wiring() -> dict:
    wiring_path = ROOT / "profile" / "config" / "pipeline-wiring.json"
    assert wiring_path.exists(), "machine-readable wiring registry prevents consumer-only orphan modules"
    return json.loads(wiring_path.read_text(encoding="utf-8"))


def assert_generation_wired(wiring: dict, source_reader: Callable[[str], str] = read) -> None:
    generated = {item["capability"]: item for item in wiring["generated_capabilities"]}
    consumed = wiring.get("report_consumed_capabilities")
    assert consumed, "report_consumed_capabilities must list real consumer-side reads; empty list makes this guard inert"

    for consumer_item in consumed:
        capability = consumer_item["capability"]
        assert capability in generated, f"report consumes {capability} but no producer wiring is declared"

    for capability, item in generated.items():
        assert item["producer"]
        orchestrators = item.get("orchestrators") or []
        assert orchestrators, f"{capability} must declare at least one orchestrator"
        for orchestrator in orchestrators:
            path = orchestrator["path"]
            source = source_reader(path)
            call_markers = orchestrator.get("generation_call_markers") or []
            assert call_markers, f"{capability} in {path} must assert generation calls, not producer filename strings"
            for marker in call_markers:
                assert marker in source, f"{capability} generation call marker {marker!r} not wired in {path}"
            for marker in orchestrator.get("manifest_markers", []):
                assert marker in source, f"{capability} manifest marker {marker!r} not assembled in {path}"


def test_report_consumed_capabilities_have_generation_wiring() -> None:
    assert_generation_wired(load_wiring())


def test_role_engine_wiring_guard_fails_when_generation_call_is_removed() -> None:
    wiring = load_wiring()

    def poisoned_reader(path: str) -> str:
        source = read(path)
        if path == "profile/skills/odds-analysis/scripts/wc26_match_pipeline.py":
            return source.replace("build_role_artifact(", "REMOVED_BUILD_ROLE_ARTIFACT(")
        return source

    with pytest.raises(AssertionError, match="role_engine generation call marker"):
        assert_generation_wired(wiring, poisoned_reader)


def assert_consumed_markers_exist(wiring: dict, source_reader: Callable[[str], str] = read) -> None:
    consumed = wiring.get("report_consumed_capabilities") or []
    assert consumed
    for item in consumed:
        capability = item["capability"]
        consumers = item.get("consumers") or []
        assert consumers, f"{capability} must declare the consumer files that actually read it"
        for consumer in consumers:
            source = source_reader(consumer["path"])
            markers = consumer.get("read_markers") or []
            assert markers, f"{capability} consumer {consumer['path']} must declare concrete read markers"
            for marker in markers:
                assert marker in source, f"{capability} consumer marker {marker!r} missing in {consumer['path']}"


def test_consumed_capability_evidence_markers_exist_in_consumers() -> None:
    assert_consumed_markers_exist(load_wiring())


def test_role_engine_consumer_guard_fails_when_read_marker_is_fake() -> None:
    wiring = load_wiring()
    for item in wiring["report_consumed_capabilities"]:
        if item["capability"] == "role_engine":
            item["consumers"][0]["read_markers"].append("FAKE_ROLE_ENGINE_READ_MARKER_DOES_NOT_EXIST")
            break
    else:
        raise AssertionError("role_engine must be present in report_consumed_capabilities")

    with pytest.raises(AssertionError, match="FAKE_ROLE_ENGINE_READ_MARKER_DOES_NOT_EXIST"):
        assert_consumed_markers_exist(wiring)


def test_opportunity_watch_is_required_registered_sidecar() -> None:
    manifest = json.loads((ROOT / "deploy" / "cron-manifest.json").read_text(encoding="utf-8"))
    jobs = {job["name"]: job for job in manifest["jobs"]}

    assert "wc26-opportunity-watch" in jobs
    job = jobs["wc26-opportunity-watch"]
    assert job["required"] is True
    assert job["profile"] == "wc26-handicap-analyst"
    assert job["script"] == "wc26-opportunity-watch.py"
    assert job["deliver"] == "telegram"
    assert job["mode"] == "no-agent"


def test_rich_summary_is_declared_recovery_sidecar_not_primary_pipeline() -> None:
    wiring = load_wiring()
    sidecars = {item["script"]: item for item in wiring["sidecars"]}

    assert sidecars["rich_summary.py"]["status"] == "sidecar_recovery_summary"
    assert "blocked_recovery.py" in sidecars["rich_summary.py"]["called_by"]
    assert sidecars["direct_summary.py"]["status"] == "primary_direct_summary"


def test_direct_report_bypass_paths_are_registered_for_role_engine_recovery() -> None:
    wiring = load_wiring()
    role_engine = next(item for item in wiring["generated_capabilities"] if item["capability"] == "role_engine")
    orchestrator_paths = {item["path"] for item in role_engine["orchestrators"]}

    assert "profile/skills/odds-analysis/scripts/blocked_recovery.py" in orchestrator_paths
    recovery = next(
        item
        for item in role_engine["orchestrators"]
        if item["path"] == "profile/skills/odds-analysis/scripts/blocked_recovery.py"
    )
    assert "local_snapshot_rebuild" in recovery.get("covers", [])
    assert "legacy_guarded_report" in recovery.get("covers", [])
    assert "cached_direct_report" in recovery.get("covers", [])
