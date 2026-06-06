#!/usr/bin/env python3
"""Stub: delegates to co-located wc26_cron_payload.py with WC26_JOB set."""
import os, sys, subprocess
from pathlib import Path
worker = Path(__file__).parent / "wc26_cron_payload.py"
env = os.environ.copy()
env["WC26_JOB"] = "wc26-daily-reflect"
sys.exit(subprocess.call([sys.executable, str(worker)], env=env))
