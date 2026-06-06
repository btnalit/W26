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
qualified_play_count:
best_edge:

| Market | Outcome | Book | Offered | Sharp Fair Odds | EV Shin | EV Power | EV Mult | Survives All Methods | Band | Suspect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

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
