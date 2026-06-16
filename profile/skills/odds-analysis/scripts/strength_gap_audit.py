#!/usr/bin/env python3
"""Strength-gap stratified dimension score audit.

Produces a (dimension × tier) cross-tabulated hit-rate matrix from the
dimension_score_ledger.  Purely observational — never emits any "alpha is in
close games" or "lopsided games are uninformative" judgment.  All interpretation
is left to the human.

See: strength-gap-spec.md v1
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTRACT = "wc26.strength_gap_audit.v1"
REPORT_SCHEMA = "wc26.strength_gap_audit_report.v1"

DISCLAIMER = (
    "本报表为(维度×实力差层)的裸命中率观测, 不评价任何层/维度好坏, "
    "不下'接近盘更有价值'之类的结论。如何解读完全由人判断。"
)


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    if config_path is not None and config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8"))
    import os
    candidates = [
        Path(os.environ.get("WC26_CONFIG_DIR", "")) / "strength-gap-config.json",
        Path(__file__).resolve().parent.parent.parent.parent / "config" / "strength-gap-config.json",
    ]
    for c in candidates:
        if c.exists():
            return json.loads(c.read_text(encoding="utf-8"))
    return {
        "tiers": {"even": {"max": 0.20}, "moderate": {"min": 0.20, "max": 0.50}, "lopsided": {"min": 0.50}},
        "min_cell_sample": 15,
        "boundary_version": "v1",
    }


def _tier_order(config: dict[str, Any]) -> list[str]:
    tiers = config.get("tiers", {})
    ordered = []
    for tier_name in ("even", "moderate", "lopsided"):
        if tier_name in tiers:
            ordered.append(tier_name)
    for tier_name in tiers:
        if tier_name not in ordered:
            ordered.append(tier_name)
    return ordered


def audit_by_strength_tier(
    ledger: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Build (dimension × tier) hit-rate matrix from dimension_score_ledger.

    Only raw data.  No "alpha" / "invalid" / "useful" judgments.
    """
    records = ledger.get("records", []) if isinstance(ledger, dict) else []
    min_cell = int(config.get("min_cell_sample", 15))
    tiers = _tier_order(config)

    # ── Collect dimensions and group by (dimENSION, tier) ──
    dimensions: set[str] = set()
    cells: dict[str, dict[str, list[dict[str, Any]]]] = {}  # dim → tier → [records]

    for rec in records:
        if not isinstance(rec, dict):
            continue
        dim = str(rec.get("dimension") or "")
        if not dim:
            continue
        sg = rec.get("strength_gap")
        if not isinstance(sg, dict):
            continue
        tier = str(sg.get("tier") or "unknown")
        if tier == "unknown":
            continue
        dimensions.add(dim)
        cells.setdefault(dim, {}).setdefault(tier, []).append(rec)

    # ── Build matrix ──
    matrix: dict[str, Any] = {}
    for dim in sorted(dimensions):
        dim_cells: dict[str, Any] = {}
        for tier in tiers:
            tier_records = cells.get(dim, {}).get(tier, [])
            scored = [r for r in tier_records if r.get("verdict") in ("hit", "miss")]
            na = [r for r in tier_records if r.get("verdict") == "not_applicable"]
            n_scored = len(scored)
            n_total = n_scored + len(na)
            hits = sum(1 for r in scored if r.get("verdict") == "hit")
            hit_rate = round(hits / n_scored, 4) if n_scored else None
            not_applicable_rate = round(len(na) / n_total, 4) if n_total else None
            dim_cells[tier] = {
                "hit_rate": hit_rate,
                "n_scored": n_scored,
                "n_hit": hits,
                "n_miss": n_scored - hits,
                "n_not_applicable": len(na),
                "not_applicable_rate": not_applicable_rate,
                "sample_sufficient": n_scored >= min_cell,
            }
        matrix[dim] = dim_cells

    # ── Overall by-tier aggregation (cross all dimensions) ──
    by_tier_overall: dict[str, Any] = {}
    for tier in tiers:
        all_tier: list[dict[str, Any]] = []
        for dim in dimensions:
            all_tier.extend(cells.get(dim, {}).get(tier, []))
        scored = [r for r in all_tier if r.get("verdict") in ("hit", "miss")]
        hits = sum(1 for r in scored if r.get("verdict") == "hit")
        n = len(scored)
        by_tier_overall[tier] = {
            "hit_rate": round(hits / n, 4) if n else None,
            "n_scored": n,
        }

    return {
        "schema_version": REPORT_SCHEMA,
        "contract": CONTRACT,
        "disclaimer": DISCLAIMER,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_used": {
            "boundary_version": str(config.get("boundary_version", "v1")),
            "min_cell_sample": min_cell,
            "tiers": {k: v for k, v in config.get("tiers", {}).items()},
        },
        "dimension_tier_matrix": matrix,
        "by_tier_overall": by_tier_overall,
    }


def render_text_report(report: dict[str, Any]) -> str:
    """Render a human-readable text summary of the audit report."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("Strength-Gap Stratified Dimension Score Audit")
    lines.append(f"Generated: {report.get('generated_at_utc', 'N/A')}")
    lines.append("=" * 60)
    lines.append("")

    config = report.get("config_used", {})
    lines.append(f"Boundary version: {config.get('boundary_version')}")
    lines.append(f"Min cell sample:  {config.get('min_cell_sample')}")
    lines.append("Tiers: " + json.dumps(config.get("tiers", {}), ensure_ascii=False))
    lines.append("")

    lines.append("── Dimension × Tier Hit-Rate Matrix ──")
    lines.append("")

    matrix = report.get("dimension_tier_matrix", {})
    if not matrix:
        lines.append("  (no data)")
    else:
        # Determine tier order
        first_dim = next(iter(matrix))
        tiers = list(matrix[first_dim].keys())
        header = f"{'Dimension':<24}" + " ".join(f"{t:>14}" for t in tiers)
        lines.append(header)
        lines.append("-" * len(header))
        for dim, dim_cells in sorted(matrix.items()):
            parts = [f"{dim:<24}"]
            for tier in tiers:
                cell = dim_cells.get(tier, {})
                hr = cell.get("hit_rate")
                n = cell.get("n_scored", 0)
                if hr is not None:
                    ss = "*" if not cell.get("sample_sufficient") else ""
                    parts.append(f"{hr:.2f}(n{n:>2d}){ss:<1}")
                else:
                    parts.append(f"{'--':>14}")
            lines.append("".join(parts))

    lines.append("")
    lines.append("── Overall by Tier ──")
    overall = report.get("by_tier_overall", {})
    for tier, data in overall.items():
        hr = data.get("hit_rate")
        n = data.get("n_scored", 0)
        if hr is not None:
            lines.append(f"  {tier:<12}: hit_rate={hr:.4f}  n={n}")
        else:
            lines.append(f"  {tier:<12}: (no data)")

    lines.append("")
    lines.append(f"Disclaimer: {report.get('disclaimer', '')}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strength-gap stratified dimension score audit"
    )
    parser.add_argument("--ledger", type=Path, required=True,
                        help="Path to dimension_score_ledger.json")
    parser.add_argument("--config", type=Path,
                        help="Path to strength-gap-config.json")
    parser.add_argument("--output", type=Path,
                        help="Write JSON report (stdout if omitted)")
    parser.add_argument("--text", action="store_true",
                        help="Render human-readable text instead of JSON")
    args = parser.parse_args()

    config = load_config(args.config)
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    report = audit_by_strength_tier(ledger, config)

    if args.text:
        print(render_text_report(report))
    elif args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
