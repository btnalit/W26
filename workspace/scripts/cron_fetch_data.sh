#!/bin/bash
# Wrapper for fetch_international_data.py — uses the workspace venv
# Pre-tournament: no paid API, GitHub raw only
cd /hermesdata/worldcup-2026-handicap || exit 1
export WORKSPACE="/hermesdata/worldcup-2026-handicap"
LOCKFILE="/tmp/wc26-fetch-data.lock"

exec 200>"$LOCKFILE" || exit 1
flock -n 200 || { echo "[cron_fetch_data] Lock held — skipping."; exit 0; }

exec /hermesdata/worldcup-2026-handicap/.venv/bin/python3 \
  scripts/fetch_international_data.py
