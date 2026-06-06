#!/usr/bin/env python3
"""Cron wrapper for the WC26 read-only opportunity watcher."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROFILE_ROOT = Path(__file__).resolve().parent.parent
WATCHER = PROFILE_ROOT / "skills" / "odds-analysis" / "scripts" / "opportunity_watch.py"
WORKSPACE = os.environ.get("WC26_WORKSPACE", "/hermesdata/worldcup-2026-handicap")


def main() -> int:
    python_bin = os.environ.get("WC26_PYTHON") or sys.executable
    args = [python_bin, str(WATCHER), "--workspace", WORKSPACE]
    return subprocess.call(args, env=os.environ.copy())


if __name__ == "__main__":
    raise SystemExit(main())
