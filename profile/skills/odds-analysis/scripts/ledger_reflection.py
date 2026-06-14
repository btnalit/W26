#!/usr/bin/env python3
"""Deterministic reflection-layer ledger statistics for WC26.

Pure functions only: no network, no LLM, no p_adj/gate mutation.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "reflection-layer-config.json"

DEFAULT_CONFIG = {
    "MIN_PHASE_SAMPLE": 12,
    "EMERGING_SAMPLE": 12,
    "ESTABLISHED_SAMPLE": 30,
    "MIN_STRATEGIC_N": 20,
    "DIRECTION_HIT_ALPHA_THRESHOLD": 0.55,
}


def load_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                merged.update(raw)
        except Exception:
            pass
    if isinstance(config, dict):
        merged.update(config)
    return merged


def confidence_for_sample(sample_n: int, config: dict[str, Any] | None = None) -> str:
    cfg = load_config(config)
    if sample_n < int(cfg["MIN_PHASE_SAMPLE"]):
        return "provisional_low_n"
    if sample_n < int(cfg["ESTABLISHED_SAMPLE"]):
        return "emerging"
    return "established"


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "yes", "y", "over", "covered", "hit"}:
            return True
        if low in {"false", "0", "no", "n", "under", "not_covered", "miss"}:
            return False
    return None


def _mean_bool(values: list[Any]) -> float | None:
    bools = [_bool_value(v) for v in values]
    clean = [v for v in bools if v is not None]
    if not clean:
        return None
    return sum(1 for v in clean if v) / len(clean)


def _mean_num(values: list[Any]) -> float | None:
    clean = [_num(v) for v in values]
    clean = [v for v in clean if v is not None]
    if not clean:
        return None
    return mean(clean)


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def total_bias_direction(ledger_value: float | None, market_implied_avg: float | None, tolerance: float = 0.05) -> str:
    if ledger_value is None or market_implied_avg is None:
        return "insufficient_data"
    diff = float(ledger_value) - float(market_implied_avg)
    if abs(diff) <= tolerance:
        return "aligned"
    if diff < 0:
        return "market_over_actual_under"
    return "market_under_actual_over"


def cover_bias_direction(fav_cover_rate: float | None, neutral: float = 0.5, tolerance: float = 0.05) -> str:
    if fav_cover_rate is None:
        return "insufficient_data"
    diff = float(fav_cover_rate) - neutral
    if abs(diff) <= tolerance:
        return "aligned"
    if diff < 0:
        return "market_overrates_cover"
    return "market_underrates_cover"


def ledger_phase_stats(phase: str, settled_ledger: list[dict[str, Any]] | None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = [row for row in (settled_ledger or []) if isinstance(row, dict) and str(row.get("phase")) == str(phase)]
    sample_n = len(rows)
    confidence = confidence_for_sample(sample_n, config)
    over25_rate = _mean_bool([row.get("actual_over25") for row in rows])
    market_over25_avg = _mean_num([row.get("market_over25_implied") for row in rows])
    fav_cover_rate = _mean_bool([row.get("favorite_covered_main_handicap") for row in rows])
    return {
        "total_goals": {
            "metric": "over25_rate",
            "ledger_value": _round(over25_rate),
            "market_implied_avg": _round(market_over25_avg),
            "bias_direction": total_bias_direction(over25_rate, market_over25_avg),
            "sample_n": sample_n,
            "confidence": confidence,
        },
        "favorite_cover": {
            "metric": "fav_handicap_cover_rate",
            "ledger_value": _round(fav_cover_rate),
            "bias_direction": cover_bias_direction(fav_cover_rate),
            "sample_n": sample_n,
            "confidence": confidence,
        },
    }


def strategic_signal(settled_ledger: list[dict[str, Any]] | None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = load_config(config)
    blocked = [row for row in (settled_ledger or []) if isinstance(row, dict) and row.get("no_play_type") == "directional_blocked"]
    hits = [row.get("post_result_direction_hit") for row in blocked]
    clean_hits = [_bool_value(v) for v in hits]
    clean_hits = [v for v in clean_hits if v is not None]
    n = len(blocked)
    hit_rate = (sum(1 for v in clean_hits if v) / len(clean_hits)) if clean_hits else None
    threshold_n = int(cfg["MIN_STRATEGIC_N"])
    alpha_threshold = float(cfg["DIRECTION_HIT_ALPHA_THRESHOLD"])
    if n < threshold_n:
        interpretation = "n<INSUFFICIENT → 样本不足,暂不解读"
    elif hit_rate is not None and hit_rate > alpha_threshold:
        interpretation = "hit_rate>0.55 → 分析层alpha可能真实,瓶颈在执行面(单平台/无软盘);可作为'是否投入解决多平台'的战略决策输入"
    else:
        interpretation = "hit_rate≈0.5 → 方向感无显著alpha,维持纯价格纪律"
    return {
        "directional_blocked_count": n,
        "direction_hit_rate": _round(hit_rate),
        "sample_n": n,
        "interpretation": interpretation,
        "disclaimer_zh": "此为系统自我评估,非下注信号;hit_rate高不代表单场可下注,仍需逐场price gate。",
    }
