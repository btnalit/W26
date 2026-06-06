# Building a Report When No Cache Exists

When a user requests analysis and no cached `reports/match/` or
`reports/artifacts/manifest-*.json` exists, do not invoke the full
`wc26_match_pipeline.py` compiler unless snapshot freshness warrants it.
Instead, follow this lightweight build path from existing multibook snapshots.

## Step 1: Find and validate the fixture

```bash
# Search fixture cache for team names
python3 -c "
import json
with open('snapshots/fixtures/football-data-wc-matches-latest.json') as f:
    data = json.load(f)
matches = data.get('matches', []) or data.get('data',{}).get('matches',[])
for i, m in enumerate(matches):
    home = (m.get('homeTeam') or {}).get('name', '') or ''
    away = (m.get('awayTeam') or {}).get('name', '') or ''
    print(f'M{i+1:03d} | id={m.get(\"id\")} | {home} vs {away} | {m.get(\"utcDate\",\"\")[:16]}')
"

# Validate
python3 fixture_registry.py --match-id M007
```

## Step 2: Create direct request record

```bash
python3 direct_request_record.py \
  --from-latest-session \
  --request-text "<exact user request>" \
  --match-id "M007" \
  --match-label "Haiti vs Scotland" \
  --status received --header-lines
```

## Step 3: Extract Pinnacle odds from the multibook snapshot

Use the latest multibook snapshot in `snapshots/odds/`:

```bash
python3 -c "
import json
with open('snapshots/odds/the-odds-api-multibook-20260605T143408Z.json') as f:
    data = json.load(f)
for m in data['data']:
    if m.get('home_team') == 'Haiti':
        for b in m.get('bookmakers', []):
            if b.get('key') == 'pinnacle':
                for mk in b.get('markets', []):
                    outcomes = {o.get('name'): o for o in mk.get('outcomes', [])}
                    k = mk.get('key')
                    prices = {n: o.get('price') for n, o in outcomes.items()}
                    points = {n: o.get('point') for n, o in outcomes.items() if o.get('point') is not None}
                    print(f'{k}: prices={prices} points={points}')
"
```

## Step 4: Run devig and cross_book_scan

```bash
# Devig H2H
python3 devig.py 6.79 4.39 1.51

# Cross-book scan (writes full artifact)
python3 cross_book_scan.py \
  --input-snapshot snapshots/odds/the-odds-api-multibook-20260605T143408Z.json \
  --output reports/artifacts/crossbook-M007-20260605T210000Z.json \
  --match-home "Haiti" \
  --match-away "Scotland"
```

## Step 5: Run consistency triangle (optional, may produce no output)

```bash
python3 consistency_triangle.py \
  --snapshot snapshots/odds/the-odds-api-multibook-20260605T143408Z.json \
  --match "Haiti vs Scotland"
# Empty output + exit 0 = no signal found (normal)
```

## Step 6: Read model artifact if available

```bash
cat reports/artifacts/model-M007-*.json
```

## Step 7: Compose direct report without manifest

When no manifest exists, compose the Telegram reply directly from:
1. The cross_book_scan artifact (Path A data)
2. devig output (no-vig probabilities)
3. model artifact (p_model, calibration status)
4. Deep Research web searches (Exa/Jina)

The reply must still include:
- `direct_request_id` and traceability
- Source quality grade
- Path A edge counts
- p_market / p_model / p_adj
- Deep Research section with `WC26_DEEP_RESEARCH_FINALIZER: completed`
- Final decision (PASS / WATCH / NO PLAY)

## Step 8: Bind direct request

```bash
python3 direct_report_bind.py \
  --direct-request-path direct_requests/.../direct-XXXXXXXX.json \
  --manifest reports/artifacts/manifest-...json \
  --report reports/match/...md \
  --cache-mode local_snapshot_rebuild \
  --source-snapshot-id the-odds-api-multibook-20260605T143408Z.json \
  --api-refresh-performed false
```

If no manifest was created (lean build), bind with `--status completed`
and `--cache-mode manual_from_snapshots`.

