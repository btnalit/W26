#!/usr/bin/env python3
"""
snapshot_validator.py — the-odds-api 快照数据质量验证器

定位: 采集层与扫描层之间的强制关卡。
      collector 写完快照 → 本脚本生成 snapshot_health JSON →
      cross_book_scan / pipeline 读 health, 对带 ERROR 的腿 fail-closed。

覆盖的已知事故:
  M004: Pinnacle 1X2 负水位 (腿级陈旧/坏数据, 隐含概率和 < 1)
  M003: Pinnacle totals 2.25@50/50 与全市场矛盾 (腿级陈旧 + 主流线漂移, 无 last_update 校验)

检查项 (code / severity):
  anchor_missing      ERROR  该市场 Pinnacle 腿缺失
  stale_leg           ERROR  腿的 last_update 距快照抓取时间超过阈值
  negative_overround  ERROR  水位 < 0 (数学上不可能的 sharp 价 → 必是坏数据)
  high_overround      WARN   水位 > 上限 (默认 12%)
  missing_side        ERROR  两向市场缺一边 (同线 over 无 under 等)
  odds_out_of_range   ERROR  赔率 <= 1.01 或 > 100
  line_divergence     WARN   Pinnacle 主线 ≠ 全市场众数线 (totals/spreads)
  anchor_outlier      WARN   同线同市场, Pinnacle 隐含概率偏离全书商中位数 > 阈值
  no_last_update      WARN   腿缺 last_update 字段 (无法做时效判断)

退出码: 0 = 全干净; 1 = 有 WARN; 2 = 有 ERROR (cron 友好, 可 fail-closed)

用法:
  python3 snapshot_validator.py SNAPSHOT.json \
      --fetched-at 2026-06-10T02:00:24Z \
      [--max-leg-age-min 90] [--max-overround 0.12] \
      [--outlier-pp 0.04] [--output health.json]

  --fetched-at 缺省时从文件名提取 (…-YYYYMMDDTHHMMSSZ.json), 再缺省用文件 mtime。
零外部依赖。
"""

from __future__ import annotations
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ANCHOR_BOOK = "pinnacle"
TWO_WAY_MARKETS = ("spreads", "totals")
ALL_MARKETS = ("h2h", "spreads", "totals")


# ── 工具 ──────────────────────────────────────────────────────────

def parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def fetched_at_from_filename(path: Path) -> datetime | None:
    m = re.search(r"(\d{8}T\d{6})Z", path.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def implied(odds: float) -> float:
    return 1.0 / odds


def flag(flags: list, severity: str, code: str, **ctx) -> None:
    flags.append({"severity": severity, "code": code, **ctx})


# ── 单场校验 ───────────────────────────────────────────────────────

def validate_match(match: dict, fetched_at: datetime | None,
                   max_age_min: float, max_overround: float,
                   outlier_pp: float) -> dict:
    home = match.get("home_team", "?")
    away = match.get("away_team", "?")
    flags: list[dict] = []

    # 收集: per (book, market) → {line(or None): {outcome: odds}}, 以及 last_update
    boards: dict[str, dict[str, dict]] = defaultdict(dict)   # market → book → {line: {name: odds}}
    leg_updates: dict[tuple, str] = {}

    for bm in match.get("bookmakers", []):
        book = (bm.get("key") or bm.get("title") or "?").lower()
        for mkt in bm.get("markets", []):
            mkey = mkt.get("key")
            if mkey not in ALL_MARKETS:
                continue
            lu = mkt.get("last_update") or bm.get("last_update")
            leg_updates[(book, mkey)] = lu
            lines: dict = defaultdict(dict)
            for oc in mkt.get("outcomes", []):
                name = str(oc.get("name", "")).lower()
                price = oc.get("price")
                point = oc.get("point")  # h2h 无 point → None
                if not isinstance(price, (int, float)) or price <= 1.01 or price > 100:
                    flag(flags, "ERROR", "odds_out_of_range",
                         book=book, market=mkey, outcome=name, price=price)
                    continue
                lines[point][name] = float(price)
            boards[mkey][book] = dict(lines)

    # ── 腿级检查 ──
    for (book, mkey), lu in leg_updates.items():
        lu_dt = parse_iso(lu) if lu else None
        if lu_dt is None:
            flag(flags, "WARN", "no_last_update", book=book, market=mkey)
        elif fetched_at is not None:
            age_min = (fetched_at - lu_dt).total_seconds() / 60.0
            if age_min > max_age_min:
                sev = "ERROR" if book == ANCHOR_BOOK else "WARN"
                flag(flags, sev, "stale_leg", book=book, market=mkey,
                     last_update=lu, age_minutes=round(age_min, 1))

    for mkey in ALL_MARKETS:
        book_lines = boards.get(mkey, {})

        # 锚缺失
        if mkey in book_lines and ANCHOR_BOOK not in book_lines and book_lines:
            pass  # boards 用 defaultdict, 统一在下面判
        if book_lines and ANCHOR_BOOK not in book_lines:
            flag(flags, "ERROR", "anchor_missing", market=mkey,
                 books_present=sorted(book_lines.keys()))

        # 水位 / 缺边: 按 (book, line) 分组
        for book, lines in book_lines.items():
            for line, oc_map in lines.items():
                n = len(oc_map)
                if mkey == "h2h":
                    expect = 3
                    if n < expect:
                        flag(flags, "ERROR", "missing_side", book=book, market=mkey,
                             line=line, outcomes_present=sorted(oc_map.keys()))
                        continue
                    ovr = sum(implied(o) for o in oc_map.values()) - 1.0
                    if ovr < 0:
                        flag(flags, "ERROR", "negative_overround", book=book, market=mkey,
                             line=line, overround=round(ovr, 4))
                    elif ovr > max_overround:
                        flag(flags, "WARN", "high_overround", book=book, market=mkey,
                             line=line, overround=round(ovr, 4))
                elif mkey == "totals":
                    expect = 2
                    if n < expect:
                        flag(flags, "ERROR", "missing_side", book=book, market=mkey,
                             line=line, outcomes_present=sorted(oc_map.keys()))
                        continue
                    ovr = sum(implied(o) for o in oc_map.values()) - 1.0
                    if ovr < 0:
                        flag(flags, "ERROR", "negative_overround", book=book, market=mkey,
                             line=line, overround=round(ovr, 4))
                    elif ovr > max_overround:
                        flag(flags, "WARN", "high_overround", book=book, market=mkey,
                             line=line, overround=round(ovr, 4))
                # spreads: 用正反 point 标示两边, 不按同 point 分组求 2 个 outcome
                # 跳过 per-point missing_side 和 overround 检查

        # 主流线漂移 (totals/spreads): Pinnacle 主线 vs 全书商众数线 (M003)
        if mkey in TWO_WAY_MARKETS and ANCHOR_BOOK in book_lines:
            anchor_lines = set(book_lines[ANCHOR_BOOK].keys()) - {None}
            all_lines = [ln for bk, lines in book_lines.items()
                         if bk != ANCHOR_BOOK
                         for ln in lines if ln is not None]
            if anchor_lines and all_lines:
                modal_line, _ = Counter(all_lines).most_common(1)[0]
                if modal_line not in anchor_lines:
                    flag(flags, "WARN", "line_divergence", market=mkey,
                         anchor_lines=sorted(anchor_lines),
                         market_modal_line=modal_line,
                         note="同线过滤将丢弃多数书商, 该市场将变 anchor-only")

        # 锚离群 (同线同 outcome, Pinnacle 隐含概率 vs 其他书商中位数)
        if ANCHOR_BOOK in book_lines:
            for line, oc_map in book_lines[ANCHOR_BOOK].items():
                for name, odds in oc_map.items():
                    others = [implied(lines[line][name])
                              for bk, lines in book_lines.items()
                              if bk != ANCHOR_BOOK and line in lines and name in lines[line]]
                    if len(others) >= 3:
                        others.sort()
                        med = others[len(others) // 2]
                        dev = implied(odds) - med
                        if abs(dev) > outlier_pp:
                            flag(flags, "WARN", "anchor_outlier", market=mkey,
                                 line=line, outcome=name,
                                 anchor_implied=round(implied(odds), 4),
                                 market_median_implied=round(med, 4),
                                 deviation_pp=round(dev * 100, 1))

    errors = sum(1 for f in flags if f["severity"] == "ERROR")
    warns = sum(1 for f in flags if f["severity"] == "WARN")
    return {
        "match": f"{home} vs {away}",
        "status": "ERROR" if errors else ("WARN" if warns else "OK"),
        "error_count": errors,
        "warn_count": warns,
        "flags": flags,
    }


# ── 主入口 ─────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot")
    ap.add_argument("--fetched-at", default=None)
    ap.add_argument("--max-leg-age-min", type=float, default=90.0)
    ap.add_argument("--max-overround", type=float, default=0.12)
    ap.add_argument("--outlier-pp", type=float, default=0.04)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    path = Path(args.snapshot)
    data = json.loads(path.read_text())
    matches = data.get("data", data) if isinstance(data, dict) else data

    fetched_at = (parse_iso(args.fetched_at) if args.fetched_at
                  else fetched_at_from_filename(path))
    if fetched_at is None:
        fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

    results = [validate_match(m, fetched_at, args.max_leg_age_min,
                              args.max_overround, args.outlier_pp)
               for m in matches]

    worst = max((r["status"] for r in results),
                key=lambda s: {"OK": 0, "WARN": 1, "ERROR": 2}[s], default="OK")
    health = {
        "artifact_type": "snapshot_health",
        "snapshot": path.name,
        "fetched_at_utc": fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "validated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "thresholds": {"max_leg_age_min": args.max_leg_age_min,
                       "max_overround": args.max_overround,
                       "anchor_outlier_pp": args.outlier_pp},
        "overall_status": worst,
        "matches": results,
    }
    out = json.dumps(health, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out)
    print(out)
    return {"OK": 0, "WARN": 1, "ERROR": 2}[worst]


if __name__ == "__main__":
    sys.exit(main())
