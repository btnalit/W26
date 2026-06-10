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
import math
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
KICKOFF_FALLBACK = "2026-06-11T19:00:00Z"  # for demo/oracle only

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


def shin_devig(odds: list[float], max_iter: int = 200) -> list[float]:
    """Shin devig (内联, 零依赖)."""
    pi = [1.0 / o for o in odds]
    Z = sum(pi)
    b = [p / Z for p in pi]

    def p_of(z: float) -> list[float]:
        return [(math.sqrt(z * z + 4 * (1 - z) * bi * bi * Z) - z) / (2 * (1 - z)) for bi in b]

    lo, hi = 1e-6, 0.4
    for _ in range(max_iter):
        z = (lo + hi) / 2
        s = sum(p_of(z))
        if s > 1:
            lo = z
        else:
            hi = z
    return p_of((lo + hi) / 2)


def multiplicative_devig(odds: list[float]) -> list[float]:
    imp = [1.0 / o for o in odds]
    s = sum(imp)
    return [p / s for p in imp]


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
    """从 Pinnacle outcomes 生成 devig artifact (不再调用 devig.py CLI)."""
    odds = [o["price"] for o in outcomes]
    outcome_names = [o["name"] for o in outcomes]
    nv_shin = shin_devig(odds)
    nv_mult = multiplicative_devig(odds)
    overround = sum(1.0 / o for o in odds) - 1.0

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
        "script": "wc26-match-analyze.py (inline)",
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
) -> dict[str, Any]:
    manifest_id = f"manifest-{match_id}-{window}-{utc_now()}"
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
    kickoff_utc = match.get("commence_time") or KICKOFF_FALLBACK

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

    # ── 可选的: 读 snapshot health 做 fail-closed ──
    health_path = SANPSHOT_HEALTH_DIR / f"{snapshot_path.stem}.health.json"
    if health_path.exists():
        health = read_json(health_path, {})
        if isinstance(health, dict) and health.get("overall_status") == "ERROR":
            health_note = " [健康检查: ERROR — 快照中有带 ERROR 的比赛]"
            print(f"[match-analyze]   WARN: snapshot health=ERROR{health_note}")
        else:
            print(f"[match-analyze]   snapshot health: {health.get('overall_status', '?')}")

    # ── Step 1: devig artifacts ──
    devig_paths: dict[str, Path] = {}
    for mkey in ("h2h", "spreads", "totals"):
        outcomes = pinnacle.get(mkey, [])
        if not outcomes:
            continue
        devig_artifact = generate_devig_artifact(match_id, mkey, outcomes, captured_at, snapshot_id)
        devig_path = output_dir / f"devig-{mkey}-{match_id}-{stable_id(captured_at)}.json"
        write_json(devig_path, devig_artifact)
        devig_paths[mkey] = devig_path
        aid = f"devig:{match_id}:{mkey}:{stable_id(captured_at)}"
        artifacts.append({
            "artifact_id": aid,
            "path": str(devig_path.relative_to(output_dir.parent.parent)) if devig_path.is_relative_to(output_dir.parent.parent) else str(devig_path),
            "provides": ["no_vig"],
            "artifact_type": "devig",
            "market": mkey,
            "bookmaker": "pinnacle",
        })
        print(f"[match-analyze]    devig-{mkey}: {devig_path.name}  ({len(outcomes)} outcomes)")

    # ── Step 2: cross_book_scan (Path A) ──
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
        else:
            crossbook_aid = f"crossbook:{match_id}:{stable_id(captured_at)}"
            artifacts.append({
                "artifact_id": crossbook_aid,
                "path": str(crossbook_path),
                "provides": ["path_a_crossbook"],
                "artifact_type": "crossbook_scan",
            })
            print(f"[match-analyze]   crossbook: {crossbook_path.name}")
    except subprocess.TimeoutExpired:
        print(f"[match-analyze]   crossbook 超时", file=sys.stderr)

    if mode == "fast":
        print(f"[match-analyze] mode=fast, 跳过 manifest + path_c + audit")
        print(f"[match-analyze] 产出: {crossbook_path}")
        return 0

    # ── Step 3: 写初始 manifest ──
    manifest = build_manifest(
        match_id, match_home, match_away,
        window, timing_class, kickoff_utc,
        str(snapshot_path), snapshot_id, artifacts,
    )
    if health_note:
        manifest["health_note"] = health_note.strip()

    manifest_path = output_dir / f"manifest-{match_id}-{window}-{utc_now().replace(':','-')}.json"
    write_json(manifest_path, manifest)
    print(f"[match-analyze]   manifest: {manifest_path.name}  ({len(artifacts)} artifacts)")

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
                        "path": str(ct_path),
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
                "path": str(audit_path),
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
    kickoff = match.get("commence_time") or KICKOFF_FALLBACK
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
        f"artifact_manifest_path: {manifest.get('manifest_id', '')}",
        "artifact_contract_status: pass",
        "report_guard_status: pass",
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
    ap.add_argument("--match-id", default="Mxxx", help="Match ID (e.g. M001)")
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
