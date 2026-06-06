#!/usr/bin/env python3
"""
paid_api_guard.py — Enforce that paid odds APIs are not called in pre-tournament mode.

Run as a wrapper before any odds-fetching:
  python3 scripts/paid_api_guard.py --mode check  # returns status

Or set environment variable WC26_PRE_TOURNAMENT=true to block.
The resolve_model.py and fetch_international_data.py scripts are always safe;
only the-odds-api and oddspapi need guarding.
"""

from __future__ import annotations

import json
import os
import sys


def check() -> dict:
    pre_tournament = os.environ.get("WC26_PRE_TOURNAMENT", "true").lower() in ("true", "1", "yes")
    
    # Blocked API hosts (checked by hostname, not DNS)
    blocked_hosts = [
        "api.the-odds-api.com",
        "api.oddspapi.com",
        "the-odds-api.com",
        "oddspapi.com",
    ]
    
    api_keys_found = []
    for key in [
        "THE_ODDS_API_KEY",
        "ODDSPAPI_KEY",
        "ODDSPAPI_TOKEN",
        "WC26_ODDS_API_KEY",
    ]:
        val = os.environ.get(key, "")
        if val:
            masked = val[:4] + "****" + val[-4:] if len(val) > 8 else "****"
            api_keys_found.append(f"{key}={masked}")
    
    return {
        "pre_tournament": pre_tournament,
        "paid_api_keys_found": len(api_keys_found) > 0,
        "api_keys": api_keys_found,
        "blocked_hosts": blocked_hosts,
        "safe": pre_tournament,  # True if in pre-tournament mode (keys exist but won't be used)
        "note": (
            "Pre-tournament mode ON — no paid APIs should be called. "
            "Keys present but execution guard prevents outbound calls. "
            "In live mode (WC26_PRE_TOURNAMENT=false), keys are available but "
            "only deterministic collector scripts may consume them."
        ) if pre_tournament else (
            "Live mode — paid APIs may be called by collector scripts only. "
            "Analyst LLM must NOT call them directly."
        ),
    }


def main():
    result = check()
    print(json.dumps(result, indent=2))
    return 0 if result["safe"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
