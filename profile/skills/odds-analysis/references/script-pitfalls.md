# Script CLI Pitfalls

Pitfalls gathered from live direct-report sessions. These are footguns that waste
cycles and produce confusing errors.

## fixture_registry.py: three ID types — do not confuse

The fixture registry tracks three distinct identifiers for each match:

| Field | Example | Lookup Dict | Usage |
|---|---|---|---|
| `local_ordinal_id` / `match_id` | `M010` | `registry["by_local_id"]` | `--match-id M010` |
| `football_data_id` | `537357` (int) | `registry["by_football_data_id"]` | `--football-data-id 537357` |
| `canonical_id` | `fd:537357` | (computed) | Manifest/report header |

`validate_identity()` looks up `match_id` (from manifest) in `by_local_id`
and `football_data_id` in `by_football_data_id`. Passing `537357` as `match_id`
fails because `by_local_id` only contains M-numbers.

**Manifest must carry all three correctly:**
```json
{
  "match_id": "M010",
  "football_data_id": "537357",
  "canonical_id": "fd:537357"
}
```

## fixture_registry.py: only accepts M-numbers

`fixture_registry.py --match-id` expects a local ordinal like `M001` or `M010`.
It does **not** accept raw `football_data_id` values like `537357`. It also does
**not** accept `--search`, `--home`, or `--away` as standalone filters — those
are match-identity validation flags that must be paired with `--match-id`.

**Wrong:**
```bash
python3 fixture_registry.py --match-id 537357
# ValueError: local match id not found in fixture cache: 537357

python3 fixture_registry.py --search "Haiti" "Scotland"
# error: unrecognized arguments: --search Haiti Scotland
```

**Right — find a match by name:**
```bash
# List all fixtures and grep visually or use --list + jq
python3 fixture_registry.py --list | python3 -c "
import json,sys
data=json.load(sys.stdin)
for m in data:
    if 'haiti' in m['home_norm'] and 'scotland' in m['away_norm']:
        print(m['local_ordinal_id'], m['home'], 'vs', m['away'])
"
```

**Right — validate a known M-number:**
```bash
python3 fixture_registry.py --match-id M007
# Returns the full fixture record as JSON
```

## devig.py: positional odds, not named arguments

`devig.py` takes **positional decimal odds**. Do not use `--odds` or
`--odds-format` for the main input.

**Wrong:**
```bash
python3 devig.py --odds "6.79,4.39,1.51" --method multiplicative
# Error: argument --odds-format: invalid choice: '6.79,4.39,1.51'
```

**Right:**
```bash
python3 devig.py 6.79 4.39 1.51
# Returns JSON with no_vig_probabilities and overround
```

AH two-outcome devig works the same way:
```bash
python3 devig.py 2.02 1.88
```

## consistency_triangle.py: match filter uses --match, not --match-home/--match-away

Unlike `cross_book_scan.py` which takes `--match-home` and `--match-away`
separately, `consistency_triangle.py` takes a single `--match` flag with the
full match label as it appears in the snapshot:

**Wrong:**
```bash
python3 consistency_triangle.py --snapshot snapshot.json \
  --match-home "Netherlands" --match-away "Japan"
# error: unrecognized arguments: --match-home Netherlands --match-away Japan
```

**Right:**
```bash
python3 consistency_triangle.py --snapshot snapshot.json \
  --match "Netherlands vs Japan" --full
```

When no signal is found, the script prints nothing and exits 0. With `--full`,
it prints JSON for all scanned matches including those with no signal.

## consistency_triangle.py: no_signal requires a manual artifact JSON

When `consistency_triangle.py` returns empty output (no signal detected),
`mechanism_audit.py` will mark Path C as `BLOCKED` unless a consistency
artifact JSON exists in the manifest's artifacts list.

