from __future__ import annotations

"""
WNBA Player Props Lab

A standalone Streamlit dashboard for WNBA points, rebounds, assists and PRA.
Primary data source: official WNBA Stats endpoints (LeagueID 10).
Optional market source: The Odds API.

Install:
    pip install streamlit pandas numpy requests
Run:
    streamlit run wnba_player_props_dashboard.py
"""

import io
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests
import streamlit as st


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

MODEL_VERSION = "wnba-props-lab-v1"
WNBA_LEAGUE_ID = "10"
WNBA_STATS_BASE = "https://stats.wnba.com/stats"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_SPORT = "basketball_wnba"

TARGETS: dict[str, dict[str, str]] = {
    "Points": {
        "projection": "Proj_PTS",
        "raw_projection": "Raw_Proj_PTS",
        "actual": "Actual_PTS",
        "score": "PTS_Score",
        "sd": "PTS_SD",
        "line": "PTS_Line",
        "over_odds": "PTS_Over_Odds",
        "under_odds": "PTS_Under_Odds",
        "market_prob": "PTS_Market_Over_Prob",
        "model_over": "PTS_Model_Over_Prob",
        "edge": "PTS_Probability_Edge",
        "market": "player_points",
    },
    "Rebounds": {
        "projection": "Proj_REB",
        "raw_projection": "Raw_Proj_REB",
        "actual": "Actual_REB",
        "score": "REB_Score",
        "sd": "REB_SD",
        "line": "REB_Line",
        "over_odds": "REB_Over_Odds",
        "under_odds": "REB_Under_Odds",
        "market_prob": "REB_Market_Over_Prob",
        "model_over": "REB_Model_Over_Prob",
        "edge": "REB_Probability_Edge",
        "market": "player_rebounds",
    },
    "Assists": {
        "projection": "Proj_AST",
        "raw_projection": "Raw_Proj_AST",
        "actual": "Actual_AST",
        "score": "AST_Score",
        "sd": "AST_SD",
        "line": "AST_Line",
        "over_odds": "AST_Over_Odds",
        "under_odds": "AST_Under_Odds",
        "market_prob": "AST_Market_Over_Prob",
        "model_over": "AST_Model_Over_Prob",
        "edge": "AST_Probability_Edge",
        "market": "player_assists",
    },
    "PRA": {
        "projection": "Proj_PRA",
        "raw_projection": "Raw_Proj_PRA",
        "actual": "Actual_PRA",
        "score": "PRA_Score",
        "sd": "PRA_SD",
        "line": "PRA_Line",
        "over_odds": "PRA_Over_Odds",
        "under_odds": "PRA_Under_Odds",
        "market_prob": "PRA_Market_Over_Prob",
        "model_over": "PRA_Model_Over_Prob",
        "edge": "PRA_Probability_Edge",
        "market": "player_points_rebounds_assists",
    },
}

TEAM_ALIASES: dict[str, str] = {
    "ATL": "ATL", "ATLANTA DREAM": "ATL", "ATLANTA": "ATL",
    "CHI": "CHI", "CHICAGO SKY": "CHI", "CHICAGO": "CHI",
    "CON": "CON", "CONNECTICUT SUN": "CON", "CONNECTICUT": "CON",
    "DAL": "DAL", "DALLAS WINGS": "DAL", "DALLAS": "DAL",
    "GSV": "GSV", "GS": "GSV", "GOLDEN STATE VALKYRIES": "GSV", "GOLDEN STATE": "GSV",
    "IND": "IND", "INDIANA FEVER": "IND", "INDIANA": "IND",
    "LVA": "LVA", "LV": "LVA", "LAS VEGAS ACES": "LVA", "LAS VEGAS": "LVA",
    "LAS": "LAS", "LA": "LAS", "LOS ANGELES SPARKS": "LAS", "LOS ANGELES": "LAS",
    "MIN": "MIN", "MINNESOTA LYNX": "MIN", "MINNESOTA": "MIN",
    "NYL": "NYL", "NY": "NYL", "NEW YORK LIBERTY": "NYL", "NEW YORK": "NYL",
    "PHO": "PHO", "PHX": "PHO", "PHOENIX MERCURY": "PHO", "PHOENIX": "PHO",
    "POR": "POR", "PORTLAND FIRE": "POR", "PORTLAND": "POR",
    "SEA": "SEA", "SEATTLE STORM": "SEA", "SEATTLE": "SEA",
    "TOR": "TOR", "TORONTO TEMPO": "TOR", "TORONTO": "TOR",
    "WAS": "WAS", "WSH": "WAS", "WASHINGTON MYSTICS": "WAS", "WASHINGTON": "WAS",
}

TEAM_NAMES = {
    "ATL": "Atlanta Dream", "CHI": "Chicago Sky", "CON": "Connecticut Sun",
    "DAL": "Dallas Wings", "GSV": "Golden State Valkyries", "IND": "Indiana Fever",
    "LVA": "Las Vegas Aces", "LAS": "Los Angeles Sparks", "MIN": "Minnesota Lynx",
    "NYL": "New York Liberty", "PHO": "Phoenix Mercury", "POR": "Portland Fire",
    "SEA": "Seattle Storm", "TOR": "Toronto Tempo", "WAS": "Washington Mystics",
}

BOOKMAKER_LABELS = {
    "hardrockbet": "Hard Rock Bet",
    "fanduel": "FanDuel",
    "draftkings": "DraftKings",
    "betmgm": "BetMGM",
    "caesars": "Caesars",
    "pinnacle": "Pinnacle",
    "espnbet": "ESPN BET",
    "betrivers": "BetRivers",
    "prizepicks": "PrizePicks",
    "underdog": "Underdog Fantasy",
}

DEFAULT_STAT_SDS = {"Points": 5.5, "Rebounds": 2.7, "Assists": 2.2, "PRA": 7.5}


# -----------------------------------------------------------------------------
# Presentation
# -----------------------------------------------------------------------------

