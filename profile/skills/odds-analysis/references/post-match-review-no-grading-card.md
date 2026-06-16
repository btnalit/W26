# Post-Match Review Without Grading Cards

When matches are FINISHED but `grading/cards/grade-*.json` is missing (grading cron
hasn't run, or the auto-grading pipeline has a gap), the post-match review workflow
in SKILL.md Step 1–4 must fall back to manual extraction from snapshots.

This reference documents the manual technique used successfully in the 2026-06-16
batch review of 5 matches (M011, M012, M014, M015, M016), 4 of which had manifests
and crossbook artifacts but no grading cards.

## Preconditions

- Fixture data exists in `snapshots/fixtures/football-data-wc-matches-latest.json`
  with `status: FINISHED` and a `score.fullTime` field
- Report snapshots exist (from the manifest's `source_freshness`)
- Closing snapshots exist (the snapshot closest to KO, ideally within 60 minutes)

## Manual Fallback Procedure

### Step A: Collect Results from Fixture Data

```python
import json
with open('snapshots/fixtures/football-data-wc-matches-latest.json') as fh:
    d = json.load(fh)
# The data dict has structure: {'filters', 'resultSet', 'competition', 'matches'}
matches = d['matches']  # list of 104 match dicts
for m in matches:
    if m['id'] == target_football_data_id:
        score = m['score']
        ft_home = score['fullTime']['home']
        ft_away = score['fullTime']['away']
        outcome = 'draw' if ft_home == ft_away else ('home' if ft_home > ft_away else 'away')
```

### Step B: Extract Pinnacle 1X2 from Report and Closing Snapshots

The multibook snapshot structure:

```python
snapshot = json.load(open(path))
for m in snapshot['data']:  # list of matches
    if m['home_team'] == home_name:
        for bm in m['bookmakers']:
            if bm['key'] == 'pinnacle':
                for mk in bm['markets']:
                    if mk['key'] == 'h2h':
                        for o in mk['outcomes']:
                            if o['name'] == home_name:
                                home_odds = o['price']
                            elif o['name'] == away_name:
                                away_odds = o['price']
                            elif o['name'] == 'Draw':
                                draw_odds = o['price']
```

**Team name matching caveat**: the-odds-api uses abbreviated names (e.g., "USA" not
"United States", "Bosnia & Herzegovina" not "Bosnia-Herzegovina"). Use substring
matching to handle both forms.

### Step C: Compute No-Vig Probabilities (Multiplicative Method)

The multiplicative (basic) no-vig is sufficient for post-match review when grading
cards are missing and we only need directional CLV:

```python
def multiplicative_no_vig(odds_list):
    """odds_list = [home, draw, away]"""
    implied = [1/o for o in odds_list]
    total = sum(implied)
    return [imp / total for imp in implied]
```

Shin no-vig requires solving for z numerically; when the crossbook artifact
(`cross_book_scan.py` output) exists, prefer its `fair_probs.shin` values instead.

### Step D: Compute Metrics

**CLV**: Respect the grading card's CLV when it exists — it has correct market
attribution (`clv_detail.market`). Do NOT recompute CLV from h2h no-vig
probabilities when a grading card already provides a market-specific CLV.
The grading card CLV is computed on the market where the report took its
primary position (typically spreads or totals), not on the match outcome.

When no grading card exists, CLV can be computed as probability shift toward
the actual outcome:
```
prob_shift_pp = (close_probs[outcome_idx] - report_probs[outcome_idx]) * 100
```

Mark this as "h2h probability shift (pp)" — NOT as "CLV" — to distinguish
it from market-level CLV. If the actual outcome had <10% report probability
and the report was NO PLAY, mark it `not_meaningful` instead of computing a
spurious CLV number. A +7.9% CLV on a 6.9% draw that nobody would have
positioned on is noise dressed as signal.

**Brier score** (value range [0, 2], lower is better):

```python
UNIFORM_BASELINE = (1-1/3)**2 + (0-1/3)**2 + (0-1/3)**2  # = 0.6667

def brier(probs, outcome_idx):
    """Brier = Σ(p_i - o_i)². o_i=1 for actual outcome, 0 otherwise."""
    score = 0
    for i, p in enumerate(probs):
        score += (1 - p)**2 if i == outcome_idx else (0 - p)**2
    return score

def brier_skill_vs_uniform(brier_val):
    """Negative = better than uniform random (good). Positive = worse."""
    return brier_val - UNIFORM_BASELINE
```

ALWAYS present all three numbers together: Brier, uniform baseline (0.6667),
and brier_skill_vs_uniform. Never present bare Brier without baseline —
bare absolute Brier invites narrative-over-numbers.

**Counterfactual Kelly** (full Kelly, the most aggressive):
```python
kelly_full = (odds[outcome_idx] * probs[outcome_idx] - 1) / (odds[outcome_idx] - 1)
```

If `kelly_full < 0`: NO PLAY was correct even in hindsight — no edge existed at
report odds for the winning outcome.

### Step E: Output Format for Batch Reviews

When reviewing 3+ matches in one session, use a summary table first:

```
| # | Match | Score | Outcome | What We Did | Verdict | Cfact Kelly | CLV |
|---|-------|-------|---------|-------------|---------|-------------|-----|
| M011 | CIV vs ECU | 1-0 | HOME | T-24h NO PLAY | ✅ Correct | -1.1% | -0.3pp |
```

Then per-match detail, then aggregate analysis with these dimensions:
- NO PLAY discipline (counterfactual Kelly across all matches)
- CLV direction (positive/negative count)
- Coverage blind spots (matches with no analysis)
- Grading card gaps
- Brier score interpretation

## Known Limitations

1. **No Shin no-vig without crossbook artifact**: The manual multiplicative no-vig
   is slightly less accurate. When crossbook artifacts exist, use their values.

2. **AH/Totals not covered**: Manual AH settlement EV requires a score distribution
   matrix. Skip AH/totals in manual fallback unless the crossbook artifact has
   pre-computed settlement numbers.

3. **No scoreline profile**: Path C consistency triangle data is needed for
   scoreline probability ranking. Skip when consistency artifact is missing.

4. **Closing quality assessment**: Compare closing snapshot capture time to
   kickoff time. <60min = clean, 60-240min = degraded, >240min = not usable.

## Session Example (2026-06-16)

5 matches reviewed via this fallback. See the daily review output in the session
transcript for the full format. Key finding: 4/4 matches with analysis had NO PLAY
correct, counterfactual Kelly negative on all outcomes.
