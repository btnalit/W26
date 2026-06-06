#!/usr/bin/env python3
"""Bridge between model artifacts and devig-ah artifacts (Phase 3 pre-work).

Creates/updates devig-ah artifacts with margin_distribution_ref pointing
to the corresponding model artifact. This is a "架桥不拆桥" step:
the full Asian handicap leg-settlement EV is Phase 3, but the reference
link is established here so the report writer knows which model artifact
to use for margin distribution lookup.
"""

import glob
import json
import os
import sys

WORKSPACE = os.environ.get("WORKSPACE", "/hermesdata/worldcup-2026-handicap")
ARTIFACT_DIR = os.path.join(WORKSPACE, "reports", "artifacts")


def match_id_from_filename(fname: str) -> str | None:
    """Extract match_id from model-M001-*.json or devig-ah-m001-*.json."""
    basename = os.path.basename(fname).lower()
    # Pattern: model-m001-XXX.json or devig-ah-m001-XXX.json
    # Use regex to find m\d{3}
    import re
    m = re.search(r'[_-]m(\d{3})[^a-z]', basename)
    if m:
        return f"M{m.group(1)}"
    # Also try devig-ah-m001.json (no timestamp)
    m = re.search(r'[_-]m(\d{3})\.json$', basename)
    if m:
        return f"M{m.group(1)}"
    return None


def main():
    # Find all model artifacts
    model_files = sorted(glob.glob(os.path.join(ARTIFACT_DIR, "model-M*-*.json")))
    # Find all devig-ah artifacts
    ah_files = sorted(glob.glob(os.path.join(ARTIFACT_DIR, "devig-ah-m*-*.json")))
    ah_files += sorted(glob.glob(os.path.join(ARTIFACT_DIR, "devig-ah-m*.json")))

    if not model_files:
        print("[devig-bridge] No model artifacts found. Run model_runner.py first.")
        sys.exit(0)

    # Build match_id → latest model artifact mapping
    model_map = {}
    for f in model_files:
        mid = match_id_from_filename(f)
        if mid:
            model_map[mid] = f  # last due to sort

    print(f"[devig-bridge] Found {len(model_files)} model artifacts, {len(model_map)} unique match IDs")
    print(f"[devig-bridge] Found {len(ah_files)} devig-ah artifacts")

    linked = 0
    for ah_file in ah_files:
        ah_mid = match_id_from_filename(ah_file)
        if not ah_mid or ah_mid not in model_map:
            print(f"  skip {os.path.basename(ah_file)} (no matching model for {ah_mid})")
            continue

        model_path = model_map[ah_mid]
        model_basename = os.path.basename(model_path)

        with open(ah_file) as f:
            ah_data = json.load(f)

        # Add or update the margin_distribution_ref
        ref = f"model:{ah_mid}:{model_basename.replace('model-', '').replace('.json', '')}"
        ah_data["margin_distribution_ref"] = ref

        with open(ah_file, "w") as f:
            json.dump(ah_data, f, indent=2)

        linked += 1
        print(f"  linked {os.path.basename(ah_file)} → {model_basename}")

    print(f"[devig-bridge] Done. Linked {linked} devig-ah artifacts to model artifacts.")
    return linked


if __name__ == "__main__":
    main()
