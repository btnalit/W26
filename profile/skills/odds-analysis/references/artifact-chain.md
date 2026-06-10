# Direct Report Artifact Chain

Mandatory build order for `wc26.direct_report.v1` complete reports.

## Four required artifacts

Each with non-overlapping `provides`:

| # | Artifact | Script | Input Snapshot | `provides` |
|---|----------|--------|----------------|------------|
| 1 | devig | devig.py | Pinnacle (the-odds-api) | `["no_vig", "asian_handicap", "totals"]` |
| 2 | cross_book_scan | cross_book_scan.py | Multibook (the-odds-api) | `["path_a_crossbook"]` |
| 3 | consistency_triangle | consistency_triangle.py | Pinnacle (the-odds-api) | `["path_c_consistency"]` |
| 4 | mechanism_audit | mechanism_audit.py | Manifest (reads all above) | `["mechanism_audit"]` |

## Capability collision pitfall

`report_contract.py` uses `_artifact_capabilities()` which does **substring
matching** on `provides` items. If a devig artifact includes `"cross_book"`
in its provides list, the contract validator assigns it to the
`path_a_crossbook` gate and then rejects it:

```
path_a_crossbook artifact devig-M010-X must be a cross_book_scan artifact, not devig
```

**Fix:** Strip `cross_book` and `consistency_triangle` from the devig
artifact's `provides`. Each capability maps to exactly one artifact.

## Build order

```
1. Generate devig artifact from Pinnacle snapshot
2. Generate crossbook artifact from multibook snapshot
3. Generate consistency artifact from Pinnacle snapshot
4. Write all three artifact paths + provides into manifest
5. Run report_contract.py — fix any issues before proceeding
6. Generate mechanism_audit LAST (it reads manifest + cross-references)
7. Write report markdown with sections 5A and 9B populated from artifacts
8. Run report_guard.py
9. Run direct_summary.py for user-facing output
```

## Validation checkpoints

```bash
# After step 4:
python3 skills/odds-analysis/scripts/report_contract.py <manifest.json>

# After step 7:
python3 skills/odds-analysis/scripts/report_guard.py <report.md>

# After step 9:
python3 skills/odds-analysis/scripts/direct_summary.py --manifest <manifest.json> --report <report.md>
```

## Common failure modes

| Symptom | Root cause | Fix |
|---------|-----------|-----|
| `path_a_crossbook artifact X must be a cross_book_scan artifact, not devig` | Devig provides includes `cross_book` | Strip from devig provides |
| `missing artifact capabilities: mechanism_audit` | No mechanism_audit artifact in manifest | Generate via mechanism_audit.py |
| `mechanism_audit path_a quotes_scanned does not match crossbook summary` | Audit generated before crossbook was in manifest | Regenerate audit after manifest update |
| analysis_gates missing mechanism_audit | Gate not in manifest's analysis_gates | Add "mechanism_audit": "pending" with reason "auto-populated at manifest generation" |
| all report_guard_status entries must be pass | Duplicate header blocks have mixed status or guard didn't run | 重跑 guard 并修复实质错误；不得手动覆写为 pass |
