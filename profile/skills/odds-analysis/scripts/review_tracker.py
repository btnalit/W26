#!/usr/bin/env python3
"""
review_tracker.py — Post-match review completion tracking.

Tracks which grading cards have been formally reviewed (复盘).
Reads/writes `grading/reviews/completed.json` — a simple registry
mapping football_data_id -> review metadata.

v2: Adds FINISHED-but-uncarded detection. Criterion is fixture status
    ("FINISHED" in fixture_registry), NOT manifest existence. This
    prevents future/unscheduled matches with pass_incomplete manifests
    from polluting the pending list.

Usage:
  python3 review_tracker.py --list-pending
  python3 review_tracker.py --mark-reviewed --football-data-id 537357
  python3 review_tracker.py --status --football-data-id 537357
  python3 review_tracker.py --self-test     # poison test
"""
import json, os, sys, argparse, tempfile
from datetime import datetime, timezone

DEFAULT_WORKSPACE = "/hermesdata/worldcup-2026-handicap"
REVIEWS_FILE = "grading/reviews/completed.json"
FIXTURE_PATH = "snapshots/fixtures/football-data-wc-matches-latest.json"


# ---------------------------------------------------------------------------
# Registry I/O
# ---------------------------------------------------------------------------

def load_registry(workspace):
    path = os.path.join(workspace, REVIEWS_FILE)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"reviews": {}, "updated_at_utc": None}


