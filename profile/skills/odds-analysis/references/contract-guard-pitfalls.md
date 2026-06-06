# report_contract.py / report_guard.py Pitfalls

Validation failures that cost the most iterations. Check these before calling either script.

## Devig artifact JSON requirements

### 1. devig_methods must be flat

`report_contract.py` checks: `{"shin","power","multiplicative"}.issubset(devig_methods)`

Nesting under a market-type key (e.g. `devig_methods.1x2.shin`) fails. Promote:

```json
{
  "devig_methods": {
    "multiplicative": {"Netherlands": 0.4861, "Draw": 0.2593, "Japan": 0.2546},
    "power": {"k_opt": 5.0, "probabilities": {...}},
    "shin": {"z": 0.0327, "probabilities": {...}},
    "1x2": {...},
    "asian_handicap_minus_0_5": {...},
    "totals_o_u_2_5": {...}
  }
}
```

### 2. Required non-math fields

| Field | Value | Check |
|---|---|---|
| `odds_unit_contract` | `"all odds are normalized decimal > 1.0"` | Must be present |
| `survives_all_methods` | `true` / `false` | Must be boolean |
| `provides` | `["no_vig", "asian_handicap", "totals", "cross_book", "consistency_triangle"]` | Substring-matched for capabilities |
| `script` | must contain `"devig.py"` | String contains check |

### 3. Capability detection (substring matching)

`_artifact_capabilities()` scans `provides` items + `artifact_type` + `path`:

| Token | Capability |
|---|---|
| `"no_vig"` or (`"devig"` + `"1x2"`) | `devig_1x2` |
| `"cross_book"` or `"crossbook"` | `path_a_crossbook` |
| `"asian_handicap"` or `" ah"` or `"-ah-"` | `asian_handicap` |
| `"totals"` or `"over_under"` or `"total_goals"` | `totals` |
| `"consistency_triangle"` or `"path_c"` | `path_c_consistency` |

Error when any missing: `"live direct manifest missing artifact capabilities: ..."`

## Manifest requirements

### 4. analysis_gates — all 7 required

```
devig_three_method, path_a_crossbook, asian_handicap, totals,
path_b_model_diagnostic, path_c_consistency, source_freshness
```

Status must be: `pass|ok|complete|no_signal|diagnostic`

### 5. adjustment_ledger_id

- PASS status → `""` (empty string) is valid
- `lean` / `qualified_play` → must be non-empty

### 6. Manifest artifact entry

```json
{
  "artifact_id": "devig-M010-...",
  "artifact_type": "devig",
  "script": "devig.py",
  "path": "/absolute/path/to/devig-artifact.json",
  "provides": ["no_vig", "asian_handicap", "totals", "cross_book", "consistency_triangle"]
}
```

## Artifact capability collision (M010 pitfall)

### 7. Never let one artifact's `provides` overlap another's

When the manifest has SEPARATE artifacts for devig, crossbook, consistency, and
mechanism_audit, each artifact's `provides` list must claim ONLY the capabilities
it actually delivers. Overlap causes `report_contract.py` to pick the wrong
artifact and reject it for the wrong type.

**WRONG** (causes `path_a_crossbook artifact X must be a cross_book_scan artifact, not devig`):

```json
// devig artifact — DO NOT include cross_book or consistency_triangle in provides
{"provides": ["no_vig", "asian_handicap", "totals", "cross_book", "consistency_triangle"]}
```

**RIGHT** — strict one-capability-per-artifact:

| Artifact | `provides` |
|---|---|
| devig | `["no_vig", "asian_handicap", "totals"]` |
| cross_book_scan | `["path_a_crossbook"]` |
| consistency_triangle | `["path_c_consistency"]` |
| mechanism_audit | `["mechanism_audit"]` |

The `_artifact_capabilities()` matcher is substring-based: `"cross_book"` in any
`provides` string triggers `path_a_crossbook` capability. A single artifact
claiming two capabilities that map to different required gates will fail
contract.

### 8. Duplicate header blocks — patch with enough context

The report template has header metadata AND section 9A restating `artifact_manifest_path`,
`artifact_contract_status`, `report_guard_status`. Both blocks are checked by
`report_guard.py`. When patching, a short `old_string` may match both. Either:

- Use `replace_all=true` when the fix applies to both, OR
- Include enough surrounding context (e.g. `window: manual_now` or `## 1. One-Line View`) to target one occurrence.

### 9. mechanism_audit must be generated AFTER all other artifacts

`mechanism_audit.py` reads the manifest and cross-references Path A/B/C artifacts
to verify counts and detect blocking. If the manifest doesn't yet list the
crossbook/consistency artifacts, the audit will show them as BLOCKED or with
stale numbers. Always:

1. Generate devig, crossbook, consistency artifacts
2. Update manifest with all four artifacts
3. Generate mechanism_audit LAST

## Report guard requirements

### 10. report_guard_status must be "pass" everywhere

