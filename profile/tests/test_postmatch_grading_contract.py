from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_payload():
    path = ROOT / "scripts" / "wc26_cron_payload.py"
    spec = importlib.util.spec_from_file_location("wc26_cron_payload_postmatch_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def configure_workspace(module, tmp_path: Path) -> None:
    module.WORKSPACE = tmp_path
    module.STATE_DIR = tmp_path / "state"
    module.GRADING_DIR = tmp_path / "grading"
    module.GRADING_CARDS_DIR = tmp_path / "grading" / "cards"
    module.PATH_C_LEDGER_DIR = tmp_path / "grading" / "path_c_signal_ledger"


def write_fixture_snapshot(tmp_path: Path, status: str = "FINISHED") -> Path:
    return write_json(
        tmp_path / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json",
        {
            "captured_at_utc": "2026-06-11T22:00:00Z",
            "source": "football-data.org",
            "data": {
                "matches": [
                    {
                        "id": 537327,
                        "utcDate": "2026-06-11T19:00:00Z",
                        "status": status,
                        "homeTeam": {"name": "Mexico", "tla": "MEX"},
                        "awayTeam": {"name": "South Africa", "tla": "RSA"},
                        "score": {
                            "winner": "HOME_TEAM" if status == "FINISHED" else None,
                            "duration": "REGULAR",
                            "fullTime": {
                                "home": 2 if status == "FINISHED" else None,
                                "away": 0 if status == "FINISHED" else None,
                            },
                            "halfTime": {"home": 1 if status == "FINISHED" else None, "away": 0 if status == "FINISHED" else None},
                        },
                        "events": [
                            {"type": "red_card", "team": "South Africa", "minute": 45, "player": "Sithole"},
                            {"type": "card", "card": "red", "team": {"name": "South Africa"}, "minute": 80, "player": "Zwane"},
                            {"type": "disallowed_goal", "team": "South Africa", "minute": 77, "player": "Soucek", "reason": "offside"},
                        ],
                    }
                ]
            },
        },
    )


def write_odds_snapshot(tmp_path: Path, stem: str, captured_at: str, mexico_price: float) -> Path:
    return write_json(
        tmp_path / "snapshots" / "odds" / f"the-odds-api-multibook-{stem}.json",
        {
            "captured_at_utc": captured_at,
            "source": "the-odds-api",
            "data": [
                {
                    "home_team": "Mexico",
                    "away_team": "South Africa",
                    "bookmakers": [
                        {
                            "key": "pinnacle",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "outcomes": [
                                        {"name": "Mexico", "price": mexico_price},
                                        {"name": "Draw", "price": 4.70},
                                        {"name": "South Africa", "price": 9.00},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )


def write_report_bundle(tmp_path: Path) -> Path:
    artifacts_dir = tmp_path / "reports" / "artifacts"
    devig_path = write_json(
        artifacts_dir / "devig-M001.json",
        {
            "artifact_type": "devig",
            "artifact_kind": "scalar_market",
            "decimal_odds": [1.43, 4.70, 9.00],
            "no_vig_probabilities": [0.6895, 0.2057, 0.1047],
            "devig_methods": {
                "shin": [0.6895, 0.2057, 0.1047],
                "power": [0.6895, 0.2057, 0.1047],
                "multiplicative": [0.6895, 0.2057, 0.1047],
            },
            "survives_all_methods": True,
        },
    )
    path_c_path = write_json(
        artifacts_dir / "consistency-M001.json",
        {
            "artifact_type": "consistency_triangle",
            "artifact_kind": "consistency_triangle",
            "signal": {
                "type": None,
                "strength": "diagnostic_suppressed",
                "suppressed": True,
                "suppress_reason": "wide_spread_poisson_unreliable",
                "raw_type": "人性税（Under被撑）",
                "raw_strength": "强",
                "raw_action": "AH+1X2 反推显示 Totals 市场 Under 被低估",
                "raw_discrepancy_pp": -15.6,
            },
            "discrepancy": {
                "pp": None,
                "direction": "under_cheap",
                "suppressed": True,
                "suppress_reason": "wide_spread_poisson_unreliable",
                "raw_pp": -15.6,
            },
            "market_profile": {
                "total_line_lean": {"line": 2.25, "lean": "under"},
                "score_distribution": [
                    {"score": "1-0", "home_goals": 1, "away_goals": 0, "prob": 0.1596, "rank": 1, "tied_rank": 1},
                    {"score": "2-0", "home_goals": 2, "away_goals": 0, "prob": 0.1477, "rank": 2, "tied_rank": 2},
                ],
            },
        },
    )
    manifest_path = write_json(
        artifacts_dir / "manifest-M001.json",
        {
            "workflow_contract": "wc26.direct_report.v1",
            "match_id": "M001",
            "football_data_id": 537327,
            "home": "Mexico",
            "away": "South Africa",
            "mode": "live",
            "source_quality": "B",
            "final_status": "watch",
            "window": "T-60m_lineup_final",
            "timing_class": "lineup_final",
            "source_freshness": {"snapshots": [{"source": "the-odds-api", "captured_at_utc": "2026-06-11T18:00:00Z", "age_minutes": 60}]},
            "analysis_gates": {"source_freshness": "pass"},
            "artifacts": [
                {"artifact_id": "devig:M001", "artifact_type": "devig", "path": str(devig_path), "provides": ["devig_1x2"]},
                {"artifact_id": "pathc:M001", "artifact_type": "consistency_triangle", "path": str(path_c_path), "provides": ["path_c_consistency"]},
            ],
        },
    )
    report_path = tmp_path / "reports" / "match" / "M001-MEX-RSA-T-60m.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "\n".join(
            [
                "# WC26 M001 Mexico vs South Africa - T-60m_lineup_final Handicap Report",
                "",
                "mode: live",
                "match_id: M001",
                "window: T-60m_lineup_final",
                "timing_class: lineup_final",
                "entry_price: null",
                f"artifact_manifest_path: {manifest_path}",
                "final_status: watch",
                "",
                "Mexico vs South Africa",
            ]
        ),
        encoding="utf-8",
    )
    return report_path


def test_postmatch_grade_uses_pre_kickoff_close_and_writes_context_score_and_path_c_ledger(tmp_path: Path, monkeypatch, capsys) -> None:
    module = load_payload()
    configure_workspace(module, tmp_path)
    monkeypatch.setenv("WC26_NOW_UTC", "2026-06-11T22:30:00Z")
    write_fixture_snapshot(tmp_path)
    pre = write_odds_snapshot(tmp_path, "20260611T160000Z", "2026-06-11T16:00:00Z", 1.43)
    write_odds_snapshot(tmp_path, "20260611T213000Z", "2026-06-11T21:30:00Z", 99.0)
    report_path = write_report_bundle(tmp_path)

    assert module.postmatch_grade() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "ok"

    cards = sorted((tmp_path / "grading" / "cards").glob("*.json"))
    assert len(cards) == 1
    assert cards[0].name == f"grade-{module.stable_hash(['M001', 'T-60m_lineup_final'])}.json"
    card = json.loads(cards[0].read_text(encoding="utf-8"))
    assert card["idempotency_key"] == "M001:T-60m_lineup_final"
    assert card["closing_odds_snapshot"] == str(pre)
    assert card["closing_snapshot_age_at_kickoff_minutes"] == 180
    assert card["closing_quality"] == "degraded"
    assert {flag["type"] for flag in card["match_context_flags"]} >= {"red_card", "disallowed_goal"}
    assert card["scoreline_profile"]["actual_score"] == "2-0"
    assert card["scoreline_profile"]["rank"] == 2

    ledger_files = sorted((tmp_path / "grading" / "path_c_signal_ledger").glob("*.json"))
    assert len(ledger_files) == 1
    ledger = json.loads(ledger_files[0].read_text(encoding="utf-8"))
    assert ledger["suppressed"] is True
    assert ledger["direction"] == "under_cheap"
    assert ledger["outcome_agrees"] is True
    assert ledger["pp_band"] == "ge15"

    first_hash = card["content_hash"]
    report_path.write_text(report_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert module.postmatch_grade() == 0
    capsys.readouterr()
    assert len(list((tmp_path / "grading" / "cards").glob("*.json"))) == 1
    assert len(list((tmp_path / "grading" / "path_c_signal_ledger").glob("*.json"))) == 1
    second_card = json.loads(cards[0].read_text(encoding="utf-8"))
    assert second_card["content_hash"] == first_hash


def test_postmatch_grade_blocks_stale_fixture_snapshot_after_kickoff(tmp_path: Path, monkeypatch, capsys) -> None:
    module = load_payload()
    configure_workspace(module, tmp_path)
    monkeypatch.setenv("WC26_NOW_UTC", "2026-06-12T00:45:00Z")
    path = write_fixture_snapshot(tmp_path, status="TIMED")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["captured_at_utc"] = "2026-06-11T10:00:00Z"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    assert module.postmatch_grade() == 2
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "blocked_stale_fixture_snapshot"
    assert out["stale_match_count"] == 1
    assert out["required_action"] == "run wc26-fixture-collect with WC26_FORCE_REFRESH=1 before grading"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, headers: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


def test_fixture_collect_fetches_match_detail_events_for_finished_matches(tmp_path: Path, monkeypatch, capsys) -> None:
    module = load_payload()
    configure_workspace(module, tmp_path)
    monkeypatch.setenv("FOOTBALL_DATA_TOKEN", "token")
    monkeypatch.setenv("WC26_FORCE_REFRESH", "1")
    calls: list[str] = []

    def fake_get(url: str, headers: dict, timeout: int):
        calls.append(url)
        if url.endswith("/competitions/WC/matches"):
            return FakeResponse(
                200,
                {
                    "matches": [
                        {"id": 537327, "status": "FINISHED", "homeTeam": {"name": "Mexico"}, "awayTeam": {"name": "South Africa"}},
                        {"id": 537328, "status": "TIMED", "homeTeam": {"name": "Canada"}, "awayTeam": {"name": "Qatar"}},
                    ]
                },
                headers={"X-Requests-Available-Minute": "9"},
            )
        if url.endswith("/matches/537327"):
            return FakeResponse(
                200,
                {
                    "match": {
                        "id": 537327,
                        "events": [
                            {"type": "red_card", "team": {"name": "South Africa"}, "minute": 45},
                            {"type": "card", "card": "red", "team": {"name": "South Africa"}, "minute": 80},
                            {"type": "goal", "team": {"name": "Mexico"}, "minute": 90},
                        ]
                    }
                },
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(module.requests, "get", fake_get)

    assert module.fixture_collect() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "ok"
    assert out["fixture_detail_summary"] == {"attempted": 1, "merged": 1, "failed": 0, "http_statuses": {"200": 1}}
    assert calls == [
        "https://api.football-data.org/v4/competitions/WC/matches",
        "https://api.football-data.org/v4/matches/537327",
    ]
    snapshot = json.loads((tmp_path / "snapshots" / "fixtures" / "football-data-wc-matches-latest.json").read_text(encoding="utf-8"))
    match = snapshot["data"]["matches"][0]
    flags = module.extract_match_context_flags(match, 2, 1)
    red_flags = [flag for flag in flags if flag["type"] == "red_card"]
    stoppage_flags = [flag for flag in flags if flag["type"] == "stoppage_winner"]
    assert len(red_flags) == 2
    assert len(stoppage_flags) == 1


def test_report_matching_uses_identity_not_group_context_mentions(tmp_path: Path) -> None:
    module = load_payload()
    configure_workspace(module, tmp_path)
    fixture_path = write_fixture_snapshot(tmp_path)
    good_report = write_report_bundle(tmp_path)
    bad_manifest = write_json(
        tmp_path / "reports" / "artifacts" / "manifest-KOR-CZE.json",
        {"match_id": "KOR-CZE", "football_data_id": 537328, "window": "T-24h_confirm", "final_status": "pass"},
    )
    bad_report = tmp_path / "reports" / "match" / "KOR-CZE.md"
    bad_report.write_text(
        "\n".join([
            "# South Korea vs Czechia",
            f"artifact_manifest_path: {bad_manifest}",
            "window: T-24h_confirm",
            "Group context mentions Mexico vs South Africa, but identity is KOR-CZE.",
        ]),
        encoding="utf-8",
    )
    # Make the wrong report newer: mtime must not outrank stable identity.
    bad_report.touch()
    fm = json.loads(fixture_path.read_text())["data"]["matches"][0]
    candidates = module.report_candidates_for_fixture([bad_report, good_report], fm, fixture_path)
    assert candidates
    assert candidates[0][1] == good_report


def test_clv_positions_are_percent_ev_and_brier_has_uniform_baseline() -> None:
    module = load_payload()
    report_text = "\n".join([
        "| Market | Line | Book | Source Unit | Current Decimal | Snapshot ID | No-Vig Market (Shin) | p_adj | Edge | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        "| 1X2 | Korea | Pinnacle | decimal | 2.80 | snap | 0.3464 | 0.3464 | 0 | PASS |",
        "| 1X2 | Draw | Pinnacle | decimal | 3.11 | snap | 0.3110 | 0.3110 | 0 | PASS |",
        "| 1X2 | Czechia | Pinnacle | decimal | 2.83 | snap | 0.3426 | 0.3426 | 0 | PASS |",
        "| AH 0.0 | Korea | Pinnacle | decimal | 1.94 | snap | 0.5026 | 0.5026 | 0 | PASS |",
        "| AH 0.0 | Czechia | Pinnacle | decimal | 1.96 | snap | 0.4974 | 0.4974 | 0 | PASS |",
    ])
    close_odds_raw = {
        "bookmakers": [{
            "key": "pinnacle",
            "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Czech Republic", "price": 3.06},
                    {"name": "South Korea", "price": 2.65},
                    {"name": "Draw", "price": 3.14},
                ]},
                {"key": "spreads", "outcomes": [
                    {"name": "Czech Republic", "price": 2.12, "point": -0.0},
                    {"name": "South Korea", "price": 1.83, "point": 0.0},
                ]},
            ],
        }]
    }
    positions = module.compute_clv_positions(report_text, close_odds_raw, "South Korea")
    assert module.closing_odds_for_fixture({"x": {"home_team": "South Korea", "away_team": "Czech Republic"}}, "South Korea", "Czechia")["away_team"] == "Czech Republic"
    by_market = {item["market"]: item for item in positions}
    assert by_market["h2h"]["unit"] == "percent_ev_fraction"
    assert by_market["h2h"]["clv_ev"] == 0.0349
    assert by_market["h2h"]["clv_pct"] == 3.49
    assert by_market["spreads"]["clv_ev"] == 0.0425
    assert by_market["spreads"]["clv_pct"] == 4.25

    probs = module.parse_report_1x2_model_probs(report_text)
    brier = sum((probs[i] - [1, 0, 0][i]) ** 2 for i in range(3))
    assert round(brier, 4) == 0.6413
    assert round(brier - module.uniform_three_way_brier_baseline(), 4) == -0.0254


def test_deep_research_f4_under_claim_is_red_card_downgraded() -> None:
    module = load_payload()
    findings = [
        {"finding_id": "DR-F4", "direction": "toward_under", "confidence": "medium", "claim": "South Africa 5-man defense"},
    ]
    scored = module.score_deep_research_findings(findings, "home", [{"type": "red_card", "team": "South Africa"}])
    assert scored[0]["score"] == "confounded_by_red_card"
    assert scored[0]["red_card_downgraded"] is True
