--- 
name: odds-analysis
description: WC26 football handicap and market analysis workflow
---

# odds-analysis SKILL.md

Use this skill for World Cup 2026 football handicap and market analysis.

## Contract

The worker produces advisory reports only. It must not place bets, log into bookmaker accounts, click bet buttons, publish guaranteed picks, read cookies, print env vars, read Sannai/main Hermes memory, or modify live Hermes services.

If a task asks for any forbidden action, return a concise `BLOCKED` direct
summary with the boundary reason.

Browser fallback is allowed only for low-volume verification. Use
`browser-harness` / CloakBrowser for dynamic official pages, team-news
cross-checks, public odds page sanity checks, and screenshot evidence when
web/API fetch is inadequate. Do not use browser automation for high-volume odds
scraping, bookmaker login, cookie/session extraction, paywall bypass, bet
buttons, or any funds-related workflow.

Browser fallback must use the WC26 empty profile directory
`/hermesdata/worldcup-2026-handicap/browser-profile-empty`. Do not reuse a
media-publish or bookmaker/browser profile that may carry login cookies.

Before any browser fallback, run the host preflight. It starts CloakBrowser on
CDP `http://127.0.0.1:9222` if needed and reloads browser-harness:

```bash
/hermesdata/media-publish/scripts/start_cloakbrowser_cdp.sh
/hermesdata/media-publish/scripts/browser_stack_status.sh
```

For a full fallback smoke, run:

```bash
/hermesdata/worldcup-2026-handicap/wc26_browser_fallback_check.sh
```

Do not treat `browser_navigate` failure as terminal until this preflight has
been attempted. If CDP remains unavailable after preflight, mark browser
fallback unavailable and continue only when web/API evidence is sufficient for
the requested source-quality grade.

## Data Source Order

1. FIFA official pages: schedule, groups, teams, venues, results, tiebreakers.
2. football-data.org / OpenFootball: structured match mirror and status.
3. StatsBomb Open Data / soccerdata / ScraperFC: historical and contextual football signals.
4. Open-Meteo: weather by venue.
5. the-odds-api: broad market scan.
6. oddspapi: low-frequency tournament-level Asian handicap snapshot and candidate-only sharp/soft deep dive.
7. browser-harness / CloakBrowser fallback: dynamic page verification only, not a primary data source.

Official facts override mirrors. If official facts conflict with a mirror, stop and report the conflict.

## Quota Discipline

- the-odds-api is broad scan. Use verified sport key `soccer_fifa_world_cup`, one region, selected bookmaker `pinnacle`, and `h2h,spreads,totals` when handicap context is needed. Read `x-requests-remaining`, `x-requests-used`, and `x-requests-last`; the 2026-06-04 remote smoke cost 3 credits for those three markets.
- the-odds-api winner key is `soccer_fifa_world_cup_winner`. Treat it as optional futures context; the remote smoke found the key active but Pinnacle outrights not populated.
- oddspapi uses `sportId=10` and World Cup `tournamentIds=16`. Use `/v4/odds-by-tournaments?tournamentIds=16&bookmaker=pinnacle&verbosity=3` for low-frequency cached Pinnacle snapshots. Do not call fixture-level `/odds` for every fixture.
- oddspapi `/account` is the only endpoint verified as non-billable in this run. Treat `/tournaments`, `/markets`, `/odds`, and `/odds-by-tournaments` as billable and cache aggressively.
- Current verified oddspapi soccer markets: `101` Full Time Result, `1010` Over Under Full Time 2.5, `1068` Asian Handicap -0.5. Refresh `/markets?sportId=10` before the tournament and before any live pilot.
- Cache every response under `snapshots/` or `cache/` with timestamp and params hash.
- Same reuse group should reuse cache if younger than TTL. This applies across
  windows, not only within the exact same window:
  - `T-72h_early` and `T-48h_early_update` share `early_structural`;
  - `T-90m_lineup_probe`, `T-75m_team_sheet_checkpoint`,
    `T-60m_lineup_final`, and `T-45m_price_guard` share
    `late_lineup_price`.
- Manual Telegram requests reuse the same scheduled snapshots. Do not refresh
  odds just because the user asks the same match in chat. Refresh only when the
  request explicitly says latest/refresh/realtime or the selected snapshot is
  stale/missing.
