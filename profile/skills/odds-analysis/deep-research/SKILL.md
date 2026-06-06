---
name: deep-research
description: "WC26 post-report deep research: Exa search + Jina Reader + LLM synthesis after a guarded handicap report has landed"
version: 1.2.1
author: Hermes Agent
platforms: [linux]
metadata:
  hermes:
    tags: [odds-analysis, research, exa, jina, wc26, post-report]
    related_skills: [odds-analysis]
  triggers:
    - WC26 match report/manifest is complete and a Telegram final reply is about to be sent
    - user asks for deep research on a match
    - user asks for betting direction, market psychology, historical benchmarks, or external context after a report
    - analysis is WATCH/PARTIAL and the user wants a more readable LLM interpretation
---

# WC26 Deep Research Finalizer

This skill is a **post-report final analysis layer**.

It runs **after** the deterministic WC26 report, manifest, contract, guard,
mechanism audit, and role-engine outputs have landed. It uses Exa and Jina to
add external context and then produces a readable Telegram-facing betting
direction analysis.

It is not the odds pipeline. It is not `report_contract`. It must not rewrite
the numeric report.

## Hard Boundary

Deep Research may:

- read the guarded report, manifest, rich summary, role-engine artifact,
  mechanism-audit artifact, and Path C `market_profile` artifact field;
- call Exa through `web_search` for historical benchmarks, market-efficiency
  studies, and pattern discovery;
- call Jina Reader through `curl https://r.jina.ai/http://...` or
  `curl https://r.jina.ai/https://...` for official/team/media source reading;
- synthesize a human-readable view of bookmaker intent, public psychology,
  AI lag, trap risk, matchup context, and waiting triggers;
- give a **directional betting view** such as "watch the underdog +spread",
  "wait for favorite price drift", "avoid totals until lineup", or
  "research confirms no-play";
- explain what evidence would upgrade, weaken, or reverse the direction.

Deep Research must not:

- alter `p_market`, `p_adj`, EV, Kelly, `final_status`, `relay_actionable`, or
  `qualified_play_count`;
- enter or modify the adjustment ledger;
- recalculate the main report as LEAN / qualified play from research alone;
- present historical/news context as a priced edge without the deterministic
  report already allowing it;
- hide that the baseline report is `WATCH`, `PARTIAL`, `PASS`, or `NO PLAY`;
- fabricate source URLs, samples, quotes, injury news, venue facts, or lineup
  claims.
- recompute market-profile numbers such as lambda, rho, top scores, BTTS,
  most likely total goals, or market-profile totals lean. These are
  deterministic Path C artifact fields only.
- introduce new probabilities, fair odds, "value", "edge", or actionable
  implications from market profile. Market profile is descriptive only.

If the research view points toward a side but the guarded report says no
actionable play, say so plainly:

```text
研究倾向: Curaçao +3.5
主报告裁定: PARTIAL / WATCH / NO PLAY
下注动作: 不下注; 等 T-72h/T-24h/T-60m 盘口和阵容确认
```

## Required Inputs

Before using Exa/Jina, locate and read the current match outputs:

1. `manifest_path`
2. `report_path`
3. existing Telegram/rich summary if available
4. `mechanism_audit` artifact if listed in the manifest
5. `role_engine` artifact if listed in the manifest
6. `cross_book_scan` artifact if listed in the manifest
7. `consistency_triangle` / Path C artifact if listed in the manifest

If the guarded report/manifest cannot be found, do not run speculative deep
research. Ask for or regenerate the report first.

## Market Profile Contract v1.2.1

Path C may expose a deterministic `market_profile` field with contract
`wc26.market_profile.v1`. This field is a mathematical projection of the sharp
market matrix, not a Deep Research calculation.

Deep Research may:

- quote and explain `market_profile.most_likely_1x2`;
- quote and explain `market_profile.total_line_lean`;
- quote and explain `market_profile.top_scores`, `top_total_goals`,
  `top_margin`, and `btts`;
