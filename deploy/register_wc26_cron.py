#!/usr/bin/env python3
"""Idempotently register WC26 cron jobs in the WC26 profile and remove misbound copies.

The manifest is the contract. Required WC26 jobs must live in the
wc26-handicap-analyst profile cron registry. Any required WC26 job found in the
root/default registry is removed to avoid duplicate triggers and wrong-gateway
delivery.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT_PROFILE = "default"
WC26_PROFILE = "wc26-handicap-analyst"
WORKDIR = "/hermesdata/worldcup-2026-handicap"


def run(cmd: list[str], *, dry_run: bool = False) -> subprocess.CompletedProcess[str]:
    if dry_run:
        print("DRY-RUN", " ".join(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)


def load_jobs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    jobs = payload.get("jobs", payload) if isinstance(payload, dict) else payload
    return [job for job in jobs if isinstance(job, dict)]


def job_id(job: dict[str, Any]) -> str:
    return str(job.get("id") or job.get("job_id") or "")


def manifest_jobs(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [job for job in payload.get("jobs", []) if isinstance(job, dict) and job.get("required", True)]


def cron_base(profile: str | None) -> list[str]:
    cmd = ["hermes"]
    if profile and profile != ROOT_PROFILE:
        cmd += ["--profile", profile]
    return cmd + ["cron"]


def remove_job(profile: str | None, jid: str, *, dry_run: bool) -> None:
    if not jid:
        raise RuntimeError("cannot remove cron job without id")
    cp = run(cron_base(profile) + ["remove", jid], dry_run=dry_run)
    if cp.returncode != 0:
        raise RuntimeError(f"cron remove failed for {jid}: {cp.stdout}")


def create_or_update(profile: str, existing: dict[str, Any] | None, spec: dict[str, Any], *, dry_run: bool) -> str:
    name = spec["name"]
    schedule = spec["schedule"]
    script = spec["script"]
    deliver = spec.get("deliver", "local")
    prompt = spec.get("prompt", f"Run deterministic {name} payload.")
    workdir = spec.get("workdir", WORKDIR)
    mode = spec.get("mode", "no-agent")
    base = cron_base(profile)
    if existing:
        jid = job_id(existing)
        cmd = base + [
            "edit",
            jid,
            "--schedule",
            schedule,
            "--name",
            name,
            "--deliver",
            deliver,
            "--script",
            script,
            "--workdir",
            workdir,
        ]
        cmd.append("--no-agent" if mode == "no-agent" else "--agent")
        cp = run(cmd, dry_run=dry_run)
        if cp.returncode != 0:
            raise RuntimeError(f"cron edit failed for {name}/{jid}: {cp.stdout}")
        return "updated"
    cmd = base + [
        "create",
        schedule,
        prompt,
        "--name",
        name,
        "--deliver",
        deliver,
        "--script",
        script,
        "--workdir",
        workdir,
    ]
    if mode == "no-agent":
        cmd.append("--no-agent")
    cp = run(cmd, dry_run=dry_run)
    if cp.returncode != 0:
        raise RuntimeError(f"cron create failed for {name}: {cp.stdout}")
    return "created"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("cron-manifest.json"))
    parser.add_argument("--root-jobs", type=Path, default=Path("/root/.hermes/cron/jobs.json"))
    parser.add_argument("--wc26-jobs", type=Path, default=Path(f"/root/.hermes/profiles/{WC26_PROFILE}/cron/jobs.json"))
    parser.add_argument("--profile", default=WC26_PROFILE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    specs = manifest_jobs(args.manifest)
    required_names = {spec["name"] for spec in specs}
    result: dict[str, Any] = {"profile": args.profile, "updated": [], "created": [], "removed_misplaced": [], "removed_duplicates": []}

    # Remove required WC26 jobs from the root/default registry. They may have a
    # per-job profile field, but their registry/profile is still default, so they
    # are not owned by the WC26 independent gateway.
    for job in load_jobs(args.root_jobs):
        name = str(job.get("name") or "")
        if name in required_names:
            remove_job(ROOT_PROFILE, job_id(job), dry_run=args.dry_run)
            result["removed_misplaced"].append({"profile": ROOT_PROFILE, "name": name, "id": job_id(job), "script": job.get("script")})

    # Refresh after removals so create/update sees current WC26 registry state.
    wc26_by_name: dict[str, list[dict[str, Any]]] = {}
    for job in load_jobs(args.wc26_jobs):
        wc26_by_name.setdefault(str(job.get("name") or ""), []).append(job)

    for spec in specs:
        name = spec["name"]
        matches = wc26_by_name.get(name, [])
        keep = matches[0] if matches else None
        action = create_or_update(args.profile, keep, spec, dry_run=args.dry_run)
        result[action].append(name)
        for dup in matches[1:]:
            remove_job(args.profile, job_id(dup), dry_run=args.dry_run)
            result["removed_duplicates"].append({"profile": args.profile, "name": name, "id": job_id(dup), "script": dup.get("script")})

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
