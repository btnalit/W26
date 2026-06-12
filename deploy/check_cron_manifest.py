#!/usr/bin/env python3
"""Assert the deployed Hermes cron registry contains required WC26 jobs."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


NAME_RE = re.compile(r"^\s*Name:\s*(.+?)\s*$")
SCRIPT_RE = re.compile(r"^\s*Script:\s*(.+?)\s*$")


def parse_cron_list(output: str) -> dict[str, set[str]]:
    names: set[str] = set()
    scripts: set[str] = set()
    for line in output.splitlines():
        name = NAME_RE.match(line)
        if name:
            names.add(name.group(1).strip())
            continue
        script = SCRIPT_RE.match(line)
        if script:
            scripts.add(script.group(1).strip())
    return {"names": names, "scripts": scripts}


def compare_manifest_to_cron(manifest: dict[str, Any], cron_output: str) -> dict[str, Any]:
    parsed = parse_cron_list(cron_output)
    missing: list[str] = []
    for job in manifest.get("jobs", []):
        if not isinstance(job, dict) or not job.get("required", True):
            continue
        name = str(job.get("name") or "").strip()
        script = str(job.get("script") or "").strip()
        if name not in parsed["names"] and script not in parsed["scripts"]:
            missing.append(name or script)
    return {
        "ok": not missing,
        "exit_code": 0 if not missing else 2,
        "missing_jobs": missing,
        "actual_names": sorted(parsed["names"]),
        "actual_scripts": sorted(parsed["scripts"]),
    }


def read_actual(args: argparse.Namespace) -> str:
    if args.actual_file:
        return Path(args.actual_file).read_text(encoding="utf-8")
    completed = subprocess.run(["hermes", "cron", "list"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    if completed.returncode != 0:
        raise RuntimeError(f"hermes cron list failed with {completed.returncode}: {completed.stdout[:400]}")
    return completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("cron-manifest.json"))
    parser.add_argument("--actual-file", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = compare_manifest_to_cron(manifest, read_actual(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["exit_code"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "exit_code": 2, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
