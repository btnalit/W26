#!/usr/bin/env python3
"""Generate an artifact-backed rich Telegram summary for WC26 direct reports."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


report_contract = load_module("report_contract", SCRIPT_DIR / "report_contract.py")
report_guard = load_module("report_guard", SCRIPT_DIR / "report_guard.py")
fixture_registry = load_module("fixture_registry", SCRIPT_DIR / "fixture_registry.py")


DEFAULT_FIXTURE_PATH = Path("/hermesdata/worldcup-2026-handicap/snapshots/fixtures/football-data-wc-matches-latest.json")


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return payload


def resolve_path(raw_path: Any, base: Path) -> Path | None:
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (base.parent / path).resolve()
    return path


def artifact_caps(artifact: dict[str, Any], payload: dict[str, Any] | None) -> set[str]:
    provides = artifact.get("provides")
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
    if "cross_book" in raw or "crossbook" in raw:
        caps.add("path_a_crossbook")
    if "mechanism_audit" in raw:
        caps.add("mechanism_audit")
    if "role_engine" in raw:
        caps.add("role_engine")
    if "consistency_triangle" in raw or "path_c" in raw:
        caps.add("path_c_consistency")
    if "model" in raw:
        caps.add("path_b_model_diagnostic")
    return caps


def load_artifacts(manifest: dict[str, Any], manifest_path: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        payload = None
        path = resolve_path(artifact.get("path"), manifest_path)
        if path and path.exists():
            try:
                payload = load_json(path)
            except Exception:
                payload = None
        for cap in artifact_caps(artifact, payload):
            if payload:
                found.setdefault(cap, payload)
    return found


def first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "N/A"


def fmt_ev(value: Any) -> str:
    try:
        return f"{float(value) * 100:+.2f}%"
    except Exception:
        return "N/A"


def fmt_price(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
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


def trim(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    suffix = "\n\n[截断] 摘要超过 Telegram 限制，请打开完整报告。"
    return text[: max_chars - len(suffix)].rstrip() + suffix


def market_probs(crossbook: dict[str, Any], market_key: str) -> tuple[dict[str, Any], dict[str, float]]:
    markets = crossbook.get("markets") if isinstance(crossbook.get("markets"), dict) else {}
    market = markets.get(market_key)
    if not isinstance(market, dict):
        return {}, {}
    method = str(market.get("devig_primary") or "shin")
    fair = market.get("fair_probs") if isinstance(market.get("fair_probs"), dict) else {}
    probs = fair.get(method)
    return market, probs if isinstance(probs, dict) else {}


def format_probs(probs: dict[str, float]) -> str:
    return " / ".join(f"{key} {fmt_pct(value)}" for key, value in probs.items()) or "N/A"


def report_market_rows(report_path: str | Path | None) -> dict[str, list[dict[str, str]]]:
    if not report_path:
        return {}
    path = Path(report_path)
    if not path.exists():
        return {}
    rows: dict[str, list[dict[str, str]]] = {"AH": [], "Totals": []}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for line in lines:
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        market = cells[0]
        if market not in rows:
            continue
        if cells[1].lower() == "line":
            continue
        rows[market].append(
            {
                "market": market,
                "line": cells[1],
                "book": cells[2] if len(cells) > 2 else "",
                "unit": cells[3] if len(cells) > 3 else "",
                "price": cells[4] if len(cells) > 4 else "",
            }
        )
    return {key: value for key, value in rows.items() if value}


def _split_price_label(raw: str) -> tuple[str, str]:
    text = str(raw or "").strip()
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*$", text)
    if not match:
        return text, "N/A"
    price = match.group(1)
    label = text[: match.start()].strip()
    return label, price


def _format_total_line(line: str) -> str:
    text = str(line or "").strip()
    lower = text.lower()
    if lower.startswith("over "):
        return text
    if lower.startswith("under "):
        return text
    if re.match(r"^[Oo]\s*\d", text):
        return "Over " + text[1:].strip()
    if re.match(r"^[Uu]\s*\d", text):
        return "Under " + text[1:].strip()
    return text


def format_report_market_rows(rows: list[dict[str, str]], market: str) -> str | None:
    parts: list[str] = []
    for row in rows[:2]:
        label, price = _split_price_label(row.get("price", ""))
        line = str(row.get("line") or "").strip()
        if market == "AH":
            side = " ".join(item for item in [label, line] if item).strip()
        else:
            side = _format_total_line(line)
        if not side:
            side = label or market
        parts.append(f"{side} @{price}")
    return " / ".join(parts) if parts else None


def format_fair_market_line(probs: dict[str, float], market: str) -> str | None:
    if not probs:
        return None
    parts: list[str] = []
    for key, value in probs.items():
        label = str(key)
        if market == "Totals":
            label = _format_total_line(label.replace("@", " "))
        else:
            label = label.replace("@", " ")
        parts.append(f"{label} fair={fmt_pct(value)}")
    return " / ".join(parts) if parts else None


def fixture_entry_for_manifest(manifest: dict[str, Any]) -> dict[str, Any] | None:
    if not DEFAULT_FIXTURE_PATH.exists():
        return None
    try:
        registry = fixture_registry.load_registry(DEFAULT_FIXTURE_PATH)
        return fixture_registry.resolve_fixture(
            registry,
            football_data_id=manifest.get("football_data_id"),
            match_id=manifest.get("match_id"),
        )
    except Exception:
        return None


def report_title_identity(report_path: Path | None) -> dict[str, str]:
    if not report_path or not report_path.exists():
        return {}
    try:
        lines = report_path.read_text(encoding="utf-8", errors="ignore").splitlines()[:30]
    except Exception:
        return {}
    for line in lines:
        text = line.strip().lstrip("#").strip()
        match = re.search(r"\bWC26\s+(?:(M\d{3})\s+)?(.+?)\s+vs\s+(.+?)(?:\s+[—-]\s+|$)", text)
        if match:
            return {
                "match_id": match.group(1) or "",
                "home": match.group(2).strip(),
                "away": match.group(3).strip(),
            }
    return {}


def fixture_venue(manifest: dict[str, Any]) -> str | None:
    for key in ("venue", "stadium"):
        value = str(manifest.get(key) or "").strip()
        if value and not value.upper().startswith("TBD"):
            return value
    entry = fixture_entry_for_manifest(manifest)
    if entry is None:
        return None
    value = str(entry.get("venue") or "").strip()
    if value and not value.upper().startswith("TBD"):
        return value
    return None


def direct_request_trace(manifest: dict[str, Any], manifest_path: Path) -> tuple[str, dict[str, Any] | None]:
    direct_id = str(manifest.get("direct_request_id") or "N/A")
    path = resolve_path(manifest.get("direct_request_path"), manifest_path)
    if path and path.exists():
        try:
            record = load_json(path)
            return str(record.get("direct_request_id") or direct_id), record
        except Exception:
            return direct_id, None
    return direct_id, None


def build_summary(manifest_path: str | Path, report_path: str | Path | None = None, max_chars: int = 3900) -> str:
    manifest_path = Path(manifest_path)
    manifest = load_json(manifest_path)
    artifacts = load_artifacts(manifest, manifest_path)
    contract = report_contract.validate_manifest(manifest, manifest_path)
    guard = None
    if report_path:
        guard = report_guard.validate_report(Path(report_path))

    direct_id, direct_record = direct_request_trace(manifest, manifest_path)
    report_path_obj = Path(report_path) if report_path else None
    fixture_entry = fixture_entry_for_manifest(manifest)
    report_identity = report_title_identity(report_path_obj)
    match_id = str(first_present(manifest.get("match_id"), report_identity.get("match_id"), "UNKNOWN"))
    home = str(
        first_present(
            manifest.get("home"),
            manifest.get("home_team"),
            fixture_entry.get("home") if fixture_entry else None,
            report_identity.get("home"),
            "home TBD",
        )
    )
    away = str(
        first_present(
            manifest.get("away"),
            manifest.get("away_team"),
            fixture_entry.get("away") if fixture_entry else None,
            report_identity.get("away"),
            "away TBD",
        )
    )
    title_status = str(manifest.get("final_status") or "unknown").upper()
    completeness = str(manifest.get("report_completeness") or "").upper()
    if completeness == "PARTIAL":
        title_status = "PARTIAL / " + title_status

    lines: list[str] = [f"WC26 {match_id} {home} vs {away} — {title_status}"]
    lines.append("")
    lines.append("① 比赛事实")
    lines.append(f"- 开球: {manifest.get('kickoff_utc', 'TBD')}")
    lines.append(f"- 赛事: {manifest.get('stage', 'TBD')} | {manifest.get('group', 'TBD')} | matchday={manifest.get('matchday', 'TBD')}")
    lines.append(f"- 场地: {fixture_venue(manifest) or 'TBD'}")
    lines.append(
        f"- 窗口: {first_present(manifest.get('window'), manifest.get('timing_class'), 'N/A')} "
        f"| source_quality={manifest.get('source_quality', 'N/A')} cap={manifest.get('source_quality_cap', 'N/A')}"
    )
    if direct_record:
        lines.append(
            f"- direct_request: {direct_id} | status={direct_record.get('status', 'N/A')} "
            f"| msg={direct_record.get('message_id', 'N/A')}"
        )
    else:
        lines.append(f"- direct_request: {direct_id}")

    lines.append("")
    lines.append("② 数据与契约")
    lines.append(f"- report_contract: {'PASS' if contract.get('valid') else 'FAIL'} | report_guard: {'PASS' if guard and guard.get('valid') else ('未运行' if guard is None else 'FAIL')}")
    lines.append(f"- actionable_allowed: {contract.get('actionable_allowed', False)} | review_required={manifest.get('review_required', 'N/A')}")
    if not contract.get("valid"):
        errors = contract.get("errors") if isinstance(contract.get("errors"), list) else []
        lines.append("- BLOCKED: " + "; ".join(str(item) for item in errors[:4]))

    crossbook = artifacts.get("path_a_crossbook", {})
    role_engine = artifacts.get("role_engine", {})
    path_c = artifacts.get("path_c_consistency", {})
    summary = crossbook.get("summary") if isinstance(crossbook.get("summary"), dict) else {}
    h2h, _h2h_probs = market_probs(crossbook, "h2h")
    spreads, spread_probs = market_probs(crossbook, "spreads")
    totals, totals_probs = market_probs(crossbook, "totals")
    entry = manifest.get("entry_price") if isinstance(manifest.get("entry_price"), dict) else {}
    report_rows = report_market_rows(report_path)

    lines.append("")
    lines.append("③ 盘口快照")
    if h2h:
        lines.append(
            f"- H2H: status={h2h.get('status', 'N/A')} anchor={h2h.get('sharp_anchor', 'N/A')} "
            f"quotes={h2h.get('quotes_scanned', 'N/A')} edges={len(h2h.get('edges', [])) if isinstance(h2h.get('edges'), list) else 'N/A'}"
        )
    ah_line = format_report_market_rows(report_rows.get("AH", []), "AH")
    totals_line = format_report_market_rows(report_rows.get("Totals", []), "Totals")
    if not ah_line:
        ah_line = format_fair_market_line(spread_probs, "AH")
    if not totals_line:
        totals_line = format_fair_market_line(totals_probs, "Totals")
    if ah_line:
        lines.append(f"- AH: {ah_line}")
    if totals_line:
        lines.append(f"- Totals: {totals_line}")
    if spread_probs:
        lines.append(f"- AH 去水: {format_probs(spread_probs)}")
    if totals_probs:
        lines.append(f"- Totals 去水: {format_probs(totals_probs)}")

    raw_actionable = first_present(summary.get("raw_actionable_count"), summary.get("actionable_count"), 0)
    relay_actionable = raw_actionable if contract.get("actionable_allowed") else 0
    best_edge = summary.get("best_actionable_edge") or summary.get("best_edge")
    lines.append("")
    lines.append("④ Path A 跨书商扫描")
    lines.append(
        f"- quotes={summary.get('quotes_scanned', 'N/A')} | edges={summary.get('edge_count', 'N/A')} "
        f"| noise={summary.get('noise_edge_count', 'N/A')} | raw_actionable={raw_actionable} relay_actionable={relay_actionable}"
    )
    if isinstance(best_edge, dict):
        lines.append(
            f"- 最优偏差: {best_edge.get('book', 'N/A')} {best_edge.get('market_key', 'N/A')} "
            f"{best_edge.get('outcome', 'N/A')} @{best_edge.get('offered_odds', 'N/A')} "
            f"vs fair {best_edge.get('fair_odds', 'N/A')} | EV={fmt_ev(best_edge.get('ev_shin'))} "
            f"| band={best_edge.get('ev_band', 'N/A')} | relay_actionable={bool(best_edge.get('actionable') is True and contract.get('actionable_allowed'))}"
        )
    lines.append(
        f"- AH same-line: status={spreads.get('status', 'N/A')} quotes={spreads.get('quotes_scanned', 'N/A')} edges={len(spreads.get('edges', [])) if isinstance(spreads.get('edges'), list) else 'N/A'}"
    )
    lines.append(
        f"- Totals same-line: status={totals.get('status', 'N/A')} quotes={totals.get('quotes_scanned', 'N/A')} edges={len(totals.get('edges', [])) if isinstance(totals.get('edges'), list) else 'N/A'}"
    )

    model = manifest.get("p_model") if isinstance(manifest.get("p_model"), dict) else {}
    lines.append("")
    lines.append("⑤ 模型纪律")
    if model:
        lines.append(
            f"- Dixon-Coles: {home} {fmt_pct(model.get('home'))} / 平 {fmt_pct(model.get('draw'))} / {away} {fmt_pct(model.get('away'))}"
        )
    p_adj = manifest.get("p_adj") if isinstance(manifest.get("p_adj"), dict) else {}
    note = p_adj.get("_note") if isinstance(p_adj, dict) else None
    lines.append(f"- p_adj: {note or '必须由 p_market / ledger 证明，不能裸用模型差异'}")

    mechanism = artifacts.get("mechanism_audit", {})
    decisions = mechanism.get("hypothesis_decisions") if isinstance(mechanism.get("hypothesis_decisions"), list) else []
    blocking = mechanism.get("blocking_mechanisms") if isinstance(mechanism.get("blocking_mechanisms"), list) else []
    lines.append("")
    lines.append("⑥ 博弈读盘")
    role_bullets = role_engine.get("telegram_bullets_zh") if isinstance(role_engine.get("telegram_bullets_zh"), list) else []
    if role_bullets:
        for bullet in role_bullets[:5]:
            lines.append(f"- {bullet}")
    else:
        if raw_actionable and not contract.get("actionable_allowed"):
            lines.append("- 庄家意图: PARTIAL — sharp anchor 可识别 H2H 价格散布，但 cap=C/partial 把它压成复核项，不允许 relay 成下注。 [path_a_crossbook]")
        elif raw_actionable:
            lines.append("- 庄家意图: WATCH — Path A 有算术候选，仍需人工复核执行条件。 [path_a_crossbook]")
        else:
            lines.append("- 庄家意图: REFUTED — Path A 未发现可操作跨书商偏差。 [path_a_crossbook]")
        if spread_probs and totals_probs:
            lines.append(
                f"- 散户心理: AH/Totals 同线去水接近均衡：{format_probs(spread_probs)}；{format_probs(totals_probs)}。 [asian_handicap,totals]"
            )
        if "path_c_consistency" in blocking:
            lines.append("- AI滞后/陷阱盘: BLOCKED — 缺 Path C，一致性三角未跑，不能给出陷阱或 AI 滞后结论。 [mechanism_audit]")
    for decision in decisions[:4]:
        if not isinstance(decision, dict):
            continue
        name = str(decision.get("decision") or "N/A")
        if name == "CONFIRMED_ACTIONABLE" and not contract.get("actionable_allowed"):
            name = "CONFIRMED_ACTIONABLE(raw_only; relay_blocked)"
        lines.append(
            f"- 裁决: {name} | {decision.get('subject', 'N/A')} | {decision.get('evidence', 'N/A')}"
            + (f" | EV={fmt_ev(decision.get('ev_shin'))}" if decision.get("ev_shin") is not None else "")
        )

    profile_lines = market_profile_lines(path_c.get("market_profile") if isinstance(path_c, dict) else None)
    if profile_lines:
        lines.append("")
        lines.append("⑦ Path C 市场画像")
        lines.extend(profile_lines)

    lines.append("")
    lines.append("⑧ 最终裁定" if profile_lines else "⑦ 最终裁定")
    lines.append(f"- 结论: {title_status} / NO PLAY")
    lines.append("- 原因: relay_actionable=0；缺源项已显式降级；actionable 候选只代表人工复核，不自动下注。")
    skipped = manifest.get("skipped_sections") if isinstance(manifest.get("skipped_sections"), list) else []
    if skipped:
        lines.append("- 主要缺口: " + "；".join(str(item.get("gate")) for item in skipped[:4] if isinstance(item, dict)))
    if report_path:
        lines.append(f"- 完整报告: {report_path}")
    lines.append(f"- 赛后回链: {direct_id}")

    return trim("\n".join(lines), max_chars)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate artifact-backed WC26 rich Telegram summary")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--report")
    parser.add_argument("--max-chars", type=int, default=3900)
    args = parser.parse_args()
    print(build_summary(args.manifest, args.report, args.max_chars))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
