#!/usr/bin/env python3
"""
consistency_triangle.py — 三市场一致性三角探测器

从 Pinnacle 的 AH + 1X2 反推 λ_home/λ_away（双变量 Poisson 拟合），
与相同快照中的大小球市场对校，输出修盘方向 × 类型。

数据边界（不可违反）:
  - 只能做同一快照内、同一书商的数据
  - Pinnacle 是唯一被认可的 sharp 参考源
  - Poisson 假设是结构近似，不是事实
  - 输出不是 EV，是修盘方向 × 强度信号

输出类型:
  - "人性税": 大小盘被公众偏见修过 → 价值在反方向
  - "庄家修盘": 让球/胜负与大小不一致，deviation > 阈值
  - "AI滞后": 某个具体比分/线路落后于主盘结构（仅参考）
  - "噪声": 偏差在模型误差范围内，忽略
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import sys
from pathlib import Path
from typing import Any

# ---------- constants ----------

# 偏差阈值（与 SOUL.md / MEMORY.md 一致）
THRESHOLD_NOISE = 0.05       # < 5pp → 噪声
THRESHOLD_WEAK = 0.08        # 5-8pp → 弱信号
THRESHOLD_MEDIUM = 0.13      # 8-13pp → 中信号
                              # > 13pp → 强信号

# Poisson 截断上限
MAX_GOALS = 15
MARKET_PROFILE_CONTRACT = "wc26.market_profile.v1"
RHO_GRID = [round(-0.15 + i * 0.03, 2) for i in range(6)]  # [-0.15, 0.00]
FIT_SUPPRESS_THRESHOLD_PP = 8.0
FIT_LOW_THRESHOLD_PP = 5.0
FIT_MEDIUM_THRESHOLD_PP = 3.0

# 已知的公众偏见方向
# 如果偏差方向与公众偏见一致 → 提高信号置信度
PUBLIC_BIAS = {
    "over_bias": True,        # 散户系统性偏爱 Over
    "favorite_bias": True,    # 散户系统性偏爱强队/热门
    "dog_bias": False,        # 冷门被系统性低估
}


# ---------- math helpers ----------

def no_vig(decimal_odds: list[float]) -> tuple[list[float], float]:
    """Simple proportional no-vig."""
    implied = [1.0 / x for x in decimal_odds]
    total = sum(implied)
    return [x / total for x in implied], total - 1.0


@functools.lru_cache(maxsize=None)
def poisson_prob(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def score_matrix(lam_home: float, lam_away: float, rho: float = 0.0) -> list[list[float]]:
    """Deterministic Poisson/Dixon-Coles score matrix."""
    matrix = [
        [poisson_prob(h, lam_home) * poisson_prob(a, lam_away) for a in range(MAX_GOALS + 1)]
        for h in range(MAX_GOALS + 1)
    ]
    if rho:
        tau = {
            (0, 0): 1 - lam_home * lam_away * rho,
            (0, 1): 1 + lam_home * rho,
            (1, 0): 1 + lam_away * rho,
            (1, 1): 1 - rho,
        }
        for (h, a), factor in tau.items():
            matrix[h][a] *= max(0.0, factor)
    total = sum(matrix[h][a] for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1))
    if total <= 0:
        return matrix
    return [[matrix[h][a] / total for a in range(MAX_GOALS + 1)] for h in range(MAX_GOALS + 1)]


def project_1x2_matrix(matrix: list[list[float]]) -> tuple[float, float, float]:
    home = draw = away = 0.0
    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            prob = matrix[h][a]
            if h > a:
                home += prob
            elif h == a:
                draw += prob
            else:
                away += prob
    return home, draw, away


def total_goal_distribution(matrix: list[list[float]]) -> dict[int, float]:
    dist: dict[int, float] = {}
    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            dist[h + a] = dist.get(h + a, 0.0) + matrix[h][a]
    return dist


def total_leg_score(total_goals: int, line: float, side: str) -> float:
    """Map one total line leg to an even-money settlement-equivalent score."""
    if side == "over":
        if total_goals > line:
            return 1.0
        if total_goals == line:
            return 0.5
        return 0.0
    if total_goals < line:
        return 1.0
    if total_goals == line:
        return 0.5
    return 0.0


def split_quarter_line(line: float) -> list[float]:
    doubled = round(line * 2)
    if abs(line * 2 - doubled) < 1e-9:
        return [float(line)]
    lower = math.floor(line * 2) / 2.0
    upper = math.ceil(line * 2) / 2.0
    return [lower, upper]


def total_settlement_equiv(matrix: list[list[float]], line: float, side: str) -> float:
    """Settlement-equivalent probability for half/integer/quarter totals."""
    legs = split_quarter_line(float(line))
    dist = total_goal_distribution(matrix)
    score = 0.0
    for total_goals, prob in dist.items():
        leg_score = sum(total_leg_score(total_goals, leg, side) for leg in legs) / len(legs)
        score += prob * leg_score
    return score


def skellam_probs(
    lam_home: float, lam_away: float
) -> tuple[float, float, float, dict[int, float]]:
    """Compute home_win/draw/away_win probabilities from Poisson λs."""
    probs: dict[int, float] = {}
    total = 0.0
    for h in range(MAX_GOALS + 1):
        ph = poisson_prob(h, lam_home)
        for a in range(MAX_GOALS + 1):
            p = ph * poisson_prob(a, lam_away)
            diff = h - a
            probs[diff] = probs.get(diff, 0.0) + p
            total += p
    for d in probs:
        probs[d] /= total
    p_home = sum(probs.get(d, 0.0) for d in probs if d > 0)
    p_draw = probs.get(0, 0.0)
    p_away = sum(probs.get(d, 0.0) for d in probs if d < 0)
    return p_home, p_draw, p_away, probs


def total_goals_metrics(
    lam_home: float, lam_away: float, line: float
) -> dict[str, float]:
    """From λs, compute expected total goals and P(over line)."""
    total_prob: dict[int, float] = {}
    for h in range(MAX_GOALS + 1):
        ph = poisson_prob(h, lam_home)
        for a in range(MAX_GOALS + 1):
            p = ph * poisson_prob(a, lam_away)
            t = h + a
            total_prob[t] = total_prob.get(t, 0.0) + p
    norm = sum(total_prob.values())
    for t in total_prob:
        total_prob[t] /= norm

    exp_total = sum(t * total_prob[t] for t in total_prob)
    p_over = sum(
        total_prob.get(t, 0.0) for t in range(int(math.ceil(line + 0.001)), MAX_GOALS + 1)
    )
    p_under = 1.0 - p_over
    return {
        "expected_total_goals": round(exp_total, 2),
        f"p_over_{line}": round(p_over, 4),
        f"p_under_{line}": round(p_under, 4),
    }


def _pct(value: float) -> float:
    return round(value * 100, 1)


def _prob(value: float) -> float:
    return round(value, 4)


def _fair_odds(probability: float) -> float | None:
    if probability <= 0:
        return None
    return round(1.0 / probability, 2)


def _fit_confidence(max_abs_residual_pp: float) -> str:
    if max_abs_residual_pp > FIT_SUPPRESS_THRESHOLD_PP:
        return "suppressed"
    if max_abs_residual_pp > FIT_LOW_THRESHOLD_PP:
        return "low"
    if max_abs_residual_pp > FIT_MEDIUM_THRESHOLD_PP:
        return "medium"
    return "high"


def fit_market_profile(
    p_home: float,
    p_draw: float,
    p_away: float,
    total_line: float,
    total_over_no_vig: float,
) -> dict[str, Any]:
    """
    Deterministically fit a market-profile matrix to sharp 1X2 + totals.

    This is separate from the existing Path C discrepancy fit. Path C still uses
    AH+1X2 -> Totals to detect structure breaks. The market profile is only a
    descriptive projection of what the sharp markets imply when jointly fit.
    """
    targets = {
        "home": float(p_home),
        "draw": float(p_draw),
        "away": float(p_away),
        "over_settlement_equiv": float(total_over_no_vig),
        "total_line": float(total_line),
    }

    def score(lh: float, la: float, rho: float) -> tuple[float, tuple[float, float, float, float]]:
        matrix = score_matrix(lh, la, rho)
        ph, pd, pa = project_1x2_matrix(matrix)
        p_over = total_settlement_equiv(matrix, total_line, "over")
        err = (
            (ph - p_home) ** 2
            + (pd - p_draw) ** 2
            + (pa - p_away) ** 2
            + (p_over - total_over_no_vig) ** 2
        )
        return err, (ph, pd, pa, p_over)

    best = {
        "err": float("inf"),
        "lambda_home": 1.0,
        "lambda_away": 1.0,
        "rho": 0.0,
        "projected": (0.0, 0.0, 0.0, 0.0),
    }

    for lh_i in range(3, 26):
        lh = round(lh_i * 0.2, 1)
        for la_i in range(2, 21):
            la = round(la_i * 0.2, 1)
            for rho in RHO_GRID:
                err, projected = score(lh, la, rho)
                key = (err, lh, la, rho)
                best_key = (best["err"], best["lambda_home"], best["lambda_away"], best["rho"])
                if key < best_key:
                    best.update(
                        {
                            "err": err,
                            "lambda_home": lh,
                            "lambda_away": la,
                            "rho": rho,
                            "projected": projected,
                        }
                    )

    # Deterministic local refinement around the best coarse grid point.
    lh_center = float(best["lambda_home"])
    la_center = float(best["lambda_away"])
    rho_center = float(best["rho"])
    lh_values = sorted({round(lh_center + step * 0.05, 2) for step in range(-4, 5) if 0.2 <= lh_center + step * 0.05 <= 6.0})
    la_values = sorted({round(la_center + step * 0.05, 2) for step in range(-4, 5) if 0.2 <= la_center + step * 0.05 <= 6.0})
    rho_values = sorted({round(max(-0.15, min(0.0, rho_center + step * 0.01)), 2) for step in range(-3, 4)})
    for lh in lh_values:
        for la in la_values:
            for rho in rho_values:
                err, projected = score(lh, la, rho)
                key = (err, lh, la, rho)
                best_key = (best["err"], best["lambda_home"], best["lambda_away"], best["rho"])
                if key < best_key:
                    best.update(
                        {
                            "err": err,
                            "lambda_home": lh,
                            "lambda_away": la,
                            "rho": rho,
                            "projected": projected,
                        }
                    )

    ph, pd, pa, p_over = best["projected"]
    residuals_pp = {
        "home": _pct(ph - p_home),
        "draw": _pct(pd - p_draw),
        "away": _pct(pa - p_away),
        "over_settlement_equiv": _pct(p_over - total_over_no_vig),
    }
    max_abs = round(max(abs(v) for v in residuals_pp.values()), 1)
    rms = round(math.sqrt(sum(v * v for v in residuals_pp.values()) / len(residuals_pp)), 1)
    confidence = _fit_confidence(max_abs)
    return {
        "method": "deterministic_grid_poisson_dc_1x2_totals_v1",
        "contract": MARKET_PROFILE_CONTRACT,
        "lambda_home": round(float(best["lambda_home"]), 2),
        "lambda_away": round(float(best["lambda_away"]), 2),
        "rho": round(float(best["rho"]), 2),
        "rho_bounds": [-0.15, 0.0],
        "targets": {k: _prob(v) if isinstance(v, float) else v for k, v in targets.items()},
        "projected": {
            "home": _prob(ph),
            "draw": _prob(pd),
            "away": _prob(pa),
            "over_settlement_equiv": _prob(p_over),
            "under_settlement_equiv": _prob(1.0 - p_over),
        },
        "residuals_pp": residuals_pp,
        "max_abs_residual_pp": max_abs,
        "rms_residual_pp": rms,
        "confidence": confidence,
        "suppressed": confidence == "suppressed",
    }


def build_market_profile(
    fit: dict[str, Any],
    home_name: str,
    away_name: str,
    total_line: float,
) -> dict[str, Any]:
    """Build a descriptive, non-betting market profile from a validated fit."""
    base = {
        "artifact_field": "market_profile",
        "contract": MARKET_PROFILE_CONTRACT,
        "status": "suppressed" if fit.get("suppressed") else "ok",
        "confidence": fit.get("confidence", "unknown"),
        "fit": fit,
        "footnote_zh": "市场共识画像·描述性·非下注信号；最高概率不等于价值。",
    }
    if fit.get("suppressed"):
        base["reason"] = "fit_residual_gt_8pp"
        return base

    matrix = score_matrix(float(fit["lambda_home"]), float(fit["lambda_away"]), float(fit["rho"]))
    ph, pd, pa = project_1x2_matrix(matrix)
    outcomes = [
        {"key": "home", "label": f"{home_name}胜", "prob": ph},
        {"key": "draw", "label": "平局", "prob": pd},
        {"key": "away", "label": f"{away_name}胜", "prob": pa},
    ]
    outcomes.sort(key=lambda item: (-item["prob"], item["key"]))

    p_over = total_settlement_equiv(matrix, total_line, "over")
    p_under = total_settlement_equiv(matrix, total_line, "under")
    total_lean = "over" if p_over >= p_under else "under"
    total_lean_label = f"{'Over' if total_lean == 'over' else 'Under'} {total_line:g}"

    total_dist = total_goal_distribution(matrix)
    top_total_goals = sorted(total_dist.items(), key=lambda item: (-item[1], item[0]))[:3]

    margin_dist: dict[int, float] = {}
    btts_yes = 0.0
    score_rows: list[dict[str, Any]] = []
    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            prob = matrix[h][a]
            margin_dist[h - a] = margin_dist.get(h - a, 0.0) + prob
            if h > 0 and a > 0:
                btts_yes += prob
            score_rows.append(
                {
                    "home_goals": h,
                    "away_goals": a,
                    "score": f"{h}-{a}",
                    "prob": prob,
                    "fair_odds": _fair_odds(prob),
                }
            )

    top_margin_value, top_margin_prob = sorted(margin_dist.items(), key=lambda item: (-item[1], item[0]))[0]
    if top_margin_value == 0:
        top_margin_label = "平局 净0"
    elif top_margin_value > 0:
        top_margin_label = f"{home_name} 净胜{top_margin_value}"
    else:
        top_margin_label = f"{away_name} 净胜{abs(top_margin_value)}"

    score_rows.sort(key=lambda item: (-item["prob"], item["home_goals"], item["away_goals"]))
    top_scores = []
    for row in score_rows[:6]:
        top_scores.append(
            {
                "score": row["score"],
                "home_goals": row["home_goals"],
                "away_goals": row["away_goals"],
                "prob": _prob(row["prob"]),
                "prob_pct": _pct(row["prob"]),
                "fair_odds": row["fair_odds"],
            }
        )

    base.update(
        {
            "lambda_home": fit["lambda_home"],
            "lambda_away": fit["lambda_away"],
            "rho": fit["rho"],
            "most_likely_1x2": {
                "key": outcomes[0]["key"],
                "label": outcomes[0]["label"],
                "prob": _prob(outcomes[0]["prob"]),
                "prob_pct": _pct(outcomes[0]["prob"]),
                "alternatives": [
                    {"key": item["key"], "label": item["label"], "prob": _prob(item["prob"]), "prob_pct": _pct(item["prob"])}
                    for item in outcomes
                ],
            },
            "total_line_lean": {
                "line": float(total_line),
                "lean": total_lean,
                "label": total_lean_label,
                "over_settlement_equiv": _prob(p_over),
                "over_pct": _pct(p_over),
                "under_settlement_equiv": _prob(p_under),
                "under_pct": _pct(p_under),
            },
            "top_total_goals": [
                {"goals": goals, "prob": _prob(prob), "prob_pct": _pct(prob)}
                for goals, prob in top_total_goals
            ],
            "top_margin": {
                "margin": top_margin_value,
                "label": top_margin_label,
                "prob": _prob(top_margin_prob),
                "prob_pct": _pct(top_margin_prob),
            },
            "btts": {
                "yes": _prob(btts_yes),
                "yes_pct": _pct(btts_yes),
                "no": _prob(1.0 - btts_yes),
                "no_pct": _pct(1.0 - btts_yes),
                "lean": "yes" if btts_yes >= 0.5 else "no",
            },
            "top_scores": top_scores,
        }
    )
    return base


def fit_lambda(
    p_home: float, p_draw: float, p_away: float,
    spread_prob_favorite: float | None = None,
    spread_line_favorite: float | None = None,
) -> dict[str, Any]:
    """
    Grid search for best (λ_home, λ_away) fitting the 1X2 constraints.
    Optionally also fits spread constraint if provided.
    
    spread_prob_favorite: no-vig probability that the favorite covers the spread
    spread_line_favorite: the AH line in favor of the favorite (e.g. -0.75 means favorite -0.75)
    """
    best_lh = 1.0
    best_la = 1.0
    best_err = 999.0

    step = 0.1
    for lh in [round(x * step, 1) for x in range(5, 50)]:
        for la in [round(x * step, 1) for x in range(5, 40)]:
            pw, pd, pa, margin_probs = skellam_probs(lh, la)
            # Error: squared diff from 1X2 targets
            err = (pw - p_home) ** 2 + (pd - p_draw) ** 2 + (pa - p_away) ** 2

            # Optionally add spread constraint
            if spread_prob_favorite is not None and spread_line_favorite is not None:
                line = spread_line_favorite
                # For a negative line (favorite -X), cover means:
                # favorite wins by > X
                # Convert Asian line to margin constraint
                if line < 0:
                    # line = -0.75 means favorite -0.75
                    # For two-leg AH: -0.75 splits into -0.5 and -1.0
                    legs_abs = [abs(line) - 0.25, abs(line) + 0.25] if line % 0.5 != 0 else [abs(line)]
                    legs = [-l for l in legs_abs]
                else:
                    legs = [line - 0.25, line + 0.25] if line % 0.5 != 0 else [line]
                
                # For favorite (negative line), cover means:
                # home margin > line_absolute (with leg splits for quarter lines)
                if line < 0:
                    abs_line = -line
                    # Quarter-line split: -0.75 = 50% on -0.5, 50% on -1.0
                    if abs_line % 0.5 != 0:
                        leg1 = -(abs_line - 0.25)
                        leg2 = -(abs_line + 0.25)
                        # For -0.75: leg1 = -0.5 (win if diff ≥ 0), leg2 = -1.0 (win if diff ≥ 1)
                        p_leg1_win = sum(margin_probs.get(d, 0) for d in margin_probs if d >= -leg1)
                        p_leg2_win = sum(margin_probs.get(d, 0) for d in margin_probs if d >= -leg2)
                        p_cover = 0.5 * p_leg1_win + 0.5 * p_leg2_win
                    else:
                        p_leg_win = sum(margin_probs.get(d, 0) for d in margin_probs if d >= abs_line)
                        p_cover = p_leg_win
                else:  # positive line = dog gets points
                    # Underdog +0.75 cover when home doesn't win by more than 0
                    if line % 0.5 != 0:
                        leg1 = line - 0.25  # 0.5
                        leg2 = line + 0.25  # 1.0
                        p_leg1_win = sum(margin_probs.get(d, 0) for d in margin_probs if d < leg1)
                        p_leg2_win = sum(margin_probs.get(d, 0) for d in margin_probs if d < leg2)
                        p_cover = 0.5 * p_leg1_win + 0.5 * p_leg2_win
                    else:
                        p_cover = sum(margin_probs.get(d, 0) for d in margin_probs if d < line)
                
                err += (p_cover - spread_prob_favorite) ** 2

            if err < best_err:
                best_err = err
                best_lh = lh
                best_la = la

    pw, pd, pa, margin_probs = skellam_probs(best_lh, best_la)
    return {
        "lambda_home": best_lh,
        "lambda_away": best_la,
        "fitted_p_home": round(pw, 4),
        "fitted_p_draw": round(pd, 4),
        "fitted_p_away": round(pa, 4),
        "fitting_error": round(best_err, 6),
        "n_matches_with_goals_ge": {
            str(g): round(sum(margin_probs.get(d, 0) for d in margin_probs if abs(d) >= g), 4)
            for g in [1, 2, 3]
        },
    }


def classify_signal(
    discrepancy_pp: float,
    direction: str,
    public_bias_direction: str | None,
) -> dict[str, Any]:
    """
    Classify the signal type and strength.
    
    direction: "over_cheap" (AH+1X2 implies more goals than tot market → Over is value)
               "under_cheap" (reverse → Under is value)
    public_bias_direction: "over_bias" (public loves Over) or None
    """
    abs_disc = abs(discrepancy_pp)

    if abs_disc < THRESHOLD_NOISE * 100:
        return {"type": None, "strength": "无", "action": "忽略"}

    strength = "弱" if abs_disc < THRESHOLD_WEAK * 100 else \
               "中" if abs_disc < THRESHOLD_MEDIUM * 100 else "强"

    # Signal type logic
    if direction == "over_cheap" and public_bias_direction == "over_bias":
        # Totals market prices Over lower than AH+1X2 implies
        # Public pushes Over → totals Over may be artificially cheap
        return {
            "type": "人性税（Over被压）",
            "strength": strength,
            "action": f"AH+1X2 反推 P(O) 比 Totals 市场高 {abs_disc:.0f}pp，方向与散户偏 Over 一致 → Over 可能是低估价值",
        }
    elif direction == "under_cheap" and public_bias_direction == "over_bias":
        # Totals prices Under lower than AH+1X2 implies
        # Public pushes Over → Under may be artificially inflated
        return {
            "type": "人性税（Under被撑）",
            "strength": strength,
            "action": f"散户偏 Over 把 Under 撑高，AH+1X2 反推显示 Totals 市场 Under 偏贵 → Under 可能是价值",
        }
    elif strength in ("中", "强"):
        return {
            "type": "庄家修盘/结构偏差",
            "strength": strength,
            "action": f"市场结构偏差 {abs_disc:.0f}pp，需跨书商验证方向",
        }
    else:
        return {
            "type": "弱信号",
            "strength": "弱",
            "action": f"偏差 {abs_disc:.0f}pp，在模型误差边缘，仅作参考",
        }


# ---------- main detection ----------

def extract_pinnacle_markets(
    snapshot_data: dict[str, Any], home_team: str, away_team: str
) -> dict[str, Any] | None:
    """Extract Pinnacle 1X2/spreads/totals from snapshot for given match."""
    events = snapshot_data if isinstance(snapshot_data, list) else snapshot_data.get("data", [])
    for ev in events:
        if ev.get("home_team") == home_team and ev.get("away_team") == away_team:
            for bm in ev.get("bookmakers", []):
                if "pinnacle" not in bm.get("key", "").lower():
                    continue
                markets = {m["key"]: m for m in bm.get("markets", [])}
                if "h2h" not in markets or "spreads" not in markets or "totals" not in markets:
                    return {"error": "Pinnacle missing one or more required markets"}
                return {
                    "match": f"{home_team} vs {away_team}",
                    "bookmaker": "pinnacle",
                    "h2h": {o["name"]: o["price"] for o in markets["h2h"]["outcomes"]},
                    "spreads": {
                        o["name"]: {"price": o["price"], "point": o.get("point", 0)}
                        for o in markets["spreads"]["outcomes"]
                    },
                    "totals": {
                        o["name"]: {"price": o["price"], "point": o.get("point", 0)}
                        for o in markets["totals"]["outcomes"]
                    },
                    "raw_markets": list(markets.keys()),
                }
    return {"error": f"Match {home_team} vs {away_team} not found"}


def analyze_consistency(pinnacle_data: dict[str, Any]) -> dict[str, Any]:
    """Main consistency triangle analysis."""
    if "error" in pinnacle_data:
        return pinnacle_data

    # ---- 1X2 devig ----
    h2h = pinnacle_data["h2h"]
    # Map teams - the-odds-api names vary. Try common patterns.
    home_name = pinnacle_data["match"].split(" vs ")[0]
    away_name = pinnacle_data["match"].split(" vs ")[1]
    
    odds_1x2 = []
    for candidate in [away_name, "Draw", home_name]:
        found = False
        for name, price in h2h.items():
            if name.lower() == candidate.lower() or \
               (candidate.lower() in name.lower()) or \
               (name.lower() in candidate.lower()):
                odds_1x2.append(price)
                found = True
                break
        if not found:
            odds_1x2.append(h2h.get(candidate, list(h2h.values())[0]))
    
    if len(odds_1x2) < 3:
        return {"error": "Cannot parse 1X2 outcomes"}
    
    probs_1x2, ov = no_vig(odds_1x2)

    # ---- Spreads ----
    spreads = pinnacle_data["spreads"]
    # Identify favorite team (the one with negative point)
    fav_team = None
    dog_team = None
    fav_line = 0
    dog_line = 0
    fav_price = 0
    dog_price = 0
    for team_name, sp in spreads.items():
        pt = sp["point"]
        pr = sp["price"]
        if pt < 0:
            fav_team = team_name
            fav_line = pt
            fav_price = pr
        elif pt > 0:
            dog_team = team_name
            dog_line = pt
            dog_price = pr
        else:
            # point = 0.0 or -0.0 → pick'em
            if fav_team is None:
                fav_team = team_name
                fav_line = -abs(pt)
                fav_price = pr
            else:
                dog_team = team_name
                dog_line = abs(pt)
                dog_price = pr

    if fav_team is None or dog_team is None:
        return {"error": f"Cannot parse spreads: {spreads}"}

    sp_odds = [fav_price, dog_price]
    sp_probs, sp_ov = no_vig(sp_odds)
    fav_cover_prob = sp_probs[0] if fav_line < 0 else sp_probs[1]

    # ---- Fit λ ----
    # Map: 
    # home_team in 1x2 = home_name
    # We need P(home_win), P(draw), P(away_win)
    # Probs mapping: odds_1x2 order = [away, draw, home]
    p_home = probs_1x2[2]
    p_draw = probs_1x2[1]
    p_away = probs_1x2[0]
    
    fit = fit_lambda(p_home, p_draw, p_away, fav_cover_prob, fav_line)

    # ---- Totals ----
    totals = pinnacle_data["totals"]
    over_name = [t for t in totals if t.lower() in ("over", "o")][0]
    under_name = [t for t in totals if t.lower() in ("under", "u")][0]
    tot_line = totals[over_name]["point"]
    tot_odds = [totals[over_name]["price"], totals[under_name]["price"]]
    tot_probs, tot_ov = no_vig(tot_odds)
    market_profile_fit = fit_market_profile(p_home, p_draw, p_away, float(tot_line), tot_probs[0])
    market_profile = build_market_profile(market_profile_fit, home_name, away_name, float(tot_line))

    # Compute implied totals from λ
    implied = total_goals_metrics(fit["lambda_home"], fit["lambda_away"], tot_line)
    implied_p_over = implied[f"p_over_{tot_line}"]
    
    # Discrepancy
    disc_pp = round((implied_p_over - tot_probs[0]) * 100, 1)

    # ---- Classify ----
    if disc_pp > 0:
        # AH+1X2 implies MORE goals than Totals market → Over is cheap relative to structure
        direction = "over_cheap"
        public_bias = "over_bias"  # public loves Over, pushing it down
    else:
        direction = "under_cheap"
        public_bias = "over_bias"

    signal = classify_signal(abs(disc_pp), direction, public_bias)

    # ---- Build result ----
    abs_line = abs(float(fav_line))
    spread_warning = None
    if abs_line >= 1.0:
        spread_warning = (
            f"让球线 {fav_line} 较宽（≥1.0），Poisson 模型在此区间对总进球的预测"
            f"会系统性偏高（需高 λ 才能产生匹配让球线的比分差）。"
            f"此偏差部分来自模型假设误差，不一定代表市场定价不一致。"
        )

    result = {
        "match": pinnacle_data["match"],
        "bookmaker": "pinnacle",
        "source": "the-odds-api",
        "analysis": {
            "lambda_home": fit["lambda_home"],
            "lambda_away": fit["lambda_away"],
            "expected_total_goals": implied["expected_total_goals"],
            "fitted_1x2": {
                "home": fit["fitted_p_home"],
                "draw": fit["fitted_p_draw"],
                "away": fit["fitted_p_away"],
            },
            "actual_1x2_no_vig": {
                "home": round(p_home, 4),
                "draw": round(p_draw, 4),
                "away": round(p_away, 4),
            },
            "spread_line": float(fav_line),
            "spread_cover_prob": round(fav_cover_prob, 4),
            "spread_warning": spread_warning,
        },
        "totals_market": {
            "line": tot_line,
            "price": {"over": totals[over_name]["price"], "under": totals[under_name]["price"]},
            "no_vig_over": round(tot_probs[0], 4),
            "no_vig_under": round(tot_probs[1], 4),
            "overround": round(tot_ov, 4),
        },
        "lambda_implied": {
            f"p_over_{tot_line}": round(implied_p_over, 4),
            f"p_under_{tot_line}": round(1 - implied_p_over, 4),
        },
        "discrepancy": {
            "pp": disc_pp,
            "direction": direction,
            "interpretation": (
                f"AH+1X2 反推 P(O{float(tot_line):g})={implied_p_over:.0%}, "
                f"Totals 市场 P(O{float(tot_line):g})={tot_probs[0]:.0%}, "
                f"差 {abs(disc_pp):.0f}pp"
            ),
        },
        "signal": signal,
        "market_profile": market_profile,
        "caveat": "Poisson 模型是结构近似，不是事实。<5pp 偏差在方法误差范围内。需跨书商验证才能 actionable。",
    }
    return result


def analyze_snapshot(
    snapshot_path: str, match_filter: str | None = None
) -> list[dict[str, Any]]:
    """Run consistency triangle on all matches in a snapshot."""
    raw = json.loads(Path(snapshot_path).read_text())
    events = raw if isinstance(raw, list) else raw.get("data", [])
    
    results = []
    for ev in events:
        home = ev.get("home_team", "")
        away = ev.get("away_team", "")
        if match_filter and match_filter.lower() not in f"{home} {away}".lower():
            continue
        
        pinnacle_data = extract_pinnacle_markets(raw, home, away)
        if not pinnacle_data or "error" in pinnacle_data:
            continue
        
        result = analyze_consistency(pinnacle_data)
        results.append(result)
    
    return results


# ---------- CLI ----------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="一致性三角探测器 — Pinnacle AH+1X2 vs Totals"
    )
    parser.add_argument("--snapshot", required=True, help="the-odds-api 快照路径")
    parser.add_argument("--match", default=None, help="筛选某场比赛（可选）")
    parser.add_argument("--full", action="store_true", help="输出所有比赛（默认只输出有信号的）")
    
    args = parser.parse_args()
    results = analyze_snapshot(args.snapshot, args.match)
    
    if args.match:
        # Single match — output all detail
        for r in results:
            print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.full:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        # Only show matches with signal
        with_signal = [r for r in results if r.get("signal", {}).get("type")]
        for r in with_signal:
            sig = r["signal"]
            disc = r.get("discrepancy", {})
            print(
                f"[{sig['strength']}] {r['match']:40s} | "
                f"{disc.get('interpretation','')[:50]} | "
                f"{sig.get('type','')}"
            )
        if not with_signal:
            print("No matches with detectable signal (all within noise threshold).")
        print(f"\nScanned {len(results)} matches with complete Pinnacle data.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