`report_guard.py` scans the entire file (header + body + section 9A). 
Every occurrence of `report_guard_status:` must say `pass`, not `pending`.

## Workflow for PASS reports

```
1. Write report with report_guard_status: pending everywhere
2. Run report_contract.py on manifest → must PASS
3. Change ALL report_guard_status to pass
4. Run report_guard.py → should PASS  
5. Run direct_summary.py → produces Telegram reply
```

### 13. numeric_artifact.py crossbook wrapper: do NOT use for manifest

`numeric_artifact.py crossbook` wraps `cross_book_scan.py` output inside a
`numeric_artifact` JSON envelope. The `crossbook_payload()` function checks
`scan_result.get("status") == "ok"`, but `cross_book_scan.py` output does NOT
have a top-level `status` — it has per-market statuses (`markets.h2h.status`).
This causes the wrapper to dump the entire scan into `scan_error` and lose all
market data (quotes, fair_probs, edges, summary).

**WRONG** — wrapper artifact in manifest points to empty shell:
```json
{
  "artifact_id": "crossbook:643542ae0e3c3691",
  "path": "/.../crossbook-643542ae0e3c3691.json",
  "provides": ["path_a_crossbook"]
}
```
The file at that path has `scan_error` instead of `markets`.

**RIGHT** — point manifest directly at the original `cross_book_scan.py` output:
```json
{
  "artifact_id": "crossbook:M010:20260605T152040Z",
  "path": "/.../crossbook-M010-20260605T143408Z.json",
  "provides": ["path_a_crossbook", "asian_handicap", "totals"]
}
```

This is also why `mechanism_audit.py` would report 0 quotes scanned and null
edge counts — it reads the artifact file on disk, and the wrapper has no
market data.

### 14. Crossbook artifact MUST declare asian_handicap + totals in provides

The `DIRECT_REQUIRED_ARTIFACT_CAPABILITIES` set includes `asian_handicap` and
`totals`. The `_artifact_capabilities()` function uses substring matching on
`provides`, `artifact_type`, `script`, and `path` — it does NOT inspect the
`markets` dict inside the payload. Even though `cross_book_scan.py` scans the
spreads and totals markets, the manifest artifact entry must explicitly list
`asian_handicap` and `totals` in its `provides`:

```json
"provides": ["path_a_crossbook", "asian_handicap", "totals"]
```

Without this, `report_contract.py` errors:
```
"live direct manifest missing artifact capabilities: asian_handicap, totals"
```

### 15. Partial report requires non-empty skipped_sections

When `report_completeness: partial`, `report_contract.py` requires:
- `skipped_sections` must be a non-empty list
- Each entry must have `gate`, `reason`, and `impact` fields
- `final_status` must be `watch` (not `pass`/`lean`/`qualified_play`)
- Any skipped gate in `analysis_gates` must have a corresponding `skipped_sections` entry

### 16. match_id must be local_ordinal_id (M010), NOT football_data_id (537357)

`fixture_registry.py`'s `validate_identity()` reads `match_id` from the manifest
and looks it up in `registry["by_local_id"]`, which only contains M-numbers
("M001", "M010", etc.). The `football_data_id` (537357) is a SEPARATE field
looked up in `registry["by_football_data_id"]`.

**WRONG** — causes `"match_id 537357 not found in fixture registry"`:
```json
{"match_id": "537357", "football_data_id": "537357", "canonical_id": "537357"}
```

**RIGHT** — match_id is the M-number, football_data_id is the integer, canonical_id has "fd:" prefix:
```json
{"match_id": "M010", "football_data_id": "537357", "canonical_id": "fd:537357"}
```

The `canonical_id` is the same `football_data_id` with `"fd:"` prefix. Do not
confuse `match_id` (M-number), `football_data_id` (integer), and `canonical_id`
(fd:-prefixed). Each serves a different lookup in `fixture_registry.validate_identity()`.

### 17. role_engine: valid decision enums and artifact_sources object format

**Valid decisions (role_engine.py line 21):**
`CONFIRMED`, `REFUTED`, `DIAGNOSTIC_ONLY`, `BLOCKED`, `SUSPECT`

`CONFIRMED_NOISE` is NOT valid — use `REFUTED` instead.

**artifact_sources must be objects, not strings (report_contract.py line 810-812):**

```json
// WRONG — plain strings
"artifact_sources": ["crossbook-ned-jpn-20260606"]

// RIGHT — objects with artifact_id, path, capability
"artifact_sources": [
  {
    "artifact_id": "crossbook-ned-jpn-20260606",
    "path": "/path/to/crossbook.json",
    "capability": "path_a_crossbook"
  }
]
```

Every role conclusion must have non-empty `artifact_sources`. The AI lag role
that genuinely has no source must still include a sentinel object:
```json
"artifact_sources": [{"artifact_id": "none", "path": "", "capability": "path_b_model_diagnostic", "note": "not available"}]
```

