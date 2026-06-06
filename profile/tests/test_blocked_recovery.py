"""Tests for WC26 blocked recovery classification and bounded repair."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "skills" / "odds-analysis" / "scripts" / "blocked_recovery.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_fixture_cache(workspace: Path, *, home: str = "Example Home", away: str = "Sample Away") -> Path:
    return write_json(
        workspace / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json",
        {
            "matches": [
                {
                    "id": 900001,
                    "utcDate": "2026-06-14T00:00:00Z",
                    "stage": "GROUP_STAGE",
                    "group": "GROUP_X",
                    "matchday": 1,
                    "homeTeam": {"name": home, "tla": "EXH"},
                    "awayTeam": {"name": away, "tla": "SAA"},
                }
            ]
        },
    )


def test_classifier_routes_freeform_report_as_safety_block(tmp_path: Path) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)

    event = {
        "category": "safety_block",
        "reason": "wc26 report-like Telegram output missing guarded manifest/report binding",
        "response_excerpt": "WC26 M111 Example vs Sample AH -1.0 EV +23.5%",
    }

    classification = mod.classify_event(event)

    assert classification["category"] == "safety_block"
    assert classification["auto_recoverable"] is False


def test_safety_block_with_recoverable_legacy_routes_to_guarded_report(tmp_path: Path, monkeypatch) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)
    workspace = tmp_path / "workspace"
    mod.WORKSPACE = workspace
    mod.STATE_PATH = workspace / "state" / "blocked-recovery.json"

    event_path = write_json(
        workspace / "blocked_recovery" / "queue" / "br-legacy.json",
        {
            "recovery_id": "br:legacy",
            "category": "safety_block",
            "reason": "wc26 report-like Telegram output missing guarded manifest/report binding",
            "response_excerpt": "WC26 M123 Example vs Sample Path A report",
        },
    )

    monkeypatch.setattr(mod, "legacy_recovery_inputs", lambda match_id, event: {"ok": True} if match_id == "M123" else None)
    monkeypatch.setattr(
        mod,
        "recover_missing_guarded_report",
        lambda event: {
            "status": "recovered",
            "summary": "SUMMARY",
            "manifest_path": "/tmp/manifest-M123.json",
            "report_path": "/tmp/M123.md",
        },
    )

    result = mod.process_event(event_path, mod.load_state())

    assert result["status"] == "recovered"
    assert result["category"] == "missing_guarded_report"


def test_direct_request_label_resolves_match_id_from_fixture_cache(tmp_path: Path) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)
    workspace = tmp_path / "workspace"
    mod.WORKSPACE = workspace
    mod.SCRIPTS_DIR = SCRIPT_PATH.parent
    write_fixture_cache(workspace)
    direct_path = write_json(
        workspace / "direct_requests" / "2026-06-07" / "direct-abc.json",
        {
            "direct_request_id": "direct:abc",
            "match_id": "Example Home-vs-Sample Away",
            "match_label": "Example Home vs Sample Away",
            "request_text": "分析 Example Home vs Sample Away",
        },
    )
    event = {
        "category": "safety_block",
        "reason": "wc26 report-like Telegram output missing guarded manifest/report binding",
        "direct_request_ids": ["direct:abc"],
    }

    assert mod.extract_match_id(event) == "M001"
    assert mod.latest_direct_request_for_match("M001", event) == direct_path


def test_w_ordinal_normalizes_for_legacy_recovery(tmp_path: Path, monkeypatch) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)
    workspace = tmp_path / "workspace"
    mod.WORKSPACE = workspace
    mod.STATE_PATH = workspace / "state" / "blocked-recovery.json"

    event_path = write_json(
        workspace / "blocked_recovery" / "queue" / "br-w-legacy.json",
        {
            "recovery_id": "br:w-legacy",
            "category": "safety_block",
            "reason": "wc26 report-like Telegram output missing guarded manifest/report binding",
            "response_excerpt": "WC26 W123 Example vs Sample Path A report",
        },
    )

    monkeypatch.setattr(mod, "legacy_recovery_inputs", lambda match_id, event: {"ok": True} if match_id == "M123" else None)
    monkeypatch.setattr(
        mod,
        "recover_missing_guarded_report",
        lambda event: {
            "status": "recovered",
            "summary": "SUMMARY",
            "manifest_path": "/tmp/manifest-M123.json",
            "report_path": "/tmp/M123.md",
        },
    )

    result = mod.process_event(event_path, mod.load_state())

    assert result["status"] == "recovered"
    assert result["category"] == "missing_guarded_report"


def test_legacy_recovery_requires_real_crossbook_markets(tmp_path: Path) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)

    assert mod.recoverable_crossbook_payload(
        {
            "artifact_type": "crossbook_scan",
            "source_snapshot_id": "snap",
            "summary": {"quotes_scanned": 1},
        }
    ) is False
    assert mod.recoverable_crossbook_payload(
        {
            "artifact_type": "crossbook_scan",
            "source_snapshot_id": "snap",
            "markets": {"h2h": {"status": "ok"}},
        }
    ) is True


def test_recovery_fills_missing_direct_request_contract_fields(tmp_path: Path) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)
    direct_path = write_json(
        tmp_path / "direct_requests" / "2026-06-07" / "direct-abc.json",
        {
            "direct_request_id": "direct:abc",
            "request_text": "分析 Example Home vs Sample Away",
        },
    )
    manifest_path = tmp_path / "reports" / "artifacts" / "manifest-M001.json"
    report_path = tmp_path / "reports" / "match" / "M001.md"

    record = mod.update_direct_request_for_recovery(
        direct_path,
        manifest_path,
        report_path,
        "M001",
        "Example Home vs Sample Away",
        {"platform": "telegram", "chat_id": "12345", "created_at_utc": "2026-06-07T00:00:00+00:00"},
    )

    assert record["platform"] == "telegram"
    assert record["chat_id"] == "12345"
    assert record["created_at_utc"] == "2026-06-07T00:00:00+00:00"
    assert record["cache_mode"] == "legacy_guarded_recovery"
    assert record["recovery_trace"][0]["fields"] == ["platform", "chat_id", "created_at_utc"]


def test_classifier_detects_key_mismatch_as_detector_bug_not_source_gap(tmp_path: Path) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)

    event = {
        "category": "missing_source",
        "reason": "no sharp anchor for H2H; Betfair exchange H2H key mismatch in cross_book_scan sharp detection",
    }

    classification = mod.classify_event(event)

    assert "detector_bug" in classification["candidates"]
    assert classification["category"] == "missing_source"
    # The conservative priority keeps true source gaps above engineering bugs,
    # while still surfacing detector_bug for routing.


def test_missing_guarded_report_is_not_contract_mismatch(tmp_path: Path) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)

    classification = mod.classify_event(
        {
            "category": "missing_guarded_report",
            "reason": "no guarded report/manifest for this exact window yet",
            "match_id": "M888",
        }
    )

    assert classification["category"] == "missing_guarded_report"
    assert "contract_mismatch" not in classification["candidates"]


def test_missing_artifact_recovery_stamps_provenance_and_preserves_quality(tmp_path: Path, monkeypatch) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)
    workspace = tmp_path / "workspace"
    mod.WORKSPACE = workspace
    mod.ARTIFACTS_DIR = workspace / "reports" / "artifacts"
    mod.SCRIPTS_DIR = tmp_path / "scripts"
    mod.PROFILE_ROOT = tmp_path

    manifest_path = workspace / "reports" / "artifacts" / "manifest-M999.json"
    report_path = workspace / "reports" / "match" / "M999.md"
    crossbook_path = workspace / "reports" / "artifacts" / "crossbook-M999.json"
    write_json(crossbook_path, {"artifact_type": "crossbook_scan", "summary": {"quotes_scanned": 1}})
    write_json(
        manifest_path,
        {
            "manifest_id": "manifest:m999",
            "match_id": "M999",
            "final_status": "pass_incomplete",
            "source_quality": "B",
            "source_quality_cap": "C",
            "actionable_allowed": False,
            "report_completeness": "partial",
            "report_path": str(report_path),
            "analysis_gates": {"role_engine": "missing", "mechanism_audit": "missing"},
            "artifacts": [
                {
                    "artifact_id": "crossbook:m999",
                    "artifact_type": "crossbook_scan",
                    "script": "cross_book_scan.py",
                    "path": str(crossbook_path),
                    "provides": ["path_a_crossbook"],
                }
            ],
        },
    )
    report_path.parent.mkdir(parents=True)
    report_path.write_text("WC26 M999 report\n", encoding="utf-8")

    def fake_cmd(args, timeout=60):
        joined = " ".join(str(item) for item in args)
        if "role_engine.py" in joined:
            output = Path(args[args.index("--output") + 1])
            write_json(output, {"artifact_type": "role_engine", "role_conclusions": []})
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"].append(
                {"artifact_id": "role:m999", "artifact_type": "role_engine", "script": "role_engine.py", "path": str(output), "provides": ["role_engine"]}
            )
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, "role ok", "")
        if "mechanism_audit.py" in joined:
            output = Path(args[args.index("--output") + 1])
            write_json(output, {"artifact_type": "mechanism_audit", "mechanism_audit_status": "pass_incomplete"})
            return subprocess.CompletedProcess(args, 0, "mechanism ok", "")
        if "rich_summary.py" in joined:
            return subprocess.CompletedProcess(args, 0, "SUMMARY", "")
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(mod, "cmd_run", fake_cmd)

    result = mod.recover_missing_artifacts(
        {"manifest_path": str(manifest_path), "report_path": str(report_path)},
        "br:test",
    )

    assert result["status"] == "recovered"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["final_status"] == "pass_incomplete"
    assert manifest["source_quality_cap"] == "C"
    assert manifest["actionable_allowed"] is False
    assert manifest["recovery_provenance"][0]["generated_by"] == "blocked_recovery"
    mechanism_entry = next(item for item in manifest["artifacts"] if "mechanism_audit" in item.get("provides", []))
    mechanism_payload = json.loads(Path(mechanism_entry["path"]).read_text(encoding="utf-8"))
    assert mechanism_payload["generated_by"] == "blocked_recovery"


def test_missing_artifact_recovery_generates_path_c_before_role_engine(tmp_path: Path, monkeypatch) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)
    workspace = tmp_path / "workspace"
    mod.WORKSPACE = workspace
    mod.ARTIFACTS_DIR = workspace / "reports" / "artifacts"
    mod.SCRIPTS_DIR = tmp_path / "scripts"
    mod.PROFILE_ROOT = tmp_path

    snapshot = write_json(workspace / "snapshots" / "odds" / "snapshot-M123.json", {"data": []})
    manifest_path = workspace / "reports" / "artifacts" / "manifest-M123.json"
    report_path = workspace / "reports" / "match" / "M123.md"
    crossbook_path = workspace / "reports" / "artifacts" / "crossbook-M123.json"
    write_json(crossbook_path, {"artifact_type": "crossbook_scan", "summary": {"quotes_scanned": 1}})
    write_json(
        manifest_path,
        {
            "manifest_id": "manifest:m123",
            "match_id": "M123",
            "home": "Haiti",
            "away": "Scotland",
            "final_status": "watch",
            "source_quality": "B",
            "source_quality_cap": "C",
            "actionable_allowed": False,
            "report_completeness": "partial",
            "report_path": str(report_path),
            "snapshot_id": snapshot.name,
            "analysis_gates": {
                "path_c_consistency": {"status": "skipped_missing_source"},
                "role_engine": "missing",
                "mechanism_audit": "missing",
            },
            "skipped_sections": [{"gate": "path_c_consistency", "reason": "missing"}],
            "artifacts": [
                {
                    "artifact_id": "crossbook:m123",
                    "artifact_type": "crossbook_scan",
                    "script": "cross_book_scan.py",
                    "path": str(crossbook_path),
                    "source_snapshot_id": snapshot.name,
                    "provides": ["path_a_crossbook"],
                }
            ],
        },
    )
    report_path.parent.mkdir(parents=True)
    report_path.write_text("WC26 M123 report\n", encoding="utf-8")

    def fake_cmd(args, timeout=60):
        joined = " ".join(str(item) for item in args)
        if "consistency_triangle.py" in joined:
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    {
                        "match": "Haiti vs Scotland",
                        "signal": {"type": None, "strength": "无"},
                        "discrepancy": {"pp": 0.4},
                        "market_profile": {
                            "contract": "wc26.market_profile.v1",
                            "status": "ok",
                            "confidence": "high",
                        },
                    }
                ),
                "",
            )
        if "role_engine.py" in joined:
            current = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert any("path_c_consistency" in item.get("provides", []) for item in current["artifacts"])
            output = Path(args[args.index("--output") + 1])
            write_json(output, {"artifact_type": "role_engine", "telegram_bullets_zh": []})
            current["artifacts"].append(
                {"artifact_id": "role:m123", "artifact_type": "role_engine", "script": "role_engine.py", "path": str(output), "provides": ["role_engine"]}
            )
            manifest_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, "role ok", "")
        if "mechanism_audit.py" in joined:
            output = Path(args[args.index("--output") + 1])
            write_json(output, {"artifact_type": "mechanism_audit", "mechanism_audit_status": "complete"})
            return subprocess.CompletedProcess(args, 0, "mechanism ok", "")
        if "rich_summary.py" in joined:
            return subprocess.CompletedProcess(args, 0, "SUMMARY WITH MARKET PROFILE", "")
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(mod, "cmd_run", fake_cmd)
    monkeypatch.setattr(mod, "validate_manifest", lambda manifest, report: (True, "ok"))

    result = mod.recover_missing_artifacts(
        {"manifest_path": str(manifest_path), "report_path": str(report_path)},
        "br:pathc",
    )

    assert result["status"] == "recovered"
    assert "generate_path_c_consistency" in result["actions"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["analysis_gates"]["path_c_consistency"]["status"] == "pass"
    assert not any(item.get("gate") == "path_c_consistency" for item in manifest.get("skipped_sections", []))
    path_c_entry = next(item for item in manifest["artifacts"] if "path_c_consistency" in item.get("provides", []))
    path_c_payload = json.loads(Path(path_c_entry["path"]).read_text(encoding="utf-8"))
    assert path_c_payload["generated_by"] == "blocked_recovery"
    assert path_c_payload["market_profile"]["contract"] == "wc26.market_profile.v1"


def test_missing_artifact_recovery_rolls_back_on_contract_failure(tmp_path: Path, monkeypatch) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)
    workspace = tmp_path / "workspace"
    mod.WORKSPACE = workspace
    mod.ARTIFACTS_DIR = workspace / "reports" / "artifacts"
    mod.SCRIPTS_DIR = tmp_path / "scripts"

    manifest_path = workspace / "reports" / "artifacts" / "manifest-M998.json"
    report_path = workspace / "reports" / "match" / "M998.md"
    original_manifest = {
        "manifest_id": "manifest:m998",
        "match_id": "M998",
        "final_status": "pass_incomplete",
        "source_quality_cap": "C",
        "actionable_allowed": False,
        "report_path": str(report_path),
        "analysis_gates": {"role_engine": "missing", "mechanism_audit": "missing"},
        "artifacts": [],
    }
    write_json(manifest_path, original_manifest)
    report_path.parent.mkdir(parents=True)
    report_path.write_text("ORIGINAL REPORT\n", encoding="utf-8")

    def fake_cmd(args, timeout=60):
        joined = " ".join(str(item) for item in args)
        if "role_engine.py" in joined:
            output = Path(args[args.index("--output") + 1])
            write_json(output, {"artifact_type": "role_engine", "role_conclusions": []})
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"].append(
                {"artifact_id": "role:m998", "artifact_type": "role_engine", "script": "role_engine.py", "path": str(output), "provides": ["role_engine"]}
            )
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            report_path.write_text("PATCHED REPORT\n", encoding="utf-8")
        if "mechanism_audit.py" in joined:
            output = Path(args[args.index("--output") + 1])
            write_json(output, {"artifact_type": "mechanism_audit"})
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(mod, "cmd_run", fake_cmd)
    monkeypatch.setattr(mod, "validate_manifest", lambda manifest, report: (False, "contract failed"))

    result = mod.recover_missing_artifacts(
        {"manifest_path": str(manifest_path), "report_path": str(report_path)},
        "br:rollback",
    )

    assert result["status"] == "failed_contract"
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == original_manifest
    assert report_path.read_text(encoding="utf-8") == "ORIGINAL REPORT\n"


def test_missing_guarded_report_requires_exact_window(tmp_path: Path) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)
    workspace = tmp_path / "workspace"
    mod.WORKSPACE = workspace
    mod.ARTIFACTS_DIR = workspace / "reports" / "artifacts"
    write_json(mod.ARTIFACTS_DIR / "manifest-M777.json", {"match_id": "M777", "final_status": "pass"})

    assert mod.find_latest_manifest("M777", "T-72h_early") is None


def test_existing_guarded_report_with_missing_path_c_is_repaired(tmp_path: Path, monkeypatch) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)
    workspace = tmp_path / "workspace"
    mod.WORKSPACE = workspace
    mod.ARTIFACTS_DIR = workspace / "reports" / "artifacts"

    manifest_path = workspace / "reports" / "artifacts" / "manifest-M124.json"
    report_path = workspace / "reports" / "match" / "M124.md"
    write_json(
        manifest_path,
        {
            "match_id": "M124",
            "home": "Haiti",
            "away": "Scotland",
            "report_path": str(report_path),
            "analysis_gates": {"path_c_consistency": {"status": "skipped_missing_source"}},
            "artifacts": [{"artifact_id": "role:m124", "artifact_type": "role_engine", "provides": ["role_engine"]}],
        },
    )
    report_path.parent.mkdir(parents=True)
    report_path.write_text("WC26 M124 report\n", encoding="utf-8")
    called = {}

    def fake_recover(event, recovery_id):
        called["event"] = event
        called["recovery_id"] = recovery_id
        return {"status": "recovered", "summary": "SUMMARY"}

    monkeypatch.setattr(mod, "recover_missing_artifacts", fake_recover)

    result = mod.recover_missing_guarded_report({"match_id": "M124", "reason": "missing Path C"})

    assert result["status"] == "recovered"
    assert called["event"]["manifest_path"] == str(manifest_path)
    assert called["event"]["report_path"] == str(report_path)


def test_existing_guarded_report_with_path_c_stub_is_repaired(tmp_path: Path, monkeypatch) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)
    workspace = tmp_path / "workspace"
    mod.WORKSPACE = workspace
    mod.ARTIFACTS_DIR = workspace / "reports" / "artifacts"

    manifest_path = workspace / "reports" / "artifacts" / "manifest-M125.json"
    report_path = workspace / "reports" / "match" / "M125.md"
    path_c = write_json(workspace / "reports" / "artifacts" / "consistency-M125.json", {"status": "no_signal"})
    write_json(
        manifest_path,
        {
            "match_id": "M125",
            "home": "Haiti",
            "away": "Scotland",
            "report_path": str(report_path),
            "analysis_gates": {"path_c_consistency": {"status": "pass"}},
            "artifacts": [
                {"artifact_id": "pathc:m125", "artifact_type": "consistency_triangle", "path": str(path_c), "provides": ["path_c_consistency"]},
                {"artifact_id": "role:m125", "artifact_type": "role_engine", "provides": ["role_engine"]},
                {"artifact_id": "mechanism:m125", "artifact_type": "mechanism_audit", "provides": ["mechanism_audit"]},
            ],
        },
    )
    report_path.parent.mkdir(parents=True)
    report_path.write_text("WC26 M125 report\n", encoding="utf-8")
    called = {}

    def fake_recover(event, recovery_id):
        called["event"] = event
        return {"status": "recovered", "summary": "SUMMARY"}

    monkeypatch.setattr(mod, "recover_missing_artifacts", fake_recover)

    result = mod.recover_missing_guarded_report({"match_id": "M125", "reason": "Path C stub missing market profile"})

    assert result["status"] == "recovered"
    assert called["event"]["manifest_path"] == str(manifest_path)


def test_snapshot_candidates_include_existing_path_c_payload_snapshot_path(tmp_path: Path) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)
    workspace = tmp_path / "workspace"
    mod.WORKSPACE = workspace
    snapshot = write_json(workspace / "snapshots" / "odds" / "snapshot-M126.json", {"data": []})
    path_c = write_json(
        workspace / "reports" / "artifacts" / "consistency-M126.json",
        {"artifact_type": "consistency_triangle", "snapshot_path": str(snapshot)},
    )
    manifest = {
        "match_id": "M126",
        "artifacts": [
            {
                "artifact_id": "pathc:m126",
                "artifact_type": "consistency_triangle",
                "path": str(path_c),
                "provides": ["path_c_consistency"],
            }
        ],
    }

    candidates = mod.manifest_snapshot_candidates(manifest)

    assert str(snapshot) in {str(item) for item in candidates}
    assert mod.source_snapshot_path(str(snapshot)) == snapshot


def test_terminal_event_is_archived_after_processing(tmp_path: Path) -> None:
    mod = load_module("blocked_recovery_test", SCRIPT_PATH)
    workspace = tmp_path / "workspace"
    mod.WORKSPACE = workspace
    mod.QUEUE_DIR = workspace / "blocked_recovery" / "queue"
    mod.ARCHIVE_DIR = workspace / "blocked_recovery" / "archive"
    mod.STATE_PATH = workspace / "state" / "blocked-recovery.json"
    event = {
        "recovery_id": "br:archive-test",
        "category": "safety_block",
        "reason": "freeform report blocked",
    }
    event_path = mod.enqueue_event(workspace, event)
    state = mod.load_state()
    result = mod.process_event(event_path, state)
    mod.archive_event_file(event_path, result["status"])

    assert not event_path.exists()
    assert (workspace / "blocked_recovery" / "archive" / "manual_required" / event_path.name).exists()


def test_cli_workspace_uses_workspace_queue_by_default(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    queue = workspace / "blocked_recovery" / "queue"
    queue.mkdir(parents=True)
    (queue / "br-cli.json").write_text(
        json.dumps(
            {
                "recovery_id": "br:cli",
                "category": "safety_block",
                "reason": "freeform report blocked",
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--workspace", str(workspace), "--json"],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["results"][0]["recovery_id"] == "br:cli"
    assert not (queue / "br-cli.json").exists()
    assert (workspace / "blocked_recovery" / "archive" / "manual_required" / "br-cli.json").exists()
