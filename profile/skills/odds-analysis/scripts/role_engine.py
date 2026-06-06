#!/usr/bin/env python3
"""Generate deterministic WC26 game-theory role conclusions.

The role engine is a report-content generator, not a betting trigger. It reads
numeric artifacts and emits auditable "bookmaker / public / AI / trap /
efficiency" conclusions with evidence IDs and artifact sources.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENGINE_CONTRACT = "wc26.role_engine.v1"
ENGINE_VERSION = "deterministic_v1"
ROLE_DECISIONS = {"CONFIRMED", "REFUTED", "DIAGNOSTIC_ONLY", "BLOCKED", "SUSPECT"}
ROLE_ACTIONABILITY = {"never_actionable", "supports_path_a", "contradicts_path_a"}
ROLE_ORDER = ["bookmaker_intent", "public_bias", "ai_lag", "trap_risk", "market_efficiency"]
ROLE_LABELS_ZH = {
    "bookmaker_intent": "庄家意图",
    "public_bias": "散户心理",
    "ai_lag": "AI 滞后",
    "trap_risk": "陷阱盘",
    "market_efficiency": "市场效率",
}


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def stable_slug(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def resolve_path(raw_path: str, manifest_path: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (manifest_path.parent / path).resolve()


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
            str(payload.get("script", "")) if payload else "",
        ]
    ).lower()
    caps: set[str] = set()
    if "no_vig" in raw or "scalar_market" in raw or "devig_1x2" in raw:
        caps.add("devig_1x2")
    if "cross_book" in raw or "crossbook" in raw:
        caps.add("path_a_crossbook")
    if "model" in raw or "dixon_coles" in raw:
        caps.add("path_b_model_diagnostic")
    if "consistency_triangle" in raw or "path_c" in raw:
        caps.add("path_c_consistency")
    if "role_engine" in raw:
        caps.add("role_engine")
    return caps


def load_artifacts(manifest: dict[str, Any], manifest_path: Path) -> dict[str, tuple[dict[str, Any], dict[str, Any] | None]]:
    found: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] = {}
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        payload = None
        raw_path = str(artifact.get("path", "")).strip()
        if raw_path:
            path = resolve_path(raw_path, manifest_path)
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                loaded = None
            payload = loaded if isinstance(loaded, dict) else None
        for cap in artifact_caps(artifact, payload):
            found.setdefault(cap, (artifact, payload))
    return found


def team_names(manifest: dict[str, Any]) -> tuple[str, str]:
    match = manifest.get("match") if isinstance(manifest.get("match"), dict) else {}
    home = str(manifest.get("home") or manifest.get("home_team") or match.get("home") or match.get("home_team") or "home")
    away = str(manifest.get("away") or manifest.get("away_team") or match.get("away") or match.get("away_team") or "away")
    return home, away


def side_to_label(side: str, home: str, away: str) -> str:
    return {"home": home, "away": away, "draw": "平局"}.get(side, side)


def outcome_to_side(outcome: Any, home: str, away: str) -> str | None:
    raw = str(outcome or "").lower()
    if not raw:
        return None
    home_key = home.lower()
    away_key = away.lower()
    if raw in {"home", "h"} or home_key in raw:
        return "home"
    if raw in {"away", "a"} or away_key in raw:
        return "away"
    if raw in {"draw", "tie", "平局"}:
        return "draw"
    return None


def artifact_source(capability: str, artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, Any] | None:
    entry = artifacts.get(capability)
    if not entry:
        return None
    artifact, payload = entry
    return {
        "capability": capability,
        "artifact_id": artifact.get("artifact_id") or (payload or {}).get("artifact_id"),
        "path": artifact.get("path"),
        "script": artifact.get("script") or (payload or {}).get("script"),
    }


def sources_for(capabilities: list[str], artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> list[dict[str, Any]]:
    sources = []
    for cap in capabilities:
        source = artifact_source(cap, artifacts)
        if source:
            sources.append(source)
    return sources


def evidence_number(name: str, value: Any, unit: str = "", source: str = "") -> dict[str, Any]:
    payload = {"name": name, "value": value}
    if unit:
        payload["unit"] = unit
    if source:
        payload["source"] = source
    return payload


def role_conclusion(
    index: int,
    role: str,
    decision: str,
    actionability: str,
    hypothesis_zh: str,
    interpretation_zh: str,
    trigger_artifacts: list[str],
    artifact_sources: list[dict[str, Any]],
    evidence_numbers: list[dict[str, Any]],
    confidence: str = "medium",
) -> dict[str, Any]:
    if decision not in ROLE_DECISIONS:
        decision = "BLOCKED"
    if actionability not in ROLE_ACTIONABILITY:
        actionability = "never_actionable"
    return {
        "evidence_id": f"role:{role}:{index:03d}",
        "role": role,
        "role_label_zh": ROLE_LABELS_ZH[role],
        "decision": decision,
        "actionability": actionability,
        "confidence": confidence,
        "hypothesis_zh": hypothesis_zh,
        "interpretation_zh": interpretation_zh,
        "trigger_artifacts": trigger_artifacts,
        "artifact_sources": artifact_sources,
        "evidence_numbers": evidence_numbers,
    }


def devig_probs(artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, float]:
    payload = (artifacts.get("devig_1x2") or ({}, None))[1] or {}
    probs = payload.get("no_vig_probabilities")
    if isinstance(probs, list) and len(probs) >= 3:
        return {"home": float(probs[0]), "draw": float(probs[1]), "away": float(probs[2])}
    methods = payload.get("devig_methods") if isinstance(payload.get("devig_methods"), dict) else {}
    shin = methods.get("shin")
    if isinstance(shin, list) and len(shin) >= 3:
        return {"home": float(shin[0]), "draw": float(shin[1]), "away": float(shin[2])}
    return {}


def crossbook_h2h_probs(crossbook: dict[str, Any], home: str, away: str) -> dict[str, float]:
    markets = crossbook.get("markets") if isinstance(crossbook.get("markets"), dict) else {}
    h2h = markets.get("h2h") if isinstance(markets, dict) else {}
    if not isinstance(h2h, dict) or h2h.get("status") != "ok":
        return {}
    fair = h2h.get("fair_probs") if isinstance(h2h.get("fair_probs"), dict) else {}
    method = str(h2h.get("devig_primary") or "shin")
    probs = fair.get(method) if isinstance(fair.get(method), dict) else fair.get("shin")
    if not isinstance(probs, dict):
        return {}
    mapped: dict[str, float] = {}
    for outcome, value in probs.items():
        side = outcome_to_side(outcome, home, away)
        if not side:
            continue
        try:
            mapped[side] = float(value)
        except Exception:
            continue
    return mapped


def model_probs(artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, float]:
    payload = (artifacts.get("path_b_model_diagnostic") or ({}, None))[1] or {}
    raw = payload.get("p_model") if isinstance(payload.get("p_model"), dict) else {}
    result = {}
    for key in ("home", "draw", "away"):
        try:
            result[key] = float(raw[key])
        except Exception:
            pass
    return result


def crossbook_payload(artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, Any]:
    return (artifacts.get("path_a_crossbook") or ({}, None))[1] or {}


def path_c_payload(artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, Any]:
    return (artifacts.get("path_c_consistency") or ({}, None))[1] or {}


def h2h_quotes_by_side(crossbook: dict[str, Any], home: str, away: str) -> dict[str, list[dict[str, Any]]]:
    market = (crossbook.get("markets") or {}).get("h2h") if isinstance(crossbook.get("markets"), dict) else {}
    quotes = market.get("quotes") if isinstance(market, dict) else []
    by_side = {"home": [], "draw": [], "away": []}
    for quote in quotes if isinstance(quotes, list) else []:
        if not isinstance(quote, dict):
            continue
        side = outcome_to_side(quote.get("outcome"), home, away)
        if side:
            by_side[side].append(quote)
    return by_side


def crossbook_summary(crossbook: dict[str, Any]) -> dict[str, Any]:
    summary = crossbook.get("summary") or crossbook.get("scan_summary") or {}
    return summary if isinstance(summary, dict) else {}


def role_context(manifest: dict[str, Any], artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, Any]:
    home, away = team_names(manifest)
    model = model_probs(artifacts)
    crossbook = crossbook_payload(artifacts)
    devig_market_probs = devig_probs(artifacts)
    market_probs = devig_market_probs or crossbook_h2h_probs(crossbook, home, away)
    market_prob_source = "devig_1x2" if devig_market_probs else ("path_a_crossbook_h2h" if market_probs else "")
    path_c = path_c_payload(artifacts)
    summary = crossbook_summary(crossbook)
    favorite_side = max((side for side in ("home", "away")), key=lambda side: market_probs.get(side, 0.0), default="home")
    favorite_prob = market_probs.get(favorite_side, 0.0)
    favorite_fair_odds = round(1.0 / favorite_prob, 3) if favorite_prob else None
    quotes_by_side = h2h_quotes_by_side(crossbook, home, away)
    favorite_quotes = quotes_by_side.get(favorite_side, [])
    soft_favorite = [
        quote
        for quote in favorite_quotes
        if str(quote.get("book_tier", "soft")).lower() == "soft" and isinstance(quote.get("offered_odds"), (int, float))
    ]
    soft_avg = round(sum(float(q["offered_odds"]) for q in soft_favorite) / len(soft_favorite), 3) if soft_favorite else None
    soft_discount = round((soft_avg - favorite_fair_odds), 3) if soft_avg is not None and favorite_fair_odds is not None else None
    deltas = {
        side: model.get(side, 0.0) - market_probs.get(side, 0.0)
        for side in ("home", "draw", "away")
        if side in model and side in market_probs
    }
    positive_sides = [side for side, delta in deltas.items() if delta > 0]
    model_delta_side = max(positive_sides, key=lambda side: deltas[side]) if positive_sides else (max(deltas, key=lambda side: abs(deltas[side])) if deltas else None)
    best_edge = summary.get("best_actionable_edge") or summary.get("best_edge") or summary.get("best_noise_edge") or {}
    discrepancy = path_c.get("discrepancy") if isinstance(path_c.get("discrepancy"), dict) else {}
    signal = path_c.get("signal") if isinstance(path_c.get("signal"), dict) else {}
    return {
        "home": home,
        "away": away,
        "market_probs": market_probs,
        "model_probs": model,
        "favorite_side": favorite_side,
        "favorite_label": side_to_label(favorite_side, home, away),
        "favorite_fair_odds": favorite_fair_odds,
        "soft_favorite_avg_odds": soft_avg,
        "soft_favorite_discount": soft_discount,
        "soft_favorite_quote_count": len(soft_favorite),
        "model_delta_side": model_delta_side,
        "model_delta": deltas.get(model_delta_side) if model_delta_side else None,
        "model_delta_label": side_to_label(model_delta_side or "", home, away),
        "summary": summary,
        "best_edge": best_edge if isinstance(best_edge, dict) else {},
        "path_c_discrepancy_pp": discrepancy.get("pp"),
        "path_c_signal_type": signal.get("type"),
        "path_c_signal_strength": signal.get("strength"),
        "has_path_c": bool(path_c),
        "has_crossbook": bool(crossbook),
        "has_devig": bool(devig_market_probs),
        "has_market_probs": bool(market_probs),
        "market_prob_source": market_prob_source,
        "has_model": bool(model),
        "source_quality_cap": manifest.get("source_quality_cap"),
        "report_completeness": manifest.get("report_completeness"),
    }


def bookmaker_intent(ctx: dict[str, Any], artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, Any]:
    sources = sources_for(["path_a_crossbook", "devig_1x2", "path_c_consistency"], artifacts)
    if not ctx["has_crossbook"] or not ctx["has_market_probs"]:
        return role_conclusion(1, "bookmaker_intent", "BLOCKED", "never_actionable", "判断 sharp 端是否在保护某侧", "缺少 Path A 或可用 sharp 概率，无法判断庄家意图。", ["path_a_crossbook", "devig_1x2"], sources, [evidence_number("missing_core_artifact", True)])
    if not ctx["has_path_c"]:
        text = (
            f"sharp H2H anchor 可识别{ctx['favorite_label']}价格结构，但缺 Path C 且 "
            f"source_quality_cap={ctx.get('source_quality_cap')}；只能判断为庄家价格纪律的 partial 信号，不允许 relay 成下注。"
        )
        return role_conclusion(
            1,
            "bookmaker_intent",
            "DIAGNOSTIC_ONLY",
            "never_actionable",
            "从 sharp/soft 价格形态判断庄家真实意图",
            text,
            ["path_a_crossbook", "path_c_consistency"],
            sources,
            [
                evidence_number("market_prob_source", ctx.get("market_prob_source"), source="path_a_crossbook"),
                evidence_number("favorite_fair_odds", ctx.get("favorite_fair_odds"), "decimal", ctx.get("market_prob_source") or "market"),
                evidence_number("path_c_missing", True, source="path_c_consistency"),
            ],
        )
    signal_none = ctx.get("path_c_signal_type") in (None, "", "none")
    if ctx.get("soft_favorite_discount") is not None and ctx["soft_favorite_discount"] < -0.03 and signal_none:
        text = f"soft 书压低{ctx['favorite_label']}，但 sharp 公平价与三角结构自洽；庄家更像是在维持价格纪律，而不是追随热门叙事。"
        decision = "CONFIRMED"
    else:
        text = "跨书商与三角没有形成清晰的庄家保护方向，只能保留为诊断。"
        decision = "DIAGNOSTIC_ONLY"
    return role_conclusion(
        1,
        "bookmaker_intent",
        decision,
        "never_actionable",
        "从 sharp/soft 价格形态和三角自洽度判断庄家真实意图",
        text,
        ["path_a_crossbook", "devig_1x2", "path_c_consistency"],
        sources,
        [
            evidence_number("favorite_fair_odds", ctx.get("favorite_fair_odds"), "decimal", "devig_1x2"),
            evidence_number("soft_favorite_avg_odds", ctx.get("soft_favorite_avg_odds"), "decimal", "path_a_crossbook"),
            evidence_number("path_c_discrepancy_pp", ctx.get("path_c_discrepancy_pp"), "pp", "path_c_consistency"),
        ],
    )


def public_bias(ctx: dict[str, Any], artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, Any]:
    sources = sources_for(["path_a_crossbook", "devig_1x2"], artifacts)
    if not ctx["has_crossbook"] or not ctx["has_market_probs"]:
        return role_conclusion(1, "public_bias", "BLOCKED", "never_actionable", "识别散户拥挤方向", "缺少 Path A 或可用 sharp 概率，无法判断散户心理。", ["path_a_crossbook", "devig_1x2"], sources, [evidence_number("missing_core_artifact", True)])
    confirmed = (
        (ctx.get("soft_favorite_discount") is not None and ctx["soft_favorite_discount"] < -0.03 and ctx.get("soft_favorite_quote_count", 0) >= 2)
        or (ctx.get("market_probs", {}).get(ctx.get("favorite_side"), 0.0) >= 0.9 and ctx.get("soft_favorite_quote_count", 0) >= 2)
    )
    decision = "CONFIRMED" if confirmed else "REFUTED"
    if confirmed and ctx.get("soft_favorite_discount") is not None:
        text = f"散户/soft 端更愿意买{ctx['favorite_label']}：soft 平均价比 sharp 公平价低 {abs(ctx['soft_favorite_discount']):.3f}。这说明热门叙事拥挤，但它本身不产生下注。"
    elif confirmed:
        text = f"{ctx['favorite_label']}已是 90%+ sharp 概率的超大热门，强弱叙事明显拥挤；但 AH/Totals 仍需独立裁决，叙事本身不产生下注。"
    else:
        text = "soft 书没有系统性压低热门侧，散户拥挤信号不成立。"
    return role_conclusion(
        1,
        "public_bias",
        decision,
        "never_actionable",
        "判断大众叙事是否挤向热门侧",
        text,
        ["path_a_crossbook", "devig_1x2"],
        sources,
        [
            evidence_number("soft_favorite_quote_count", ctx.get("soft_favorite_quote_count"), "quotes", "path_a_crossbook"),
            evidence_number("soft_favorite_discount_vs_fair", ctx.get("soft_favorite_discount"), "decimal_odds", "path_a_crossbook"),
            evidence_number("favorite_market_probability", ctx.get("market_probs", {}).get(ctx.get("favorite_side")), "probability", "devig_1x2"),
        ],
    )


def ai_lag(ctx: dict[str, Any], artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, Any]:
    sources = sources_for(["path_b_model_diagnostic", "devig_1x2", "path_a_crossbook", "path_c_consistency"], artifacts)
    if not ctx["has_model"] or not ctx["has_market_probs"]:
        return role_conclusion(1, "ai_lag", "BLOCKED", "never_actionable", "检查模型是否发现市场慢半拍", "缺少模型或可用 sharp 概率，无法判断 AI 滞后。", ["path_b_model_diagnostic", "devig_1x2"], sources, [evidence_number("missing_core_artifact", True)])
    delta = ctx.get("model_delta")
    side_label = ctx.get("model_delta_label")
    actionables = int(ctx.get("summary", {}).get("actionable_count") or 0)
    path_c_quiet = abs(float(ctx.get("path_c_discrepancy_pp") or 0.0)) < 5.0 and ctx.get("path_c_signal_type") in (None, "", "none")
    if delta is not None and abs(delta) >= 0.04 and actionables == 0 and path_c_quiet:
        decision = "DIAGNOSTIC_ONLY"
        text = f"模型明显偏向{side_label}（相对市场 {delta*100:+.1f}pp），但 Path A 无 actionable，Path C 也无结构信号；这只是 AI/模型分歧，不是市场慢半拍证据。"
    elif delta is not None and abs(delta) >= 0.04:
        decision = "SUSPECT"
        text = f"模型与市场在{side_label}方向分歧 {delta*100:+.1f}pp，且其他机制未完全反证，需要人工复核。"
    else:
        decision = "REFUTED"
        text = "模型与市场没有足够大的方向性分歧，AI 滞后假设不成立。"
    return role_conclusion(
        1,
        "ai_lag",
        decision,
        "never_actionable",
        "模型是否发现 sharp 市场尚未吸收的信息",
        text,
        ["path_b_model_diagnostic", "devig_1x2", "path_a_crossbook", "path_c_consistency"],
        sources,
        [
            evidence_number("model_market_delta", delta, "probability", "path_b_model_diagnostic"),
            evidence_number("path_a_actionable_count", actionables, "count", "path_a_crossbook"),
            evidence_number("path_c_discrepancy_pp", ctx.get("path_c_discrepancy_pp"), "pp", "path_c_consistency"),
        ],
    )


def trap_risk(ctx: dict[str, Any], artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, Any]:
    sources = sources_for(["path_a_crossbook", "path_c_consistency"], artifacts)
    if not ctx["has_crossbook"]:
        return role_conclusion(1, "trap_risk", "BLOCKED", "never_actionable", "判断看似便宜的价格是否是陷阱", "缺少 Path A artifact，无法判断陷阱盘。", ["path_a_crossbook"], sources, [evidence_number("missing_path_a", True)])
    best = ctx.get("best_edge") or {}
    ev = best.get("ev_shin")
    actionables = int(ctx.get("summary", {}).get("actionable_count") or 0)
    path_c_pp = float(ctx.get("path_c_discrepancy_pp") or 0.0)
    outcome = str(best.get("outcome") or "unknown")
    if actionables > 0 and not ctx["has_path_c"]:
        decision = "BLOCKED"
        actionability = "never_actionable"
        text = f"{outcome} 出现 raw 正 EV 候选，但缺 Path C 一致性三角，不能判断它是陷阱、漏洞还是软书噪声。"
    elif actionables > 0 and abs(path_c_pp) >= 5.0:
        decision = "SUSPECT"
        actionability = "contradicts_path_a"
        text = f"{outcome} 出现可下注错价，但 Path C 偏差 {path_c_pp:+.1f}pp 与其冲突，按陷阱风险降级复核。"
    elif ev is not None and float(ev) > 0 and actionables == 0:
        decision = "REFUTED"
        actionability = "never_actionable"
        text = f"{outcome} 有 +{float(ev)*100:.1f}% 表面便宜，但低于 actionable 门槛，且 Path C 无反向强信号；当前更像噪声，不是陷阱盘。"
    else:
        decision = "REFUTED"
        actionability = "never_actionable"
        text = "没有正 EV 候选或三角冲突，陷阱盘假设不成立。"
    return role_conclusion(
        1,
        "trap_risk",
        decision,
        actionability,
        "检查 Path A 便宜价是否被三角结构反证",
        text,
        ["path_a_crossbook", "path_c_consistency"],
        sources,
        [
            evidence_number("best_edge_ev_shin", ev, "ev", "path_a_crossbook"),
            evidence_number("path_a_actionable_count", actionables, "count", "path_a_crossbook"),
            evidence_number("path_c_discrepancy_pp", ctx.get("path_c_discrepancy_pp"), "pp", "path_c_consistency"),
        ],
    )


def market_efficiency(ctx: dict[str, Any], artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, Any]:
    sources = sources_for(["path_a_crossbook", "devig_1x2", "path_c_consistency"], artifacts)
    if not ctx["has_crossbook"] or not ctx["has_market_probs"]:
        return role_conclusion(1, "market_efficiency", "BLOCKED", "never_actionable", "判断当前市场是否自洽", "缺少 Path A 或可用 sharp 概率，无法判断市场效率。", ["path_a_crossbook", "devig_1x2"], sources, [evidence_number("missing_core_artifact", True)])
    actionables = int(ctx.get("summary", {}).get("actionable_count") or 0)
    path_c_pp = float(ctx.get("path_c_discrepancy_pp") or 0.0)
    if not ctx["has_path_c"]:
        decision = "DIAGNOSTIC_ONLY"
        text = "Path A 可运行，但缺 Path C；当前只能说跨书商局部有效率，不能证明 1X2/AH/Totals 全市场自洽。"
    elif actionables == 0 and abs(path_c_pp) < 5.0:
        decision = "CONFIRMED"
        text = "Path A 没有 actionable，Path C 偏差小于 5pp，三法 devig 可用；当前更像高效市场，不是可下注裂缝。"
    else:
        decision = "DIAGNOSTIC_ONLY"
        text = "市场出现局部裂缝或三角偏差，效率结论只能作为诊断，不能单独下注。"
    return role_conclusion(
        1,
        "market_efficiency",
        decision,
        "never_actionable",
        "综合 Path A、devig 与 Path C 判断市场是否自洽",
        text,
        ["path_a_crossbook", "devig_1x2", "path_c_consistency"],
        sources,
        [
            evidence_number("path_a_actionable_count", actionables, "count", "path_a_crossbook"),
            evidence_number("path_a_noise_edge_count", ctx.get("summary", {}).get("noise_edge_count"), "count", "path_a_crossbook"),
            evidence_number("path_c_abs_discrepancy_pp", abs(path_c_pp), "pp", "path_c_consistency"),
        ],
    )


def build_role_artifact(manifest: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    artifacts = load_artifacts(manifest, manifest_path)
    ctx = role_context(manifest, artifacts)
    conclusions = [
        bookmaker_intent(ctx, artifacts),
        public_bias(ctx, artifacts),
        ai_lag(ctx, artifacts),
        trap_risk(ctx, artifacts),
        market_efficiency(ctx, artifacts),
    ]
    manifest_id = str(manifest.get("manifest_id") or manifest_path.name)
    match_id = manifest.get("match_id") or (manifest.get("match") or {}).get("match_id") or "UNKNOWN"
    artifact_id = f"role_engine:{match_id}:{stable_slug(manifest_id + '|' + ENGINE_VERSION)}"
    return {
        "artifact_id": artifact_id,
        "artifact_type": "role_engine",
        "artifact_kind": "role_engine",
        "engine_contract": ENGINE_CONTRACT,
        "engine_version": ENGINE_VERSION,
        "script": "role_engine.py",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_manifest_path": str(manifest_path),
        "source_manifest_id": manifest.get("manifest_id"),
        "match_id": match_id,
        "home": ctx["home"],
        "away": ctx["away"],
        "role_order": ROLE_ORDER,
        "decision_enums": sorted(ROLE_DECISIONS),
        "actionability_enums": sorted(ROLE_ACTIONABILITY),
        "role_conclusions": conclusions,
        "telegram_bullets_zh": [
            f"{item['role_label_zh']}: {item['interpretation_zh']} ({item['evidence_id']})"
            for item in conclusions
            if item.get("decision") != "BLOCKED"
        ],
    }


def patch_manifest(manifest_path: Path, output_path: Path, artifact: dict[str, Any]) -> None:
    manifest = load_json(manifest_path)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
        manifest["artifacts"] = artifacts
    artifacts[:] = [
        item
        for item in artifacts
        if not (isinstance(item, dict) and ("role_engine" in item.get("provides", []) or item.get("artifact_type") == "role_engine"))
    ]
    artifacts.append(
        {
            "artifact_id": artifact["artifact_id"],
            "artifact_type": "role_engine",
            "script": "role_engine.py",
            "path": str(output_path),
            "provides": ["role_engine"],
        }
    )
    gates = manifest.get("analysis_gates")
    if isinstance(gates, dict):
        gates["role_engine"] = "pass"
    write_json(manifest_path, manifest)


def render_markdown_section(artifact: dict[str, Any]) -> str:
    lines = [
        "## 9B. 博弈读盘",
        "",
        f"role_engine_version: {artifact.get('engine_version', 'unknown')}",
        "role_engine_contract: wc26.role_engine.v1",
        "",
        "| 角色 | 裁决 | 影响 | 证据 | 读盘结论 |",
        "| --- | --- | --- | --- | --- |",
    ]
    conclusions = artifact.get("role_conclusions") if isinstance(artifact.get("role_conclusions"), list) else []
    for item in conclusions:
        if not isinstance(item, dict):
            continue
        lines.append(
            "| {role} | {decision} | {actionability} | {evidence_id} | {text} |".format(
                role=str(item.get("role_label_zh") or item.get("role") or "N/A").replace("|", "/"),
                decision=str(item.get("decision", "N/A")).replace("|", "/"),
                actionability=str(item.get("actionability", "N/A")).replace("|", "/"),
                evidence_id=str(item.get("evidence_id", "N/A")).replace("|", "/"),
                text=str(item.get("interpretation_zh", "N/A")).replace("|", "/"),
            )
        )
    lines.extend(
        [
            "",
            "role_engine_note: deterministic_v1 only reads artifacts; it does not create actionable plays by itself.",
            "",
        ]
    )
    return "\n".join(lines)


def patch_report(report_path: Path, artifact: dict[str, Any]) -> None:
    text = report_path.read_text(encoding="utf-8")
    section = render_markdown_section(artifact)
    marker = "## 9B. 博弈读盘"
    next_marker = "## 10. Final Decision"
    if marker in text:
        before, rest = text.split(marker, 1)
        if next_marker in rest:
            _old, after = rest.split(next_marker, 1)
            text = before.rstrip() + "\n\n" + section + next_marker + after
        else:
            text = before.rstrip() + "\n\n" + section
    elif next_marker in text:
        text = text.replace(next_marker, section + next_marker, 1)
    else:
        text = text.rstrip() + "\n\n" + section
    report_path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic WC26 role-engine artifact")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--patch-manifest", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--patch-report", action="store_true")
    args = parser.parse_args()

    manifest = load_json(args.manifest)
    artifact = build_role_artifact(manifest, args.manifest)
    write_json(args.output, artifact)
    if args.patch_manifest:
        patch_manifest(args.manifest, args.output, artifact)
    if args.patch_report:
        if args.report is None:
            raise SystemExit("--patch-report requires --report")
        patch_report(args.report, artifact)
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
