# Direct Report Pitfalls (WC26 odds-analysis)

Collected from M010 (Netherlands vs Japan) and M009 (Germany vs Curaçao)
live analysis sessions, 2026-06-05.

---

## 1. Window Naming: Pre-T-72h Windows

`report_contract.py` enforces hours-to-kickoff ranges per window name.
If hours_to_kickoff > 84, using `T-72h_early` will fail with:

```
window T-72h_early requires 60.0-84.0 hours_to_kickoff, got NNN.N
```

**Fix:** Use `T-{N}d_early_structural` where N = ceil(hours_to_kickoff / 24).
Example: Germany vs Curaçao at 218h → `T-9d_early_structural`.

Also ensure `timing_class` is `early_structural` in both manifest and report header.

---

## 2. Huge-Favorite Match: Pinnacle No H2H

For matches with extreme Elo gaps (400+ points), Pinnacle **does not offer
1X2 (H2H)** — only spreads and totals. Examples: Germany vs Curaçao (Δ428 Elo).

**Consequences:**
- `devig_three_method` gate → `skipped_missing_source` (no sharp H2H anchor)
- `cross_book_scan.py` H2H → `no_sharp_anchor` (Pinnacle missing; Betfair Exchange
  key `betfair_ex_eu` does not match `SHARP_BOOKS = ("pinnacle", "betfair_ex")`)
- `p_market` and `p_adj` for 1X2 → set to `null` with a `_note` explaining why
- Path C consistency triangle → blocked (requires H2H devig)

**Manifest pattern for this case:**

```json
{
  "p_market": {"home": null, "draw": null, "away": null,
    "_note": "p_market not available — no Pinnacle H2H"},
  "p_adj": {"home": null, "draw": null, "away": null,
    "_note": "p_adj defaults to p_market; p_market unavailable → p_adj cannot be derived"},
  "analysis_gates": {
    "devig_three_method": {"status": "skipped_missing_source",
      "reason": "Pinnacle does not offer H2H for this match"}
  }
}
```

Soft book H2H prices can be listed for diagnostic reference (e.g., Marathonbet
GER 1.04, Betfair Exchange GER 1.06) but must be clearly marked as soft.

---

## 3. source_quality_cap Synchronization

`report_contract.py` caps `source_quality_cap` to `C` for partial reports.
`report_guard.py` checks that the report header's `source_quality_cap` matches
the manifest's `source_quality_cap` AND both match what `report_contract` computed.

If you set `source_quality_cap: B` in both header and manifest but the report is
partial, report_guard will fail with:

```
source_quality_cap header B does not match report_contract C
source_quality_cap manifest B does not match report_contract C
```

**Fix:** Always set `source_quality_cap: C` in BOTH the manifest JSON AND the
report Markdown header for partial reports. `source_quality: B` can stay —
that's the raw data quality before the partial-report downgrade.

---

## 4. No-Vig Method: Scalar Artifact vs Cross-Book Scan

- `numeric_artifact.py scalar` uses **multiplicative** as the default devig
  method (via `devig.py`'s `devig_three_method` which returns multiplicative probs).
- `cross_book_scan.py` uses **shin** as the `PRIMARY` method.
- The two produce slightly different no-vig probabilities (typically ~0.3-0.5pp).

**Market Board convention:** Cite the cross_book_scan shin probabilities when
Path A is available, since that's the scanning primary. Cite the scalar
multiplicative artifact when Path A is unavailable.

**Example (M010 Netherlands vs Japan):**
- Scalar (multiplicative): NED 48.61% / DRAW 25.93% / JPN 25.46%
- Crossbook (shin):      NED 48.99% / DRAW 25.75% / JPN 25.26%
- Δ ≈ 0.4pp — well within three-method tolerance.

---

## 5. direct_report_bind.py Cache Mode

When building from a local snapshot (no paid API refresh):

```bash
python3 skills/odds-analysis/scripts/direct_report_bind.py \
  --direct-request-path <path> \
  --manifest <manifest.json> \
  --report <report.md> \
  --cache-mode local_snapshot_rebuild \
  --source-snapshot-id <snapshot_id> \
  --api-refresh-performed false
```

`report_guard.py` will fail relay if the direct request record is still
`received`, missing `report_path`/`manifest_path`, or missing
`api_refresh_performed`.

---

## 6. Manifest artifact provides for asian_handicap + totals

`report_contract.py` requires these capabilities:
`{"devig_1x2", "path_a_crossbook", "asian_handicap", "totals",
  "path_c_consistency", "mechanism_audit"}`

The cross_book_scan artifact naturally covers AH (spreads) and totals, but its
`provides` field must explicitly list them:

```json
"provides": ["path_a_crossbook", "asian_handicap", "totals"]
```

Without the explicit AH/totals entries, `report_contract` will fail with:
```
live direct manifest missing artifact capabilities: asian_handicap, totals
```

---

## 7. snapshot_resolver.py Window vs Report Window

`snapshot_resolver.py --window` accepts only these enum values:
`T-24h_confirm`, `T-45m_price_guard`, `T-48h_early_update`, `T-60m_lineup_final`,
`T-6h_preflight`, `T-72h_early`, `T-75m_team_sheet_checkpoint`,
`T-90m_lineup_probe`, `early_structural`, `manual_now`.

It does **NOT** accept `T-{N}d_early_structural` (e.g. `T-8d_early_structural`,
`T-9d_early_structural`). Those are report header / `report_contract.py` values only.

**Fix:** When hours_to_kickoff > 84 and the report uses `T-{N}d_early_structural`,
pass `--window early_structural` to `snapshot_resolver.py`. The resolver mapes
`early_structural` to the same reuse group.

```bash
# CORRECT — resolver call
python3 skills/odds-analysis/scripts/snapshot_resolver.py \
  --workspace /hermesdata/worldcup-2026-handicap \
  --window early_structural \
  --source all

# REPORT HEADER — separate concern, uses the N-day form
window: T-9d_early_structural
```

---

## 8. Multi-Snapshot Price Stability Check

When binding a cached report to a new direct request, verify the odds haven't
moved before reusing. If Pinnacle lines are identical between the original
snapshot and the freshest available snapshot, cache reuse is safe:

```python
# Compare Pinnacle prices across two multibook snapshots
# Only relevant when snapshot ages differ by >1h
# If identical → reuse_existing_report is safe
# If moved → consider local_snapshot_rebuild
```

This avoids stale-report binding when a material line move occurred between
snapshots, while still keeping the "don't refresh paid APIs on every chat
request" rule intact.

---

## 9. fixture_registry.py Argument Format

`fixture_registry.py` requires `--match-id`, not a bare positional argument:

```bash
# WRONG
python3 fixture_registry.py M009

# CORRECT
python3 fixture_registry.py --match-id M009
```

The script also supports `--football-data-id`, `--home`, `--away`, and `--list` for
cross-reference. Always use the named flag form.

---

## 10. snapshot_resolver Returns All Snapshots, Not Match-Specific

`snapshot_resolver.py` resolves snapshots for the **entire workspace**, not for a
specific match. It does not accept `--match-id`:

```bash
# WRONG
python3 snapshot_resolver.py --match-id M009 --window early_structural

# CORRECT — no match filter; filter by match in downstream scripts
python3 snapshot_resolver.py --workspace /hermesdata/worldcup-2026-handicap \
  --window early_structural --source all
```

The returned snapshots are workspace-wide. Downstream scripts or manual inspection
extract match-specific data from the snapshot content.

---

## 11. mechanism_audit Path A Count Cross-Check

`mechanism_audit.py` must be regenerated after any change to the cross_book_scan
artifact or manifest artifacts list. It cross-checks `quotes_scanned`,
`edge_count`, `noise_edge_count`, `actionable_count`, and `qualified_play_count`
against the crossbook artifact. If these don't match (e.g., mechanism_audit was
generated from a wrapper artifact that lacked market data), `report_contract`
will fail with mismatched counts.

