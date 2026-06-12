#!/usr/bin/env bash
# WC26 Production Deploy Script
# Deploys source repo files to production paths.
# Must be run from repo root.
#
# set -euo pipefail — fail fast on any error, undefined var, or pipe failure
set -euo pipefail

REPO_ROOT="$(cd "$(git rev-parse --show-toplevel 2>/dev/null)" && pwd)"
COMMIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")"
DEPLOY_TS="$(date -u +%Y%m%dT%H%M%SZ)"

# ── 0. Guard: working tree must be clean — no uncommitted changes allowed ──
if ! git diff --quiet HEAD 2>/dev/null; then
    echo "FATAL: working tree is dirty. Commit or stash changes before deploying."
    echo "       dirty files:"
    git diff --name-only HEAD | sed 's/^/         /'
    exit 1
fi
DIRTY_SUFFIX=""
echo "  working tree clean — deploying ${COMMIT_SHA}"

echo "==> WC26 Deploy ${COMMIT_SHA} @ ${DEPLOY_TS}"
echo "    from ${REPO_ROOT}"

# ── 0. Backup previous deployment ──
BACKUP_DIR="/wc26-backup/${DEPLOY_TS}"
mkdir -p "$BACKUP_DIR"
echo "--- backup previous deployment to ${BACKUP_DIR} ---"
for src in /wc26-profile /skills/odds-analysis /scripts/snapshot_resolver.py /scripts/__init__.py /hermesdata/worldcup-2026-handicap/snapshots/fixtures/venue-overrides.json; do
    if [ -e "$src" ]; then
        dst="${BACKUP_DIR}/$(echo "$src" | sed 's|^/||' | tr '/' '_')"
        cp -r "$src" "$dst" 2>/dev/null || true
    fi
done
# Backup cross_book_scan separately (different path)
if [ -f /hermesdata/worldcup-2026-handicap/scripts/cross_book_scan.py ]; then
    cp /hermesdata/worldcup-2026-handicap/scripts/cross_book_scan.py \
       "${BACKUP_DIR}/hermesdata_worldcup-2026-handicap_scripts_cross_book_scan.py"
fi
echo "  backed up to ${BACKUP_DIR}"

# ── 1. Profile scripts ──
echo "--- profile/scripts ---"
mkdir -p /wc26-profile
for f in "$REPO_ROOT"/profile/scripts/wc26*.py; do
    cp "$f" "/wc26-profile/$(basename "$f")"
    chmod +x "/wc26-profile/$(basename "$f")"
done
echo "  deployed $(ls /wc26-profile/wc26*.py | wc -l) wc26 profile scripts"

# ── 2. Skill scripts ──
echo "--- skill scripts ---"
SKILL_DEST="/skills/odds-analysis/scripts"
mkdir -p "$SKILL_DEST"
for f in "$REPO_ROOT"/profile/skills/odds-analysis/scripts/*.py; do
    cp "$f" "$SKILL_DEST/$(basename "$f")"
done
echo "  deployed $(ls "$SKILL_DEST"/*.py | wc -l) files to ${SKILL_DEST}"

# ── 3. Shared scripts (canonical path only) ──
echo "--- shared scripts ---"
# cross_book_scan: canonical version from skill scripts
# (workspace/scripts/cross_book_scan.py is a shim that imports the canonical)
cp "$SKILL_DEST/cross_book_scan.py" /hermesdata/worldcup-2026-handicap/scripts/cross_book_scan.py
echo "  deployed: /hermesdata/worldcup-2026-handicap/scripts/cross_book_scan.py (from canonical)"

# ── 4. __init__.py files ──
echo "--- __init__.py ---"
mkdir -p /scripts
echo "# Hermes shared scripts package" > /scripts/__init__.py
echo "  created: /scripts/__init__.py"
echo "# WC26 skill scripts package" > "$SKILL_DEST/__init__.py"
echo "  created: ${SKILL_DEST}/__init__.py"

# ── 5. Write DEPLOYED_SHA for audit trail ──
echo "--- audit trail ---"
DEPLOY_LOG="/wc26-backup/DEPLOYED_SHA"
{
    echo "deployed_at_utc: ${DEPLOY_TS}"
    echo "commit_sha: ${COMMIT_SHA}"
    echo "repo_root: ${REPO_ROOT}"
} > "$DEPLOY_LOG"
echo "  wrote: ${DEPLOY_LOG}"

# ── 6. Scheduler manifest assertion ──
echo "--- cron manifest assertion ---"
python3 "$REPO_ROOT/deploy/check_cron_manifest.py" --manifest "$REPO_ROOT/deploy/cron-manifest.json"
echo "  cron registry contains required WC26 jobs"

echo ""
echo "==> Deploy ${COMMIT_SHA} complete. Verify:"
echo "    python3 -c \"import sys; sys.path.insert(0, '/wc26-profile'); import wc26_cron_payload; print('OK')\""
echo "    Backup at: ${BACKUP_DIR}"