- use the fixed footnote:
  `市场共识画像·描述性·非下注信号；最高概率不等于价值。`

Deep Research must:

- cite the Path C artifact as the evidence source for every market-profile
  number;
- state `market_profile.status` and `fit.max_abs_residual_pp` when discussing
  the profile;
- say "市场画像未生成/低可信" when `status=suppressed` or confidence is low.

Deep Research must not:

- recompute lambda/rho, score probabilities, totals probabilities, BTTS, or
  fair odds;
- add any market-profile number not present in the artifact;
- use market-profile language to imply a bet is valuable, underpriced,
  actionable, or an edge. Highest probability is not betting value.

## Tool Roles

| Tool | Required role | Use |
|------|---------------|-----|
| Exa (`web_search`) | broad discovery | historical AH/totals patterns, market-efficiency research, team-specific margins, comparable fixtures |
| Jina (`curl https://r.jina.ai/...`) | source reading | FIFA venue pages, team/FA pages, coach quotes, lineup/news articles, non-English media |

For a normal finalizer run, use both tools when possible:

- At least 2-4 Exa searches, batched by template.
- At least 1-3 Jina reads from high-value URLs found via Exa or known official
  pages.

If Jina cannot read a source, mark it as `jina_failed` and do not quote it as
evidence.

## Research Templates

Choose only the templates relevant to the report gaps and market shape.

### A. Historical Handicap Benchmark

Question: given the match strength gap and AH line, are there credible
historical patterns that support or weaken the market line?

Exa examples:

```text
"World Cup" "Asian handicap" "-3.5" "cover rate"
World Cup group stage huge favorite fails to cover spread
World Cup Elo gap 400 goal margin favorite underdog
```

Output:

```yaml
template: A
metric: historical_ah_context
direction: favorite|underdog|neutral
sample_size: N|unknown
confidence: high|medium|low
directness: direct_same_line|near_line|indirect
evidence_ids: [...]
limit: why this does or does not apply to the exact current line
```

### B. Team-Specific Margin/Style

Question: does either team have repeatable style, rotation, or margin behavior
that matters for the current line?

Exa examples:

```text
Germany World Cup group stage big win margin minnows
Curaçao national team defensive style coach World Cup preview
```

Jina examples:

```text
curl https://r.jina.ai/https://www.dfb.de/...
curl https://r.jina.ai/https://www.fifa.com/...
```

### C. Lineup, Injury, Rotation, Motivation

Question: are there named, current, falsifiable team-news facts that affect
pace, spread, totals, or side?

Use Jina first for official/team/news pages; Exa only to find sources.

Never treat predicted lineups as confirmed. Label them `rumor`,
`prediction`, `coach_quote`, or `official`.

### D. Venue/Weather/Travel

Question: does the venue create a real condition edge?

Use Jina for official venue/FIFA pages and a reliable weather source. For
indoor/retractable-roof venues, state whether weather is likely muted.

### E. Market Efficiency / Missing Sharp Market

Question: if the main report has missing H2H/Pinnacle or partial coverage, does
external evidence change how cautious we should be?

This template explains confidence and risk. It cannot create actionability by
itself.

## Evidence Discipline

Every material claim in the final synthesis must have a source tag:

```text
[DR-A1] Historical AH benchmark ...
[DR-C2] Coach/lineup source ...
[DR-D1] Venue/weather source ...
```

For each source, record:

- `source_id`
- `tool`: `exa` or `jina`
- `url`
- `title`
- `source_class`: `injury_news`, `lineup_news`, `squad_news`,
  `coach_quote`, `market_news`, `historical_context`, `venue_context`,
  `team_context`, or another explicit non-news context class
- `published_at_utc`: required for news-like sources
- `fetched_at_utc`
- `what_it_supports`
- `limitations`

Do not quote long passages. Use short snippets only when needed and keep them
under normal copyright limits.

## Pricing Freshness Contract v1.2

Deep Research is allowed to reason about whether news may be priced into the
current odds snapshot only when the source publication time is known.

Required baseline fields:

