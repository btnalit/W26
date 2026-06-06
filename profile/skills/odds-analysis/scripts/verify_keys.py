#!/usr/bin/env python3
"""Verify WC26 data-source credentials without printing secrets.

Environment variables:
  FOOTBALL_DATA_TOKEN
  ODDS_API_KEY
  ODDSPAPI_KEY

Optional quota-spending probes:
  VERIFY_ODDS_PROBE=1
  VERIFY_ODDSPAPI_HEALTH=1
  VERIFY_ODDSPAPI_ODDS=1
  VERIFY_ODDSPAPI_MARKETS=1

The script checks connectivity, current coverage, and quota signals. It does
not write API keys to disk or print them. Oddspapi remote checks are opt-in
because its free tier is small and some metadata endpoints count against quota.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import requests


def hr() -> None:
    print("-" * 72)


def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 20,
) -> requests.Response:
    return requests.get(url, headers=headers, params=params, timeout=timeout)


def get_json_retry(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    attempts: int = 3,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return get_json(url, headers=headers, params=params)
        except requests.exceptions.SSLError as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(1.5 * attempt)
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("unreachable retry state")


def check_football_data() -> None:
    print("[1] football-data.org")
    token = os.environ.get("FOOTBALL_DATA_TOKEN")
    if not token:
        print("  SKIP: FOOTBALL_DATA_TOKEN not set")
        hr()
        return

    headers = {"X-Auth-Token": token}
    endpoints = [
        ("matches", "https://api.football-data.org/v4/competitions/WC/matches"),
        ("standings", "https://api.football-data.org/v4/competitions/WC/standings"),
        ("scorers", "https://api.football-data.org/v4/competitions/WC/scorers"),
    ]
    for label, url in endpoints:
        try:
            response = get_json_retry(url, headers=headers)
            print(
                f"  {label} HTTP {response.status_code} | "
                f"minute_remaining={response.headers.get('X-Requests-Available-Minute', '?')}"
            )
            if response.status_code != 200:
                print(f"  {label} FAIL: {response.text[:180]}")
                continue
            data = response.json()
            if label == "matches":
                matches = data.get("matches", [])
                print(f"  PASS: matches={len(matches)}")
                for match in matches[:3]:
                    home = match.get("homeTeam", {}).get("name") or "TBD"
                    away = match.get("awayTeam", {}).get("name") or "TBD"
                    print(f"    {match.get('utcDate', '')[:16]}Z | {home} vs {away} | {match.get('status')}")
            elif label == "standings":
                print(f"  PASS: standings={len(data.get('standings', []))}")
            elif label == "scorers":
                print(f"  PASS: scorers={len(data.get('scorers', []))}")
        except Exception as exc:
            print(f"  {label} WARN: {exc}")
    hr()


def check_the_odds_api() -> None:
    print("[2] the-odds-api.com broad scan source")
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        print("  SKIP: ODDS_API_KEY not set")
        hr()
        return

    try:
        response = get_json("https://api.the-odds-api.com/v4/sports/", params={"apiKey": key, "all": "true"})
        print(
            "  sports HTTP {code} | used={used} remaining={remaining} last={last}".format(
                code=response.status_code,
                used=response.headers.get("x-requests-used", "?"),
                remaining=response.headers.get("x-requests-remaining", "?"),
                last=response.headers.get("x-requests-last", "?"),
            )
        )
        if response.status_code != 200:
            print(f"  FAIL: {response.text[:180]}")
            hr()
            return

        sports = response.json()
        soccer = [s for s in sports if str(s.get("group", "")).lower().startswith("soccer")]
        print(f"  soccer_sports={len(soccer)}")
        keys = {
            "soccer_fifa_world_cup": next((s for s in sports if s.get("key") == "soccer_fifa_world_cup"), None),
            "soccer_fifa_world_cup_winner": next((s for s in sports if s.get("key") == "soccer_fifa_world_cup_winner"), None),
        }
        for sport_key, sport in keys.items():
            print(
                f"  key {sport_key}: found={bool(sport)} "
                f"active={sport.get('active') if sport else None} "
                f"title={sport.get('title') if sport else None}"
            )

        if keys["soccer_fifa_world_cup"] and os.environ.get("VERIFY_ODDS_PROBE") == "1":
            odds = get_json(
                "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds/",
                params={
                    "apiKey": key,
                    "regions": "eu",
                    "markets": "h2h,spreads,totals",
                    "bookmakers": "pinnacle",
                    "oddsFormat": "decimal",
                },
            )
            print(
                f"  odds_probe HTTP {odds.status_code} | "
                f"cost={odds.headers.get('x-requests-last', '?')} "
                f"remaining={odds.headers.get('x-requests-remaining', '?')}"
            )
            if odds.status_code == 200:
                print(f"  pinnacle_events={len(odds.json())}")
            else:
                print(f"  odds_probe FAIL: {odds.text[:180]}")
        elif keys["soccer_fifa_world_cup"]:
            print("  odds_probe skipped; set VERIFY_ODDS_PROBE=1 to spend odds quota")
        else:
            print("  WARN: soccer_fifa_world_cup not found")
            for sport in soccer[:8]:
                print(f"    soccer sample: {sport.get('key')} | {sport.get('title')} | active={sport.get('active')}")
    except Exception as exc:
        print(f"  FAIL: {exc}")
    hr()


def check_oddspapi() -> None:
    print("[3] oddspapi.io source")
    key = os.environ.get("ODDSPAPI_KEY")
    if not key:
        print("  SKIP: ODDSPAPI_KEY not set")
        hr()
        return

    if os.environ.get("VERIFY_ODDSPAPI_HEALTH") != "1":
        print("  remote checks skipped; set VERIFY_ODDSPAPI_HEALTH=1 for quota/account/tournament probe")
        print("  quota note: Oddspapi free tier is small; routine health checks must use cached snapshots")
        hr()
        return

    base = "https://api.oddspapi.io/v4"
    try:
        account = get_json(f"{base}/account", params={"apiKey": key})
        print(f"  account HTTP {account.status_code}")
        if account.status_code == 200:
            subscriptions = account.json().get("subscriptions", [])
            if subscriptions:
                current = subscriptions[0]
                print(f"  quota: count={current.get('request_count')} limit={current.get('request_limit')}")

        response = get_json(f"{base}/tournaments", params={"apiKey": key, "sportId": 10})
        print(f"  tournaments HTTP {response.status_code}")
        if response.status_code != 200:
            print(f"  FAIL: {response.text[:180]}")
            hr()
            return

        payload = response.json()
        tournaments = payload if isinstance(payload, list) else payload.get("tournaments", [])
        candidates = [
            t
            for t in tournaments
            if str(t.get("tournamentSlug")) == "world-cup"
            and str(t.get("categoryName")) == "International"
        ]
        print(f"  tournaments={len(tournaments)}")
        if not candidates:
            print("  WARN: exact International/world-cup tournament not found")
            for tournament in tournaments[:8]:
                print(f"    sample: {tournament.get('tournamentName')} | id={tournament.get('tournamentId')}")
            hr()
            return

        tournament = candidates[0]
        tournament_id = tournament.get("tournamentId")
        print(
            f"  PASS: {tournament.get('tournamentName')} | id={tournament_id} | "
            f"future={tournament.get('futureFixtures')} upcoming={tournament.get('upcomingFixtures')}"
        )

        if os.environ.get("VERIFY_ODDSPAPI_ODDS") == "1":
            odds = get_json(
                f"{base}/odds-by-tournaments",
                params={
                    "apiKey": key,
                    "tournamentIds": tournament_id,
                    "bookmaker": "pinnacle",
                    "language": "en",
                    "verbosity": 3,
                    "oddsFormat": "decimal",
                },
            )
            print(f"  odds_by_tournament HTTP {odds.status_code}")
            if odds.status_code == 200:
                rows = odds.json() if isinstance(odds.json(), list) else []
                print(f"  pinnacle_rows={len(rows)}")
                if rows:
                    row = rows[0]
                    books = row.get("bookmakerOdds", {})
                    markets = books.get("pinnacle", {}).get("markets", {})
                    print(
                        f"  sample={row.get('participant1Name')} vs {row.get('participant2Name')} "
                        f"markets_sample={list(markets.keys())[:8]}"
                    )
            else:
                print(f"  odds_by_tournament FAIL: {odds.text[:180]}")
        else:
            print("  odds_by_tournament skipped; set VERIFY_ODDSPAPI_ODDS=1 to spend oddspapi quota")

        if os.environ.get("VERIFY_ODDSPAPI_MARKETS") == "1":
            markets = get_json(f"{base}/markets", params={"apiKey": key, "sportId": 10})
            print(f"  markets HTTP {markets.status_code}")
            if markets.status_code == 200:
                rows = markets.json() if isinstance(markets.json(), list) else []
                selected = {
                    101: "Full Time Result",
                    1010: "Over Under Full Time 2.5",
                    1068: "Asian Handicap -0.5",
                }
                for market_id, label in selected.items():
                    row = next((m for m in rows if m.get("marketId") == market_id), None)
                    print(
                        f"  market {market_id} expected={label} "
                        f"actual={row.get('marketName') if row else None} "
                        f"handicap={row.get('handicap') if row else None}"
                    )
        else:
            print("  markets skipped; set VERIFY_ODDSPAPI_MARKETS=1 to spend oddspapi quota")

        print("  quota note: /account is non-billable; cache all tournament/market/odds calls")
    except Exception as exc:
        print(f"  FAIL: {exc}")
    hr()


def main() -> int:
    print(f"verify_keys captured_at={datetime.now(timezone.utc).isoformat()}")
    hr()
    check_football_data()
    check_the_odds_api()
    check_oddspapi()
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