- If quota/freshness is inadequate, output `watch` or block.
- The analyst worker reads cached snapshots. Paid API keys are owned by
  deterministic no-agent collector scripts such as `scripts/wc26_cron_payload.py`.
  Do not run raw `curl`/`requests` calls to paid odds APIs from analyst prose.

Use `scripts/snapshot_resolver.py` before asking for fresh data:

```bash
python3 skills/odds-analysis/scripts/snapshot_resolver.py \
  --workspace /hermesdata/worldcup-2026-handicap \
  --window T-45m_price_guard \
  --source all
```

If it returns `cache_hit=true`, cite that snapshot. If it returns
`must_refresh=true`, only a deterministic collector/no-agent job may refresh
quota-bearing sources. If the user did not explicitly request latest data and a
valid snapshot exists inside the reuse group, refreshing is a policy violation.

## Numeric Unit And Provenance Contract

All market math uses normalized decimal odds greater than `1.0`.

- If a source gives Chinese water, Hong Kong, or Malay odds, convert with
  `scripts/devig.py` / `to_decimal()` before no-vig, EV, Kelly, or Asian
  settlement math.
- Record both the source unit and normalized decimal value.
- `devig.py` rejects decimal odds `<= 1.0`; do not silently treat water
  `0.95` as decimal `0.95`.

Every report number used for no-vig probability, edge, EV, robust EV, Kelly,
or Asian settlement must come from deterministic JSON, not freeform report
prose. Prefer the compiler boundary first:

```bash
python3 scripts/wc26-match-analyze.py \
  --snapshot snapshots/odds/the-odds-api-multibook-20260604T120000Z.json \
  --match-home "Mexico" --match-away "South Africa" \
  --match-id M001 --window T-72h_early \
  --output reports/artifacts \
  --mode full
```

**Every match analysis MUST start with the orchestrator.** `wc26-match-analyze.py`
is the only deterministic entry point for live direct reports. It runs the full
chain: devig + crossbook + consistency_triangle + mechanism_audit + manifest + report,
then writes 6+ artifacts and a guarded report. Do NOT skip it unless the match
has zero path_a edges (fast path below).

Do not replace orchestrator numbers with LLM-generated numbers. If a live task
needs richer data, feed fresh snapshot values into the orchestrator first.

**Fast path (0 edges, 0 actionable → NO PLAY):** Run only crossbook scan, skip
full orchestrator:

```bash
python3 skills/odds-analysis/scripts/cross_book_scan.py \
  --input-snapshot snapshots/odds/the-odds-api-multibook-*.json \
  --output reports/artifacts/crossbook-{match}-{date}.json \
  --match-home "Canada" \
  --match-away "Bosnia & Herzegovina"
```

For custom calculations, use `numeric_artifact.py` / `devig.py` first, then cite:

```yaml
snapshot_id:
devig_artifact_id:
artifact_type: devig
artifact_path:
```

The manifest must include an `artifacts` list whose `artifact_id` matches every
critical number and whose `path` points to an actual JSON file emitted by
`devig.py` or `numeric_artifact.py`.

Before writing final Markdown, build deterministic numeric artifact JSON.
Then write a JSON sidecar artifact manifest next to the report or under
`reports/artifacts/`, and run:

```bash
python skills/odds-analysis/scripts/report_contract.py reports/artifacts/<artifact>.json
```

After writing Markdown, run:

```bash
python skills/odds-analysis/scripts/report_guard.py reports/match/<report>.md
```

If either validation fails, do not complete with analytical metadata and do
not show unprovenanced numbers. Block the task with the validation error. Main
must not reconstruct from logs.

Simulation mode is allowed only for dry practice and must use:

```yaml
mode: simulation
final_status: simulation_only
source_quality: C
```

Synthetic numbers must be labeled `synthetic`; they must not enter actionable
direct request state, paid quota, CLV grading, or calibration proposals.

## Analysis Pipeline

### 1. Fact Lock

Required fields:

```yaml
match_id:
teams:
stage:
group:
kickoff_utc:
venue:
official_source_url:
fixture_status:
```

If official fixture facts are missing or conflicting, block.

### 2. Context Snapshot

Collect:

- rest days;
- travel and time zone;
- venue weather and altitude;
- FIFA rank/Elo-style prior;
- injury, suspension, roster, expected XI;
- tournament incentives and group math.

### 3. Model Baseline

Use a simple ensemble:

- rating/Elo prior;
- Poisson or Dixon-Coles where data supports it;
- roster and context adjustments;
- shrink toward market prior because national-team samples are small.