```json
{
  "baseline": {
    "snapshot_at_utc": "2026-06-05T14:34:08Z",
    "snapshot_source": "the-odds-api-multibook-20260605T143408Z",
    "baseline_report_generated_at_utc": "2026-06-05T15:30:00Z"
  }
}
```

For every `injury_news`, `lineup_news`, `squad_news`, `coach_quote`, or
`market_news` source, include:

```json
{
  "source_id": "DR-C1",
  "source_class": "squad_news",
  "published_at_utc": "2026-06-06T04:00:00Z",
  "fetched_at_utc": "2026-06-06T05:10:00Z",
  "pricing_freshness": "post_snapshot",
  "recency_bucket": "fresh_0_24h"
}
```

Rules:

- `fetched_at_utc` is **not** publication time. It only proves when Hermes read
  the page.
- Use Exa to discover sources, then use Jina/page content to find the actual
  publication timestamp. If publication time cannot be read, do not use that
  item as a news finding.
- `pricing_freshness` is computed against `baseline.snapshot_at_utc`:
  - `post_snapshot`: `published_at_utc > snapshot_at_utc`
  - `pre_snapshot`: `published_at_utc <= snapshot_at_utc`
  - `unknown`: only allowed for non-news context or failed/unused candidates
- `recency_bucket` is computed dynamically against `generated_utc`:
  - `fresh_0_24h`
  - `recent_24_72h`
  - `stale_gt_72h`
  - `unknown`
- The "three day" rule is a dynamic 72-hour bucket from the finalizer time,
  not a hardcoded calendar date.

Telegram wording rules:

- `post_snapshot`: may say "可能尚未被当前盘口吸收，等待下一张盘口确认".
- `pre_snapshot`: must say "该信息早于盘口快照，可能已被市场定价；只能观察盘口是否继续移动".
- `unknown`: must not discuss pricing freshness. Use "发布时间不明，不用于判断是否未定价".

Forbidden unless at least one relevant source is `post_snapshot`:

- "尚未 price in"
- "市场可能没消化"
- "旧快照在官宣前"
- "new squad announcement"
- "可能尚未被当前盘口吸收"

Before Telegram append, run:

```bash
python3 skills/odds-analysis/scripts/deep_research_contract.py \
  --artifact <deep-research artifact> \
  --manifest <manifest> \
  --text-stdin --json
```

If the contract fails, first remove the non-conforming finding lines from the
Telegram Deep Research section:

- remove lines that reference invalid news source ids such as `[DR-C1]`;
- remove "not priced in / 市场未消化 / 旧快照在官宣前" wording unless supported
  by a `post_snapshot` source;
- keep compliant historical, tactical, venue, and market-context findings.

After filtering, re-run the contract. If the filtered section passes, append it
with a short filter note. If it still fails, or if a `completed` Deep Research
section has no deep-research artifact path, send the guarded rich summary only.
The main report result is unaffected.

## Artifact Output

Write a post-report research artifact:

`/hermesdata/worldcup-2026-handicap/reports/artifacts/deep-research-{match_id}-{timestamp}.json`

Use this shape:

```json
{
  "artifact_type": "deep_research",
  "artifact_version": "1.2",
  "mode": "post_report_finalizer",
  "match_id": "M009",
  "generated_utc": "...",
  "baseline": {
    "manifest_path": "...",
    "report_path": "...",
    "snapshot_at_utc": "...",
    "snapshot_source": "...",
    "baseline_report_generated_at_utc": "...",
    "baseline_status": "PARTIAL / WATCH",
    "relay_actionable": 0,
    "source_quality_cap": "C"
  },
  "tools_used": ["exa", "jina"],
  "templates_run": ["A", "B", "D", "E"],
  "sources": [
    {
      "source_id": "DR-A1",
      "tool": "exa",
      "source_class": "historical_context",
      "url": "https://...",
      "title": "...",
      "published_at_utc": null,
      "fetched_at_utc": "...",
      "pricing_freshness": "unknown",
      "recency_bucket": "unknown",
      "what_it_supports": "...",
      "limitations": "..."
    }
  ],
  "findings": [
    {
      "finding_id": "DR-F1",
      "template": "A",
      "claim": "...",
      "direction": "toward_underdog",
      "confidence": "low",
      "evidence_ids": ["DR-A1"],
      "limitations": "Indirect sample, not exact same AH line"
    }
  ],
  "final_view": {
    "betting_direction": "watch_underdog_plus_spread",
    "direction_label_zh": "研究倾向: 观察受让方",
    "does_change_baseline": false,
    "action": "NO BET / WAIT",
    "why": "...",
    "upgrade_triggers": ["..."],
    "falsifiers": ["..."]
  }
}
```

