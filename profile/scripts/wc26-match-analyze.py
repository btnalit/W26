#!/usr/bin/env python3
"""
wc26-match-analyze.py — 比赛分析编排器

把 devig + crossbook + consistency_triangle + mechanism_audit + manifest
+ report + direct_summary 串成一个确定性入口。

用法:
  python3 wc26-match-analyze.py \\
    --snapshot snapshots/odds/the-odds-api-multibook-20260610T040051Z.json \\
    --match-home "Mexico" --match-away "South Africa" \\
    --match-id M001 [--window T-24h_confirm] [--timing-class confirmation] \\
    --output reports/artifacts \\
    [--mode full|fast]

  --mode full (默认): 完整链 (devig + crossbook + path_c + audit + manifest + report)
  --mode fast: 仅 crossbook (快速查看)

依赖: devig.py, cross_book_scan.py, consistency_triangle.py,
      mechanism_audit.py, direct_summary.py
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path("/hermesdata/worldcup-2026-handicap")
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_SCRIPTS = SCRIPT_DIR.parent / "skills" / "odds-analysis" / "scripts"
PYTHON = sys.executable
MATCH_REPORT_DIR = WORKSPACE / "reports" / "match"
ARTIFACT_DIR = WORKSPACE / "reports" / "artifacts"
TEMPLATE_PATH = SCRIPT_DIR.parent / "templates" / "report-template.md"
SANPSHOT_HEALTH_DIR = WORKSPACE / "snapshots" / "health"


# ── 工具 ──────────────────────────────────────────────────────────

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# ── 导入 devig.py 的标准去水函数 ──
_DEVIG_MODULE: Any = None

def _load_devig_module():
    global _DEVIG_MODULE
    if _DEVIG_MODULE is not None:
        return _DEVIG_MODULE
    devig_path = SKILL_SCRIPTS / "devig.py"
    if devig_path.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("_devig_module", str(devig_path))
        _DEVIG_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_DEVIG_MODULE)
        return _DEVIG_MODULE
    raise ImportError(f"devig.py not found at {devig_path}")


def get_devig_method(name: str):
    mod = _load_devig_module()
    return getattr(mod, name, None)


def normalize_book_key(raw: str | None) -> str:
    key = str(raw or "").strip().lower()
    if key.startswith("pinnacle"):
        return "pinnacle"
    if key.startswith("betfair"):
        return "betfair_ex"
    return key


def stable_id(seed: str) -> str:
    import hashlib
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


# ── 从 snapshot 提取 Pinnacle 赔率 ──────────────────────────────

def extract_match(snapshot_path: Path, match_home: str, match_away: str) -> dict[str, Any] | None:
    data = read_json(snapshot_path)
    if not data:
        return None
    matches = data if isinstance(data, list) else data.get("data", data) if isinstance(data, dict) else data
    for m in matches:
        if not isinstance(m, dict):
            continue
        if m.get("home_team", "").lower() == match_home.lower() and m.get("away_team", "").lower() == match_away.lower():
            return m
    return None


def extract_pinnacle(match: dict) -> dict[str, dict[str, Any]]:
    """从 match 提取 Pinnacle 的 h2h/spreads/totals 赔率."""
    result: dict[str, list[dict]] = {}
    for bm in match.get("bookmakers", []):
        if normalize_book_key(bm.get("key")) != "pinnacle":
            continue
        for mkt in bm.get("markets", []):
            mkey = mkt.get("key")
            if mkey not in ("h2h", "spreads", "totals"):
                continue
            outcomes: list[dict] = []
            for oc in mkt.get("outcomes", []):
                price = oc.get("price")
                if not isinstance(price, (int, float)) or price <= 1.01:
                    continue
                entry = {"name": oc["name"], "price": float(price)}
                if oc.get("point") is not None:
                    entry["point"] = oc["point"]
                outcomes.append(entry)
            if outcomes:
                result[mkey] = outcomes
    return result


# ── 生成 devig artifact ───────────────────────────────────────────

def generate_devig_artifact(
    match_id: str, market_key: str, outcomes: list[dict],
    captured_at: str, snapshot_id: str,
) -> dict[str, Any]:
    """从 Pinnacle outcomes 生成 devig artifact (使用 devig.py 规范去水)."""
    mod = _load_devig_module()
    odds = [o["price"] for o in outcomes]
    outcome_names = [o["name"] for o in outcomes]
    nv_shin = mod.devig_shin(odds)
    nv_mult = mod.devig_multiplicative(odds)
    nv_power = mod.devig_power(odds)
    overround = sum(1.0 / o for o in odds) - 1.0

    # methods_agree_on_favorite: all 3 methods agree on which outcome has highest prob
    shin_best = max(range(len(nv_shin)), key=lambda i: nv_shin[i])
    mult_best = max(range(len(nv_mult)), key=lambda i: nv_mult[i])
    power_best = max(range(len(nv_power)), key=lambda i: nv_power[i])
    methods_agree = (shin_best == mult_best == power_best)

    devig_methods = {
        "shin": [round(p, 6) for p in nv_shin],
        "multiplicative": [round(p, 6) for p in nv_mult],
        "power": [round(p, 6) for p in nv_power],
    }

    loc = "away"  # default for h2h
    line = None
    for o in outcomes:
        if o.get("point") is not None:
            line = o["point"]
            loc = "line"
            break

    return {
        "artifact_id": f"devig-{market_key}-{match_id}-{stable_id(captured_at)}",
        "artifact_type": "devig",
        "script": "wc26-match-analyze.py (via devig.py import)",
        "provides": ["no_vig"],
        "match_id": match_id,
        "market": market_key,
        "line": line,
        "bookmaker": "pinnacle",
        "snapshot_id": snapshot_id,
        "captured_at_utc": captured_at,
        "generated_at_utc": utc_now(),
        "decimal_odds": odds,
        "outcomes": outcome_names,
        "no_vig_probabilities": [round(p, 6) for p in nv_shin],
        "no_vig_multiplicative": [round(p, 6) for p in nv_mult],
        "no_vig_power": [round(p, 6) for p in nv_power],
        "devig_methods": devig_methods,
        "methods_agree_on_favorite": methods_agree,
        "overround": round(overround, 6),
        "devig_method": "shin",
    }


# ── 构建 manifest ────────────────────────────────────────────────

def build_manifest(
    match_id: str, home: str, away: str,
    window: str, timing_class: str,
    kickoff_utc: str,
    snapshot_path: str, snapshot_id: str,
    artifacts: list[dict],
    captured_at_utc: str = "",
) -> dict[str, Any]:
    manifest_id = f"manifest-{match_id}-{window}-{utc_now()}"
    # Compute real snapshot age if captured_at_utc is available
    snapshot_age = 0
    if captured_at_utc:
        try:
            captured_dt = datetime.fromisoformat(captured_at_utc.replace("Z", "+00:00")).astimezone(timezone.utc)
            snapshot_age = round((datetime.now(timezone.utc) - captured_dt).total_seconds() / 60)
        except Exception:
            pass
    return {
        "manifest_id": manifest_id,
        "workflow_contract": "wc26.direct_report.v1",
        "artifact_contract": "wc26.artifact_chain.v1",
        "mode": "live",
        "match_id": match_id,
        "match_label": f"{home} vs {away}",
        "home": home,
        "away": away,
        "kickoff_utc": kickoff_utc,
        "window": window,
        "timing_class": timing_class,
        "snapshot_path": snapshot_path,
        "snapshot_id": snapshot_id,
        "generated_at_utc": utc_now(),
        "source_quality": "B",
        "final_status": "watch",
        "artifacts": artifacts,
        "source_freshness": {
            "captured_at_utc": captured_at_utc or utc_now(),
            "snapshot_age_minutes": snapshot_age,
            "sources": [
                {"type": "the-odds-api", "path": snapshot_path, "id": snapshot_id}
            ]
        },
        "artifact_capabilities": [
            "devig_1x2", "path_a_crossbook", "asian_handicap",
            "totals", "path_c_consistency", "mechanism_audit"
        ],
        "analysis_gates": {},
    }


# ── 主流程 ────────────────────────────────────────────────────────

def run_orchestrator(
    snapshot_path: Path, match_home: str, match_away: str,
    match_id: str, window: str, timing_class: str,
    output_dir: Path, mode: str,
) -> int:
    # ── Read snapshot ──
    match = extract_match(snapshot_path, match_home, match_away)
    if not match:
        print(f"[match-analyze] 在 {snapshot_path.name} 中未找到 {match_home} vs {match_away}", file=sys.stderr)
        return 1

    snapshot_data = read_json(snapshot_path) or {}
    captured_at = snapshot_data.get("captured_at_utc") or match.get("captured_at_utc") or utc_now()
    snapshot_id = snapshot_path.name
    kickoff_utc = match.get("commence_time")
    if not kickoff_utc:
        print(f"[match-analyze]   错误: match 数据中无 kickoff_time (commence_time 缺失)", file=sys.stderr)
        return 1

    print(f"[match-analyze] {match_id}: {match_home} vs {match_away}")
    print(f"[match-analyze]   snapshot: {snapshot_path.name}")
    print(f"[match-analyze]   captured: {captured_at}")
    print(f"[match-analyze]   mode: {mode}")
    print(f"[match-analyze]   window: {window}")

    pinnacle = extract_pinnacle(match)
    if not pinnacle:
        print(f"[match-analyze]   WARN: Pinnacle 数据缺失", file=sys.stderr)

    artifacts: list[dict[str, Any]] = []
    health_note = ""

    # ── 读 snapshot health 做 fail-closed (按本场比赛 ERROR flag) ──
    health_path = SANPSHOT_HEALTH_DIR / f"{snapshot_path.stem}.health.json"
    match_health_status = "?"
    if health_path.exists():
        health = read_json(health_path, {})
        if isinstance(health, dict):
            # Check per-match ERROR flags
            this_match_has_error = False
            for hm in health.get("matches", []):
                if isinstance(hm, dict):
                    match_label = hm.get("match", "")
                    if match_home.lower() in match_label.lower() and match_away.lower() in match_label.lower():
                        match_health_status = hm.get("status", "?")
                        if hm.get("status") == "ERROR":
                            this_match_has_error = True
                            break
            if this_match_has_error:
                print(f"[match-analyze]   FAIL: match {match_home} vs {match_away} has health ERROR — 终止分析", file=sys.stderr)
                return 1
            else:
                print(f"[match-analyze]   snapshot health: {health.get('overall_status', '?')} (this match: {match_health_status})")
        else:
            print(f"[match-analyze]   snapshot health: unreadable, continuing")

    # ── Step 1: devig artifacts ──
    devig_paths: dict[str, Path] = {}
    for mkey in ("h2h", "spreads", "totals"):
        outcomes = pinnacle.get(mkey, [])
        if not outcomes:
            continue

        # For totals, group by @point to avoid multi-line devig contamination.
        # spreads/h2h keep all outcomes together (spreads: 1 outcome per point → no devig needed per-point).
        if mkey in ("h2h", "spreads"):
            outcome_groups: list[list[dict]] = [outcomes]  # type: ignore[assignment]
            group_keys: list[str] = [mkey]
        else:  # totals
            groups: dict[Any, list[dict]] = {}
            for o in outcomes:
                pt = o.get("point", "none")
                groups.setdefault(pt, []).append(o)
            outcome_groups = list(groups.values())
            group_keys = [f"{mkey}@{pt}" if pt != "none" else mkey for pt in groups]

        for gk, g_outcomes in zip(group_keys, outcome_groups):
            devig_artifact = generate_devig_artifact(match_id, mkey, g_outcomes, captured_at, snapshot_id)
            # Tag which line this devig covers
            if gk != mkey:
                devig_artifact["line"] = gk.split("@", 1)[1] if "@" in gk else None
            devig_path = output_dir / f"devig-{gk}-{match_id}-{stable_id(captured_at)}.json"
            write_json(devig_path, devig_artifact)
            devig_paths[gk] = devig_path
            aid = f"devig:{match_id}:{gk}:{stable_id(captured_at)}"
            cap_map = {"h2h": "devig_1x2", "spreads": "asian_handicap", "totals": "totals"}
            provides = ["no_vig", cap_map.get(mkey, "no_vig")]
            artifacts.append({
                "artifact_id": aid,
                "path": str(devig_path.resolve()),
                "provides": provides,
                "artifact_type": "devig",
                "market": mkey,
                "line_group": gk,
                "bookmaker": "pinnacle",
            })
            print(f"[match-analyze]    devig-{gk}: {devig_path.name}  ({len(g_outcomes)} outcomes)")

    # ── Step 2: cross_book_scan (Path A) ──
    crossbook_ok = False
    crossbook_path = output_dir / f"crossbook-{match_id}-{stable_id(captured_at)}.json"
    scan_cmd = [
        PYTHON, str(SKILL_SCRIPTS / "cross_book_scan.py"),
        "--input-snapshot", str(snapshot_path),
        "--match-home", match_home,
        "--match-away", match_away,
        "--output", str(crossbook_path),
    ]
    try:
        scan_ret = subprocess.run(scan_cmd, capture_output=True, text=True, timeout=120)
        if scan_ret.returncode != 0:
            print(f"[match-analyze]   crossbook 失败: {scan_ret.stderr[:300]}", file=sys.stderr)
            artifacts.append({
                "artifact_id": f"crossbook:{match_id}:{stable_id(captured_at)}",
                "path": str(crossbook_path.resolve()),
                "provides": [],
                "artifact_type": "crossbook_scan",
                "status": "failed",
                "error": (scan_ret.stderr or scan_ret.stdout or "unknown error").strip()[:300],
            })
        else:
            crossbook_ok = True
            crossbook_aid = f"crossbook:{match_id}:{stable_id(captured_at)}"
            artifacts.append({
                "artifact_id": crossbook_aid,
                "path": str(crossbook_path.resolve()),
                "provides": ["path_a_crossbook"],
                "artifact_type": "crossbook_scan",
            })
            print(f"[match-analyze]   crossbook: {crossbook_path.name}")
    except subprocess.TimeoutExpired:
        print(f"[match-analyze]   crossbook 超时", file=sys.stderr)
        artifacts.append({
            "artifact_id": f"crossbook:{match_id}:{stable_id(captured_at)}",
            "path": str(crossbook_path.resolve()),
            "provides": [],
            "artifact_type": "crossbook_scan",
            "status": "failed",
            "error": "timeout (120s)",
        })

    if mode == "fast":
        # fast 模式: 产 minimal manifest + 走 direct_summary, 禁止 LLM 手工组装
        fast_manifest = build_manifest(
            match_id, match_home, match_away,
            window, timing_class, kickoff_utc,
            str(snapshot_path), snapshot_id, artifacts,
            captured_at_utc=captured_at,
        )
        fast_manifest["report_completeness"] = "fast_no_play"
        fast_manifest["workflow_contract"] = "wc26.direct_report.v1.fast"
        fast_manifest["mode"] = "fast"
        fast_path = output_dir / f"manifest-{match_id}-{window}-{utc_now().replace(':','-')}.json"
        write_json(fast_path, fast_manifest)
        print(f"[match-analyze] mode=fast, 产出 manifest + crossbook")
        print(f"[match-analyze]   manifest: {fast_path}")
        print(f"[match-analyze]   crossbook: {crossbook_path}")

        # direct_summary (不传 --report, 不强制 contract/guard)
        ds_cmd = [
            PYTHON, str(SKILL_SCRIPTS / "direct_summary.py"),
            "--manifest", str(fast_path),
            "--max-chars", "3900",
        ]
        try:
            ds_ret = subprocess.run(ds_cmd, capture_output=True, text=True, timeout=60)
            if ds_ret.returncode == 0:
                print(f"[match-analyze]   fast path direct_summary: OK")
                print(ds_ret.stdout)
            else:
                print(f"[match-analyze]   fast path direct_summary stderr: {ds_ret.stderr[:300]}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"[match-analyze]   fast path direct_summary 超时", file=sys.stderr)
        return 0

    # ── Step 3: 写初始 manifest ──
    manifest = build_manifest(
        match_id, match_home, match_away,
        window, timing_class, kickoff_utc,
        str(snapshot_path), snapshot_id, artifacts,
        captured_at_utc=captured_at,
    )
    if health_note:
        manifest["health_note"] = health_note.strip()

    manifest_path = output_dir / f"manifest-{match_id}-{window}-{utc_now().replace(':','-')}.json"
    write_json(manifest_path, manifest)

    # ── 设置所有 8 个 analysis_gates ──
    gates = manifest.setdefault("analysis_gates", {})
    # devig: artifacts have been produced but not contract-verified
    has_h2h = any(a.get("artifact_type") == "devig" and a.get("market") == "h2h" for a in artifacts)
    has_spreads = any(a.get("artifact_type") == "devig" and a.get("market") == "spreads" for a in artifacts)
    has_totals_devig = any(a.get("artifact_type") == "devig" and a.get("market") == "totals" for a in artifacts)
    has_crossbook = any(a.get("artifact_type") == "crossbook_scan" for a in artifacts)
    gates["devig_three_method"] = {"status": "pending", "reason": "produced, awaiting report_contract verification"}
    gates["path_a_crossbook"] = {
        "status": "pending" if crossbook_ok else ("failed" if any(a.get("artifact_type") == "crossbook_scan" and a.get("status") == "failed" for a in artifacts) else "skipped_not_applicable"),
        "reason": "produced, awaiting report_contract verification" if crossbook_ok else ("subprocess failed" if any(a.get("artifact_type") == "crossbook_scan" and a.get("status") == "failed" for a in artifacts) else ""),
    }
    gates["asian_handicap"] = {"status": "pending" if has_spreads else "skipped_not_applicable", "reason": "produced, awaiting report_contract verification" if has_spreads else ""}
    gates["totals"] = {"status": "pending" if has_totals_devig else "skipped_not_applicable", "reason": "produced, awaiting report_contract verification" if has_totals_devig else ""}
    gates["path_b_model_diagnostic"] = {"status": "diagnostic", "reason": "no model data at generation time"}
    gates["source_freshness"] = {"status": "pass"}
    # path_c_consistency + mechanism_audit 由后续步骤设置
    manifest["manifest_path"] = str(manifest_path)
    write_json(manifest_path, manifest)
    print(f"[match-analyze]   manifest: {manifest_path.name}  ({len(artifacts)} artifacts)")
    print(f"[match-analyze]   gates: {len(gates)} set")

    # ── Step 4: consistency_triangle (Path C) ──
    ct_cmd = [
        PYTHON, str(SKILL_SCRIPTS / "consistency_triangle.py"),
        "--snapshot", str(snapshot_path),
        "--match", f"{match_home} vs {match_away}",
    ]
    ct_artifact_entry: dict[str, Any] | None = None
    try:
        ct_ret = subprocess.run(ct_cmd, capture_output=True, text=True, timeout=120)
        if ct_ret.returncode == 0:
            # Parse stdout: consistency_triangle outputs JSON result per match
            ct_stdout = ct_ret.stdout.strip()
            if ct_stdout:
                try:
                    ct_result = json.loads(ct_stdout)
                    ct_path = output_dir / f"path-c-{match_id}-{stable_id(captured_at)}.json"
                    ct_artifact = {
                        "artifact_id": f"consistency:{match_id}:{stable_id(captured_at)}",
                        "artifact_type": "consistency_triangle",
                        "artifact_kind": "consistency_triangle",
                        "script": "consistency_triangle.py",
                        "provides": ["path_c_consistency"],
                        "match_id": match_id,
                        "home": match_home,
                        "away": match_away,
                        "snapshot_id": snapshot_id,
                        "snapshot_path": str(snapshot_path),
                        "status": "signal" if ct_result.get("signal", {}).get("type") else "no_signal",
                        "analysis": ct_result.get("analysis"),
                        "discrepancy": ct_result.get("discrepancy"),
                        "signal": ct_result.get("signal"),
                        "market_profile": ct_result.get("market_profile"),
                        "caveat": ct_result.get("caveat"),
                    }
                    write_json(ct_path, ct_artifact)
                    ct_artifact_entry = {
                        "artifact_id": ct_artifact["artifact_id"],
                        "path": str(ct_path.resolve()),
                        "provides": ["path_c_consistency"],
                        "artifact_type": "consistency_triangle",
                        "status": ct_artifact["status"],
                    }
                    manifest.setdefault("artifacts", []).append(ct_artifact_entry)
                    manifest.setdefault("analysis_gates", {})["path_c_consistency"] = {
                        "status": "pass", "generated_by": "consistency_triangle"
                    }
                    write_json(manifest_path, manifest)
                    print(f"[match-analyze]   path_c: {ct_path.name}  status={ct_artifact['status']}")
                except json.JSONDecodeError:
                    print(f"[match-analyze]   consistency_triangle 输出非 JSON: {ct_stdout[:200]}", file=sys.stderr)
        else:
            err = ct_ret.stderr[:300]
            if not err:
                err = ct_ret.stdout[:300]
            print(f"[match-analyze]   consistency_triangle 退出码 {ct_ret.returncode}: {err}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"[match-analyze]   consistency_triangle 超时", file=sys.stderr)

    # ── Step 5: mechanism_audit ──
    audit_path = output_dir / f"audit-{match_id}-{stable_id(captured_at)}.json"
    audit_cmd = [
        PYTHON, str(SKILL_SCRIPTS / "mechanism_audit.py"),
        "--manifest", str(manifest_path),
        "--output", str(audit_path),
    ]
    try:
        audit_ret = subprocess.run(audit_cmd, capture_output=True, text=True, timeout=120)
        if audit_ret.returncode == 0:
            # 把 audit artifact 补入 manifest
            manifest = read_json(manifest_path, manifest)
            audit_aid = f"audit:{match_id}:{stable_id(captured_at)}"
            manifest.setdefault("artifacts", []).append({
                "artifact_id": audit_aid,
                "path": str(audit_path.resolve()),
                "provides": ["mechanism_audit"],
                "artifact_type": "mechanism_audit",
            })
            manifest.setdefault("analysis_gates", {})["mechanism_audit"] = {"status": "pass"}
            write_json(manifest_path, manifest)
            print(f"[match-analyze]   audit: {audit_path.name}")
        else:
            print(f"[match-analyze]   mechanism_audit stderr: {audit_ret.stderr[:300]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"[match-analyze]   mechanism_audit 超时", file=sys.stderr)

    # ── Step 6: 生成 report.md ──
    report_path = MATCH_REPORT_DIR / f"{match_id}-{match_home.replace(' ','')}-{match_away.replace(' ','')}-{window}-{utc_now().replace(':','-')}.md"
    report_md = generate_report(manifest, match, match_id, match_home, match_away, window, health_note)
    write_json(report_path, report_md)  # 不对, report 是 markdown
    report_path.write_text(report_md, encoding="utf-8")
    manifest["report_path"] = str(report_path)
    write_json(manifest_path, manifest)
    print(f"[match-analyze]   report: {report_path.name}")

    # ── Step 7: direct_summary ──
    ds_cmd = [
        PYTHON, str(SKILL_SCRIPTS / "direct_summary.py"),
        "--manifest", str(manifest_path),
        "--report", str(report_path),
        "--max-chars", "3900",
    ]
    try:
        ds_ret = subprocess.run(ds_cmd, capture_output=True, text=True, timeout=60)
        if ds_ret.returncode == 0:
            print(f"[match-analyze]   direct_summary: OK")
            print(f"[match-analyze]")
            print(ds_ret.stdout)
        else:
            print(f"[match-analyze]   direct_summary stderr: {ds_ret.stderr[:300]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"[match-analyze]   direct_summary 超时", file=sys.stderr)

    print(f"[match-analyze] 完成")
    print(f"[match-analyze]   manifest: {manifest_path}")
    print(f"[match-analyze]   report:   {report_path}")
    return 0


def generate_report(
    manifest: dict, match: dict,
    match_id: str, home: str, away: str, window: str,
    health_note: str,
) -> str:
    """Generate minimal report markdown from manifest + match data."""
    kickoff = match.get('commence_time', '')
    lines = [
        f"# WC26 {match_id} {home} vs {away} - {window} Handicap Report",
        "",
        f"cutoff_utc: {manifest.get('generated_at_utc', utc_now())}",
        "workflow_contract: wc26.direct_report.v1",
        f"match_id: {match_id}",
        f"home: {home}",
        f"away: {away}",
        f"kickoff_utc: {kickoff}",
        f"window: {window}",
        f"source_quality: {manifest.get('source_quality', 'B')}",
        f"final_status: {manifest.get('final_status', 'watch')}",
        f"artifact_manifest_path: {manifest.get('manifest_path', '')}",
        "artifact_contract_status: pending",
        "report_guard_status: pending",
        "",
        "## 1. One-Line View",
        f"Generated by wc26-match-analyze orchestrator.",
        "",
        "## 4. Market Board",
        "| Market | Line | Pinnacle Price | No-Vig Prob | Overround |",
        "| --- | --- | --- | --- | --- |",
    ]
    for a in manifest.get("artifacts", []):
        if not isinstance(a, dict):
            continue
        if a.get("artifact_type") == "devig":
            apath = Path(a["path"])
            if not apath.exists():
                continue
            dv = read_json(apath, {})
            if not dv:
                continue
            mkt = dv.get("market", "?")
            line = dv.get("line") or ""
            odds_str = " / ".join(str(round(o, 2)) for o in dv.get("decimal_odds", []))
            probs = dv.get("no_vig_probabilities", [])
            if probs:
                prob_str = " / ".join(f"{p*100:.1f}%" for p in probs)
            else:
                prob_str = "N/A"
            ovr = dv.get("overround", 0)
            ovr_str = f"{ovr*100:.2f}%"
            lines.append(f"| {mkt} | {line} | {odds_str} | {prob_str} | {ovr_str} |")

    lines.extend([
        "",
        "## 5A. Path A Cross-Book Value Scan",
    ])
    # Find crossbook artifact
    for a in manifest.get("artifacts", []):
        if not isinstance(a, dict):
            continue
        if "path_a_crossbook" in str(a.get("provides", "")):
            apath = Path(a["path"])
            if apath.exists():
                cb = read_json(apath, {})
                if cb:
                    summary = cb.get("summary", {})
                    lines.append(f"- quotes_scanned: {summary.get('quotes_scanned', 0)}")
                    lines.append(f"- actionable_count: {summary.get('actionable_count', 0)}")
                    lines.append(f"- edge_count: {summary.get('edge_count', 0)}")
            break

    lines.extend([
        "",
        "## Artifacts Generated",
    ])
    for a in manifest.get("artifacts", []):
        if isinstance(a, dict):
            lines.append(f"- `{Path(a['path']).name}` provides={a.get('provides', '?')}")

    if health_note:
        lines.extend(["", f"⚠️ {health_note}"])

    lines.append("")
    return "\n".join(lines)


# ── 主入口 ────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="WC26 match analysis orchestrator")
    ap.add_argument("--snapshot", required=True, help="the-odds-api multibook snapshot path")
    ap.add_argument("--match-home", required=True, help="Home team name")
    ap.add_argument("--match-away", required=True, help="Away team name")
    ap.add_argument("--match-id", required=True, help="Match ID (e.g. M001)")
    ap.add_argument("--window", default="T-24h_confirm", help="Analysis window")
    ap.add_argument("--timing-class", default="confirmation", help="Timing class")
    ap.add_argument("--output", default=None, help="Output directory (default: reports/artifacts)")
    ap.add_argument("--mode", choices=["full", "fast"], default="full", help="Analysis mode")
    args = ap.parse_args()

    snapshot_path = Path(args.snapshot)
    if not snapshot_path.exists():
        print(f"[match-analyze] snapshot not found: {snapshot_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output) if args.output else ARTIFACT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    return run_orchestrator(
        snapshot_path=snapshot_path,
        match_home=args.match_home,
        match_away=args.match_away,
        match_id=args.match_id,
        window=args.window,
        timing_class=args.timing_class,
        output_dir=output_dir,
        mode=args.mode,
    )


if __name__ == "__main__":
    sys.exit(main())
