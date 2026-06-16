#!/usr/bin/env python3
"""Dimension score audit: read-only hit-rate report from dimension_score_ledger.

Produces raw data only: hit_rate, n_scored, not_applicable_rate, sample_sufficient.
NEVER emits any "candidate_for_removal" or "this dimension is valid/invalid".
Interpretation is always left to the human.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CONTRACT = "wc26.dimension_audit.v1"
AUDIT_REPORT_SCHEMA = "wc26.dimension_audit_report.v2"

DISCLAIMER = (
    "本报表仅为裸数据观测(命中率/样本量/沉默率), 不评价任何维度好坏, "
    "不建议删除任何维度。是否精简、如何解读, 完全由人判断。"
)


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    if config_path is None:
        import os
        # Try default locations
        candidates = [
            Path(__file__).resolve().parent.parent.parent.parent / "config" / "dimension-scoring-config.json",
            Path(os.environ.get("WC26_CONFIG_DIR", "")) / "dimension-scoring-config.json",
        ]
        for c in candidates:
            if c.exists():
                config_path = c
                break
        if config_path is None or not config_path.exists():
            return {
                "dimension_sample_thresholds": {},
                "default_threshold": 20,
                "cross_tournament_accumulation": True,
                "VALUE_INTERPRETATION_IS_HUMAN_ONLY": True,
            }

    return json.loads(config_path.read_text(encoding="utf-8"))


def threshold_for(dimension: str, config: dict[str, Any]) -> int:
    thresholds = config.get("dimension_sample_thresholds", {})
    return int(thresholds.get(dimension, config.get("default_threshold", 20)))


def audit_dimensions(
    ledger: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Compute raw hit-rate data per dimension. No judgments, no removal suggestions."""
    records = ledger.get("records", []) if isinstance(ledger, dict) else []

    # Group by dimension
    by_dim: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        dim = str(rec.get("dimension") or "")
        if not dim:
            continue
        by_dim.setdefault(dim, []).append(rec)

    report: dict[str, Any] = {
        "schema_version": AUDIT_REPORT_SCHEMA,
        "contract": CONTRACT,
        "disclaimer": DISCLAIMER,
        "generated_at_utc": "",  # populated by caller
        "dimensions": {},
    }

    for dim, dim_records in sorted(by_dim.items()):
        scored = [r for r in dim_records if r.get("verdict") in ("hit", "miss")]
        na = [r for r in dim_records if r.get("verdict") == "not_applicable"]
        n_scored = len(scored)
        n_total = n_scored + len(na)
        hits = sum(1 for r in scored if r.get("verdict") == "hit")
        hit_rate = round(hits / n_scored, 4) if n_scored else None
        not_applicable_rate = round(len(na) / n_total, 4) if n_total else None
        sample_sufficient = n_scored >= threshold_for(dim, config)

        report["dimensions"][dim] = {
            "hit_rate": hit_rate,
            "n_scored": n_scored,
            "n_total": n_total,
            "n_hit": hits,
            "n_miss": n_scored - hits,
            "n_not_applicable": len(na),
            "not_applicable_rate": not_applicable_rate,
            "sample_sufficient": sample_sufficient,
            "threshold": threshold_for(dim, config),
        }

    report["generated_at_utc"] = (
        __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat()
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Dimension score audit: raw hit-rate report")
    parser.add_argument("--ledger", type=Path, required=True,
                        help="Path to dimension_score_ledger JSON")
    parser.add_argument("--config", type=Path,
                        help="Path to dimension-scoring-config.json")
    parser.add_argument("--output", type=Path,
                        help="Write audit report to this path (stdout if omitted)")
    args = parser.parse_args()

    config = load_config(args.config)
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    report = audit_dimensions(ledger, config)

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