### 18. Partial report gate statuses: use the DIRECT_SKIPPED_GATE_STATUSES enum

When `report_completeness: partial`, analysis gates being skipped must use one of:
- `skipped_missing_source` — source data genuinely unavailable (e.g., Pinnacle no H2H)
- `skipped_not_applicable` — gate not applicable at this window (e.g., Path B at T-9d)
- `skipped_partial` — available but incomplete

Bare `"skipped"` or `"not_available"` are NOT valid and will cause:
```
analysis_gates.{gate} has non-pass status: skipped
```

Each skipped gate must have a corresponding `skipped_sections` entry with `gate`,
`reason`, and `impact` fields.

### 19. Partial report final_status must be "watch"

`report_contract.py` line 856-857:
```python
if is_partial and final_status != "watch":
    errors.append("partial direct report must use final_status=watch")
```

Even a clear PASS / NO PLAY report becomes `"watch"` when it's partial. The
`report_completeness` downgrade is the honest signal — the numbers and analysis
are still valid, but the contract requires acknowledging the missing gates.

## Error → fix mapping (from multiple sessions)

| Error | Root cause | Fix |
|---|---|---|
| `mode must be live or simulation` | Missing mode field in manifest | Add `"mode": "live"` |
| `analysis_gates missing source_freshness` | Only 6 of 7 gates present | Add `"source_freshness": "pass"` |
| `missing artifact capabilities: asian_handicap, devig_1x2, ...` | `provides` list missing tokens | Add all 5 capability tokens to provides |
| `missing shin/power/multiplicative devig_methods` | Methods nested under `"1x2"` sub-key | Promote to top-level keys |
| `missing odds_unit_contract` | Devig artifact missing contract field | Add `"odds_unit_contract": "..."` |
| `report_guard_status must be pass` | Section 9A still says "pending" | Change all occurrences to "pass" |

### 11. Cache reuse: bind BEFORE report_guard

When reusing an existing guarded report for a new Telegram request, the order matters:

1. `direct_request_record.py` → creates new request (status: `received`)
2. `direct_report_bind.py` → binds report/manifest to the new request (status: `completed_cached`)
3. `report_contract.py` / `report_guard.py` → validate

Running `report_guard.py` BEFORE binding produces:

```
"direct request record status must be completed or completed_cached before relay"
"direct request record missing report_path"
"direct request record missing manifest_path"
```

The fix is always: bind first, then guard. This applies to every cache-reuse flow, not just M010.

### 20. Report header YAML values must NOT be quoted

`report_guard.py` parses the markdown YAML frontmatter header. Quoted values
like `canonical_id: "fd:537357"` or `direct_request_path: "/path/to/file.json"`
are treated as **including the quote characters** in the value. This causes:

```
"artifact manifest does not exist: /hermesdata/.../\"/hermesdata/.../manifest.json\""
"direct request record does not exist: /hermesdata/.../\"/hermesdata/.../request.json\""
```

**WRONG:**
```yaml
cutoff_utc: "2026-06-06T14:47:00Z"
direct_request_id: "direct:4c6b557be32bb2bd"
direct_request_path: "/hermesdata/worldcup-2026-handicap/direct_requests/2026-06-06/direct-4c6b557be32bb2bd.json"
canonical_id: "fd:537357"
football_data_id: "537357"
artifact_manifest_path: "/hermesdata/worldcup-2026-handicap/reports/artifacts/manifest-M010.json"
entry_time_utc: "2026-06-06T14:47:00Z"
```

**RIGHT — all values unquoted:**
```yaml
cutoff_utc: 2026-06-06T14:47:00Z
direct_request_id: direct:4c6b557be32bb2bd
direct_request_path: /hermesdata/worldcup-2026-handicap/direct_requests/2026-06-06/direct-4c6b557be32bb2bd.json
canonical_id: fd:537357
football_data_id: 537357
artifact_manifest_path: /hermesdata/worldcup-2026-handicap/reports/artifacts/manifest-M010.json
entry_time_utc: 2026-06-06T14:47:00Z
```

Note: `match_id: M010` and `source_quality: B` don't need quoting anyway.
`null` and booleans (`true`/`false`) also go unquoted.

### 21. completed_cached direct request requires cache_mode

When the direct request record status is `completed_cached` (reusing an existing
report without paid API refresh), the record MUST include a `cache_mode` field:

```json
{
  "status": "completed_cached",
  "cache_mode": "reuse_existing_report",
  "report_path": "/hermesdata/.../report.md",
  "manifest_path": "/hermesdata/.../manifest.json",
  "source_snapshot_id": "the-odds-api-multibook-20260605T175153Z.json",
  "api_refresh_performed": false
}
```

Without `cache_mode`, `report_guard.py` fails with:
```
"completed_cached direct request record requires cache_mode"
```

Valid values: `reuse_existing_report`, `local_snapshot_rebuild`.
