# WC26 Deployment

This directory tracks the mapping between source repository files and their
production deployment paths. It ensures a `git clone` can reproduce the full
production environment.

## Structure

```
deploy/
  manifest.yaml   — authoritative file-by-file mapping
  deploy.sh       — runnable script (bash deploy/deploy.sh from repo root)
  cron-manifest.json — required Hermes scheduler jobs
  check_cron_manifest.py — deploy-time scheduler assertion
```

## Deployment flow

1. `git pull` latest source
2. `bash deploy/deploy.sh` — copies files to production paths
3. Verify imports: `python3 -c "import sys; sys.path.insert(0, '/wc26-profile'); import wc26_cron_payload"`

## Production paths used

| Path | Content | Source |
|------|---------|--------|
| `/wc26-profile/` | Cron payload + report contract | `profile/scripts/` |
| `/skills/odds-analysis/scripts/` | Skill scripts (21 files) | `profile/skills/odds-analysis/scripts/` |
| `/scripts/` | Shared scripts + `__init__.py` | Mixed (workspace/scripts/ + deploy) |
| `/hermesdata/worldcup-2026-handicap/scripts/` | Workspace scripts | `workspace/scripts/` |

## Production-only patches

These changes are applied by deploy.sh but are NOT in source (they're
deployment-context differences, not code changes):

- `/wc26-profile/wc26_cron_payload.py` uses `load_module()` for `devig`
  (source repo already uses this pattern — only the older production
  copy needed the change)
- `__init__.py` files in `/scripts/` and `/skills/odds-analysis/scripts/`
