#!/usr/bin/env python3
"""Build deterministic numeric artifacts for WC26 reports.

This is the calculation boundary: market numbers are computed here and then
referenced by the LLM/report. The LLM must not invent no-vig, EV, Kelly, or
Asian settlement numbers outside these artifacts.
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
DEVIG_PATH = SCRIPT_DIR / "devig.py"
spec = importlib.util.spec_from_file_location("devig", DEVIG_PATH)
devig = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(devig)


def stable_id(prefix: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def artifact_timestamp(args: argparse.Namespace) -> str:
    if getattr(args, "created_at_utc", None):
        return str(args.created_at_utc)
    return datetime.now(timezone.utc).isoformat()


def scalar_payload(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    decimal_odds = devig.normalize_odds(args.odds, args.odds_format)
    ensemble = devig.devig_three_method(decimal_odds)
    probs = ensemble["probabilities"]
    payload: dict[str, Any] = {
        "artifact_kind": "scalar_market",
        "source_snapshot_id": args.snapshot_id,
        "input_odds": args.odds,
        "input_odds_format": args.odds_format,
        "decimal_odds": decimal_odds,
        "odds_unit_contract": "all probability and EV math uses normalized decimal odds > 1.0",
        "no_vig_probabilities": probs,
        "devig_primary": ensemble["primary"],
        "devig_methods": ensemble["methods"],
        "devig_method_tolerance": ensemble["tolerance"],
        "devig_sensitivity_max_abs": ensemble["max_abs_delta"],
        "survives_all_methods": ensemble["survives_all_methods"],
        "overround": sum(1 / odd for odd in decimal_odds) - 1,
        "created_at_utc": artifact_timestamp(args),
    }
    numbers = [
        {
            "name": f"no_vig_{idx}",
            "kind": "no_vig",
            "value": prob,
            "snapshot_id": args.snapshot_id,
            "artifact_type": "devig",
            "probability_source": "market",
            "uses_p_model_directly": False,
        }
        for idx, prob in enumerate(probs)
    ]
    if args.prob is not None and args.price is not None:
        price_decimal = devig.to_decimal(args.price, args.price_format)
        payload["probability_used"] = args.prob
        payload["probability_contract"] = "p_adj"
        payload["price_input"] = args.price
        payload["price_input_format"] = args.price_format
        payload["price_decimal"] = price_decimal
        payload["ev"] = devig.ev(args.prob, price_decimal)
        numbers.append(
            {
                "name": "scalar_ev",
                "kind": "scalar_ev",
                "value": payload["ev"],
                "snapshot_id": args.snapshot_id,
                "artifact_type": "devig",
                "probability_source": "p_adj",
                "uses_p_model_directly": False,
            }
        )
    return payload, numbers


def ah_payload(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    margin_probs = devig.parse_margin_probs(args.margin_probs_json)
    price_decimal = devig.to_decimal(args.ah_price, args.ah_price_format)
    payload = {
        "artifact_kind": "asian_handicap_market",
        "source_snapshot_id": args.snapshot_id,
        "odds_unit_contract": "all probability and EV math uses normalized decimal odds > 1.0",
        "settlement_contract": "asian_handicap_by_legs",
        "line": args.ah_line,
        "legs": devig.asian_handicap_legs(args.ah_line),
        "price_input": args.ah_price,
        "price_input_format": args.ah_price_format,
        "price": price_decimal,
        "margin_probabilities": {str(margin): margin_probs[margin] for margin in sorted(margin_probs)},
        "returns_by_margin": {str(margin): devig.settlement_return(margin, args.ah_line, price_decimal) for margin in sorted(margin_probs)},
        "ev": devig.asian_handicap_ev(margin_probs, args.ah_line, price_decimal),
        "kelly_fraction_full": devig.asian_handicap_kelly(margin_probs, args.ah_line, price_decimal),
        "created_at_utc": artifact_timestamp(args),
    }
    numbers = [
        {
            "name": "asian_handicap_ev",
            "kind": "asian_handicap_ev",
            "value": payload["ev"],
            "snapshot_id": args.snapshot_id,
            "artifact_type": "devig",
            "probability_source": "adjustment_ledger",
            "uses_p_model_directly": False,
        },
        {
            "name": "asian_handicap_kelly",
            "kind": "asian_handicap_kelly",
            "value": payload["kelly_fraction_full"],
            "snapshot_id": args.snapshot_id,
            "artifact_type": "devig",
            "probability_source": "adjustment_ledger",
            "uses_p_model_directly": False,
        },
    ]
    return payload, numbers


def crossbook_payload(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a cross-book scan artifact from cross_book_scan.py output JSON."""
    import json
    with open(args.crossbook_json) as f:
        scan_result = json.load(f)

    payload: dict[str, Any] = {
        "artifact_kind": "cross_book_scan",
        "source_snapshot_id": args.snapshot_id,
        "created_at_utc": artifact_timestamp(args),
        "match_id": args.match_id,
    }
    numbers: list[dict[str, Any]] = []

    if scan_result.get("status") == "ok" if isinstance(scan_result, dict) else False:
        payload["scan_summary"] = {
            "markets_scanned": list(scan_result.get("markets", {}).keys()),
        }
        for market_key, market_result in scan_result.get("markets", {}).items():
            if market_result.get("status") != "ok":
                continue
            # Record fair probabilities for reference
            payload.setdefault("fair_probs", {})[market_key] = market_result.get("fair_probs")

            for edge in market_result.get("edges", []):
                if edge.get("survives_all_methods") and not edge.get("suspect"):
                    numbers.append({
                        "name": f"crossbook_{edge['book']}_{market_key}_{edge['outcome'].replace(' ', '_')}",
                        "kind": "crossbook_edge",
                        "value": edge["ev_shin"],
                        "snapshot_id": args.snapshot_id,
                        "artifact_type": "crossbook_scan",
                        "probability_source": "crossbook_scan",
                        "uses_p_model_directly": False,
                        "survives_all_methods": edge["survives_all_methods"],
                        "suspect": edge["suspect"],
                        "book": edge["book"],
                        "market_key": market_key,
                        "outcome": edge["outcome"],
                        "offered_odds": edge["offered_odds"],
                        "tier": edge.get("book_tier", "soft"),
                    })
    else:
        payload["scan_error"] = str(scan_result)

    return payload, numbers


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="kind", required=True)

    scalar = sub.add_parser("scalar")
    scalar.add_argument("--snapshot-id", required=True)
    scalar.add_argument("--out-dir", type=Path, required=True)
    scalar.add_argument("--odds", nargs="+", type=float, required=True)
    scalar.add_argument("--odds-format", default="decimal", choices=sorted(devig.SUPPORTED_ODDS_FORMATS))
    scalar.add_argument("--prob", type=float)
    scalar.add_argument("--price", type=float)
    scalar.add_argument("--price-format", default="decimal", choices=sorted(devig.SUPPORTED_ODDS_FORMATS))
    scalar.add_argument("--created-at-utc", help="Stable artifact timestamp for reproducible pipeline runs.")

    ah = sub.add_parser("ah")
    ah.add_argument("--snapshot-id", required=True)
    ah.add_argument("--out-dir", type=Path, required=True)
    ah.add_argument("--ah-line", type=float, required=True)
    ah.add_argument("--ah-price", type=float, required=True)
    ah.add_argument("--ah-price-format", default="decimal", choices=sorted(devig.SUPPORTED_ODDS_FORMATS))
    ah.add_argument("--margin-probs-json", required=True)
    ah.add_argument("--created-at-utc", help="Stable artifact timestamp for reproducible pipeline runs.")

    crossbook = sub.add_parser("crossbook")
    crossbook.add_argument("--snapshot-id", required=True)
    crossbook.add_argument("--out-dir", type=Path, required=True)
    crossbook.add_argument("--crossbook-json", required=True,
                          help="cross_book_scan.py output JSON path")
    crossbook.add_argument("--match-id", required=True)
    crossbook.add_argument("--created-at-utc",
                          help="Stable artifact timestamp for reproducible pipeline runs.")

    args = parser.parse_args()

    # Dispatch: each payload function returns (payload, numbers)
    kind_dispatch = {
        "scalar": scalar_payload,
        "ah": ah_payload,
        "crossbook": crossbook_payload,
    }
    if args.kind not in kind_dispatch:
        parser.error(f"unknown kind: {args.kind}")

    payload, numbers = kind_dispatch[args.kind](args)

    # Generate stable artifact ID
    artifact_id = stable_id("devig" if args.kind != "crossbook" else "crossbook", payload)
    path = args.out_dir / f"{artifact_id.replace(':', '-')}.json"
    payload["artifact_id"] = artifact_id
    write_json(path, payload)

    for number in numbers:
        number["artifact_id"] = artifact_id

    artifact_type = "crossbook_scan" if args.kind == "crossbook" else "devig"
    script_name = "cross_book_scan.py" if args.kind == "crossbook" else "devig.py"

    result = {
        "artifact": {
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "script": script_name,
            "path": str(path),
            "source_snapshot_id": args.snapshot_id,
        },
        "numbers": numbers,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
