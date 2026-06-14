#!/usr/bin/env python3
"""WC26 market-profile bias mirror.

Read-only comparison between Path C market_profile and phase priors. The module
never mutates input profile and never emits reverse betting advice.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _pct_text(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except Exception:
        return "N/A"


def _total_profile(profile: dict[str, Any]) -> tuple[str, str]:
    lean = profile.get("total_line_lean") if isinstance(profile.get("total_line_lean"), dict) else {}
    direction = str(lean.get("lean") or "unknown").lower()
    label = str(lean.get("label") or ("Over" if direction == "over" else "Under" if direction == "under" else "N/A"))
    pct = lean.get("over_pct") if direction == "over" else lean.get("under_pct")
    if pct is None and direction == "over":
        pct = lean.get("prob_pct")
    if pct is None and direction == "under":
        pct = lean.get("prob_pct")
    return direction, f"{label} ({_pct_text(pct)})"


def _favorite_profile(profile: dict[str, Any]) -> tuple[str, str]:
    fav = profile.get("favorite_cover") if isinstance(profile.get("favorite_cover"), dict) else {}
    direction = str(fav.get("lean") or fav.get("direction") or "unknown").lower()
    label = str(fav.get("label") or fav.get("summary") or "N/A")
    return direction, label


def _alignment(profile_direction: str, prior_direction: str, confidence: str, dimension: str) -> str:
    if confidence == "provisional_low_n" or not prior_direction or prior_direction == "insufficient_data":
        return "NEUTRAL"
    if dimension == "total_goals":
        if profile_direction == "over" and prior_direction == "market_over_actual_under":
            return "CONTRADICTS"
        if profile_direction == "under" and prior_direction == "market_under_actual_over":
            return "CONTRADICTS"
        if profile_direction == "over" and prior_direction == "market_under_actual_over":
            return "CONFIRMS"
        if profile_direction == "under" and prior_direction == "market_over_actual_under":
            return "CONFIRMS"
    if dimension == "favorite_cover":
        if profile_direction in {"favorite", "fav", "cover"} and prior_direction == "market_overrates_cover":
            return "CONTRADICTS"
        if profile_direction in {"dog", "underdog", "fade_favorite"} and prior_direction == "market_underrates_cover":
            return "CONTRADICTS"
        if profile_direction in {"favorite", "fav", "cover"} and prior_direction == "market_underrates_cover":
            return "CONFIRMS"
        if profile_direction in {"dog", "underdog", "fade_favorite"} and prior_direction == "market_overrates_cover":
            return "CONFIRMS"
    return "NEUTRAL" if prior_direction == "aligned" else "NEUTRAL"


def _prior_text(dimension: str, prior: dict[str, Any]) -> str:
    direction = prior.get("bias_direction", "insufficient_data")
    sample_n = prior.get("sample_n", 0)
    value = prior.get("ledger_value")
    if dimension == "total_goals":
        if direction == "market_over_actual_under":
            phrase = "本阶段实际偏 Under"
        elif direction == "market_under_actual_over":
            phrase = "本阶段实际偏 Over"
        elif direction == "aligned":
            phrase = "本阶段与市场均值接近"
        else:
            phrase = "本阶段样本不足"
        return f"{phrase} (over25率{value}, n={sample_n})"
    if direction == "market_overrates_cover":
        phrase = "本阶段热门赢盘偏低"
    elif direction == "market_underrates_cover":
        phrase = "本阶段热门赢盘偏高"
    elif direction == "aligned":
        phrase = "本阶段热门赢盘接近中性"
    else:
        phrase = "本阶段样本不足"
    return f"{phrase} (cover率{value}, n={sample_n})"


def _read_text(alignment: str, dimension: str, confidence: str) -> str:
    if confidence == "provisional_low_n":
        return "样本不足,仅留痕,不下方向判断"
    if alignment == "CONTRADICTS":
        return "画像方向与本阶段已观察偏差相反 → 该倾向可信度降低"
    if alignment == "CONFIRMS":
        return "画像方向与本阶段已观察偏差一致 → 描述层互相印证"
    return "画像与阶段先验未形成有效同向/反向关系"


def analyze_bias_mirror(market_profile: dict[str, Any] | None, phase_context: dict[str, Any] | None) -> dict[str, Any]:
    profile = deepcopy(market_profile or {})
    phase = phase_context or {}
    priors = phase.get("phase_priors") if isinstance(phase.get("phase_priors"), dict) else {}
    mirrors: list[dict[str, Any]] = []

    total_prior = priors.get("total_goals") if isinstance(priors.get("total_goals"), dict) else {}
    total_dir, total_profile = _total_profile(profile)
    total_conf = str(total_prior.get("confidence") or "provisional_low_n")
    total_alignment = _alignment(total_dir, str(total_prior.get("bias_direction") or "insufficient_data"), total_conf, "total_goals")
    mirrors.append({
        "dimension": "total_goals",
        "profile_says": total_profile,
        "phase_prior_says": _prior_text("total_goals", total_prior),
        "alignment": total_alignment,
        "read_zh": _read_text(total_alignment, "total_goals", total_conf),
        "confidence": total_conf,
    })

    cover_prior = priors.get("favorite_cover") if isinstance(priors.get("favorite_cover"), dict) else {}
    fav_dir, fav_profile = _favorite_profile(profile)
    fav_conf = str(cover_prior.get("confidence") or "provisional_low_n")
    fav_alignment = _alignment(fav_dir, str(cover_prior.get("bias_direction") or "insufficient_data"), fav_conf, "favorite_cover")
    mirrors.append({
        "dimension": "favorite_cover",
        "profile_says": fav_profile,
        "phase_prior_says": _prior_text("favorite_cover", cover_prior),
        "alignment": fav_alignment,
        "read_zh": _read_text(fav_alignment, "favorite_cover", fav_conf),
        "confidence": fav_conf,
    })

    return {
        "artifact_field": "bias_mirror",
        "contract": "wc26.bias_mirror.v1",
        "mirrors": mirrors,
        "footnote_zh": "偏差校正镜·只读对照·不修改画像·非下注信号;反向仅表示可信度降低,不构成反向下注理由。",
    }