## Telegram Final Synthesis

The final user-facing answer should be readable and direct. It should combine:

1. One-line baseline from the guarded report.
2. Deep Research direction.
3. Evidence bullets with `[DR-*]` tags.
4. Bookmaker/public/AI/trap interpretation.
5. Exact betting instruction boundary: `不下注 / 等价位 / 等阵容 / 人工复核`.
6. Upgrade triggers and falsifiers.

Start the Deep Research section with this exact marker so the WC26 Telegram
output hook can preserve it after the artifact-backed baseline summary:

```text
WC26_DEEP_RESEARCH_FINALIZER: completed
```

If the finalizer cannot complete, use:

```text
WC26_DEEP_RESEARCH_FINALIZER: failed
reason: <short reason>
```

Do not send a WC26 match final answer that contains only the rich summary unless
the finalizer failed or the user explicitly asked to skip Deep Research.

Example:

```text
WC26_DEEP_RESEARCH_FINALIZER: completed

M009 德国 vs 库拉索 — 主报告仍是 PARTIAL / WATCH / NO PLAY

Deep Research 结论:
研究倾向不是德国 -3.5, 而是观察库拉索 +3.5 或更高让球。

为什么:
- [DR-A1] 历史大热门首轮深盘穿盘证据偏弱, 但样本不是 exact -3.5。
- [DR-B2] 德国强弱差距真实, 但 4+ 净胜需要持续进攻强度。
- [DR-D1] 场地/天气没有明显支持 over 或大胜的额外条件。

下注方向:
现在不下注。若 T-72h/T-24h 库拉索 +3.5 升到更好价格, 且德国轮换信号增强,
才进入人工复核。若德国明确全主力且盘口不升反降, 这个方向撤回。
```

## Cost And Cache

- Exa uses configured web search quota. Do not run repeated broad searches for
  the same match if a fresh deep-research artifact already exists.
- Reuse a deep-research artifact if it is fresh enough for the window and the
  baseline report did not materially change.
- Jina is free but rate-limited. Space requests by 3+ seconds where practical.
- Cache Jina outputs under:
  `/hermesdata/worldcup-2026-handicap/snapshots/deep-research/jina/`

### v1.0/v1.1 Artifact Backward Compatibility

Artifacts written with `artifact_version: "1.0"` or `"1.1"` may still be read
for context, but they cannot support news freshness claims unless they contain
the v1.2 time fields above. If a reused artifact lacks `published_at_utc` for
news-like sources, either regenerate a v1.2 artifact or filter those source
lines out of the Telegram Deep Research append. Safe non-news context may still
be shown.

## Failure Behavior

If Exa/Jina fails:

- do not block the guarded report;
- send the rich summary fallback;
- append a short line: `Deep Research 未完成: <reason>; 主报告结论不受影响`;
- never invent research findings to make the final answer look complete.

## Pitfalls

### Venue Discovery is Unofficial

When fixture_registry.py returns `venue: null`, Deep Research via Exa/Jina
may surface venue names from news articles, previews, or unofficial sources.
Example: M010 (Netherlands vs Japan) venue was null in the registry but
Squawka listed "Arlington (AT&T Stadium)".

**Always label externally-discovered venues as unofficial:**
```
场地: Arlington (AT&T Stadium) — 非官方确认(<来源名>,fixture registry仍为null)
```