st.set_page_config(page_title="WNBA Player Props Lab", page_icon="🏀", layout="wide")


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .stApp { background: linear-gradient(135deg, #f8fafc 0%, #fff7ed 45%, #f5f3ff 100%); }
        .block-container { max-width: 1550px; padding-top: 1.2rem; }
        .app-kicker {
            display: inline-flex; padding: .35rem .75rem; border-radius: 999px;
            background: linear-gradient(90deg, #f97316, #7c3aed); color: white;
            font-weight: 850; letter-spacing: .09em; font-size: .72rem;
        }
        div[data-testid="stMetric"] {
            background: rgba(255,255,255,.88); border: 1px solid rgba(124,58,237,.16);
            border-radius: 18px; padding: .85rem 1rem; box-shadow: 0 10px 24px rgba(15,23,42,.06);
        }
        .status-chip { display:inline-block; border-radius:999px; padding:.25rem .55rem; font-weight:800; font-size:.75rem; }
        .small-note { color:#64748b; font-size:.83rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------


def normalize_text(value: object) -> str:
    text = str(value or "").strip().upper()
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_player_name(value: object) -> str:
    text = normalize_text(value)
    suffixes = {"JR", "SR", "II", "III", "IV"}
    parts = [p for p in text.split() if p not in suffixes]
    return " ".join(parts)


def normalize_team(value: object) -> str:
    key = normalize_text(value)
    if key in TEAM_ALIASES:
        return TEAM_ALIASES[key]
    for alias, abbr in TEAM_ALIASES.items():
        if alias and alias in key:
            return abbr
    return key[:3]


def parse_minutes(value: object) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    text = str(value).strip()
    if ":" in text:
        try:
            minutes, seconds = text.split(":", 1)
            return float(minutes) + float(seconds) / 60.0
        except (TypeError, ValueError):
            return np.nan
    return float(pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0])


def numeric(series: pd.Series | Iterable[Any]) -> pd.Series:
    return pd.to_numeric(pd.Series(series), errors="coerce")


def safe_mean(series: pd.Series, default: float = np.nan) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.mean()) if not values.empty else float(default)


def safe_std(series: pd.Series, default: float) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) < 3:
        return float(default)
    value = float(values.std(ddof=1))
    return value if np.isfinite(value) and value > 0.1 else float(default)


def percentile(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() <= 1:
        return pd.Series(50.0, index=series.index)
    return values.rank(pct=True, method="average").fillna(0.5) * 100.0


def normal_cdf(value: float, mean: float, sd: float) -> float:
    if not np.isfinite(mean) or not np.isfinite(sd) or sd <= 0:
        return np.nan
    z = (float(value) - float(mean)) / (float(sd) * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(z))


def prop_probabilities(mean: float, sd: float, line: float) -> tuple[float, float, float]:
    """Approximate discrete-stat over/under/push probabilities with continuity correction."""
    if not all(np.isfinite(x) for x in [mean, sd, line]) or sd <= 0:
        return np.nan, np.nan, np.nan
    if abs(line - round(line)) < 1e-9:
        lower = line - 0.5
        upper = line + 0.5
        under = normal_cdf(lower, mean, sd)
        push = max(normal_cdf(upper, mean, sd) - under, 0.0)
        over = max(1.0 - normal_cdf(upper, mean, sd), 0.0)
    else:
        under = normal_cdf(line, mean, sd)
        over = 1.0 - under
        push = 0.0
    return float(np.clip(over, 0, 1)), float(np.clip(under, 0, 1)), float(np.clip(push, 0, 1))


def american_to_prob(odds: object) -> float:
    value = pd.to_numeric(pd.Series([odds]), errors="coerce").iloc[0]
    if pd.isna(value) or float(value) == 0:
        return np.nan
    value = float(value)
    return 100.0 / (value + 100.0) if value > 0 else (-value) / ((-value) + 100.0)


def no_vig_over_probability(over_odds: object, under_odds: object) -> float:
    over = american_to_prob(over_odds)
    under = american_to_prob(under_odds)
    if not np.isfinite(over) or not np.isfinite(under) or over + under <= 0:
        return np.nan
    return float(over / (over + under))


def parse_stats_result(payload: dict) -> pd.DataFrame:
    sets = payload.get("resultSets") or payload.get("resultSet") or []
    if isinstance(sets, dict):
        sets = [sets]
    for result in sets:
        headers = result.get("headers") or result.get("Headers")
        rows = result.get("rowSet") or result.get("rows") or []
        if headers:
            return pd.DataFrame(rows, columns=headers)
    return pd.DataFrame()


def stats_headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Origin": "https://stats.wnba.com",
        "Referer": "https://stats.wnba.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token": "true",
    }


# -----------------------------------------------------------------------------
# Official WNBA data
# -----------------------------------------------------------------------------


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_wnba_stats(endpoint: str, params: dict[str, Any]) -> pd.DataFrame:
    url = f"{WNBA_STATS_BASE}/{endpoint}"
    last_error: Exception | None = None
    for timeout_seconds in (20, 35):
        try:
            response = requests.get(url, params=params, headers=stats_headers(), timeout=timeout_seconds)
            response.raise_for_status()
            frame = parse_stats_result(response.json())
            if frame.empty:
                raise ValueError(f"WNBA Stats returned no rows for {endpoint}.")
            return frame
        except Exception as exc:  # network/API can be temperamental
            last_error = exc
            time.sleep(0.8)
    raise RuntimeError(f"WNBA Stats request failed: {type(last_error).__name__}: {last_error}")


def league_game_log_params(season: str, player_or_team: str, season_type: str) -> dict[str, Any]:
    return {
        "Counter": "0",
        "DateFrom": "",
        "DateTo": "",
        "Direction": "DESC",
        "LeagueID": WNBA_LEAGUE_ID,
        "PlayerOrTeam": player_or_team,
        "Season": season,
        "SeasonType": season_type,
        "Sorter": "DATE",
    }


@st.cache_data(ttl=3600, show_spinner=False)
def load_official_game_logs(season: str, season_type: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    players = fetch_wnba_stats("leaguegamelog", league_game_log_params(season, "P", season_type))
    teams = fetch_wnba_stats("leaguegamelog", league_game_log_params(season, "T", season_type))
    return normalize_player_logs(players), normalize_team_logs(teams)


def normalize_player_logs(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    aliases = {
        "PLAYER_ID": "PlayerID", "PLAYER_NAME": "Player", "TEAM_ID": "TeamID",
        "TEAM_ABBREVIATION": "Team", "GAME_ID": "GameID", "GAME_DATE": "GameDate",
        "MATCHUP": "Matchup", "MIN": "MIN", "PTS": "PTS", "REB": "REB", "AST": "AST",
        "OREB": "OREB", "DREB": "DREB", "STL": "STL", "BLK": "BLK", "TOV": "TOV",
        "FGM": "FGM", "FGA": "FGA", "FG3M": "FG3M", "FG3A": "FG3A",
        "FTM": "FTM", "FTA": "FTA", "PLUS_MINUS": "PLUS_MINUS", "WL": "WL",
    }
    result = result.rename(columns={k: v for k, v in aliases.items() if k in result.columns})
    required = ["PlayerID", "Player", "Team", "GameID", "GameDate", "MIN", "PTS", "REB", "AST"]
    for column in required:
        if column not in result.columns:
            result[column] = np.nan
    result["GameDate"] = pd.to_datetime(result["GameDate"], errors="coerce").dt.date
    result["MIN"] = result["MIN"].map(parse_minutes)
    numeric_columns = [
        "PlayerID", "TeamID", "MIN", "PTS", "REB", "AST", "OREB", "DREB", "STL", "BLK",
        "TOV", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "PLUS_MINUS",
    ]
    for column in numeric_columns:
        if column not in result.columns:
            result[column] = np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["Team"] = result["Team"].map(normalize_team)
    result["PRA"] = result["PTS"].fillna(0) + result["REB"].fillna(0) + result["AST"].fillna(0)
    result["NameKey"] = result["Player"].map(normalize_player_name)
    result = result.dropna(subset=["PlayerID", "GameDate"]).sort_values(["PlayerID", "GameDate"], ascending=[True, False])
    return result


def extract_opponent(matchup: object) -> str:
    text = str(matchup or "")
    match = re.search(r"(?:@|vs\.?)[ ]*([A-Z]{2,4})", text, flags=re.IGNORECASE)
    return normalize_team(match.group(1)) if match else ""


def normalize_team_logs(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    aliases = {
        "TEAM_ID": "TeamID", "TEAM_ABBREVIATION": "Team", "TEAM_NAME": "TeamName",
        "GAME_ID": "GameID", "GAME_DATE": "GameDate", "MATCHUP": "Matchup", "MIN": "MIN",
        "PTS": "PTS", "REB": "REB", "AST": "AST", "OREB": "OREB", "DREB": "DREB",
        "TOV": "TOV", "FGA": "FGA", "FTA": "FTA", "FGM": "FGM", "PLUS_MINUS": "PLUS_MINUS",
        "WL": "WL",
    }
    result = result.rename(columns={k: v for k, v in aliases.items() if k in result.columns})
    for column in ["Team", "GameID", "GameDate", "Matchup"]:
        if column not in result.columns:
            result[column] = ""
    result["GameDate"] = pd.to_datetime(result["GameDate"], errors="coerce").dt.date
    for column in ["TeamID", "MIN", "PTS", "REB", "AST", "OREB", "DREB", "TOV", "FGA", "FTA", "FGM", "PLUS_MINUS"]:
        if column not in result.columns:
            result[column] = np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["Team"] = result["Team"].map(normalize_team)
    result["Opponent"] = result["Matchup"].map(extract_opponent)
    result["Possessions"] = (
        result["FGA"].fillna(0) + 0.44 * result["FTA"].fillna(0)
        - result["OREB"].fillna(0) + result["TOV"].fillna(0)
    )
    return result.sort_values("GameDate", ascending=False)


# -----------------------------------------------------------------------------
# Schedule loading: official CDN/page with fallbacks
# -----------------------------------------------------------------------------


def recursive_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from recursive_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_dicts(child)


def parse_schedule_payload(payload: Any, slate_date: date) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for node in recursive_dicts(payload):
        key_map = {str(k).lower(): k for k in node.keys()}
        home_value = None
        away_value = None
        for candidate in ["hometeam", "hometeamname", "home", "hteam"]:
            if candidate in key_map:
                home_value = node[key_map[candidate]]
                break
        for candidate in ["awayteam", "awayteamname", "away", "vteam", "visitorteam"]:
            if candidate in key_map:
                away_value = node[key_map[candidate]]
                break
        if isinstance(home_value, dict):
            home_value = home_value.get("teamTricode") or home_value.get("teamName") or home_value.get("name")
        if isinstance(away_value, dict):
            away_value = away_value.get("teamTricode") or away_value.get("teamName") or away_value.get("name")
        home = normalize_team(home_value)
        away = normalize_team(away_value)
        if home not in TEAM_NAMES or away not in TEAM_NAMES or home == away:
            continue
        raw_time = None
        for candidate in ["gamedatetimeutc", "gameutc", "gametimeutc", "gameDateTimeUTC", "gameDateTimeEst", "gameDate"]:
            if candidate.lower() in key_map:
                raw_time = node[key_map[candidate.lower()]]
                break
        parsed_time = pd.to_datetime(raw_time, utc=True, errors="coerce")
        game_date = parsed_time.date() if pd.notna(parsed_time) else slate_date
        if game_date != slate_date:
            continue
        event_id = str(
            node.get("gameId") or node.get("gameID") or node.get("id") or
            f"{slate_date}-{away}-{home}"
        )
        rows.append({
            "EventID": event_id,
            "GameDate": slate_date,
            "GameTimeUTC": parsed_time.isoformat() if pd.notna(parsed_time) else "",
            "Away": away,
            "Home": home,
            "Game": f"{away} @ {home}",
            "Source": "Official WNBA schedule",
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(["Away", "Home", "GameDate"])


@st.cache_data(ttl=900, show_spinner=False)
def fetch_official_schedule(slate_date: date) -> tuple[pd.DataFrame, list[str]]:
    errors: list[str] = []
    urls = [
        "https://cdn.wnba.com/static/json/staticData/scheduleLeagueV2_1.json",
        "https://www.wnba.com/schedule",
    ]
    headers = {"User-Agent": stats_headers()["User-Agent"], "Accept": "application/json,text/html,*/*"}
    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            payload: Any
            content_type = response.headers.get("content-type", "")
            if "json" in content_type or response.text.lstrip().startswith(("{", "[")):
                payload = response.json()
            else:
                match = re.search(
                    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
                    response.text,
                    flags=re.DOTALL | re.IGNORECASE,
                )
                if not match:
                    raise ValueError("No embedded schedule JSON was found.")
                payload = json.loads(match.group(1))
            frame = parse_schedule_payload(payload, slate_date)
            if not frame.empty:
                return frame, errors
            errors.append(f"{url}: no games found for {slate_date}")
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
    return pd.DataFrame(), errors


# -----------------------------------------------------------------------------
# The Odds API
# -----------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner=False)
def fetch_odds_events(api_key: str) -> tuple[pd.DataFrame, dict[str, str]]:
    if not api_key:
        return pd.DataFrame(), {}
    response = requests.get(
        f"{ODDS_API_BASE}/sports/{ODDS_API_SPORT}/events",
        params={"apiKey": api_key}, timeout=20,
    )
    response.raise_for_status()
    rows = []
    for event in response.json():
        home = normalize_team(event.get("home_team"))
        away = normalize_team(event.get("away_team"))
        rows.append({
            "EventID": str(event.get("id", "")),
            "GameTimeUTC": str(event.get("commence_time", "")),
            "Home": home, "Away": away, "Game": f"{away} @ {home}",
            "Source": "The Odds API",
        })
    meta = {
        "remaining": response.headers.get("x-requests-remaining", ""),
        "used": response.headers.get("x-requests-used", ""),
    }
    return pd.DataFrame(rows), meta


@st.cache_data(ttl=300, show_spinner=False)
def fetch_event_props(api_key: str, event_id: str, markets: tuple[str, ...]) -> tuple[pd.DataFrame, dict[str, str]]:
    if not api_key or not event_id or not markets:
        return pd.DataFrame(), {}
    response = requests.get(
        f"{ODDS_API_BASE}/sports/{ODDS_API_SPORT}/events/{event_id}/odds",
        params={
            "apiKey": api_key,
            "regions": "us",
            "markets": ",".join(markets),
            "oddsFormat": "american",
            "dateFormat": "iso",
        },
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    rows: list[dict[str, Any]] = []
    for bookmaker in payload.get("bookmakers", []):
        book_key = str(bookmaker.get("key", ""))
        book_title = str(bookmaker.get("title", BOOKMAKER_LABELS.get(book_key, book_key)))
        for market in bookmaker.get("markets", []):
            market_key = str(market.get("key", ""))
            updated = market.get("last_update", "")
            for outcome in market.get("outcomes", []):
                player = outcome.get("description") or outcome.get("name")
                side = outcome.get("name") if outcome.get("description") else outcome.get("side")
                rows.append({
                    "EventID": event_id,
                    "Bookmaker": book_title,
                    "BookmakerKey": book_key,
                    "Market": market_key,
                    "Player": str(player or ""),
                    "NameKey": normalize_player_name(player),
                    "Side": str(side or "").title(),
                    "Line": pd.to_numeric(pd.Series([outcome.get("point")]), errors="coerce").iloc[0],
                    "Odds": pd.to_numeric(pd.Series([outcome.get("price")]), errors="coerce").iloc[0],
                    "Updated": str(updated),
                })
    meta = {
        "remaining": response.headers.get("x-requests-remaining", ""),
        "used": response.headers.get("x-requests-used", ""),
    }
    return pd.DataFrame(rows), meta


def combine_odds_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    valid = [frame for frame in frames if frame is not None and not frame.empty]
    if not valid:
        return pd.DataFrame()
    return pd.concat(valid, ignore_index=True).drop_duplicates(
        ["EventID", "BookmakerKey", "Market", "NameKey", "Side", "Line"], keep="last"
    )


def choose_market_quote(group: pd.DataFrame, source_mode: str) -> dict[str, Any]:
    if group.empty:
        return {}
    data = group.copy()
    if source_mode != "Consensus":
        data = data[data["Bookmaker"].eq(source_mode)]
        if data.empty:
            return {}
    lines = pd.to_numeric(data["Line"], errors="coerce").dropna()
    if lines.empty:
        return {}
    target_line = float(lines.median())
    near = data[np.isclose(pd.to_numeric(data["Line"], errors="coerce"), target_line, atol=0.01)].copy()
    over_prices = pd.to_numeric(near.loc[near["Side"].str.lower().eq("over"), "Odds"], errors="coerce").dropna()
    under_prices = pd.to_numeric(near.loc[near["Side"].str.lower().eq("under"), "Odds"], errors="coerce").dropna()
    over_odds = float(over_prices.median()) if not over_prices.empty else np.nan
    under_odds = float(under_prices.median()) if not under_prices.empty else np.nan
    books = sorted(near["Bookmaker"].dropna().astype(str).unique())
    return {
        "Line": target_line,
        "OverOdds": over_odds,
        "UnderOdds": under_odds,
        "MarketOverProb": no_vig_over_probability(over_odds, under_odds),
        "Source": source_mode if source_mode != "Consensus" else f"Consensus ({len(books)} books)",
    }


# -----------------------------------------------------------------------------
# Team environment and projections
# -----------------------------------------------------------------------------


def build_team_environment(team_logs: pd.DataFrame) -> pd.DataFrame:
    if team_logs is None or team_logs.empty:
        return pd.DataFrame(columns=["Team", "Pace", "Opp_PTS", "Opp_REB", "Opp_AST", "Games"])
    logs = team_logs.copy()
    opponent = logs[["GameID", "Team", "PTS", "REB", "AST", "Possessions"]].rename(columns={
        "Team": "Opponent", "PTS": "Opp_PTS", "REB": "Opp_REB", "AST": "Opp_AST",
        "Possessions": "Opp_Possessions",
    })
    merged = logs.merge(opponent, on=["GameID", "Opponent"], how="left")
    merged["GamePace"] = merged[["Possessions", "Opp_Possessions"]].mean(axis=1)
    grouped = merged.groupby("Team", as_index=False).agg(
        Pace=("GamePace", "mean"), Opp_PTS=("Opp_PTS", "mean"),
        Opp_REB=("Opp_REB", "mean"), Opp_AST=("Opp_AST", "mean"), Games=("GameID", "nunique"),
    )
    return grouped


def weighted_recent_mean(values: pd.Series, last5_weight: float = 0.50, last10_weight: float = 0.30) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return np.nan
    season = float(clean.mean())
    last5 = float(clean.head(5).mean())
    last10 = float(clean.head(10).mean())
    return last5_weight * last5 + last10_weight * last10 + (1.0 - last5_weight - last10_weight) * season


def confidence_label(games: int, avg_minutes: float) -> str:
    if games >= 15 and avg_minutes >= 20:
        return "High"
    if games >= 7 and avg_minutes >= 12:
        return "Medium"
    return "Low"


def projected_role(minutes: float) -> str:
    if minutes >= 28:
        return "Starter / core"
    if minutes >= 18:
        return "Rotation"
    return "Bench / limited"


def get_team_factor(environment: pd.DataFrame, opponent: str, column: str) -> float:
    if environment.empty or column not in environment.columns:
        return 1.0
    league = safe_mean(environment[column], 1.0)
    row = environment[environment["Team"].eq(opponent)]
    if row.empty or not np.isfinite(league) or league == 0:
        return 1.0
    value = pd.to_numeric(row.iloc[0][column], errors="coerce")
    if pd.isna(value):
        return 1.0
    return float(np.clip(float(value) / league, 0.85, 1.15))


def get_pace_factor(environment: pd.DataFrame, team: str, opponent: str) -> float:
    if environment.empty or "Pace" not in environment.columns:
        return 1.0
    league = safe_mean(environment["Pace"], 80.0)
    team_row = environment[environment["Team"].eq(team)]
    opp_row = environment[environment["Team"].eq(opponent)]
    team_pace = float(team_row.iloc[0]["Pace"]) if not team_row.empty else league
    opp_pace = float(opp_row.iloc[0]["Pace"]) if not opp_row.empty else league
    expected = math.sqrt(max(team_pace, 1.0) * max(opp_pace, 1.0))
    return float(np.clip(expected / max(league, 1.0), 0.90, 1.10))


def build_base_board(player_logs: pd.DataFrame, team_logs: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    if player_logs.empty or games.empty:
        return pd.DataFrame()
    environment = build_team_environment(team_logs)
    rows: list[dict[str, Any]] = []

    logs = player_logs.sort_values(["PlayerID", "GameDate"], ascending=[True, False]).copy()
    latest_team = logs.groupby("PlayerID", as_index=False).first()[["PlayerID", "Team"]].rename(columns={"Team": "LatestTeam"})
    logs = logs.merge(latest_team, on="PlayerID", how="left")

    for game in games.itertuples(index=False):
        for team, opponent, home_away in [(game.Away, game.Home, "Away"), (game.Home, game.Away, "Home")]:
            team_players = logs[logs["LatestTeam"].eq(team)].copy()
            if team_players.empty:
                continue
            for player_id, group in team_players.groupby("PlayerID"):
                group = group.sort_values("GameDate", ascending=False)
                active = group.head(12)
                games_played = int(group["GameID"].nunique())
                avg_minutes = safe_mean(group["MIN"], 0.0)
                if games_played < 1 or avg_minutes < 5.0:
                    continue
                proj_min = weighted_recent_mean(group["MIN"])
                proj_min = float(np.clip(proj_min if np.isfinite(proj_min) else avg_minutes, 4.0, 38.0))

                minute_floor = group["MIN"].replace(0, np.nan)
                rates: dict[str, float] = {}
                for stat in ["PTS", "REB", "AST", "PRA"]:
                    per_min = group[stat] / minute_floor
                    league_rate = safe_mean(logs[stat] / logs["MIN"].replace(0, np.nan), 0.0)
                    raw_rate = weighted_recent_mean(per_min)
                    sample_minutes = float(group["MIN"].fillna(0).sum())
                    weight = sample_minutes / (sample_minutes + 180.0)
                    rates[stat] = float(weight * raw_rate + (1.0 - weight) * league_rate) if np.isfinite(raw_rate) else league_rate

                pace_factor = get_pace_factor(environment, team, opponent)
                point_factor = 0.70 + 0.30 * get_team_factor(environment, opponent, "Opp_PTS")
                rebound_factor = 0.65 + 0.35 * get_team_factor(environment, opponent, "Opp_REB")
                assist_factor = 0.65 + 0.35 * get_team_factor(environment, opponent, "Opp_AST")
                home_factor = 1.01 if home_away == "Home" else 0.99

                raw_pts = proj_min * rates["PTS"] * pace_factor * point_factor * home_factor
                raw_reb = proj_min * rates["REB"] * pace_factor * rebound_factor
                raw_ast = proj_min * rates["AST"] * pace_factor * assist_factor
                raw_pra = raw_pts + raw_reb + raw_ast

                recent_pts = safe_mean(active["PTS"].head(5), raw_pts)
                recent_reb = safe_mean(active["REB"].head(5), raw_reb)
                recent_ast = safe_mean(active["AST"].head(5), raw_ast)

                scale = math.sqrt(max(proj_min, 1.0) / max(avg_minutes, 1.0))
                pts_sd = float(np.clip(safe_std(group["PTS"], DEFAULT_STAT_SDS["Points"]) * scale, 2.5, 12.0))
                reb_sd = float(np.clip(safe_std(group["REB"], DEFAULT_STAT_SDS["Rebounds"]) * scale, 1.2, 6.5))
                ast_sd = float(np.clip(safe_std(group["AST"], DEFAULT_STAT_SDS["Assists"]) * scale, 1.0, 6.0))
                pra_sd = float(np.clip(safe_std(group["PRA"], DEFAULT_STAT_SDS["PRA"]) * scale, 4.0, 18.0))

                player_name = str(group.iloc[0]["Player"])
                rows.append({
                    "EventID": str(game.EventID), "Game": str(game.Game), "GameDate": game.GameDate,
                    "GameTimeUTC": str(game.GameTimeUTC), "PlayerID": int(player_id), "Player": player_name,
                    "NameKey": normalize_player_name(player_name), "Team": team, "Opponent": opponent,
                    "HomeAway": home_away, "Games": games_played, "Avg_MIN": avg_minutes,
                    "Proj_MIN": proj_min, "Manual_MIN": proj_min, "Usage_Adjustment": 0.0,
                    "Injury_Status": "Available", "Role": projected_role(proj_min),
                    "Confidence": confidence_label(games_played, avg_minutes),
                    "PTS_per_MIN": rates["PTS"], "REB_per_MIN": rates["REB"], "AST_per_MIN": rates["AST"],
                    "Pace_Factor": pace_factor, "PTS_Matchup_Factor": point_factor,
                    "REB_Matchup_Factor": rebound_factor, "AST_Matchup_Factor": assist_factor,
                    "Recent5_PTS": recent_pts, "Recent5_REB": recent_reb, "Recent5_AST": recent_ast,
                    "Raw_Proj_PTS": raw_pts, "Raw_Proj_REB": raw_reb, "Raw_Proj_AST": raw_ast,
                    "Raw_Proj_PRA": raw_pra, "Proj_PTS": raw_pts, "Proj_REB": raw_reb,
                    "Proj_AST": raw_ast, "Proj_PRA": raw_pra,
                    "PTS_SD": pts_sd, "REB_SD": reb_sd, "AST_SD": ast_sd, "PRA_SD": pra_sd,
                })
    board = pd.DataFrame(rows)
    if board.empty:
        return board
    return score_board(board)


def score_board(board: pd.DataFrame) -> pd.DataFrame:
    result = board.copy()
    for target, config in TARGETS.items():
        projection = config["projection"]
        score = config["score"]
        recent_col = {"Points": "Recent5_PTS", "Rebounds": "Recent5_REB", "Assists": "Recent5_AST", "PRA": None}[target]
        matchup_col = {"Points": "PTS_Matchup_Factor", "Rebounds": "REB_Matchup_Factor", "Assists": "AST_Matchup_Factor", "PRA": None}[target]
        projection_pct = percentile(result[projection])
        minutes_pct = percentile(result["Proj_MIN"])
        if recent_col:
            recent_pct = percentile(result[recent_col])
        else:
            recent_pct = (percentile(result["Recent5_PTS"]) + percentile(result["Recent5_REB"]) + percentile(result["Recent5_AST"])) / 3.0
        if matchup_col:
            matchup_pct = percentile(result[matchup_col])
        else:
            matchup_pct = (percentile(result["PTS_Matchup_Factor"]) + percentile(result["REB_Matchup_Factor"]) + percentile(result["AST_Matchup_Factor"])) / 3.0
        result[score] = 0.50 * projection_pct + 0.20 * recent_pct + 0.15 * matchup_pct + 0.15 * minutes_pct
    result["Overall_Score"] = (
        result["PTS_Score"] + result["REB_Score"] + result["AST_Score"] + result["PRA_Score"]
    ) / 4.0
    result = result.sort_values(["Overall_Score", "Proj_PRA"], ascending=False).reset_index(drop=True)
    result["Rank"] = np.arange(1, len(result) + 1)
    return result


def apply_player_overrides(board: pd.DataFrame, overrides: pd.DataFrame) -> pd.DataFrame:
    result = board.copy()
    if overrides is None or overrides.empty:
        return result
    edit = overrides[[c for c in ["PlayerID", "Manual_MIN", "Usage_Adjustment", "Injury_Status"] if c in overrides.columns]].copy()
    result = result.drop(columns=[c for c in ["Manual_MIN", "Usage_Adjustment", "Injury_Status"] if c in result.columns])
    result = result.merge(edit, on="PlayerID", how="left")
    result["Manual_MIN"] = pd.to_numeric(result["Manual_MIN"], errors="coerce").fillna(result["Proj_MIN"])
    result["Usage_Adjustment"] = pd.to_numeric(result["Usage_Adjustment"], errors="coerce").fillna(0.0)
    result["Injury_Status"] = result["Injury_Status"].fillna("Available")
    status_factor = result["Injury_Status"].map({
        "Available": 1.00, "Probable": 0.98, "Questionable": 0.92,
        "Doubtful": 0.65, "Out": 0.0,
    }).fillna(1.0)
    result["Proj_MIN"] = (result["Manual_MIN"] * status_factor).clip(0, 40)
    usage = 1.0 + result["Usage_Adjustment"].clip(-30, 50) / 100.0
    home_factor = np.where(result["HomeAway"].eq("Home"), 1.01, 0.99)
    result["Raw_Proj_PTS"] = result["Proj_MIN"] * result["PTS_per_MIN"] * result["Pace_Factor"] * result["PTS_Matchup_Factor"] * home_factor * usage
    result["Raw_Proj_REB"] = result["Proj_MIN"] * result["REB_per_MIN"] * result["Pace_Factor"] * result["REB_Matchup_Factor"] * (1.0 + (usage - 1.0) * 0.20)
    result["Raw_Proj_AST"] = result["Proj_MIN"] * result["AST_per_MIN"] * result["Pace_Factor"] * result["AST_Matchup_Factor"] * (1.0 + (usage - 1.0) * 0.60)
    result["Raw_Proj_PRA"] = result["Raw_Proj_PTS"] + result["Raw_Proj_REB"] + result["Raw_Proj_AST"]
    for target, config in TARGETS.items():
        result[config["projection"]] = result[config["raw_projection"]]
    result["Role"] = result["Proj_MIN"].map(projected_role)
    return score_board(result)


# -----------------------------------------------------------------------------
# Projection calibration
# -----------------------------------------------------------------------------


def parse_calibration_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    targets = payload.get("targets", payload)
    if not isinstance(targets, dict):
        return {}
    normalized: dict[str, dict[str, float]] = {}
    for target, values in targets.items():
        if target not in TARGETS or not isinstance(values, dict):
            continue
        slope = pd.to_numeric(pd.Series([values.get("slope")]), errors="coerce").iloc[0]
        intercept = pd.to_numeric(pd.Series([values.get("intercept")]), errors="coerce").iloc[0]
        if pd.isna(slope) or pd.isna(intercept):
            continue
        normalized[target] = {"slope": float(slope), "intercept": float(intercept), "n": int(values.get("n", 0))}
    if not normalized:
        return {}
    return {"version": str(payload.get("version", MODEL_VERSION)), "targets": normalized}


def apply_projection_calibration(board: pd.DataFrame, calibration: dict[str, Any] | None) -> pd.DataFrame:
    result = board.copy()
    payload = parse_calibration_payload(calibration or {})
    target_payload = payload.get("targets", {}) if payload else {}
    limits = {"Points": (0, 45), "Rebounds": (0, 22), "Assists": (0, 18), "PRA": (0, 70)}
    for target, config in TARGETS.items():
        raw_col = config["raw_projection"]
        projection_col = config["projection"]
        if raw_col not in result.columns:
            result[raw_col] = pd.to_numeric(result[projection_col], errors="coerce")
        coefficients = target_payload.get(target)
        if not coefficients:
            result[projection_col] = result[raw_col]
            continue
        calibrated = coefficients["intercept"] + coefficients["slope"] * pd.to_numeric(result[raw_col], errors="coerce")
        result[projection_col] = calibrated.clip(*limits[target])
    result["Calibration_Applied"] = bool(target_payload)
    result["Calibration_Targets"] = ", ".join(target_payload.keys())
    return score_board(result)


# -----------------------------------------------------------------------------
# Market application
# -----------------------------------------------------------------------------


def apply_market_quotes(board: pd.DataFrame, odds: pd.DataFrame, source_mode: str) -> pd.DataFrame:
    result = board.copy()
    for target, config in TARGETS.items():
        for column in [config["line"], config["over_odds"], config["under_odds"], config["market_prob"], config["model_over"], config["edge"]]:
            if column not in result.columns:
                result[column] = np.nan
        source_col = f"{target}_Line_Source"
        result[source_col] = ""
        if odds is None or odds.empty:
            continue
        target_odds = odds[odds["Market"].eq(config["market"])]
        quotes: dict[tuple[str, str], dict[str, Any]] = {}
        for (event_id, name_key), group in target_odds.groupby(["EventID", "NameKey"]):
            quotes[(str(event_id), str(name_key))] = choose_market_quote(group, source_mode)
        for index, row in result.iterrows():
            quote = quotes.get((str(row["EventID"]), str(row["NameKey"])))
            if not quote:
                continue
            result.at[index, config["line"]] = quote["Line"]
            result.at[index, config["over_odds"]] = quote["OverOdds"]
            result.at[index, config["under_odds"]] = quote["UnderOdds"]
            result.at[index, config["market_prob"]] = quote["MarketOverProb"]
            result.at[index, source_col] = quote["Source"]
    return calculate_prop_probabilities(result)


def calculate_prop_probabilities(board: pd.DataFrame) -> pd.DataFrame:
    result = board.copy()
    for target, config in TARGETS.items():
        over_values: list[float] = []
        under_values: list[float] = []
        push_values: list[float] = []
        for mean, sd, line in zip(result[config["projection"]], result[config["sd"]], result[config["line"]]):
            over, under, push = prop_probabilities(float(mean), float(sd), float(line)) if pd.notna(line) else (np.nan, np.nan, np.nan)
            over_values.append(over); under_values.append(under); push_values.append(push)
        result[config["model_over"]] = over_values
        result[f"{target}_Model_Under_Prob"] = under_values
        result[f"{target}_Push_Prob"] = push_values
        result[config["edge"]] = pd.to_numeric(result[config["model_over"]], errors="coerce") - pd.to_numeric(result[config["market_prob"]], errors="coerce")
        result[f"{target}_Projection_Edge"] = pd.to_numeric(result[config["projection"]], errors="coerce") - pd.to_numeric(result[config["line"]], errors="coerce")
    return result


# -----------------------------------------------------------------------------
# Backtesting
# -----------------------------------------------------------------------------


def build_snapshot(board: pd.DataFrame, slate_date: date) -> pd.DataFrame:
    snapshot = board.copy()
    snapshot["SlateDate"] = slate_date
    snapshot["GeneratedAtUTC"] = pd.Timestamp.now(tz="UTC").isoformat()
    snapshot["ModelVersion"] = MODEL_VERSION
    for target, config in TARGETS.items():
        snapshot[config["actual"]] = np.nan
    snapshot["ResultStatus"] = ""
    preferred = [
        "SlateDate", "GameTimeUTC", "GeneratedAtUTC", "ModelVersion", "EventID", "PlayerID", "Player",
        "Team", "Opponent", "HomeAway", "Game", "Games", "Avg_MIN", "Proj_MIN", "Manual_MIN",
        "Usage_Adjustment", "Injury_Status", "Role", "Confidence", "Calibration_Applied", "Calibration_Targets",
    ]
    for target, config in TARGETS.items():
        preferred += [
            config["raw_projection"], config["projection"], config["sd"], config["score"], config["line"],
            config["over_odds"], config["under_odds"], config["market_prob"], config["model_over"],
            f"{target}_Model_Under_Prob", f"{target}_Push_Prob", config["edge"], f"{target}_Projection_Edge",
            config["actual"],
        ]
    preferred += ["Overall_Score", "ResultStatus"]
    existing = [column for column in preferred if column in snapshot.columns]
    extras = [column for column in snapshot.columns if column not in existing]
    return snapshot[existing + extras]


def normalize_history(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame()
    result = history.copy()
    result["SlateDate"] = pd.to_datetime(result.get("SlateDate"), errors="coerce").dt.date
    result["GameTimeUTC"] = pd.to_datetime(result.get("GameTimeUTC"), utc=True, errors="coerce")
    result["GeneratedAtUTC"] = pd.to_datetime(result.get("GeneratedAtUTC"), utc=True, errors="coerce")
    for column in ["PlayerID", "Proj_MIN", "Overall_Score"]:
        if column not in result.columns:
            result[column] = np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")
    for target, config in TARGETS.items():
        for column in [config["raw_projection"], config["projection"], config["sd"], config["score"], config["line"], config["model_over"], config["market_prob"], config["actual"]]:
            if column not in result.columns:
                result[column] = np.nan
            result[column] = pd.to_numeric(result[column], errors="coerce")
    result["SnapshotAfterStart"] = (
        result["GeneratedAtUTC"].notna() & result["GameTimeUTC"].notna()
        & result["GeneratedAtUTC"].gt(result["GameTimeUTC"])
    )
    if "ResultStatus" not in result.columns:
        result["ResultStatus"] = ""
    return result


def combine_history_uploads(uploaded_files: list[Any]) -> tuple[pd.DataFrame, list[str]]:
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for uploaded in uploaded_files or []:
        try:
            frames.append(pd.read_csv(uploaded))
        except Exception as exc:
            errors.append(f"{getattr(uploaded, 'name', 'file')}: {type(exc).__name__}: {exc}")
    if not frames:
        return pd.DataFrame(), errors
    return normalize_history(pd.concat(frames, ignore_index=True)), errors


def append_snapshot(history: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([history, snapshot], ignore_index=True) if history is not None and not history.empty else snapshot.copy()
    return normalize_history(combined)


def fill_actual_results(history: pd.DataFrame, player_logs: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    result = normalize_history(history)
    if result.empty or player_logs.empty:
        return result, {"matched": 0, "unmatched": len(result)}
    lookup = player_logs.copy()
    lookup["SlateDate"] = pd.to_datetime(lookup["GameDate"], errors="coerce").dt.date
    actual_cols = lookup[["PlayerID", "SlateDate", "Team", "PTS", "REB", "AST", "PRA"]].copy()
    actual_cols = actual_cols.rename(columns={"PTS": "_PTS", "REB": "_REB", "AST": "_AST", "PRA": "_PRA", "Team": "_Team"})
    result = result.merge(actual_cols, on=["PlayerID", "SlateDate"], how="left")
    matched = result["_PTS"].notna()
    result.loc[matched, "Actual_PTS"] = result.loc[matched, "_PTS"]
    result.loc[matched, "Actual_REB"] = result.loc[matched, "_REB"]
    result.loc[matched, "Actual_AST"] = result.loc[matched, "_AST"]
    result.loc[matched, "Actual_PRA"] = result.loc[matched, "_PRA"]
    result.loc[matched, "ResultStatus"] = "Final"
    result = result.drop(columns=["_PTS", "_REB", "_AST", "_PRA", "_Team"], errors="ignore")
    return normalize_history(result), {"matched": int(matched.sum()), "unmatched": int((~matched).sum())}


def latest_pregame_history(history: pd.DataFrame) -> pd.DataFrame:
    frame = normalize_history(history)
    if frame.empty:
        return frame
    frame = frame[~frame["SnapshotAfterStart"].fillna(False)].copy()
    frame = frame.sort_values("GeneratedAtUTC")
    return frame.drop_duplicates(["SlateDate", "EventID", "PlayerID"], keep="last")


def projection_metrics(history: pd.DataFrame, target: str) -> dict[str, float]:
    config = TARGETS[target]
    frame = history[[config["projection"], config["actual"]]].dropna().copy()
    if frame.empty:
        return {"N": 0, "MAE": np.nan, "RMSE": np.nan, "Bias": np.nan, "Correlation": np.nan, "Within_2": np.nan, "Within_3": np.nan, "Within_5": np.nan}
    prediction = frame[config["projection"]].to_numpy(float)
    actual = frame[config["actual"]].to_numpy(float)
    error = prediction - actual
    correlation = float(np.corrcoef(prediction, actual)[0, 1]) if len(frame) >= 3 and np.std(prediction) > 0 and np.std(actual) > 0 else np.nan
    return {
        "N": int(len(frame)), "MAE": float(np.mean(np.abs(error))), "RMSE": float(np.sqrt(np.mean(error ** 2))),
        "Bias": float(np.mean(error)), "Correlation": correlation,
        "Within_2": float(np.mean(np.abs(error) <= 2)), "Within_3": float(np.mean(np.abs(error) <= 3)),
        "Within_5": float(np.mean(np.abs(error) <= 5)),
    }


def score_bucket_table(history: pd.DataFrame, target: str) -> pd.DataFrame:
    config = TARGETS[target]
    columns = [config["score"], config["projection"], config["actual"], config["line"]]
    frame = history[[c for c in columns if c in history.columns]].copy()
    for column in frame.columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=[config["score"], config["actual"]])
    if frame.empty:
        return pd.DataFrame()
    bins = [-0.001, 30, 45, 55, 70, 85, 100.001]
    labels = ["0–29", "30–44", "45–54", "55–69", "70–84", "85–100"]
    frame["Score_Bucket"] = pd.cut(frame[config["score"]], bins=bins, labels=labels, right=False)
    if config["line"] in frame.columns:
        frame["Over"] = np.where(frame[config["line"]].notna(), frame[config["actual"]] > frame[config["line"]], np.nan)
    else:
        frame["Over"] = np.nan
    return frame.groupby("Score_Bucket", observed=False).agg(
        Sample=(config["actual"], "size"), Average_Projection=(config["projection"], "mean"),
        Average_Actual=(config["actual"], "mean"), Average_Score=(config["score"], "mean"), Over_Rate=("Over", "mean"),
    ).reset_index()


def probability_records(history: pd.DataFrame, target: str) -> pd.DataFrame:
    config = TARGETS[target]
    required = [config["model_over"], config["actual"], config["line"]]
    if any(column not in history.columns for column in required):
        return pd.DataFrame()
    frame = history[required].copy()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna()
    if frame.empty:
        return frame
    rows = []
    for row in frame.itertuples(index=False):
        over_prob, actual, line = map(float, row)
        if actual == line:
            continue
        under_prob = 1.0 - over_prob
        preferred_side = "Over" if over_prob >= under_prob else "Under"
        preferred_probability = max(over_prob, under_prob)
        outcome = float(actual > line) if preferred_side == "Over" else float(actual < line)
        rows.append({"Preferred_Side": preferred_side, "Preferred_Probability": preferred_probability, "Outcome": outcome})
    return pd.DataFrame(rows)


def probability_calibration_table(records: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    if records is None or records.empty:
        return pd.DataFrame(), np.nan
    bins = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 1.001]
    labels = ["50–54%", "55–59%", "60–64%", "65–69%", "70–74%", "75%+"]
    frame = records.copy()
    frame["Probability_Bucket"] = pd.cut(frame["Preferred_Probability"], bins=bins, labels=labels, right=False)
    grouped = frame.groupby("Probability_Bucket", observed=False).agg(
        Sample=("Outcome", "size"), Average_Probability=("Preferred_Probability", "mean"), Actual_Win_Rate=("Outcome", "mean"),
    ).reset_index()
    grouped["Calibration_Gap"] = grouped["Actual_Win_Rate"] - grouped["Average_Probability"]
    brier = float(np.mean((frame["Preferred_Probability"] - frame["Outcome"]) ** 2))
    return grouped, brier


def fit_linear_calibration(history: pd.DataFrame, target: str, min_rows: int = 50) -> dict[str, Any]:
    config = TARGETS[target]
    projection_col = config["raw_projection"] if config["raw_projection"] in history.columns else config["projection"]
    frame = history[[projection_col, config["actual"], "SlateDate"]].copy()
    frame[projection_col] = pd.to_numeric(frame[projection_col], errors="coerce")
    frame[config["actual"]] = pd.to_numeric(frame[config["actual"]], errors="coerce")
    frame["SlateDate"] = pd.to_datetime(frame["SlateDate"], errors="coerce")
    frame = frame.dropna().sort_values("SlateDate")
    if len(frame) < min_rows or frame[projection_col].nunique() < 3:
        return {"ok": False, "n": len(frame), "message": f"Need at least {min_rows} completed player-games."}
    x = frame[projection_col].to_numpy(float)
    y = frame[config["actual"]].to_numpy(float)
    slope, intercept = np.polyfit(x, y, 1)
    slope = float(np.clip(slope, 0.25, 1.75))
    intercept = float(intercept)
    calibrated = intercept + slope * x
    raw_mae = float(np.mean(np.abs(x - y)))
    calibrated_mae = float(np.mean(np.abs(calibrated - y)))
    split = max(int(len(frame) * 0.70), 1)
    train, test = frame.iloc[:split], frame.iloc[split:]
    holdout_raw = holdout_cal = np.nan
    if len(train) >= 30 and len(test) >= 20 and train[projection_col].nunique() >= 3:
        train_slope, train_intercept = np.polyfit(train[projection_col].to_numpy(float), train[config["actual"]].to_numpy(float), 1)
        train_slope = float(np.clip(train_slope, 0.25, 1.75))
        tx = test[projection_col].to_numpy(float)
        ty = test[config["actual"]].to_numpy(float)
        holdout_raw = float(np.mean(np.abs(tx - ty)))
        holdout_cal = float(np.mean(np.abs((train_intercept + train_slope * tx) - ty)))
    return {
        "ok": True, "n": int(len(frame)), "slope": slope, "intercept": intercept,
        "raw_mae": raw_mae, "calibrated_mae": calibrated_mae, "holdout_n": int(len(test)),
        "holdout_raw_mae": holdout_raw, "holdout_calibrated_mae": holdout_cal,
    }


def make_calibration_bundle(history: pd.DataFrame, selected_targets: list[str], min_rows: int) -> tuple[dict[str, Any], pd.DataFrame]:
    bundle = {"version": MODEL_VERSION, "created_at_utc": pd.Timestamp.now(tz="UTC").isoformat(), "targets": {}}
    rows = []
    for target in TARGETS:
        fit = fit_linear_calibration(history, target, min_rows)
        rows.append({"Target": target, **fit})
        if target in selected_targets and fit.get("ok"):
            bundle["targets"][target] = {"slope": fit["slope"], "intercept": fit["intercept"], "n": fit["n"]}
    return bundle, pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# Data import helpers
# -----------------------------------------------------------------------------


def read_uploaded_csv(uploaded: Any, normalizer) -> pd.DataFrame:
    if uploaded is None:
        return pd.DataFrame()
    return normalizer(pd.read_csv(uploaded))


def manual_games_frame(slate_date: date, text: str) -> pd.DataFrame:
    rows = []
    for index, raw in enumerate((text or "").splitlines()):
        cleaned = raw.strip()
        if not cleaned:
            continue
        match = re.split(r"\s+@\s+|\s+vs\.?\s+", cleaned, maxsplit=1, flags=re.IGNORECASE)
        if len(match) != 2:
            continue
        away, home = normalize_team(match[0]), normalize_team(match[1])
        if away in TEAM_NAMES and home in TEAM_NAMES:
            rows.append({
                "EventID": f"manual-{slate_date}-{index}-{away}-{home}", "GameDate": slate_date,
                "GameTimeUTC": "", "Away": away, "Home": home, "Game": f"{away} @ {home}", "Source": "Manual",
            })
    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------

inject_css()
st.markdown('<div class="app-kicker">WNBA PLAYER PROPS LAB</div>', unsafe_allow_html=True)
st.title("🏀 WNBA Points · Rebounds · Assists · PRA")
st.caption("Minutes-driven projections, target-specific scores, market comparison and built-in backtesting.")

with st.sidebar:
    st.header("Slate & data")
    current_year = date.today().year
    season = st.selectbox("Season", [str(year) for year in range(current_year, 2019, -1)], index=0)
    season_type = st.selectbox("Season type", ["Regular Season", "Playoffs"])
    slate_date = st.date_input("Slate date", value=date.today())
    st.caption("Official WNBA Stats is the primary source. CSV uploads are available when the endpoint is blocked.")

    player_csv = st.file_uploader("Optional player game-log CSV", type=["csv"], key="wnba_player_logs")
    team_csv = st.file_uploader("Optional team game-log CSV", type=["csv"], key="wnba_team_logs")

    st.divider()
    st.subheader("Calibration")
    calibration_upload = st.file_uploader("Optional calibration JSON", type=["json"], key="wnba_calibration")
    uploaded_calibration: dict[str, Any] = {}
    if calibration_upload is not None:
        try:
            uploaded_calibration = parse_calibration_payload(json.loads(calibration_upload.getvalue().decode("utf-8-sig")))
            if uploaded_calibration:
                st.success("Calibration loaded.")
        except Exception as exc:
            st.warning(f"Calibration could not be read: {exc}")
    session_calibration = parse_calibration_payload(st.session_state.get("wnba_projection_calibration", {}))
    active_calibration = uploaded_calibration or session_calibration

    st.divider()
    st.subheader("Automatic odds")
    odds_api_key = st.text_input("The Odds API key (session only)", type="password")
    odds_source_mode = st.selectbox("Line source", ["Consensus"] + list(BOOKMAKER_LABELS.values()))

    if st.button("Clear cached data", width="stretch"):
        st.cache_data.clear()
        for key in ["wnba_board", "wnba_games", "wnba_odds", "wnba_player_logs_data", "wnba_team_logs_data"]:
            st.session_state.pop(key, None)
        st.rerun()

# Load logs
load_col, schedule_col = st.columns([1, 1])
with load_col:
    load_data = st.button("Load / refresh WNBA stats", type="primary", width="stretch")
with schedule_col:
    load_schedule = st.button("Load / refresh slate", width="stretch")

if load_data or "wnba_player_logs_data" not in st.session_state:
    try:
        if player_csv is not None and team_csv is not None:
            player_logs = read_uploaded_csv(player_csv, normalize_player_logs)
            team_logs = read_uploaded_csv(team_csv, normalize_team_logs)
        else:
            with st.spinner("Loading official WNBA player and team game logs…"):
                player_logs, team_logs = load_official_game_logs(season, season_type)
        st.session_state["wnba_player_logs_data"] = player_logs
        st.session_state["wnba_team_logs_data"] = team_logs
    except Exception as exc:
        st.error(f"Could not load WNBA stats: {type(exc).__name__}: {exc}")
        st.info("Upload exported player and team game-log CSV files in the sidebar as a fallback.")

player_logs = st.session_state.get("wnba_player_logs_data", pd.DataFrame())
team_logs = st.session_state.get("wnba_team_logs_data", pd.DataFrame())

if load_schedule or "wnba_games" not in st.session_state:
    games, schedule_errors = fetch_official_schedule(slate_date)
    if games.empty and odds_api_key:
        try:
            odds_events, event_meta = fetch_odds_events(odds_api_key)
            if not odds_events.empty:
                odds_events["GameDate"] = pd.to_datetime(odds_events["GameTimeUTC"], utc=True, errors="coerce").dt.date
                games = odds_events[odds_events["GameDate"].eq(slate_date)].copy()
        except Exception as exc:
            schedule_errors.append(f"Odds events: {type(exc).__name__}: {exc}")
    st.session_state["wnba_games"] = games
    st.session_state["wnba_schedule_errors"] = schedule_errors

games = st.session_state.get("wnba_games", pd.DataFrame())

with st.expander("Slate games and manual fallback", expanded=games.empty):
    if not games.empty:
        st.dataframe(games[[c for c in ["Game", "GameTimeUTC", "Source"] if c in games.columns]], hide_index=True, width="stretch")
    errors = st.session_state.get("wnba_schedule_errors", [])
    if errors:
        st.caption("Schedule connection details: " + " | ".join(errors[:3]))
    manual_text = st.text_area("Manual games — one per line, e.g. IND @ NYL", value="", height=100)
    if st.button("Use manual games"):
        manual = manual_games_frame(slate_date, manual_text)
        if manual.empty:
            st.warning("No valid games were found. Use WNBA abbreviations such as IND @ NYL.")
        else:
            st.session_state["wnba_games"] = manual
            st.rerun()

if not games.empty:
    game_labels = games["Game"].tolist()
    selected_games = st.multiselect("Games to model", game_labels, default=game_labels)
    selected_game_frame = games[games["Game"].isin(selected_games)].copy()
else:
    selected_game_frame = pd.DataFrame()

build_board_button = st.button("Build WNBA player board", type="primary", width="stretch")
if build_board_button:
    if player_logs.empty or selected_game_frame.empty:
        st.warning("Load stats and select at least one game first.")
    else:
        with st.spinner("Building minutes, rate and matchup projections…"):
            raw_board = build_base_board(player_logs, team_logs, selected_game_frame)
            calibrated = apply_projection_calibration(raw_board, active_calibration)
            st.session_state["wnba_board"] = calibrated
            st.session_state["wnba_base_board"] = raw_board
            st.session_state["wnba_board_date"] = slate_date

board = st.session_state.get("wnba_board", pd.DataFrame())
if board.empty:
    st.info("Load the slate and press **Build WNBA player board**.")
    st.stop()

# Fetch props after board exists
with st.expander("Automatic prop-line fetch", expanded=False):
    market_options = {target: config["market"] for target, config in TARGETS.items()}
    selected_markets = st.multiselect("Markets", list(market_options), default=list(market_options))
    odds_games = st.multiselect("Events to query", board["Game"].drop_duplicates().tolist(), default=board["Game"].drop_duplicates().tolist())
    st.caption("Player props are event-level markets. Querying fewer games and markets preserves API credits.")
    if st.button("Fetch selected WNBA prop lines", width="stretch"):
        if not odds_api_key:
            st.error("Enter your temporary The Odds API key in the sidebar.")
        else:
            try:
                events, meta = fetch_odds_events(odds_api_key)
                event_lookup = events.set_index("Game")["EventID"].to_dict() if not events.empty else {}
                frames = []
                last_meta: dict[str, str] = meta
                for game in odds_games:
                    event_id = event_lookup.get(game)
                    if not event_id:
                        continue
                    frame, last_meta = fetch_event_props(
                        odds_api_key, str(event_id), tuple(market_options[target] for target in selected_markets)
                    )
                    # The official schedule and The Odds API use different event IDs.
                    # Re-key the quote rows to the board's event ID so player lines merge correctly.
                    board_event_ids = board.loc[board["Game"].eq(game), "EventID"].dropna().astype(str).unique()
                    if not frame.empty and len(board_event_ids):
                        frame["EventID"] = board_event_ids[0]
                    frames.append(frame)
                st.session_state["wnba_odds"] = combine_odds_frames(frames)
                st.session_state["wnba_odds_meta"] = last_meta
                st.rerun()
            except Exception as exc:
                st.error(f"Odds fetch failed: {type(exc).__name__}: {exc}")
    meta = st.session_state.get("wnba_odds_meta", {})
    if meta:
        st.caption(f"API requests used: {meta.get('used', '—')} · remaining: {meta.get('remaining', '—')}")

odds = st.session_state.get("wnba_odds", pd.DataFrame())
board = apply_market_quotes(board, odds, odds_source_mode)
st.session_state["wnba_board"] = board

# Leaders
leaders = st.columns(5)
for column, target in zip(leaders[:4], TARGETS):
    config = TARGETS[target]
    top = board.sort_values(config["projection"], ascending=False).iloc[0]
    column.metric(f"Top {target}", top["Player"], f"{top[config['projection']]:.1f}")
leaders[4].metric("Players modeled", f"{len(board):,}", f"{board['Game'].nunique()} games")

(
    board_tab, points_tab, rebounds_tab, assists_tab, pra_tab, lines_tab,
    minutes_tab, backtest_tab, notes_tab,
) = st.tabs([
    "Player board", "Points", "Rebounds", "Assists", "PRA", "Line comparison",
    "Minutes & injuries", "Backtest & calibration", "Model notes",
])


def render_target_table(target: str) -> None:
    config = TARGETS[target]
    columns = [
        "Rank", "Player", "Team", "Opponent", "HomeAway", "Proj_MIN", config["projection"],
        config["score"], config["sd"], config["line"], config["model_over"], config["market_prob"],
        config["edge"], f"{target}_Projection_Edge", "Confidence", "Injury_Status", "Role",
    ]
    view = board[[c for c in columns if c in board.columns]].copy()
    rename = {
        "Proj_MIN": "Proj Min", config["projection"]: f"Proj {target}", config["score"]: "Target Score",
        config["sd"]: "SD", config["line"]: "Line", config["model_over"]: "Model Over",
        config["market_prob"]: "No-vig Market Over", config["edge"]: "Probability Edge",
        f"{target}_Projection_Edge": "Projection Edge", "Injury_Status": "Status",
    }
    view = view.rename(columns=rename)
    style_cols = [c for c in [f"Proj {target}", "Target Score", "Model Over", "Probability Edge"] if c in view.columns]
    style = view.style
    if style_cols:
        style = style.background_gradient(cmap="RdYlGn", subset=style_cols, axis=0)
    st.dataframe(style.format({
        "Proj Min": "{:.1f}", f"Proj {target}": "{:.2f}", "Target Score": "{:.1f}", "SD": "{:.2f}",
        "Line": "{:.1f}", "Model Over": "{:.1%}", "No-vig Market Over": "{:.1%}",
        "Probability Edge": "{:+.1%}", "Projection Edge": "{:+.2f}",
    }), hide_index=True, width="stretch", height=650)


with board_tab:
    columns = [
        "Rank", "Player", "Team", "Opponent", "HomeAway", "Proj_MIN", "Proj_PTS", "Proj_REB",
        "Proj_AST", "Proj_PRA", "PTS_Score", "REB_Score", "AST_Score", "PRA_Score", "Overall_Score",
        "Confidence", "Injury_Status", "Role",
    ]
    view = board[[c for c in columns if c in board.columns]].copy().rename(columns={
        "Proj_MIN": "Proj Min", "Proj_PTS": "PTS", "Proj_REB": "REB", "Proj_AST": "AST", "Proj_PRA": "PRA",
        "PTS_Score": "PTS Score", "REB_Score": "REB Score", "AST_Score": "AST Score",
        "PRA_Score": "PRA Score", "Overall_Score": "Overall", "Injury_Status": "Status",
    })
    gradient = ["PTS", "REB", "AST", "PRA", "PTS Score", "REB Score", "AST Score", "PRA Score", "Overall"]
    st.dataframe(
        view.style.background_gradient(cmap="RdYlGn", subset=[c for c in gradient if c in view.columns], axis=0).format({
            "Proj Min": "{:.1f}", "PTS": "{:.2f}", "REB": "{:.2f}", "AST": "{:.2f}", "PRA": "{:.2f}",
            "PTS Score": "{:.1f}", "REB Score": "{:.1f}", "AST Score": "{:.1f}", "PRA Score": "{:.1f}", "Overall": "{:.1f}",
        }), hide_index=True, width="stretch", height=680,
    )
    st.caption("Heatmap text color is automatic contrast only; it is not an additional signal.")

with points_tab:
    render_target_table("Points")
with rebounds_tab:
    render_target_table("Rebounds")
with assists_tab:
    render_target_table("Assists")
with pra_tab:
    render_target_table("PRA")

with lines_tab:
    target = st.selectbox("Prop target", list(TARGETS), key="wnba_line_target")
    config = TARGETS[target]
    line_columns = [
        "PlayerID", "Player", "Team", "Opponent", "Proj_MIN", config["projection"], config["sd"],
        config["line"], config["over_odds"], config["under_odds"], config["market_prob"],
    ]
    editor = board[[c for c in line_columns if c in board.columns]].copy().rename(columns={
        "Proj_MIN": "Proj Min", config["projection"]: "Projection", config["sd"]: "SD",
        config["line"]: "Line", config["over_odds"]: "Over Odds", config["under_odds"]: "Under Odds",
        config["market_prob"]: "No-vig Market Over",
    })
    edited = st.data_editor(
        editor, hide_index=True, width="stretch", height=620,
        disabled=[c for c in editor.columns if c not in ["Line", "Over Odds", "Under Odds", "No-vig Market Over"]],
        column_config={
            "Line": st.column_config.NumberColumn(step=0.5, format="%.1f"),
            "Over Odds": st.column_config.NumberColumn(step=1),
            "Under Odds": st.column_config.NumberColumn(step=1),
            "No-vig Market Over": st.column_config.NumberColumn(min_value=0.0, max_value=1.0, step=0.01, format="%.3f"),
        }, key=f"wnba_line_editor_{target}",
    )
    if st.button("Apply line edits", key=f"apply_line_edits_{target}"):
        updates = edited.rename(columns={
            "Line": config["line"], "Over Odds": config["over_odds"], "Under Odds": config["under_odds"],
            "No-vig Market Over": config["market_prob"],
        })
        update_cols = ["PlayerID", config["line"], config["over_odds"], config["under_odds"], config["market_prob"]]
        refreshed = board.drop(columns=update_cols[1:], errors="ignore").merge(updates[update_cols], on="PlayerID", how="left")
        refreshed[config["market_prob"]] = refreshed[config["market_prob"]].fillna(
            refreshed.apply(lambda row: no_vig_over_probability(row[config["over_odds"]], row[config["under_odds"]]), axis=1)
        )
        st.session_state["wnba_board"] = calculate_prop_probabilities(refreshed)
        st.rerun()

with minutes_tab:
    st.markdown("### Projected minutes and availability")
    st.caption("Minutes are the most important editable input. Usage adjustment changes scoring/assist opportunity more than rebounds.")
    editor_columns = ["PlayerID", "Player", "Team", "Opponent", "Avg_MIN", "Manual_MIN", "Usage_Adjustment", "Injury_Status", "Confidence"]
    minute_editor = board[[c for c in editor_columns if c in board.columns]].copy().rename(columns={
        "Avg_MIN": "Season Avg Min", "Manual_MIN": "Projected Min", "Usage_Adjustment": "Usage Adj %",
        "Injury_Status": "Status",
    })
    minute_edits = st.data_editor(
        minute_editor, hide_index=True, width="stretch", height=650,
        disabled=[c for c in minute_editor.columns if c not in ["Projected Min", "Usage Adj %", "Status"]],
        column_config={
            "Projected Min": st.column_config.NumberColumn(min_value=0.0, max_value=40.0, step=0.5, format="%.1f"),
            "Usage Adj %": st.column_config.NumberColumn(min_value=-30.0, max_value=50.0, step=1.0, format="%.0f"),
            "Status": st.column_config.SelectboxColumn(options=["Available", "Probable", "Questionable", "Doubtful", "Out"]),
        }, key="wnba_minutes_editor",
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Apply minute / injury adjustments", type="primary", width="stretch"):
            overrides = minute_edits.rename(columns={"Projected Min": "Manual_MIN", "Usage Adj %": "Usage_Adjustment", "Status": "Injury_Status"})
            base = st.session_state.get("wnba_base_board", board)
            adjusted = apply_player_overrides(base, overrides)
            adjusted = apply_projection_calibration(adjusted, active_calibration)
            adjusted = apply_market_quotes(adjusted, odds, odds_source_mode)
            st.session_state["wnba_board"] = adjusted
            st.rerun()
    with c2:
        st.link_button("Open official WNBA injury report", "https://www.wnba.com/wnba-injury-report", width="stretch")

with backtest_tab:
    st.markdown("### Save the current slate before tipoff")
    snapshot = build_snapshot(board, slate_date)
    st.download_button(
        "Download pregame snapshot CSV", snapshot.to_csv(index=False).encode("utf-8"),
        file_name=f"wnba_props_snapshot_{slate_date}.csv", mime="text/csv", width="stretch",
    )
    uploads = st.file_uploader("Upload snapshot or master-history CSV files", type=["csv"], accept_multiple_files=True, key="wnba_history_upload")
    if uploads:
        signature = tuple((item.name, len(item.getvalue())) for item in uploads)
        if st.session_state.get("wnba_history_signature") != signature:
            history, history_errors = combine_history_uploads(uploads)
            st.session_state["wnba_history"] = history
            st.session_state["wnba_history_errors"] = history_errors
            st.session_state["wnba_history_signature"] = signature
    history = normalize_history(st.session_state.get("wnba_history", pd.DataFrame()))
    action1, action2 = st.columns(2)
    with action1:
        if st.button("Add current snapshot to session master", width="stretch"):
            history = append_snapshot(history, snapshot)
            st.session_state["wnba_history"] = history
            st.success("Snapshot added. Download the updated master before the session resets.")
    with action2:
        if st.button("Fill official completed-game results", type="primary", width="stretch"):
            history, result_summary = fill_actual_results(history, player_logs)
            st.session_state["wnba_history"] = history
            st.session_state["wnba_result_summary"] = result_summary
    if not history.empty:
        st.download_button(
            "Download updated master history", history.to_csv(index=False).encode("utf-8"),
            file_name="wnba_props_master_history.csv", mime="text/csv", width="stretch",
        )
    summary = st.session_state.get("wnba_result_summary")
    if summary:
        st.caption(f"Results matched: {summary.get('matched', 0)} · unmatched: {summary.get('unmatched', 0)}")

    analysis = latest_pregame_history(history)
    completed = analysis[analysis[[config["actual"] for config in TARGETS.values()]].notna().any(axis=1)].copy() if not analysis.empty else pd.DataFrame()
    metrics = st.columns(4)
    metrics[0].metric("Master rows", f"{len(history):,}")
    metrics[1].metric("Analysis rows", f"{len(analysis):,}")
    metrics[2].metric("Completed outcomes", f"{len(completed):,}")
    metrics[3].metric("After-start rows", f"{int(history.get('SnapshotAfterStart', pd.Series(dtype=bool)).fillna(False).sum()):,}")

    if not completed.empty:
        st.markdown("### Projection accuracy")
        metrics_table = pd.DataFrame([{"Target": target, **projection_metrics(completed, target)} for target in TARGETS])
        st.dataframe(metrics_table.style.format({
            "MAE": "{:.3f}", "RMSE": "{:.3f}", "Bias": "{:+.3f}", "Correlation": "{:.3f}",
            "Within_2": "{:.1%}", "Within_3": "{:.1%}", "Within_5": "{:.1%}",
        }), hide_index=True, width="stretch")

        bucket_target = st.selectbox("Score target", list(TARGETS), key="wnba_score_bucket_target")
        st.markdown("### Score buckets")
        bucket_table = score_bucket_table(completed, bucket_target)
        st.dataframe(bucket_table.style.format({
            "Average_Projection": "{:.2f}", "Average_Actual": "{:.2f}", "Average_Score": "{:.1f}", "Over_Rate": "{:.1%}",
        }), hide_index=True, width="stretch")

        probability_target = st.selectbox("Probability target", list(TARGETS), key="wnba_probability_target")
        records = probability_records(completed, probability_target)
        if not records.empty:
            table, brier = probability_calibration_table(records)
            p1, p2 = st.columns(2)
            p1.metric("Decisions", f"{len(records):,}")
            p2.metric("Brier score", f"{brier:.3f}")
            st.dataframe(table.style.format({
                "Average_Probability": "{:.1%}", "Actual_Win_Rate": "{:.1%}", "Calibration_Gap": "{:+.1%}",
            }), hide_index=True, width="stretch")

        st.markdown("### Fit target-specific projection calibration")
        min_rows = st.number_input("Minimum completed player-games", min_value=30, max_value=1000, value=75, step=25)
        selected_fit_targets = st.multiselect("Targets to include if applied", list(TARGETS), default=[])
        bundle, fit_table = make_calibration_bundle(completed, selected_fit_targets, int(min_rows))
        st.dataframe(fit_table.style.format({
            "slope": "{:.4f}", "intercept": "{:+.4f}", "raw_mae": "{:.3f}", "calibrated_mae": "{:.3f}",
            "holdout_raw_mae": "{:.3f}", "holdout_calibrated_mae": "{:.3f}",
        }), hide_index=True, width="stretch")
        st.caption("Apply a target only when the chronological holdout MAE improves—not merely the full-sample MAE.")
        if bundle.get("targets"):
            c1, c2 = st.columns(2)
            with c1:
                st.download_button(
                    "Download selected calibration JSON", json.dumps(bundle, indent=2).encode("utf-8"),
                    file_name="wnba_projection_calibration.json", mime="application/json", width="stretch",
                )
            with c2:
                if st.button("Apply selected calibration to current board", width="stretch"):
                    st.session_state["wnba_projection_calibration"] = bundle
                    base = st.session_state.get("wnba_base_board", board)
                    calibrated = apply_projection_calibration(base, bundle)
                    calibrated = apply_market_quotes(calibrated, odds, odds_source_mode)
                    st.session_state["wnba_board"] = calibrated
                    st.rerun()

        st.markdown("### Segment checks")
        segment = st.selectbox("Break results down by", ["Confidence", "Role", "HomeAway", "Injury_Status"])
        segment_rows = []
        if segment in completed.columns:
            for group_name, group in completed.groupby(segment, dropna=False):
                for target in TARGETS:
                    segment_rows.append({"Group": str(group_name), "Target": target, **projection_metrics(group, target)})
        if segment_rows:
            st.dataframe(pd.DataFrame(segment_rows).style.format({
                "MAE": "{:.3f}", "RMSE": "{:.3f}", "Bias": "{:+.3f}", "Correlation": "{:.3f}",
                "Within_2": "{:.1%}", "Within_3": "{:.1%}", "Within_5": "{:.1%}",
            }), hide_index=True, width="stretch")
    else:
        st.info("Save pregame snapshots and fill final results to unlock backtesting and calibration.")

with notes_tab:
    st.markdown(
        """
        ### Reading the WNBA board

        - **Projected minutes** is the central input. Review official injuries and edit minutes shortly before tipoff.
        - **Points, rebounds and assists** use blended season/recent per-minute production, projected minutes, pace and opponent allowances.
        - **PRA** is the sum of the three component projections; its uncertainty uses the player's historical PRA variation.
        - **Target Score** is a 0–100 slate-relative comparison score, not a literal probability.
        - **Model Over probability** uses the projection and player-specific game-to-game variance with a continuity correction for integer lines.
        - **Probability Edge** compares model over probability with the no-vig market over probability.
        - **Confidence** reflects game/minute sample depth, not certainty that the prop will win.
        - Save snapshots before tipoff. Backtesting automatically excludes snapshots generated after the recorded game start.
        - Calibrate each target separately and only when chronological holdout error improves.

        Data sources: official WNBA Stats for completed game logs; official WNBA schedule/injury pages when available; The Odds API for optional prop lines.
        """
    )
