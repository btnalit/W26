#!/bin/bash
# Wrapper for fixture-refresh cron — polls football-data API
# Uses Python (curl has SSL issues on this host)
cd /hermesdata/worldcup-2026-handicap || exit 1
export WORKSPACE="/hermesdata/worldcup-2026-handicap"
exec /usr/bin/python3 \
  scripts/cron_fetch_fixtures.py
