#!/usr/bin/env python3
"""Assert WC26 cron registry matches the manifest's profile/name/entrypoint contract.

This is intentionally stricter than a name-or-script presence check: a WC26 job
registered in the default profile is a misdeployment even if the same script name
exists somewhere in Hermes cron state.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_PROFILE = "wc26-handicap-analyst"
DEFAULT_PROFILE_JOBS = Path(f"/root/.hermes/profiles/{DEFAULT_PROFILE}/cron/jobs.json")
DEFAULT_ROOT_JOBS = Path("/root/.hermes/cron/jobs.json")


def _jobs_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        jobs = payload.get("jobs", [])
    else:
        jobs = payload
    return [job for job in jobs if isinstance(job, dict)]


def load_jobs_json(path: Path, registry_profile: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for job in _jobs_from_payload(payload):
        item = dict(job)
        item["registry_profile"] = registry_profile
        # A job stored inside a profile cron registry belongs to that profile even
        # when the per-job field is null. In the root registry, profile=None means
        # default/root, not WC26.
        item["actual_profile"] = registry_profile
        out.append(item)
    return out


def load_actual_jobs(actual_file: Path | None, root_jobs: Path, wc26_jobs: Path, wc26_profile: str) -> list[dict[str, Any]]:
    if actual_file:
        payload = json.loads(actual_file.read_text(encoding="utf-8"))
        jobs = []
        for job in _jobs_from_payload(payload):
            item = dict(job)
            item.setdefault("registry_profile", item.get("actual_profile") or item.get("profile") or "default")
            item.setdefault("actual_profile", item.get("registry_profile") or item.get("profile") or "default")
            jobs.append(item)
        return jobs
    return load_jobs_json(root_jobs, "default") + load_jobs_json(wc26_jobs, wc26_profile)


def expected_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job in manifest.get("jobs", []):
        if not isinstance(job, dict) or not job.get("required", True):
            continue
        rows.append(
            {
                "name": str(job.get("name") or "").strip(),
                "profile": str(job.get("profile") or "").strip(),
                "entrypoint": str(job.get("script") or job.get("entrypoint") or "").strip(),
                "deliver": str(job.get("deliver") or "").strip(),
                "mode": str(job.get("mode") or "").strip(),
            }
        )
    return rows


def actual_rows(actual_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job in actual_jobs:
        name = str(job.get("name") or "").strip()
        if not name.startswith("wc26-"):
            continue
        rows.append(
            {
                "id": str(job.get("id") or job.get("job_id") or ""),
                "name": name,
                "profile": str(job.get("actual_profile") or job.get("profile") or "default"),
                "job_profile": job.get("profile"),
                "registry_profile": str(job.get("registry_profile") or ""),
                "entrypoint": str(job.get("script") or "").strip(),
                "deliver": str(job.get("deliver") or "").strip(),
                "mode": "no-agent" if bool(job.get("no_agent")) else "agent",
                "enabled": bool(job.get("enabled", True)),
            }
        )
    return rows


def compare_manifest_to_cron(manifest: dict[str, Any], actual: str | list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(actual, str):
        # Tests and deploy tooling should pass JSON. Text parsing cannot reliably
        # prove registry profile, so treat it as unsupported rather than green.
        try:
            actual_payload = json.loads(actual)
            jobs = _jobs_from_payload(actual_payload)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "exit_code": 2,
                "error": "actual cron data must be JSON with job profile metadata; text cron list cannot prove profile binding",
                "expected": expected_rows(manifest),
                "actual": [],
                "differences": [],
            }
    else:
        jobs = _jobs_from_payload(actual)

    expected = expected_rows(manifest)
    actual_table = actual_rows(jobs)
    differences: list[dict[str, Any]] = []

    for exp in expected:
        matches = [row for row in actual_table if row["name"] == exp["name"]]
        exact = [row for row in matches if row["profile"] == exp["profile"] and row["entrypoint"] == exp["entrypoint"]]
        if not exact:
            differences.append({"kind": "missing", "expected": exp, "actual_candidates": matches})
            continue
        wrong = [row for row in matches if row not in exact]
        for row in wrong:
            differences.append({"kind": "misplaced", "expected": exp, "actual": row})
        for row in exact:
            if exp["deliver"] and row["deliver"] != exp["deliver"]:
                differences.append({"kind": "deliver_mismatch", "expected": exp, "actual": row})
            if exp["mode"] and row["mode"] != exp["mode"]:
                differences.append({"kind": "mode_mismatch", "expected": exp, "actual": row})

    expected_names = {row["name"] for row in expected}
    for row in actual_table:
        if row["name"] in expected_names and row["profile"] not in {exp["profile"] for exp in expected if exp["name"] == row["name"]}:
            if not any(diff.get("actual") == row for diff in differences):
                differences.append({"kind": "misplaced", "expected": [exp for exp in expected if exp["name"] == row["name"]], "actual": row})

    return {
        "ok": not differences,
        "exit_code": 0 if not differences else 2,
        "expected": expected,
        "actual": actual_table,
        "differences": differences,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("cron-manifest.json"))
    parser.add_argument("--actual-file", type=Path, help="JSON test fixture containing cron jobs")
    parser.add_argument("--root-jobs", type=Path, default=DEFAULT_ROOT_JOBS)
    parser.add_argument("--wc26-jobs", type=Path, default=DEFAULT_PROFILE_JOBS)
    parser.add_argument("--wc26-profile", default=DEFAULT_PROFILE)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    actual_jobs = load_actual_jobs(args.actual_file, args.root_jobs, args.wc26_jobs, args.wc26_profile)
    result = compare_manifest_to_cron(manifest, actual_jobs)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["exit_code"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "exit_code": 2, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