Record `p_model`, model version, and uncertainty.

**p_model 的 calibration gate**（Phase 1 DC Integration）：
p_model 直接从 `reports/artifacts/model-{match_id}-{window}.json` 读取，
由 `model_runner.py`（Dixon-Coles 拟合器）周期性产出。

Market Board 中 `Model Fair` 列的填充规则：

| calibration.status | 显示内容 | p_model 能否触发 adjustment |
|---|---|---|
| `insufficient_data` | `— (info)` | 否。模型不可用，不显示数值 |
| `holdout_pass` | `0.734 (info)` | 否。仅供参考，p_adj 仍锁 p_market |
| `pass` | `0.734` | 仅可作为**诊断输入**被引用进**具名的结构性 ledger 调整**中佐证。**永远不能单独把 p_adj 推离 p_market。模型 vs 市场分歧本身永远不是 edge。** |
| `fail` | `— (info)` | 否。模型不可用 |

**安全边界**：
- 校准通过只证明模型概率在总体上诚实，不证明它比 Pinnacle 收盘价准。
一个校准过的 DC 跟 Pinnacle 分歧，八成是模型缺乏 Pinnacle 已知的信息（首发/新闻），不是 edge。
- p_model 不包含结构因子（海拔/天气/球场/大赛动机）。这些因子全部留在 adjustment ledger。

For Asian handicap/totals, the model step must output a score matrix or a
selected-side margin/goal distribution. A 1X2 probability vector alone is not
enough. Use `scripts/model_margin.py` to convert a score matrix or Poisson
baseline into `margin_probabilities`, then feed that distribution into
`scripts/numeric_artifact.py ah ...` or `scripts/devig.py --ah-line ...
--ah-price ... --margin-probs-json ...`.

### 4. Market Snapshot

For every requested market:

```yaml
market:
bookmaker:
line:
source_odds_unit:
decimal_odds:
captured_at_utc:
source:
snapshot_id:
freshness_minutes:
overround:
no_vig_probability:
devig_artifact_id:
```

Use devig methods consistently. Compare method sensitivity when edge is small.

### 5. Bookmaker Intent Hypotheses

Write hypotheses, not mind-reading:

```yaml
hypothesis: protect_favorite | public_drift | sharp_alignment | injury_overadjustment | liquidity_noise | draw_suppression
evidence:
falsifier:
weight:
```

### 6. Anti-AI Red Team

Answer:

- What would a generic model/LLM say?
- Is the obvious side already priced?
- Is the narrative crowded?
- Which source could be stale?
- Which evidence would reverse the view?

### 7. Derive `p_adj`

`p_adj` is the only probability allowed to feed EV, edge, Kelly, and
`final_status`.

Default rule:

```text
p_adj := p_market
```

Move away from `p_market` only through an adjustment ledger. Each adjustment
must be small, named, sourced, and falsifiable:

```yaml
adjustment:
  factor: rotation | injury | weather | altitude | travel | tactical_matchup | public_bias | stale_line | group_math | liquidity_noise
  base_probability: p_market | p_model | previous_adjustment
  direction: plus | minus
  magnitude_pct: 0.00
  evidence:
  why_market_may_not_price_it:
  falsifier:
  uncertainty_pct:
```

Rules:

- If no adjustment explains why the market may not have priced it, keep
  `p_adj = p_market` and output `pass` or `watch`.
- If the only reason is "model says so", keep `p_adj = p_market`.
- If `p_model`, `p_market`, and the adjustment ledger converge, output `pass`.
- Raw `p_model - p_market` is never an edge. It can only enter the adjustment
  ledger as context, not as a decision metric.
- If a red-team falsifier is unresolved, cap final status at `watch` or `lean`.
- Sum of adjustments must still clear model and source uncertainty before
  `qualified_play`.

Uncertainty gate:

```text
sigma_total = sqrt(model_uncertainty_pct^2 + source_uncertainty_pct^2 + sum(adjustment_uncertainty_pct_i^2))
```

For scalar no-push markets:

```text
robust_p_adj = p_adj - sigma_total
robust_edge = robust_p_adj - p_market
robust_ev = robust_p_adj * decimal_odds - 1
```

`qualified_play` requires `robust_edge > 0` and
`robust_ev >= ev_threshold`. If this fails, cap final status at `lean`.

