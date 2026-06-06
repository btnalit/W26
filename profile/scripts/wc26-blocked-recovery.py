#!/usr/bin/env python3
"""Cron wrapper for the WC26 blocked recovery sidecar."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROFILE_ROOT = Path(os.environ.get("HERMES_HOME", "/root/.hermes/profiles/wc26-handicap-analyst"))
RECOVERY = PROFILE_ROOT / "skills" / "odds-analysis" / "scripts" / "blocked_recovery.py"
WORKSPACE = Path(os.environ.get("WC26_WORKSPACE", "/hermesdata/worldcup-2026-handicap"))


def main() -> int:
    cmd = [sys.executable, str(RECOVERY), "--workspace", str(WORKSPACE)]
    completed = subprocess.run(cmd, cwd=str(WORKSPACE), text=True)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
