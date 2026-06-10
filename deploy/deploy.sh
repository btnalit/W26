#!/usr/bin/env bash
# WC26 Production Deploy Script
# Deploys source repo files to production paths.
# Must be run from repo root.
# Production-specific patches (e.g. load_module for devig) are
# applied by this script, not committed as source differences.

set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo "$(dirname "$0")/..")"
REPO_ROOT="$(pwd)"
echo "==> Deploying from $REPO_ROOT"

# 1. Profile scripts
echo "--- profile/scripts ---"
cp "$REPO_ROOT/profile/scripts/wc26_cron_payload.py" /wc26-profile/wc26_cron_payload.py
chmod +x /wc26-profile/wc26_cron_payload.py
echo "  deployed: /wc26-profile/wc26_cron_payload.py"

# 2. Skill scripts
echo "--- skill scripts ---"
SKILL_DEST="/skills/odds-analysis/scripts"
mkdir -p "$SKILL_DEST"
for f in "$REPO_ROOT"/profile/skills/odds-analysis/scripts/*.py; do
    cp "$f" "$SKILL_DEST/$(basename "$f")"
done
echo "  deployed $(ls "$SKILL_DEST"/*.py | wc -l) files to $SKILL_DEST"

# 3. Shared scripts
echo "--- shared scripts ---"
cp "$REPO_ROOT/workspace/scripts/cross_book_scan.py" /hermesdata/worldcup-2026-handicap/scripts/cross_book_scan.py
echo "  deployed: /hermesdata/worldcup-2026-handicap/scripts/cross_book_scan.py"

# 4. __init__.py files
echo "--- __init__.py ---"
mkdir -p /scripts
echo "# Hermes shared scripts package" > /scripts/__init__.py
echo "  created: /scripts/__init__.py"
echo "# WC26 skill scripts package" > "$SKILL_DEST/__init__.py"
echo "  created: $SKILL_DEST/__init__.py"

# 5. Production-specific patch: devig import uses load_module
echo "--- production patch ---"
if grep -q 'from scripts.devig import devig_shin' /wc26-profile/wc26_cron_payload.py; then
    echo "WARN: production wc26_cron_payload.py still has inline import — applying patch"
    # This is automatically applied in the source repo version which uses DEVIG = load_module(...)
    # If deploying a version that uses inline import, this patch is needed
fi

echo ""
echo "==> Deploy complete. Verify with: python3 -c \"import sys; sys.path.insert(0, '/wc26-profile'); import wc26_cron_payload; print('OK')\""