For Asian handicap and Asian totals, run the settlement EV on a stressed
margin/goal distribution after moving `sigma_total` probability mass from the
best settlement-return buckets to the worst settlement-return buckets.

For scalar no-push markets compute:

```text
edge = p_adj - p_market
expected_value = p_adj * decimal_odds - 1
```

For Asian handicap/totals, `p_adj` and `p_market` are diagnostic; the
decision metric is settlement EV and robust settlement EV from the
margin/goal distribution.

### 8. Decision

Allowed final statuses:

- `pass`
- `watch`
- `lean`
- `qualified_play`

`qualified_play` requires:

- source_quality `A`;
- fresh odds for the analysis window;
- `report_contract.py` PASS on the sidecar artifact manifest;
- `report_guard.py` PASS on the Markdown report;
- `p_adj` derived from the adjustment ledger, never raw model edge;
- edge clears uncertainty;
- explicit red-team pass;
- human review required.

Do not use `BET` as terminal status. Use `qualified_play` and block for human review.

## Formulas

Simple scalar formulas below apply only to no-push markets, such as 1X2
outcomes or half-goal totals/handicaps. Do not use them for integer or
quarter Asian handicap/totals.

```text
decimal_implied_probability = 1 / decimal_odds
no_vig_probability_i = implied_i / sum(implied_all_outcomes)
edge = p_adj - p_market
expected_value = p_adj * decimal_odds - 1
kelly_fraction_full = (decimal_odds * p_adj - 1) / (decimal_odds - 1)
stake_fraction = min(max_stake_pct, max(0, kelly_fraction_full * kelly_fraction))
```

Asian handicap settlement:

```text
margin = selected_team_goals - opponent_goals
adjusted_margin = margin + handicap_leg
leg_return =
  decimal_odds - 1  if adjusted_margin > 0
  0                 if adjusted_margin = 0
  -1                if adjusted_margin < 0

whole/half line EV = sum(P(margin) * leg_return)
quarter line EV = average(EV(adjacent lower half-line), EV(adjacent upper half-line))
```

Examples:

- `-0.25` splits into `0` and `-0.5`.
- `+0.25` splits into `0` and `+0.5`.
- `-0.75` splits into `-0.5` and `-1`.
- integer lines such as `0` or `-1` can push and must not use
  `p_adj * decimal_odds - 1`.

Kelly for Asian handicap/totals must be solved over the actual settlement
return distribution. Use `scripts/devig.py --ah-line ... --ah-price ...
--margin-probs-json ...` for local verification, or a smoke-tested modeling
library output that proves the same leg settlement.

## NO PLAY Triggers

- `|p_adj - p_market| < min_p_adj_market_delta` (default `0.02`).
- EV below threshold.
- uncertainty gate fails: scalar `robust_ev < ev_threshold` or Asian
  settlement `robust_ev < ev_threshold`.
- source_quality `C` or `D` for any actionable conclusion.
- missing `snapshot_id`, `devig_artifact_id`, or report artifact validation.
- mode is `simulation`.
- key lineup/news not available and material.
- odds stale for the window.
- market moved beyond acceptable price.
- red-team identifies unpriced uncertainty.

## Timing Window Contract

Timing is part of the edge hypothesis. Do not treat every window as a fresh
pick opportunity.

| Window | Timing Class | Actionable | Purpose |
| --- | --- | --- | --- |
| `T-{N}d_early_structural` | early_structural | yes, review-gated (data gaps likely) | pre-T-72h structural scan; N=ceil(hours_to_kickoff/24) |
| `T-72h_early` | early_structural | yes, review-gated | structural/tournament/venue/public-bias edge |
| `T-48h_early_update` | early_structural | only if price improves or new evidence appears | early CLV update |
| `T-24h_confirm` | confirmation | normally no | injury/news/press and market-move confirmation |
| `T-6h_preflight` | preflight | normally no | freshness, quota, weather, stale-source guard |
| `T-90m_lineup_probe` | lineup_probe | no | begin lineup/team-sheet polling and final odds cache |
| `T-75m_team_sheet_checkpoint` | lineup_probe | no | check team-sheet/official source availability |
| `T-60m_lineup_final` | lineup_final | yes, review-gated | confirmed lineup edge if market lags |
| `T-45m_price_guard` | price_guard | yes only if T-60 view still valid | price threshold and freshness guard |
| `postmatch` | postmatch | no | CLV/calibration grading |

Rules:

