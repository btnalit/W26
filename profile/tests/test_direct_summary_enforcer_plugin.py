"""Tests for the WC26 direct Telegram summary enforcer plugin."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


PLUGIN_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "wc26-direct-summary-enforcer"
    / "__init__.py"
)
if not PLUGIN_PATH.exists():
    PLUGIN_PATH = Path(__file__).resolve().parent / "enforcer.py"


def load_plugin(monkeypatch, tmp_path: Path):
    home = tmp_path / "profile"
    workspace = tmp_path / "workspace"
    script_dir = home / "skills" / "odds-analysis" / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "direct_summary.py").write_text(
        "import argparse\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--manifest', required=True)\n"
        "p.add_argument('--report')\n"
        "p.add_argument('--max-chars')\n"
        "a=p.parse_args()\n"
        "print('CANONICAL SUMMARY')\n"
        "print('manifest=' + a.manifest)\n"
        "print('report=' + str(a.report))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("WC26_WORKSPACE", str(workspace))
    spec = importlib.util.spec_from_file_location("wc26_direct_summary_enforcer_test", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, home, workspace


def test_replaces_telegram_reply_when_manifest_path_is_present(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M010.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"match_id": "M010"}), encoding="utf-8")

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s1",
        response_text=f"自由摘要\nManifest: {manifest}",
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert f"manifest={manifest}" in result


def test_resolves_manifest_from_report_header(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M009.json"
    report = workspace / "reports" / "match" / "M009.md"
    manifest.parent.mkdir(parents=True)
    report.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"match_id": "M009"}), encoding="utf-8")
    report.write_text(f"---\nartifact_manifest_path: {manifest}\n---\n", encoding="utf-8")

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s1",
        response_text=f"完整报告: {report}",
    )

    assert result is not None
    assert f"manifest={manifest}" in result
    assert f"report={report}" in result


def test_relative_manifest_path_uses_workspace(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M008.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s1",
        response_text="Manifest: reports/artifacts/manifest-M008.json",
    )

    assert result is not None
    assert f"manifest={manifest}" in result


def test_resolves_manifest_from_direct_request_id(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M009.json"
    report = workspace / "reports" / "match" / "M009.md"
    request = workspace / "direct_requests" / "2026-06-05" / "direct-abc123.json"
    manifest.parent.mkdir(parents=True)
    report.parent.mkdir(parents=True)
    request.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"match_id": "M009"}), encoding="utf-8")
    report.write_text(f"artifact_manifest_path: {manifest}\n", encoding="utf-8")
    request.write_text(
        json.dumps({"direct_request_id": "direct:abc123", "manifest_path": str(manifest), "report_path": str(report)}),
        encoding="utf-8",
    )

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s1",
        response_text="赛后 CLV 回链: direct:abc123 — 报告已绑定",
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert f"manifest={manifest}" in result
    assert f"report={report}" in result


def test_blocks_wc26_report_when_direct_request_has_empty_paths(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    request = workspace / "direct_requests" / "2026-06-06" / "direct-empty123.json"
    request.parent.mkdir(parents=True)
    request.write_text(
        json.dumps({"direct_request_id": "direct:empty123", "manifest_path": "", "report_path": ""}),
        encoding="utf-8",
    )

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s-empty",
        response_text=(
            "WC26 M010 Netherlands vs Japan — WATCH\n"
            "direct_request: direct:empty123\n"
            "Path A 跨书商扫描\n"
            "WC26_DEEP_RESEARCH_FINALIZER: completed"
        ),
    )

    assert result is not None
    assert result.startswith("BLOCKED — WC26 Telegram report missing artifact manifest/report binding")
    assert "deterministic Telegram summary failed" not in result
    queued = list((workspace / "blocked_recovery" / "queue").glob("*.json"))
    assert len(queued) == 1
    event = json.loads(queued[0].read_text(encoding="utf-8"))
    assert event["category"] == "safety_block"
    assert event["direct_request_ids"] == ["direct:empty123"]


def test_direct_request_with_bad_manifest_falls_back_to_latest_match_manifest(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M010-20260605.json"
    report = workspace / "reports" / "match" / "M010.md"
    numeric = workspace / "reports" / "artifacts" / "numeric-M010-20260606.json"
    request = workspace / "direct_requests" / "2026-06-06" / "direct-badpath.json"
    manifest.parent.mkdir(parents=True)
    report.parent.mkdir(parents=True)
    request.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"manifest_id": "m010", "match_id": "M010", "report_path": str(report), "artifacts": []}), encoding="utf-8")
    report.write_text(f"artifact_manifest_path: {manifest}\n", encoding="utf-8")
    numeric.write_text("{not json", encoding="utf-8")
    request.write_text(
        json.dumps(
            {
                "direct_request_id": "direct:badpath",
                "match_id": "M010",
                "manifest_path": str(numeric),
                "report_path": str(numeric),
            }
        ),
        encoding="utf-8",
    )

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s-badpath",
        response_text="WC26 M010 Netherlands vs Japan — WATCH\n赛后回链: direct:badpath",
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert f"manifest={manifest}" in result
    assert f"report={report}" in result
    assert "deterministic Telegram summary failed" not in result


def test_report_like_text_falls_back_to_latest_match_manifest(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M123-legacy-recovered.json"
    report = workspace / "reports" / "match" / "M123.md"
    manifest.parent.mkdir(parents=True)
    report.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"manifest_id": "m123", "match_id": "M123", "report_path": str(report), "artifacts": []}),
        encoding="utf-8",
    )
    report.write_text(f"artifact_manifest_path: {manifest}\n", encoding="utf-8")

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s-match-fallback",
        response_text="WC26 M123 Example vs Sample — WATCH\nPath A 跨书商扫描\nreport_contract PASS",
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert f"manifest={manifest}" in result
    assert f"report={report}" in result


def test_report_like_tail_uses_unique_recent_direct_context(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M007-legacy-recovered.json"
    report = workspace / "reports" / "match" / "M007.md"
    request = workspace / "direct_requests" / "2026-06-06" / "direct-context.json"
    manifest.parent.mkdir(parents=True)
    report.parent.mkdir(parents=True)
    request.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"manifest_id": "m007", "match_id": "M007", "report_path": str(report), "artifacts": []}),
        encoding="utf-8",
    )
    report.write_text(f"artifact_manifest_path: {manifest}\n", encoding="utf-8")
    request.write_text(
        json.dumps(
            {
                "direct_request_id": "direct:context",
                "status": "completed_cached",
                "match_id": "HAI_vs_SCO",
                "match_label": "海地 vs 苏格兰",
                "request_text": "分析 海地 vs 苏格兰",
                "manifest_path": str(manifest),
                "report_path": str(report),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s-context",
        response_text=(
            "分析完毕。以上是海地 vs 苏格兰的完整盘口分析。\n"
            "核心结论: WATCH / NO PLAY。Deep Research 发现历史趋势略倾向受让方。"
        ),
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert f"manifest={manifest}" in result
    assert f"report={report}" in result
    queued = list((workspace / "blocked_recovery" / "queue").glob("*.json"))
    assert queued == []


def test_report_like_tail_uses_newest_recent_direct_for_same_match(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    request_dir = workspace / "direct_requests" / "2026-06-06"
    request_dir.mkdir(parents=True)
    manifests = []
    reports = []
    for suffix in ("old", "new"):
        manifest = workspace / "reports" / "artifacts" / f"manifest-M007-direct-{suffix}.json"
        report = workspace / "reports" / "match" / f"M007-direct-{suffix}.md"
        request = request_dir / f"direct-{suffix}.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({"manifest_id": f"m007-{suffix}", "match_id": "M007", "report_path": str(report), "artifacts": []}),
            encoding="utf-8",
        )
        report.write_text(f"artifact_manifest_path: {manifest}\n", encoding="utf-8")
        request.write_text(
            json.dumps(
                {
                    "direct_request_id": f"direct:{suffix}",
                    "status": "completed_cached",
                    "match_id": "M007",
                    "match_label": "海地 vs 苏格兰",
                    "request_text": "分析 海地 vs 苏格兰",
                    "manifest_path": str(manifest),
                    "report_path": str(report),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manifests.append(manifest)
        reports.append(report)

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s-context-repeat",
        response_text="分析完毕。以上是海地 vs 苏格兰的完整盘口分析。核心结论: WATCH / NO PLAY。",
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert f"manifest={manifests[-1]}" in result
    assert f"report={reports[-1]}" in result


def test_ignores_directory_manifest_token_instead_of_running_summary(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest_dir = workspace / "reports" / "artifacts" / "manifest-M010.json"
    manifest_dir.mkdir(parents=True)

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s-dir",
        response_text=f"WC26 M010 Netherlands vs Japan\nManifest: {manifest_dir}\nPath A 跨书商扫描",
    )

    assert result is not None
    assert result.startswith("BLOCKED — WC26 Telegram report missing artifact manifest/report binding")
    assert "deterministic Telegram summary failed" not in result


def test_appends_deep_research_finalizer_section(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M009.json"
    artifact = workspace / "reports" / "artifacts" / "deep-research-M009.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"match_id": "M009"}), encoding="utf-8")
    artifact.write_text(json.dumps({"artifact_type": "deep_research"}), encoding="utf-8")

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s1",
        response_text=(
            f"Manifest: {manifest}\n\n"
            "WC26_DEEP_RESEARCH_FINALIZER: completed\n"
            f"Deep Research: {artifact}\n"
            "## Exa × Jina 深度研究\n"
            "- [DR-A1] 研究倾向: 观察受让方 +3.5。\n"
            "- 当前动作: 不下注, 等 T-72h。"
        ),
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert "WC26_DEEP_RESEARCH_FINALIZER: completed" in result
    assert "Exa × Jina 深度研究" in result


def test_auto_appends_cached_deep_research_artifact_for_report_like_reply(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M011.json"
    artifact = workspace / "reports" / "artifacts" / "deep-research-M011-20260606T120000Z.json"
    path_c = workspace / "reports" / "artifacts" / "consistency-M011.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "match_id": "M011",
                "artifacts": [
                    {
                        "artifact_type": "consistency_triangle",
                        "provides": ["path_c_consistency"],
                        "path": str(path_c),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path_c.write_text(
        json.dumps(
            {
                "artifact_type": "consistency_triangle",
                "market_profile": {
                    "status": "ok",
                    "confidence": "high",
                    "fit": {"max_abs_residual_pp": 1.2},
                    "most_likely_1x2": {"label": "Team A胜", "prob_pct": 49.3},
                    "total_line_lean": {"lean": "under", "label": "Under 2.5", "under_pct": 54.9},
                    "top_scores": [
                        {"score": "1-1", "prob_pct": 12.6},
                        {"score": "1-0", "prob_pct": 12.3},
                        {"score": "2-0", "prob_pct": 9.5},
                    ],
                    "top_margin": {"label": "平局 净0", "prob_pct": 26.7},
                    "btts": {"lean": "no", "no_pct": 51.2},
                },
            }
        ),
        encoding="utf-8",
    )
    artifact.write_text(
        json.dumps(
            {
                "artifact_type": "deep_research",
                "artifact_version": "1.2",
                "match_id": "M011",
                "final_view": {
                    "direction_label_zh": "研究倾向: 观察受让方",
                    "action": "NO BET / WATCH",
                    "why": "主报告无 actionable edge，研究层只给观察方向。",
                    "upgrade_triggers": ["价格升到 2.10+"],
                    "falsifiers": ["关键球员缺阵"],
                },
            }
        ),
        encoding="utf-8",
    )

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s-cached-deep",
        response_text=f"WC26 M011 Team A vs Team B — WATCH\nPath A 跨书商扫描\nManifest: {manifest}",
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert "WC26_DEEP_RESEARCH_FINALIZER: completed" in result
    assert "研究倾向: 观察受让方" in result
    assert "市场画像（Path C 描述性，不是下注信号）" in result
    assert "胜平负最可能: Team A胜 49.3%" in result
    assert "最可能比分: 1-1 12.6%, 1-0 12.3%, 2-0 9.5%" in result
    assert f"Deep Research: {artifact}" in result


def test_incoming_unbound_deep_research_falls_back_to_cached_artifact(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M014.json"
    artifact = workspace / "reports" / "artifacts" / "deep-research-M014-20260606T120000Z.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"match_id": "M014"}), encoding="utf-8")
    artifact.write_text(
        json.dumps(
            {
                "artifact_type": "deep_research",
                "artifact_version": "1.2",
                "match_id": "M014",
                "final_view": {
                    "direction_label_zh": "研究倾向: 观察受让方",
                    "action": "NO BET / WATCH",
                    "why": "cached artifact-backed research is allowed.",
                },
            }
        ),
        encoding="utf-8",
    )

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s-unbound-deep-fallback",
        response_text=(
            f"WC26 M014 Team A vs Team B — WATCH\nPath A 跨书商扫描\nManifest: {manifest}\n\n"
            "WC26_DEEP_RESEARCH_FINALIZER: completed\n"
            "这段没有 Deep Research artifact 路径，不能直接放行。"
        ),
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert "WC26_DEEP_RESEARCH_FINALIZER: completed" in result
    assert "研究倾向: 观察受让方" in result
    assert f"Deep Research: {artifact}" in result
    assert "这段没有 Deep Research artifact 路径" not in result


def test_cached_deep_research_keeps_artifact_path_when_section_is_long(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin, "TOTAL_MAX_CHARS", 900)
    monkeypatch.setattr(plugin, "BASE_MAX_CHARS_WITH_DEEP_RESEARCH", "420")
    manifest = workspace / "reports" / "artifacts" / "manifest-M015.json"
    artifact = workspace / "reports" / "artifacts" / "deep-research-M015-20260606T120000Z.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"match_id": "M015"}), encoding="utf-8")
    artifact.write_text(
        json.dumps(
            {
                "artifact_type": "deep_research",
                "artifact_version": "1.2",
                "match_id": "M015",
                "final_view": {
                    "direction_label_zh": "研究倾向: 观察受让方",
                    "action": "NO BET / WATCH",
                    "why": "长文本。" * 300,
                },
            }
        ),
        encoding="utf-8",
    )

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s-long-cached-deep",
        response_text=f"WC26 M015 Team A vs Team B — WATCH\nPath A 跨书商扫描\nManifest: {manifest}",
    )

    assert result is not None
    assert f"📁 Deep Research: {artifact}" in result
    assert result.index(f"📁 Deep Research: {artifact}") < result.index("研究倾向")


def test_report_like_reply_marks_deep_research_failed_when_no_artifact_exists(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M012.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"match_id": "M012"}), encoding="utf-8")

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s-missing-deep",
        response_text=f"WC26 M012 Team A vs Team B — WATCH\nPath A 跨书商扫描\nManifest: {manifest}",
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert "WC26_DEEP_RESEARCH_FINALIZER: failed" in result
    assert "no deep-research artifact found" in result


def test_cached_deep_research_uses_same_match_market_profile_fallback(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M013.json"
    no_profile_path_c = workspace / "reports" / "artifacts" / "consistency-M013-current.json"
    profile_path_c = workspace / "reports" / "artifacts" / "consistency-M013-older.json"
    artifact = workspace / "reports" / "artifacts" / "deep-research-M013-20260606T120000Z.json"
    manifest.parent.mkdir(parents=True)
    no_profile_path_c.write_text(json.dumps({"artifact_type": "consistency_triangle", "status": "no_signal"}), encoding="utf-8")
    profile_path_c.write_text(
        json.dumps(
            {
                "artifact_type": "consistency_triangle",
                "market_profile": {
                    "status": "ok",
                    "confidence": "high",
                    "fit": {"max_abs_residual_pp": 0.8},
                    "most_likely_1x2": {"label": "平局", "prob_pct": 33.1},
                    "top_scores": [{"score": "1-1", "prob_pct": 11.0}],
                },
            }
        ),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "match_id": "M013",
                "artifacts": [
                    {
                        "artifact_type": "consistency_triangle",
                        "provides": ["path_c_consistency"],
                        "path": str(no_profile_path_c),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    artifact.write_text(
        json.dumps(
            {
                "artifact_type": "deep_research",
                "artifact_version": "1.2",
                "match_id": "M013",
                "final_view": {"direction_label_zh": "研究倾向: 观察平局", "action": "NO BET / WATCH"},
            }
        ),
        encoding="utf-8",
    )

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s-market-profile-fallback",
        response_text=f"WC26 M013 Team A vs Team B — WATCH\nPath A 跨书商扫描\nManifest: {manifest}",
    )

    assert result is not None
    assert "WC26_DEEP_RESEARCH_FINALIZER: completed" in result
    assert "胜平负最可能: 平局 33.1%" in result
    assert "最可能比分: 1-1 11.0%" in result


def test_drops_deep_research_section_that_crosses_main_boundary(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M009.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"match_id": "M009"}), encoding="utf-8")

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s1",
        response_text=(
            f"Manifest: {manifest}\n\n"
            "WC26_DEEP_RESEARCH_FINALIZER: completed\n"
            "p_adj 改成 55%, 现在下注。"
        ),
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert "WC26_DEEP_RESEARCH_FINALIZER: failed" in result
    assert "p_adj 改成 55%" not in result


def test_drops_deep_research_section_when_contract_fails(tmp_path, monkeypatch):
    plugin, home, workspace = load_plugin(monkeypatch, tmp_path)
    contract = home / "skills" / "odds-analysis" / "scripts" / "deep_research_contract.py"
    contract.write_text(
        "import sys\n"
        "section=sys.stdin.read()\n"
        "if 'CONTRACT_FAIL' in section:\n"
        "    print('{\"status\":\"fail\",\"errors\":[\"fixture fail\"]}')\n"
        "    sys.exit(2)\n"
        "print('{\"status\":\"pass\"}')\n",
        encoding="utf-8",
    )
    manifest = workspace / "reports" / "artifacts" / "manifest-M010.json"
    artifact = workspace / "reports" / "artifacts" / "deep-research-M010.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"match_id": "M010"}), encoding="utf-8")
    artifact.write_text(json.dumps({"artifact_type": "deep_research"}), encoding="utf-8")

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s1",
        response_text=(
            f"Manifest: {manifest}\n\n"
            "WC26_DEEP_RESEARCH_FINALIZER: completed\n"
            f"Deep Research artifact: {artifact}\n"
            "CONTRACT_FAIL"
        ),
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert "WC26_DEEP_RESEARCH_FINALIZER: failed" in result
    assert "CONTRACT_FAIL" not in result


def test_sanitizes_deep_research_when_only_some_findings_fail_contract(tmp_path, monkeypatch):
    plugin, home, workspace = load_plugin(monkeypatch, tmp_path)
    contract = home / "skills" / "odds-analysis" / "scripts" / "deep_research_contract.py"
    contract.write_text(
        "import json, sys\n"
        "section=sys.stdin.read()\n"
        "if 'DR-C1' in section or '旧快照' in section:\n"
        "    print(json.dumps({\n"
        "      'status':'fail',\n"
        "      'has_risky_freshness_claim': True,\n"
        "      'normalized_sources': [\n"
        "        {'source_id':'DR-C1','source_class':'squad_news','pricing_freshness':'unknown'},\n"
        "        {'source_id':'DR-A1','source_class':'historical_context','pricing_freshness':'unknown'}\n"
        "      ]\n"
        "    }))\n"
        "    sys.exit(2)\n"
        "print(json.dumps({'status':'pass'}))\n",
        encoding="utf-8",
    )
    manifest = workspace / "reports" / "artifacts" / "manifest-M010.json"
    artifact = workspace / "reports" / "artifacts" / "deep-research-M010.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"match_id": "M010"}), encoding="utf-8")
    artifact.write_text(json.dumps({"artifact_type": "deep_research"}), encoding="utf-8")

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s1",
        response_text=(
            f"Manifest: {manifest}\n\n"
            "WC26_DEEP_RESEARCH_FINALIZER: completed\n"
            f"Deep Research: {artifact}\n"
            "- [DR-A1] 历史样本支持等待更好价格。\n"
            "- [DR-C1] 三笘缺阵，旧快照在官宣前，市场可能尚未消化。\n"
            "当前动作: 不下注。"
        ),
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert "WC26_DEEP_RESEARCH_FINALIZER: completed" in result
    assert "Deep Research 过滤" in result
    assert "[DR-A1] 历史样本" in result
    assert "[DR-C1]" not in result
    assert "旧快照" not in result


def test_drops_deep_research_freshness_claim_when_contract_missing(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M010.json"
    artifact = workspace / "reports" / "artifacts" / "deep-research-M010.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"match_id": "M010"}), encoding="utf-8")
    artifact.write_text(json.dumps({"artifact_type": "deep_research"}), encoding="utf-8")

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s1",
        response_text=(
            f"Manifest: {manifest}\n\n"
            "WC26_DEEP_RESEARCH_FINALIZER: completed\n"
            f"Deep Research: {artifact}\n"
            "旧快照在官宣前，市场可能尚未消化。"
        ),
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert "WC26_DEEP_RESEARCH_FINALIZER: failed" in result
    assert "旧快照在官宣前" not in result
    assert "市场可能尚未消化" not in result


def test_drops_completed_deep_research_without_artifact_path(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)
    manifest = workspace / "reports" / "artifacts" / "manifest-M010.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"match_id": "M010"}), encoding="utf-8")

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s1",
        response_text=(
            f"Manifest: {manifest}\n\n"
            "WC26_DEEP_RESEARCH_FINALIZER: completed\n"
            "研究倾向: 观察日本方向，但不下注。"
        ),
    )

    assert result is not None
    assert result.startswith("CANONICAL SUMMARY")
    assert "WC26_DEEP_RESEARCH_FINALIZER: failed" in result
    assert "研究倾向: 观察日本方向" not in result


def test_skips_non_telegram_or_missing_manifest(tmp_path, monkeypatch):
    plugin, _home, _workspace = load_plugin(monkeypatch, tmp_path)

    assert plugin.transform_llm_output(platform="cli", response_text="reports/artifacts/manifest-M010.json") is None
    assert plugin.transform_llm_output(platform="telegram", response_text="普通聊天") is None


def test_blocks_wc26_report_without_manifest_binding(tmp_path, monkeypatch):
    plugin, _home, workspace = load_plugin(monkeypatch, tmp_path)

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s-w007",
        response_text=(
            "WC26 M007 Haiti vs Scotland — PASS / NO PLAY\n\n"
            "② 盘口快照\n"
            "Pinnacle AH Scotland -1.0 @1.88\n\n"
            "⑦ AH -1.0 Settlement 分析\n"
            "EV(苏格兰-1.0) ≈ +23.5%"
        ),
    )

    assert result is not None
    assert result.startswith("BLOCKED")
    assert "missing artifact manifest" in result
    queued = list((workspace / "blocked_recovery" / "queue").glob("*.json"))
    assert len(queued) == 1
    event = json.loads(queued[0].read_text(encoding="utf-8"))
    assert event["category"] == "safety_block"
    assert event["source"] == "wc26-direct-summary-enforcer"
    assert event["session_id"] == "s-w007"


def test_returns_blocked_text_when_direct_summary_fails(tmp_path, monkeypatch):
    plugin, home, workspace = load_plugin(monkeypatch, tmp_path)
    (home / "skills" / "odds-analysis" / "scripts" / "direct_summary.py").write_text(
        "import sys\nprint('boom', file=sys.stderr)\nsys.exit(2)\n",
        encoding="utf-8",
    )
    manifest = workspace / "reports" / "artifacts" / "manifest-M010.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")

    result = plugin.transform_llm_output(
        platform="telegram",
        session_id="s1",
        response_text=f"Manifest: {manifest}",
    )

    assert result is not None
    assert result.startswith("BLOCKED")
    assert "boom" in result
