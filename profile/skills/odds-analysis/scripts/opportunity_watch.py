#!/usr/bin/env python3
"""Read-only WC26 opportunity watcher.

This is a sidecar, not a report generator. It reads already guarded per-match
artifacts, ranks green-lit edges, writes an opportunity board, and prints a
Telegram-safe alert only when an opportunity opens/changes/closes.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_WORKSPACE = Path(os.environ.get("WC26_WORKSPACE", "/hermesdata/worldcup-2026-handicap"))
DEFAULT_PROFILE_ROOT = SCRIPT_DIR.parents[2] if len(SCRIPT_DIR.parents) >= 3 else Path.cwd()
DEFAULT_STATE_NAME = "opportunity-watch.json"
KELLY_FRACTION = 0.25
MAX_STAKE_FRACTION = 0.02
TOTAL_EXPOSURE_FRACTION = 0.05
ALERT_MIN_STAKE_PCT = 0.05
FRESHNESS_DEFAULT_MINUTES = 120.0
FRESHNESS_LATE_MINUTES = 30.0
CONFIDENCE_BY_QUALITY = {
    "liquid_main": 0.90,
    "mid": 0.70,
    "thin_longshot": 0.40,
}
QUALITY_RANK = {"A": 3, "B": 2, "C": 1}
NOISE_BANDS = {"noise_lt_5pp", "noise"}
SUSPECT_BANDS = {"suspect", "strong_gt_13pp_suspect"}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


report_contract = load_module("report_contract", SCRIPT_DIR / "report_contract.py")


def utc_now_dt() -> datetime:
    override = os.environ.get("WC26_NOW_UTC")
    if override:
        return parse_time(override) or datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def utc_now() -> str:
    return utc_now_dt().isoformat().replace("+00:00", "Z")


def parse_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def resolve_path(raw: Any, manifest_path: Path) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = (manifest_path.parent / path).resolve()
    return path


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def cap_rank(value: Any) -> int:
    return QUALITY_RANK.get(str(value or "").upper(), 0)


def fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def fmt_stake_pct(value: float) -> str:
    return f"{value:.3f}%"


def normalize_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9.+-]+", "-", str(value or "").strip().lower()).strip("-")


def artifact_is_crossbook(artifact: dict[str, Any]) -> bool:
    raw = " ".join(
        [
            str(artifact.get("artifact_type", "")),
            str(artifact.get("artifact_kind", "")),
            str(artifact.get("script", "")),
            " ".join(str(item) for item in artifact.get("provides", []) if isinstance(artifact.get("provides"), list)),
            str(artifact.get("path", "")),
        ]
    ).lower()
    return "crossbook" in raw or "cross_book" in raw or "path_a_crossbook" in raw


def crossbook_path_from_manifest(manifest: dict[str, Any], manifest_path: Path) -> Path | None:
    for artifact in manifest.get("artifacts", []):
        if isinstance(artifact, dict) and artifact_is_crossbook(artifact):
            path = resolve_path(artifact.get("path"), manifest_path)
            if path and path.exists():
                return path
    return None


def source_time_from_crossbook(crossbook: dict[str, Any]) -> datetime | None:
    for key in ("scan_timestamp_utc", "captured_at_utc", "snapshot_at_utc", "created_at_utc"):
        parsed = parse_time(crossbook.get(key))
        if parsed is not None:
            return parsed
    input_snapshot = str(crossbook.get("input_snapshot") or "").strip()
    if input_snapshot:
        payload = load_json(Path(input_snapshot), {})
        if isinstance(payload, dict):
            for key in ("captured_at_utc", "created_at_utc", "snapshot_at_utc"):
                parsed = parse_time(payload.get(key))
                if parsed is not None:
                    return parsed
    return None


def freshness_limit_minutes(manifest: dict[str, Any]) -> float:
    raw = " ".join(
        str(manifest.get(key) or "")
        for key in ("window", "timing_class", "analysis_window", "phase")
    ).lower()
    if any(token in raw for token in ("t-60m", "t-45m", "t-75m", "t-90m", "lineup", "price_guard")):
        return FRESHNESS_LATE_MINUTES
    return FRESHNESS_DEFAULT_MINUTES


def edge_matches(a: dict[str, Any], b: dict[str, Any]) -> bool:
    fields = ("book", "market_key", "outcome", "offered_odds", "fair_odds")
    return all(str(a.get(field)) == str(b.get(field)) for field in fields if field in a or field in b)


def is_noise(edge: dict[str, Any]) -> bool:
    band = str(edge.get("ev_band") or "").lower()
    return band in NOISE_BANDS or band.startswith("noise")


def is_suspect(edge: dict[str, Any]) -> bool:
    band = str(edge.get("ev_band") or "").lower()
    return bool(edge.get("suspect")) or band in SUSPECT_BANDS


def qualifies_for_relay(edge: dict[str, Any], summary: dict[str, Any], contract: dict[str, Any]) -> bool:
    if not contract.get("valid") or not contract.get("actionable_allowed"):
        return False
    if not edge.get("survives_all_methods"):
        return False
    if is_noise(edge) or is_suspect(edge):
        return False
    try:
        if float(edge.get("ev_shin", 0.0)) < 0.05:
            return False
    except Exception:
        return False
    if edge.get("qualifies") is True or edge.get("relay_actionable") is True:
        return True
    best_qualified = summary.get("best_qualified_edge")
    if isinstance(best_qualified, dict) and edge_matches(edge, best_qualified):
        return True
    return False


def confidence_quality(edge: dict[str, Any]) -> str:
    market = str(edge.get("market_key") or "").lower()
    try:
        odds = float(edge.get("offered_odds"))
    except Exception:
        odds = math.inf
    try:
        prob = float(edge.get("sharp_fair_prob"))
    except Exception:
        prob = 0.0
    if odds >= 12.0 or prob <= 0.08:
        return "thin_longshot"
    if market in {"spreads", "totals"} and odds <= 3.5:
        return "liquid_main"
    if odds <= 5.0:
        return "mid"
    return "thin_longshot"


def kelly_full(prob: float, odds: float) -> float:
    if odds <= 1.0:
        return 0.0
    return max(0.0, (prob * odds - 1.0) / (odds - 1.0))


def correlation_key(match_id: str, edge: dict[str, Any]) -> str:
    market = normalize_token(edge.get("market_key"))
    outcome = normalize_token(edge.get("outcome"))
    if market == "totals":
        if outcome.startswith("over"):
            outcome = "over"
        elif outcome.startswith("under"):
            outcome = "under"
    return f"{match_id}|{market}|{outcome}"


def edge_key(manifest: dict[str, Any], edge: dict[str, Any]) -> str:
    payload = {
        "match_id": manifest.get("match_id"),
        "book": edge.get("book"),
        "market_key": edge.get("market_key"),
        "outcome": edge.get("outcome"),
        "line": edge.get("point") or edge.get("line"),
    }
    return "opp:" + stable_hash(payload)


def card_from_edge(
    manifest: dict[str, Any],
    manifest_path: Path,
    crossbook_path: Path,
    crossbook: dict[str, Any],
    edge: dict[str, Any],
    contract: dict[str, Any],
    age_minutes: float,
    max_age_minutes: float,
) -> dict[str, Any]:
    prob = float(edge.get("sharp_fair_prob"))
    odds = float(edge.get("offered_odds"))
    raw_ev = prob * odds - 1.0
    reported_ev = float(edge.get("ev_shin", raw_ev))
    quality = confidence_quality(edge)
    confidence = CONFIDENCE_BY_QUALITY[quality]
    robust_ev = raw_ev * confidence
    stake_fraction = min(kelly_full(prob, odds) * KELLY_FRACTION * confidence, MAX_STAKE_FRACTION)
    stake_pct = stake_fraction * 100.0
    if stake_pct >= 0.50:
        stake_band = "normal"
    elif stake_pct >= 0.10:
        stake_band = "small"
    elif stake_pct >= ALERT_MIN_STAKE_PCT:
        stake_band = "tiny_alert"
    else:
        stake_band = "footnote"
    ev_delta = abs(reported_ev - raw_ev)
    match_id = str(manifest.get("match_id") or "UNKNOWN")
    return {
        "opportunity_id": edge_key(manifest, edge),
        "match_id": match_id,
        "match": f"{manifest.get('home') or manifest.get('home_team') or 'home TBD'} vs {manifest.get('away') or manifest.get('away_team') or 'away TBD'}",
        "window": manifest.get("window") or manifest.get("timing_class"),
        "book": edge.get("book"),
        "market_key": edge.get("market_key"),
        "outcome": edge.get("outcome"),
        "offered_odds": odds,
        "fair_odds": edge.get("fair_odds"),
        "sharp_fair_prob": prob,
        "raw_ev": raw_ev,
        "reported_ev_shin": reported_ev,
        "ev_recompute_delta": ev_delta,
        "robust_ev": robust_ev,
        "confidence_quality": quality,
        "confidence": confidence,
        "suggested_stake_pct": stake_pct,
        "stake_band": stake_band,
        "survives_all_methods": bool(edge.get("survives_all_methods")),
        "ev_band": edge.get("ev_band"),
        "source_quality_cap": manifest.get("source_quality_cap"),
        "contract_valid": bool(contract.get("valid")),
        "contract_actionable_allowed": bool(contract.get("actionable_allowed")),
        "snapshot_age_minutes": round(age_minutes, 2),
        "snapshot_max_age_minutes": max_age_minutes,
        "freshness_status": "fresh" if age_minutes <= max_age_minutes else "stale",
        "input_snapshot": crossbook.get("input_snapshot"),
        "source_snapshot_id": crossbook.get("source_snapshot_id"),
        "crossbook_path": str(crossbook_path),
        "manifest_path": str(manifest_path),
        "report_path": manifest.get("report_path"),
        "correlation_key": correlation_key(match_id, edge),
        "entry_condition": f"下注前确认 {edge.get('book')} 仍提供 {edge.get('outcome')} @{odds}",
        "withdraw_if": "价格跌破 fair、sharp fair 恶化、或出现关键反向伤停/首发信息",
        "clv_hook": {
            "entry_price": odds,
            "entry_sharp_fair": edge.get("fair_odds"),
            "source_snapshot_id": crossbook.get("source_snapshot_id"),
            "input_snapshot": crossbook.get("input_snapshot"),
        },
    }


def latest_manifests(workspace: Path, lookback_hours: float, now: datetime) -> list[Path]:
    root = workspace / "reports" / "artifacts"
    if not root.exists():
        return []
    cutoff_seconds = lookback_hours * 3600.0
    candidates: list[Path] = []
    for path in root.glob("manifest-*.json"):
        try:
            age = now.timestamp() - path.stat().st_mtime
        except OSError:
            continue
        if age <= cutoff_seconds:
            candidates.append(path)
    return sorted(candidates)


def scan_manifest(path: Path, now: datetime) -> dict[str, Any]:
    manifest = load_json(path, {})
    if not isinstance(manifest, dict):
        return {"manifest_path": str(path), "status": "skip", "reason": "manifest_not_json"}
    cross_path = crossbook_path_from_manifest(manifest, path)
    if not cross_path:
        return {"manifest_path": str(path), "match_id": manifest.get("match_id"), "status": "skip", "reason": "missing_crossbook"}
    crossbook = load_json(cross_path, {})
    if not isinstance(crossbook, dict):
        return {"manifest_path": str(path), "match_id": manifest.get("match_id"), "status": "skip", "reason": "crossbook_not_json"}
    source_time = source_time_from_crossbook(crossbook)
    if source_time is None:
        age_minutes = math.inf
    else:
        age_minutes = max(0.0, (now - source_time).total_seconds() / 60.0)
    max_age = freshness_limit_minutes(manifest)
    contract = report_contract.validate_manifest(manifest, path)
    summary = crossbook.get("summary") if isinstance(crossbook.get("summary"), dict) else {}
    markets = crossbook.get("markets") if isinstance(crossbook.get("markets"), dict) else {}
    observations: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    stale_candidates = 0
    raw_only = 0
    noise = 0
    suspect = 0
    for market in markets.values():
        if not isinstance(market, dict):
            continue
        for edge in market.get("edges", []) or []:
            if not isinstance(edge, dict):
                continue
            if is_suspect(edge):
                suspect += 1
                continue
            if is_noise(edge):
                noise += 1
            relay = qualifies_for_relay(edge, summary, contract)
            if not relay:
                if edge.get("actionable"):
                    raw_only += 1
                    observations.append({
                        "type": "raw_only",
                        "match_id": manifest.get("match_id"),
                        "book": edge.get("book"),
                        "market_key": edge.get("market_key"),
                        "outcome": edge.get("outcome"),
                        "ev_shin": edge.get("ev_shin"),
                        "reason": "not relay-qualified by contract/cap/noise/suspect gates",
                    })
                continue
            if age_minutes > max_age:
                stale_candidates += 1
                observations.append({
                    "type": "stale_relay_candidate",
                    "match_id": manifest.get("match_id"),
                    "book": edge.get("book"),
                    "market_key": edge.get("market_key"),
                    "outcome": edge.get("outcome"),
                    "ev_shin": edge.get("ev_shin"),
                    "snapshot_age_minutes": round(age_minutes, 2) if math.isfinite(age_minutes) else None,
                    "max_age_minutes": max_age,
                })
                continue
            if cap_rank(manifest.get("source_quality_cap")) < cap_rank("B"):
                observations.append({
                    "type": "cap_blocked_relay_candidate",
                    "match_id": manifest.get("match_id"),
                    "source_quality_cap": manifest.get("source_quality_cap"),
                    "reason": "source_quality_cap below B",
                })
                continue
            card = card_from_edge(manifest, path, cross_path, crossbook, edge, contract, age_minutes, max_age)
            if float(card.get("ev_recompute_delta", 1.0)) > 0.0015:
                observations.append({"type": "ev_recompute_mismatch", **card})
                continue
            if card["stake_band"] == "footnote":
                observations.append({"type": "tiny_relay_candidate", **card})
                continue
            cards.append(card)
    return {
        "manifest_path": str(path),
        "match_id": manifest.get("match_id"),
        "status": "ok",
        "contract_valid": bool(contract.get("valid")),
        "contract_actionable_allowed": bool(contract.get("actionable_allowed")),
        "snapshot_age_minutes": round(age_minutes, 2) if math.isfinite(age_minutes) else None,
        "snapshot_max_age_minutes": max_age,
        "freshness_status": "fresh" if age_minutes <= max_age else "stale",
        "summary": summary,
        "cards": cards,
        "observations": observations,
        "raw_only_count": raw_only,
        "noise_count": noise,
        "suspect_count": suspect,
        "stale_candidate_count": stale_candidates,
    }


def dedupe_correlated(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    related: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        key = str(card.get("correlation_key"))
        current = best.get(key)
        if current is None or float(card.get("suggested_stake_pct", 0.0)) > float(current.get("suggested_stake_pct", 0.0)):
            if current is not None:
                related.setdefault(key, []).append(current)
            best[key] = card
        else:
            related.setdefault(key, []).append(card)
    rows = list(best.values())
    for row in rows:
        row["related_suppressed_count"] = len(related.get(str(row.get("correlation_key")), []))
    rows.sort(key=lambda item: float(item.get("suggested_stake_pct", 0.0)), reverse=True)
    total = sum(float(item.get("suggested_stake_pct", 0.0)) for item in rows)
    scale = min(1.0, (TOTAL_EXPOSURE_FRACTION * 100.0) / total) if total > 0 else 1.0
    for row in rows:
        row["exposure_scale"] = scale
        row["scaled_stake_pct"] = float(row.get("suggested_stake_pct", 0.0)) * scale
    return rows


def load_state(workspace: Path) -> dict[str, Any]:
    payload = load_json(workspace / "state" / DEFAULT_STATE_NAME, {})
    return payload if isinstance(payload, dict) else {}


def save_state(workspace: Path, payload: dict[str, Any]) -> None:
    write_json(workspace / "state" / DEFAULT_STATE_NAME, payload)


def write_board(workspace: Path, board: dict[str, Any]) -> tuple[Path, Path]:
    latest = workspace / "opportunities" / "opportunity-board-latest.json"
    dated = workspace / "opportunities" / "boards" / f"opportunity-board-{board['captured_at_utc'].replace(':', '').replace('-', '').replace('Z', 'Z')}.json"
    write_json(latest, board)
    write_json(dated, board)
    return latest, dated


def classify_changes(cards: list[dict[str, Any]], state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    active = state.get("active") if isinstance(state.get("active"), dict) else {}
    current = {str(card["opportunity_id"]): card for card in cards}
    new_cards: list[dict[str, Any]] = []
    updated_cards: list[dict[str, Any]] = []
    closed_cards: list[dict[str, Any]] = []
    for key, card in current.items():
        prior = active.get(key)
        if not isinstance(prior, dict):
            new_cards.append(card)
            continue
        price_changed = abs(float(card.get("offered_odds", 0.0)) - float(prior.get("offered_odds", 0.0))) >= 0.01
        ev_changed = abs(float(card.get("robust_ev", 0.0)) - float(prior.get("robust_ev", 0.0))) >= 0.01
        if price_changed or ev_changed:
            updated_cards.append(card)
    for key, prior in active.items():
        if key not in current and isinstance(prior, dict):
            closed_cards.append(prior)
    next_state = {
        "active": current,
        "updated_at_utc": utc_now(),
    }
    return new_cards, updated_cards, closed_cards, next_state


def render_alert(board: dict[str, Any], new_cards: list[dict[str, Any]], updated_cards: list[dict[str, Any]], closed_cards: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    if new_cards or updated_cards:
        lines.append("🎯 WC26 Opportunity Watch")
        lines.append("")
        lines.append(f"captured_at_utc: {board['captured_at_utc']}")
        lines.append(f"new: {len(new_cards)} | updated: {len(updated_cards)} | active: {len(board['opportunities'])}")
        lines.append("")
        for idx, card in enumerate((new_cards + updated_cards)[:8], 1):
            label = "NEW" if card in new_cards else "UPDATE"
            lines.append(
                f"{idx}. {label} {card['match_id']} {card['match']} | {card['book']} {card['market_key']} {card['outcome']} @{card['offered_odds']}"
            )
            lines.append(
                f"   rawEV {fmt_pct(float(card['raw_ev']))} | robustEV {fmt_pct(float(card['robust_ev']))} | "
                f"注码 {fmt_stake_pct(float(card['scaled_stake_pct']))} bankroll ({card['stake_band']})"
            )
            lines.append(
                f"   snapshot_age={card['snapshot_age_minutes']}m/{card['snapshot_max_age_minutes']}m | "
                f"source_cap={card['source_quality_cap']} | contract=recomputed"
            )
            lines.append(f"   entry: {card['entry_condition']}")
            lines.append(f"   withdraw_if: {card['withdraw_if']}")
            lines.append(f"   opportunity_id: {card['opportunity_id']}")
        lines.append("")
        lines.append("Discipline: human review only; confirm price still exists before any action.")
    if closed_cards:
        if lines:
            lines.append("")
            lines.append("---")
            lines.append("")
        lines.append("🔒 WC26 Opportunity Closed")
        lines.append("")
        for card in closed_cards[:8]:
            lines.append(
                f"- {card.get('match_id')} {card.get('match')} | {card.get('book')} {card.get('market_key')} {card.get('outcome')} "
                f"@{card.get('offered_odds')} | prior_id={card.get('opportunity_id')}"
            )
        lines.append("")
        lines.append("Reason: opportunity no longer passes current watcher gates or fresh artifact set.")
    return "\n".join(lines).strip()


def render_empty(board: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# WC26 Opportunity Watch Rehearsal",
            "",
            f"captured_at_utc: {board['captured_at_utc']}",
            f"manifests_scanned: {board['manifests_scanned']}",
            f"active_opportunities: {len(board['opportunities'])}",
            f"observations: {len(board['observations'])}",
            f"stale_candidates: {board['stats']['stale_candidates']}",
            f"raw_only_candidates: {board['stats']['raw_only_candidates']}",
            f"noise_edges: {board['stats']['noise_edges']}",
            f"suspect_edges: {board['stats']['suspect_edges']}",
            "telegram_alert: none",
        ]
    )


def build_board(workspace: Path, lookback_hours: float, now: datetime) -> dict[str, Any]:
    scan_results = [scan_manifest(path, now) for path in latest_manifests(workspace, lookback_hours, now)]
    all_cards = [card for result in scan_results for card in result.get("cards", []) if isinstance(result, dict)]
    opportunities = dedupe_correlated(all_cards)
    observations = [obs for result in scan_results for obs in result.get("observations", []) if isinstance(result, dict)]
    return {
        "schema_version": "wc26.opportunity_board.v1",
        "captured_at_utc": now.isoformat().replace("+00:00", "Z"),
        "mode": "read_only_sidecar",
        "network_used": False,
        "llm_used": False,
        "manifests_scanned": len(scan_results),
        "opportunities": opportunities,
        "observations": observations[:100],
        "stats": {
            "fresh_opportunities": len(opportunities),
            "raw_only_candidates": sum(int(result.get("raw_only_count", 0)) for result in scan_results),
            "noise_edges": sum(int(result.get("noise_count", 0)) for result in scan_results),
            "suspect_edges": sum(int(result.get("suspect_count", 0)) for result in scan_results),
            "stale_candidates": sum(int(result.get("stale_candidate_count", 0)) for result in scan_results),
            "skipped_manifests": sum(1 for result in scan_results if result.get("status") != "ok"),
        },
        "scan_results": scan_results,
    }


def run(args: argparse.Namespace) -> int:
    workspace = args.workspace
    now = parse_time(args.now) if args.now else utc_now_dt()
    if now is None:
        raise ValueError(f"invalid --now: {args.now}")
    board = build_board(workspace, args.lookback_hours, now)
    latest, dated = write_board(workspace, board)
    state = load_state(workspace)
    new_cards, updated_cards, closed_cards, next_state = classify_changes(board["opportunities"], state)
    next_state.update(
        {
            "last_board_path": str(latest),
            "last_board_archive_path": str(dated),
            "last_run_utc": board["captured_at_utc"],
            "last_stats": board["stats"],
            "last_active_count": len(board["opportunities"]),
        }
    )
    if not args.dry_run:
        save_state(workspace, next_state)
    if args.emit_empty:
        print(render_empty(board))
        return 0
    alert = render_alert(board, new_cards, updated_cards, closed_cards)
    if alert:
        print(alert)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="WC26 read-only opportunity watcher")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--lookback-hours", type=float, default=72.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--emit-empty", action="store_true", help="print a rehearsal/health summary even when no alert exists")
    parser.add_argument("--now", help="override current UTC time for tests/rehearsal")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