- `T-72h_early` is the main structural edge window.
- When hours-to-kickoff exceeds 84, use `T-{N}d_early_structural` with
  `N = ceil(hours_to_kickoff / 24)`. `report_contract.py` enforces hour
  ranges per window name; a mismatch blocks the manifest.
- `T-24h_confirm` and `T-6h_preflight` are update/monitor windows unless a
  material new information event appears.
- `T-90m_lineup_probe` and `T-75m_team_sheet_checkpoint` should not output
  `qualified_play`.
- `T-60m_lineup_final` and `T-45m_price_guard` replace the old `T-1h` terminal
  check.
- If lineup is required and still missing at `T-45m`, cap at `watch` or block.
- Every report must record `timing_class`, `information_event`,
  `entry_time_utc`, and `entry_price` so CLV can be graded by timing class.

## Feedback Loop Contract

The feedback loop evaluates process quality. Do not tune from one match result.

Primary feedback metrics:

- CLV: whether the report price beat the closing market.
- calibration: whether buckets of similar `p_adj` resolve at the stated rate.
- Brier/log-loss: probability quality over groups.

Hit rate is secondary and must never be the primary trigger for an adjustment
proposal.

Daily reflect may summarize:

- settled cards: `p_adj/status/market/entry` -> `close/result` -> CLV and
  Brier/log-loss contribution;
- aggregate CLV by timing class, window, market, source quality, adjustment tag, and status;
- calibration buckets;
- quota consumption and stale-source counts;
- candidate bias flags.

Most daily reflects should produce no proposal.

Calibration proposals:

- require at least 25 graded cards in the affected slice, or the stricter
  parameter-specific threshold;
- must use CLV, calibration, or Brier/log-loss as primary evidence;
- must declare bounded delta and rollback rule;
- must not touch the locked anchor zone: three-probability separation,
  `p_adj = p_market` default, no-auto-bet, five-dimension framework, source
  quality rules, human review, or Hermes memory boundaries;
- must be created as blocked `calibration-proposal` records with
  `review_required: true`.

Before any approved apply job, run:

```bash
python skills/odds-analysis/scripts/calibration_gate.py proposal.yaml --mode apply
```

`calibration_gate.py` PASS plus human approval is required. A proposal in
`PENDING` state is reviewable but not applicable.

## Report Template

For the direct Telegram gateway, a live report is relay-safe only when its
manifest declares `workflow_contract: wc26.direct_report.v1` and includes:

- `direct_request_id` plus `direct_request_path` from `direct_request_record.py`;
- canonical fixture identity: `canonical_id`, `football_data_id`, `match_id`,
  home, away, and kickoff validated by `fixture_registry.py`. Treat `M001`
  style IDs as display aliases only;
- `analysis_gates` for `devig_three_method`, `path_a_crossbook`,
  `asian_handicap`, `totals`, `path_b_model_diagnostic`,
  `path_c_consistency`, `role_engine`, `mechanism_audit`, and
  `source_freshness`;
- artifact capabilities for 1X2 three-method devig, Path A cross-book scan,
  Asian handicap settlement, totals, Path C consistency triangle, and
  mechanism audit. When a role-engine artifact is available, include it with
  `provides: ["role_engine"]`;
- a non-empty `source_freshness.sources` or `source_freshness.snapshots` list.

Path A has a stricter shape than a prose market read. The `path_a_crossbook`
gate can pass only with a real `cross_book_scan.py` artifact generated from one
multibook snapshot. The artifact must include:

- `artifact_type: crossbook_scan` or `artifact_kind: cross_book_scan`;
- `input_snapshot` / `source_snapshot_id`;
- `markets.h2h`, `markets.spreads`, and `markets.totals` status records;
- for each `ok` market: `sharp_anchor`, `devig_primary`,
  `fair_probs.shin/power/multiplicative`, `outcomes_scanned`,
  `quotes_scanned`, and a `quotes` list;
- each quote's offered odds, sharp fair probability, fair odds, EV under
  shin/power/multiplicative, `survives_all_methods`, `suspect`, `edge_candidate`,
  `actionable`, and `qualifies`. `actionable` is the raw arithmetic scan
  candidate; `qualifies` is relay-level only and remains false until the
  report contract allows relay actionability.
- `edge_count`, `noise_edge_count`, `actionable_count`,
  `raw_actionable_count`, `relay_actionable_count`, and
  `qualified_play_count`; `raw_actionable_count` must equal the scan's raw
  actionable rows, while `qualified_play_count` is relay-level only and must
  equal `relay_actionable_count`. A `noise_lt_5pp` row is never a qualified
  play even if it survives all three devig methods.