**Fix:** After manifest changes, always re-run:
```bash
python3 skills/odds-analysis/scripts/mechanism_audit.py \
  --manifest <manifest.json> \
  --output <mechanism-audit.json>
```

---

## 12. rich_summary.py AH/Totals Cross-Match Contamination

`rich_summary.py` may display wrong AH and totals lines from a DIFFERENT match
when the manifest doesn't explicitly carry AH/totals line metadata. Observed
M010 (Netherlands vs Japan, AH -0.5, Totals 2.5) printing M009's lines
("GER -3.5 @N/A", "Over 4.25 @N/A").

This happens when `rich_summary.py` falls back to parsing the Markdown report
degradedly rather than reading structured crossbook artifact data. The AH and
totals sections of the rich summary are not part of the report_contract
validation scope, so the guard won't catch it.

**Detection:** Cross-check the rich summary's `③ 盘口快照` AH and Totals
lines against the report's Market Board. If they disagree, trust the Markdown
report.

**Mitigation:** Always run `rich_summary.py` with both `--manifest` and
`--report` flags. When displaying the rich summary to users, manually verify
the AH/totals line matches the report. If contaminated, override the AH/totals
section with the correct values from the report's Market Board.

---

## 13. direct_report_bind.py Can Corrupt Report Formatting

`direct_report_bind.py` rewrites the report file and prepends extra YAML
metadata (artifact_manifest_path, direct_request_path, direct_request_id)
before the `# WC26` title. In some cases it also adds line numbers (`1|`, `2|`)
to the report content, which breaks `report_guard.py` header parsing:

```
report mode must be live or simulation
artifact_contract_status must be pass
artifact_manifest_path is required
```

**Fix:** Do NOT use the report that `direct_report_bind.py` writes. Instead:
1. Manually update the direct request record JSON with `report_path`, `manifest_path`,
   `status: completed_cached`, `api_refresh_performed: false`
2. Keep the original clean report and manifest files
3. Run `report_guard.py` on the original report

## 14. read_file + write_file Corrupts Report with Line Numbers

`read_file()` returns content with `LINE|CONTENT` format (e.g., `1|# WC26...`).
Writing this back via `write_file()` embeds the line numbers as literal text in
the file. `report_guard.py` then fails to parse the header because line 1
starts with `1|#` instead of `#`.

**Fix:** Always use `write_file()` directly with clean content — never read
with `read_file()`, modify, and write back. If you need to edit the report,
use `patch()` with specific `old_string`/`new_string` targeting the exact text
to change.

## 15. Venue Discovery from Deep Research

Fixture registry may have `venue: null` for early fixtures (T-9d). Deep
Research via Exa/Jina can surface venue information from unofficial sources
(news articles, team previews). Example: M010 venue discovered as
"Arlington (AT&T Stadium)" via Squawka article, while fixture registry showed
null.

**Rule:** Venue from deep research must be labeled as unofficial:
```text
场地: Arlington (AT&T Stadium) — 非官方确认(Squawka来源,fixture registry仍为null)
```

Do not overwrite the fixture registry venue field in the manifest based on
deep research. The venue is a fact-lock field; only FIFA official sources or
football-data.org updates can change it. Deep Research venue is supplementary
context only.