Never back-fill the manifest's venue field from deep research. The venue is
a fact-lock field that only FIFA official sources or football-data.org updates
can resolve. Deep Research venue is supplementary context only.

### v1.0/v1.1 Artifact Reuse

Existing deep-research artifacts written with `artifact_version: "1.0"` or
`"1.1"` (e.g. early M009/M010 artifacts from 2026-06-05/06) lack the full v1.2
pricing freshness contract. They may be reused only for non-news context and
safe "needs new odds confirmation" language. If the final Telegram section
mentions squad/injury/lineup freshness or "not priced in", regenerate the
artifact as v1.2 first or let the Telegram enforcer strip those lines before
sending. Do not silently send stale freshness claims.

### Squad Announcement Staleness

The most common reason a cached deep-research artifact becomes materially
stale: **squad announcements / injury news published after the artifact was
created**. This fires especially in the T-9d → T-3d window when FIFA
deadlines force 26-man lists.

When reusing an existing artifact, always run 2-3 fresh `web_search` queries
for the current squad/injury state BEFORE finalizing the Telegram reply:

```text
"<team> World Cup 2026 squad announcement June 2026"
"<team> injury World Cup 2026 Mitoma Gakpo squad"
"<team> World Cup 2026 squad injuries"
```

If squad news materially changes the findings (e.g., Mitoma OUT), write a
**new** deep-research artifact that:
- Copies `baseline` and any still-valid `sources`/`findings` from the old one.
- Adds fresh `sources` with `published_at_utc`, `fetched_at_utc`,
  `pricing_freshness`, and `recency_bucket` for the new searches.
- Adds new `findings` tagged with the updated templates.
- Updates `final_view` (especially `upgrade_triggers` and `falsifiers`).

Do NOT silently reuse a pre-squad-announcement artifact without checking.
The `does_change_baseline` flag remains `false` unless the deterministic
report itself is regenerated — squad news affects the *supplementary* deep
research, not the artifact-backed baseline status.

### v1.2 Artifact Field Correction Workflow

When writing a new v1.2 deep-research artifact, `pricing_freshness` and
`recency_bucket` are **computed by `deep_research_contract.py`**, not by the
LLM. Do not try to compute them manually — you will almost certainly get some
wrong. The most common failure mode is mentally flipping which date is earlier
(e.g., thinking a May 15 squad announcement happened "after" a June 5 snapshot
because "May" sounds more recent in the current month). Always compare ISO
timestamps numerically: `2026-05-15 < 2026-06-05` → `pre_snapshot`. The correct
workflow is:

1. Write the artifact with **best-guess** `pricing_freshness` and
   `recency_bucket` values on every source.
2. Run `deep_research_contract.py --artifact <path> --manifest <manifest> --json`.
3. If the contract returns `status: fail` with errors like
   `recency_bucket=X does not match computed Y`, read the computed values from
   the contract output's `normalized_sources` array.
4. Patch the artifact JSON in-place with the computed `pricing_freshness` and
   `recency_bucket` values. Use `terminal` with `python3 -c` for this, not
   `patch` or `read_file` from `execute_code` (see JSON reading pitfall below).
5. Re-run the contract checker. Repeat until `status: pass`.
6. Only then proceed to the Telegram synthesis.

This is a required gate — a v1.2 artifact that has not passed the contract
checker must not be used as the basis for Telegram deep-research claims.

### JSON File Reading in execute_code

`read_file()` from the `execute_code` sandbox returns content with line number
prefixes (e.g., `1|{`, `2|  "key": ...`). This breaks `json.loads()`.

**Do not use `read_file` from `execute_code` for JSON files.** Instead, use
`terminal` with a Python one-liner:

```bash
python3 -c "import json; data=json.load(open('/path/to/file.json')); print(data['key'])"
```

Or read the whole file with `terminal` + `cat` and parse from there. The same
applies for patching: use `terminal` with Python, not `execute_code`'s
`read_file` + `json.loads`.