Do not use a separate later Pinnacle snapshot as the Path A anchor. Cross-book
comparison must use the Pinnacle price inside the same multibook snapshot.
Path A is pure arithmetic: scan every 1X2 outcome, including draw and underdog,
plus same-line AH/totals. Keep it separate from Market Psychology / ledger
prose. Do not write public-bias, "Pinnacle absorbing money", or bookmaker
intent narrative inside the Path A section; put that under Market Psychology as
diagnostic prose only.

Role engine is the deterministic game-theory reading layer. It is a report
content generator, not a betting trigger and not an LLM prose generator. After
the raw numeric/path artifacts exist and before `mechanism_audit.py`, run:

```bash
python skills/odds-analysis/scripts/role_engine.py \
  --manifest reports/artifacts/<manifest>.json \
  --output reports/artifacts/<role-engine>.json \
  --patch-manifest
```

The role-engine artifact must declare `engine_contract:
wc26.role_engine.v1`, `engine_version: deterministic_v1`, and
`role_conclusions` for bookmaker intent, public bias, AI lag, trap risk, and
market efficiency. Each conclusion must carry:

- `evidence_id`, `role`, `decision`, and `actionability`;
- `hypothesis_zh` and `interpretation_zh`;
- `trigger_artifacts`, `artifact_sources`, and `evidence_numbers`.

Role conclusions may enrich the report and may conservatively contradict or
downgrade a Path A candidate, but they cannot create an actionable play by
themselves. If a role lacks required artifacts, mark that role `BLOCKED`
instead of writing narrative.

Mechanism audit is the relay-time self-audit. The `mechanism_audit` gate can
pass only with a real `mechanism_audit.py` artifact. The artifact must:

- declare `audit_contract: wc26.mechanism_audit.v1`;
- list mechanism status for Path A, Path B, Path C, role engine, and
  artifact-generated hypothesis engine;
- emit only fixed decision enums: `CONFIRMED_ACTIONABLE`,
  `CONFIRMED_NOISE`, `REFUTED`, `DIAGNOSTIC_ONLY`, `SUSPECT`, `BLOCKED`;
- cross-check Path A counts against the cross-book artifact;
- set `required_final_status` to `pass_incomplete` or `watch` when a required
  mechanism is blocked. A manifest cannot claim `final_status: pass` when the
  audit requires `pass_incomplete`.

If a required source is absent but the report can still safely identify the
match, use a partial direct report instead of inventing data:

- set `report_completeness: partial`;
- set `final_status: watch`;
- set `source_quality_cap: C` or lower;
- include `skipped_sections` entries with `gate`, `reason`, and `impact`;
- do not emit actionable, `qualified_play`, or `PASS / NO PLAY` language.

If the report is not explicitly partial and any required item is missing, do
not mark `artifact_contract_status` or `report_guard_status` as `pass`; return
a blocked/needs-data explanation instead of a completed betting report.

Direct Telegram replies must use the artifact-backed rich projection:

```bash
python skills/odds-analysis/scripts/rich_summary.py \
  --manifest <manifest.json> \
  --report <report.md>
```

Do not replace this with an unchecked freeform conclusion. `rich_summary.py`
may use more natural Chinese and game-theory language, but all numbers and
facts must come from manifest/artifacts/report_contract. `direct_summary.py`
remains the deterministic audit projection/fallback. The user-visible reply
must include contract/source status, Path A, AH/Totals, Path B, Path C status,
game-theory/read-the-market conclusions, final decision, and post-match
traceability. If the script prints `BLOCKED`, relay that blocked summary and
the missing items.

After a guarded report and rich summary are available, the final user-facing
Telegram analysis should run `skills/odds-analysis/deep-research` as a
post-report finalizer. This finalizer uses Exa + Jina + LLM synthesis to add
readable betting-direction and game-theory interpretation. It may say which
side is worth watching and which triggers would upgrade or invalidate that
direction, but it must not rewrite the manifest/report numbers, enter the
adjustment ledger, change `p_adj`, EV, Kelly, `relay_actionable`, or
`qualified_play_count`, or convert a `WATCH / NO PLAY` baseline into a bet.
If some Deep Research findings fail the freshness/source contract, ignore those
findings and keep the guarded main report. If the Deep Research layer as a
whole fails, send the artifact-backed `rich_summary.py` output and state that
the post-report research layer did not complete.

