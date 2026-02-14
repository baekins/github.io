"""The Odds API 연동 모듈.

무료 API 키로 외부 북메이커 배당률을 자동 수집합니다.
API 키가 없으면 gracefully 스킵합니다.

설정: .env 파일에 ODDS_API_KEY=your_key 추가
무료 가입: https://the-odds-api.com/
"""
from __future__ import annotations

import os
import json
import urllib.request
import urllib.error
from typing import Optional

API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4"

# Polymarket 타이틀에서 스포츠 종류를 추정하는 매핑
SPORT_KEYWORDS = {
    "soccer": ["fc", "united", "city", "real", "barcelona", "arsenal", "chelsea",
               "liverpool", "juventus", "bayern", "psg", "milan", "inter",
               "tottenham", "dortmund", "atletico", "benfica", "porto",
               "fa cup", "premier league", "la liga", "serie a", "bundesliga",
               "champions league", "europa league", "mls", "ligue 1"],
    "basketball_nba": ["lakers", "celtics", "warriors", "nets", "knicks",
                       "bulls", "heat", "bucks", "76ers", "suns",
                       "nuggets", "clippers", "mavs", "mavericks", "nba"],
    "basketball_ncaab": ["ncaa", "march madness", "college basketball"],
    "americanfootball_nfl": ["nfl", "chiefs", "eagles", "cowboys", "49ers",
                             "bills", "ravens", "dolphins", "jets", "patriots",
                             "packers", "lions", "bears", "vikings", "rams",
                             "super bowl"],
    "baseball_mlb": ["mlb", "yankees", "dodgers", "mets", "red sox",
                     "cubs", "astros", "braves", "phillies", "padres"],
    "icehockey_nhl": ["nhl", "rangers", "bruins", "penguins", "maple leafs",
                      "canadiens", "blackhawks", "oilers", "avalanche"],
    "mma_mixed_martial_arts": ["ufc", "mma", "bellator"],
    "boxing_boxing": ["boxing", "bout", "fight night"],
}

# The Odds API 스포츠 키 매핑
ODDS_API_SPORTS = {
    "soccer": "soccer_epl",  # 기본값, 실제로는 리그별로 다름
    "basketball_nba": "basketball_nba",
    "basketball_ncaab": "basketball_ncaab",
    "americanfootball_nfl": "americanfootball_nfl",
    "baseball_mlb": "baseball_mlb",
    "icehockey_nhl": "icehockey_nhl",
    "mma_mixed_martial_arts": "mma_mixed_martial_arts",
    "boxing_boxing": "boxing_boxing",
}

SOCCER_LEAGUES = {
    "fa cup": "soccer_fa_cup",
    "premier league": "soccer_epl",
    "epl": "soccer_epl",
    "la liga": "soccer_spain_la_liga",
    "serie a": "soccer_italy_serie_a",
    "bundesliga": "soccer_germany_bundesliga",
    "ligue 1": "soccer_france_ligue_one",
    "champions league": "soccer_uefa_champs_league",
    "europa league": "soccer_uefa_europa_league",
    "mls": "soccer_usa_mls",
}


def _detect_sport(title: str) -> Optional[str]:
    """마켓 타이틀에서 스포츠 종류를 추정합니다."""
    title_lower = title.lower()
    for sport, keywords in SPORT_KEYWORDS.items():
        for kw in keywords:
            if kw in title_lower:
                if sport == "soccer":
                    for league, api_key in SOCCER_LEAGUES.items():
                        if league in title_lower:
                            return api_key
                    return "soccer_epl"
                return ODDS_API_SPORTS.get(sport, sport)
    return None


def _fetch_json(url: str) -> dict:
    """URL에서 JSON을 가져툵니다."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return {}


def _match_event(events: list, title: str) -> Optional[dict]:
    """이벤트 목록에서 타이틀과 가장 유사한 이벤트를 찾습니다."""
    title_lower = title.lower()
    best_match = None
    best_score = 0

    for event in events:
        home = event.get("home_team", "").lower()
        away = event.get("away_team", "").lower()
        score = 0
        for word in home.split():
            if len(word) > 2 and word in title_lower:
                score += 1
        for word in away.split():
            if len(word) > 2 and word in title_lower:
                score += 1
        if score > best_score:
            best_score = score
            best_match = event

    return best_match if best_score >= 1 else None


async def fetch_external_odds(title: str) -> dict[str, dict[str, float]]:
    """외부 북메이커 배당률을 수집합니다.

    Returns:
        {bookmaker_name: {outcome_name: decimal_odds}} 형태의 딕셔너리
    """
    if not API_KEY:
        return {}

    sport = _detect_sport(title)
    if not sport:
        return {}

    url = (
        f"{BASE_URL}/sports/{sport}/odds/"
        f"?apiKey={API_KEY}&regions=eu,us&markets=h2h&oddsFormat=decimal"
    )

    data = _fetch_json(url)
    if not isinstance(data, list) or not data:
        return {}

    event = _match_event(data, title)
    if not event:
        return {}

    result = {}
    for bookmaker in event.get("bookmakers", []):
        bookie_name = bookmaker.get("title", bookmaker.get("key", "unknown"))
        for market in bookmaker.get("markets", []):
            if market.get("key") != "h2h":
                continue
            odds_map = {}
            for outcome in market.get("outcomes", []):
                name = outcome.get("name", "")
                price = outcome.get("price", 0)
                if name and price > 0:
                    odds_map[name] = price
            if odds_map:
                result[bookie_name] = odds_map

    # 상위 5개 북메이커만 반환
    return dict(list(result.items())[:5])
