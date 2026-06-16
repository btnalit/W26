#!/usr/bin/env python3
"""
coverage_scan.py — Single source of truth for WC26 match coverage status.

Scans all data islands (fixtures, reports, manifests, artifacts, grading cards,
reviews) and outputs one row per match in the requested date range.

Usage:
  python3 coverage_scan.py                          # today's matches
  python3 coverage_scan.py --from 2026-06-15        # single date
  python3 coverage_scan.py --from 2026-06-14 --to 2026-06-16  # date range
  python3 coverage_scan.py --pending-only           # only rows with gaps
  python3 coverage_scan.py --summary                # counts only
"""
import json, os, sys, argparse, glob, unicodedata
from datetime import datetime, timezone
from collections import defaultdict

DEFAULT_WORKSPACE = "/hermesdata/worldcup-2026-handicap"

FIXTURE_PATH = "snapshots/fixtures/football-data-wc-matches-latest.json"
REPORTS_DIR = "reports/match"
ARTIFACTS_DIR = "reports/artifacts"
GRADING_DIR = "grading/cards"
REVIEWS_PATH = "grading/reviews/completed.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(s):
    text = unicodedata.normalize("NFKD", (s or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().replace("-", " ").split())


def _jload(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

def load_fixtures(workspace):
    path = os.path.join(workspace, FIXTURE_PATH)
    if not os.path.exists(path):
        return []
    payload = _jload(path)
    if not payload:
        return []
    raw = payload.get("matches", []) or payload.get("data", {}).get("matches", [])
    entries = []
    for i, m in enumerate(raw, 1):
        ht = (m.get("homeTeam") or {})
        at = (m.get("awayTeam") or {})
        home = (ht.get("name") or "").strip()
        away = (at.get("name") or "").strip()
        if not home or not away or home == "TBD" or away == "TBD":
            continue
        entries.append({
            "football_data_id": m.get("id"),
            "local_ordinal_id": f"M{i:03d}",
            "home": home,
            "away": away,
            "home_tla": (ht.get("tla") or "").strip(),
            "away_tla": (at.get("tla") or "").strip(),
            "home_norm": _norm(home),
            "away_norm": _norm(away),
            "kickoff_utc": m.get("utcDate"),
            "stage": m.get("stage"),
            "group": m.get("group"),
            "matchday": m.get("matchday"),
            "status": m.get("status"),
        })
    return entries


def _build_team_index(fixtures):
    """Build (home_norm, away_norm) → fixture lookup."""
    idx = {}
    for f in fixtures:
        idx[(f["home_norm"], f["away_norm"])] = f
        idx[(f["away_norm"], f["home_norm"])] = f
    return idx


# ---------------------------------------------------------------------------
# Artifact scanning — Phase 1: by football_data_id
# ---------------------------------------------------------------------------

def _scan_dir_by_fid(workspace, rel_dir, glob_pat, build_meta):
    """
    Scan a directory for JSON files, extract football_data_id from content,
    return {fid_str: meta_dict}.
    """
    scan_dir = os.path.join(workspace, rel_dir)
    results = {}
    if not os.path.isdir(scan_dir):
        return results
    for path_str in sorted(glob.glob(os.path.join(scan_dir, glob_pat))):
        data = _jload(path_str)
        if not data:
            continue
        fid = data.get("football_data_id")
        if fid is not None:
            key = str(fid)
            results[key] = build_meta(data, path_str)
    return results


def _scan_dir_team_fallback(workspace, rel_dir, glob_pat, build_meta, team_index, fixtures_by_ordinal=None, fixtures_by_tla_pair=None):
    """
    Like _scan_dir_by_fid but with multi-level fallback for artifacts
    without football_data_id:
      1. match_home + match_away → team-name matching
      2. match_id → local_ordinal_id lookup
      3. "match" string like "Belgium vs Egypt" → parse and team-name match
    Returns {fid_str: meta_dict}.
    """
    scan_dir = os.path.join(workspace, rel_dir)
    results = {}
    if not os.path.isdir(scan_dir):
        return results
    for path_str in sorted(glob.glob(os.path.join(scan_dir, glob_pat))):
        data = _jload(path_str)
        if not data:
            continue
        fid = data.get("football_data_id")
        if fid is not None:
            key = str(fid)
            if key not in results:
                results[key] = build_meta(data, path_str)
            continue

        resolved_fid = None

        # Fallback 1: team name matching
        home = data.get("match_home") or data.get("home")
        away = data.get("match_away") or data.get("away")
        if home and away:
            key = (_norm(home), _norm(away))
            fixture = team_index.get(key)
            if fixture:
                resolved_fid = str(fixture["football_data_id"])

        # Fallback 2: local_ordinal_id via match_id
        if resolved_fid is None and fixtures_by_ordinal:
            mid = data.get("match_id")
            if mid and str(mid).upper() in fixtures_by_ordinal:
                resolved_fid = str(fixtures_by_ordinal[str(mid).upper()]["football_data_id"])

        # Fallback 2.5: TLA-pair match_id like "KSA-URY"
        if resolved_fid is None and fixtures_by_tla_pair:
            mid = data.get("match_id")
            if mid and mid.upper() in fixtures_by_tla_pair:
                resolved_fid = str(fixtures_by_tla_pair[mid.upper()]["football_data_id"])

        # Fallback 3: parse "match" string like "Belgium vs Egypt"
        if resolved_fid is None:
            match_str = data.get("match")
            if isinstance(match_str, str) and " vs " in match_str:
                parts = match_str.split(" vs ", 1)
                key = (_norm(parts[0]), _norm(parts[1]))
                fixture = team_index.get(key)
                if fixture:
                    resolved_fid = str(fixture["football_data_id"])

        if resolved_fid:
            if resolved_fid not in results:
                results[resolved_fid] = build_meta(data, path_str)
    return results


# ---------------------------------------------------------------------------
# Meta builders
# ---------------------------------------------------------------------------

def _meta_manifest(data, path_str):
    return {
        "has_manifest": True,
        "manifest_path": path_str,
        "manifest_final_status": data.get("final_status"),
        "manifest_window": data.get("window"),
    }


def _meta_crossbook(data, path_str):
    h2h = (data.get("markets") or {}).get("h2h", {})
    summary = data.get("summary", {})
    return {
        "has_crossbook": True,
        "crossbook_path": path_str,
        "crossbook_edge_count": h2h.get("edge_count") or summary.get("edge_count"),
        "crossbook_actionable_count": h2h.get("actionable_count") or summary.get("actionable_count", 0),
        "crossbook_qualified_play_count": h2h.get("qualified_play_count") or summary.get("qualified_play_count", 0),
    }


def _meta_consistency(data, path_str):
    return {
        "has_consistency": True,
        "consistency_path": path_str,
    }


def _meta_mechanism_audit(data, path_str):
    return {
        "has_mechanism_audit": True,
        "mechanism_audit_path": path_str,
        "mechanism_required_final_status": data.get("required_final_status"),
    }


def _meta_deep_research(data, path_str):
    return {
        "has_deep_research": True,
        "deep_research_path": path_str,
    }


def _meta_grading_card(data, path_str):
    return {
        "has_grading_card": True,
        "grading_card_path": path_str,
        "grading_result": data.get("result"),
        "grading_actual_outcome": data.get("actual_outcome"),
    }


# ---------------------------------------------------------------------------
# MD report scanning
# ---------------------------------------------------------------------------

def _scan_md_reports(workspace):
    """Parse .md reports for football_data_id in header lines."""
    reports_dir = os.path.join(workspace, REPORTS_DIR)
    results = {}
    if not os.path.isdir(reports_dir):
        return results
    for path_str in sorted(glob.glob(os.path.join(reports_dir, "*.md"))):
        try:
            with open(path_str) as f:
                head = "".join(f.readline() for _ in range(30))
        except Exception:
            continue
        fid = None
        for line in head.split("\n"):
            if "football_data_id:" in line:
                try:
                    fid = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
                if fid:
                    break
            if "canonical_id:" in line and "fd:" in line:
                try:
                    fid = int(line.split("fd:", 1)[1].strip().split()[0])
                except ValueError:
                    pass
                if fid:
                    break
        if fid:
            key = str(fid)
            if key not in results:
                results[key] = {"has_md_report": True, "md_report_path": path_str}
    return results


def _load_reviews(workspace):
    data = _jload(os.path.join(workspace, REVIEWS_PATH))
    if not data:
        return set()
    return set(data.get("reviews", {}).keys())


# ---------------------------------------------------------------------------
# Review worthiness triage
# ---------------------------------------------------------------------------

# Priority tiers:
#   1 — MUST review (claimed edge or directional observation)
#   2 — SHOULD review (watch with anomaly, or result available)
#   3 — CAN review (low-priority completeness)
#   0 — SKIP (no signal, mechanical gap only, or no analysis)

def compute_review_worthiness(row):
    """Return (worth_it: bool, reason: str, priority: int)."""
    status = row.get("manifest_final_status") or ""
    edges = row.get("crossbook_edge_count") or 0
    actionable = row.get("crossbook_actionable_count") or 0
    has_result = bool(row.get("grading_result"))
    gaps = row.get("gaps", [])

    # Tier 1: Claimed edge — always review
    if status == "qualified_play":
        return True, "claimed_edge", 1
    if status == "lean":
        return True, "directional_observation", 1

    # Tier 2: Watch with actionable signal or result available
    if status == "watch" and actionable > 0:
        return True, "watch_with_actionable_anomaly", 2
    if status == "watch" and has_result:
        return True, "watch_with_known_result", 2
    if actionable > 0 and status in ("pass_incomplete", "pass"):
        return True, "actionable_edge_existed", 2
    # Noise edges (>0 total but 0 actionable) on heavy favorite → skip
    if edges > 0 and actionable == 0 and status in ("pass_incomplete", "pass", "watch"):
        return False, "noise_edges_only_no_review_value", 0

    # Tier 3: Needs grading card for completeness (has manifest, no card)
    if "no_grading_card" in gaps and row.get("has_manifest"):
        return True, "needs_grading_card_for_completeness", 3

    # Tier 0: No analysis — can't review nothing
    if "no_analysis" in gaps:
        return False, "no_analysis_to_review", 0

    # Tier 0: Pure pass/pass_incomplete/watch with 0 edges, card exists or not
    if status in ("pass", "pass_incomplete", "watch") and edges == 0 and not has_result:
        if row.get("has_grading_card"):
            return False, "no_edge_no_surprise_result", 0
        return False, "no_edge_mechanical_gap_only", 0

    # Default: anything else with gaps is low priority
    if gaps:
        return False, "mechanical_gaps_only", 0

    return False, "complete_no_gaps", 0


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan(workspace, from_date, to_date, pending_only=False, review_worthy_only=False):
    fixtures_all = load_fixtures(workspace)

    # Filter by date range
    fixtures = []
    for f in fixtures_all:
        try:
            ko = datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if from_date <= ko.date() <= to_date:
            fixtures.append(f)

    if not fixtures:
        return {"matches": [], "scan_range": {"from": str(from_date), "to": str(to_date)}, "total": 0}

    team_index = _build_team_index(fixtures)
    fixtures_by_ordinal = {f["local_ordinal_id"]: f for f in fixtures}
    # Build TLA-pair index for match_ids like "KSA-URY", "IRN-NZL"
    fixtures_by_tla_pair = {}
    for f in fixtures:
        ht = (f.get("home_tla") or "").upper()
        at = (f.get("away_tla") or "").upper()
        if ht and at:
            fixtures_by_tla_pair[f"{ht}-{at}"] = f
            fixtures_by_tla_pair[f"{at}-{ht}"] = f

    # Phase 1: by football_data_id
    manifests = _scan_dir_by_fid(workspace, ARTIFACTS_DIR, "manifest-*.json", _meta_manifest)
    grading_cards = _scan_dir_by_fid(workspace, GRADING_DIR, "grade-*.json", _meta_grading_card)
    # Merged manifest-like from both naming conventions
    manifests_alt = _scan_dir_by_fid(workspace, ARTIFACTS_DIR, "manifest-*.json", _meta_manifest)
    for k, v in manifests_alt.items():
        if k not in manifests:
            manifests[k] = v

    # Phase 2: team-name / ordinal / TLA fallback
    crossbooks = _scan_dir_team_fallback(workspace, ARTIFACTS_DIR, "crossbook-*.json", _meta_crossbook, team_index, fixtures_by_ordinal, fixtures_by_tla_pair)
    consistencies = _scan_dir_team_fallback(workspace, ARTIFACTS_DIR, "consistency-*.json", _meta_consistency, team_index, fixtures_by_ordinal, fixtures_by_tla_pair)
    audits = _scan_dir_team_fallback(workspace, ARTIFACTS_DIR, "mechanism-audit-*.json", _meta_mechanism_audit, team_index, fixtures_by_ordinal, fixtures_by_tla_pair)
    deep_researches = _scan_dir_team_fallback(workspace, ARTIFACTS_DIR, "deep-research-*.json", _meta_deep_research, team_index, fixtures_by_ordinal, fixtures_by_tla_pair)

    md_reports = _scan_md_reports(workspace)
    reviewed_ids = _load_reviews(workspace)

    # Merge all artifact maps into a single lookup
    all_maps = [
        manifests, grading_cards, crossbooks, consistencies, audits, deep_researches, md_reports
    ]
    merged = defaultdict(dict)
    for src in all_maps:
        for fid, meta in src.items():
            merged[fid].update(meta)

    # Build rows
    rows = []
    for f in fixtures:
        fid = str(f["football_data_id"])
        m = merged.get(fid, {})

        row = {
            "football_data_id": f["football_data_id"],
            "local_ordinal_id": f["local_ordinal_id"],
            "home": f["home"],
            "away": f["away"],
            "kickoff_utc": f["kickoff_utc"],
            "status": f["status"],
            "stage": f["stage"],
            "group": f.get("group"),
            "matchday": f.get("matchday"),
            # Coverage
            "has_md_report": m.get("has_md_report", False),
            "md_report_path": m.get("md_report_path"),
            "has_manifest": m.get("has_manifest", False),
            "manifest_path": m.get("manifest_path"),
            "manifest_final_status": m.get("manifest_final_status"),
            "manifest_window": m.get("manifest_window"),
            "has_crossbook": m.get("has_crossbook", False),
            "crossbook_path": m.get("crossbook_path"),
            "crossbook_edge_count": m.get("crossbook_edge_count"),
            "crossbook_actionable_count": m.get("crossbook_actionable_count", 0),
            "crossbook_qualified_play_count": m.get("crossbook_qualified_play_count", 0),
            "has_consistency": m.get("has_consistency", False),
            "consistency_path": m.get("consistency_path"),
            "has_mechanism_audit": m.get("has_mechanism_audit", False),
            "mechanism_audit_path": m.get("mechanism_audit_path"),
            "has_deep_research": m.get("has_deep_research", False),
            "deep_research_path": m.get("deep_research_path"),
            "has_grading_card": m.get("has_grading_card", False),
            "grading_card_path": m.get("grading_card_path"),
            "grading_result": m.get("grading_result"),
            "is_reviewed": fid in reviewed_ids,
        }

        # Gap analysis
        gaps = []
        if f["status"] == "FINISHED" and not row["has_grading_card"]:
            gaps.append("no_grading_card")
        if not row["has_md_report"] and row["has_manifest"]:
            gaps.append("no_md_report")
        if not row["has_manifest"] and not row["has_md_report"]:
            gaps.append("no_analysis")
        if f["status"] == "FINISHED" and not row["is_reviewed"]:
            gaps.append("not_reviewed")
        row["gaps"] = gaps
        row["coverage_complete"] = len(gaps) == 0

        # Review worthiness triage
        worthy, reason, priority = compute_review_worthiness(row)
        row["review_worthiness"] = worthy
        row["review_worthiness_reason"] = reason
        row["review_worthiness_priority"] = priority

        rows.append(row)

    if pending_only:
        rows = [r for r in rows if r["gaps"]]
    if review_worthy_only:
        rows = [r for r in rows if r.get("review_worthiness")]

    return {
        "scan_utc": datetime.now(timezone.utc).isoformat(),
        "workspace": workspace,
        "scan_range": {"from": str(from_date), "to": str(to_date)},
        "total": len(rows),
        "matches": rows,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="WC26 match coverage scanner")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    parser.add_argument("--from", dest="from_date", help="Start date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--to", dest="to_date", help="End date YYYY-MM-DD (default: same as --from)")
    parser.add_argument("--pending-only", action="store_true", help="Only rows with coverage gaps")
    parser.add_argument("--review-worthy-only", action="store_true", help="Only rows worth reviewing (triage filter)")
    parser.add_argument("--summary", action="store_true", help="Counts only")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    from_date = datetime.fromisoformat(args.from_date).date() if args.from_date else today
    to_date = datetime.fromisoformat(args.to_date).date() if args.to_date else from_date

    result = scan(args.workspace, from_date, to_date, pending_only=args.pending_only, review_worthy_only=args.review_worthy_only)

    if args.summary:
        rows = result["matches"]
        total = len(rows)
        print(json.dumps({
            "scan_range": result["scan_range"],
            "total_fixtures": total,
            "has_report": sum(1 for r in rows if r["has_md_report"]),
            "has_manifest": sum(1 for r in rows if r["has_manifest"]),
            "has_crossbook": sum(1 for r in rows if r["has_crossbook"]),
            "has_consistency": sum(1 for r in rows if r["has_consistency"]),
            "has_mechanism_audit": sum(1 for r in rows if r["has_mechanism_audit"]),
            "has_deep_research": sum(1 for r in rows if r["has_deep_research"]),
            "has_grading_card": sum(1 for r in rows if r["has_grading_card"]),
            "reviewed": sum(1 for r in rows if r["is_reviewed"]),
            "with_gaps": sum(1 for r in rows if r["gaps"]),
            "review_worthy": sum(1 for r in rows if r.get("review_worthiness")),
            "review_worthy_by_priority": {
                "1_must": sum(1 for r in rows if r.get("review_worthiness_priority") == 1),
                "2_should": sum(1 for r in rows if r.get("review_worthiness_priority") == 2),
                "3_can": sum(1 for r in rows if r.get("review_worthiness_priority") == 3),
                "0_skip": sum(1 for r in rows if r.get("review_worthiness_priority") == 0),
            },
        }, indent=2))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
