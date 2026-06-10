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

echo "==> WC26 Deploy ${COMMIT_SHA} @ ${DEPLOY_TS}"
echo "    from ${REPO_ROOT}"

# ── 0. Backup previous deployment ──
BACKUP_DIR="/wc26-backup/${DEPLOY_TS}"
mkdir -p "$BACKUP_DIR"
echo "--- backup previous deployment to ${BACKUP_DIR} ---"
for src in /wc26-profile /skills/odds-analysis /scripts/snapshot_resolver.py /scripts/__init__.py; do
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
cp "$REPO_ROOT/profile/scripts/wc26_cron_payload.py" /wc26-profile/wc26_cron_payload.py
chmod +x /wc26-profile/wc26_cron_payload.py
echo "  deployed: /wc26-profile/wc26_cron_payload.py"

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

echo ""
echo "==> Deploy ${COMMIT_SHA} complete. Verify:"
echo "    python3 -c \"import sys; sys.path.insert(0, '/wc26-profile'); import wc26_cron_payload; print('OK')\""
echo "    Backup at: ${BACKUP_DIR}"
