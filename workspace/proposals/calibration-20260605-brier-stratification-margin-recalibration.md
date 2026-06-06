# Calibration Proposal: Brier Stratification + Margin Recalibration

**Proposal ID:** `cal-prop-20260605-001`
**Author:** owner review (M005 audit)
**Status:** `PENDING` (blocked for review)
**Created:** 2026-06-05
**Requires:** human approval + `calibration_gate.py PASS`

---

## 1. Problem Summary

The M005 audit (owner review, 2026-06-05) identified two structural weaknesses in current model evaluation that limit the system's ability to know when the model is trustworthy:

### ① Holdout Brier threshold has no meaningful selectivity

Current: `holdout_pass` = Brier < 0.55 AND < 85% of random (0.667). This only proves the model is not random — any minimally working model passes. Worse, the holdout set is dominated by blowout matches (strong vs weak), which artificially lower the Brier. A model that predicts "Brazil beats tiny opponent" is trivial, but that's not where you bet. The relevant test is on **competitive matches** where actual edge might exist.

### ② Margin distribution (net胜球分布) is overconfident on strong-vs-weak

The model assigns Switzerland -1.75 a 40.65% probability of winning by 3+ against Qatar. This is a systemic overestimate — the known "strong vs parked-bus weak" goal model bias documented in MEMORY.md. The overconfidence amplifies in the tails (3+ goals), which directly inflates Asian handicap settlement EV.

Current "fix" is to suppress all model-driven action via the iron law, but the underlying bias is not addressed. Even after pass status, affected matches would produce inflated AH/totals EV that the uncertainty gate would need to swallow.

---

## 2. Proposal: Two Independent Changes

### ① Brier by Competitiveness Stratification

**What:** Instead of a single holdout Brier, compute and report per-stratum Brier for three buckets defined by the **market-implied favorite probability** (or model-implied, if market unavailable):

| Stratum | Definition | What it tests |
|---------|-----------|---------------|
| **Blowout** | Implied favorite > 75% | Model on mismatches — not actionable |
| **Competitive** | Implied favorite 40–75% | Model on real betting matches — **this is the meaningful test** |
| **Toss-up** | Implied favorite < 40% | Near-even matches — hardest test, small sample |

**Pass criterion:** `holdout_pass` requires Brier < 0.55 in the **competitive stratum** specifically. The blowout stratum is informational only — it must not count toward qualification.

**Data source:** martj42 historical results already in the pipeline. No new data needed. The stratification key is the **fitted model probability** of the favorite at match time — for historical data, use the DC model's retrospective prediction, or the Elo-based proxy.

**Implementation:**
- `calibration_check.py` already computes per-bucket Brier. Extend to output `brier_by_stratum: { blowout: N, competitive: N, toss_up: N }`.
- `holdout_pass` gate: `competitive.brier < 0.55` AND `brier < 0.85 * random_for_stratum`.
- Display in Market Board: `0.734 (info, competitive Brier 0.51)` — the competitive Brier is the headline number.

**Pitfall:** Stratum sizes may be uneven. Some World Cup groups have no competitive matches because the implied spread is too wide. Solution: floor the competitive-stratum validation at `min_matches=20`. If < 20 competitive matches exist, cap at `insufficient_data` for the stratified check (but still show informational Brier).

### ② Margin Distribution Historical Recalibration (Primary) + Market Regression (Secondary Cap)

**What:** The margin distribution (score matrix by goal difference) is currently unfitted — it comes directly from the DC model's Poisson-likelihood output with no correction for known overconfidence. Fix it by recalibrating against actual historical margins.

**Primary mechanism — Historical margin recalibration:**

1. For every historical match where the DC model predicted, record:
   - The predicted margin probabilities (bucketed: -5, -4, ..., 0, ..., +4, +5)
   - The actual margin
2. Bucket by **predicted win probability of the favorite** (same stratification as above, or a simpler binned approach: 50-60%, 60-70%, 70-80%, 80-90%, 90%+)
3. Within each bucket, compute **empirical margin CDF** vs **model-predicted margin CDF**
4. Apply isotonic regression (or Platt scaling) to the margin log-odds per bucket to make the tails honest

Output: a `margin_calibration_map` that the worker applies to `margin_distribution` before computing AH/totals settlement EV.

**Verification:** After calibration, the gap between predicted and empirical Pr(margin ≥ 3) for strong-favorite matches should shrink. Target: Brier on margin-bucket assignment improves by ≥ 0.02.

**Secondary mechanism — Market regression as cap (NOT primary):**

Once the margin distribution is historically calibrated, optionally shrink the remaining model-market gap using a **capped market pull**:

```
p_final(m) = α * p_calibrated_model(m) + (1-α) * p_market(m)
```

Where:
- `α ≥ 0.5` (model keeps majority weight — prevents market echo)
- `α` is determined by relative uncertainty: wider calibration error → smaller α
- This is **never** applied when p_adj = p_market — it only affects the EV that feeds diagnostic analysis (robust_ev, settlement EV insights)

**What this does NOT mean:**
- This is NOT "let the model be a market parrot" — the model still leads (α ≥ 0.5)
- This does NOT change p_adj — p_adj remains p_market by default
- This only affects the diagnostic reporting of model-driven AH EV so it's more honest

---

## 3. Bounded Scope

| Item | In Scope | Out of Scope |
|------|----------|--------------|
| ① | Add competitive-stratum Brier to calibration_check.py | Change holdout_pass criterion without competitive-bucket minimum threshold |
| ① | Display both on Market Board | Retroactively change existing reports |
| ② | Build margin_calibration_map from historical data | Change p_adj logic or pass/decision gates |
| ② | Apply calibration to margin_distribution in resolve_model.py | Touch the iron law (p_adj=p_market default, model-market divergence never an edge) |
| ② | Market regression cap (α ≥ 0.5) for diagnostic EV only | Use regression cap for anything beyond diagnostic display |

---

## 4. Rollback Rule

Rollback if either condition is met within 30 days:
- ① Competitive-stratum Brier improves by < 0.01 relative to total Brier (stratification didn't add information)
- ② Calibrated margin distribution produces AH settlement EV that is systematically more wrong than uncalibrated (checked via 10+ postmatch gradings)

Rollback: revert the `margin_calibration_map` to identity, revert `calibration_check.py` to single-bucket Brier.

---

## 5. Implementation Order

1. Extend `calibration_check.py` to output per-stratum Brier (data already available in martj42 holdout)
2. Build `margin_calibration_map` from the same holdout set (split predict/calibrate to avoid overfitting)
3. Wire into `resolve_model.py` so model artifacts include both raw and calibrated margin distributions
4. Wire stratification into `holdout_pass` gate in calibration status display
5. Add market regression cap (α ≥ 0.5) as optional diagnostic EV shrink in numeric_artifact.py

Steps 1-3 can be parallel. Step 4 is a one-line threshold change. Step 5 is optional/can wait.

---

*This proposal was drafted from owner review feedback (2026-06-05 M005 audit). Apply requires human approval + `calibration_gate.py PASS`.*
