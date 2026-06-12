from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, rel: str):
    path = ROOT / rel
    assert path.exists(), f"missing deploy helper: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cron_manifest_declares_required_wc26_jobs_and_scripts_exist() -> None:
    manifest_path = ROOT / "deploy" / "cron-manifest.json"
    assert manifest_path.exists(), "deploy/cron-manifest.json is the scheduler source of truth"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    jobs = {job["name"]: job for job in payload["jobs"]}
    for required in [
        "wc26-fixture-collect",
        "wc26-match-window-direct",
        "wc26-postmatch-grade",
        "wc26-postmatch-notify",
        "wc26-blocked-recovery",
    ]:
        assert required in jobs
        assert jobs[required]["required"] is True
        assert (ROOT / "profile" / "scripts" / jobs[required]["script"]).exists()

    assert jobs["wc26-fixture-collect"]["env"]["WC26_FORCE_REFRESH"] == "1"


def test_cron_manifest_check_reports_missing_jobs() -> None:
    cron_check = load_module("cron_check", "deploy/check_cron_manifest.py")
    actual = """
  2af755464ca8 [active]
    Name:      memory-os-owner-review-digest
    Script:    memory_os_cron_owner_review_digest_gate.py
"""
    manifest = {
        "jobs": [
            {"name": "wc26-postmatch-grade", "script": "wc26-postmatch-grade.py", "required": True},
            {"name": "wc26-postmatch-notify", "script": "wc26-postmatch-notify.py", "required": True},
        ]
    }

    result = cron_check.compare_manifest_to_cron(manifest, actual)

    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert result["missing_jobs"] == ["wc26-postmatch-grade", "wc26-postmatch-notify"]


def test_cron_manifest_check_accepts_expected_job_names() -> None:
    cron_check = load_module("cron_check", "deploy/check_cron_manifest.py")
    actual = """
  abc [active]
    Name:      wc26-postmatch-grade
    Script:    wc26-postmatch-grade.py
  def [active]
    Name:      wc26-postmatch-notify
    Script:    wc26-postmatch-notify.py
"""
    manifest = {
        "jobs": [
            {"name": "wc26-postmatch-grade", "script": "wc26-postmatch-grade.py", "required": True},
            {"name": "wc26-postmatch-notify", "script": "wc26-postmatch-notify.py", "required": True},
        ]
    }

    result = cron_check.compare_manifest_to_cron(manifest, actual)

    assert result["ok"] is True
    assert result["missing_jobs"] == []