def save_registry(workspace, registry):
    path = os.path.join(workspace, REVIEWS_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    registry["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w") as f:
        json.dump(registry, f, indent=2)
    return path


# ---------------------------------------------------------------------------
# Fixture loading (lightweight — no dependency on fixture_registry.py)
# ---------------------------------------------------------------------------

def load_fixtures(workspace):
    """Return list of fixture entries from football-data cache."""
    path = os.path.join(workspace, FIXTURE_PATH)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        payload = json.load(f)
    raw_matches = payload.get("matches", []) or payload.get("data", {}).get("matches", [])
    entries = []
    for i, m in enumerate(raw_matches, 1):
        home_team = (m.get("homeTeam") or {})
        away_team = (m.get("awayTeam") or {})
        home = (home_team.get("name") or "").strip()
        away = (away_team.get("name") or "").strip()
        if not home or not away or home == "TBD" or away == "TBD":
            continue
        entries.append({
            "football_data_id": m.get("id"),
            "local_ordinal_id": f"M{i:03d}",
            "home": home,
            "away": away,
            "home_tla": home_team.get("tla", "").strip(),
            "away_tla": away_team.get("tla", "").strip(),
            "kickoff_utc": m.get("utcDate"),
            "stage": m.get("stage"),
            "group": m.get("group"),
            "matchday": m.get("matchday"),
            "status": m.get("status"),
            "venue": m.get("venue"),
        })
    return entries


# ---------------------------------------------------------------------------
# Grading card scanning
# ---------------------------------------------------------------------------

def list_all_grading_cards(workspace):
    """Return all football_data_ids that have grading cards."""
    cards_dir = os.path.join(workspace, "grading", "cards")
    if not os.path.exists(cards_dir):
        return []
    ids = []
    for f in os.listdir(cards_dir):
        if f.startswith("grade-") and f.endswith(".json"):
            path = os.path.join(cards_dir, f)
            try:
                with open(path) as fh:
                    card = json.load(fh)
                ids.append({
                    "football_data_id": card.get("football_data_id"),
                    "home": card.get("home"),
                    "away": card.get("away"),
                    "result": card.get("result"),
                    "card_id": card.get("card_id"),
                    "card_path": path,
                })
            except Exception:
                pass
    return ids


def list_finished_uncarded(workspace, fixtures=None):
    """
    Return FINISHED fixtures that have NO grading card.

    Criterion: fixture_registry status == "FINISHED" AND no matching
    grade-*.json exists. This is the correct gate — a future TIMED
    match with a pass_incomplete manifest will NOT appear here.

    Args:
        workspace: WC26 workspace path
        fixtures: pre-loaded fixture list (avoids re-reading file)
    """
    if fixtures is None:
        fixtures = load_fixtures(workspace)

    finished = [f for f in fixtures if f.get("status") == "FINISHED"]
    cards = list_all_grading_cards(workspace)
    carded_ids = {str(c["football_data_id"]) for c in cards}

    uncarded = []
    for f in finished:
        fid = str(f["football_data_id"])
        if fid not in carded_ids:
            uncarded.append({
                "football_data_id": f["football_data_id"],
                "home": f["home"],
                "away": f["away"],
                "local_ordinal_id": f["local_ordinal_id"],
                "kickoff_utc": f["kickoff_utc"],
                "status": f["status"],
                "reason": "FINISHED but no grading card exists",
            })
    return uncarded


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list_pending(workspace):
    """List everything pending review: grading cards + FINISHED-uncarded."""
    registry = load_registry(workspace)
    fixtures = load_fixtures(workspace)
    cards = list_all_grading_cards(workspace)
    uncarded = list_finished_uncarded(workspace, fixtures=fixtures)
    reviewed = set(registry.get("reviews", {}).keys())

    # Pending grading cards (have card, not reviewed)
    pending_cards = []
    for c in cards:
        fid = str(c["football_data_id"])
        if fid not in reviewed:
            pending_cards.append(c)

    # Uncarded FINISHED matches (no grading card at all)
    pending_uncarded = []
    for u in uncarded:
        fid = str(u["football_data_id"])
        if fid not in reviewed:
            pending_uncarded.append(u)

    print(json.dumps({
        "total_grading_cards": len(cards),
        "total_finished_uncarded": len(uncarded),
        "reviewed_count": len(reviewed),
        "pending_card_count": len(pending_cards),
        "pending_uncarded_count": len(pending_uncarded),
        "pending_cards": pending_cards,
        "pending_uncarded": pending_uncarded,
        "reviewed_ids": sorted(list(reviewed)),
    }, indent=2, default=str))
    return pending_cards + pending_uncarded


def cmd_mark_reviewed(workspace, football_data_id, summary=None):
    """Mark a football_data_id as reviewed (works for carded and uncarded)."""
    fid = str(football_data_id)
    registry = load_registry(workspace)
    registry["reviews"][fid] = {
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": summary or "",
    }
    path = save_registry(workspace, registry)
    print(json.dumps({"status": "marked_reviewed", "football_data_id": fid, "registry_path": path}))


def cmd_status(workspace, football_data_id):
    """Check if a football_data_id has been reviewed."""
    fid = str(football_data_id)
    registry = load_registry(workspace)
    if fid in registry.get("reviews", {}):
        print(json.dumps({"football_data_id": fid, "reviewed": True, "detail": registry["reviews"][fid]}, default=str))
    else:
        print(json.dumps({"football_data_id": fid, "reviewed": False}))


# ---------------------------------------------------------------------------
# Poison self-test
# ---------------------------------------------------------------------------

def cmd_self_test(workspace):
    """
    Poison test: create a fake manifest for a TIMED (future) match, run
    uncarded detection, assert it does NOT appear.

    Uses the first TIMED fixture found. If none exist, test is skipped
    (not a failure — no poison to inject).
    """
    fixtures = load_fixtures(workspace)
    timed = [f for f in fixtures if f.get("status") == "TIMED"]
    if not timed:
        print(json.dumps({"self_test": "skipped", "reason": "no TIMED fixtures available for poison injection"}))
        return 0

    target = timed[0]
    target_fid = target["football_data_id"]
    target_id = target["local_ordinal_id"]

    # Create a fake pass_incomplete manifest for this TIMED fixture
    artifacts_dir = os.path.join(workspace, "reports", "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    poison_path = os.path.join(artifacts_dir, f"manifest-POISON-TEST-{target_id}-{target_fid}.json")
    poison_manifest = {
        "workflow_contract": "wc26.direct_report.v1",
        "football_data_id": target_fid,
        "match_id": target_id,
        "home": target["home"],
        "away": target["away"],
        "final_status": "pass_incomplete",
        "window": "POISON_TEST",
        "_poison_test": True,
    }
    with open(poison_path, "w") as f:
        json.dump(poison_manifest, f, indent=2)

    errors = []
    try:
        # Run uncarded detection
        uncarded = list_finished_uncarded(workspace, fixtures=fixtures)
        uncarded_ids = {str(u["football_data_id"]) for u in uncarded}

        # Assert: the TIMED fixture is NOT in uncarded
        if str(target_fid) in uncarded_ids:
            errors.append(
                f"POISON LEAK: TIMED fixture {target_id} (fid={target_fid}, "
                f"status={target['status']}) appeared in uncarded list. "
                f"Gate failure — FINISHED filter not working."
            )

        # Assert: the TIMED fixture IS in fixtures
        fixture_ids = {str(f["football_data_id"]) for f in fixtures}
        if str(target_fid) not in fixture_ids:
            errors.append(f"POISON LEAK: target fixture {target_id} not found in fixtures at all")

        # Also verify no other TIMED/SCHEDULED fixtures leaked
        non_finished_statuses = {"TIMED", "SCHEDULED", "POSTPONED", "CANCELLED", "PAUSED", "SUSPENDED"}
        for u in uncarded:
            u_fid = str(u["football_data_id"])
            matching = [f for f in fixtures if str(f["football_data_id"]) == u_fid]
            if matching and matching[0].get("status") in non_finished_statuses:
                errors.append(
                    f"POISON LEAK: non-FINISHED fixture {matching[0]['local_ordinal_id']} "
                    f"(fid={u_fid}, status={matching[0]['status']}) leaked into uncarded"
                )

    finally:
        # Clean up the poison manifest
        if os.path.exists(poison_path):
            os.remove(poison_path)

    if errors:
        print(json.dumps({"self_test": "FAILED", "errors": errors}, indent=2))
        return 1
    else:
        print(json.dumps({
            "self_test": "PASSED",
            "poison_fixture": f"{target_id} ({target['home']} vs {target['away']}, status={target['status']})",
            "assertion": "TIMED fixture did NOT appear in uncarded list",
        }, indent=2))
        return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Post-match review completion tracker (v2)")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE, help="WC26 workspace path")
    parser.add_argument("--list-pending", action="store_true", help="List grading cards + FINISHED-uncarded not yet reviewed")
    parser.add_argument("--mark-reviewed", action="store_true", help="Mark a football_data_id as reviewed")
    parser.add_argument("--status", action="store_true", help="Check review status of a football_data_id")
    parser.add_argument("--self-test", action="store_true", help="Run poison test (TIMED fixture must NOT leak into uncarded)")
    parser.add_argument("--football-data-id", type=str, help="football_data_id to operate on")
    parser.add_argument("--summary", type=str, help="Optional review summary for --mark-reviewed")
    args = parser.parse_args()

    if args.self_test:
        sys.exit(cmd_self_test(args.workspace))
    elif args.list_pending:
        cmd_list_pending(args.workspace)
    elif args.mark_reviewed and args.football_data_id:
        cmd_mark_reviewed(args.workspace, args.football_data_id, args.summary)
    elif args.status and args.football_data_id:
        cmd_status(args.workspace, args.football_data_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