The Deep Research addendum must include a non-empty artifact path:

```text
WC26_DEEP_RESEARCH_FINALIZER: completed
📁 Deep Research: /hermesdata/worldcup-2026-handicap/reports/artifacts/deep-research-...json
```

Do not emit `📁 Deep Research:` with a blank value. If no artifact exists, use
`WC26_DEEP_RESEARCH_FINALIZER: failed` and a short reason after the main
summary.

Market profile / most-likely-score commentary is allowed only when the guarded
manifest includes a `path_c_consistency` artifact with `market_profile`. The
finalizer may explain those artifact numbers, but must not recalculate them.
If no such artifact exists, write `市场画像未生成: 缺 Path C artifact`.

Direct Telegram request persistence is a separate required lifecycle. Every
new Telegram analysis message must get a new `direct_request_id`, even when the
report/manifest/artifacts are reused from cache. This is local JSON only and
must not refresh paid APIs.

At the start of a Telegram request, create the request record from the current
Hermes session metadata:

```bash
python skills/odds-analysis/scripts/direct_request_record.py \
  --from-latest-session \
  --request-text "<exact user request text>" \
  --match-id "<fixture alias or canonical id>" \
  --match-label "<home vs away>" \
  --status received \
  --header-lines
```

Use the printed `direct_request_id` and `direct_request_path` in the manifest
and report header. If an existing guarded report is reused, do not create a new
analysis artifact chain unless the cache is stale or incomplete; bind the new
request to the existing report instead:

```bash
python skills/odds-analysis/scripts/direct_report_bind.py \
  --direct-request-path <direct_request_path> \
  --manifest <manifest.json> \
  --report <report.md> \
  --cache-mode reuse_existing_report \
  --source-snapshot-id <snapshot id/path> \
  --api-refresh-performed false
```

If the analysis actually generated a fresh report from local snapshots, use
`--status completed`, `--cache-mode local_snapshot_rebuild`, and
`--api-refresh-performed false`. Only set `--api-refresh-performed true` when a
paid external API was explicitly called. `report_guard.py` will fail relay if
the direct request record is still `received`, lacks Telegram `message_id`, or
does not back-link to the exact report and manifest.

```markdown
# WC26 {match_id} {home} vs {away} - {window} Handicap Report

cutoff_utc:
workflow_contract: wc26.direct_report.v1
direct_request_id:
direct_request_path:
mode: live | simulation
report_completeness: complete | partial
canonical_id:
football_data_id:
source_quality:
source_quality_cap:
final_status:
review_required:
artifact_manifest_path:
artifact_contract_status: pass
report_guard_status: pass
window:
timing_class:
information_event:
entry_time_utc:
entry_price:
lineup_status: not_required | pending | confirmed | missing

## 1. One-Line View

## 2. Source Snapshot

| Source | Type | Snapshot ID | Captured | Freshness | Status |
| --- | --- | --- | --- | --- | --- |

## 3. Official Match Facts

## 4. Market Board

| Market | Line | Book | Source Unit | Current Decimal | Snapshot ID | Devig Artifact | No-Vig Market | Model Fair | p_adj | Edge | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## 5. Football Read

## 5A. Path A Cross-Book Value Scan

artifact_id:
input_snapshot:
sharp_anchor:
markets_scanned:
quotes_scanned:
edge_count:
noise_edge_count:
actionable_count:
raw_actionable_count:
relay_actionable_count:
qualified_play_count:  # relay-level only; must equal relay_actionable_count
best_edge:
best_actionable_edge:

| Market | Outcome | Book | Offered | Sharp Fair Odds | EV Shin | EV Power | EV Mult | Survives All Methods | Band | Suspect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

This section is deterministic arithmetic only. State market coverage by market
(`h2h`, `spreads`, `totals`) and whether any edge is `noise`, `actionable`, or
`suspect`. Do not summarize "public bias" here.

## 6. Market Psychology

## 7. Bookmaker Intent Hypotheses

| Hypothesis | Evidence | Falsifier | Weight |
| --- | --- | --- | --- |

## 8. Anti-AI Red Team

## 9. Adjustment Ledger

Default: `p_adj = p_market` unless the rows below justify a change.

| Factor | Direction | Magnitude | Evidence | Why Not Priced | Falsifier | Uncertainty |
| --- | --- | --- | --- | --- | --- | --- |

edge_formula_scalar_no_push: p_adj - p_market
ev_formula_scalar_no_push: p_adj * decimal_odds - 1
ev_formula_asian: settlement EV by handicap legs
sigma_total:
robust_ev:
uncertainty_gate_status:
adjustment_ledger_id:

## 9A. Numeric Artifact Manifest Summary

manifest_path:
contract_check_command:
contract_check_status:
report_guard_command:
report_guard_status:
skipped_sections:

## 9B. Mechanism Audit / Game-Theory Verdict

mechanism_audit_artifact:
mechanism_audit_status:
required_final_status:
blocking_mechanisms:

| Mechanism | Status | Required For Complete | Artifact | Evidence |
| --- | --- | --- | --- | --- |

| Source | Subject | Decision Enum | Evidence | EV/Delta |
| --- | --- | --- | --- | --- |

## 10. Final Decision

status:
market:
acceptable_price:
confidence:
stake_advice:
review_required:
next_check:

## 11. Post-Match Grading Slot

closing_line:
result:
CLV:
CLV_by_timing_class:
Brier/log_loss:
lesson:
```

