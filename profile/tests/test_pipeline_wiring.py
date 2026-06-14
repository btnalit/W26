from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_report_consumed_capabilities_have_generation_wiring() -> None:
    wiring_path = ROOT / "profile" / "config" / "pipeline-wiring.json"
    assert wiring_path.exists(), "machine-readable wiring registry prevents consumer-only orphan modules"
    wiring = json.loads(wiring_path.read_text(encoding="utf-8"))

    generated = {item["capability"]: item for item in wiring["generated_capabilities"]}
    for capability in ["path_a_crossbook", "path_c_consistency", "role_engine", "mechanism_audit", "motivation_context"]:
        assert capability in generated
        item = generated[capability]
        assert item["producer"]
        for orchestrator in item["orchestrators"]:
            source = read(orchestrator)
            assert item["producer"] in source, f"{capability} producer not wired in {orchestrator}"


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
    wiring_path = ROOT / "profile" / "config" / "pipeline-wiring.json"
    assert wiring_path.exists()
    wiring = json.loads(wiring_path.read_text(encoding="utf-8"))
    sidecars = {item["script"]: item for item in wiring["sidecars"]}

    assert sidecars["rich_summary.py"]["status"] == "sidecar_recovery_summary"
    assert "blocked_recovery.py" in sidecars["rich_summary.py"]["called_by"]
    assert sidecars["direct_summary.py"]["status"] == "primary_direct_summary"