**After running consistency_triangle.py with no output, create a minimal artifact:**
```json
{
  "artifact_id": "consistency-triangle-M010-early_structural",
  "artifact_type": "consistency_triangle",
  "script": "consistency_triangle.py",
  "provides": ["path_c_consistency"],
  "snapshot_id": "the-odds-api-multibook-20260605T175153Z.json",
  "snapshot_path": "/hermesdata/.../snapshot.json",
  "match": "Netherlands vs Japan",
  "signal_detected": false,
  "status": "no_signal",
  "note": "consistency_triangle.py returned empty output — no anomalous patterns detected"
}
```

Add this artifact to the manifest's `artifacts` list, then re-run `mechanism_audit.py`.

## devig.py: shin/power values must come from cross_book_scan.py, not manual implementation

Manual shin devig implementations easily hit z-boundaries (z=0.1) and produce
incorrect no-vig probabilities. The `cross_book_scan.py` output is the canonical
source for all three devig methods.

**WRONG — manual shin produces wrong probs (z hits 0.1 bound):**
```
H2H (manual shin): NED=0.4672 Draw=0.2682 JPN=0.2646  # WRONG
AH (manual shin):  JPN+0.5=0.5105 NED-0.5=0.4895      # ~close but off
```

**RIGHT — use cross_book_scan.py fair_probs:**
```
H2H (crossbook shin): NED=0.4897 Draw=0.2573 JPN=0.2531
AH (crossbook shin):  JPN+0.5=0.5118 NED-0.5=0.4882
Totals (crossbook shin): Over=0.4908 Under=0.5092
```

**Build order to ensure correct values:**
1. Run `cross_book_scan.py` first → get `fair_probs.shin/power/multiplicative`
2. Build the devig artifact using those values (not manual devig)
3. `devig.py` can still be used for multiplicative baseline + overround checks
4. But the artifact's shin and power sections must match cross_book_scan output

## snapshot_resolver.py: no --match-id or --match-label flag

`snapshot_resolver.py` resolves snapshots by `--window` and `--source`, not by
match. Passing `--match-id`, `--match-label`, or any match-filtering flag is
rejected as an unrecognized argument.

**Wrong:**
```bash
python3 snapshot_resolver.py --workspace /hermesdata/worldcup-2026-handicap \
  --match-id M010 --window T-72h_early --source all
# error: unrecognized arguments: --match-id M010

python3 snapshot_resolver.py --workspace /hermesdata/worldcup-2026-handicap \
  --window manual_now --source all --match-label 'Haiti Scotland'
# error: unrecognized arguments: --match-label Haiti Scotland
```

**Right:**
```bash
python3 snapshot_resolver.py --workspace /hermesdata/worldcup-2026-handicap \
  --window manual_now --source all
```

It returns all available snapshots, not match-filtered. Check the returned
`cache_hit` / `must_refresh` per source to decide whether to reuse or refresh.

## snapshot_resolver.py: T-{N}d_early_structural is NOT a valid window

The report and manifest use dynamic display names like `T-8d_early_structural`
or `T-5d_early_structural`, but `snapshot_resolver.py` only accepts the base
window name `early_structural`. Do not pass the dynamic form.

**Wrong:**
```bash
python3 snapshot_resolver.py --workspace /hermesdata/worldcup-2026-handicap \
  --window T-8d_early_structural --source all
# error: argument --window: invalid choice: 'T-8d_early_structural'
```

**Right:**
```bash
python3 snapshot_resolver.py --workspace /hermesdata/worldcup-2026-handicap \
  --window early_structural --source all
```

The same rule applies to all dynamic prefixes: `T-72h_early`, `T-48h_early_update`,
etc. are the actual window names; there is no `T-{N}h_` runtime prefix for the
resolver.

## cross_book_scan.py: match filter uses exact team names

The `--match-home` and `--match-away` flags require the **exact team name** as
it appears in the the-odds-api snapshot (e.g., "Haiti", not "haiti").

```bash
python3 cross_book_scan.py \
  --input-snapshot snapshots/odds/the-odds-api-multibook-20260605T143408Z.json \
  --output reports/artifacts/crossbook-M007-20260605T210000Z.json \
  --match-home "Haiti" \
  --match-away "Scotland"
```

The script writes the artifact to `--output` and also prints a summary to stdout.