## Direct Completion

Direct gateway reports use direct request records as the completion surface. If
final status is `pass`, `watch`, or non-actionable `lean`, write/update the
direct request record and relay only the deterministic direct summary:

```json
{
  "match_id": "...",
  "source_quality": "B",
  "final_status": "watch",
  "mode": "live",
  "artifact_manifest_path": "/hermesdata/worldcup-2026-handicap/reports/artifacts/...",
  "artifact_contract_status": "pass",
  "report_guard_status": "pass",
  "timing_class": "confirmation",
  "report_path": "/hermesdata/worldcup-2026-handicap/reports/match/...",
  "review_required": false
}
```

If final status is `qualified_play`, keep `review_required=true` and block for
human approval:

```text
review-required: qualified_play needs human approval; see report_path
```

## Script Entry Points

- `scripts/verify_keys.py`: verify API availability without printing secrets.
- `scripts/direct_request_record.py`: create the direct Telegram request record that replaces task identity for direct gateway reports.
- `scripts/direct_report_bind.py`: bind a cached report/manifest to the current direct request and mark the request completed without paid API refresh.
- `scripts/direct_summary.py`: render the deterministic zh-CN Telegram summary from a guarded manifest/report; this is the direct gateway reply surface.
- `scripts/fixture_registry.py`: load the football-data fixture cache, resolve canonical `football_data_id` identity, and validate `M001` display aliases against home/away/kickoff.
- `scripts/cross_book_scan.py`: Path A arithmetic scanner; scans all 1X2 outcomes plus same-line AH/totals inside one multibook snapshot and emits quote-level EV/survives data.
- `scripts/mechanism_audit.py`: deterministic mechanism/game-theory audit; converts Path A/B/C artifacts into fixed verdict enums and required final-status downgrade.
- `scripts/devig.py`: no-vig, scalar EV, Asian settlement EV/Kelly, and uncertainty-gate helper.
- `scripts/model_margin.py`: score matrix / Poisson baseline to margin distribution for Asian markets.
- `scripts/numeric_artifact.py`: writes deterministic devig artifact JSON and number references.
|- `scripts/wc26-match-analyze.py`: **编排器 — 所有 live direct report 的唯一确定性入口**。运行完整链：devig（内联 Shin/Multiplicative）+ crossbook（Path A）+ consistency_triangle（Path C）+ mechanism_audit（综合审计）+ manifest + guarded report。传 `--mode full` 产出 ≥6 件 artifact。依赖快照 snapshot JSON，不额外消耗 API 配额。
- `scripts/snapshot_resolver.py`: selects reusable snapshots by window/reuse group/TTL without spending quota.
- `scripts/report_contract.py`: validates numeric provenance before report completion.
- `scripts/report_guard.py`: validates Markdown report headers, simulation mode, manifest, and relay safety.
- `scripts/calibration_gate.py`: validate calibration proposals before approved apply.

## Reference Files

- `references/artifact-chain.md`: artifact chain and pipeline overview.
- `references/contract-guard-pitfalls.md`: report_contract and report_guard pitfalls.
- `references/direct-report-pitfalls.md`: collected pitfalls from live direct report sessions (window naming, huge-favorite matches, source_quality_cap sync, cross-book anchor detection, manifest provides).
