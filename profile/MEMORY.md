# MEMORY.md - WC26 Handicap Analyst Long-Term Notes

本文件只存长期校准结论和策略纪律。不要把原始赔率、API 响应、私聊、cookie、`.env`、Sannai/main Hermes 记忆、临时日志写入这里。

## Durable Policy

- Official fixture/team/venue facts come from FIFA first.
- Deep Research is a post-report finalizer only. It reads the guarded
  report/manifest/artifacts, calls Exa and Jina for outside context, and
  produces a readable betting-direction addendum. It must never alter
  `p_market`, `p_adj`, EV, Kelly, `relay_actionable`, `qualified_play_count`,
  or the adjustment ledger.
- football-data.org and OpenFootball are mirrors/context, not override sources.
- the-odds-api is broad scan; oddspapi is low-frequency tournament snapshot plus candidate deep dive.
- Free odds source coverage is never assumed. Use exact verified keys first and refresh current sport/tournament/market keys before live analysis.
- No auto-bet. Human review is required for any `qualified play`.
- CLV and calibration are primary feedback. Hit rate is secondary and often misleading.
- Automatic CLV/model calibration belongs in `grading/model_calibration.duckdb`; MEMORY.md only stores manually curated durable lessons.
- Never tune from one match result or hit rate. Calibration proposals require CLV/calibration/Brier evidence, adequate sample size, bounded delta, rollback rule, `calibration_gate.py` PASS, and human approval.
- The feedback loop can adjust only execution-zone parameters. It must never alter the three-probability rule, `p_adj = p_market` default, no-auto-bet boundary, five-dimension framework, source-quality gate, `qualified_play` review gate, or Hermes memory boundary.

## Source Roles

- `FIFA`: official schedule, groups, teams, venues, results, tiebreakers.
- `football-data.org`: structured match/status/standings API, WC competition code.
- `OpenFootball`: public fixture mirror, useful for parsing and backup.
- `StatsBomb Open Data`: historical World Cup/event-level calibration.
- `soccerdata`: ClubElo/FBref/Sofascore-style context when available.
- `ScraperFC`: optional Transfermarkt/team depth context; validate API surface before relying on it.
- `Open-Meteo`: weather forecast by venue coordinates.
- `the-odds-api`: quota-aware broad scan, `soccer_fifa_world_cup`, `h2h/spreads/totals`, one region/bookmaker first.
- `oddspapi`: `sportId=10`, `tournamentIds=16`, Pinnacle tournament snapshot, then Asian handicap / bookmaker deep dive for selected candidate fixtures.

## Quota Lessons

- the-odds-api quota cost = markets x regions. The verified Pinnacle `h2h,spreads,totals` snapshot cost 3 credits.
- oddspapi free quota is tight. `/account` is non-billable; treat `/tournaments`, `/markets`, `/odds`, and `/odds-by-tournaments` as billable.
- Current verified oddspapi markets: `101` Full Time Result, `1010` Over Under Full Time 2.5, `1068` Asian Handicap -0.5.
- Never sweep all 104 matches through fixture-level `/odds`.
- Asian handicap/totals with integer or quarter lines must use settlement-leg EV/Kelly. The scalar `p_adj * odds - 1` formula is only for no-push markets.
- Odds math uses normalized decimal odds only. Chinese water/HK/Malay units must be converted before no-vig, EV, Kelly, or Asian settlement.
- Every actionable numeric claim must cite a snapshot id and a devig artifact id. Missing provenance caps source quality at C and final status at watch/pass.
- Exa/Jina research claims require source ids and limitations. They can support
  "watch this side", "wait for this trigger", or "research confirms no-play",
  but they cannot create deterministic actionability without the main report
  already allowing it.
- Deep Research validation is finding-level. Invalid or time-uncertain news
  findings are filtered; they do not block the guarded main report. A final
  Telegram addendum must include a non-empty deep-research artifact path or a
  short failed status.
- Market profile is descriptive only and must come from the Path C
  `consistency_triangle.py` artifact field `market_profile`. LLM research may
  explain it, but must not recalculate score probabilities or turn the most
  likely score/result into value.
- Simulation reports use `mode=simulation` and `final_status=simulation_only`; they are never actionable and never enter CLV/calibration.
- Uncertainty gate uses root-sum-square `sigma_total`; qualified plays must survive robust EV after the adverse uncertainty stress.
- Timing is an edge hypothesis. Early structural reports use `T-72h_early`; `T-24h_confirm` and `T-6h_preflight` are monitoring by default; the old `T-1h` check is replaced by `T-60m_lineup_final` and `T-45m_price_guard`.
- Track CLV by timing class. Use CLV/calibration, not hit rate, to decide whether early or lineup-final windows deserve more weight.
- Cache every response with `captured_at_utc`, source, URL, params hash, and freshness.
- If source freshness fails threshold, output `watch` or block.

## Calibration Notes

开赛前先验:

- 国家队样本小, model output 必须向 market prior 收缩。
- 强队 vs 摆大巴弱队时, goal model 容易高估大胜和 over。
- 小组末轮、最佳第三规则、已出线轮换会制造非典型动机。
- 高温、海拔、旅行疲劳对大小球和下半场强度尤其重要。

赛后 append:

```text
YYYY-MM-DD | match_id | market | p_adj | close_no_vig | result | CLV | lesson
```
