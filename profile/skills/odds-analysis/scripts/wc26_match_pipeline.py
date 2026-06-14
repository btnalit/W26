#!/usr/bin/env python3
"""Compile a WC26 match-analysis numeric artifact chain and guarded report.

This is the deterministic pipeline boundary for the analyst worker. The LLM can
write football interpretation and adjustment-ledger prose, but no market number
is relay-safe unless this compiler or an equivalent deterministic script emitted
the manifest and the report passed both contract checks.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROFILE_ROOT = SCRIPT_DIR.parents[2]
DEFAULT_WORKSPACE = Path("/hermesdata/worldcup-2026-handicap")
DEFAULT_FIXTURE_PATH = DEFAULT_WORKSPACE / "snapshots/fixtures/football-data-wc-matches-latest.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


numeric_artifact = load_module("numeric_artifact", SCRIPT_DIR / "numeric_artifact.py")
model_margin = load_module("model_margin", SCRIPT_DIR / "model_margin.py")
report_contract = load_module("report_contract", SCRIPT_DIR / "report_contract.py")
report_guard = load_module("report_guard", SCRIPT_DIR / "report_guard.py")
fixture_registry = load_module("fixture_registry", SCRIPT_DIR / "fixture_registry.py")
motivation_context = load_module("motivation_context", SCRIPT_DIR / "motivation_context.py")
role_engine = load_module("role_engine", SCRIPT_DIR / "role_engine.py")
mechanism_audit = load_module("mechanism_audit", SCRIPT_DIR / "mechanism_audit.py")


def parse_utc(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def stable_slug(parts: list[str]) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fixture_by_match_id(fixture_path: Path, match_id: str) -> dict[str, Any]:
    registry = fixture_registry.load_registry(fixture_path)
    item = fixture_registry.resolve_fixture(registry, match_id=match_id)
    return {
        "match_id": item["local_ordinal_id"],
        "canonical_id": item["canonical_id"],
        "football_data_id": item["football_data_id"],
        "home": item["home"],
        "away": item["away"],
        "home_tla": item.get("home_tla"),
        "away_tla": item.get("away_tla"),
        "kickoff_utc": item.get("kickoff_utc"),
        "stage": item.get("stage"),
        "group": item.get("group"),
        "matchday": item.get("matchday"),
        "venue": item.get("venue") or "TBD",
        "fixture_status": item.get("status"),
    }


def artifact_from_payload(out_dir: Path, payload: dict[str, Any], numbers: list[dict[str, Any]]) -> dict[str, Any]:
    artifact_id = numeric_artifact.stable_id("devig", payload)
    payload["artifact_id"] = artifact_id
    path = out_dir / f"{artifact_id.replace(':', '-')}.json"
    write_json(path, payload)
    for number in numbers:
        number["artifact_id"] = artifact_id
    return {
        "artifact_id": artifact_id,
        "artifact_type": "devig",
        "script": "devig.py",
        "path": str(path),
        "source_snapshot_id": payload.get("source_snapshot_id"),
    }


def _load_optional_json(path: Path | None) -> Any:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_motivation_artifact(args: argparse.Namespace, artifact_dir: Path, fixture: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    standings = _load_optional_json(getattr(args, "standings_path", None))
    remaining = _load_optional_json(getattr(args, "remaining_fixtures_path", None))
    rules = _load_optional_json(getattr(args, "advancement_rules_path", None))
    artifact = motivation_context.analyze_motivation_context(
        standings=standings,
        group_remaining_fixtures=remaining,
        match_under_analysis=fixture,
        advancement_rules=rules,
    )
    artifact_id = f"motivation:{fixture['match_id']}:{stable_slug([fixture['match_id'], str(fixture.get('matchday')), artifact.get('situation_tag') or 'NONE'])}"
    artifact["artifact_id"] = artifact_id
    path = artifact_dir / f"{artifact_id.replace(':', '-')}.json"
    write_json(path, artifact)
    manifest_entry = {
        "artifact_id": artifact_id,
        "artifact_type": "motivation_context",
        "script": "motivation_context.py",
        "path": str(path),
        "provides": ["motivation_context"],
        "source_snapshot_id": artifact.get("standings_snapshot_id"),
    }
    return artifact, manifest_entry


def append_role_engine_artifact(manifest: dict[str, Any], manifest_path: Path, artifact_dir: Path) -> dict[str, Any]:
    artifact = role_engine.build_role_artifact(manifest, manifest_path)
    artifact_path = artifact_dir / f"{artifact['artifact_id'].replace(':', '-')}.json"
    write_json(artifact_path, artifact)
    manifest.setdefault("artifacts", []).append(
        {
            "artifact_id": artifact["artifact_id"],
            "artifact_type": "role_engine",
            "script": "role_engine.py",
            "path": str(artifact_path),
            "provides": ["role_engine"],
        }
    )
    manifest.setdefault("analysis_gates", {})["role_engine"] = "pass"
    caps = manifest.setdefault("artifact_capabilities", [])
    if isinstance(caps, list) and "role_engine" not in caps:
        caps.append("role_engine")
    return artifact


def append_mechanism_audit_artifact(manifest: dict[str, Any], manifest_path: Path, artifact_dir: Path) -> dict[str, Any]:
    audit = mechanism_audit.build_audit(manifest, manifest_path)
    match_id = manifest.get("match_id") or (manifest.get("match") or {}).get("match_id") or "UNKNOWN"
    artifact_id = f"mechanism:{match_id}:{stable_slug([json.dumps(audit, ensure_ascii=False, sort_keys=True, default=str)])}"
    audit["artifact_id"] = artifact_id
    audit_path = artifact_dir / f"{artifact_id.replace(':', '-')}.json"
    write_json(audit_path, audit)
    manifest.setdefault("artifacts", []).append(
        {
            "artifact_id": artifact_id,
            "artifact_type": "mechanism_audit",
            "script": "mechanism_audit.py",
            "path": str(audit_path),
            "provides": ["mechanism_audit"],
        }
    )
    manifest.setdefault("analysis_gates", {})["mechanism_audit"] = "pass"
    caps = manifest.setdefault("artifact_capabilities", [])
    if isinstance(caps, list) and "mechanism_audit" not in caps:
        caps.append("mechanism_audit")
    return audit


def build_numeric_chain(args: argparse.Namespace, run_dir: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    artifact_dir = run_dir / "artifacts"
    as_of = parse_utc(args.as_of_utc).isoformat().replace("+00:00", "Z")
    slug = stable_slug([fixture["match_id"], args.window, args.market_set, args.mode, as_of])
    snapshot_prefix = "synthetic-no-paid" if args.mode == "simulation" else "snapshot"

    scalar_args = argparse.Namespace(
        snapshot_id=f"{snapshot_prefix}:{fixture['match_id']}:1x2:{as_of}",
        out_dir=artifact_dir,
        odds=[args.home_odds, args.draw_odds, args.away_odds],
        odds_format=args.odds_format,
        prob=None,
        price=None,
        price_format="decimal",
        created_at_utc=as_of,
    )
    scalar_payload, scalar_numbers = numeric_artifact.scalar_payload(scalar_args)
    scalar_artifact = artifact_from_payload(artifact_dir, scalar_payload, scalar_numbers)

    matrix = model_margin.poisson_score_matrix(args.home_xg, args.away_xg, args.max_goals)
    margin_probs = model_margin.margin_distribution_from_score_matrix(matrix)
    model_artifact_id = f"model:{fixture['match_id']}:{slug}"
    model_path = artifact_dir / f"{model_artifact_id.replace(':', '-')}.json"
    model_payload = {
        "artifact_id": model_artifact_id,
        "artifact_type": "model",
        "script": "model_margin.py",
        "model_contract": "score_matrix_to_margin_distribution",
        "matrix_source": "poisson_baseline",
        "home_xg": args.home_xg,
        "away_xg": args.away_xg,
        "max_goals": args.max_goals,
        "margin_probabilities": {str(margin): margin_probs[margin] for margin in sorted(margin_probs)},
        "sum_probability": sum(margin_probs.values()),
        "created_at_utc": as_of,
    }
    write_json(model_path, model_payload)

    ah_args = argparse.Namespace(
        snapshot_id=f"{snapshot_prefix}:{fixture['match_id']}:ah:{as_of}",
        out_dir=artifact_dir,
        ah_line=args.ah_line,
        ah_price=args.ah_price,
        ah_price_format=args.ah_price_format,
        margin_probs_json=json.dumps({str(k): v for k, v in margin_probs.items()}, sort_keys=True),
        created_at_utc=as_of,
    )
    ah_payload, ah_numbers = numeric_artifact.ah_payload(ah_args)
    ah_artifact = artifact_from_payload(artifact_dir, ah_payload, ah_numbers)
    motivation_artifact, motivation_manifest_entry = build_motivation_artifact(args, artifact_dir, fixture)

    p_market_home = scalar_payload["no_vig_probabilities"][0]
    p_adj_home = p_market_home
    manifest = {
        "schema_version": "wc26.numeric_artifact.v1",
        "compiler": "wc26_match_pipeline.py",
        "compiler_contract": "deterministic_numeric_first_then_guarded_report",
        "mode": args.mode,
        "source_quality": args.source_quality,
        "final_status": "simulation_only" if args.mode == "simulation" else args.final_status,
        "review_required": False,
        "match": fixture,
        "window": args.window,
        "timing_class": args.timing_class,
        "market_set": args.market_set,
        "created_at_utc": as_of,
        "motivation_context": motivation_artifact,
        "adjustment_ledger_id": f"ledger:{fixture['match_id']}:{slug}:default_p_adj_equals_market",
        "adjustment_ledger": [
            {
                "factor": "default_market_shrinkage",
                "direction": "none",
                "magnitude": 0.0,
                "evidence": "compiler canary uses p_adj=p_market unless analyst later writes a guarded ledger",
                "why_not_priced": "none",
                "falsifier": "material information event with fresh source snapshot",
                "uncertainty_pct": 0.0,
            }
        ],
        "numbers": [
            *scalar_numbers,
            {
                "name": "home_p_adj_edge",
                "kind": "p_adj_edge",
                "value": 0.0,
                "snapshot_id": scalar_args.snapshot_id,
                "artifact_id": scalar_artifact["artifact_id"],
                "artifact_type": "devig",
                "probability_source": "p_adj",
                "p_market": p_market_home,
                "p_adj": p_adj_home,
                "uses_p_model_directly": False,
            },
            *ah_numbers,
        ],
        "artifacts": [
            scalar_artifact,
            {
                "artifact_id": model_artifact_id,
                "artifact_type": "model",
                "script": "model_margin.py",
                "path": str(model_path),
                "source_snapshot_id": f"{snapshot_prefix}:{fixture['match_id']}:model:{as_of}",
            },
            ah_artifact,
            motivation_manifest_entry,
        ],
    }
    return manifest


def report_text(args: argparse.Namespace, fixture: dict[str, Any], manifest_path: Path, manifest: dict[str, Any]) -> str:
    numbers = {item["name"]: item for item in manifest["numbers"]}
    scalar_artifact = manifest["artifacts"][0]
    ah_artifact = manifest["artifacts"][2]
    no_vig_home = numbers["no_vig_0"]["value"]
    no_vig_draw = numbers["no_vig_1"]["value"]
    no_vig_away = numbers["no_vig_2"]["value"]
    ah_ev = numbers["asian_handicap_ev"]["value"]
    ah_kelly = numbers["asian_handicap_kelly"]["value"]
    final_status = manifest["final_status"]
    motivation = manifest.get("motivation_context", {})
    motivation_hint = motivation.get("model_hint", {}) if isinstance(motivation, dict) else {}
    mode_note = "synthetic no-paid canary" if args.mode == "simulation" else "live cached snapshot"
    source_snapshot = scalar_artifact["source_snapshot_id"]
    ah_snapshot = ah_artifact["source_snapshot_id"]

    return "\n".join(
        [
            f"# WC26 {fixture['match_id']} {fixture['home']} vs {fixture['away']} - {args.window} Handicap Report",
            "",
            f"cutoff_utc: {manifest['created_at_utc']}",
            f"mode: {args.mode}",
            f"source_quality: {args.source_quality}",
            f"final_status: {final_status}",
            "review_required: false",
            f"artifact_manifest_path: {manifest_path}",
            "artifact_contract_status: pass",
            "report_guard_status: pass",
            f"window: {args.window}",
            f"timing_class: {args.timing_class}",
            f"information_event: {args.information_event}",
            "entry_time_utc: N/A (simulation/pre-window)" if args.mode == "simulation" else f"entry_time_utc: {manifest['created_at_utc']}",
            "entry_price: N/A" if args.mode == "simulation" else f"entry_price: {args.ah_price}",
            "lineup_status: not_required",
            "---",
            "",
            "## 1. One-Line View",
            "",
            f"{mode_note}. Numeric claims are compiler-generated from `{manifest_path.name}`. No paid API quota was used by this pipeline.",
            "",
            "## 2. Source Snapshot",
            "",
            "| Source | Type | Snapshot ID | Captured | Freshness | Status |",
            "| --- | --- | --- | --- | --- | --- |",
            f"| synthetic/no-paid fixture+odds | canary | {source_snapshot} | {manifest['created_at_utc']} | test fixture | C |",
            f"| synthetic/no-paid AH | canary | {ah_snapshot} | {manifest['created_at_utc']} | test fixture | C |",
            "",
            "## 3. Official Match Facts",
            "",
            f"- Match: {fixture['match_id']} / football-data id {fixture['football_data_id']}",
            f"- Fixture: {fixture['home']} vs {fixture['away']}",
            f"- Kickoff UTC: {fixture['kickoff_utc']}",
            f"- Venue: {fixture['venue']}",
            f"- Stage/group: {fixture['stage']} / {fixture['group']}",
            "",
            "## 4. Market Board",
            "",
            "| Market | Line | Book | Source Unit | Current Decimal | Snapshot ID | Devig Artifact | No-Vig Market | Model Fair | p_adj | Edge | Note |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            f"| 1X2 home | win | synthetic | {args.odds_format} | {args.home_odds:.3f} | {source_snapshot} | {scalar_artifact['artifact_id']} | {no_vig_home:.4f} | N/A | {no_vig_home:.4f} | 0.0000 | p_adj defaults to market |",
            f"| 1X2 draw | draw | synthetic | {args.odds_format} | {args.draw_odds:.3f} | {source_snapshot} | {scalar_artifact['artifact_id']} | {no_vig_draw:.4f} | N/A | {no_vig_draw:.4f} | 0.0000 | p_adj defaults to market |",
            f"| 1X2 away | win | synthetic | {args.odds_format} | {args.away_odds:.3f} | {source_snapshot} | {scalar_artifact['artifact_id']} | {no_vig_away:.4f} | N/A | {no_vig_away:.4f} | 0.0000 | p_adj defaults to market |",
            f"| Asian handicap | {args.ah_line:+.2f} | synthetic | {args.ah_price_format} | {args.ah_price:.3f} | {ah_snapshot} | {ah_artifact['artifact_id']} | N/A | margin distribution | ledger default | EV {ah_ev:.4f}; Kelly {ah_kelly:.4f} | leg-settlement EV |",
            "",
            "## 5. Football Read",
            "",
            "Compiler canary does not add qualitative football claims. The analyst may add prose only after keeping these numbers unchanged and cited.",
            "",
            "## 6. Market Psychology",
            "",
            "No bookmaker-intent claim is made by the compiler.",
            "",
            "## 6A. motivation_context",
            "",
            f"- contract: {motivation.get('contract', 'wc26.motivation_context.v1')}",
            f"- status: {motivation.get('status', 'none')}",
            f"- situation_tag: {motivation.get('situation_tag', 'NONE')}",
            f"- direction: {motivation_hint.get('direction', 'none')}",
            f"- magnitude: {motivation_hint.get('magnitude', 'qualitative_only')}",
            f"- footnote: {motivation.get('footnote_zh', '动机情境·描述性·非下注信号')}",
            "- actionability: diagnostic_only unless market_reflection_check + Path A both pass",
            "",
            "## 7. Bookmaker Intent Hypotheses",
            "",
            "| Hypothesis | Evidence | Falsifier | Weight |",
            "| --- | --- | --- | --- |",
            "| none | compiler canary only | fresh priced market snapshot | 0 |",
            "",
            "## 8. Anti-AI Red Team",
            "",
            "No edge is claimed because p_adj equals p_market and source quality is C.",
            "",
            "## 9. Adjustment Ledger",
            "",
            "Default: `p_adj = p_market` unless the rows below justify a change.",
            "",
            "| Factor | Direction | Magnitude | Evidence | Why Not Priced | Falsifier | Uncertainty |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            "| default_market_shrinkage | none | 0.0000 | compiler canary | none | material information event | 0.0000 |",
            "",
            "edge_formula_scalar_no_push: p_adj - p_market",
            "ev_formula_scalar_no_push: p_adj * decimal_odds - 1",
            "ev_formula_asian: settlement EV by handicap legs",
            "sigma_total: 0.0000",
            f"robust_ev: {ah_ev:.4f}",
            "uncertainty_gate_status: no_actionable_edge",
            f"adjustment_ledger_id: {manifest['adjustment_ledger_id']}",
            "",
            "## 9A. Numeric Artifact Manifest Summary",
            "",
            f"manifest_path: {manifest_path}",
            f"contract_check_command: python3 {SCRIPT_DIR / 'report_contract.py'} {manifest_path}",
            "contract_check_status: pass",
            f"report_guard_command: python3 {SCRIPT_DIR / 'report_guard.py'} <this report>",
            "report_guard_status: pass",
            "",
            "## 10. Final Decision",
            "",
            f"status: {final_status}",
            "market: none",
            "acceptable_price: N/A",
            "confidence: canary_only",
            "stake_advice: none",
            "review_required: false",
            "next_check: fresh real snapshot when the scheduled window arrives",
            "",
            "## 11. Post-Match Grading Slot",
            "",
            "closing_line:",
            "result:",
            "CLV:",
            "CLV_by_timing_class:",
            "Brier/log_loss:",
            "lesson:",
            "",
        ]
    )


def compile_report(args: argparse.Namespace) -> dict[str, Any]:
    workspace = args.workspace
    fixture = fixture_by_match_id(args.fixture_path, args.match_id)
    as_of = parse_utc(args.as_of_utc).isoformat().replace("+00:00", "Z")
    slug = stable_slug([args.match_id, args.window, args.market_set, args.mode, as_of])
    run_dir = workspace / "reports" / "pipeline" / f"{args.match_id}-{args.window}-{slug}"
    manifest = build_numeric_chain(args, run_dir, fixture)
    manifest_path = run_dir / "manifest.json"
    write_json(manifest_path, manifest)

    artifact_dir = run_dir / "artifacts"
    role_artifact = append_role_engine_artifact(manifest, manifest_path, artifact_dir)
    write_json(manifest_path, manifest)
    append_mechanism_audit_artifact(manifest, manifest_path, artifact_dir)
    write_json(manifest_path, manifest)

    contract_result = report_contract.validate_manifest(manifest, manifest_path)
    if not contract_result.get("valid"):
        raise RuntimeError("report_contract failed: " + "; ".join(contract_result.get("errors", [])))

    report_path = run_dir / "report.md"
    report_path.write_text(report_text(args, fixture, manifest_path, manifest), encoding="utf-8")
    role_engine.patch_report(report_path, role_artifact)
    guard_result = report_guard.validate_report(report_path)
    if not guard_result.get("safe_to_relay"):
        raise RuntimeError("report_guard failed: " + "; ".join(guard_result.get("errors", [])))

    handoff_summary = (
        f"wc26_match_pipeline 已完成 {fixture['match_id']} {fixture['home']} vs {fixture['away']} "
        f"{args.window} {args.market_set} {args.mode} 分析编译。"
        f"final_status={manifest['final_status']}，source_quality={args.source_quality}，"
        "report_contract=pass，report_guard=pass；数字来自 deterministic manifest，未使用付费 API。"
    )
    metadata = {
        "final_status": manifest["final_status"],
        "mode": args.mode,
        "source_quality": args.source_quality,
        "artifact_manifest_path": str(manifest_path),
        "artifact_contract_status": "pass",
        "report_guard_status": "pass",
        "timing_class": args.timing_class,
        "report_path": str(report_path),
        "review_required": False,
        "handoff_summary_zh": handoff_summary,
    }
    return {
        "ok": True,
        "compiler": "wc26_match_pipeline.py",
        "match": fixture,
        "report_path": str(report_path),
        "manifest_path": str(manifest_path),
        "metadata": metadata,
        "contract": contract_result,
        "guard": guard_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--fixture-path", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--match-id", default="M001")
    parser.add_argument("--mode", choices=["live", "simulation"], default="simulation")
    parser.add_argument("--source-quality", choices=["A", "B", "C", "D"], default="C")
    parser.add_argument("--final-status", choices=["pass", "watch", "lean", "qualified_play"], default="watch")
    parser.add_argument("--window", default="early_structural")
    parser.add_argument("--timing-class", default="early_structural")
    parser.add_argument("--information-event", default="structural")
    parser.add_argument("--market-set", default="handicap")
    parser.add_argument("--as-of-utc", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--home-odds", type=float, default=1.50)
    parser.add_argument("--draw-odds", type=float, default=4.13)
    parser.add_argument("--away-odds", type=float, default=6.23)
    parser.add_argument("--odds-format", default="decimal", choices=sorted(numeric_artifact.devig.SUPPORTED_ODDS_FORMATS))
    parser.add_argument("--home-xg", type=float, default=1.65)
    parser.add_argument("--away-xg", type=float, default=0.80)
    parser.add_argument("--max-goals", type=int, default=10)
    parser.add_argument("--ah-line", type=float, default=-1.0)
    parser.add_argument("--ah-price", type=float, default=1.88)
    parser.add_argument("--ah-price-format", default="decimal", choices=sorted(numeric_artifact.devig.SUPPORTED_ODDS_FORMATS))
    parser.add_argument("--standings-path", type=Path)
    parser.add_argument("--remaining-fixtures-path", type=Path)
    parser.add_argument("--advancement-rules-path", type=Path)
    args = parser.parse_args()

    result = compile_report(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
