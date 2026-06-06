#!/usr/bin/env python3
"""Generate deterministic mechanism audit for WC26 direct reports.

This is the machine-generated "did the mechanisms actually run?" artifact.
It turns existing numeric artifacts into auditable mechanism status and fixed
decision enums. It does not invent hypotheses from prose.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_CONTRACT = "wc26.mechanism_audit.v1"
ACTIONABLE_EV_THRESHOLD = 0.05
DECISION_ENUMS = {
    "CONFIRMED_ACTIONABLE",
    "CONFIRMED_NOISE",
    "REFUTED",
    "DIAGNOSTIC_ONLY",
    "SUSPECT",
    "BLOCKED",
}


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return payload


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
    if "asian_handicap" in raw or " ah" in raw or "-ah-" in raw:
        caps.add("asian_handicap")
    if "totals" in raw or "over_under" in raw or "total_goals" in raw:
        caps.add("totals")
    if "consistency_triangle" in raw or "path_c" in raw:
        caps.add("path_c_consistency")
    if "mechanism_audit" in raw or "mechanism audit" in raw:
        caps.add("mechanism_audit")
    if "model" in raw or "dixon_coles" in raw:
        caps.add("path_b_model_diagnostic")
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


def gate_status(manifest: dict[str, Any], gate: str) -> str:
    gates = manifest.get("analysis_gates")
    if not isinstance(gates, dict):
        return "missing"
    raw = gates.get(gate)
    if isinstance(raw, dict):
        return str(raw.get("status", "missing")).strip().lower()
    return str(raw or "missing").strip().lower()


def edge_decision(edge: dict[str, Any]) -> str:
    if edge.get("suspect"):
        return "SUSPECT"
    try:
        ev = float(edge.get("ev_shin"))
    except Exception:
        ev = 0.0
    if edge.get("survives_all_methods") is True and ev >= ACTIONABLE_EV_THRESHOLD:
        return "CONFIRMED_ACTIONABLE"
    if ev > 0:
        return "CONFIRMED_NOISE"
    return "REFUTED"


def path_a_mechanism(
    artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    entry = artifacts.get("path_a_crossbook")
    if not entry or not entry[1]:
        return (
            {
                "status": "BLOCKED",
                "required_for_complete": True,
                "reason": "missing cross_book_scan artifact",
            },
            [
                {
                    "source": "path_a_crossbook",
                    "subject": "cross-book arithmetic scan",
                    "decision": "BLOCKED",
                    "evidence": "missing cross_book_scan artifact",
                }
            ],
        )

    artifact, payload = entry
    summary = payload.get("summary") or payload.get("scan_summary") or {}
    markets = payload.get("markets") if isinstance(payload.get("markets"), dict) else {}
    decisions: list[dict[str, Any]] = []
    for market_name, market_result in markets.items():
        if not isinstance(market_result, dict):
            continue
        for edge in market_result.get("edges", []):
            if not isinstance(edge, dict):
                continue
            decision = edge_decision(edge)
            decisions.append(
                {
                    "source": "path_a_crossbook",
                    "subject": f"{edge.get('book', 'unknown')} {edge.get('market_key', market_name)} {edge.get('outcome', 'unknown')}",
                    "decision": decision,
                    "market_key": edge.get("market_key", market_name),
                    "book": edge.get("book"),
                    "outcome": edge.get("outcome"),
                    "offered_odds": edge.get("offered_odds"),
                    "fair_odds": edge.get("fair_odds"),
                    "ev_shin": edge.get("ev_shin"),
                    "ev_power": edge.get("ev_power"),
                    "ev_multiplicative": edge.get("ev_multiplicative"),
                    "survives_all_methods": edge.get("survives_all_methods"),
                    "suspect": edge.get("suspect"),
                    "evidence": "cross_book_scan edge row",
                }
            )

    if not decisions:
        decisions.append(
            {
                "source": "path_a_crossbook",
                "subject": "cross-book arithmetic scan",
                "decision": "REFUTED",
                "evidence": "no quote met the scan edge threshold",
            }
        )

    return (
        {
            "status": "COMPLETE",
            "required_for_complete": True,
            "artifact_id": artifact.get("artifact_id"),
            "input_snapshot": payload.get("input_snapshot") or payload.get("source_snapshot_id"),
            "markets_scanned": summary.get("markets_scanned", list(markets.keys())),
            "quotes_scanned": summary.get("quotes_scanned"),
            "edge_count": summary.get("edge_count"),
            "noise_edge_count": summary.get("noise_edge_count"),
            "actionable_count": summary.get("actionable_count"),
            "raw_actionable_count": summary.get("raw_actionable_count", summary.get("actionable_count")),
            "relay_actionable_count": summary.get("relay_actionable_count", 0),
            "qualified_play_count": summary.get("qualified_play_count"),
            "best_edge": summary.get("best_actionable_edge") or summary.get("best_edge"),
        },
        decisions,
    )


def path_b_mechanism(manifest: dict[str, Any], artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    status = gate_status(manifest, "path_b_model_diagnostic")
    model_entry = artifacts.get("path_b_model_diagnostic")
    model_payload = model_entry[1] if model_entry else None
    calibration = None
    if isinstance(model_payload, dict):
        raw_cal = model_payload.get("calibration")
        if isinstance(raw_cal, dict):
            calibration = raw_cal.get("calibration_status") or raw_cal.get("status")
        calibration = calibration or model_payload.get("calibration_status")
    if status in {"pass", "ok", "complete", "diagnostic", "no_signal"}:
        return (
            {
                "status": "COMPLETE",
                "required_for_complete": True,
                "gate_status": status,
                "calibration_status": calibration,
            },
            [
                {
                    "source": "path_b_model_diagnostic",
                    "subject": "model probability vs market",
                    "decision": "DIAGNOSTIC_ONLY",
                    "evidence": f"calibration_status={calibration or 'unknown'}; p_adj must not move from model alone",
                }
            ],
        )
    return (
        {
            "status": "BLOCKED",
            "required_for_complete": True,
            "gate_status": status,
            "reason": "path_b_model_diagnostic gate missing or not pass/diagnostic",
        },
        [
            {
                "source": "path_b_model_diagnostic",
                "subject": "model probability vs market",
                "decision": "BLOCKED",
                "evidence": f"gate_status={status}",
            }
        ],
    )


def path_c_mechanism(artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    entry = artifacts.get("path_c_consistency")
    if not entry or not entry[1]:
        return (
            {
                "status": "BLOCKED",
                "required_for_complete": True,
                "reason": "missing consistency_triangle artifact",
            },
            [
                {
                    "source": "path_c_consistency",
                    "subject": "1X2-AH-totals consistency triangle",
                    "decision": "BLOCKED",
                    "evidence": "missing consistency_triangle artifact",
                }
            ],
        )
    artifact, payload = entry
    signal = payload.get("signal") if isinstance(payload.get("signal"), dict) else {}
    discrepancy = payload.get("discrepancy") if isinstance(payload.get("discrepancy"), dict) else {}
    decision = "CONFIRMED_NOISE"
    if signal.get("actionable") is True:
        decision = "CONFIRMED_ACTIONABLE"
    elif signal.get("type") in (None, "", "none"):
        decision = "REFUTED"
    return (
        {
            "status": "COMPLETE",
            "required_for_complete": True,
            "artifact_id": artifact.get("artifact_id"),
            "signal_type": signal.get("type"),
            "discrepancy_pp": discrepancy.get("pp"),
        },
        [
            {
                "source": "path_c_consistency",
                "subject": "1X2-AH-totals consistency triangle",
                "decision": decision,
                "evidence": f"signal={signal.get('type')}; discrepancy_pp={discrepancy.get('pp')}",
            }
        ],
    )


def role_engine_mechanism(artifacts: dict[str, tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, Any]:
    entry = artifacts.get("role_engine")
    if not entry or not entry[1]:
        return {
            "status": "BLOCKED",
            "required_for_complete": False,
            "reason": "deterministic role engine artifact missing; game-theory reading unavailable",
        }
    artifact, payload = entry
    conclusions = payload.get("role_conclusions") if isinstance(payload.get("role_conclusions"), list) else []
    version = str(payload.get("engine_version", "unknown")).strip() or "unknown"
    return {
        "status": f"COMPLETE({version})",
        "required_for_complete": False,
        "artifact_id": artifact.get("artifact_id") or payload.get("artifact_id"),
        "engine_contract": payload.get("engine_contract"),
        "engine_version": version,
        "conclusion_count": len(conclusions),
    }


def required_final_status(manifest: dict[str, Any], blocking: list[str]) -> str:
    final_status = str(manifest.get("final_status", "unknown")).strip().lower()
    if not blocking:
        return final_status
    if final_status == "pass":
        return "pass_incomplete"
    if final_status in {"lean", "qualified_play"}:
        return "watch"
    return final_status


def build_audit(manifest: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    artifacts = load_artifacts(manifest, manifest_path)
    path_a, path_a_decisions = path_a_mechanism(artifacts)
    path_b, path_b_decisions = path_b_mechanism(manifest, artifacts)
    path_c, path_c_decisions = path_c_mechanism(artifacts)

    mechanisms = {
        "path_a_crossbook": path_a,
        "path_b_model_diagnostic": path_b,
        "path_c_consistency": path_c,
        "role_engine": role_engine_mechanism(artifacts),
        "artifact_hypothesis_engine_v0": {
            "status": "COMPLETE",
            "required_for_complete": False,
            "reason": "hypotheses are generated only from Path A/B/C artifacts",
        },
    }
    decisions = path_a_decisions + path_b_decisions + path_c_decisions
    for item in decisions:
        if item.get("decision") not in DECISION_ENUMS:
            item["decision"] = "BLOCKED"

    blocking = [
        name
        for name, mechanism in mechanisms.items()
        if mechanism.get("required_for_complete") and mechanism.get("status") == "BLOCKED"
    ]
    required_status = required_final_status(manifest, blocking)
    audit_status = "complete" if not blocking else "pass_incomplete"

    return {
        "artifact_type": "mechanism_audit",
        "artifact_kind": "mechanism_audit",
        "audit_contract": AUDIT_CONTRACT,
        "script": "mechanism_audit.py",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_manifest_path": str(manifest_path),
        "source_manifest_id": manifest.get("manifest_id"),
        "match_id": manifest.get("match_id") or (manifest.get("match") or {}).get("match_id"),
        "manifest_final_status": str(manifest.get("final_status", "")).strip().lower(),
        "mechanism_audit_status": audit_status,
        "required_final_status": required_status,
        "review_required": bool(blocking),
        "blocking_mechanisms": blocking,
        "mechanisms": mechanisms,
        "hypothesis_decisions": decisions,
        "decision_enums": sorted(DECISION_ENUMS),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate WC26 mechanism audit artifact")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    manifest = load_json(args.manifest)
    audit = build_audit(manifest, args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