## Step 9: Write numeric artifact (lightweight pseudo-manifest)

The deep research contract checker needs a manifest-like file with
`generated_utc`.  Write a lightweight numeric artifact that consolidates
devig output, p_market, p_model, and p_adj for the match:

```bash
python3 -c "
import json, datetime
artifact = {
    'artifact_type': 'devig',
    'match_id': 'M010',
    'home': 'Netherlands', 'away': 'Japan',
    'generated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'source_snapshot_id': 'the-odds-api-multibook-20260605T175153Z.json',
    'snapshot_captured_utc': '2026-06-05T17:51:53Z',
    'markets': { ... },
    'p_market': { ... },
    'p_model': { ... },
    'p_adj': { ... }
}
with open('reports/artifacts/numeric-M010-20260606T122400Z.json', 'w') as f:
    json.dump(artifact, f, indent=2)
"
```

This artifact serves as `--manifest` for `deep_research_contract.py` and
`direct_report_bind.py` when no formal manifest exists.

## Step 10: Deep Research artifact + contract validation

```bash
# 1. Write the deep-research artifact (v1.2)
#    Use the shape from skills/odds-analysis/deep-research/SKILL.md
#    Best-guess pricing_freshness and recency_bucket on every source

# 2. Run contract checker — it computes correct pricing_freshness/recency_bucket
python3 skills/odds-analysis/scripts/deep_research_contract.py \
  --artifact reports/artifacts/deep-research-M010-20260606T123000Z.json \
  --manifest reports/artifacts/numeric-M010-20260606T122400Z.json \
  --json

# 3. If status=fail with computed values in normalized_sources:
#    Patch the artifact in-place with terminal + python3 -c
python3 -c "
import json
path = 'reports/artifacts/deep-research-M010-20260606T123000Z.json'
with open(path) as f:
    data = json.load(f)
# Fix specific sources by source_id
for s in data['sources']:
    if s['source_id'] == 'DR-C1':
        s['pricing_freshness'] = 'pre_snapshot'   # from contract output
        s['recency_bucket'] = 'stale_gt_72h'       # from contract output
with open(path, 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
"

# 4. Re-run contract checker until status=pass
```

### Pitfall: pricing_freshness date comparison

When the contract checker tells you `pricing_freshness=X does not match
computed Y`, it's almost always because you mentally flipped which date is
earlier.  Compare ISO timestamps numerically:

- `published_at_utc <= snapshot_at_utc` → `pre_snapshot`
- `published_at_utc > snapshot_at_utc` → `post_snapshot`

Example: May 15 (2026-05-15) < June 5 (2026-06-05) → `pre_snapshot`.
Do not reason in words like "May is after June" — always compare the
ISO strings lexicographically or with datetime objects.

## Step 11: Bind and send

```bash
# Uncached build: --cache-mode manual_from_snapshots, no --report (lean path)
python3 direct_report_bind.py \
  --direct-request-path direct_requests/.../direct-XXXXXXXX.json \
  --manifest reports/artifacts/numeric-M010-20260606T122400Z.json \
  --report reports/artifacts/numeric-M010-20260606T122400Z.json \
  --cache-mode manual_from_snapshots \
  --source-snapshot-id the-odds-api-multibook-20260605T175153Z.json \
  --api-refresh-performed false \
  --status completed
```

## Key differences from cached-reuse path

| Aspect | Cached Reuse | Uncached Build |
|--------|-------------|----------------|
| report.md | Exists, validated | Generated from scratch |
| manifest.json | Exists, validated | Numeric artifact as pseudo-manifest |
| report_contract | Run on manifest | Not run (no formal manifest) |
| report_guard | Run on report.md | Not run (no formal report) |
| rich_summary.py | Run from manifest+report | Cannot run (missing artifacts) |
| Deep Research | Reuse if fresh | Always run fresh, validate with contract |
The uncached build is a **fallback path**. Prefer the full compiler pipeline
(`wc26_match_pipeline.py`) when snapshot freshness justifies it and the
user explicitly asks for fresh data.
user explicitly asks for fresh data.
