# Pinnacle H2H Gap Pattern

## When Pinnacle Doesn't Offer 1X2

Pinnacle sometimes omits the 1X2 (H2H) market for extreme-favorite matches,
typically when the favorite's implied win probability exceeds ~92-95% (decimal
odds ≤ 1.08). This session confirmed the pattern on two matches:

| Match | Elo Gap | Pinnacle H2H? | Sharp Anchor Fallback | source_quality | Path C |
|-------|---------|--------------|----------------------|----------------|--------|
| M009 GER vs CUR | 428 | ❌ None | Betfair Exchange | cap=C | BLOCKED |
| M010 NED vs JPN | ~0 | ✅ Full | Pinnacle | cap=C (other) | PASS |

## Degradation Chain

When Pinnacle H2H is absent, a cascade of analysis degradations follows:

1. **No p_market for 1X2** — manifest must set `p_market.home/draw/away: null`
2. **No p_adj** — `p_adj := p_market` defaults to null; adjustment ledger
   cannot start from a market baseline
3. **devig_three_method gate → skipped_missing_source** — no three-method
   no-vig validation for 1X2
4. **Path C consistency triangle → BLOCKED** — requires 1X2-AH-totals
   coherency check, which needs valid 1X2 no-vig probabilities
5. **source_quality_cap → C** — missing sharp H2H anchor degrades the
   entire report's actionable ceiling
6. **final_status → watch** — mechanism audit sets required_final_status=watch
   when a required mechanism (Path C) is blocked

## What Still Works

- **Path A cross-book scan** can use Betfair Exchange as H2H sharp anchor.
  The cross_book_scan.py artifact will run, find edges, and populate the
  manifest — but `relay_actionable_count` stays 0 because source_quality_cap=C
  blocks relay actionability.
- **Asian handicap and totals** remain viable if Pinnacle offers those lines.
  They use Pinnacle as sharp anchor independently of H2H.
- **Deep Research** can still run and add interpretive context.

## Detection

Before running analysis, check the multibook snapshot for Pinnacle H2H
presence. The cross_book_scan.py artifact will detect this automatically:
`markets.h2h.status` will be `"ok"` with `"sharp_anchor": "betfair_ex"`
instead of `"pinnacle"`, and the manifest's `analysis_gates.devig_three_method`
will show `"status": "skipped_missing_source"`.

## Relevance

This pattern will recur throughout WC26 for any match where one team is an
overwhelming favorite. Expected triggers: top-10 FIFA teams vs debutants or
teams outside the top 80. Check the Elo gap — gaps above 350+ are strong
predictors of Pinnacle H2H omission.
