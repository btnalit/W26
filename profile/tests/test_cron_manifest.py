from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WC26_PROFILE = "wc26-handicap-analyst"


def load_module(name: str, rel: str):
    path = ROOT / rel
    assert path.exists(), f"missing deploy helper: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cron_manifest_declares_required_wc26_jobs_profile_and_scripts_exist() -> None:
    manifest_path = ROOT / "deploy" / "cron-manifest.json"
    assert manifest_path.exists(), "deploy/cron-manifest.json is the scheduler source of truth"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["profile"] == WC26_PROFILE
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
        assert jobs[required]["profile"] == WC26_PROFILE
        assert jobs[required]["mode"] == "no-agent"
        assert jobs[required]["workdir"] == "/hermesdata/worldcup-2026-handicap"
        assert (ROOT / "profile" / "scripts" / jobs[required]["script"]).exists()

    assert jobs["wc26-fixture-collect"]["env"]["WC26_FORCE_REFRESH"] == "1"


def test_cron_manifest_check_reports_missing_jobs() -> None:
    cron_check = load_module("cron_check", "deploy/check_cron_manifest.py")
    actual = {"jobs": [{"name": "memory-os-owner-review-digest", "script": "memory_os_cron_owner_review_digest_gate.py", "actual_profile": "default"}]}
    manifest = {
        "jobs": [
            {"name": "wc26-postmatch-grade", "profile": WC26_PROFILE, "script": "wc26-postmatch-grade.py", "required": True, "mode": "no-agent", "deliver": "local"},
            {"name": "wc26-postmatch-notify", "profile": WC26_PROFILE, "script": "wc26-postmatch-notify.py", "required": True, "mode": "no-agent", "deliver": "telegram"},
        ]
    }

    result = cron_check.compare_manifest_to_cron(manifest, actual)

    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert [diff["kind"] for diff in result["differences"]] == ["missing", "missing"]
    assert result["expected"][0]["profile"] == WC26_PROFILE


def test_cron_manifest_check_accepts_expected_profile_name_entrypoint_triples() -> None:
    cron_check = load_module("cron_check", "deploy/check_cron_manifest.py")
    actual = {
        "jobs": [
            {"name": "wc26-postmatch-grade", "script": "wc26-postmatch-grade.py", "actual_profile": WC26_PROFILE, "deliver": "local", "no_agent": True, "enabled": True},
            {"name": "wc26-postmatch-notify", "script": "wc26-postmatch-notify.py", "actual_profile": WC26_PROFILE, "deliver": "telegram", "no_agent": True, "enabled": True},
        ]
    }
    manifest = {
        "jobs": [
            {"name": "wc26-postmatch-grade", "profile": WC26_PROFILE, "script": "wc26-postmatch-grade.py", "required": True, "mode": "no-agent", "deliver": "local"},
            {"name": "wc26-postmatch-notify", "profile": WC26_PROFILE, "script": "wc26-postmatch-notify.py", "required": True, "mode": "no-agent", "deliver": "telegram"},
        ]
    }

    result = cron_check.compare_manifest_to_cron(manifest, actual)

    assert result["ok"] is True
    assert result["differences"] == []


def test_cron_manifest_check_rejects_misplaced_default_profile_job() -> None:
    cron_check = load_module("cron_check", "deploy/check_cron_manifest.py")
    manifest = {
        "jobs": [
            {"name": "wc26-postmatch-notify", "profile": WC26_PROFILE, "script": "wc26-postmatch-notify.py", "required": True, "mode": "no-agent", "deliver": "telegram"},
        ]
    }
    actual = {
        "jobs": [
            {"id": "good", "name": "wc26-postmatch-notify", "script": "wc26-postmatch-notify.py", "actual_profile": WC26_PROFILE, "registry_profile": WC26_PROFILE, "deliver": "telegram", "no_agent": True, "enabled": True},
            {"id": "bad", "name": "wc26-postmatch-notify", "script": "wc26-postmatch-notify.py", "actual_profile": "default", "registry_profile": "default", "deliver": "telegram", "no_agent": True, "enabled": True},
        ]
    }

    result = cron_check.compare_manifest_to_cron(manifest, actual)

    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert result["differences"][0]["kind"] == "misplaced"
    assert result["differences"][0]["actual"]["profile"] == "default"
    assert result["differences"][0]["expected"]["profile"] == WC26_PROFILE


def test_register_script_exists_for_deterministic_register_update_cleanup() -> None:
    script = ROOT / "deploy" / "register_wc26_cron.py"
    text = script.read_text(encoding="utf-8")
    assert "create_or_update" in text
    assert "removed_misplaced" in text
    assert "hermes" in text and "--profile" in text
