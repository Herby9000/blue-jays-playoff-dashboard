#!/usr/bin/env python3
"""Update data.json for the Blue Jays dashboard.

Sources:
- TeamRankings Toronto Blue Jays projections page for playoff probability.
- MLB Stats API for last five completed games and next scheduled game.
- MLB Blue Jays RSS feed for latest news headlines.

Uses only the Python standard library so it runs cleanly in GitHub Actions.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

TEAM_ID = 141  # Toronto Blue Jays
TEAM_NAME = "Toronto Blue Jays"
ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BlueJaysDashboard/1.0; +https://github.com/Herby9000)",
    "Accept": "text/html,application/json,application/xml,text/xml,*/*",
}


def fetch_text(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, "replace")


def fetch_json(url: str, timeout: int = 25) -> dict[str, Any]:
    return json.loads(fetch_text(url, timeout=timeout))


def strip_tags(value: str) -> str:
    value = re.sub(r"<script.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def get_playoff_odds() -> dict[str, Any]:
    """Return playoff odds from TeamRankings, with Baseball-Reference fallback."""
    sources: list[str] = []
    notes: list[str] = []

    # Primary source: TeamRankings has a simple HTML card: h4 Make Playoffs -> p/span NN.N%
    url = "https://www.teamrankings.com/mlb/team/toronto-blue-jays/projections"
    try:
        page = fetch_text(url)
        m = re.search(
            r"<h4>\s*Make\s+Playoffs\s*</h4>\s*<p>\s*<span>\s*([0-9.]+)%\s*</span>",
            page,
            flags=re.I | re.S,
        )
        final_record = None
        fr = re.search(r"<h4>\s*Final\s+Record\s*</h4>\s*<p>\s*([0-9.]+\s*-\s*[0-9.]+)\s*</p>", page, re.I | re.S)
        if fr:
            final_record = strip_tags(fr.group(1))
        if m:
            return {
                "probability_pct": float(m.group(1)),
                "label": f"{float(m.group(1)):.1f}%",
                "source": "TeamRankings",
                "source_url": url,
                "final_record_projection": final_record,
                "notes": notes,
            }
        notes.append("TeamRankings page loaded but the Make Playoffs value was not found.")
        sources.append(url)
    except Exception as exc:  # noqa: BLE001 - record source-specific failure in data
        notes.append(f"TeamRankings unavailable: {type(exc).__name__}: {exc}")
        sources.append(url)

    # Fallback: Baseball-Reference playoff odds page has the team row in static HTML.
    # It may change column names, so we parse the Toronto row and look for a postseason percentage-ish cell.
    year = dt.datetime.now(dt.timezone.utc).year
    url = f"https://www.baseball-reference.com/leagues/majors/{year}-playoff-odds.shtml"
    try:
        page = fetch_text(url)
        row_match = re.search(r"<tr[^>]*>.*?Toronto Blue Jays.*?</tr>", page, flags=re.I | re.S)
        if row_match:
            row = row_match.group(0)
            cells = re.findall(r"data-stat=\"([^\"]+)\"[^>]*>(.*?)</td>", row, flags=re.I | re.S)
            for stat, raw in cells:
                text = strip_tags(raw)
                if stat in {"ppr_playoffs", "ppr_postseason", "ppr_post"} and re.search(r"[0-9]", text):
                    value = float(re.sub(r"[^0-9.]", "", text))
                    return {
                        "probability_pct": value,
                        "label": f"{value:.1f}%",
                        "source": "Baseball-Reference",
                        "source_url": url,
                        "final_record_projection": None,
                        "notes": notes,
                    }
            notes.append("Baseball-Reference row found, but no recognized playoff probability column was present.")
        else:
            notes.append("Baseball-Reference Toronto row not found.")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"Baseball-Reference fallback unavailable: {type(exc).__name__}: {exc}")

    return {
        "probability_pct": None,
        "label": "Unavailable",
        "source": "Unavailable",
        "source_url": sources[0] if sources else None,
        "final_record_projection": None,
        "notes": notes,
    }


def game_datetime(game: dict[str, Any]) -> str | None:
    return game.get("gameDate")


def team_side(game: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], bool]:
    teams = game["teams"]
    home = teams["home"]
    away = teams["away"]
    is_home = home["team"]["id"] == TEAM_ID
    return (home if is_home else away), (away if is_home else home), is_home


def compact_game(game: dict[str, Any]) -> dict[str, Any]:
    jays, opp, is_home = team_side(game)
    j_score = jays.get("score")
    o_score = opp.get("score")
    result = None
    if isinstance(j_score, int) and isinstance(o_score, int):
        result = "W" if j_score > o_score else "L"
    venue = game.get("venue", {}).get("name")
    return {
        "game_pk": game.get("gamePk"),
        "date": game.get("officialDate"),
        "game_date_utc": game_datetime(game),
        "opponent": opp["team"]["name"],
        "opponent_abbrev": opp["team"].get("abbreviation"),
        "home_away": "home" if is_home else "away",
        "venue": venue,
        "status": game.get("status", {}).get("detailedState"),
        "result": result,
        "blue_jays_score": j_score,
        "opponent_score": o_score,
        "summary": f"{result or ''} {j_score}-{o_score} {'vs' if is_home else '@'} {opp['team']['name']}" if result else f"{'vs' if is_home else '@'} {opp['team']['name']}",
    }


def get_games() -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    notes: list[str] = []
    today = dt.datetime.now(dt.timezone.utc).date()
    # Wide enough windows to handle All-Star breaks and off days.
    start = today - dt.timedelta(days=90)
    end = today + dt.timedelta(days=45)
    params = urllib.parse.urlencode({
        "sportId": 1,
        "teamId": TEAM_ID,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "hydrate": "team,venue",
    })
    url = f"https://statsapi.mlb.com/api/v1/schedule?{params}"
    try:
        data = fetch_json(url)
    except Exception as exc:  # noqa: BLE001
        return [], None, [f"MLB Stats API unavailable: {type(exc).__name__}: {exc}"]

    games: list[dict[str, Any]] = []
    for date_block in data.get("dates", []):
        games.extend(date_block.get("games", []))

    finals = [g for g in games if g.get("status", {}).get("abstractGameState") == "Final"]
    finals.sort(key=lambda g: g.get("gameDate") or "")
    last_five = [compact_game(g) for g in finals[-5:]][::-1]

    now = dt.datetime.now(dt.timezone.utc)
    future = []
    for g in games:
        gd = g.get("gameDate")
        if not gd:
            continue
        try:
            when = dt.datetime.fromisoformat(gd.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when >= now and g.get("status", {}).get("abstractGameState") != "Final":
            future.append(g)
    future.sort(key=lambda g: g.get("gameDate") or "")
    next_game = compact_game(future[0]) if future else None
    return last_five, next_game, notes


def get_news() -> tuple[list[dict[str, str]], list[str]]:
    notes: list[str] = []
    url = "https://www.mlb.com/bluejays/feeds/news/rss.xml"
    try:
        raw = fetch_text(url)
        root = ET.fromstring(raw)
        items = []
        for item in root.findall("./channel/item")[:8]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            desc = strip_tags(item.findtext("description") or "")
            if title:
                items.append({"title": title, "url": link, "published": pub, "summary": desc})
            if len(items) >= 5:
                break
        if items:
            return items, notes
        notes.append("MLB RSS returned no news items.")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"MLB RSS unavailable: {type(exc).__name__}: {exc}")

    return [], notes


def main() -> int:
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    odds = get_playoff_odds()
    last_five, next_game, game_notes = get_games()
    news, news_notes = get_news()

    payload = {
        "generated_at_utc": generated_at,
        "team": {"id": TEAM_ID, "name": TEAM_NAME},
        "playoff_odds": odds,
        "last_five_games": last_five,
        "next_game": next_game,
        "news": news,
        "sources": {
            "playoff_odds": odds.get("source_url"),
            "games": "https://statsapi.mlb.com/",
            "news": "https://www.mlb.com/bluejays/feeds/news/rss.xml",
        },
        "notes": [*odds.get("notes", []), *game_notes, *news_notes],
    }
    DATA_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {DATA_PATH}")
    print(json.dumps({
        "generated_at_utc": generated_at,
        "playoff_odds": odds.get("label"),
        "last_five_count": len(last_five),
        "next_game": next_game.get("summary") if next_game else None,
        "news_count": len(news),
        "notes": payload["notes"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
