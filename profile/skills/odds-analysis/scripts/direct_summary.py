#!/usr/bin/env python3
"""Generate the WC26 direct Telegram report summary.

This is the user-visible projection for the direct gateway. It summarizes the
guarded manifest; it must not turn an incomplete manifest into a completed
betting report.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPORT_CONTRACT_PATH = SCRIPT_DIR / "report_contract.py"
REPORT_GUARD_PATH = SCRIPT_DIR / "report_guard.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


report_contract = load_module("report_contract", REPORT_CONTRACT_PATH)
report_guard = load_module("report_guard", REPORT_GUARD_PATH)


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def load_artifact_payload(artifact: dict[str, Any], manifest_path: Path) -> dict[str, Any] | None:
    raw_path = str(artifact.get("path", "")).strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = (manifest_path.parent / path).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def load_direct_request_payload(manifest: dict[str, Any], manifest_path: Path) -> dict[str, Any] | None:
    raw_path = str(manifest.get("direct_request_path", "")).strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = (manifest_path.parent / path).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "N/A"


def fmt_num(value: Any, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "N/A"


def fmt_ev(value: Any) -> str:
    try:
        return f"{float(value) * 100:+.2f}%"
    except Exception:
        return "N/A"


def fmt_profile_pct(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except Exception:
        return "N/A"


def market_profile_lines(profile: Any) -> list[str]:
    if not isinstance(profile, dict):
        return []
    fit = profile.get("fit") if isinstance(profile.get("fit"), dict) else {}
    status = str(profile.get("status") or "unknown")
    confidence = str(profile.get("confidence") or fit.get("confidence") or "unknown")
    residual = first_present(fit.get("max_abs_residual_pp"), profile.get("max_abs_residual_pp"), "N/A")
    footnote = profile.get("footnote_zh") or "市场共识画像·描述性·非下注信号；最高概率不等于价值。"
    if status == "suppressed":
        return [
            f"- 市场画像: suppressed | confidence={confidence} residual={residual}pp | reason={profile.get('reason', 'fit_residual')}",
            f"- 画像说明: {footnote}",
        ]

    lines = [f"- 市场画像: confidence={confidence} residual={residual}pp | {footnote}"]
    most = profile.get("most_likely_1x2") if isinstance(profile.get("most_likely_1x2"), dict) else {}
    lean = profile.get("total_line_lean") if isinstance(profile.get("total_line_lean"), dict) else {}
    lean_key = lean.get("lean")
    lean_pct = lean.get("over_pct") if lean_key == "over" else lean.get("under_pct")
    if most or lean:
        lines.append(
            f"- 胜负平最看好: {most.get('label', 'N/A')} {fmt_profile_pct(most.get('prob_pct'))}；"
            f"大小倾向: {lean.get('label', 'N/A')} {fmt_profile_pct(lean_pct)}"
        )
    top_scores = profile.get("top_scores") if isinstance(profile.get("top_scores"), list) else []
    if top_scores:
        parts = [
            f"{row.get('score', 'N/A')} {fmt_profile_pct(row.get('prob_pct'))}"
            for row in top_scores[:3]
            if isinstance(row, dict)
        ]
        if parts:
            lines.append("- 最可能比分TOP3: " + " / ".join(parts))
    top_goals = profile.get("top_total_goals") if isinstance(profile.get("top_total_goals"), list) else []
    margin = profile.get("top_margin") if isinstance(profile.get("top_margin"), dict) else {}
    btts = profile.get("btts") if isinstance(profile.get("btts"), dict) else {}
    if top_goals or margin or btts:
        top_goal = top_goals[0] if top_goals and isinstance(top_goals[0], dict) else {}
        btts_label = "是" if btts.get("lean") == "yes" else "否"
        btts_pct = btts.get("yes_pct") if btts.get("lean") == "yes" else btts.get("no_pct")
        lines.append(
            f"- 最可能总进球: {top_goal.get('goals', 'N/A')}球 {fmt_profile_pct(top_goal.get('prob_pct'))} "
            f"| 净胜球: {margin.get('label', 'N/A')} {fmt_profile_pct(margin.get('prob_pct'))} "
            f"| BTTS: {btts_label} {fmt_profile_pct(btts_pct)}"
        )
    return lines


def is_crossbook_payload(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    raw = " ".join(
        [
            str(payload.get("artifact_type", "")),
            str(payload.get("artifact_kind", "")),
            str(payload.get("script", "")),
        ]
    ).lower()
    return "crossbook" in raw or "cross_book" in raw


def _label_point(label: str) -> float:
    try:
        return float(str(label).rsplit("@", 1)[1])
    except Exception:
        return 999.0


def _ordered_market_labels(labels: list[str], market_key: str) -> list[str]:
    if market_key == "totals":
        return sorted(labels, key=lambda item: (0 if item.startswith("over@") else 1 if item.startswith("under@") else 2, item))
    if market_key == "spreads":
        return sorted(labels, key=lambda item: (_label_point(item), item))
    return labels


def crossbook_market_projection(payload: dict[str, Any] | None, market_key: str, label: str) -> str | None:
    if not is_crossbook_payload(payload):
        return None
    markets = payload.get("markets") if isinstance(payload.get("markets"), dict) else {}
    market = markets.get(market_key)
    if not isinstance(market, dict) or market.get("status") != "ok":
        return None
    method = str(market.get("devig_primary") or "shin")
    fair_by_method = market.get("fair_probs") if isinstance(market.get("fair_probs"), dict) else {}
    probs = fair_by_method.get(method)
    if not isinstance(probs, dict) or not probs:
        return None
    raw_outcomes = market.get("outcomes_scanned") if isinstance(market.get("outcomes_scanned"), list) else list(probs)
    outcomes = [str(item) for item in raw_outcomes if str(item) in probs]
    outcomes = _ordered_market_labels(outcomes, market_key)
    # Board prices (raw Pinnacle/h2h) vs fair (no-vig)
    board_prices = market.get("sharp_board_prices") if isinstance(market.get("sharp_board_prices"), dict) else {}
    fair_odds_str = " / ".join(f"{outcome} {fmt_pct(probs[outcome])}" for outcome in outcomes) if outcomes else ""
    board_str = ""
    if board_prices and outcomes:
        board_parts = [f"{outcome} {fmt_num(board_prices.get(outcome, 0))}" for outcome in outcomes if outcome in board_prices]
        if board_parts:
            board_str = " | board: " + " / ".join(board_parts)
    overround = market.get("sharp_overround")
    over_str = f" | overround={overround*100:+.2f}%" if overround is not None and market_key != "spreads" else ""
    rendered = f"{fair_odds_str}{board_str}{over_str}" if fair_odds_str else "N/A"
    return (
        f"- {label}: anchor={market.get('sharp_anchor', 'N/A')} | {rendered} "
        f"| method={method} quotes={market.get('quotes_scanned', 'N/A')} "
        f"edges={len(market.get('edges', [])) if isinstance(market.get('edges'), list) else 'N/A'}"
    )


def first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def match_label(manifest: dict[str, Any]) -> tuple[str, str, str]:
    match = manifest.get("match") if isinstance(manifest.get("match"), dict) else {}
    teams = manifest.get("teams") if isinstance(manifest.get("teams"), list) else []
    match_id = str(first_present(manifest.get("match_id"), match.get("match_id"), "UNKNOWN"))
    home = str(
        first_present(
            match.get("home"),
            match.get("home_team"),
            manifest.get("home"),
            manifest.get("home_team"),
            teams[0] if teams else None,
            "home TBD",
        )
    )
    away = str(
        first_present(
            match.get("away"),
            match.get("away_team"),
            manifest.get("away"),
            manifest.get("away_team"),
            teams[1] if len(teams) > 1 else None,
            "away TBD",
        )
    )
    return match_id, home, away


def artifact_caps(artifact: dict[str, Any], payload: dict[str, Any] | None) -> set[str]:
    provides = artifact.get("provides", [])
    if not isinstance(provides, list):
        provides = []
    raw = " ".join(
        [
            str(artifact.get("artifact_type", "")),
            str(artifact.get("script", "")),
            str(artifact.get("path", "")),
            " ".join(str(item) for item in provides),
            str(payload.get("artifact_kind", "")) if payload else "",
            str(payload.get("artifact_type", "")) if payload else "",
        ]
    ).lower()
    caps: set[str] = set()
    if "no_vig" in raw or "scalar_market" in raw or "devig_1x2" in raw:
        caps.add("devig_1x2")
    if "cross_book" in raw or "crossbook" in raw:
        caps.add("path_a_crossbook")
    if "asian_handicap" in raw:
        caps.add("asian_handicap")
    if "totals" in raw or "total_goals" in raw:
        caps.add("totals")
    if "consistency_triangle" in raw or "path_c" in raw:
        caps.add("path_c_consistency")
    if "mechanism_audit" in raw or "mechanism audit" in raw:
        caps.add("mechanism_audit")
    if "role_engine" in raw:
        caps.add("role_engine")
    if "phase_context" in raw:
        caps.add("phase_context")
    if "bias_mirror" in raw:
        caps.add("bias_mirror")
    if "no_play_classification" in raw:
        caps.add("no_play_classification")
    return caps


def find_artifacts(manifest: dict[str, Any], manifest_path: Path) -> dict[str, tuple[dict[str, Any], dict[str, Any] | None]]:
    found: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] = {}
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        payload = load_artifact_payload(artifact, manifest_path)
        for cap in artifact_caps(artifact, payload):
            found.setdefault(cap, (artifact, payload))
    return found


def gate_status(manifest: dict[str, Any], gate: str) -> str:
    gates = manifest.get("analysis_gates")
    if not isinstance(gates, dict):
        return "missing"
    raw = gates.get(gate)
    if isinstance(raw, dict):
        return str(raw.get("status", "missing"))
    if raw:
        return str(raw)
    return "missing"


def number_lines(manifest: dict[str, Any]) -> list[str]:
    lines = []
    for number in manifest.get("numbers", []):
        if not isinstance(number, dict):
            continue
        kind = str(number.get("kind", "")).lower()
        label = str(first_present(number.get("label"), number.get("name"), number.get("id"), kind))
        if kind in {"no_vig", "p_adj_edge", "scalar_ev", "asian_handicap_ev", "asian_handicap_kelly", "kelly", "robust_ev"}:
            value = number.get("value")
            rendered = fmt_pct(value) if kind in {"no_vig", "p_adj_edge", "asian_handicap_kelly", "kelly"} else fmt_num(value, 4)
            lines.append(f"- {label}: {rendered}")
    return lines[:6]


def missing_direct_items(contract_errors: list[str]) -> list[str]:
    keywords = [
        "workflow_contract",
        "direct_request_id",
        "direct_request_path",
        "source_freshness",
        "analysis_gates",
        "devig_methods",
        "survives_all_methods",
        "artifact capabilities",
    ]
    found = []
    for error in contract_errors:
        for key in keywords:
            if key in error and key not in found:
                found.append(key)
    return found


def trim_summary(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    suffix = "\n\n[截断] 摘要超过 Telegram 限制，请打开完整报告/manifest。"
    return text[: max_chars - len(suffix)].rstrip() + suffix


def build_summary(manifest_path: str | Path, report_path: str | Path | None = None, max_chars: int = 3900) -> str:
    manifest_path = Path(manifest_path)
    manifest = load_json(manifest_path)
    contract = report_contract.validate_manifest(manifest, manifest_path)
    guard = None
    if report_path:
        guard = report_guard.validate_report(Path(report_path))

    match_id, home, away = match_label(manifest)
    match = manifest.get("match") if isinstance(manifest.get("match"), dict) else {}
    artifacts = find_artifacts(manifest, manifest_path)
    direct_request = load_direct_request_payload(manifest, manifest_path)
    source_freshness = manifest.get("source_freshness") if isinstance(manifest.get("source_freshness"), dict) else {}
    sources = source_freshness.get("sources") or source_freshness.get("snapshots") or []
    sources = sources if isinstance(sources, list) else []
    final_status = str(manifest.get("final_status", "unknown")).upper()
    completeness = str(manifest.get("report_completeness", "complete")).strip().lower()

    contract_status = "PASS" if contract.get("valid") else "FAIL"
    guard_status = "PASS" if guard and guard.get("safe_to_relay") else ("未运行" if guard is None else "FAIL")
    # In live (full) mode, guard is mandatory (--report required).
    # Partial and fast manifests skip guard intentionally.
    manifest_mode = str(manifest.get("mode", "")).strip().lower()
    report_completeness = str(manifest.get("report_completeness", "complete")).strip().lower()
    if guard:
        relay_ready = contract.get("valid") and guard.get("safe_to_relay")
    elif manifest_mode == "live" and report_completeness == "complete":
        relay_ready = False  # live full mode requires --report
    else:
        relay_ready = contract.get("valid")  # fast/partial: guard optional
    source_quality_text = str(manifest.get("source_quality", "TBD"))
    source_quality_cap = first_present(contract.get("source_quality_cap"), manifest.get("source_quality_cap"))
    if source_quality_cap and str(source_quality_cap) != source_quality_text:
        source_quality_text = f"{source_quality_text} cap={source_quality_cap}"
    window_text = str(first_present(manifest.get("window"), manifest.get("timing_class"), "TBD"))
    if manifest.get("window_display"):
        window_text = f"{window_text} / {manifest.get('window_display')}"
    elif manifest.get("timing_class") and str(manifest.get("timing_class")) != window_text:
        window_text = f"{window_text} / {manifest.get('timing_class')}"
    message_id_text = "N/A"
    if direct_request:
        message_id_text = str(direct_request.get("message_id", "N/A"))
        source = direct_request.get("message_id_source")
        exact = direct_request.get("message_id_exact")
        if source or exact is not None:
            message_id_text = f"{message_id_text} ({source or 'unknown'}, exact={exact})"

    if not relay_ready:
        title_status = "BLOCKED"
    elif completeness == "partial":
        title_status = f"PARTIAL / {final_status}"
    else:
        title_status = final_status
    lines = [
        f"WC26 {match_id} {home} vs {away} — {title_status}",
        "",
        "① 比赛基本信息",
        f"- 开球: {first_present(match.get('kickoff_utc'), manifest.get('kickoff_utc'), 'TBD')}",
        f"- 场地: {first_present(match.get('venue'), manifest.get('venue'), 'TBD')}",
        f"- 窗口: {window_text} | source_quality={source_quality_text} | mode={manifest.get('mode', 'TBD')}",
        "",
        "② 数据与契约",
        f"- direct_request_id: {first_present(manifest.get('direct_request_id'), '缺失')}",
        "- direct_request_trace: "
        + (
            f"status={direct_request.get('status', 'unknown')} | cache={direct_request.get('cache_mode', 'N/A')} | api_refresh={direct_request.get('api_refresh_performed', 'N/A')} | chat={direct_request.get('chat_id', 'N/A')} | msg={message_id_text}"
            if direct_request
            else "缺失/不可读"
        ),
        f"- report_contract: {contract_status} | report_guard: {guard_status}",
        f"- source_freshness: {gate_status(manifest, 'source_freshness')} | 数据源数: {len(sources)}",
    ]

    if not relay_ready:
        lines.extend(
            [
                "- 状态: 不能按完整盘口报告发送；下面列缺口，不能口头补成 PASS。",
                "- 主要缺口: " + (", ".join(missing_direct_items(contract.get("errors", []))) or "见 contract errors"),
            ]
        )
    elif completeness == "partial":
        lines.append("- 状态: PARTIAL 可发送；缺源项目已显式跳过，结论只能 WATCH，不能 PASS/下注。")
        skipped = manifest.get("skipped_sections", [])
        if isinstance(skipped, list) and skipped:
            lines.append("- 跳过项:")
            for item in skipped[:6]:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"  - {item.get('gate', 'unknown')}: {item.get('reason', 'N/A')}；影响: {item.get('impact', 'N/A')}"
                )

    lines.extend(["", "③ 1X2 去水"])
    devig_entry = artifacts.get("devig_1x2")
    if devig_entry:
        _artifact, payload = devig_entry
        if payload:
            probs = payload.get("no_vig_probabilities") or payload.get("probabilities") or []
            methods = payload.get("devig_methods") if isinstance(payload.get("devig_methods"), dict) else {}
            lines.append(f"- 主方法: {payload.get('devig_primary', 'N/A')} | 三法: {', '.join(sorted(methods)) or '缺失'} | survives_all_methods={payload.get('survives_all_methods', 'N/A')}")
            if isinstance(probs, list) and probs:
                lines.append("- 去水概率: " + " / ".join(fmt_pct(v) for v in probs[:3]))
        else:
            lines.append("- devig artifact 不可读")
    else:
        lines.append("- 缺 1X2 三法去水 artifact")
    lines.extend(number_lines(manifest))

    lines.extend(["", "④ Path A 跨书商"])
    crossbook = artifacts.get("path_a_crossbook")
    if crossbook and crossbook[1]:
        payload = crossbook[1]
        summary = payload.get("summary") or payload.get("scan_summary") or {}
        lines.append(f"- gate: {gate_status(manifest, 'path_a_crossbook')} | artifact={crossbook[0].get('artifact_id')}")
        if isinstance(summary, dict):
            best_edge = summary.get("best_actionable_edge") or summary.get("best_edge")
            raw_actionable = first_present(summary.get("actionable_count"), summary.get("qualified_play_count"), "N/A")
            relay_actionable = raw_actionable if contract.get("actionable_allowed") else 0
            display_best_ev = best_edge.get("ev_shin") if isinstance(best_edge, dict) else summary.get("best_ev")
            lines.append(
                f"- 扫描: markets={','.join(summary.get('markets_scanned', [])) if isinstance(summary.get('markets_scanned'), list) else 'N/A'} "
                f"| quotes={first_present(summary.get('quotes_scanned'), 'N/A')} "
                f"| edges={first_present(summary.get('edge_count'), 'N/A')} "
                f"| noise={first_present(summary.get('noise_edge_count'), 'N/A')} "
                f"| raw_actionable={raw_actionable} relay_actionable={relay_actionable} "
                f"| best_ev={fmt_ev(first_present(display_best_ev, None))}"
            )
            if isinstance(best_edge, dict):
                scan_actionable = best_edge.get("actionable", best_edge.get("qualifies", "N/A"))
                relay_edge_actionable = bool(scan_actionable is True and contract.get("actionable_allowed"))
                lines.append(
                    "- 最优偏差: "
                    f"{best_edge.get('book', 'N/A')} {best_edge.get('market_key', 'N/A')} {best_edge.get('outcome', 'N/A')} "
                    f"odds={best_edge.get('offered_odds', 'N/A')} fair={best_edge.get('fair_odds', 'N/A')} "
                    f"EV={fmt_ev(best_edge.get('ev_shin'))} all_methods={best_edge.get('survives_all_methods', 'N/A')} "
                    f"band={best_edge.get('ev_band', 'N/A')} "
                    f"scan_actionable={scan_actionable} relay_actionable={relay_edge_actionable}"
                )
        markets = payload.get("markets") if isinstance(payload.get("markets"), dict) else {}
        for market_name in ("h2h", "spreads", "totals"):
            market_result = markets.get(market_name)
            if not isinstance(market_result, dict):
                continue
            parts = [f"- {market_name}: status={market_result.get('status', 'N/A')}"]
            board_prices = market_result.get("sharp_board_prices")
            fair_probs = market_result.get("fair_probs", {}).get("shin")
            overround = market_result.get("sharp_overround")
            outcomes = market_result.get("outcomes_scanned", [])
            if board_prices and isinstance(board_prices, dict) and outcomes:
                board_rendered = " / ".join(
                    f"{o} {fmt_num(board_prices[o])}" for o in outcomes if o in board_prices
                )
                parts.append(f"board: {board_rendered}")
                if overround is not None and market_name != "spreads":
                    parts.append(f"overround={overround*100:+.2f}%")
                if fair_probs and isinstance(fair_probs, dict):
                    fair_odds_rendered = " / ".join(
                        f"{fmt_num(1.0/fair_probs[o] if fair_probs[o] > 0 else 0)}"
                        for o in outcomes if o in fair_probs
                    )
                    parts.append(f"fair: {fair_odds_rendered}")
            parts.append(f"anchor={market_result.get('sharp_anchor', 'N/A')}")
            parts.append(f"quotes={market_result.get('quotes_scanned', 'N/A')}")
            parts.append(f"edges={len(market_result.get('edges', [])) if isinstance(market_result.get('edges'), list) else 'N/A'}")
            lines.append(" ".join(parts))
    else:
        lines.append(f"- gate: {gate_status(manifest, 'path_a_crossbook')} | 缺 cross_book_scan artifact")

    lines.extend(["", "⑤ 亚盘与大小球"])
    ah = artifacts.get("asian_handicap")
    ah_line = crossbook_market_projection(ah[1] if ah else None, "spreads", "亚盘")
    if ah_line:
        lines.append(ah_line)
    elif ah and ah[1]:
        payload = ah[1]
        lines.append(f"- 亚盘: line={payload.get('line', 'N/A')} price={payload.get('price', payload.get('price_input', 'N/A'))} EV={fmt_num(payload.get('ev'), 4)} Kelly={fmt_num(payload.get('kelly_fraction_full'), 4)}")
    else:
        lines.append(f"- 亚盘: gate={gate_status(manifest, 'asian_handicap')} | 缺腿拆/EV/Kelly artifact")
    totals = artifacts.get("totals")
    totals_line = crossbook_market_projection(totals[1] if totals else None, "totals", "大小球")
    if totals_line:
        lines.append(totals_line)
    elif totals and totals[1]:
        payload = totals[1]
        lines.append(f"- 大小球: line={payload.get('line', 'N/A')} Over={payload.get('over_price', payload.get('price_over', 'N/A'))} Under={payload.get('under_price', payload.get('price_under', 'N/A'))} P(Over)={fmt_pct(payload.get('no_vig_over'))}")
    else:
        lines.append(f"- 大小球: gate={gate_status(manifest, 'totals')} | 缺 totals artifact")

    lines.extend(["", "⑥ Path B 模型纪律"])
    lines.append(f"- gate: {gate_status(manifest, 'path_b_model_diagnostic')} | final_status={manifest.get('final_status', 'unknown')}")
    lines.append(f"- p_adj 默认纪律: {first_present(manifest.get('p_model_note'), manifest.get('edge_summary'), '必须由 manifest/ledger 证明，不能裸用模型差异')}")

    lines.extend(["", "⑦ Path C 一致性"])
    path_c = artifacts.get("path_c_consistency")
    if path_c and path_c[1]:
        payload = path_c[1]
        signal = payload.get("signal") if isinstance(payload.get("signal"), dict) else {}
        discrepancy = payload.get("discrepancy") if isinstance(payload.get("discrepancy"), dict) else {}
        lines.append(f"- gate: {gate_status(manifest, 'path_c_consistency')} | signal={signal.get('type', '无')} strength={signal.get('strength', 'N/A')} discrepancy={discrepancy.get('pp', 'N/A')}pp")
        lines.extend(market_profile_lines(payload.get("market_profile")))
    else:
        lines.append(f"- gate: {gate_status(manifest, 'path_c_consistency')} | 缺 consistency_triangle artifact")

    role_engine = artifacts.get("role_engine")
    lines.extend(["", "🎭 博弈读盘"])
    if role_engine and role_engine[1]:
        payload = role_engine[1]
        lines.append(f"- engine={payload.get('engine_version', 'N/A')} | contract={payload.get('engine_contract', 'N/A')}")
        conclusions = payload.get("role_conclusions") if isinstance(payload.get("role_conclusions"), list) else []
        for conclusion in conclusions[:5]:
            if not isinstance(conclusion, dict):
                continue
            lines.append(
                f"- {conclusion.get('role_label_zh', conclusion.get('role', 'N/A'))}: "
                f"{conclusion.get('decision', 'N/A')} | {conclusion.get('interpretation_zh', 'N/A')} "
                f"[{conclusion.get('evidence_id', 'no-evidence-id')}]"
            )
    else:
        lines.append("- role_engine: 未运行；缺 deterministic role_engine artifact")

    lines.extend(["", "⑨ 博弈裁决 / 机制审计"])
    audit = artifacts.get("mechanism_audit")
    if audit and audit[1]:
        payload = audit[1]
        mechanisms = payload.get("mechanisms") if isinstance(payload.get("mechanisms"), dict) else {}
        blocking = payload.get("blocking_mechanisms") if isinstance(payload.get("blocking_mechanisms"), list) else []
        lines.append(
            f"- audit_status={payload.get('mechanism_audit_status', 'N/A')} "
            f"| required_final_status={payload.get('required_final_status', 'N/A')} "
            f"| blocking={','.join(str(item) for item in blocking) if blocking else 'none'}"
        )
        for key, label in [
            ("path_a_crossbook", "Path A"),
            ("path_b_model_diagnostic", "Path B"),
            ("path_c_consistency", "Path C"),
            ("role_engine", "角色引擎"),
            ("artifact_hypothesis_engine_v0", "假设引擎v0"),
        ]:
            mechanism = mechanisms.get(key)
            if not isinstance(mechanism, dict):
                continue
            detail = ""
            if key == "path_a_crossbook":
                detail = (
                    f"quotes={mechanism.get('quotes_scanned', 'N/A')} "
                    f"edges={mechanism.get('edge_count', 'N/A')} "
                    f"noise={mechanism.get('noise_edge_count', 'N/A')} "
                    f"actionable={mechanism.get('actionable_count', mechanism.get('qualified_play_count', 'N/A'))}"
                )
            elif key == "path_c_consistency":
                detail = f"signal={mechanism.get('signal_type', 'N/A')} discrepancy={mechanism.get('discrepancy_pp', 'N/A')}pp"
            elif key == "role_engine":
                detail = (
                    f"version={mechanism.get('engine_version', 'N/A')} "
                    f"conclusions={mechanism.get('conclusion_count', 'N/A')}"
                )
            elif mechanism.get("reason"):
                detail = str(mechanism.get("reason"))
            lines.append(f"- {label}: {mechanism.get('status', 'N/A')} | {detail}")
        decisions = payload.get("hypothesis_decisions") if isinstance(payload.get("hypothesis_decisions"), list) else []
        if decisions:
            lines.append("- 裁决:")
            for decision in decisions[:6]:
                if not isinstance(decision, dict):
                    continue
                extra = ""
                if decision.get("ev_shin") not in (None, ""):
                    extra = f" EV={fmt_ev(decision.get('ev_shin'))}"
                decision_name = decision.get("decision", "N/A")
                if decision_name == "CONFIRMED_ACTIONABLE" and not contract.get("actionable_allowed"):
                    decision_name = "CONFIRMED_ACTIONABLE(raw_only; relay_blocked)"
                lines.append(
                    f"  - {decision_name}: {decision.get('subject', 'N/A')} | "
                    f"{decision.get('evidence', 'N/A')}{extra}"
                )
    else:
        lines.append("- gate: mechanism_audit missing | 缺机器生成的 mechanism_audit artifact")

    lines.extend(["", "⑩A 复盘诊断附录"])
    reflection = manifest.get("reflection_layer") if isinstance(manifest.get("reflection_layer"), dict) else {}
    phase_payload = reflection.get("phase_context") if isinstance(reflection.get("phase_context"), dict) else (artifacts.get("phase_context", ({}, None))[1] if artifacts.get("phase_context") else None)
    mirror_payload = reflection.get("bias_mirror") if isinstance(reflection.get("bias_mirror"), dict) else (artifacts.get("bias_mirror", ({}, None))[1] if artifacts.get("bias_mirror") else None)
    nop_payload = reflection.get("no_play_classification") if isinstance(reflection.get("no_play_classification"), dict) else (artifacts.get("no_play_classification", ({}, None))[1] if artifacts.get("no_play_classification") else None)
    if isinstance(phase_payload, dict):
        priors = phase_payload.get("phase_priors") if isinstance(phase_payload.get("phase_priors"), dict) else {}
        total = priors.get("total_goals") if isinstance(priors.get("total_goals"), dict) else {}
        lines.append(
            f"- 阶段先验: phase={phase_payload.get('phase', 'N/A')} n={total.get('sample_n', 0)} "
            f"bias={total.get('bias_direction', 'N/A')} confidence={total.get('confidence', 'N/A')}"
        )
    else:
        lines.append("- 阶段先验: 未运行")
    if isinstance(mirror_payload, dict):
        mirrors = mirror_payload.get("mirrors") if isinstance(mirror_payload.get("mirrors"), list) else []
        rendered = " / ".join(f"{row.get('dimension')}={row.get('alignment')}" for row in mirrors if isinstance(row, dict))
        lines.append(f"- 偏差校正镜: {rendered or 'N/A'}")
    else:
        lines.append("- 偏差校正镜: 未运行")
    if isinstance(nop_payload, dict):
        lines.append(f"- NO PLAY分类: {nop_payload.get('type', 'N/A')} | direction={nop_payload.get('direction_if_any', 'N/A')}")
    else:
        lines.append("- NO PLAY分类: 不适用/未运行")
    lines.append("- 脚注: 复盘辅助层·裁定后诊断附录·描述性·非下注信号;不改变既有裁定。")

    lines.extend(
        [
            "",
            "⑩ 结论与复盘",
            f"- 当前结论: {title_status}; actionable 机会也只代表人工复核，不自动下注。",
            f"- 报告: {report_path or manifest.get('report_path', '未提供')}",
            f"- Manifest: {manifest_path}",
            "- 赛后回链: "
            + (
                f"{direct_request.get('status')} | report/manifest 已绑定到 direct_request_id={direct_request.get('direct_request_id')}"
                if direct_request and guard_status == "PASS"
                else "未确认；guard 未 PASS 或 direct_request 不可读，不能声称已回链。"
            ),
        ]
    )
    return trim_summary("\n".join(str(line) for line in lines), max_chars)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-chars", type=int, default=3900)
    args = parser.parse_args()
    print(build_summary(args.manifest, args.report, args.max_chars))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
