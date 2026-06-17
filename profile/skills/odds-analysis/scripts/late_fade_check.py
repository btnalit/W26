#!/usr/bin/env python3
"""late_fade_check.py — T-45m/T-60m lopsided-favorite fade detector.

Reads the current snapshot and scans historical snapshots for the earliest
available Pinnacle H2H odds.  When a lopsided favourite has been faded ≥2pp
from the early line, emits an advisory signal.

This is a read-only diagnostic check.  It never produces actionable betting
instructions on its own — it flags a pattern the analyst should review.

Usage:
  python3 late_fade_check.py \\
    --snapshot snapshots/odds/the-odds-api-multibook-20260616T184515Z.json \\
    --match-home "France" --match-away "Senegal" \\
    --match-id M017 --window T-45m_price_guard \\
    --workspace /hermesdata/worldcup-2026-handicap \\
    --output reports/artifacts/fade-M017-T-45m.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_SCRIPTS = SCRIPT_DIR


def _load_devig():
    spec = importlib.util.spec_from_file_location("devig", SKILL_SCRIPTS / "devig.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_devig = _load_devig()
devig_shin = _devig.devig_shin


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stable_id(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def parse_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


# ── threshold constants ──
LOPSIDED_GAP = 0.50       # |p_fav - p_dog| >= this → lopsided
FADE_THRESHOLD_PP = 2.0   # favourite prob drop >= this pp → signal


def extract_pinnacle_h2h(match: dict) -> list[float] | None:
    """Return [home, away, draw] decimal odds from Pinnacle H2H."""
    for bm in match.get("bookmakers", []):
        if bm.get("key") == "pinnacle":
            for mk in bm.get("markets", []):
                if mk.get("key") == "h2h":
                    return [o["price"] for o in mk["outcomes"]]
    return None


def find_match_in_snapshot(
    snapshot_path: Path, home: str, away: str
) -> dict | None:
    raw = load_json(snapshot_path)
    if raw is None:
        return None
    data = raw if isinstance(raw, list) else raw.get("data", [])
    for m in data:
        if not isinstance(m, dict):
            continue
        h = (m.get("home_team") or "").strip()
        a = (m.get("away_team") or "").strip()
        if h.lower() == home.lower() and a.lower() == away.lower():
            return m
    return None


def scan_earliest_snapshot(
    workspace: Path, home: str, away: str, ko_utc: str | None
) -> tuple[Path | None, list[float] | None, str | None]:
    """Find the earliest pre-KO snapshot with Pinnacle H2H for this match."""
    snap_dir = workspace / "snapshots" / "odds"
    if not snap_dir.exists():
        return None, None, None

    ko = parse_time(ko_utc)
    best: tuple[datetime, Path, list[float]] | None = None

    for sp in sorted(snap_dir.glob("the-odds-api-multibook-*.json")):
        fn = sp.name
        ts_str = fn.replace("the-odds-api-multibook-", "").replace(".json", "")
        snap_time = parse_time(ts_str)
        if snap_time is None:
            continue
        if ko is not None and snap_time >= ko:
            continue  # post-KO, skip

        m = find_match_in_snapshot(sp, home, away)
        if m is None:
            continue
        odds = extract_pinnacle_h2h(m)
        if odds is None:
            continue

        if best is None or snap_time < best[0]:
            best = (snap_time, sp, odds)

    if best is None:
        return None, None, None
    return best[1], best[2], best[0].strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_fade_signal(
    early_odds: list[float],
    current_odds: list[float],
) -> dict[str, Any]:
    """Compute whether a lopsided favorite has been faded ≥ threshold."""
    early_probs = devig_shin(early_odds)   # [home, away, draw]
    current_probs = devig_shin(current_odds)

    # Determine favourite side from early odds
    if early_probs[0] >= early_probs[1]:
        fav_idx = 0
        dog_idx = 1
        fav_side = "home"
    else:
        fav_idx = 1
        dog_idx = 0
        fav_side = "away"

    gap_early = early_probs[fav_idx] - early_probs[dog_idx]
    gap_current = current_probs[fav_idx] - current_probs[dog_idx]
    is_lopsided_early = gap_early >= LOPSIDED_GAP

    fav_drop_pp = (current_probs[fav_idx] - early_probs[fav_idx]) * 100
    fade_detected = is_lopsided_early and fav_drop_pp <= -FADE_THRESHOLD_PP

    return {
        "early_odds": early_odds,
        "current_odds": current_odds,
        "early_probs": {"home": early_probs[0], "away": early_probs[1], "draw": early_probs[2]},
        "current_probs": {"home": current_probs[0], "away": current_probs[1], "draw": current_probs[2]},
        "favorite_side": fav_side,
        "favorite_idx": fav_idx,
        "gap_early": round(gap_early, 4),
        "gap_current": round(gap_current, 4),
        "is_lopsided_early": is_lopsided_early,
        "favorite_prob_drop_pp": round(fav_drop_pp, 2),
        "fade_detected": fade_detected,
        "fade_threshold_pp": FADE_THRESHOLD_PP,
    }


def render_zh_signal(result: dict) -> str:
    """Render a one-line Chinese signal summary."""
    fav_side_cn = "主队" if result["favorite_side"] == "home" else "客队"
    drop = abs(result["favorite_prob_drop_pp"])
    gap = result["gap_early"]

    if not result["fade_detected"]:
        if not result["is_lopsided_early"]:
            return f"非悬殊盘（早期实力差 {gap:.2f} < {LOPSIDED_GAP}），跳过临场褪水检查"
        else:
            return f"悬殊盘（早期实力差 {gap:.2f}），但{fav_side_cn}概率仅下降 {drop:.1f}pp（<{FADE_THRESHOLD_PP}pp 阈值），无褪水信号"

    return (
        f"⚠️ 临场褪水信号：早期悬殊盘（实力差 {gap:.2f}），"
        f"{fav_side_cn}概率已从早期 {result['early_probs'][result['favorite_side']]:.1%} "
        f"降至当前 {result['current_probs'][result['favorite_side']]:.1%} "
        f"（-{drop:.1f}pp ≥ {FADE_THRESHOLD_PP}pp 阈值）"
        f"——历史数据显示此类褪水≠强队翻车，建议关注反向机会"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="T-45m/T-60m lopsided-favorite fade detector")
    parser.add_argument("--snapshot", required=True, help="Current snapshot path (absolute or workspace-relative)")
    parser.add_argument("--match-home", required=True)
    parser.add_argument("--match-away", required=True)
    parser.add_argument("--match-id", default="UNKNOWN")
    parser.add_argument("--window", default="T-45m_price_guard")
    parser.add_argument("--kickoff-utc", default=None, help="Optional KO time to exclude post-KO snapshots")
    parser.add_argument("--workspace", default=os.environ.get("WC26_WORKSPACE", "/hermesdata/worldcup-2026-handicap"))
    parser.add_argument("--output", required=True, help="Output JSON artifact path")
    args = parser.parse_args()

    workspace = Path(args.workspace)
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_absolute():
        snapshot_path = workspace / snapshot_path

    # ── Load current snapshot ──
    current_match = find_match_in_snapshot(snapshot_path, args.match_home, args.match_away)
    if current_match is None:
        result = {
            "artifact_type": "late_fade_check",
            "artifact_id": f"fade:{args.match_id}:{stable_id(args.match_home + args.match_away)}",
            "match_id": args.match_id,
            "home": args.match_home,
            "away": args.match_away,
            "window": args.window,
            "generated_at_utc": utc_now(),
            "snapshot_path": str(snapshot_path),
            "status": "skipped",
            "reason": "current_snapshot_match_not_found",
        }
        write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    current_odds = extract_pinnacle_h2h(current_match)
    if current_odds is None:
        result = {
            "artifact_type": "late_fade_check",
            "artifact_id": f"fade:{args.match_id}:{stable_id(args.match_home + args.match_away)}",
            "match_id": args.match_id,
            "home": args.match_home,
            "away": args.match_away,
            "window": args.window,
            "generated_at_utc": utc_now(),
            "snapshot_path": str(snapshot_path),
            "status": "skipped",
            "reason": "current_pinnacle_h2h_missing",
        }
        write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # ── Find earliest snapshot ──
    early_path, early_odds, early_ts = scan_earliest_snapshot(
        workspace, args.match_home, args.match_away, args.kickoff_utc
    )

    if early_odds is None:
        result = {
            "artifact_type": "late_fade_check",
            "artifact_id": f"fade:{args.match_id}:{stable_id(args.match_home + args.match_away)}",
            "match_id": args.match_id,
            "home": args.match_home,
            "away": args.match_away,
            "window": args.window,
            "generated_at_utc": utc_now(),
            "snapshot_path": str(snapshot_path),
            "status": "skipped",
            "reason": "no_early_snapshot_found",
        }
        write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # ── Compute signal ──
    signal = compute_fade_signal(early_odds, current_odds)
    zh_signal = render_zh_signal(signal)

    result = {
        "artifact_type": "late_fade_check",
        "artifact_contract": "wc26.late_fade_check.v1",
        "artifact_id": f"fade:{args.match_id}:{stable_id(args.match_home + args.match_away)}",
        "match_id": args.match_id,
        "home": args.match_home,
        "away": args.match_away,
        "window": args.window,
        "generated_at_utc": utc_now(),
        "snapshot_path": str(snapshot_path),
        "early_snapshot_path": str(early_path) if early_path else None,
        "early_snapshot_captured_at_utc": early_ts,
        "status": "ok",
        "signal": signal,
        "summary_zh": zh_signal,
    }

    write_json(Path(args.output), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
