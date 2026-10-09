from __future__ import annotations

COOCHIE_BOARD_BUILD = "v26-streamlit-cloud"

"""
WNBA Player Props Lab

A standalone Streamlit dashboard for WNBA points, rebounds, assists and PRA.
Primary data path: SportsDataverse release with WNBA/ESPN best-effort fallbacks.
Optional market sources: PrizePicks import and The Odds API.

Install:
    pip install streamlit pandas numpy requests pillow pytesseract
Run:
    streamlit run wnba_player_props_dashboard.py
"""

import io
import os
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup, Comment


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

MODEL_VERSION = "wnba-props-lab-v1"
WNBA_LEAGUE_ID = "10"
WNBA_STATS_BASE = "https://stats.wnba.com/stats"
WNBA_STATS_BASES = ["https://stats.wnba.com/stats", "https://stats.nba.com/stats"]
ESPN_WNBA_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
EASTERN_TZ = ZoneInfo("America/New_York")
THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
DEFAULT_THE_ODDS_SPORT_KEY = "basketball_wnba"
BALLDONTLIE_WNBA_BASE = "https://api.balldontlie.io/wnba/v1"
BREF_WNBA_BASE = "https://www.basketball-reference.com/wnba"
SDV_WNBA_PLAYER_BOX_RAW = "https://raw.githubusercontent.com/sportsdataverse/wehoop-wnba-data/main/wnba/player_box/parquet/player_box_{season}.parquet"
PRIZEPICKS_WNBA_LEAGUE_ID = "3"
PRIZEPICKS_API_BASES = ["https://partner-api.prizepicks.com", "https://api.prizepicks.com"]
# Streamlit Cloud has ephemeral local storage. This cache is best-effort only and
# must never be treated as persistent calibration/history storage.
WNBA_CACHE_DIR = Path(__file__).resolve().parent / "data_cache"
WNBA_CACHE_DIR.mkdir(parents=True, exist_ok=True)

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

BREF_TEAM_CODES = {
    "ATL": "ATL", "CHI": "CHI", "CON": "CON", "DAL": "DAL", "GSV": "GSV",
    "IND": "IND", "LVA": "LVA", "LAS": "LAS", "MIN": "MIN", "NYL": "NYL",
    "PHO": "PHO", "POR": "POR", "SEA": "SEA", "TOR": "TOR", "WAS": "WAS",
}

BOOKMAKER_LABELS = {
    "hardrockbet": "Hard Rock Bet",
    "hardrockbet_fl": "Hard Rock Bet FL",
    "hardrockbet_az": "Hard Rock Bet AZ",
    "hardrockbet_oh": "Hard Rock Bet OH",
    "hard_rock_bet": "Hard Rock Bet",
    "fanduel": "FanDuel",
    "fan_duel": "FanDuel",
    "draftkings": "DraftKings",
    "draft_kings": "DraftKings",
    "betmgm": "BetMGM",
    "bet_mgm": "BetMGM",
    "caesars": "Caesars",
    "pinnacle": "Pinnacle",
    "espnbet": "ESPN BET",
    "espn_bet": "ESPN BET",
    "betrivers": "BetRivers",
    "bet_rivers": "BetRivers",
    "prizepicks": "PrizePicks",
    "prize_picks": "PrizePicks",
    "underdog": "Underdog Fantasy",
    "underdog_fantasy": "Underdog Fantasy",
}

LINE_SOURCE_OPTIONS = [
    "PrizePicks",
    "Consensus sportsbooks",
    "Best sportsbook over line",
    "Best sportsbook under line",
    "Hard Rock Bet",
    "FanDuel",
    "DraftKings",
    "BetMGM",
    "Caesars",
    "ESPN BET",
    "BetRivers",
    "Pinnacle",
    "Underdog Fantasy",
]

LINE_SOURCE_DEFAULT_BOOKMAKER_IDS = {
    "PrizePicks": ("prizepicks",),
    "Hard Rock Bet": ("hardrockbet_fl", "hardrockbet", "hardrockbet_az", "hardrockbet_oh"),
    "FanDuel": ("fanduel",),
    "DraftKings": ("draftkings",),
    "BetMGM": ("betmgm",),
    "Caesars": ("caesars",),
    "ESPN BET": ("espnbet",),
    "BetRivers": ("betrivers",),
    "Pinnacle": ("pinnacle",),
    "Underdog Fantasy": ("underdog",),
}

SPORTSBOOK_BOOKMAKER_KEYS = {
    "hardrockbet", "hardrockbet_fl", "hardrockbet_az", "hardrockbet_oh", "hard_rock_bet",
    "fanduel", "fan_duel", "draftkings", "draft_kings", "betmgm", "bet_mgm",
    "caesars", "espnbet", "espn_bet", "betrivers", "bet_rivers", "pinnacle",
}

THE_ODDS_DEFAULT_BOOKMAKER_IDS = (
    "hardrockbet_fl", "hardrockbet", "fanduel", "draftkings", "betmgm",
    "caesars", "espnbet", "betrivers", "pinnacle",
)

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
    """Try both official WNBA/NBA Stats hosts before giving up."""
    errors: list[str] = []
    session = requests.Session()
    for base_url in WNBA_STATS_BASES:
        url = f"{base_url}/{endpoint}"
        for timeout_seconds in (12, 24):
            try:
                response = session.get(url, params=params, headers=stats_headers(), timeout=timeout_seconds)
                response.raise_for_status()
                frame = parse_stats_result(response.json())
                if frame.empty:
                    raise ValueError(f"no rows returned for {endpoint}")
                return frame
            except Exception as exc:
                errors.append(f"{base_url}: {type(exc).__name__}: {exc}")
                time.sleep(0.35)
    raise RuntimeError(" | ".join(errors[-4:]))


def league_game_log_params(season: str, player_or_team: str, season_type: str) -> dict[str, Any]:
    # Parameter order matters on the 2026 WNBA Stats endpoint. The WNBA client
    # sends LeagueID first; Cloudflare can reject/re-route alphabetically ordered
    # query strings. Python dicts preserve insertion order, and requests keeps it.
    return {
        "LeagueID": WNBA_LEAGUE_ID,
        "PlayerOrTeam": player_or_team,
        "Season": season,
        "SeasonType": season_type,
        "Sorter": "DATE",
        "Direction": "DESC",
        "DateFrom": "",
        "DateTo": "",
        "Counter": "0",
    }


def _espn_get_json(path: str, params: dict[str, Any] | None = None, timeout: int = 20) -> dict[str, Any]:
    url = f"{ESPN_WNBA_BASE}/{path.lstrip('/')}"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": stats_headers()["User-Agent"],
        "Referer": "https://www.espn.com/wnba/",
    }
    last_error: Exception | None = None
    for wait in (0.0, 0.5):
        if wait:
            time.sleep(wait)
        try:
            response = requests.get(url, params=params or {}, headers=headers, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("ESPN returned a non-object response")
            return payload
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"ESPN request failed: {type(last_error).__name__}: {last_error}")


def _event_date_et(value: object) -> date | None:
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.tz_convert(EASTERN_TZ).date()


def parse_espn_events(payload: dict[str, Any], slate_date: date | None = None, completed_only: bool = False) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in payload.get("events", []) or []:
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competition = competitions[0]
        competitors = competition.get("competitors") or []
        home = away = ""
        for competitor in competitors:
            team = competitor.get("team") or {}
            code = normalize_team(team.get("abbreviation") or team.get("shortDisplayName") or team.get("displayName"))
            if competitor.get("homeAway") == "home":
                home = code
            elif competitor.get("homeAway") == "away":
                away = code
        if home not in TEAM_NAMES or away not in TEAM_NAMES:
            continue
        raw_time = event.get("date") or competition.get("date")
        game_date = _event_date_et(raw_time)
        if slate_date is not None and game_date != slate_date:
            continue
        status_type = ((event.get("status") or {}).get("type") or {})
        completed = bool(status_type.get("completed")) or str(status_type.get("name", "")).upper() in {"STATUS_FINAL", "FINAL"}
        if completed_only and not completed:
            continue
        season_info = event.get("season") or competition.get("season") or {}
        rows.append({
            "EventID": str(event.get("id") or competition.get("id") or ""),
            "GameDate": game_date,
            "GameTimeUTC": str(raw_time or ""),
            "Away": away,
            "Home": home,
            "Game": f"{away} @ {home}",
            "Completed": completed,
            "SeasonType": str(season_info.get("slug") or season_info.get("type") or ""),
            "Source": "ESPN fallback",
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates("EventID")


@st.cache_data(ttl=900, show_spinner=False)
def fetch_espn_schedule(slate_date: date) -> pd.DataFrame:
    payload = _espn_get_json("scoreboard", {"dates": slate_date.strftime("%Y%m%d"), "limit": 100})
    return parse_espn_events(payload, slate_date=slate_date)


def _parse_made_attempt(value: object) -> tuple[float, float]:
    text = str(value or "").strip()
    match = re.match(r"^\s*(\d+)\s*[-/]\s*(\d+)\s*$", text)
    if not match:
        return np.nan, np.nan
    return float(match.group(1)), float(match.group(2))


def _numeric_stat(value: object) -> float:
    text = str(value or "").strip()
    if not text or text.upper() in {"--", "DNP", "N/A", "NA"}:
        return np.nan
    return float(pd.to_numeric(pd.Series([text]), errors="coerce").iloc[0])


def parse_espn_summary(payload: dict[str, Any], game_row: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    boxscore = payload.get("boxscore") or {}
    for team_block in boxscore.get("players", []) or []:
        team_info = team_block.get("team") or {}
        team = normalize_team(team_info.get("abbreviation") or team_info.get("displayName"))
        if team not in TEAM_NAMES:
            continue
        opponent = game_row["Home"] if team == game_row["Away"] else game_row["Away"]
        for group in team_block.get("statistics", []) or []:
            labels = [str(label).upper().strip() for label in (group.get("labels") or group.get("keys") or [])]
            for athlete_row in group.get("athletes", []) or []:
                athlete = athlete_row.get("athlete") or {}
                player_id = pd.to_numeric(pd.Series([athlete.get("id")]), errors="coerce").iloc[0]
                player_name = athlete.get("displayName") or athlete.get("shortName") or athlete.get("fullName")
                values = athlete_row.get("stats") or []
                if pd.isna(player_id) or not player_name or not values:
                    continue
                stat_map = {labels[i]: values[i] for i in range(min(len(labels), len(values)))}
                minutes = parse_minutes(stat_map.get("MIN"))
                pts = _numeric_stat(stat_map.get("PTS"))
                reb = _numeric_stat(stat_map.get("REB"))
                ast = _numeric_stat(stat_map.get("AST"))
                if not np.isfinite(minutes) and not any(np.isfinite(v) for v in [pts, reb, ast]):
                    continue
                fgm, fga = _parse_made_attempt(stat_map.get("FG"))
                fg3m, fg3a = _parse_made_attempt(stat_map.get("3PT") or stat_map.get("3P"))
                ftm, fta = _parse_made_attempt(stat_map.get("FT"))
                rows.append({
                    "PlayerID": int(player_id), "Player": str(player_name), "Team": team,
                    "GameID": str(game_row["EventID"]), "GameDate": game_row["GameDate"],
                    "Matchup": f"{team} {'@' if team == game_row['Away'] else 'vs.'} {opponent}",
                    "MIN": minutes, "PTS": pts, "REB": reb, "AST": ast,
                    "OREB": _numeric_stat(stat_map.get("OREB")), "DREB": _numeric_stat(stat_map.get("DREB")),
                    "STL": _numeric_stat(stat_map.get("STL")), "BLK": _numeric_stat(stat_map.get("BLK")),
                    "TOV": _numeric_stat(stat_map.get("TO") or stat_map.get("TOV")),
                    "FGM": fgm, "FGA": fga, "FG3M": fg3m, "FG3A": fg3a,
                    "FTM": ftm, "FTA": fta, "PLUS_MINUS": _numeric_stat(stat_map.get("+/-") or stat_map.get("PLUS/MINUS")),
                    "WL": "", "Opponent": opponent,
                })
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame = frame.sort_values("MIN", ascending=False).drop_duplicates(["GameID", "PlayerID"], keep="first")
    return frame



def fetch_espn_scores_live_results(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch finalized WNBA player box scores through the espn-scores package.

    This is intentionally separate from the app's legacy direct ESPN HTTP path. The
    package owns scoreboard/event discovery and exposes ESPN's raw box-score payload,
    which we pass through the existing parse_espn_summary() normalizer.
    """
    try:
        from espn_scores import wnba as espn_scores_wnba
    except Exception as exc:
        raise RuntimeError(
            "espn-scores is not installed. Make sure the required package is present in requirements.txt, then reboot the Streamlit app. "
            f"Import error: {type(exc).__name__}: {exc}"
        ) from exc

    frames: list[pd.DataFrame] = []
    per_date: dict[str, dict[str, Any]] = {}
    cursor = start_date
    while cursor <= end_date:
        key = cursor.isoformat()
        diag = {
            "Date": key,
            "FinalGames": 0,
            "BoxScoresFetched": 0,
            "RawPlayerRows": 0,
            "NormalizedPlayerRows": 0,
            "Error": "",
        }
        try:
            scoreboard = espn_scores_wnba.date(cursor.strftime("%Y%m%d"), status="final")
            games = (scoreboard or {}).get("games", []) if isinstance(scoreboard, dict) else []
            diag["FinalGames"] = len(games)
            for game in games:
                event_id = str(game.get("id") or "").strip()
                if not event_id:
                    continue
                away_obj = game.get("away_team") or {}
                home_obj = game.get("home_team") or {}
                away = normalize_team(away_obj.get("abbreviation") or away_obj.get("name"))
                home = normalize_team(home_obj.get("abbreviation") or home_obj.get("name"))
                if away not in TEAM_NAMES or home not in TEAM_NAMES:
                    continue
                try:
                    raw = espn_scores_wnba.box_score(event_id, raw=True)
                    game_row = {
                        "EventID": event_id,
                        "GameDate": cursor,
                        "Away": away,
                        "Home": home,
                    }
                    parsed = parse_espn_summary(raw or {}, game_row)
                    diag["BoxScoresFetched"] += 1
                    if not parsed.empty:
                        diag["RawPlayerRows"] += len(parsed)
                        frames.append(parsed)
                except Exception as game_exc:
                    msg = f"{event_id}: {type(game_exc).__name__}: {game_exc}"
                    diag["Error"] = (diag["Error"] + " | " + msg).strip(" |")
        except Exception as exc:
            diag["Error"] = f"{type(exc).__name__}: {exc}"
        per_date[key] = diag
        cursor += timedelta(days=1)

    if not frames:
        st.session_state["wnba_espn_scores_diag"] = {"per_date": per_date, "rows": 0}
        details = " | ".join(
            f"{d}: {r.get('Error')}" for d, r in per_date.items() if r.get("Error")
        )[:1200]
        raise RuntimeError(
            "espn-scores returned no usable finalized WNBA player rows"
            + (f" ({details})" if details else "")
        )

    raw_frame = pd.concat(frames, ignore_index=True)
    normalized = normalize_player_logs(raw_frame)
    normalized = normalized[normalized["GameDate"].between(start_date, end_date)].copy()
    if normalized.empty:
        st.session_state["wnba_espn_scores_diag"] = {"per_date": per_date, "rows": 0}
        raise RuntimeError("espn-scores parsed box scores, but no player rows survived normalization/date filtering.")

    counts = normalized.groupby("GameDate").size().to_dict()
    for d, cnt in counts.items():
        key = d.isoformat() if hasattr(d, "isoformat") else str(d)
        if key in per_date:
            per_date[key]["NormalizedPlayerRows"] = int(cnt)
    st.session_state["wnba_espn_scores_diag"] = {
        "per_date": per_date,
        "rows": int(len(normalized)),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    return normalized


def build_team_logs_from_players(player_logs: pd.DataFrame) -> pd.DataFrame:
    if player_logs.empty:
        return pd.DataFrame()
    numeric_sum = ["PTS", "REB", "AST", "OREB", "DREB", "TOV", "FGM", "FGA", "FTM", "FTA"]
    work = player_logs.copy()
    # Be defensive with imported/legacy logs that pre-date the Opponent column.
    if "Opponent" not in work.columns:
        work["Opponent"] = work.get("Matchup", pd.Series("", index=work.index)).map(extract_opponent)
    else:
        missing_opp = work["Opponent"].astype(str).str.strip().eq("")
        if missing_opp.any() and "Matchup" in work.columns:
            work.loc[missing_opp, "Opponent"] = work.loc[missing_opp, "Matchup"].map(extract_opponent)
    work["Opponent"] = work["Opponent"].map(normalize_team)
    for column in numeric_sum:
        source = work[column] if column in work.columns else pd.Series(np.nan, index=work.index)
        work[column] = pd.to_numeric(source, errors="coerce").fillna(0)
    grouped = work.groupby(["GameID", "GameDate", "Team", "Opponent"], as_index=False, dropna=False)[numeric_sum].sum()
    grouped["TeamID"] = np.nan
    grouped["MIN"] = 200.0
    grouped["Matchup"] = grouped.apply(lambda row: f"{row['Team']} vs. {row['Opponent']}", axis=1)
    grouped["PLUS_MINUS"] = np.nan
    grouped["WL"] = ""
    return normalize_team_logs(grouped)


@st.cache_data(ttl=21600, show_spinner=False)
def load_espn_game_logs(season: str, season_type: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    season_year = int(season)
    if season_type == "Playoffs":
        start = date(season_year, 9, 1)
    else:
        start = date(season_year, 4, 15)
    end = min(date.today(), date(season_year, 12, 31))
    if end < start:
        raise RuntimeError("No completed games are available for the selected season yet.")

    event_frames: list[pd.DataFrame] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=30), end)
        date_range = f"{cursor.strftime('%Y%m%d')}-{chunk_end.strftime('%Y%m%d')}"
        payload = _espn_get_json("scoreboard", {"dates": date_range, "limit": 1000})
        frame = parse_espn_events(payload, completed_only=True)
        if not frame.empty:
            event_frames.append(frame)
        cursor = chunk_end + timedelta(days=1)
    if not event_frames:
        raise RuntimeError("ESPN returned no completed WNBA games for the selected season.")
    events = pd.concat(event_frames, ignore_index=True).drop_duplicates("EventID")
    season_key = events.get("SeasonType", pd.Series("", index=events.index)).astype(str).str.lower()
    if season_type == "Playoffs":
        filtered = events[season_key.str.contains("post|playoff|3", regex=True, na=False)]
    else:
        filtered = events[~season_key.str.contains("pre|post|playoff|1|3", regex=True, na=False)]
    if not filtered.empty:
        events = filtered

    def fetch_one(record: dict[str, Any]) -> pd.DataFrame:
        payload = _espn_get_json("summary", {"event": record["EventID"]}, timeout=25)
        return parse_espn_summary(payload, record)

    frames: list[pd.DataFrame] = []
    records = events.to_dict("records")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_one, record): record for record in records}
        for future in as_completed(futures):
            try:
                frame = future.result()
                if not frame.empty:
                    frames.append(frame)
            except Exception:
                continue
    if not frames:
        raise RuntimeError("ESPN game summaries were reachable, but no player box scores could be parsed.")
    player_logs = normalize_player_logs(pd.concat(frames, ignore_index=True))
    team_logs = build_team_logs_from_players(player_logs)
    return player_logs, team_logs


def _game_log_cache_paths(season: str, season_type: str) -> tuple[Path, Path, Path]:
    key = re.sub(r"[^a-z0-9]+", "_", f"{season}_{season_type}".lower()).strip("_")
    return (
        WNBA_CACHE_DIR / f"{key}_players.csv",
        WNBA_CACHE_DIR / f"{key}_teams.csv",
        WNBA_CACHE_DIR / f"{key}_meta.json",
    )


def save_game_logs_snapshot(
    season: str,
    season_type: str,
    player_logs: pd.DataFrame,
    team_logs: pd.DataFrame,
    source: str,
) -> None:
    """Persist the last known-good logs so a provider outage cannot take down the board."""
    if player_logs is None or player_logs.empty or team_logs is None or team_logs.empty:
        return
    players_path, teams_path, meta_path = _game_log_cache_paths(season, season_type)
    try:
        WNBA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        players_tmp = players_path.with_suffix(".csv.tmp")
        teams_tmp = teams_path.with_suffix(".csv.tmp")
        meta_tmp = meta_path.with_suffix(".json.tmp")
        player_logs.to_csv(players_tmp, index=False)
        team_logs.to_csv(teams_tmp, index=False)
        meta_tmp.write_text(json.dumps({
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            "season": season,
            "season_type": season_type,
            "source": source,
            "player_rows": int(len(player_logs)),
            "team_rows": int(len(team_logs)),
        }, indent=2), encoding="utf-8")
        players_tmp.replace(players_path)
        teams_tmp.replace(teams_path)
        meta_tmp.replace(meta_path)
    except Exception:
        # Caching is a resilience feature; a filesystem problem must never break live data loading.
        return


def load_game_logs_snapshot(season: str, season_type: str) -> tuple[pd.DataFrame, pd.DataFrame, str] | None:
    players_path, teams_path, meta_path = _game_log_cache_paths(season, season_type)
    if not players_path.exists() or not teams_path.exists():
        return None
    try:
        players = normalize_player_logs(pd.read_csv(players_path))
        teams = normalize_team_logs(pd.read_csv(teams_path))
        if players.empty or teams.empty:
            return None
        meta: dict[str, Any] = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        saved_at = str(meta.get("saved_at_utc") or "unknown time")
        original_source = str(meta.get("source") or "previous successful load")
        source = f"Saved local snapshot ({saved_at}) · originally {original_source}"
        return players, teams, source
    except Exception:
        return None



def _to_pandas_frame(value: Any) -> pd.DataFrame:
    """Convert SportsDataverse Polars/Pandas output to pandas without forcing one backend."""
    if value is None:
        return pd.DataFrame()
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if hasattr(value, "to_pandas"):
        try:
            return value.to_pandas()
        except Exception:
            pass
    try:
        return pd.DataFrame(value)
    except Exception:
        return pd.DataFrame()


def load_sportsdataverse_game_logs(season: str, season_type: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load maintained WNBA player box-score releases from SportsDataverse.

    This is intentionally file/release based and avoids making the dashboard depend on
    stats.wnba.com/ESPN being willing to serve this particular machine.
    """
    try:
        import sportsdataverse as sdv
    except Exception as exc:
        raise RuntimeError(
            "SportsDataverse is not installed. Make sure sportsdataverse and pyarrow are listed in requirements.txt, then reboot the Streamlit app."
        ) from exc

    try:
        raw = sdv.wnba.load_wnba_player_boxscore(seasons=[int(season)])
    except TypeError:
        # Compatibility with package builds that expose the pandas switch directly.
        raw = sdv.wnba.load_wnba_player_boxscore(seasons=[int(season)], return_as_pandas=True)
    except Exception as exc:
        raise RuntimeError(f"SportsDataverse player-boxscore download failed: {type(exc).__name__}: {exc}") from exc

    box = _to_pandas_frame(raw)
    if box.empty:
        raise RuntimeError(f"SportsDataverse returned no WNBA player box scores for {season}.")

    # Keep only the requested competition phase when the release contains a season_type field.
    if "season_type" in box.columns:
        season_type_values = box["season_type"].astype(str).str.lower().str.strip()
        if season_type == "Playoffs":
            mask = season_type_values.isin({"3", "postseason", "post season", "playoffs", "playoff"}) | season_type_values.str.contains("post|playoff", regex=True, na=False)
        else:
            mask = season_type_values.isin({"2", "regular season", "regular", "reg"}) | season_type_values.str.contains("regular", regex=True, na=False)
        if mask.any():
            box = box.loc[mask].copy()

    def series(name: str, default: Any = np.nan) -> pd.Series:
        if name in box.columns:
            return box[name]
        return pd.Series(default, index=box.index)

    team_label = series("team_display_name", "")
    team_label = team_label.where(team_label.astype(str).str.strip().ne(""), series("team_name", ""))
    team_label = team_label.where(team_label.astype(str).str.strip().ne(""), series("team_abbreviation", ""))
    opp_label = series("opponent_team_display_name", "")
    opp_label = opp_label.where(opp_label.astype(str).str.strip().ne(""), series("opponent_team_name", ""))
    opp_label = opp_label.where(opp_label.astype(str).str.strip().ne(""), series("opponent_team_abbreviation", ""))

    teams = team_label.map(normalize_team)
    opponents = opp_label.map(normalize_team)
    home_away = series("home_away", "").astype(str).str.lower()
    matchup = pd.Series(index=box.index, dtype="object")
    for idx in box.index:
        t = teams.loc[idx] if idx in teams.index else ""
        o = opponents.loc[idx] if idx in opponents.index else ""
        marker = "@" if str(home_away.loc[idx]).lower() == "away" else "vs."
        matchup.loc[idx] = f"{t} {marker} {o}".strip()

    players = pd.DataFrame({
        "PLAYER_ID": series("athlete_id"),
        "PLAYER_NAME": series("athlete_display_name", ""),
        "TEAM_ID": series("team_id"),
        "TEAM_ABBREVIATION": teams,
        "GAME_ID": series("game_id"),
        "GAME_DATE": series("game_date"),
        "MATCHUP": matchup,
        "MIN": series("minutes"),
        "PTS": series("points"),
        "REB": series("rebounds"),
        "AST": series("assists"),
        "OREB": series("offensive_rebounds"),
        "DREB": series("defensive_rebounds"),
        "STL": series("steals"),
        "BLK": series("blocks"),
        "TOV": series("turnovers"),
        "FGM": series("field_goals_made"),
        "FGA": series("field_goals_attempted"),
        "FG3M": series("three_point_field_goals_made"),
        "FG3A": series("three_point_field_goals_attempted"),
        "FTM": series("free_throws_made"),
        "FTA": series("free_throws_attempted"),
        "PLUS_MINUS": series("plus_minus"),
        "WL": series("team_winner").map(lambda x: "W" if x is True or str(x).lower() == "true" else ("L" if x is False or str(x).lower() == "false" else "")),
    })
    players = normalize_player_logs(players)
    players = players[players["Team"].isin(TEAM_NAMES.keys())].copy()
    if players.empty:
        raise RuntimeError("SportsDataverse downloaded data, but no recognized WNBA player rows remained after normalization.")

    team_logs = build_team_logs_from_players(players)
    if team_logs.empty:
        raise RuntimeError("SportsDataverse player rows loaded, but team game logs could not be constructed.")
    return players, team_logs




def fetch_sportsdataverse_github_results(
    season: str,
    season_type: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Fetch the current processed WNBA player-boxscore parquet directly from GitHub.

    This deliberately bypasses ESPN's live endpoints and the installed SportsDataverse
    loader/cache. The wehoop-wnba-data repository publishes one parquet per season,
    so a normal raw.githubusercontent.com download is enough for finalized grading.
    """
    url = SDV_WNBA_PLAYER_BOX_RAW.format(season=int(season))
    headers = {
        "User-Agent": "CoochieBoard/1.0 (+local WNBA grading)",
        "Accept": "application/octet-stream,*/*;q=0.8",
        "Cache-Control": "no-cache",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=35)
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"SportsDataverse GitHub parquet request failed: {type(exc).__name__}: {exc}") from exc

    try:
        box = pd.read_parquet(io.BytesIO(resp.content))
    except Exception as exc:
        raise RuntimeError(f"SportsDataverse GitHub parquet could not be read: {type(exc).__name__}: {exc}") from exc
    if box.empty:
        raise RuntimeError("SportsDataverse GitHub parquet was empty.")

    # Keep the requested phase when present.
    if "season_type" in box.columns:
        values = box["season_type"].astype(str).str.lower().str.strip()
        if season_type == "Playoffs":
            mask = values.isin({"3", "postseason", "post season", "playoffs", "playoff"}) | values.str.contains("post|playoff", regex=True, na=False)
        else:
            mask = values.isin({"2", "regular season", "regular", "reg"}) | values.str.contains("regular", regex=True, na=False)
        if mask.any():
            box = box.loc[mask].copy()

    def col(name: str, default: Any = np.nan) -> pd.Series:
        if name in box.columns:
            return box[name]
        return pd.Series(default, index=box.index)

    team_label = col("team_display_name", "")
    team_label = team_label.where(team_label.astype(str).str.strip().ne(""), col("team_name", ""))
    team_label = team_label.where(team_label.astype(str).str.strip().ne(""), col("team_abbreviation", ""))
    opp_label = col("opponent_team_display_name", "")
    opp_label = opp_label.where(opp_label.astype(str).str.strip().ne(""), col("opponent_team_name", ""))
    opp_label = opp_label.where(opp_label.astype(str).str.strip().ne(""), col("opponent_team_abbreviation", ""))

    teams = team_label.map(normalize_team)
    opponents = opp_label.map(normalize_team)
    home_away = col("home_away", "").astype(str).str.lower()
    matchup = pd.Series(index=box.index, dtype="object")
    for idx in box.index:
        t = teams.loc[idx] if idx in teams.index else ""
        o = opponents.loc[idx] if idx in opponents.index else ""
        marker2 = "@" if str(home_away.loc[idx]).lower() == "away" else "vs."
        matchup.loc[idx] = f"{t} {marker2} {o}".strip()

    players = pd.DataFrame({
        "PLAYER_ID": col("athlete_id"),
        "PLAYER_NAME": col("athlete_display_name", ""),
        "TEAM_ID": col("team_id"),
        "TEAM_ABBREVIATION": teams,
        "GAME_ID": col("game_id"),
        "GAME_DATE": col("game_date"),
        "MATCHUP": matchup,
        "MIN": col("minutes"),
        "PTS": col("points"),
        "REB": col("rebounds"),
        "AST": col("assists"),
        "OREB": col("offensive_rebounds"),
        "DREB": col("defensive_rebounds"),
        "STL": col("steals"),
        "BLK": col("blocks"),
        "TOV": col("turnovers"),
        "FGM": col("field_goals_made"),
        "FGA": col("field_goals_attempted"),
        "FG3M": col("three_point_field_goals_made"),
        "FG3A": col("three_point_field_goals_attempted"),
        "FTM": col("free_throws_made"),
        "FTA": col("free_throws_attempted"),
        "PLUS_MINUS": col("plus_minus"),
        "WL": col("team_winner").map(lambda x: "W" if x is True or str(x).lower() == "true" else ("L" if x is False or str(x).lower() == "false" else "")),
    })
    players = normalize_player_logs(players)
    players = players[players["Team"].isin(TEAM_NAMES.keys())].copy()
    if players.empty:
        raise RuntimeError("SportsDataverse GitHub parquet contained no recognized WNBA player rows after normalization.")

    d = pd.to_datetime(players["GameDate"], errors="coerce").dt.date
    latest = d.max() if d.notna().any() else None
    st.session_state["wnba_sdv_github_latest_date"] = str(latest) if latest else None
    st.session_state["wnba_sdv_github_total_rows"] = int(len(players))
    mask = d.between(start_date, end_date)
    out = players.loc[mask].copy()
    st.session_state["wnba_sdv_github_range_rows"] = int(len(out))
    st.session_state["wnba_sdv_github_range"] = f"{start_date} → {end_date}"
    if out.empty:
        raise RuntimeError(
            f"SportsDataverse GitHub parquet loaded {len(players):,} rows but had no games in "
            f"{start_date} → {end_date}; latest available date was {latest}."
        )
    return out



def _bref_get_html(url: str, timeout: int = 25) -> str:
    """Fetch Basketball-Reference HTML conservatively with browser-like headers.

    Sports Reference rate-limits aggressive clients, so this helper uses a normal
    browser UA, retries only a couple of times, and never parallelizes box-score
    requests.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://www.basketball-reference.com/wnba/",
    }
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                time.sleep(3 + attempt * 3)
                last_error = RuntimeError("Basketball-Reference rate-limited the request (HTTP 429).")
                continue
            resp.raise_for_status()
            text = resp.text or ""
            if "<html" not in text.lower():
                raise RuntimeError(f"Basketball-Reference returned non-HTML content ({resp.headers.get('content-type','unknown')}).")
            return text
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 + attempt)
    raise RuntimeError(f"Basketball-Reference request failed for {url}: {type(last_error).__name__}: {last_error}")


def _bref_soup_with_commented_tables(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    # Sports Reference commonly wraps some stat tables in HTML comments.
    for node in list(soup.find_all(string=lambda x: isinstance(x, Comment))):
        raw = str(node)
        if "<table" in raw.lower():
            try:
                frag = BeautifulSoup(raw, "html.parser")
                node.replace_with(frag)
            except Exception:
                pass
    return soup


def _snapshot_effective_game_date(row: pd.Series | dict[str, Any]) -> date | None:
    """Use the stored tip time as the real game date when available."""
    try:
        game_time = row.get("GameTimeUTC")
    except Exception:
        game_time = None
    parsed = pd.to_datetime(game_time, utc=True, errors="coerce")
    if pd.notna(parsed):
        try:
            return parsed.tz_convert(EASTERN_TZ).date()
        except Exception:
            return parsed.date()
    try:
        slate = pd.to_datetime(row.get("SlateDate"), errors="coerce")
    except Exception:
        slate = pd.NaT
    return slate.date() if pd.notna(slate) else None


def _history_bref_game_hints(history: pd.DataFrame | None, start_date: date, end_date: date) -> list[tuple[date, str]]:
    """Construct BRef box-score URLs from saved snapshot matchup/home-team data."""
    hist = normalize_history(history) if history is not None and not history.empty else pd.DataFrame()
    if hist.empty:
        return []
    hints: set[tuple[date, str]] = set()
    for _, row in hist.iterrows():
        d = _snapshot_effective_game_date(row)
        if d is None or d < start_date or d > end_date:
            continue
        team = normalize_team(row.get("Team", ""))
        opp = normalize_team(row.get("Opponent", ""))
        homeaway = str(row.get("HomeAway", "") or "").strip().lower()
        home = ""
        if homeaway.startswith("home") and team in TEAM_NAMES:
            home = team
        elif homeaway.startswith("away") and opp in TEAM_NAMES:
            home = opp
        if not home:
            game = str(row.get("Game", "") or "")
            if "@" in game:
                right = normalize_team(game.split("@", 1)[1].strip())
                if right in TEAM_NAMES:
                    home = right
        code = BREF_TEAM_CODES.get(home, "")
        if code:
            hints.add((d, f"{BREF_WNBA_BASE}/boxscores/{d.strftime('%Y%m%d')}0{code}.html"))
    return sorted(hints, key=lambda x: (x[0], x[1]))


def _bref_schedule_boxscore_links(season: str, start_date: date, end_date: date) -> list[tuple[date, str]]:
    """Discover finalized WNBA box scores from Basketball-Reference by date.

    The old implementation depended on /wnba/years/{year}_games.html. That page
    can be stale/blocked and its table markup has changed. Basketball-Reference's
    daily WNBA box-score index is a much safer discovery surface for grading.
    """
    links: list[tuple[date, str]] = []
    seen: set[str] = set()
    errors: list[str] = []
    per_date: dict[str, dict[str, Any]] = {}
    day = start_date
    while day <= end_date:
        url = (
            f"{BREF_WNBA_BASE}/boxscores/index.fcgi?"
            f"month={day.month}&day={day.day}&year={day.year}"
        )
        day_key = day.isoformat()
        per_date.setdefault(day_key, {
            "Date": day_key, "IndexOK": False, "BoxScoreLinks": 0,
            "ParsedBoxScores": 0, "RawPlayerRows": 0, "NormalizedPlayerRows": 0,
            "MatchedSnapshotRows": 0, "Error": "",
        })
        try:
            soup = _bref_soup_with_commented_tables(_bref_get_html(url))
            per_date[day_key]["IndexOK"] = True
            found_for_day = 0
            for a in soup.find_all("a", href=True):
                href = str(a.get("href") or "")
                # WNBA box score links look like /wnba/boxscores/YYYYMMDD0TEAM.html
                if not re.search(r"^/wnba/boxscores/\d{9}[A-Za-z0-9]+\.html$", href):
                    continue
                full = f"https://www.basketball-reference.com{href}"
                if full in seen:
                    continue
                seen.add(full)
                links.append((day, full))
                found_for_day += 1
            # Some pages expose canonical links without the /wnba prefix in odd layouts.
            if found_for_day == 0:
                for a in soup.find_all("a", href=True):
                    href = str(a.get("href") or "")
                    if not re.search(r"/wnba/boxscores/\d{9}[A-Za-z0-9]+\.html$", href):
                        continue
                    full = href if href.startswith("http") else f"https://www.basketball-reference.com{href}"
                    if full not in seen:
                        seen.add(full)
                        links.append((day, full))
                        found_for_day += 1
            per_date[day_key]["BoxScoreLinks"] = int(found_for_day)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            errors.append(f"{day.isoformat()}: {err}")
            per_date[day_key]["Error"] = err
        day += timedelta(days=1)
        # Avoid hammering Sports Reference when several pending dates exist.
        time.sleep(0.35)

    # Save lightweight diagnostics for the Streamlit UI.
    try:
        st.session_state["wnba_bref_discovery_diag"] = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "boxscore_links": len(links),
            "errors": errors[:8],
            "per_date": per_date,
        }
    except Exception:
        pass
    return links

def _bref_stat_text(row, stat: str) -> Any:
    cell = row.find(attrs={"data-stat": stat})
    if cell is None:
        return None
    txt = cell.get_text(" ", strip=True)
    return txt if txt != "" else None


def fetch_basketball_reference_results(season: str, start_date: date, end_date: date, history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Fetch finalized WNBA player box scores from Basketball-Reference.

    Only games with a Box Score link on the season schedule are requested. Future
    games therefore do not grade. Requests are sequential and lightly throttled.
    """
    links = _bref_schedule_boxscore_links(season, start_date, end_date)
    discovery_diag = st.session_state.get("wnba_bref_discovery_diag", {}) if hasattr(st, "session_state") else {}
    per_date_seed = dict(discovery_diag.get("per_date") or {})
    existing_urls = {u for _, u in links}
    constructed = 0
    for d, url in _history_bref_game_hints(history, start_date, end_date):
        key = d.isoformat()
        if int((per_date_seed.get(key) or {}).get("BoxScoreLinks", 0) or 0) > 0:
            continue
        if url not in existing_urls:
            links.append((d, url))
            existing_urls.add(url)
            constructed += 1
            per_date_seed.setdefault(key, {
                "Date": key, "IndexOK": True, "BoxScoreLinks": 0, "ParsedBoxScores": 0,
                "RawPlayerRows": 0, "NormalizedPlayerRows": 0, "MatchedSnapshotRows": 0, "Error": "",
            })
            per_date_seed[key]["ConstructedLinks"] = int(per_date_seed[key].get("ConstructedLinks", 0) or 0) + 1
    if constructed:
        try:
            disc = dict(discovery_diag)
            disc["per_date"] = per_date_seed
            disc["constructed_links"] = int(constructed)
            disc["boxscore_links"] = int(len(links))
            st.session_state["wnba_bref_discovery_diag"] = disc
        except Exception:
            pass
    if not links:
        diag = st.session_state.get("wnba_bref_discovery_diag", {}) if hasattr(st, "session_state") else {}
        errs = diag.get("errors") or []
        detail = f"0 box-score links discovered for {start_date} through {end_date}"
        if errs:
            detail += " | " + " | ".join(errs[:3])
        raise RuntimeError(detail)

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    discovery_diag = st.session_state.get("wnba_bref_discovery_diag", {}) if hasattr(st, "session_state") else {}
    per_date_diag = dict(discovery_diag.get("per_date") or {})
    for n, (game_date, url) in enumerate(links):
        day_key = game_date.isoformat()
        per_date_diag.setdefault(day_key, {
            "Date": day_key, "IndexOK": True, "BoxScoreLinks": 0,
            "ParsedBoxScores": 0, "RawPlayerRows": 0, "NormalizedPlayerRows": 0,
            "MatchedSnapshotRows": 0, "Error": "",
        })
        rows_before_game = len(rows)
        try:
            soup = _bref_soup_with_commented_tables(_bref_get_html(url))
            basic_tables = []
            for table in soup.find_all("table"):
                table_id = str(table.get("id") or "")
                if re.match(r"^box-[A-Za-z0-9]+-game-basic$", table_id):
                    basic_tables.append(table)
            if not basic_tables:
                raise RuntimeError("no player basic box-score tables found")

            game_id_match = re.search(r"/boxscores/([^/.]+)", url)
            game_id = game_id_match.group(1) if game_id_match else f"BREF-{game_date.isoformat()}-{n}"
            for table in basic_tables:
                table_id = str(table.get("id") or "")
                m = re.match(r"^box-([A-Za-z0-9]+)-game-basic$", table_id)
                team_code = normalize_team(m.group(1) if m else "")
                body = table.find("tbody")
                if body is None:
                    continue
                for tr in body.find_all("tr"):
                    classes = set(tr.get("class") or [])
                    if "thead" in classes:
                        continue
                    player_cell = tr.find(attrs={"data-stat": "player"})
                    if player_cell is None:
                        continue
                    player_name = player_cell.get_text(" ", strip=True)
                    if not player_name or player_name.lower() in {"team totals", "reserves"}:
                        continue
                    player_link = player_cell.find("a", href=True)
                    player_id = None
                    if player_link is not None:
                        pm = re.search(r"/players/[a-z]/([^/.]+)\.html", str(player_link.get("href") or ""), flags=re.I)
                        if pm:
                            player_id = f"BREF:{pm.group(1)}"
                    mp = _bref_stat_text(tr, "mp")
                    # DNP rows have no minutes/stats; exclude them from grading candidates.
                    if mp is None:
                        continue
                    rows.append({
                        "PLAYER_ID": player_id or f"BREF:{normalize_player_name(player_name)}",
                        "PLAYER_NAME": player_name,
                        "TEAM_ABBREVIATION": team_code,
                        "GAME_ID": game_id,
                        "GAME_DATE": game_date.isoformat(),
                        "MATCHUP": "",
                        "MIN": mp,
                        "PTS": _bref_stat_text(tr, "pts"),
                        "REB": _bref_stat_text(tr, "trb"),
                        "AST": _bref_stat_text(tr, "ast"),
                        "OREB": _bref_stat_text(tr, "orb"),
                        "DREB": _bref_stat_text(tr, "drb"),
                        "STL": _bref_stat_text(tr, "stl"),
                        "BLK": _bref_stat_text(tr, "blk"),
                        "TOV": _bref_stat_text(tr, "tov"),
                        "FGM": _bref_stat_text(tr, "fg"),
                        "FGA": _bref_stat_text(tr, "fga"),
                        "FG3M": _bref_stat_text(tr, "fg3"),
                        "FG3A": _bref_stat_text(tr, "fg3a"),
                        "FTM": _bref_stat_text(tr, "ft"),
                        "FTA": _bref_stat_text(tr, "fta"),
                        "PLUS_MINUS": _bref_stat_text(tr, "plus_minus"),
                    })
            per_date_diag[day_key]["ParsedBoxScores"] = int(per_date_diag[day_key].get("ParsedBoxScores", 0)) + 1
            per_date_diag[day_key]["RawPlayerRows"] = int(per_date_diag[day_key].get("RawPlayerRows", 0)) + max(len(rows) - rows_before_game, 0)
            # Be respectful of Sports Reference request limits.
            time.sleep(0.8)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            errors.append(f"{game_date} {err}")
            existing = str(per_date_diag[day_key].get("Error") or "")
            per_date_diag[day_key]["Error"] = (existing + " | " + err).strip(" |")

    if not rows:
        detail = " | ".join(errors[:4]) if errors else "no finalized box-score rows found"
        raise RuntimeError(f"Basketball-Reference returned no usable WNBA player box scores ({detail}).")
    raw_frame = pd.DataFrame(rows)
    raw_count = len(raw_frame)
    frame = normalize_player_logs(raw_frame)
    normalized_count = len(frame)
    if frame.empty:
        raise RuntimeError(
            f"Basketball-Reference parsed {raw_count} player rows, but 0 survived normalization. "
            "Check provider ID/stat normalization."
        )
    frame = frame[frame["GameDate"].between(start_date, end_date)].copy()
    if frame.empty:
        parsed_dates = sorted({str(x) for x in pd.to_datetime(raw_frame.get("GAME_DATE"), errors="coerce").dt.date.dropna()})
        raise RuntimeError(
            f"Basketball-Reference parsed {raw_count} rows ({normalized_count} normalized), but none remained "
            f"between {start_date} and {end_date}. Parsed dates: {parsed_dates[:12]}"
        )
    try:
        # Count rows that survived shared normalization by calendar date.
        normalized_counts = frame.groupby("GameDate").size().to_dict() if not frame.empty else {}
        for d, cnt in normalized_counts.items():
            key = d.isoformat() if hasattr(d, "isoformat") else str(d)
            per_date_diag.setdefault(key, {
                "Date": key, "IndexOK": True, "BoxScoreLinks": 0,
                "ParsedBoxScores": 0, "RawPlayerRows": 0, "NormalizedPlayerRows": 0,
                "MatchedSnapshotRows": 0, "Error": "",
            })
            per_date_diag[key]["NormalizedPlayerRows"] = int(cnt)
        st.session_state["wnba_bref_rows_diag"] = {
            "raw_player_rows": raw_count,
            "normalized_player_rows": normalized_count,
            "in_range_player_rows": len(frame),
            "dates": sorted({d.isoformat() for d in frame["GameDate"].dropna().tolist()}),
            "per_date": per_date_diag,
        }
        # Also mirror the richer per-date diagnostics into the discovery object.
        disc = dict(st.session_state.get("wnba_bref_discovery_diag") or {})
        disc["per_date"] = per_date_diag
        st.session_state["wnba_bref_discovery_diag"] = disc
    except Exception:
        pass
    return frame

def fetch_balldontlie_live_results(api_key: str, start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch finalized WNBA player box scores from BALLDONTLIE.

    BALLDONTLIE's WNBA player_stats endpoint requires its WNBA GOAT access tier.
    We first query the games endpoint (available on the free tier) and only request
    stats for games whose status is `post`, so in-progress rows can never grade a
    pregame snapshot as final.
    """
    key = str(api_key or "").strip()
    if not key:
        raise RuntimeError("BALLDONTLIE API key is not set.")
    headers = {"Authorization": key, "Accept": "application/json", "User-Agent": "CoochieBoard/1.0"}

    def get_paged(path: str, base_params: list[tuple[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor: Any = None
        for _ in range(25):
            params = list(base_params)
            params.append(("per_page", 100))
            if cursor not in (None, ""):
                params.append(("cursor", cursor))
            resp = requests.get(f"{BALLDONTLIE_WNBA_BASE}/{path}", headers=headers, params=params, timeout=25)
            if resp.status_code == 401:
                raise RuntimeError(
                    "BALLDONTLIE returned 401. WNBA player_stats requires WNBA GOAT access (or the GOAT trial) and a valid API key."
                )
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data", []) if isinstance(payload, dict) else []
            if isinstance(data, list):
                out.extend([x for x in data if isinstance(x, dict)])
            meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
            cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
            if cursor in (None, ""):
                break
        return out

    games = get_paged("games", [
        ("start_date", start_date.isoformat()),
        ("end_date", end_date.isoformat()),
    ])
    final_game_ids = [g.get("id") for g in games if str(g.get("status", "")).lower() == "post" and g.get("id") is not None]
    if not final_game_ids:
        return pd.DataFrame()

    stat_params: list[tuple[str, Any]] = []
    for gid in final_game_ids:
        stat_params.append(("game_ids[]", gid))
    stats = get_paged("player_stats", stat_params)
    if not stats:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for rec in stats:
        player = rec.get("player") if isinstance(rec.get("player"), dict) else {}
        team = rec.get("team") if isinstance(rec.get("team"), dict) else {}
        game = rec.get("game") if isinstance(rec.get("game"), dict) else {}
        first = str(player.get("first_name") or "").strip()
        last = str(player.get("last_name") or "").strip()
        rows.append({
            "PLAYER_ID": player.get("id"),
            "PLAYER_NAME": f"{first} {last}".strip(),
            "TEAM_ID": team.get("id"),
            "TEAM_ABBREVIATION": team.get("abbreviation") or team.get("full_name") or team.get("name"),
            "GAME_ID": game.get("id"),
            "GAME_DATE": game.get("date"),
            "MATCHUP": "",
            "MIN": rec.get("min"),
            "PTS": rec.get("pts"),
            "REB": rec.get("reb"),
            "AST": rec.get("ast"),
            "OREB": rec.get("oreb"),
            "DREB": rec.get("dreb"),
            "STL": rec.get("stl"),
            "BLK": rec.get("blk"),
            "TOV": rec.get("turnover"),
            "FGM": rec.get("fgm"),
            "FGA": rec.get("fga"),
            "FG3M": rec.get("fg3m"),
            "FG3A": rec.get("fg3a"),
            "FTM": rec.get("ftm"),
            "FTA": rec.get("fta"),
            "PLUS_MINUS": rec.get("plus_minus"),
        })
    frame = normalize_player_logs(pd.DataFrame(rows))
    if frame.empty:
        raise RuntimeError("BALLDONTLIE returned final-game stats, but no usable WNBA player rows remained after normalization.")
    return frame


def merge_result_logs(base_logs: pd.DataFrame, fresh_logs: pd.DataFrame) -> pd.DataFrame:
    """Merge a live grading feed into session logs without replacing model-history data."""
    frames = []
    if base_logs is not None and not base_logs.empty:
        frames.append(normalize_player_logs(base_logs))
    if fresh_logs is not None and not fresh_logs.empty:
        frames.append(normalize_player_logs(fresh_logs))
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True, sort=False)
    merged["_NameKeyMerge"] = merged["Player"].map(normalize_player_name)
    merged["_TeamKeyMerge"] = merged["Team"].map(normalize_team)
    # Keep the live row when the same player/date/team exists across providers.
    merged = merged.drop_duplicates(subset=["_NameKeyMerge", "GameDate", "_TeamKeyMerge"], keep="last")
    return merged.drop(columns=["_NameKeyMerge", "_TeamKeyMerge"], errors="ignore")


@st.cache_data(ttl=3600, show_spinner=False)
def load_official_game_logs(season: str, season_type: str, force_refresh: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    # Normal startup uses the permanent snapshot immediately when available.
    cached = load_game_logs_snapshot(season, season_type)
    if cached is not None and not force_refresh:
        return cached

    # SportsDataverse is the reliable model-history source on this local build.
    # Try it BEFORE the official WNBA/NBA endpoints, which are known to time out
    # on this machine, and before ESPN, which is known to return 400/403.
    sdv_error = ""
    try:
        players, teams = load_sportsdataverse_game_logs(season, season_type)
        save_game_logs_snapshot(season, season_type, players, teams, "SportsDataverse ESPN WNBA release")
        return players, teams, "SportsDataverse ESPN WNBA release"
    except Exception as exc:
        sdv_error = f"{type(exc).__name__}: {exc}"

    official_error = ""
    try:
        players = normalize_player_logs(fetch_wnba_stats("leaguegamelog", league_game_log_params(season, "P", season_type)))
        teams = normalize_team_logs(fetch_wnba_stats("leaguegamelog", league_game_log_params(season, "T", season_type)))
        save_game_logs_snapshot(season, season_type, players, teams, "Official WNBA Stats")
        return players, teams, "Official WNBA Stats"
    except Exception as exc:
        official_error = f"{type(exc).__name__}: {exc}"

    espn_error = ""
    try:
        players, teams = load_espn_game_logs(season, season_type)
        players = normalize_player_logs(players)
        teams = normalize_team_logs(teams)
        save_game_logs_snapshot(season, season_type, players, teams, "ESPN automatic fallback")
        return players, teams, "ESPN automatic fallback"
    except Exception as exc:
        espn_error = f"{type(exc).__name__}: {exc}"

    if cached is not None:
        players, teams, source = cached
        return players, teams, f"{source} · refresh failed; using saved data"

    raise RuntimeError(
        f"SportsDataverse failed ({sdv_error}). Official WNBA Stats failed ({official_error}). "
        f"ESPN fallback also failed ({espn_error}). No saved local snapshot exists yet. "
        "Make sure sportsdataverse and pyarrow are in requirements.txt, then reboot the Streamlit app."
    )


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
    # Player IDs are provider-specific identifiers, not guaranteed numeric.
    # Official WNBA/ESPN commonly use numeric IDs, while Basketball-Reference
    # uses values such as ``BREF:stewabr01``. Coercing PlayerID to numeric
    # destroys every Basketball-Reference row and prevents grading.
    if "PlayerID" not in result.columns:
        result["PlayerID"] = np.nan
    result["PlayerID"] = result["PlayerID"].map(
        lambda v: np.nan if pd.isna(v) or str(v).strip() == "" else str(v).strip()
    )

    numeric_columns = [
        "TeamID", "MIN", "PTS", "REB", "AST", "OREB", "DREB", "STL", "BLK",
        "TOV", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "PLUS_MINUS",
    ]
    for column in numeric_columns:
        if column not in result.columns:
            result[column] = np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["Team"] = result["Team"].map(normalize_team)
    # Preserve/build opponent for downstream team aggregation. Official WNBA logs
    # encode the opponent inside MATCHUP; SportsDataverse is normalized into the
    # same representation before entering this function.
    if "Opponent" not in result.columns:
        result["Opponent"] = ""
    if "Matchup" in result.columns:
        derived_opponent = result["Matchup"].map(extract_opponent)
        result["Opponent"] = result["Opponent"].where(
            result["Opponent"].astype(str).str.strip().ne(""), derived_opponent
        )
    result["Opponent"] = result["Opponent"].map(normalize_team)
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
        game_date = parsed_time.tz_convert(EASTERN_TZ).date() if pd.notna(parsed_time) else slate_date
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


def _first_existing_column(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    lower = {str(c).lower(): c for c in frame.columns}
    for candidate in candidates:
        key = candidate.lower()
        if key in lower:
            return lower[key]
    return None


def _normalize_sdv_schedule_frame(raw: Any, slate_date: date) -> pd.DataFrame:
    frame = _to_pandas_frame(raw)
    if frame.empty:
        return pd.DataFrame()

    date_col = _first_existing_column(frame, [
        "game_date", "date", "start_date", "game_datetime", "game_date_time",
        "start_time", "date_time", "game_time", "game_datetime_utc",
    ])
    home_col = _first_existing_column(frame, [
        "home_team_abbreviation", "home_team_abbrev", "home_abbreviation",
        "home_team_name", "home_team_display_name", "home_team", "home",
    ])
    away_col = _first_existing_column(frame, [
        "away_team_abbreviation", "away_team_abbrev", "away_abbreviation",
        "away_team_name", "away_team_display_name", "away_team", "away",
    ])
    id_col = _first_existing_column(frame, ["game_id", "event_id", "id", "gameid"])

    if not home_col or not away_col:
        return pd.DataFrame()

    # SportsDataverse schedule frames may expose either a true UTC timestamp or a
    # date-only game_date.  A date-only value such as 2026-08-12 is already the
    # schedule's calendar date and MUST NOT be interpreted as midnight UTC, or an
    # Eastern conversion would incorrectly move it to 2026-08-11.
    rows: list[dict[str, Any]] = []
    for idx, row in frame.iterrows():
        home = normalize_team(row.get(home_col, ""))
        away = normalize_team(row.get(away_col, ""))
        if home not in TEAM_NAMES or away not in TEAM_NAMES or home == away:
            continue

        raw_date = row.get(date_col, "") if date_col else ""
        raw_text = str(raw_date or "").strip()
        if not raw_text or raw_text.lower() in {"nan", "nat", "none"}:
            continue

        # Detect YYYY-MM-DD/date-only values and compare them directly.
        date_only = False
        if len(raw_text) <= 10 and raw_text[:4].isdigit() and "-" in raw_text:
            date_only = True
        elif date_col and date_col.lower() in {"game_date", "date", "start_date"} and "t" not in raw_text.lower() and ":" not in raw_text:
            date_only = True

        if date_only:
            parsed_day = pd.to_datetime(raw_text, errors="coerce")
            if pd.isna(parsed_day) or parsed_day.date() != slate_date:
                continue
            time_utc = ""
        else:
            parsed = pd.to_datetime(raw_text, utc=True, errors="coerce")
            if pd.isna(parsed):
                continue
            local_date = parsed.tz_convert(EASTERN_TZ).date()
            if local_date != slate_date:
                continue
            time_utc = parsed.isoformat()
        event_id = str(row.get(id_col, "") if id_col else "").strip() or f"sdv-{slate_date}-{away}-{home}"
        rows.append({
            "EventID": event_id,
            "GameDate": slate_date,
            "GameTimeUTC": time_utc,
            "Away": away,
            "Home": home,
            "Game": f"{away} @ {home}",
            "Source": "SportsDataverse schedule release",
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(["Away", "Home", "GameDate"])


def fetch_sportsdataverse_schedule(slate_date: date) -> pd.DataFrame:
    """Load a WNBA schedule from SportsDataverse's published release data when available."""
    try:
        import sportsdataverse as sdv
    except Exception as exc:
        raise RuntimeError(f"SportsDataverse unavailable: {type(exc).__name__}: {exc}") from exc

    wnba = getattr(sdv, "wnba", None)
    if wnba is None:
        raise RuntimeError("SportsDataverse WNBA module is unavailable")

    candidates = ["load_wnba_schedule", "load_wnba_schedules"]
    errors: list[str] = []
    for name in candidates:
        func = getattr(wnba, name, None)
        if not callable(func):
            continue
        attempts = [
            (([slate_date.year],), {}),
            ((), {"seasons": [slate_date.year]}),
            ((), {"season": slate_date.year}),
        ]
        for args, kwargs in attempts:
            try:
                raw = func(*args, **kwargs)
                parsed = _normalize_sdv_schedule_frame(raw, slate_date)
                if not parsed.empty:
                    return parsed
            except TypeError as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                break
    available = [n for n in dir(wnba) if "schedule" in n.lower() and "wnba" in n.lower()]
    detail = "; ".join(errors[-3:]) if errors else "no compatible load_wnba_schedule function found"
    if available:
        detail += f"; available schedule functions: {', '.join(available[:8])}"
    raise RuntimeError(detail)


def _odds_events_to_schedule(events: pd.DataFrame, slate_date: date) -> pd.DataFrame:
    if events is None or events.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, row in events.iterrows():
        away = normalize_team(row.get("AwayTeam") or row.get("AwayName") or "")
        home = normalize_team(row.get("HomeTeam") or row.get("HomeName") or "")
        if away not in TEAM_NAMES or home not in TEAM_NAMES or away == home:
            continue
        parsed = pd.to_datetime(row.get("GameTimeUTC"), utc=True, errors="coerce")
        if pd.notna(parsed) and parsed.tz_convert(EASTERN_TZ).date() != slate_date:
            continue
        rows.append({
            "EventID": str(row.get("TheOddsEventID") or f"odds-{slate_date}-{away}-{home}"),
            "GameDate": slate_date,
            "GameTimeUTC": parsed.isoformat() if pd.notna(parsed) else str(row.get("GameTimeUTC") or ""),
            "Away": away,
            "Home": home,
            "Game": f"{away} @ {home}",
            "Source": "The Odds API events",
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(["Away", "Home", "GameDate"])


def fetch_official_schedule(slate_date: date, odds_api_key: str = "") -> tuple[pd.DataFrame, list[str]]:
    """Load the live prop slate from date-authoritative sources first."""
    errors: list[str] = []

    # 1) PrizePicks live game relationships. This is the primary slate authority
    # because the board is using PrizePicks lines and PP dates each live projection.
    try:
        frame = fetch_prizepicks_schedule(slate_date)
        if not frame.empty:
            return frame, errors
        errors.append(f"PrizePicks live slate: no fully oriented games found for {slate_date}")
    except Exception as exc:
        errors.append(f"PrizePicks live slate: {type(exc).__name__}: {exc}")

    # 2) The Odds API events endpoint supplies explicit commence_time + home/away.
    if str(odds_api_key or "").strip():
        try:
            events, _meta = fetch_the_odds_events(str(odds_api_key).strip(), DEFAULT_THE_ODDS_SPORT_KEY, slate_date)
            frame = _odds_events_to_schedule(events, slate_date)
            if not frame.empty:
                return frame, errors
            errors.append(f"The Odds API events: no games found for {slate_date}")
        except Exception as exc:
            errors.append(f"The Odds API events: {type(exc).__name__}: {exc}")
    else:
        errors.append("The Odds API events: no API key entered")

    # The WNBA CDN and ESPN are intentionally skipped here. On this local
    # installation both hosts have repeatedly returned HTML/403/timeouts, and
    # waiting on them makes the refresh button look broken. PrizePicks + The Odds
    # API are the live slate sources; manual entry remains the final fallback.
    errors.append("WNBA/ESPN schedule fallbacks skipped on local board (known blocked sources)")
    return pd.DataFrame(), errors


# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# The Odds API WNBA player prop-line API
# -----------------------------------------------------------------------------


def _the_odds_request(api_key: str, path: str, params: dict[str, Any] | None = None, timeout: int = 30) -> requests.Response:
    url = f"{THE_ODDS_API_BASE}{path}"
    clean_params = {k: v for k, v in (params or {}).items() if v not in [None, "", [], ()]}
    clean_params["apiKey"] = str(api_key or "").strip()
    response = requests.get(url, params=clean_params, timeout=timeout)
    response.raise_for_status()
    return response


def _team_candidates_from_event(event: dict[str, Any]) -> tuple[str, str]:
    away = normalize_team(event.get("away_team") or event.get("awayTeam") or event.get("away"))
    home = normalize_team(event.get("home_team") or event.get("homeTeam") or event.get("home"))
    return away, home


@st.cache_data(ttl=300, show_spinner=False)
def fetch_the_odds_events(api_key: str, sport_key: str, slate_date: date) -> tuple[pd.DataFrame, dict[str, str]]:
    """Fetch WNBA event IDs from The Odds API for a slate date."""
    if not api_key:
        return pd.DataFrame(), {}
    sport_key = str(sport_key or DEFAULT_THE_ODDS_SPORT_KEY).strip() or DEFAULT_THE_ODDS_SPORT_KEY
    # Wide UTC window handles U.S. evening games and date rollover.
    start_dt = datetime.combine(slate_date, datetime.min.time(), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    end_dt = datetime.combine(slate_date + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    params = {
        "dateFormat": "iso",
        "commenceTimeFrom": start_dt,
        "commenceTimeTo": end_dt,
    }
    response = _the_odds_request(api_key, f"/sports/{sport_key}/events", params, timeout=30)
    events = response.json()
    if not isinstance(events, list):
        events = []
    rows: list[dict[str, Any]] = []
    debug_rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        away, home = _team_candidates_from_event(event)
        game = f"{away} @ {home}" if away and home else ""
        row = {
            "Game": game,
            "TheOddsEventID": str(event.get("id") or ""),
            "GameTimeUTC": str(event.get("commence_time") or ""),
            "AwayTeam": away,
            "HomeTeam": home,
            "AwayName": str(event.get("away_team") or ""),
            "HomeName": str(event.get("home_team") or ""),
            "Source": "The Odds API",
        }
        if game and row["TheOddsEventID"]:
            rows.append(row)
        debug_rows.append(row)
    st.session_state["wnba_the_odds_event_debug"] = pd.DataFrame(debug_rows)
    frame = pd.DataFrame(rows).drop_duplicates(["TheOddsEventID"]) if rows else pd.DataFrame()
    meta = {
        "provider": "The Odds API",
        "endpoint": f"/sports/{sport_key}/events",
        "raw_events": str(len(events)),
        "parsed_events": str(len(frame)),
        "requests_remaining": response.headers.get("x-requests-remaining", ""),
        "requests_used": response.headers.get("x-requests-used", ""),
        "requests_last": response.headers.get("x-requests-last", ""),
    }
    return frame, meta


@st.cache_data(ttl=300, show_spinner=False)
def fetch_the_odds_event_props(
    api_key: str,
    sport_key: str,
    event_id: str,
    bookmaker_ids: tuple[str, ...] = ("hardrockbet_fl", "hardrockbet"),
    markets: tuple[str, ...] = ("player_points", "player_rebounds", "player_assists", "player_points_rebounds_assists"),
    regions: str = "us,us2",
    discover_all_books: bool = False,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Fetch and parse player prop quote rows for one The Odds API event."""
    if not api_key or not event_id:
        return pd.DataFrame(), {}
    sport_key = str(sport_key or DEFAULT_THE_ODDS_SPORT_KEY).strip() or DEFAULT_THE_ODDS_SPORT_KEY
    bookmakers = ",".join(str(x).strip().lower() for x in bookmaker_ids if str(x).strip())
    params: dict[str, Any] = {
        "regions": regions,
        "markets": ",".join(markets),
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    # The Odds API ignores regions when bookmakers is supplied. Keep bookmakers for the
    # normal Hard Rock pull, but allow a discovery mode with no bookmaker filter so the
    # debug table can show which books/markets The Odds API actually returned.
    if bookmakers and not discover_all_books:
        params["bookmakers"] = bookmakers
    response = _the_odds_request(api_key, f"/sports/{sport_key}/events/{event_id}/odds", params, timeout=35)
    event = response.json()
    rows: list[dict[str, Any]] = []
    raw_market_labels: set[str] = set()
    raw_bookmakers: set[str] = set()
    if isinstance(event, dict):
        bookmakers_payload = event.get("bookmakers", []) or []
    else:
        bookmakers_payload = []
    for bookmaker in bookmakers_payload:
        if not isinstance(bookmaker, dict):
            continue
        book_key = str(bookmaker.get("key") or "").lower().replace(" ", "_").replace("-", "_")
        book_label = str(bookmaker.get("title") or BOOKMAKER_LABELS.get(book_key, book_key) or book_key)
        raw_bookmakers.add(book_key)
        for market in bookmaker.get("markets", []) or []:
            if not isinstance(market, dict):
                continue
            market_key = str(market.get("key") or "")
            raw_market_labels.add(market_key)
            if market_key not in {config["market"] for config in TARGETS.values()}:
                continue
            for outcome in market.get("outcomes", []) or []:
                if not isinstance(outcome, dict):
                    continue
                side = str(outcome.get("name") or "").title()
                if side.lower() not in {"over", "under"}:
                    continue
                player_name = str(outcome.get("description") or outcome.get("participant") or outcome.get("player") or "").strip()
                if not player_name:
                    continue
                line = pd.to_numeric(pd.Series([outcome.get("point")]), errors="coerce").iloc[0]
                price = pd.to_numeric(pd.Series([outcome.get("price")]), errors="coerce").iloc[0]
                if pd.isna(line):
                    continue
                rows.append({
                    "EventID": str(event_id),
                    "TheOddsEventID": str(event_id),
                    "Bookmaker": book_label,
                    "BookmakerKey": book_key,
                    "Market": market_key,
                    "Player": player_name,
                    "NameKey": normalize_player_name(player_name),
                    "Side": side,
                    "Line": float(line),
                    "Odds": price if pd.notna(price) else np.nan,
                    "Updated": str(market.get("last_update") or bookmaker.get("last_update") or ""),
                    "MarketType": "ou",
                    "Source": "The Odds API",
                    "OddID": f"{book_key}:{market_key}:{player_name}:{side}:{line}",
                })
    meta = {
        "provider": "The Odds API",
        "endpoint": f"/sports/{sport_key}/events/{event_id}/odds",
        "requested_bookmakers": bookmakers if not discover_all_books else "DISCOVERY: all returned books",
        "regions": regions,
        "returned_bookmakers": ", ".join(sorted(raw_bookmakers)),
        "raw_markets": ", ".join(sorted(raw_market_labels)),
        "parsed_quote_rows": str(len(rows)),
        "requests_remaining": response.headers.get("x-requests-remaining", ""),
        "requests_used": response.headers.get("x-requests-used", ""),
        "requests_last": response.headers.get("x-requests-last", ""),
    }
    return pd.DataFrame(rows), meta





@st.cache_data(ttl=300, show_spinner=False)
def fetch_the_odds_event_markets(
    api_key: str,
    sport_key: str,
    event_id: str,
    bookmaker_ids: tuple[str, ...] = (),
    regions: str = "us,us2",
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Return available market keys by bookmaker for one The Odds API event."""
    if not api_key or not event_id:
        return pd.DataFrame(), {}
    sport_key = str(sport_key or DEFAULT_THE_ODDS_SPORT_KEY).strip() or DEFAULT_THE_ODDS_SPORT_KEY
    bookmakers = ",".join(str(x).strip().lower() for x in bookmaker_ids if str(x).strip())
    params: dict[str, Any] = {"regions": regions, "dateFormat": "iso"}
    if bookmakers:
        params["bookmakers"] = bookmakers
    response = _the_odds_request(api_key, f"/sports/{sport_key}/events/{event_id}/markets", params, timeout=30)
    event = response.json()
    rows: list[dict[str, Any]] = []
    if isinstance(event, dict):
        for bookmaker in event.get("bookmakers", []) or []:
            if not isinstance(bookmaker, dict):
                continue
            book_key = str(bookmaker.get("key") or "").lower()
            book_title = str(bookmaker.get("title") or BOOKMAKER_LABELS.get(book_key, book_key) or book_key)
            market_keys = []
            for market in bookmaker.get("markets", []) or []:
                if isinstance(market, dict) and market.get("key"):
                    market_keys.append(str(market.get("key")))
            rows.append({
                "TheOddsEventID": str(event_id),
                "BookmakerKey": book_key,
                "Bookmaker": book_title,
                "MarketCount": len(market_keys),
                "AvailableMarkets": ", ".join(sorted(set(market_keys))),
                "HasPoints": "player_points" in set(market_keys),
                "HasRebounds": "player_rebounds" in set(market_keys),
                "HasAssists": "player_assists" in set(market_keys),
                "HasPRA": "player_points_rebounds_assists" in set(market_keys),
            })
    meta = {
        "provider": "The Odds API",
        "endpoint": f"/sports/{sport_key}/events/{event_id}/markets",
        "requested_bookmakers": bookmakers or "all returned books",
        "regions": regions,
        "rows": str(len(rows)),
        "requests_remaining": response.headers.get("x-requests-remaining", ""),
        "requests_used": response.headers.get("x-requests-used", ""),
        "requests_last": response.headers.get("x-requests-last", ""),
    }
    return pd.DataFrame(rows), meta


@st.cache_data(ttl=300, show_spinner=False)
def fetch_the_odds_featured_odds_check(
    api_key: str,
    sport_key: str,
    event_id: str,
    regions: str = "us,us2",
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Small diagnostic to confirm the API key/sport/event can return normal markets."""
    if not api_key or not event_id:
        return pd.DataFrame(), {}
    sport_key = str(sport_key or DEFAULT_THE_ODDS_SPORT_KEY).strip() or DEFAULT_THE_ODDS_SPORT_KEY
    params = {
        "regions": regions,
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    response = _the_odds_request(api_key, f"/sports/{sport_key}/events/{event_id}/odds", params, timeout=30)
    event = response.json()
    rows: list[dict[str, Any]] = []
    if isinstance(event, dict):
        for bookmaker in event.get("bookmakers", []) or []:
            if not isinstance(bookmaker, dict):
                continue
            book_key = str(bookmaker.get("key") or "").lower()
            book_title = str(bookmaker.get("title") or BOOKMAKER_LABELS.get(book_key, book_key) or book_key)
            markets = [str(m.get("key")) for m in bookmaker.get("markets", []) or [] if isinstance(m, dict) and m.get("key")]
            rows.append({
                "TheOddsEventID": str(event_id),
                "BookmakerKey": book_key,
                "Bookmaker": book_title,
                "FeaturedMarketsReturned": ", ".join(sorted(set(markets))),
            })
    meta = {
        "provider": "The Odds API",
        "endpoint": f"/sports/{sport_key}/events/{event_id}/odds featured check",
        "rows": str(len(rows)),
        "requests_remaining": response.headers.get("x-requests-remaining", ""),
        "requests_used": response.headers.get("x-requests-used", ""),
        "requests_last": response.headers.get("x-requests-last", ""),
    }
    return pd.DataFrame(rows), meta

def _compact_bookmaker(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def combine_odds_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    valid = [frame for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty]
    if not valid:
        return pd.DataFrame()
    combined = pd.concat(valid, ignore_index=True)
    required = ["EventID", "BookmakerKey", "Market", "NameKey", "Side", "Line"]
    for col in required:
        if col not in combined.columns:
            combined[col] = "" if col != "Line" else np.nan
    return combined.drop_duplicates(required, keep="last")


def _is_sportsbook_quote(row: pd.Series) -> bool:
    key = str(row.get("BookmakerKey", "") or "").lower().replace(" ", "_").replace("-", "_")
    label = _compact_bookmaker(row.get("Bookmaker", ""))
    if key in SPORTSBOOK_BOOKMAKER_KEYS:
        return True
    return any(_compact_bookmaker(book_key) in label or label.startswith(_compact_bookmaker(book_key)) for book_key in SPORTSBOOK_BOOKMAKER_KEYS)


def _best_price(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return np.nan
    return float(numeric.max())


def choose_market_quote(group: pd.DataFrame, source_mode: str) -> dict[str, Any]:
    if group is None or group.empty:
        return {}
    data = group.copy()
    source_mode = str(source_mode or "Consensus sportsbooks")
    normalized_mode = source_mode.strip().lower()

    # Keep the old label working for saved sessions, but make sportsbook consensus the default behavior.
    consensus_modes = {"consensus", "consensus sportsbooks", "consensus sportsbook", "sportsbook consensus"}
    best_over_modes = {"best sportsbook over line", "best over line", "best over"}
    best_under_modes = {"best sportsbook under line", "best under line", "best under"}

    if normalized_mode in consensus_modes or normalized_mode in best_over_modes or normalized_mode in best_under_modes:
        sports_mask = data.apply(_is_sportsbook_quote, axis=1)
        sportsbook_data = data[sports_mask].copy()
        if not sportsbook_data.empty:
            data = sportsbook_data

    if normalized_mode in best_over_modes:
        numeric_line = pd.to_numeric(data.get("Line", pd.Series(dtype=float)), errors="coerce")
        valid = data[numeric_line.notna()].copy()
        if valid.empty:
            return {}
        numeric_line = pd.to_numeric(valid["Line"], errors="coerce")
        # For an over, the best book is the lowest line. Tie-break by best over price.
        target_line = float(numeric_line.min())
        near = valid[np.isclose(numeric_line, target_line, atol=0.01)].copy()
        over_prices = pd.to_numeric(near.loc[near["Side"].astype(str).str.lower().eq("over"), "Odds"], errors="coerce") if "Odds" in near.columns else pd.Series(dtype=float)
        best_over = _best_price(over_prices)
        if np.isfinite(best_over):
            preferred_books = near[near["Side"].astype(str).str.lower().eq("over") & pd.to_numeric(near["Odds"], errors="coerce").eq(best_over)]
            if not preferred_books.empty:
                preferred_key = str(preferred_books.iloc[0].get("BookmakerKey", ""))
                near = near[near.get("BookmakerKey", pd.Series(index=near.index, dtype=object)).astype(str).eq(preferred_key)]
        under_prices = pd.to_numeric(near.loc[near["Side"].astype(str).str.lower().eq("under"), "Odds"], errors="coerce") if "Odds" in near.columns else pd.Series(dtype=float)
        books = sorted(near.get("Bookmaker", pd.Series(dtype=str)).dropna().astype(str).unique())
        return {
            "Line": target_line,
            "OverOdds": best_over,
            "UnderOdds": _best_price(under_prices),
            "MarketOverProb": no_vig_over_probability(best_over, _best_price(under_prices)),
            "Source": f"Best over · {books[0] if books else 'sportsbook'}",
        }

    if normalized_mode in best_under_modes:
        numeric_line = pd.to_numeric(data.get("Line", pd.Series(dtype=float)), errors="coerce")
        valid = data[numeric_line.notna()].copy()
        if valid.empty:
            return {}
        numeric_line = pd.to_numeric(valid["Line"], errors="coerce")
        # For an under, the best book is the highest line. Tie-break by best under price.
        target_line = float(numeric_line.max())
        near = valid[np.isclose(numeric_line, target_line, atol=0.01)].copy()
        under_prices = pd.to_numeric(near.loc[near["Side"].astype(str).str.lower().eq("under"), "Odds"], errors="coerce") if "Odds" in near.columns else pd.Series(dtype=float)
        best_under = _best_price(under_prices)
        if np.isfinite(best_under):
            preferred_books = near[near["Side"].astype(str).str.lower().eq("under") & pd.to_numeric(near["Odds"], errors="coerce").eq(best_under)]
            if not preferred_books.empty:
                preferred_key = str(preferred_books.iloc[0].get("BookmakerKey", ""))
                near = near[near.get("BookmakerKey", pd.Series(index=near.index, dtype=object)).astype(str).eq(preferred_key)]
        over_prices = pd.to_numeric(near.loc[near["Side"].astype(str).str.lower().eq("over"), "Odds"], errors="coerce") if "Odds" in near.columns else pd.Series(dtype=float)
        books = sorted(near.get("Bookmaker", pd.Series(dtype=str)).dropna().astype(str).unique())
        return {
            "Line": target_line,
            "OverOdds": _best_price(over_prices),
            "UnderOdds": best_under,
            "MarketOverProb": no_vig_over_probability(_best_price(over_prices), best_under),
            "Source": f"Best under · {books[0] if books else 'sportsbook'}",
        }

    if normalized_mode not in consensus_modes:
        label_match = data["Bookmaker"].map(lambda value: bookmaker_matches_source(value, source_mode)) if "Bookmaker" in data.columns else pd.Series(False, index=data.index)
        id_match = data["BookmakerKey"].map(lambda value: bookmaker_matches_source(value, source_mode)) if "BookmakerKey" in data.columns else pd.Series(False, index=data.index)
        data = data[label_match | id_match]
        if data.empty:
            return {}

    lines = pd.to_numeric(data.get("Line", pd.Series(dtype=float)), errors="coerce").dropna()
    if lines.empty:
        return {}
    # If multiple books are used, choose the median line. For one book, this is that book's line.
    target_line = float(lines.median())
    numeric_line = pd.to_numeric(data["Line"], errors="coerce")
    near = data[np.isclose(numeric_line, target_line, atol=0.01)].copy()
    over_prices = pd.to_numeric(near.loc[near["Side"].astype(str).str.lower().eq("over"), "Odds"], errors="coerce").dropna() if "Odds" in near.columns else pd.Series(dtype=float)
    under_prices = pd.to_numeric(near.loc[near["Side"].astype(str).str.lower().eq("under"), "Odds"], errors="coerce").dropna() if "Odds" in near.columns else pd.Series(dtype=float)
    over_odds = float(over_prices.median()) if not over_prices.empty else np.nan
    under_odds = float(under_prices.median()) if not under_prices.empty else np.nan
    books = sorted(near.get("Bookmaker", pd.Series(dtype=str)).dropna().astype(str).unique())
    if normalized_mode in consensus_modes:
        source_label = f"Consensus sportsbooks ({len(books)} books)"
    else:
        source_label = source_mode
    return {
        "Line": target_line,
        "OverOdds": over_odds,
        "UnderOdds": under_odds,
        "MarketOverProb": no_vig_over_probability(over_odds, under_odds),
        "Source": source_label,
    }


def bookmaker_matches_source(value: Any, source_mode: str) -> bool:
    wanted = _compact_bookmaker(source_mode)
    candidate = _compact_bookmaker(value)
    if not wanted:
        return True
    if wanted == "hardrockbet":
        return candidate.startswith("hardrockbet") or candidate == "hardrock"
    return candidate == wanted or candidate.startswith(wanted)




# -----------------------------------------------------------------------------
# PrizePicks live WNBA projections
# -----------------------------------------------------------------------------

PRIZEPICKS_STAT_TO_MARKET = {
    "points": "player_points",
    "point": "player_points",
    "rebounds": "player_rebounds",
    "rebound": "player_rebounds",
    "assists": "player_assists",
    "assist": "player_assists",
    "pts+rebs+asts": "player_points_rebounds_assists",
    "pts + rebs + asts": "player_points_rebounds_assists",
    "points+rebounds+assists": "player_points_rebounds_assists",
    "points + rebounds + assists": "player_points_rebounds_assists",
    "pra": "player_points_rebounds_assists",
}


def _normalize_prizepicks_stat_type(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    compact = text.replace(" ", "")
    aliases = {
        "points": "player_points",
        "rebounds": "player_rebounds",
        "assists": "player_assists",
        "pts+rebs+asts": "player_points_rebounds_assists",
        "points+rebounds+assists": "player_points_rebounds_assists",
        "pra": "player_points_rebounds_assists",
    }
    return PRIZEPICKS_STAT_TO_MARKET.get(text) or aliases.get(compact, "")


def _prizepicks_headers() -> dict[str, str]:
    # Normal browser-like headers only. If PrizePicks returns a challenge/403, the
    # dashboard reports it and keeps the existing upload fallback rather than trying
    # to bypass access controls.
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        "Referer": "https://app.prizepicks.com/",
        "Origin": "https://app.prizepicks.com",
    }



def _pp_related_object(rels: dict[str, Any], included_map: dict[tuple[str, str], dict[str, Any]], *names: str) -> dict[str, Any]:
    """Resolve the first PrizePicks JSON:API relationship present in *names*."""
    for name in names:
        rel = rels.get(name) or {}
        data = rel.get("data") if isinstance(rel, dict) else None
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            continue
        rid = str(data.get("id") or "")
        rtype = str(data.get("type") or "")
        if rid:
            obj = included_map.get((rtype, rid))
            if obj:
                return obj
            # Some PrizePicks payload versions rename relationship types.
            for (itype, iid), candidate in included_map.items():
                if iid == rid:
                    return candidate
    return {}


def _pp_first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return ""


def _pp_game_date(attrs: dict[str, Any], game_attrs: dict[str, Any]) -> tuple[str, date | None]:
    raw = _pp_first_value(
        attrs,
        "start_time", "startTime", "starts_at", "scheduled_at", "game_time",
    ) or _pp_first_value(
        game_attrs,
        "start_time", "startTime", "starts_at", "scheduled_at", "game_time", "start_date", "game_date",
    )
    raw_text = str(raw or "").strip()
    if not raw_text:
        return "", None

    # PrizePicks sometimes exposes game_date as YYYY-MM-DD.  That value is
    # already the event calendar date; interpreting midnight as UTC would shift
    # an Aug 12 game backward into Aug 11 in Eastern time.
    if len(raw_text) <= 10 and raw_text[:4].isdigit() and "-" in raw_text and "T" not in raw_text.upper() and ":" not in raw_text:
        parsed_day = pd.to_datetime(raw_text, errors="coerce")
        return raw_text, (parsed_day.date() if pd.notna(parsed_day) else None)

    parsed = pd.to_datetime(raw_text, errors="coerce", utc=True)
    if pd.isna(parsed):
        return raw_text, None
    try:
        return raw_text, parsed.tz_convert(EASTERN_TZ).date()
    except Exception:
        return raw_text, parsed.date()


def _pp_player_name(p_attrs: dict[str, Any], attrs: dict[str, Any]) -> str:
    name = _pp_first_value(
        p_attrs,
        "name", "display_name", "displayName", "full_name", "fullName", "player_name", "playerName",
    )
    if name:
        return str(name).strip()
    desc = str(_pp_first_value(attrs, "description", "player_name", "playerName") or "").strip()
    # Description is sometimes formatted as "Player Name - Stat".
    desc = re.split(r"\s+-\s+|\s+—\s+|\s+–\s+", desc, maxsplit=1)[0].strip()
    return desc


def _pp_team_from_objects(p_attrs: dict[str, Any], team_obj: dict[str, Any], game_attrs: dict[str, Any], player_name: str = "") -> str:
    tattrs = team_obj.get("attributes") or {}
    raw = _pp_first_value(
        p_attrs, "team", "team_abbreviation", "teamAbbreviation", "team_name", "teamName",
    ) or _pp_first_value(
        tattrs, "abbreviation", "team_abbreviation", "name", "display_name", "displayName",
    )
    return normalize_team(raw) if raw else ""


def _pp_game_team(game_obj: dict[str, Any], included_map: dict[tuple[str, str], dict[str, Any]], side: str) -> str:
    """Best-effort extraction of a PrizePicks game home/away team."""
    attrs = game_obj.get("attributes") or {}
    side = str(side).lower()
    keys = (
        f"{side}_team_abbreviation", f"{side}TeamAbbreviation",
        f"{side}_team", f"{side}Team", f"{side}_team_name", f"{side}TeamName",
        side,
    )
    raw = _pp_first_value(attrs, *keys)
    if raw:
        team = normalize_team(raw)
        if team in TEAM_NAMES:
            return team

    grels = game_obj.get("relationships") or {}
    obj = _pp_related_object(
        grels, included_map,
        f"{side}_team", f"{side}Team", f"{side}_new_team",
    )
    tattrs = obj.get("attributes") or {}
    raw = _pp_first_value(tattrs, "abbreviation", "team_abbreviation", "name", "display_name", "displayName")
    team = normalize_team(raw) if raw else ""
    return team if team in TEAM_NAMES else ""


def _teams_from_game_description(description: Any) -> tuple[str, str]:
    """Parse common 'AWAY @ HOME' / 'AWAY at HOME' PrizePicks descriptions."""
    text = str(description or "").strip()
    if not text:
        return "", ""
    upper = re.sub(r"\s+", " ", text.upper()).strip()

    # Prefer explicit separators because their order encodes away/home.
    parts = re.split(r"\s+@\s+|\s+AT\s+", upper, maxsplit=1)
    if len(parts) == 2:
        away, home = normalize_team(parts[0]), normalize_team(parts[1])
        if away in TEAM_NAMES and home in TEAM_NAMES and away != home:
            return away, home

    # 'vs' feeds commonly list HOME vs AWAY. Use it only as a fallback.
    parts = re.split(r"\s+VS\.?\s+|\s+VERSUS\s+", upper, maxsplit=1)
    if len(parts) == 2:
        home, away = normalize_team(parts[0]), normalize_team(parts[1])
        if away in TEAM_NAMES and home in TEAM_NAMES and away != home:
            return away, home

    return "", ""


def _compact_name_key(value: Any) -> str:
    return normalize_player_name(value).replace(" ", "")


@st.cache_data(ttl=90, show_spinner=False)
def fetch_prizepicks_live_rows(slate_date: date, league_id: str = PRIZEPICKS_WNBA_LEAGUE_ID) -> tuple[pd.DataFrame, dict[str, str]]:
    """Fetch current public PrizePicks WNBA projections and normalize supported props.

    PrizePicks does not publish sportsbook-style prices here, so these rows supply the
    line only. Sportsbook quotes can still be used separately for market consensus.
    """
    cache_path = WNBA_CACHE_DIR / f"prizepicks_wnba_{slate_date.isoformat()}.csv"
    params = {
        "league_id": str(league_id or PRIZEPICKS_WNBA_LEAGUE_ID),
        "per_page": 500,
        "single_stat": "true",
        "game_mode": "pickem",
    }
    errors: list[str] = []
    payload: dict[str, Any] | None = None
    used_url = ""
    status_code = ""
    for base in PRIZEPICKS_API_BASES:
        url = f"{base}/projections"
        try:
            response = requests.get(url, params=params, headers=_prizepicks_headers(), timeout=20)
            status_code = str(response.status_code)
            response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "").lower()
            if "json" not in content_type and not response.text.lstrip().startswith(("{", "[")):
                raise RuntimeError(f"non-JSON response ({content_type or 'unknown content type'})")
            obj = response.json()
            if not isinstance(obj, dict):
                raise RuntimeError("unexpected PrizePicks payload shape")
            payload = obj
            used_url = response.url
            break
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")

    if payload is None:
        if cache_path.exists():
            try:
                cached = pd.read_csv(cache_path)
                if not cached.empty:
                    return cached, {
                        "provider": "PrizePicks local cache",
                        "endpoint": str(cache_path),
                        "status": "cached",
                        "raw_projections": str(len(cached)),
                        "supported_slate_lines": str(len(cached)),
                        "warning": "Live PrizePicks fetch failed; using the last saved lines for this slate.",
                        "live_errors": " | ".join(errors)[:1500],
                    }
            except Exception:
                pass
        raise RuntimeError(" | ".join(errors) if errors else "PrizePicks request failed")

    included = payload.get("included") or []
    included_map: dict[tuple[str, str], dict[str, Any]] = {}
    for item in included:
        if not isinstance(item, dict):
            continue
        included_map[(str(item.get("type") or ""), str(item.get("id") or ""))] = item

    rows: list[dict[str, Any]] = []
    ignored_stats: set[str] = set()
    all_data = payload.get("data") or []
    for item in all_data:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes") or {}
        rels = item.get("relationships") or {}
        stat_raw = str(attrs.get("stat_type") or attrs.get("statType") or "").strip()
        market = _normalize_prizepicks_stat_type(stat_raw)
        if not market:
            if stat_raw:
                ignored_stats.add(stat_raw)
            continue
        line = pd.to_numeric(pd.Series([attrs.get("line_score", attrs.get("line"))]), errors="coerce").iloc[0]
        if pd.isna(line):
            continue

        player_obj = _pp_related_object(rels, included_map, "new_player", "player", "projection_player")
        p_attrs = player_obj.get("attributes") or {}
        player_name = _pp_player_name(p_attrs, attrs)
        if not player_name:
            continue

        game_obj = _pp_related_object(rels, included_map, "game", "new_game", "event")
        game_attrs = game_obj.get("attributes") or {}
        team_obj = _pp_related_object(rels, included_map, "team", "new_team")
        start_raw, game_date_et = _pp_game_date(attrs, game_attrs)
        pp_home = _pp_game_team(game_obj, included_map, "home")
        pp_away = _pp_game_team(game_obj, included_map, "away")
        if not pp_home or not pp_away:
            desc_away, desc_home = _teams_from_game_description(
                _pp_first_value(game_attrs, "description", "name", "short_name", "shortName")
            )
            pp_away = pp_away or desc_away
            pp_home = pp_home or desc_home
        # Current PrizePicks payloads commonly put schedule context on the game relationship.
        # Once a date is known, enforce the selected WNBA slate strictly.
        if game_date_et is not None and game_date_et != slate_date:
            continue

        # PrizePicks can expose regular, Demon and Goblin lines for the same
        # player/stat.  Do NOT use the generic JSON `type` field here -- in some
        # payload versions it describes the object (for example single_stat), not
        # the projection variant.  That previously caused all variants to tie and
        # the newest-updated Demon line could win the de-duplication step.
        variant_fields = [
            attrs.get("projection_type"), attrs.get("projectionType"),
            attrs.get("odds_type"), attrs.get("oddsType"),
            attrs.get("promo_type"), attrs.get("promoType"),
            attrs.get("pick_type"), attrs.get("pickType"),
            attrs.get("line_type"), attrs.get("lineType"),
            attrs.get("tier"), attrs.get("label"),
        ]
        variant_blob = " ".join(str(v or "") for v in variant_fields).strip().lower()
        if "demon" in variant_blob:
            promo_type = "demon"
        elif "goblin" in variant_blob or "gobby" in variant_blob:
            promo_type = "goblin"
        elif "standard" in variant_blob or "normal" in variant_blob or "regular" in variant_blob:
            promo_type = "standard"
        else:
            promo_type = "unknown"
        is_promo = bool(attrs.get("is_promo") or attrs.get("isPromo") or attrs.get("is_discount") or attrs.get("isDiscount") or False)
        if promo_type == "unknown" and is_promo:
            promo_type = "promo"
        team = _pp_team_from_objects(p_attrs, team_obj, game_attrs, player_name)
        rows.append({
            "ProjectionID": str(item.get("id") or ""),
            "Player": player_name,
            "NameKey": normalize_player_name(player_name),
            "Team": team,
            "Market": market,
            "MarketRaw": stat_raw,
            "Line": float(line),
            "PromoType": promo_type,
            "IsPromo": is_promo,
            "StartTime": str(start_raw or ""),
            "SlateDate": game_date_et.isoformat() if game_date_et is not None else "",
            "GameID": str(game_obj.get("id") or ""),
            "AwayTeam": pp_away,
            "HomeTeam": pp_home,
            "GameDescription": str(_pp_first_value(game_attrs, "description", "name", "short_name", "shortName") or ""),
            "Updated": str(attrs.get("updated_at") or attrs.get("updatedAt") or ""),
            "Source": "PrizePicks live",
        })

    frame = pd.DataFrame(rows)
    if not frame.empty:
        # Keep ONE regular PrizePicks projection per player/stat.  Demon/Goblin
        # variants are alternate MORE-only projections and must never replace the
        # normal line on the main Player Board. PrizePicks documents regular,
        # Demon and Goblin projections as separate alternatives.
        selected_groups: list[pd.DataFrame] = []
        for _, grp in frame.groupby(["NameKey", "Market"], sort=False, dropna=False):
            grp = grp.copy()
            kind = grp["PromoType"].astype(str).str.lower()

            # 1) Explicitly marked standard/normal/regular always wins.
            standard = grp[kind.isin({"standard", "normal", "regular"})].copy()
            if not standard.empty:
                selected_groups.append(standard.sort_values("Updated", ascending=False).head(1))
                continue

            # 2) Remove anything explicitly identified as an alternate/promo.
            #    An unmarked row is much more likely to be the regular square than
            #    a known Demon/Goblin/Taco/discount row.
            alternates = {"demon", "goblin", "gobby", "promo", "taco", "discount", "flash_sale", "flash sale"}
            candidates = grp[~kind.isin(alternates)].copy()
            if candidates.empty:
                # Do not silently put a known Demon/Goblin on the normal board.
                # If PP only exposes alternates for this player/stat, leave the
                # regular line blank rather than displaying the wrong square.
                continue

            # 3) Some current PP payloads leave projection variant unmarked.  When
            #    multiple unmarked lines coexist, regular is normally the center
            #    line because Goblins are lower and Demons are higher.  Choose the
            #    observation closest to the median instead of newest-updated.
            candidates["_LineNum"] = pd.to_numeric(candidates["Line"], errors="coerce")
            candidates = candidates[candidates["_LineNum"].notna()].copy()
            if candidates.empty:
                continue
            if candidates["_LineNum"].nunique() > 1:
                med = float(candidates["_LineNum"].median())
                candidates["_RegularDistance"] = (candidates["_LineNum"] - med).abs()
                candidates = candidates.sort_values(["_RegularDistance", "Updated"], ascending=[True, False])
            else:
                candidates = candidates.sort_values("Updated", ascending=False)
            selected_groups.append(candidates.head(1).drop(columns=[c for c in ["_LineNum", "_RegularDistance"] if c in candidates.columns]))

        if selected_groups:
            frame = pd.concat(selected_groups, ignore_index=True)
        else:
            frame = frame.iloc[0:0].copy()

    if not frame.empty:
        try:
            WNBA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            frame.to_csv(cache_path, index=False)
        except Exception:
            pass

    meta = {
        "provider": "PrizePicks",
        "parser_version": "v13-regular-only",
        "endpoint": used_url or "/projections",
        "status": status_code,
        "raw_projections": str(len(all_data)),
        "supported_slate_lines": str(len(frame)),
        "ignored_stat_types": ", ".join(sorted(ignored_stats))[:1000],
    }
    return frame, meta


def fetch_prizepicks_schedule(slate_date: date) -> pd.DataFrame:
    """Build the live WNBA slate from PrizePicks game relationships.

    PrizePicks is already the dashboard's primary line source, so its dated live
    game objects are a better authority for the current prop slate than a release
    dataset whose date field can lag or be interpreted differently.
    """
    pp_rows, _meta = fetch_prizepicks_live_rows(slate_date)
    if pp_rows is None or pp_rows.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    work = pp_rows.copy()
    if "SlateDate" in work.columns:
        work = work[work["SlateDate"].astype(str).str[:10] == slate_date.isoformat()].copy()
    if work.empty:
        return pd.DataFrame()

    # Prefer PrizePicks' explicit game id.  If absent, the dated start time is a
    # stable enough grouping key for the live slate.
    if "GameID" not in work.columns:
        work["GameID"] = ""
    work["_GameKey"] = work["GameID"].fillna("").astype(str)
    missing_key = work["_GameKey"].eq("")
    if "StartTime" in work.columns:
        work.loc[missing_key, "_GameKey"] = work.loc[missing_key, "StartTime"].fillna("").astype(str)

    for game_key, grp in work.groupby("_GameKey", dropna=False):
        away = ""
        home = ""
        if "AwayTeam" in grp.columns:
            vals = [normalize_team(x) for x in grp["AwayTeam"].dropna().astype(str) if normalize_team(x) in TEAM_NAMES]
            away = vals[0] if vals else ""
        if "HomeTeam" in grp.columns:
            vals = [normalize_team(x) for x in grp["HomeTeam"].dropna().astype(str) if normalize_team(x) in TEAM_NAMES]
            home = vals[0] if vals else ""

        if not away or not home:
            descriptions = grp.get("GameDescription", pd.Series(dtype=str)).dropna().astype(str).tolist()
            for desc in descriptions:
                d_away, d_home = _teams_from_game_description(desc)
                away = away or d_away
                home = home or d_home
                if away and home:
                    break

        # Last resort: identify the two participating teams from player props.
        # Do NOT silently invent home/away unless a description/relationship gave
        # us orientation; an incorrect Home/Away flag would contaminate projections.
        teams = []
        if "Team" in grp.columns:
            teams = sorted({normalize_team(x) for x in grp["Team"].dropna().astype(str) if normalize_team(x) in TEAM_NAMES})
        if (not away or not home) and len(teams) == 2:
            # Keep the game available only if a parseable description can orient it.
            # Otherwise let The Odds API / manual slate supply orientation.
            continue

        if away not in TEAM_NAMES or home not in TEAM_NAMES or away == home:
            continue

        start_raw = str(grp["StartTime"].dropna().astype(str).iloc[0]) if "StartTime" in grp.columns and not grp["StartTime"].dropna().empty else ""
        parsed = pd.to_datetime(start_raw, utc=True, errors="coerce")
        rows.append({
            "EventID": f"pp-{game_key}" if str(game_key) else f"pp-{slate_date}-{away}-{home}",
            "GameDate": slate_date,
            "GameTimeUTC": parsed.isoformat() if pd.notna(parsed) else start_raw,
            "Away": away,
            "Home": home,
            "Game": f"{away} @ {home}",
            "Source": "PrizePicks live slate",
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(["Away", "Home", "GameDate"])


def prizepicks_rows_to_odds_frame(pp_rows: pd.DataFrame, board: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Match normalized PrizePicks rows to the model board and emit O/U quote rows.

    Matching order: exact normalized name -> compact normalized name -> conservative
    fuzzy name match (team-aware when PrizePicks supplies a team). This handles
    apostrophes, suffixes and minor provider naming differences without broad matches.
    """
    if pp_rows is None or pp_rows.empty or board is None or board.empty:
        return pd.DataFrame(), pd.DataFrame()
    work = pp_rows.copy()
    if "NameKey" not in work.columns:
        work["NameKey"] = work.get("Player", pd.Series(dtype=str)).map(normalize_player_name)
    if "Market" not in work.columns and "MarketRaw" in work.columns:
        work["Market"] = work["MarketRaw"].map(_normalize_prizepicks_stat_type)
    work["Line"] = pd.to_numeric(work.get("Line"), errors="coerce")
    work = work[work["Line"].notna() & work["Market"].isin({cfg["market"] for cfg in TARGETS.values()})].copy()

    cols = [c for c in ["EventID", "Player", "NameKey", "Team", "Opponent", "Game"] if c in board.columns]
    board_lookup = board[cols].copy()
    if "NameKey" not in board_lookup.columns:
        board_lookup["NameKey"] = board_lookup["Player"].map(normalize_player_name)
    board_lookup["CompactNameKey"] = board_lookup["NameKey"].map(_compact_name_key)
    board_lookup["TeamNorm"] = board_lookup.get("Team", pd.Series("", index=board_lookup.index)).map(normalize_team)

    from difflib import SequenceMatcher
    debug_rows: list[dict[str, Any]] = []
    odds_rows: list[dict[str, Any]] = []
    for row in work.to_dict("records"):
        name_key = normalize_player_name(row.get("NameKey") or row.get("Player") or "")
        compact = _compact_name_key(name_key)
        pp_team = normalize_team(row.get("Team") or "")
        method = "exact"

        matches = board_lookup[board_lookup["NameKey"].astype(str).eq(name_key)].copy()
        if matches.empty and compact:
            matches = board_lookup[board_lookup["CompactNameKey"].astype(str).eq(compact)].copy()
            if not matches.empty:
                method = "compact"

        if pp_team and not matches.empty:
            tm = matches[matches["TeamNorm"].eq(pp_team)]
            if not tm.empty:
                matches = tm

        # Conservative fuzzy fallback only when exact/compact matching fails.
        # Require >= .93, and if a team is supplied only compare that team.
        fuzzy_score = 0.0
        if matches.empty and name_key:
            candidates = board_lookup
            if pp_team:
                same_team = candidates[candidates["TeamNorm"].eq(pp_team)]
                if not same_team.empty:
                    candidates = same_team
            scored = []
            for idx, candidate in candidates.iterrows():
                cand_key = str(candidate.get("NameKey") or "")
                score = SequenceMatcher(None, name_key, cand_key).ratio()
                scored.append((score, idx))
            scored.sort(reverse=True)
            if scored and scored[0][0] >= 0.93:
                # Avoid ambiguous fuzzy matches.
                if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.04:
                    fuzzy_score = float(scored[0][0])
                    matches = board_lookup.loc[[scored[0][1]]].copy()
                    method = "fuzzy"

        matched = not matches.empty
        closest = ""
        if not matched and name_key and not board_lookup.empty:
            scored_names = sorted(
                ((SequenceMatcher(None, name_key, str(x)).ratio(), str(x)) for x in board_lookup["NameKey"].dropna().unique()),
                reverse=True,
            )
            if scored_names:
                closest = f"{scored_names[0][1]} ({scored_names[0][0]:.2f})"

        debug_rows.append({
            "Player": row.get("Player", ""),
            "NameKey": name_key,
            "Team": pp_team,
            "Market": row.get("Market", ""),
            "Line": row.get("Line"),
            "SlateDate": row.get("SlateDate", ""),
            "StartTime": row.get("StartTime", ""),
            "PromoType": row.get("PromoType", "standard"),
            "Matched": matched,
            "MatchMethod": method if matched else "none",
            "FuzzyScore": fuzzy_score if method == "fuzzy" else np.nan,
            "MatchCount": int(len(matches)),
            "BoardPlayerCount": int(len(board_lookup)),
            "MatchedGame": str(matches.iloc[0].get("Game", "")) if matched else "",
            "ClosestBoardName": closest,
        })
        if not matched:
            continue
        for match in matches.to_dict("records"):
            for side in ("Over", "Under"):
                odds_rows.append({
                    "EventID": str(match.get("EventID") or ""),
                    "Bookmaker": "PrizePicks",
                    "BookmakerKey": "prizepicks",
                    "Market": str(row.get("Market") or ""),
                    "Player": str(row.get("Player") or match.get("Player") or ""),
                    "NameKey": str(match.get("NameKey") or name_key),
                    "Side": side,
                    "Line": float(row.get("Line")),
                    "Odds": np.nan,
                    "Updated": str(row.get("Updated") or ""),
                    "MarketType": "ou",
                    "Source": "PrizePicks regular",
                    "PromoType": str(row.get("PromoType") or "standard"),
                    "OddID": f"prizepicks:{row.get('ProjectionID','')}:{name_key}:{row.get('Market')}:{side}",
                })
    return pd.DataFrame(odds_rows), pd.DataFrame(debug_rows)


def _sportsbook_consensus_at_line(group: pd.DataFrame, line: float) -> dict[str, Any]:
    """Return sportsbook consensus prices at the exact PrizePicks line, if available."""
    if group is None or group.empty or not np.isfinite(line):
        return {}
    sports = group[group.apply(_is_sportsbook_quote, axis=1)].copy()
    if sports.empty:
        return {}
    numeric = pd.to_numeric(sports.get("Line"), errors="coerce")
    near = sports[np.isclose(numeric, float(line), atol=0.01)].copy()
    if near.empty:
        return {}
    over_prices = pd.to_numeric(near.loc[near["Side"].astype(str).str.lower().eq("over"), "Odds"], errors="coerce").dropna()
    under_prices = pd.to_numeric(near.loc[near["Side"].astype(str).str.lower().eq("under"), "Odds"], errors="coerce").dropna()
    over_odds = float(over_prices.median()) if not over_prices.empty else np.nan
    under_odds = float(under_prices.median()) if not under_prices.empty else np.nan
    books = sorted(near.get("Bookmaker", pd.Series(dtype=str)).dropna().astype(str).unique())
    return {
        "OverOdds": over_odds,
        "UnderOdds": under_odds,
        "MarketOverProb": no_vig_over_probability(over_odds, under_odds),
        "Books": books,
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
            quote = choose_market_quote(group, source_mode)
            # PrizePicks supplies the displayed line, while sportsbook quotes at that
            # exact same line supply fair market probability when available.
            if str(source_mode or "").strip().lower() == "prizepicks" and quote:
                market = _sportsbook_consensus_at_line(group, float(quote.get("Line", np.nan)))
                if market:
                    quote["OverOdds"] = market.get("OverOdds", np.nan)
                    quote["UnderOdds"] = market.get("UnderOdds", np.nan)
                    quote["MarketOverProb"] = market.get("MarketOverProb", np.nan)
                    books = market.get("Books", [])
                    quote["Source"] = f"PrizePicks · consensus {len(books)} book{'s' if len(books) != 1 else ''}"
                else:
                    quote["Source"] = "PrizePicks regular"
            quotes[(str(event_id), str(name_key))] = quote
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


def build_snapshot(board: pd.DataFrame, slate_date: date, snapshot_type: str = "pregame_live") -> pd.DataFrame:
    snapshot = board.copy()
    snapshot["SlateDate"] = slate_date
    snapshot["GeneratedAtUTC"] = pd.Timestamp.now(tz="UTC").isoformat()
    snapshot["ModelVersion"] = MODEL_VERSION
    snapshot["SnapshotType"] = snapshot_type
    for target, config in TARGETS.items():
        snapshot[config["actual"]] = np.nan
    snapshot["ResultStatus"] = ""
    preferred = [
        "SlateDate", "GameTimeUTC", "GeneratedAtUTC", "ModelVersion", "SnapshotType", "EventID", "PlayerID", "Player",
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
    if "SnapshotType" not in result.columns:
        result["SnapshotType"] = ""
    result["SnapshotType"] = result["SnapshotType"].fillna("").astype(str)
    snapshot_type_key = result["SnapshotType"].str.lower().str.replace(r"[^a-z0-9]+", "_", regex=True).str.strip("_")
    result["SnapshotRetroClean"] = snapshot_type_key.isin({"retro_clean", "retroclean", "backfill", "backfilled", "historical_backfill"})
    result["CleanForAnalysis"] = (~result["SnapshotAfterStart"].fillna(False)) | result["SnapshotRetroClean"].fillna(False)
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



def normalize_completed_results_upload(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Normalize a user-supplied completed-results CSV into player-game logs.

    Accepts friendly headers such as Player/Name, Date/GameDate, Points/PTS,
    Rebounds/REB, Assists/AST, Team, Minutes/MIN and 3PM/FG3M. PlayerID is
    optional; a stable provider-agnostic import ID is synthesized from the
    normalized player name when it is absent.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(), ["The uploaded results file is empty."]
    work = frame.copy()
    original = {str(c): c for c in work.columns}
    key_to_col = {re.sub(r"[^a-z0-9]+", "", str(c).lower()): c for c in work.columns}

    aliases = {
        "Player": ["player", "playername", "name", "athlete", "athletename"],
        "PlayerID": ["playerid", "athleteid", "id"],
        "GameDate": ["gamedate", "date", "slatedate", "eventdate"],
        "Team": ["team", "teamabbr", "teamabbreviation"],
        "GameID": ["gameid", "eventid"],
        "MIN": ["min", "minutes", "mins"],
        "PTS": ["pts", "points", "point"],
        "REB": ["reb", "rebs", "rebounds", "rebound"],
        "AST": ["ast", "asts", "assists", "assist"],
        "FG3M": ["fg3m", "3pm", "threes", "threepointersmade", "3ptmade"],
        "STL": ["stl", "steals"],
        "BLK": ["blk", "blocks"],
        "TOV": ["tov", "turnovers"],
    }
    rename = {}
    for canonical, candidates in aliases.items():
        for candidate in candidates:
            if candidate in key_to_col:
                rename[key_to_col[candidate]] = canonical
                break
    work = work.rename(columns=rename)

    errors: list[str] = []
    for required in ["Player", "GameDate"]:
        if required not in work.columns:
            errors.append(f"Missing required column: {required}")
    stat_cols = [c for c in ["PTS", "REB", "AST"] if c in work.columns]
    if not stat_cols:
        errors.append("Include at least one result column: PTS/Points, REB/Rebounds, or AST/Assists.")
    if errors:
        return pd.DataFrame(), errors

    work["Player"] = work["Player"].astype(str).str.strip()
    work["GameDate"] = pd.to_datetime(work["GameDate"], errors="coerce").dt.date
    if "Team" not in work.columns:
        work["Team"] = ""
    work["Team"] = work["Team"].map(normalize_team)
    work["NameKey"] = work["Player"].map(normalize_player_name)

    if "PlayerID" not in work.columns:
        work["PlayerID"] = ""
    work["PlayerID"] = work["PlayerID"].fillna("").astype(str).str.strip()
    missing_pid = work["PlayerID"].eq("")
    work.loc[missing_pid, "PlayerID"] = "IMPORT:" + work.loc[missing_pid, "NameKey"].str.replace(" ", "_", regex=False)

    if "GameID" not in work.columns:
        work["GameID"] = ""
    work["GameID"] = work["GameID"].fillna("").astype(str)
    missing_gid = work["GameID"].eq("")
    work.loc[missing_gid, "GameID"] = (
        "IMPORT:" + work.loc[missing_gid, "GameDate"].astype(str) + ":" + work.loc[missing_gid, "Team"].astype(str)
    )
    if "MIN" not in work.columns:
        work["MIN"] = np.nan
    for col in ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "MIN"]:
        if col not in work.columns:
            work[col] = np.nan
        if col != "MIN":
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work["Source"] = "Manual completed-results import"
    work = work[work["Player"].ne("") & work["GameDate"].notna()].copy()
    if work.empty:
        return pd.DataFrame(), ["No valid player/date rows remained after parsing the file."]
    normalized = normalize_player_logs(work)
    if normalized.empty:
        return pd.DataFrame(), ["The file parsed, but no usable player-game rows remained after normalization."]
    return normalized, []


def load_manual_results_db() -> pd.DataFrame:
    """Cloud-safe session copy of manually imported completed results."""
    frame = st.session_state.get("wnba_manual_results_store", pd.DataFrame())
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    return normalize_player_logs(frame)


def save_manual_results_db(frame: pd.DataFrame) -> pd.DataFrame:
    """Merge completed-result imports into this Streamlit session only."""
    incoming = normalize_player_logs(frame)
    if incoming.empty:
        return load_manual_results_db()
    existing = load_manual_results_db()
    combined = pd.concat([existing, incoming], ignore_index=True) if not existing.empty else incoming.copy()
    combined["NameKey"] = combined["Player"].map(normalize_player_name)
    combined["TeamKey"] = combined["Team"].map(normalize_team)
    combined = combined.sort_values("GameDate").drop_duplicates(
        ["GameDate", "NameKey", "TeamKey"], keep="last"
    )
    st.session_state["wnba_manual_results_store"] = combined.copy()
    return normalize_player_logs(combined)


def append_snapshot(history: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    return merge_history(history, snapshot)


# -----------------------------------------------------------------------------
# Streamlit Cloud session storage
# -----------------------------------------------------------------------------
# These function names are retained so the rest of the app can use the same
# calibration logic. Nothing here assumes a Windows path or durable local disk.
# Downloaded snapshot/history/calibration files are the durable recovery path.

def init_local_db() -> None:
    return None


def _history_for_sql(history: pd.DataFrame) -> pd.DataFrame:
    return normalize_history(history)


def load_history_db() -> pd.DataFrame:
    frame = st.session_state.get("wnba_history_store", pd.DataFrame())
    if not isinstance(frame, pd.DataFrame):
        return pd.DataFrame()
    return normalize_history(frame)


def save_history_db(history: pd.DataFrame) -> None:
    st.session_state["wnba_history_store"] = normalize_history(history).copy()


def save_snapshot_db(snapshot: pd.DataFrame) -> pd.DataFrame:
    history = load_history_db()
    updated = append_snapshot(history, snapshot)
    save_history_db(updated)
    return updated


def load_active_calibration_db() -> dict[str, Any]:
    return parse_calibration_payload(st.session_state.get("wnba_active_calibration_store", {}))


def save_active_calibration_db(bundle: dict[str, Any]) -> None:
    save_calibration_run_db(bundle, active=True)


def calibration_history_db() -> pd.DataFrame:
    records = st.session_state.get("wnba_calibration_runs_store", [])
    return pd.DataFrame(records) if records else pd.DataFrame()


def get_app_meta(key: str, default: str = "") -> str:
    meta = st.session_state.get("wnba_app_meta_store", {})
    return str(meta.get(key, default))


def set_app_meta(key: str, value: object) -> None:
    meta = dict(st.session_state.get("wnba_app_meta_store", {}))
    meta[key] = str(value)
    st.session_state["wnba_app_meta_store"] = meta

def merge_history(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """Merge history without making repeated imports/saves multiply identical snapshots."""
    left = normalize_history(existing)
    right = normalize_history(incoming)
    if left.empty:
        combined = right.copy()
    elif right.empty:
        combined = left.copy()
    else:
        combined = pd.concat([left, right], ignore_index=True)
    if combined.empty:
        return combined
    # GeneratedAtUTC distinguishes genuinely different pregame snapshots. Exact re-imports are removed.
    keys = [c for c in ["SlateDate", "EventID", "PlayerID", "GeneratedAtUTC", "ModelVersion", "SnapshotType"] if c in combined.columns]
    if keys:
        combined = combined.drop_duplicates(keys, keep="last")
    else:
        combined = combined.drop_duplicates(keep="last")
    return normalize_history(combined)


def save_calibration_run_db(bundle: dict[str, Any], active: bool = False) -> int | None:
    normalized = parse_calibration_payload(bundle)
    if not normalized:
        return None
    payload = dict(bundle) if isinstance(bundle, dict) else {}
    payload["version"] = normalized.get("version", MODEL_VERSION)
    payload["targets"] = normalized.get("targets", {})
    runs = list(st.session_state.get("wnba_calibration_runs_store", []))
    run_id = (max([int(r.get("ID", 0)) for r in runs], default=0) + 1)
    if active:
        for r in runs:
            r["Active"] = False
        st.session_state["wnba_active_calibration_store"] = payload
    runs.insert(0, {
        "ID": run_id,
        "CreatedUTC": pd.Timestamp.now(tz="UTC").isoformat(),
        "ModelVersion": MODEL_VERSION,
        "Active": bool(active),
        "Targets": ", ".join(payload.get("targets", {}).keys()),
        "Auto": bool(payload.get("auto", False)),
        "CompletedRows": payload.get("completed_rows", np.nan),
    })
    st.session_state["wnba_calibration_runs_store"] = runs
    return run_id


def _calibration_frame(history: pd.DataFrame, target: str) -> tuple[pd.DataFrame, str]:
    config = TARGETS[target]
    projection_col = config["raw_projection"] if config["raw_projection"] in history.columns else config["projection"]
    frame = history[[projection_col, config["actual"], "SlateDate"]].copy()
    frame[projection_col] = pd.to_numeric(frame[projection_col], errors="coerce")
    frame[config["actual"]] = pd.to_numeric(frame[config["actual"]], errors="coerce")
    frame["SlateDate"] = pd.to_datetime(frame["SlateDate"], errors="coerce")
    frame = frame.dropna().sort_values("SlateDate")
    return frame, projection_col


def evaluate_calibration_on_holdout(history: pd.DataFrame, target: str, calibration: dict[str, Any] | None) -> float:
    payload = parse_calibration_payload(calibration or {})
    coeff = payload.get("targets", {}).get(target, {})
    if not coeff:
        return np.nan
    frame, projection_col = _calibration_frame(history, target)
    split = max(int(len(frame) * 0.70), 1)
    test = frame.iloc[split:]
    if len(test) < 20:
        return np.nan
    x = test[projection_col].to_numpy(float)
    y = test[TARGETS[target]["actual"]].to_numpy(float)
    pred = float(coeff.get("intercept", 0.0)) + float(coeff.get("slope", 1.0)) * x
    return float(np.mean(np.abs(pred - y)))


def auto_refit_calibration_db(history: pd.DataFrame, min_rows: int = 75, min_improvement: float = 0.01, force: bool = False) -> dict[str, Any]:
    analysis = latest_pregame_history(history, include_retro_clean=True)
    actual_cols = [config["actual"] for config in TARGETS.values()]
    completed = analysis[analysis[actual_cols].notna().any(axis=1)].copy() if not analysis.empty else pd.DataFrame()
    completed_n = int(len(completed))
    previous_n = int(float(get_app_meta("last_auto_calibration_completed_rows", "0") or 0))
    if not force and completed_n <= previous_n:
        return {"ran": False, "completed": completed_n, "reason": "No new completed calibration rows."}

    current = load_active_calibration_db()
    promoted_targets: dict[str, Any] = {}
    fit_rows: list[dict[str, Any]] = []
    for target in TARGETS:
        fit = fit_linear_calibration(completed, target, int(min_rows))
        row = {"Target": target, **fit}
        if fit.get("ok"):
            candidate_mae = float(fit.get("holdout_calibrated_mae", np.nan))
            raw_mae = float(fit.get("holdout_raw_mae", np.nan))
            active_mae = evaluate_calibration_on_holdout(completed, target, current)
            row["active_holdout_mae"] = active_mae
            raw_pass = np.isfinite(candidate_mae) and np.isfinite(raw_mae) and candidate_mae <= (raw_mae - float(min_improvement))
            active_pass = (not np.isfinite(active_mae)) or candidate_mae <= (active_mae - float(min_improvement))
            promote = bool(raw_pass and active_pass)
            row["promote"] = promote
            if promote:
                promoted_targets[target] = {
                    "slope": fit["slope"], "intercept": fit["intercept"], "n": fit["n"],
                    "holdout_raw_mae": raw_mae, "holdout_calibrated_mae": candidate_mae,
                    "previous_active_holdout_mae": active_mae if np.isfinite(active_mae) else None,
                }
        fit_rows.append(row)

    bundle = {
        "version": MODEL_VERSION,
        "created_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "targets": promoted_targets,
        "auto": True,
        "completed_rows": completed_n,
        "min_rows": int(min_rows),
        "min_holdout_improvement": float(min_improvement),
        "fit_summary": fit_rows,
    }
    # Keep currently active targets that were not replaced, so a partial promotion does not erase them.
    if promoted_targets and current.get("targets"):
        merged = dict(current.get("targets", {}))
        merged.update(promoted_targets)
        bundle["targets"] = merged

    run_id = save_calibration_run_db(bundle, active=bool(promoted_targets))
    set_app_meta("last_auto_calibration_completed_rows", completed_n)
    set_app_meta("last_auto_calibration_run_id", run_id or "")
    set_app_meta("last_auto_calibration_promoted_targets", ",".join(promoted_targets.keys()))
    return {
        "ran": True, "completed": completed_n, "promoted": list(promoted_targets.keys()),
        "run_id": run_id, "bundle": bundle, "fit_table": pd.DataFrame(fit_rows),
    }


def grade_and_calibrate_db(player_logs: pd.DataFrame, force_refit: bool = False) -> dict[str, Any]:
    history = load_history_db()
    if history.empty:
        return {"history": history, "matched": 0, "unmatched": 0, "calibration": {"ran": False, "reason": "No saved snapshots yet."}}
    before_actual = int(history[[c["actual"] for c in TARGETS.values() if c["actual"] in history.columns]].notna().any(axis=1).sum())
    graded, summary = fill_actual_results(history, player_logs)
    after_actual = int(graded[[c["actual"] for c in TARGETS.values() if c["actual"] in graded.columns]].notna().any(axis=1).sum())
    if after_actual != before_actual:
        save_history_db(graded)
    calibration_result = auto_refit_calibration_db(graded, force=force_refit)
    return {"history": graded, **summary, "newly_graded": max(after_actual - before_actual, 0), "calibration": calibration_result}


def calibration_grading_diagnostics(history: pd.DataFrame, player_logs: pd.DataFrame) -> dict[str, Any]:
    hist = normalize_history(history)
    logs = normalize_player_logs(player_logs) if player_logs is not None and not player_logs.empty else pd.DataFrame()
    out: dict[str, Any] = {
        "history_rows": int(len(hist)), "completed_rows": 0, "pending_rows": int(len(hist)),
        "latest_snapshot_date": None, "latest_graded_date": None, "latest_log_date": None, "by_date": pd.DataFrame(),
    }
    if not logs.empty and "GameDate" in logs.columns:
        log_dates = pd.to_datetime(logs["GameDate"], errors="coerce").dt.date.dropna()
        if not log_dates.empty:
            out["latest_log_date"] = max(log_dates)
    if hist.empty:
        return out
    actual_columns = [cfg["actual"] for cfg in TARGETS.values() if cfg["actual"] in hist.columns]
    graded_mask = hist[actual_columns].notna().any(axis=1) if actual_columns else pd.Series(False, index=hist.index)
    out["completed_rows"] = int(graded_mask.sum())
    out["pending_rows"] = int((~graded_mask).sum())
    dates = pd.to_datetime(hist.get("SlateDate"), errors="coerce").dt.date
    if dates.notna().any():
        out["latest_snapshot_date"] = dates.dropna().max()
    if graded_mask.any() and dates.loc[graded_mask].notna().any():
        out["latest_graded_date"] = dates.loc[graded_mask].dropna().max()
    temp = pd.DataFrame({"SlateDate": dates, "Graded": graded_mask}).dropna(subset=["SlateDate"])
    if not temp.empty:
        by_date = temp.groupby("SlateDate", as_index=False).agg(SnapshotRows=("Graded", "size"), GradedRows=("Graded", "sum"))
        by_date["PendingRows"] = by_date["SnapshotRows"] - by_date["GradedRows"]
        if not logs.empty and "GameDate" in logs.columns:
            log_count = pd.DataFrame({"SlateDate": pd.to_datetime(logs["GameDate"], errors="coerce").dt.date}).dropna()
            log_count = log_count.groupby("SlateDate").size().rename("PlayerLogRows").reset_index()
            by_date = by_date.merge(log_count, on="SlateDate", how="left")
            by_date["PlayerLogRows"] = by_date["PlayerLogRows"].fillna(0).astype(int)
        else:
            by_date["PlayerLogRows"] = 0
        out["by_date"] = by_date.sort_values("SlateDate", ascending=False).reset_index(drop=True)
    return out


def fill_actual_results(history: pd.DataFrame, player_logs: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Fill actuals across providers and repair stale SlateDate rows safely."""
    result = normalize_history(history)
    if result.empty or player_logs is None or player_logs.empty:
        return result, {"matched": 0, "unmatched": len(result), "matched_by_id": 0, "matched_by_name": 0, "matched_by_game_time": 0}

    lookup = normalize_player_logs(player_logs).copy()
    if lookup.empty:
        return result, {"matched": 0, "unmatched": len(result), "matched_by_id": 0, "matched_by_name": 0, "matched_by_game_time": 0}
    lookup["SlateDate"] = pd.to_datetime(lookup["GameDate"], errors="coerce").dt.date
    lookup["NameKey"] = lookup["Player"].map(normalize_player_name)
    lookup["TeamKey"] = lookup["Team"].map(normalize_team)
    lookup["_complete"] = lookup[["PTS", "REB", "AST"]].notna().sum(axis=1)
    lookup = lookup.sort_values(["_complete", "MIN"], ascending=[False, False])

    id_map = {}
    name_team_map = {}
    name_date_candidates = {}
    for rec in lookup.to_dict("records"):
        d = rec.get("SlateDate")
        if d is None or pd.isna(d):
            continue
        pid = pd.to_numeric(pd.Series([rec.get("PlayerID")]), errors="coerce").iloc[0]
        if pd.notna(pid):
            id_map.setdefault((int(pid), d), rec)
        nk = str(rec.get("NameKey") or "")
        tk = str(rec.get("TeamKey") or "")
        if nk:
            name_team_map.setdefault((nk, d, tk), rec)
            name_date_candidates.setdefault((nk, d), []).append(rec)

    def find_name(nk: str, tk: str, d: date | None):
        if not nk or d is None:
            return None
        rec = name_team_map.get((nk, d, tk))
        if rec is not None:
            return rec
        candidates = name_date_candidates.get((nk, d), [])
        unique_games = {(str(c.get("GameID")), str(c.get("TeamKey"))) for c in candidates}
        if len(unique_games) == 1 and candidates:
            return candidates[0]
        return None

    matched = matched_by_id = matched_by_name = matched_by_game_time = 0
    actual_cols = [c["actual"] for c in TARGETS.values() if c["actual"] in result.columns]
    for idx, row in result.iterrows():
        if actual_cols and any(pd.notna(row.get(c)) for c in actual_cols):
            continue
        slate = row.get("SlateDate")
        effective = _snapshot_effective_game_date(row)
        rec = None
        pid = pd.to_numeric(pd.Series([row.get("PlayerID")]), errors="coerce").iloc[0]
        if pd.notna(pid):
            rec = id_map.get((int(pid), slate))
            if rec is not None:
                matched_by_id += 1
        nk = normalize_player_name(row.get("Player", ""))
        tk = normalize_team(row.get("Team", ""))
        if rec is None:
            rec = find_name(nk, tk, slate)
            if rec is not None:
                matched_by_name += 1
        if rec is None and effective is not None and effective != slate:
            rec = find_name(nk, tk, effective)
            if rec is not None:
                matched_by_name += 1
                matched_by_game_time += 1
        if rec is None:
            continue

        pts = pd.to_numeric(pd.Series([rec.get("PTS")]), errors="coerce").iloc[0]
        reb = pd.to_numeric(pd.Series([rec.get("REB")]), errors="coerce").iloc[0]
        ast = pd.to_numeric(pd.Series([rec.get("AST")]), errors="coerce").iloc[0]
        result.at[idx, TARGETS["Points"]["actual"]] = pts
        result.at[idx, TARGETS["Rebounds"]["actual"]] = reb
        result.at[idx, TARGETS["Assists"]["actual"]] = ast
        if pd.notna(pts) and pd.notna(reb) and pd.notna(ast):
            result.at[idx, TARGETS["PRA"]["actual"]] = float(pts + reb + ast)
        result.at[idx, "ResultStatus"] = "graded"
        matched += 1

    completed_now = int(result[actual_cols].notna().any(axis=1).sum()) if actual_cols else 0
    return result, {
        "matched": matched, "unmatched": max(len(result) - completed_now, 0),
        "matched_by_id": matched_by_id, "matched_by_name": matched_by_name,
        "matched_by_game_time": matched_by_game_time,
    }


def latest_pregame_history(history: pd.DataFrame, include_retro_clean: bool = True) -> pd.DataFrame:
    frame = normalize_history(history)
    if frame.empty:
        return frame
    if include_retro_clean:
        clean_mask = frame["CleanForAnalysis"].fillna(False)
    else:
        clean_mask = ~frame["SnapshotAfterStart"].fillna(False)
    frame = frame[clean_mask].copy()
    frame = frame.sort_values(["SlateDate", "GeneratedAtUTC"], na_position="last")
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
st.caption("Minutes-driven projections, target-specific scores, prop-line comparison and built-in backtesting.")

with st.sidebar:
    st.header("Slate & data")
    current_year = date.today().year
    season = st.selectbox("Season", [str(year) for year in range(current_year, 2019, -1)], index=0)
    season_type = st.selectbox("Season type", ["Regular Season", "Playoffs"])
    slate_date = st.date_input("Slate date", value=date.today())
    st.caption(f"Build: {COOCHIE_BOARD_BUILD}")
    st.caption("Stats order: temporary cloud cache → SportsDataverse → official WNBA/ESPN fallbacks. Slate order: PrizePicks live → The Odds API → WNBA/ESPN fallbacks.")

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
    db_calibration = load_active_calibration_db()
    active_calibration = uploaded_calibration or session_calibration or db_calibration
    if db_calibration and not uploaded_calibration and not session_calibration:
        st.caption("Active calibration loaded from the current Streamlit session.")

    st.divider()
    st.subheader("Live grading results")
    balldontlie_api_key = ""  # retained internally for backward compatibility; no key is required in v17.
    st.caption("For durable calibration on Streamlit Cloud, download pregame snapshots/master history and upload them again when grading. Cloud-local storage is temporary.")

    st.divider()
    st.subheader("Automatic prop lines")
    env_odds_key = str(os.getenv("THE_ODDS_API_KEY", "")).strip()
    the_odds_api_key = st.text_input(
        "The Odds API key",
        value=env_odds_key,
        type="password",
        help="You can paste the key here or set THE_ODDS_API_KEY in Streamlit Secrets/environment variables.",
    )
    default_line_source = st.session_state.get("wnba_line_source_mode", "PrizePicks")
    if str(default_line_source).strip().lower() == "consensus":
        default_line_source = "Consensus sportsbooks"
    if default_line_source not in LINE_SOURCE_OPTIONS:
        default_line_source = "PrizePicks" if "PrizePicks" in LINE_SOURCE_OPTIONS else LINE_SOURCE_OPTIONS[0]
    odds_source_mode = st.selectbox(
        "Line source",
        LINE_SOURCE_OPTIONS,
        index=LINE_SOURCE_OPTIONS.index(default_line_source),
        key="wnba_line_source_mode",
        help="Choose PrizePicks, a specific sportsbook, sportsbook consensus, or the best available over/under line after fetching The Odds API lines.",
    )
    if st.button("Clear cached data", width="stretch"):
        st.cache_data.clear()
        for key in ["wnba_board", "wnba_games", "wnba_odds", "wnba_player_logs_data", "wnba_team_logs_data", "wnba_data_source", "wnba_the_odds_events", "wnba_the_odds_event_debug", "wnba_the_odds_fetch_debug"]:
            st.session_state.pop(key, None)
        st.rerun()

# Load logs
load_col, schedule_col = st.columns([1, 1])
with load_col:
    load_data = st.button("Load / refresh WNBA stats", type="primary", width="stretch")
with schedule_col:
    load_schedule = st.button("Load / refresh slate", width="stretch")

if load_data or "wnba_player_logs_data" not in st.session_state:
    if load_data:
        # A manual refresh should really retry providers instead of reusing Streamlit's function cache.
        for cached_func in (load_official_game_logs, fetch_wnba_stats, load_espn_game_logs):
            try:
                cached_func.clear()
            except Exception:
                pass
    try:
        if player_csv is not None and team_csv is not None:
            player_logs = read_uploaded_csv(player_csv, normalize_player_logs)
            team_logs = read_uploaded_csv(team_csv, normalize_team_logs)
        else:
            with st.spinner("Refreshing WNBA game logs from providers…"):
                player_logs, team_logs, data_source = load_official_game_logs(season, season_type, force_refresh=bool(load_data))
        if player_csv is not None and team_csv is not None:
            data_source = "Uploaded CSV files"
            save_game_logs_snapshot(season, season_type, player_logs, team_logs, data_source)
        st.session_state["wnba_player_logs_data"] = player_logs
        st.session_state["wnba_team_logs_data"] = team_logs
        st.session_state["wnba_data_source"] = data_source
        # Integrated calibration: whenever fresh completed-game logs arrive, grade saved snapshots and refit if new outcomes exist.
        auto_sync = grade_and_calibrate_db(player_logs)
        st.session_state["wnba_auto_calibration_sync"] = auto_sync
        if not auto_sync.get("history", pd.DataFrame()).empty:
            st.session_state["wnba_history"] = auto_sync["history"]
        if auto_sync.get("calibration", {}).get("promoted"):
            st.session_state["wnba_projection_calibration"] = load_active_calibration_db()
    except Exception as exc:
        st.error(f"Could not load WNBA stats: {type(exc).__name__}: {exc}")
        st.info("Upload exported player and team game-log CSV files in the sidebar as a fallback.")

player_logs = st.session_state.get("wnba_player_logs_data", pd.DataFrame())
team_logs = st.session_state.get("wnba_team_logs_data", pd.DataFrame())
data_source = st.session_state.get("wnba_data_source", "")
if data_source and not player_logs.empty:
    st.success(f"WNBA data loaded from: {data_source} · {len(player_logs):,} player-game rows")

# The slate date is authoritative.  Never keep a schedule from a previous date
# in session state after the date picker changes.
stored_schedule_date = st.session_state.get("wnba_schedule_date")
schedule_date_changed = str(stored_schedule_date) != str(slate_date)
if load_schedule or "wnba_games" not in st.session_state or schedule_date_changed:
    # A refresh must be visible and deterministic. Clear only the underlying live
    # feed cache; fetch_official_schedule itself is deliberately uncached.
    if load_schedule:
        try:
            fetch_prizepicks_live_rows.clear()
        except Exception:
            pass
        st.session_state.pop("wnba_games", None)
        st.session_state.pop("wnba_schedule_errors", None)

    with st.spinner(f"Refreshing WNBA slate for {slate_date.isoformat()}…"):
        try:
            games, schedule_errors = fetch_official_schedule(slate_date, the_odds_api_key)
        except Exception as exc:
            games = pd.DataFrame()
            schedule_errors = [f"Schedule refresh crashed: {type(exc).__name__}: {exc}"]

    st.session_state["wnba_games"] = games
    st.session_state["wnba_schedule_errors"] = schedule_errors
    st.session_state["wnba_schedule_date"] = slate_date
    st.session_state["wnba_schedule_last_refresh"] = datetime.now(timezone.utc).isoformat()

    if load_schedule:
        if not games.empty:
            source = str(games.get("Source", pd.Series(["live source"])).iloc[0])
            st.success(f"Slate refreshed: {len(games)} game(s) for {slate_date.isoformat()} from {source}.")
        else:
            st.warning(
                f"Refresh completed, but no fully oriented games were found for {slate_date.isoformat()}. "
                "If PrizePicks has lines but no home/away orientation, enter The Odds API key or use the manual-games box below."
            )

games = st.session_state.get("wnba_games", pd.DataFrame())

with st.expander("Slate games and manual fallback", expanded=games.empty):
    if not games.empty:
        st.dataframe(games[[c for c in ["Game", "GameTimeUTC", "Source"] if c in games.columns]], hide_index=True, width="stretch")
    last_refresh = st.session_state.get("wnba_schedule_last_refresh")
    if last_refresh:
        st.caption(f"Last slate refresh: {last_refresh}")
    errors = st.session_state.get("wnba_schedule_errors", [])
    if errors:
        st.caption("Schedule connection details: " + " | ".join(errors[:4]))
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

# Track the exact slate used to construct the model board. Streamlit session state can
# otherwise retain yesterday's board while the date selector / PrizePicks feed has
# already moved to a new slate, which makes every otherwise-valid PP player match 0.
def _selected_games_signature(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return ""
    key_col = "EventID" if "EventID" in frame.columns else "Game"
    return "|".join(sorted(frame[key_col].fillna("").astype(str).tolist()))

current_game_signature = _selected_games_signature(selected_game_frame)

build_board_button = st.button("Build WNBA player board", type="primary", width="stretch")
if build_board_button:
    if player_logs.empty or selected_game_frame.empty:
        st.warning("Load stats and select at least one game first.")
    else:
        with st.spinner("Building minutes, rate and matchup projections…"):
            raw_board = build_base_board(player_logs, team_logs, selected_game_frame)
            latest_calibration = uploaded_calibration or parse_calibration_payload(st.session_state.get("wnba_projection_calibration", {})) or load_active_calibration_db()
            calibrated = apply_projection_calibration(raw_board, latest_calibration)
            st.session_state["wnba_board"] = calibrated
            st.session_state["wnba_base_board"] = raw_board
            st.session_state["wnba_board_date"] = slate_date
            st.session_state["wnba_board_game_signature"] = current_game_signature
            # Automatic pregame persistence. Exact duplicate imports/saves are de-duplicated by merge_history().
            auto_snapshot = build_snapshot(calibrated, slate_date, snapshot_type="pregame_live")
            history_after_save = save_snapshot_db(auto_snapshot)
            st.session_state["wnba_history"] = history_after_save
            st.session_state["wnba_last_auto_snapshot_utc"] = pd.Timestamp.now(tz="UTC").isoformat()

board = st.session_state.get("wnba_board", pd.DataFrame())
stored_board_date = st.session_state.get("wnba_board_date")
stored_game_signature = st.session_state.get("wnba_board_game_signature", "")
board_is_stale = (
    not board.empty
    and (str(stored_board_date) != str(slate_date) or str(stored_game_signature) != str(current_game_signature))
)

# Automatically rebuild when the selected slate changes. This prevents the direct
# PrizePicks feed for today's date from being matched against a board cached from a
# prior date/game selection.
if board_is_stale and not player_logs.empty and not selected_game_frame.empty:
    with st.spinner("Slate changed — rebuilding the WNBA player board before matching PrizePicks…"):
        raw_board = build_base_board(player_logs, team_logs, selected_game_frame)
        latest_calibration = uploaded_calibration or parse_calibration_payload(st.session_state.get("wnba_projection_calibration", {})) or load_active_calibration_db()
        board = apply_projection_calibration(raw_board, latest_calibration)
        st.session_state["wnba_board"] = board
        st.session_state["wnba_base_board"] = raw_board
        st.session_state["wnba_board_date"] = slate_date
        st.session_state["wnba_board_game_signature"] = current_game_signature
        if not board.empty:
            auto_snapshot = build_snapshot(board, slate_date, snapshot_type="pregame_live")
            st.session_state["wnba_history"] = save_snapshot_db(auto_snapshot)
            st.session_state["wnba_last_auto_snapshot_utc"] = pd.Timestamp.now(tz="UTC").isoformat()

if board.empty:
    st.info("Load the slate and press **Build WNBA player board**.")
    st.stop()

# Fetch/import props after board exists
# Direct PrizePicks integration: when PrizePicks is the selected line source, refresh
# the public WNBA projection feed automatically (cached briefly to avoid repeated calls).
if str(odds_source_mode).strip().lower() == "prizepicks":
    try:
        live_pp_rows, live_pp_meta = fetch_prizepicks_live_rows(slate_date)
        live_pp_odds, live_pp_debug = prizepicks_rows_to_odds_frame(live_pp_rows, board)
        st.session_state["wnba_prizepicks_rows"] = live_pp_rows
        st.session_state["wnba_prizepicks_debug"] = live_pp_debug
        st.session_state["wnba_prizepicks_meta"] = live_pp_meta
        existing_odds = st.session_state.get("wnba_odds", pd.DataFrame())
        if isinstance(existing_odds, pd.DataFrame) and not existing_odds.empty and "BookmakerKey" in existing_odds.columns:
            existing_odds = existing_odds[~existing_odds["BookmakerKey"].astype(str).str.lower().eq("prizepicks")].copy()
        else:
            existing_odds = pd.DataFrame()
        st.session_state["wnba_odds"] = combine_odds_frames([existing_odds, live_pp_odds])
        if not live_pp_rows.empty and live_pp_odds.empty:
            pp_dates = sorted({str(x) for x in live_pp_rows.get("SlateDate", pd.Series(dtype=str)).dropna().astype(str) if str(x).strip()})
            date_note = f" PrizePicks dates seen: {', '.join(pp_dates[:6])}." if pp_dates else " PrizePicks did not expose a usable game date on these rows."
            st.session_state["wnba_prizepicks_live_warning"] = f"PrizePicks returned WNBA lines, but none matched the current model board for {slate_date}.{date_note} Open the match debug table below to see the closest model names."
        else:
            st.session_state.pop("wnba_prizepicks_live_warning", None)
    except Exception as exc:
        st.session_state["wnba_prizepicks_live_warning"] = f"Direct PrizePicks fetch failed: {type(exc).__name__}: {exc}"

with st.expander("PrizePicks — direct live lines + backup import", expanded=False):
    st.caption("PrizePicks is now a direct line source. When selected above, the board automatically fetches the current WNBA projection feed and matches supported Points, Rebounds, Assists, and PRA lines to modeled players.")
    pp_meta_live = st.session_state.get("wnba_prizepicks_meta", {})
    pp_warning_live = st.session_state.get("wnba_prizepicks_live_warning", "")
    if pp_warning_live:
        st.warning(pp_warning_live)
    if isinstance(pp_meta_live, dict) and pp_meta_live:
        st.write({k: v for k, v in pp_meta_live.items() if v not in [None, ""]})
    c_live1, c_live2 = st.columns(2)
    with c_live1:
        if st.button("Refresh PrizePicks live lines", width="stretch"):
            fetch_prizepicks_live_rows.clear()
            st.rerun()
    with c_live2:
        current_pp_debug = st.session_state.get("wnba_prizepicks_debug", pd.DataFrame())
        if isinstance(current_pp_debug, pd.DataFrame) and not current_pp_debug.empty and "Matched" in current_pp_debug.columns:
            matched_pp_count = int(pd.to_numeric(current_pp_debug["Matched"], errors="coerce").fillna(0).astype(bool).sum())
        else:
            matched_pp_count = 0
        st.metric("Matched PrizePicks player-markets", matched_pp_count)
    st.markdown("#### Backup screenshot / CSV import")
    st.markdown(
        """
        Use this when APIs are stale or missing PrizePicks. No browser console or auto-scroll scripts are needed.
        Upload PrizePicks screenshots, CSV files, or pasted-text TXT files and the dashboard will OCR/parse the lines,
        then match them to the current WNBA board.
        """
    )
    st.info(
        "Screenshot workflow: filter PrizePicks to WNBA, open one market at a time (Points, Rebounds, Assists, or PRA), "
        "take screenshots while you manually scroll, then upload all screenshots here. Avoid developer tools to prevent account restrictions."
    )
    with st.expander("Screenshot tips", expanded=False):
        st.markdown(
            """
            - Capture the player name, stat type, and line in the same screenshot.
            - Use one market filter at a time when possible.
            - Slight overlap between screenshots is fine; duplicates are removed.
            - Goblin/Demon text is detected when visible.
            - If OCR misses a few rows, upload a small CSV/TXT correction for just those rows.
            """
        )
    pp_uploads = st.file_uploader(
        "Upload PrizePicks screenshots / CSV / TXT / JSON",
        type=["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "csv", "txt", "json"],
        key="wnba_prizepicks_uploads",
        accept_multiple_files=True,
        help="Supports screenshots plus CSV/TXT/JSON. CSV/TXT can use player, market/stat, line, and promo_type columns/fields.",
    )
    col_pp_1, col_pp_2 = st.columns([1, 1])
    with col_pp_1:
        import_pp = st.button("Import PrizePicks lines", width="stretch")
    with col_pp_2:
        clear_pp = st.button("Clear imported PrizePicks lines", width="stretch")
    if clear_pp:
        st.session_state.pop("wnba_prizepicks_rows", None)
        st.session_state.pop("wnba_prizepicks_debug", None)
        current_odds = st.session_state.get("wnba_odds", pd.DataFrame())
        if isinstance(current_odds, pd.DataFrame) and not current_odds.empty and "BookmakerKey" in current_odds.columns:
            st.session_state["wnba_odds"] = current_odds[~current_odds["BookmakerKey"].astype(str).str.lower().eq("prizepicks")].copy()
        st.rerun()
    if import_pp:
        if not pp_uploads:
            st.warning("Upload at least one PrizePicks screenshot, CSV, TXT, or JSON file first.")
        else:
            try:
                parsed_frames: list[pd.DataFrame] = []
                for uploaded_file in pp_uploads:
                    frame = parse_prizepicks_upload(uploaded_file)
                    if isinstance(frame, pd.DataFrame) and not frame.empty:
                        parsed_frames.append(frame)
                pp_rows = pd.concat(parsed_frames, ignore_index=True) if parsed_frames else pd.DataFrame()
                if not pp_rows.empty:
                    dedupe_cols = [c for c in ["NameKey", "Market", "Line", "PromoType"] if c in pp_rows.columns]
                    if dedupe_cols:
                        pp_rows = pp_rows.drop_duplicates(dedupe_cols, keep="last")
                pp_odds, pp_debug = prizepicks_rows_to_odds_frame(pp_rows, board)
                st.session_state["wnba_prizepicks_rows"] = pp_rows
                st.session_state["wnba_prizepicks_debug"] = pp_debug
                existing_odds = st.session_state.get("wnba_odds", pd.DataFrame())
                if isinstance(existing_odds, pd.DataFrame) and not existing_odds.empty and "BookmakerKey" in existing_odds.columns:
                    existing_odds = existing_odds[~existing_odds["BookmakerKey"].astype(str).str.lower().eq("prizepicks")].copy()
                else:
                    existing_odds = pd.DataFrame()
                st.session_state["wnba_odds"] = combine_odds_frames([existing_odds, pp_odds])
                if pp_rows.empty:
                    st.warning("No parseable PrizePicks rows were found. Try a closer screenshot with player name + line + stat visible, or upload a TXT/CSV correction.")
                elif pp_odds.empty:
                    st.warning("PrizePicks rows were parsed, but none matched the current model board. Check the debug table for player-name mismatches.")
                else:
                    st.success(f"Imported {len(pp_rows):,} PrizePicks rows and matched {len(pp_odds) // 2:,} player-market lines.")
                st.rerun()
            except Exception as exc:
                st.error(f"PrizePicks screenshot/import failed: {type(exc).__name__}: {exc}")
    pp_rows_display = st.session_state.get("wnba_prizepicks_rows", pd.DataFrame())
    if isinstance(pp_rows_display, pd.DataFrame) and not pp_rows_display.empty:
        st.caption("Parsed PrizePicks rows")
        st.dataframe(pp_rows_display[[c for c in ["Player", "MarketRaw", "Line", "PromoType"] if c in pp_rows_display.columns]].head(200), hide_index=True, width="stretch")
    pp_debug_display = st.session_state.get("wnba_prizepicks_debug", pd.DataFrame())
    if isinstance(pp_debug_display, pd.DataFrame) and not pp_debug_display.empty:
        with st.expander("PrizePicks match debug", expanded=False):
            st.dataframe(pp_debug_display, hide_index=True, width="stretch")

with st.expander("Automatic The Odds API multi-book WNBA prop-line fetch", expanded=False):
    st.caption("Uses The Odds API event-odds endpoint. The app fetches WNBA player prop markets one event at a time, stores every returned sportsbook line, then your Line source selector decides which line to apply.")
    st.info("Use this like the MLB pitcher dashboard: fetch several sportsbooks once, then switch between Hard Rock, FanDuel, DraftKings, Consensus sportsbooks, Best sportsbook over line, or Best sportsbook under line.")
    the_odds_sport_key = st.text_input("The Odds API sport key", value=DEFAULT_THE_ODDS_SPORT_KEY, help="Usually basketball_wnba. If your account uses a different WNBA sport key, enter it here.")
    selected_default_bookmakers = LINE_SOURCE_DEFAULT_BOOKMAKER_IDS.get(odds_source_mode, THE_ODDS_DEFAULT_BOOKMAKER_IDS)
    if odds_source_mode in {"Consensus sportsbooks", "Best sportsbook over line", "Best sportsbook under line"}:
        selected_default_bookmakers = THE_ODDS_DEFAULT_BOOKMAKER_IDS
    default_bookmaker_text = ",".join(selected_default_bookmakers)
    the_odds_bookmakers = st.text_input("The Odds API bookmaker IDs to fetch", value=default_bookmaker_text, help="Comma-separated sportsbook keys. For consensus/best-line mode, leave the default multi-book list. For one book, select that source above or type its bookmaker key here.")
    the_odds_regions = st.text_input("The Odds API regions for all-book discovery", value="us,us2", help="Only used when you request all returned books/discovery; bookmaker-specific fetches ignore regions per The Odds API rules.")
    the_odds_markets = st.multiselect("Markets to fetch from The Odds API", list(TARGETS), default=list(TARGETS))
    discover_the_odds_books = st.checkbox("Debug/discovery: also check all returned bookmakers if selected books return no rows", value=True)
    the_odds_games = st.multiselect("Games to query from The Odds API", board["Game"].drop_duplicates().tolist(), default=board["Game"].drop_duplicates().tolist())
    if st.button("Find The Odds API WNBA event IDs", width="stretch"):
        if not the_odds_api_key:
            st.error("Enter your temporary The Odds API key in the sidebar.")
        else:
            try:
                odds_events, meta = fetch_the_odds_events(the_odds_api_key, the_odds_sport_key, slate_date)
                st.session_state["wnba_the_odds_events"] = odds_events
                st.session_state["wnba_odds_meta"] = meta
                if odds_events.empty:
                    st.warning("No The Odds API WNBA event IDs matched this slate date. Check the event debug table below or confirm the sport key.")
                else:
                    st.success(f"Found {len(odds_events):,} The Odds API event IDs.")
            except Exception as exc:
                st.error(f"The Odds API event lookup failed: {type(exc).__name__}: {exc}")
    odds_events = st.session_state.get("wnba_the_odds_events", pd.DataFrame())
    if isinstance(odds_events, pd.DataFrame) and not odds_events.empty:
        display_cols = [c for c in ["Game", "TheOddsEventID", "GameTimeUTC", "Source"] if c in odds_events.columns]
        st.dataframe(odds_events[display_cols], hide_index=True, width="stretch")
    odds_event_debug = st.session_state.get("wnba_the_odds_event_debug", pd.DataFrame())
    if isinstance(odds_event_debug, pd.DataFrame) and not odds_event_debug.empty:
        with st.expander("The Odds API event lookup debug", expanded=False):
            st.dataframe(odds_event_debug, hide_index=True, width="stretch")
    manual_the_odds_ids = st.text_area(
        "Manual The Odds API event IDs (optional)",
        value="",
        height=80,
        help="One per line, for example: MIN @ LAS = abc123",
    )

    def _manual_the_odds_id_lookup(text: str) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for line in str(text or "").splitlines():
            if "=" not in line:
                continue
            game, event_id = line.split("=", 1)
            game = game.strip()
            event_id = event_id.strip()
            if game and event_id:
                lookup[game] = event_id
        return lookup


    if st.button("Check The Odds API available markets/bookmakers", width="stretch"):
        if not the_odds_api_key:
            st.error("Enter your temporary The Odds API key in the sidebar.")
        else:
            try:
                manual_lookup = _manual_the_odds_id_lookup(manual_the_odds_ids)
                if (not isinstance(odds_events, pd.DataFrame) or odds_events.empty) and not manual_lookup:
                    odds_events, meta = fetch_the_odds_events(the_odds_api_key, the_odds_sport_key, slate_date)
                    st.session_state["wnba_the_odds_events"] = odds_events
                    st.session_state["wnba_odds_meta"] = meta
                event_lookup = odds_events.set_index("Game")["TheOddsEventID"].astype(str).to_dict() if isinstance(odds_events, pd.DataFrame) and not odds_events.empty else {}
                event_lookup.update(manual_lookup)
                bookmaker_ids = tuple(v.strip().lower().replace(" ", "") for v in str(the_odds_bookmakers or "").split(",") if v.strip())
                market_frames: list[pd.DataFrame] = []
                featured_frames: list[pd.DataFrame] = []
                missing_games: list[str] = []
                last_meta: dict[str, str] = {"provider": "The Odds API"}
                for game in the_odds_games:
                    event_id = event_lookup.get(game)
                    if not event_id:
                        missing_games.append(game)
                        continue
                    all_markets, last_meta = fetch_the_odds_event_markets(the_odds_api_key, the_odds_sport_key, str(event_id), tuple(), regions=the_odds_regions)
                    if not all_markets.empty:
                        all_markets.insert(0, "Game", game)
                    market_frames.append(all_markets)
                    selected_markets, _ = fetch_the_odds_event_markets(the_odds_api_key, the_odds_sport_key, str(event_id), bookmaker_ids, regions=the_odds_regions)
                    if not selected_markets.empty:
                        selected_markets.insert(0, "Game", game)
                        selected_markets["Scope"] = "Selected bookmaker IDs"
                        market_frames.append(selected_markets)
                    featured, _ = fetch_the_odds_featured_odds_check(the_odds_api_key, the_odds_sport_key, str(event_id), regions=the_odds_regions)
                    if not featured.empty:
                        featured.insert(0, "Game", game)
                    featured_frames.append(featured)
                market_debug = pd.concat(market_frames, ignore_index=True) if market_frames else pd.DataFrame()
                featured_debug = pd.concat(featured_frames, ignore_index=True) if featured_frames else pd.DataFrame()
                st.session_state["wnba_the_odds_market_debug"] = market_debug
                st.session_state["wnba_the_odds_featured_debug"] = featured_debug
                st.session_state["wnba_odds_meta"] = last_meta
                if missing_games:
                    st.warning("Missing The Odds API event IDs for: " + ", ".join(missing_games))
                if market_debug.empty and featured_debug.empty:
                    st.warning("The Odds API returned no available markets and no featured odds for the selected events. This points to sport/date/event coverage or account access, not player-name matching.")
                elif not market_debug.empty and not market_debug[[c for c in ["HasPoints", "HasRebounds", "HasAssists", "HasPRA"] if c in market_debug.columns]].any().any():
                    st.warning("The Odds API returned bookmakers/markets, but none of the WNBA player prop markets are available for these events. Use PrizePicks/CSV import for this slate.")
                else:
                    st.success("Market diagnostic complete. Open the market debug tables below.")
                st.rerun()
            except Exception as exc:
                st.error(f"The Odds API market diagnostic failed: {type(exc).__name__}: {exc}")

    if st.button("Fetch selected WNBA prop lines from The Odds API", width="stretch"):
        if not the_odds_api_key:
            st.error("Enter your temporary The Odds API key in the sidebar.")
        else:
            try:
                manual_lookup = _manual_the_odds_id_lookup(manual_the_odds_ids)
                if (not isinstance(odds_events, pd.DataFrame) or odds_events.empty) and not manual_lookup:
                    odds_events, meta = fetch_the_odds_events(the_odds_api_key, the_odds_sport_key, slate_date)
                    st.session_state["wnba_the_odds_events"] = odds_events
                    st.session_state["wnba_odds_meta"] = meta
                elif (not isinstance(odds_events, pd.DataFrame) or odds_events.empty) and manual_lookup:
                    meta = {"provider": "The Odds API", "endpoint": "manual event IDs"}
                    st.session_state["wnba_odds_meta"] = meta
                event_lookup = odds_events.set_index("Game")["TheOddsEventID"].astype(str).to_dict() if isinstance(odds_events, pd.DataFrame) and not odds_events.empty else {}
                event_lookup.update(manual_lookup)
                wanted_markets = tuple(TARGETS[target]["market"] for target in the_odds_markets)
                bookmaker_ids = tuple(v.strip().lower().replace(" ", "") for v in str(the_odds_bookmakers or "").split(",") if v.strip())
                frames: list[pd.DataFrame] = []
                missing_games: list[str] = []
                debug_rows: list[dict[str, Any]] = []
                last_meta: dict[str, str] = {"provider": "The Odds API"}
                for game in the_odds_games:
                    event_id = event_lookup.get(game)
                    if not event_id:
                        missing_games.append(game)
                        continue
                    frame, last_meta = fetch_the_odds_event_props(the_odds_api_key, the_odds_sport_key, str(event_id), bookmaker_ids, wanted_markets, regions=the_odds_regions, discover_all_books=False)
                    debug_row = {"Game": game, "TheOddsEventID": str(event_id), **last_meta}
                    # If selected books return nothing, optionally do a no-bookmaker-filter request
                    # only for debugging so you can see whether The Odds API has any props at all.
                    if (frame is None or frame.empty) and discover_the_odds_books:
                        discovery_frame, discovery_meta = fetch_the_odds_event_props(the_odds_api_key, the_odds_sport_key, str(event_id), tuple(), wanted_markets, regions=the_odds_regions, discover_all_books=True)
                        debug_row.update({
                            "discovery_returned_bookmakers": discovery_meta.get("returned_bookmakers", ""),
                            "discovery_raw_markets": discovery_meta.get("raw_markets", ""),
                            "discovery_parsed_quote_rows": discovery_meta.get("parsed_quote_rows", ""),
                        })
                    debug_rows.append(debug_row)
                    if frame is not None and not frame.empty:
                        board_event_ids = board.loc[board["Game"].eq(game), "EventID"].dropna().astype(str).unique()
                        if len(board_event_ids):
                            frame["EventID"] = board_event_ids[0]
                    frames.append(frame if frame is not None else pd.DataFrame())
                if debug_rows:
                    st.session_state["wnba_the_odds_fetch_debug"] = pd.DataFrame(debug_rows)
                combined = combine_odds_frames(frames)
                existing_odds = st.session_state.get("wnba_odds", pd.DataFrame())
                if isinstance(existing_odds, pd.DataFrame) and not existing_odds.empty and "Source" in existing_odds.columns:
                    existing_odds = existing_odds[~existing_odds["Source"].astype(str).str.lower().eq("the odds api")].copy()
                else:
                    existing_odds = pd.DataFrame()
                st.session_state["wnba_odds"] = combine_odds_frames([existing_odds, combined])
                st.session_state["wnba_odds_meta"] = last_meta
                if missing_games:
                    st.warning("Missing The Odds API event IDs for: " + ", ".join(missing_games))
                if combined.empty:
                    st.warning("No selected-book player props were parsed from The Odds API. Open the debug table: if returned_bookmakers is blank, those books did not return props for that game/market; if discovery_returned_bookmakers has other books, The Odds API has props but not for your selected IDs.")
                else:
                    # Keep the user's existing selectbox choice. Streamlit does not allow
                    # changing a widget-backed session-state key after that widget has
                    # already been created during the current run.
                    returned_books = sorted(combined.get("Bookmaker", pd.Series(dtype=str)).dropna().astype(str).unique())
                    book_text = ", ".join(returned_books[:8]) + ("..." if len(returned_books) > 8 else "")
                    st.success(f"Fetched {len(combined):,} quote rows from The Odds API across {combined.get('BookmakerKey', pd.Series(dtype=str)).nunique()} books. Books: {book_text}")
                st.rerun()
            except Exception as exc:
                st.error(f"The Odds API prop fetch failed: {type(exc).__name__}: {exc}")
    the_odds_meta = st.session_state.get("wnba_odds_meta", {})
    if isinstance(the_odds_meta, dict) and the_odds_meta.get("provider") == "The Odds API":
        st.caption("The Odds API meta: " + " · ".join(f"{k}: {v}" for k, v in the_odds_meta.items() if v))
    the_odds_debug_fetch = st.session_state.get("wnba_the_odds_fetch_debug", pd.DataFrame())
    if isinstance(the_odds_debug_fetch, pd.DataFrame) and not the_odds_debug_fetch.empty:
        with st.expander("The Odds API fetch debug", expanded=False):
            st.dataframe(the_odds_debug_fetch, hide_index=True, width="stretch")

    the_odds_market_debug = st.session_state.get("wnba_the_odds_market_debug", pd.DataFrame())
    if isinstance(the_odds_market_debug, pd.DataFrame) and not the_odds_market_debug.empty:
        with st.expander("The Odds API available markets debug", expanded=False):
            st.dataframe(the_odds_market_debug, hide_index=True, width="stretch")
    the_odds_featured_debug = st.session_state.get("wnba_the_odds_featured_debug", pd.DataFrame())
    if isinstance(the_odds_featured_debug, pd.DataFrame) and not the_odds_featured_debug.empty:
        with st.expander("The Odds API featured odds diagnostic", expanded=False):
            st.dataframe(the_odds_featured_debug, hide_index=True, width="stretch")


odds = st.session_state.get("wnba_odds", pd.DataFrame())
if isinstance(odds, pd.DataFrame) and not odds.empty:
    # Keep only PrizePicks uploads and The Odds API quotes. Older SportsGameOdds rows from prior sessions are ignored.
    if "Source" in odds.columns:
        odds = odds[~odds["Source"].astype(str).str.contains("SportsGameOdds", case=False, na=False)].copy()
    if "BookmakerKey" in odds.columns:
        # Remove common SportsGameOdds-only bookmaker aliases that may have been cached from older app versions.
        stale_keys = {"sportsgameodds", "sgo"}
        odds = odds[~odds["BookmakerKey"].astype(str).str.lower().isin(stale_keys)].copy()
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


def safe_styled_dataframe(view: pd.DataFrame, format_map: dict | None = None, gradient_cols: list[str] | None = None, height: int = 650) -> None:
    """Display a dataframe without crashing if matplotlib is unavailable on Streamlit Cloud."""
    format_map = format_map or {}
    gradient_cols = [c for c in (gradient_cols or []) if c in view.columns]
    try:
        styled = view.style
        if gradient_cols:
            styled = styled.background_gradient(cmap="RdYlGn", subset=gradient_cols, axis=0)
        styled = styled.format(format_map)
        st.dataframe(styled, hide_index=True, width="stretch", height=height)
    except ImportError:
        # pandas Styler.background_gradient requires matplotlib. If it is missing, show the table normally.
        st.dataframe(view, hide_index=True, width="stretch", height=height)
    except Exception:
        # Keep the dashboard alive if styling fails for any reason.
        st.dataframe(view, hide_index=True, width="stretch", height=height)


def render_target_table(target: str) -> None:
    config = TARGETS[target]
    columns = [
        "Rank", "Player", "Team", "Opponent", "HomeAway", "Proj_MIN", config["projection"],
        config["score"], config["sd"], config["line"], config["model_over"], config["market_prob"],
        config["edge"], f"{target}_Projection_Edge", f"{target}_Line_Source", "Confidence", "Injury_Status", "Role",
    ]
    view = board[[c for c in columns if c in board.columns]].copy()
    rename = {
        "Proj_MIN": "Proj Min", config["projection"]: f"Proj {target}", config["score"]: "Target Score",
        config["sd"]: "SD", config["line"]: "Line", config["model_over"]: "Model Over",
        config["market_prob"]: "Market Over", config["edge"]: "Prob Edge",
        f"{target}_Projection_Edge": "Projection Edge", f"{target}_Line_Source": "Line Source", "Injury_Status": "Status",
    }
    view = view.rename(columns=rename)
    style_cols = [c for c in [f"Proj {target}", "Target Score", "Model Over", "Prob Edge"] if c in view.columns]
    safe_styled_dataframe(view, {
        "Proj Min": "{:.1f}", f"Proj {target}": "{:.2f}", "Target Score": "{:.1f}", "SD": "{:.2f}",
        "Line": "{:.1f}", "Model Over": "{:.1%}", "Market Over": "{:.1%}",
        "Prob Edge": "{:+.1%}", "Projection Edge": "{:+.2f}",
    }, style_cols, height=650)


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
    safe_styled_dataframe(
        view,
        {
            "Proj Min": "{:.1f}", "PTS": "{:.2f}", "REB": "{:.2f}", "AST": "{:.2f}", "PRA": "{:.2f}",
            "PTS Score": "{:.1f}", "REB Score": "{:.1f}", "AST Score": "{:.1f}", "PRA Score": "{:.1f}", "Overall": "{:.1f}",
        },
        [c for c in gradient if c in view.columns],
        height=680,
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
        config["market_prob"]: "Market Over",
    })
    edited = st.data_editor(
        editor, hide_index=True, width="stretch", height=620,
        disabled=[c for c in editor.columns if c not in ["Line", "Over Odds", "Under Odds", "Market Over"]],
        column_config={
            "Line": st.column_config.NumberColumn(step=0.5, format="%.1f"),
            "Over Odds": st.column_config.NumberColumn(step=1),
            "Under Odds": st.column_config.NumberColumn(step=1),
            "Market Over": st.column_config.NumberColumn(min_value=0.0, max_value=1.0, step=0.01, format="%.3f"),
        }, key=f"wnba_line_editor_{target}",
    )
    if st.button("Apply line edits", key=f"apply_line_edits_{target}"):
        updates = edited.rename(columns={
            "Line": config["line"], "Over Odds": config["over_odds"], "Under Odds": config["under_odds"],
            "Market Over": config["market_prob"],
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
    st.markdown("### Integrated calibration")
    st.caption("Streamlit Cloud workflow: download pregame snapshots, then upload snapshots/master history here when you want to grade or calibrate. Session storage is temporary and may reset on redeploy/restart.")
    sync = st.session_state.get("wnba_auto_calibration_sync", {})
    sync_cal = sync.get("calibration", {}) if isinstance(sync, dict) else {}
    status_cols = st.columns(4)
    status_cols[0].metric("Newly graded", f"{int(sync.get('newly_graded', 0)):,}" if isinstance(sync, dict) else "0")
    status_cols[1].metric("Auto-refit", "Ran" if sync_cal.get("ran") else "No new rows")
    status_cols[2].metric("Promoted", ", ".join(sync_cal.get("promoted", [])) or "None")
    status_cols[3].metric("Active targets", ", ".join(load_active_calibration_db().get("targets", {}).keys()) or "None")
    if st.session_state.get("wnba_last_auto_snapshot_utc"):
        st.info(f"Current board is also held in this temporary Streamlit session since {st.session_state['wnba_last_auto_snapshot_utc']}. Download the snapshot below for durable storage.")

    st.markdown("### Snapshot / recovery tools")
    retro_clean_snapshot = st.checkbox(
        "Mark this snapshot as retro-clean backfill",
        value=False,
        help="Use this when you are recreating an old slate with only pregame-safe inputs. Retro-clean rows can be included in calibration even though GeneratedAtUTC is after tipoff.",
    )
    snapshot_type = "retro_clean" if retro_clean_snapshot else "pregame_live"
    snapshot = build_snapshot(board, slate_date, snapshot_type=snapshot_type)
    st.download_button(
        "Download pregame snapshot CSV", snapshot.to_csv(index=False).encode("utf-8"),
        file_name=f"wnba_props_snapshot_{slate_date}.csv", mime="text/csv", width="stretch",
    )
    uploads = st.file_uploader("Upload snapshot or master-history CSV files", type=["csv"], accept_multiple_files=True, key="wnba_history_upload")
    if uploads:
        signature = tuple((item.name, len(item.getvalue())) for item in uploads)
        if st.session_state.get("wnba_history_signature") != signature:
            imported_history, history_errors = combine_history_uploads(uploads)
            history = merge_history(load_history_db(), imported_history)
            st.session_state["wnba_history"] = history
            st.session_state["wnba_history_errors"] = history_errors
            st.session_state["wnba_history_signature"] = signature
            if not history.empty:
                save_history_db(history)
    if "wnba_history" not in st.session_state:
        st.session_state["wnba_history"] = load_history_db()
    history = normalize_history(st.session_state.get("wnba_history", pd.DataFrame()))
    st.caption("History in this browser session is temporary. Use the download/upload controls as the durable source of truth.")
    manual_results_db = load_manual_results_db()
    calibration_player_logs = merge_result_logs(player_logs, manual_results_db) if not manual_results_db.empty else player_logs
    diag = calibration_grading_diagnostics(history, calibration_player_logs)
    diag_cols = st.columns(5)
    diag_cols[0].metric("Snapshot rows", f"{diag['history_rows']:,}")
    diag_cols[1].metric("Graded rows", f"{diag['completed_rows']:,}")
    diag_cols[2].metric("Pending rows", f"{diag['pending_rows']:,}")
    diag_cols[3].metric("Latest stats date", str(diag.get('latest_log_date') or "None"))
    diag_cols[4].metric("Latest graded date", str(diag.get('latest_graded_date') or "None"))
    if "live refresh failed" in str(data_source).lower():
        st.warning("The last manual stats refresh could not obtain newer provider data, so grading is using the saved local game-log snapshot.")
    if isinstance(diag.get("by_date"), pd.DataFrame) and not diag["by_date"].empty:
        with st.expander("Grading coverage by slate date", expanded=False):
            st.dataframe(diag["by_date"], width="stretch", hide_index=True)
    st.markdown("### Completed results import")
    st.caption(
        "Optional grading path: upload a CSV of finalized player results for this session. For durable recovery, download the updated master history afterward."
    )
    results_template = pd.DataFrame(columns=["GameDate", "Player", "Team", "PTS", "REB", "AST", "MIN"])
    st.download_button(
        "Download completed-results CSV template",
        results_template.to_csv(index=False).encode("utf-8"),
        file_name="wnba_completed_results_template.csv",
        mime="text/csv",
        width="stretch",
        key="wnba_results_template_download",
    )
    results_upload = st.file_uploader(
        "Upload completed WNBA player results CSV",
        type=["csv"],
        key="wnba_completed_results_upload",
        help="Required: Player and GameDate/Date, plus PTS/Points, REB/Rebounds and/or AST/Assists. Team is strongly recommended.",
    )
    if results_upload is not None:
        try:
            raw_results_upload = pd.read_csv(results_upload)
            parsed_results_upload, results_upload_errors = normalize_completed_results_upload(raw_results_upload)
            if results_upload_errors:
                for err in results_upload_errors:
                    st.error(err)
            elif not parsed_results_upload.empty:
                preview_cols = [c for c in ["GameDate", "Player", "Team", "PTS", "REB", "AST", "MIN"] if c in parsed_results_upload.columns]
                st.success(f"Parsed {len(parsed_results_upload):,} player-game result rows.")
                with st.expander("Preview imported results", expanded=False):
                    st.dataframe(parsed_results_upload[preview_cols].head(100), width="stretch", hide_index=True)
                if st.button("Import results + grade + calibration check", type="primary", width="stretch", key="wnba_import_results_grade"):
                    saved_manual = save_manual_results_db(parsed_results_upload)
                    combined_results = merge_result_logs(player_logs, saved_manual)
                    sync_import = grade_and_calibrate_db(combined_results, force_refit=False)
                    st.session_state["wnba_player_logs_data"] = combined_results
                    st.session_state["wnba_history"] = sync_import.get("history", history)
                    st.session_state["wnba_result_summary"] = {
                        "matched": sync_import.get("matched", 0),
                        "unmatched": sync_import.get("unmatched", 0),
                        "matched_by_id": sync_import.get("matched_by_id", 0),
                        "matched_by_name": sync_import.get("matched_by_name", 0),
                        "matched_by_game_time": sync_import.get("matched_by_game_time", 0),
                    }
                    st.session_state["wnba_auto_calibration_sync"] = sync_import
                    if sync_import.get("calibration", {}).get("promoted"):
                        st.session_state["wnba_projection_calibration"] = load_active_calibration_db()
                    st.session_state["wnba_manual_results_last_import"] = len(parsed_results_upload)
                    st.rerun()
        except Exception as exc:
            st.error(f"Could not read completed-results CSV: {type(exc).__name__}: {exc}")

    stored_manual_results = load_manual_results_db()
    if not stored_manual_results.empty:
        stored_dates = pd.to_datetime(stored_manual_results["GameDate"], errors="coerce").dt.date.dropna()
        date_text = f"{min(stored_dates)} → {max(stored_dates)}" if not stored_dates.empty else "date unavailable"
        st.caption(f"SQLite completed-results store: {len(stored_manual_results):,} player-game rows · {date_text}")
        st.download_button(
            "Download stored completed results backup",
            stored_manual_results.to_csv(index=False).encode("utf-8"),
            file_name="wnba_completed_results_backup.csv",
            mime="text/csv",
            width="stretch",
            key="wnba_download_manual_results",
        )

    st.caption("Auto-promotion rule: at least 75 completed player-games and ≥0.01 lower chronological holdout MAE than raw projections and the currently active target calibration.")
    include_retro_clean_rows = st.checkbox(
        "Include retro-clean backfill rows in calibration analysis",
        value=True,
        help="Keeps true pregame rows plus rows marked SnapshotType=retro_clean. This prevents clean historical backfills from being excluded just because they were created after tipoff.",
    )
    retro_actions = st.columns(2)
    with retro_actions[0]:
        if st.button("Mark uploaded after-start rows as retro-clean", width="stretch", help="Use only for backfilled slates rebuilt with pregame-safe inputs. This marks current after-start rows as SnapshotType=retro_clean."):
            if not history.empty:
                mask = history["SnapshotAfterStart"].fillna(False)
                history.loc[mask, "SnapshotType"] = "retro_clean"
                history = normalize_history(history)
                st.session_state["wnba_history"] = history
                save_history_db(history)
                st.success(f"Marked {int(mask.sum()):,} rows as retro-clean in this session. Download the updated master history to keep the change.")
    with retro_actions[1]:
        st.caption("Retro-clean rows can get official results and count in calibration; postgame/dirty rows should stay excluded.")
    result_refresh_col, result_status_col = st.columns([1, 1])
    with result_refresh_col:
        refresh_results_now = st.button(
            "Try public web results + grade (optional)",
            width="stretch",
            help="Best-effort only. Public WNBA result sources may be stale, blocked, or rate-limited. Manual completed-results import is the reliable grading path.",
        )
    with result_status_col:
        st.caption(f"Current grading result source: {data_source or 'None loaded'}")
        if str(diag.get("latest_log_date") or "") < str(slate_date):
            st.caption("Public result sources are best-effort only. If they are behind or blocked, use the completed-results CSV import above.")
        if st.session_state.get("wnba_grading_sdv_github_error"):
            st.warning(f"SportsDataverse GitHub live-results attempt: {st.session_state['wnba_grading_sdv_github_error']}")
        sdv_latest = st.session_state.get("wnba_sdv_github_latest_date")
        sdv_total = st.session_state.get("wnba_sdv_github_total_rows")
        sdv_range_rows = st.session_state.get("wnba_sdv_github_range_rows")
        sdv_range = st.session_state.get("wnba_sdv_github_range")
        if sdv_latest or sdv_total is not None:
            with st.expander("SportsDataverse GitHub result diagnostics", expanded=True):
                st.dataframe(pd.DataFrame([{
                    "RepositoryLatestDate": sdv_latest,
                    "SeasonPlayerRows": int(sdv_total or 0),
                    "RequestedRange": sdv_range,
                    "RowsInRequestedRange": int(sdv_range_rows or 0),
                }]), width="stretch", hide_index=True)
        if st.session_state.get("wnba_grading_bref_error"):
            st.warning(f"Basketball-Reference live-results attempt: {st.session_state['wnba_grading_bref_error']}")
        bref_diag = st.session_state.get("wnba_bref_discovery_diag") or {}
        if bref_diag:
            st.caption(
                "Basketball-Reference discovery: "
                f"{bref_diag.get('boxscore_links', 0)} finalized/constructed box-score links found "
                f"for {bref_diag.get('start_date', '?')} → {bref_diag.get('end_date', '?')} "
                f"(constructed fallback: {bref_diag.get('constructed_links', 0)})."
            )
            date_diag = (st.session_state.get("wnba_bref_rows_diag") or {}).get("per_date") or bref_diag.get("per_date") or {}
            if date_diag:
                diag_rows = []
                coverage_df = diag.get("by_date") if isinstance(diag.get("by_date"), pd.DataFrame) else pd.DataFrame()
                coverage_map = {}
                if coverage_df is not None and not coverage_df.empty:
                    for rec in coverage_df.to_dict("records"):
                        coverage_map[str(rec.get("SlateDate"))] = rec
                for key in sorted(date_diag.keys(), reverse=True):
                    rec = dict(date_diag[key])
                    cov = coverage_map.get(str(key), {})
                    rec["SnapshotRows"] = int(cov.get("SnapshotRows", 0) or 0)
                    rec["GradedRows"] = int(cov.get("GradedRows", 0) or 0)
                    rec["PendingRows"] = int(cov.get("PendingRows", 0) or 0)
                    diag_rows.append(rec)
                with st.expander("Basketball-Reference fetch diagnostics by date", expanded=True):
                    st.dataframe(pd.DataFrame(diag_rows), width="stretch", hide_index=True)

    if refresh_results_now:
        try:
            with st.spinner("Refreshing finalized WNBA box scores and grading pending snapshots…"):
                grading_logs = pd.DataFrame()
                grading_source_parts: list[str] = []
                sdv_github_error = ""
                bref_error = ""

                actual_cols = [c["actual"] for c in TARGETS.values() if c["actual"] in history.columns]
                pending_mask = ~history[actual_cols].notna().any(axis=1) if actual_cols else pd.Series(True, index=history.index)
                pending_dates = pd.to_datetime(history.loc[pending_mask, "SlateDate"], errors="coerce").dt.date.dropna() if not history.empty else pd.Series([], dtype=object)

                fresh_result_frames: list[pd.DataFrame] = []
                if not pending_dates.empty:
                    result_start = min(pending_dates)
                    result_end = min(max(pending_dates), date.today())

                    # Primary: current processed SportsDataverse/wehoop player-boxscore
                    # parquet from GitHub. This bypasses ESPN's blocked live endpoint.
                    try:
                        sdv_live = fetch_sportsdataverse_github_results(
                            season, season_type, result_start, result_end,
                        )
                        if not sdv_live.empty:
                            fresh_result_frames.append(sdv_live)
                            grading_source_parts.append(f"SportsDataverse GitHub ({len(sdv_live):,} rows)")
                    except Exception as exc:
                        sdv_github_error = f"{type(exc).__name__}: {exc}"

                    # Secondary: Basketball-Reference. Keep it even when ESPN succeeds
                    # so it can fill dates/games ESPN did not return.
                    try:
                        bref_live = fetch_basketball_reference_results(
                            season, result_start, result_end, history=history,
                        )
                        if not bref_live.empty:
                            fresh_result_frames.append(bref_live)
                            grading_source_parts.append(f"Basketball-Reference ({len(bref_live):,} rows)")
                            st.session_state["wnba_bref_live_rows"] = int(len(bref_live))
                    except Exception as exc:
                        bref_error = f"{type(exc).__name__}: {exc}"

                if fresh_result_frames:
                    fresh_combined = pd.concat(fresh_result_frames, ignore_index=True)
                    fresh_combined = normalize_player_logs(fresh_combined)
                    fresh_combined = fresh_combined.drop_duplicates(["GameDate", "PlayerID"], keep="last")
                    grading_logs = merge_result_logs(calibration_player_logs, fresh_combined)
                    grading_source = " + ".join(grading_source_parts + (["SQLite manual results"] if not manual_results_db.empty else []))
                else:
                    # Last-resort historical chain. This may be stale, but keeps the
                    # dashboard usable if both live result paths are unavailable.
                    for cached_func in (load_official_game_logs, fetch_wnba_stats, load_espn_game_logs):
                        try:
                            cached_func.clear()
                        except Exception:
                            pass
                    fresh_players, fresh_teams, fresh_source = load_official_game_logs(season, season_type, force_refresh=True)
                    grading_logs = merge_result_logs(fresh_players, manual_results_db) if not manual_results_db.empty else fresh_players
                    grading_source = fresh_source + (" + SQLite manual results" if not manual_results_db.empty else "")
                    st.session_state["wnba_team_logs_data"] = fresh_teams

                st.session_state["wnba_player_logs_data"] = grading_logs
                st.session_state["wnba_data_source"] = grading_source
                st.session_state["wnba_grading_sdv_github_error"] = sdv_github_error
                st.session_state["wnba_grading_bref_error"] = bref_error
                sync_results = grade_and_calibrate_db(grading_logs, force_refit=False)
                st.session_state["wnba_history"] = sync_results.get("history", history)
                st.session_state["wnba_result_summary"] = {
                    "matched": sync_results.get("matched", 0),
                    "unmatched": sync_results.get("unmatched", 0),
                    "matched_by_id": sync_results.get("matched_by_id", 0),
                    "matched_by_name": sync_results.get("matched_by_name", 0),
                    "matched_by_game_time": sync_results.get("matched_by_game_time", 0),
                }
                st.session_state["wnba_auto_calibration_sync"] = sync_results
                try:
                    date_diag = dict((st.session_state.get("wnba_bref_rows_diag") or {}).get("per_date") or {})
                    graded_hist = normalize_history(sync_results.get("history", pd.DataFrame()))
                    if date_diag and not graded_hist.empty:
                        actual_cols = [c["actual"] for c in TARGETS.values() if c["actual"] in graded_hist.columns]
                        graded_mask = graded_hist[actual_cols].notna().any(axis=1) if actual_cols else pd.Series(False, index=graded_hist.index)
                        gd = pd.to_datetime(graded_hist.get("SlateDate"), errors="coerce").dt.date
                        counts = pd.DataFrame({"d": gd, "g": graded_mask}).dropna().groupby("d")["g"].sum().to_dict()
                        for d, cnt in counts.items():
                            key = d.isoformat()
                            if key in date_diag:
                                date_diag[key]["MatchedSnapshotRows"] = int(cnt)
                        rd = dict(st.session_state.get("wnba_bref_rows_diag") or {})
                        rd["per_date"] = date_diag
                        st.session_state["wnba_bref_rows_diag"] = rd
                except Exception:
                    pass
                if sync_results.get("calibration", {}).get("promoted"):
                    st.session_state["wnba_projection_calibration"] = load_active_calibration_db()
            st.rerun()
        except Exception as exc:
            st.error(f"Completed-results refresh failed: {type(exc).__name__}: {exc}")

    action1, action2 = st.columns(2)
    with action1:
        if st.button("Save another snapshot now", width="stretch"):
            history = save_snapshot_db(snapshot)
            st.session_state["wnba_history"] = history
            st.success("Snapshot added to this session history. Download the updated master history or snapshot CSV to keep it permanently.")
    with action2:
        if st.button("Grade + refit calibration now", type="primary", width="stretch"):
            sync_now = grade_and_calibrate_db(calibration_player_logs, force_refit=True)
            history = sync_now.get("history", history)
            st.session_state["wnba_history"] = history
            st.session_state["wnba_result_summary"] = {"matched": sync_now.get("matched", 0), "unmatched": sync_now.get("unmatched", 0)}
            st.session_state["wnba_auto_calibration_sync"] = sync_now
            if sync_now.get("calibration", {}).get("promoted"):
                st.session_state["wnba_projection_calibration"] = load_active_calibration_db()
            st.rerun()
    if not history.empty:
        st.download_button(
            "Download updated master history", history.to_csv(index=False).encode("utf-8"),
            file_name="wnba_props_master_history.csv", mime="text/csv", width="stretch",
        )
    summary = st.session_state.get("wnba_result_summary")
    if summary:
        st.caption(
            f"Results matched: {summary.get('matched', 0)} · unmatched: {summary.get('unmatched', 0)} "
            f"· by ID: {summary.get('matched_by_id', 0)} · by name/date: {summary.get('matched_by_name', 0)} "
            f"· repaired by saved game time: {summary.get('matched_by_game_time', 0)}"
        )

    analysis = latest_pregame_history(history, include_retro_clean=include_retro_clean_rows)
    completed = analysis[analysis[[config["actual"] for config in TARGETS.values()]].notna().any(axis=1)].copy() if not analysis.empty else pd.DataFrame()
    metrics = st.columns(5)
    after_start_n = int(history.get("SnapshotAfterStart", pd.Series(dtype=bool)).fillna(False).sum())
    retro_clean_n = int(history.get("SnapshotRetroClean", pd.Series(dtype=bool)).fillna(False).sum())
    metrics[0].metric("Master rows", f"{len(history):,}")
    metrics[1].metric("Analysis rows", f"{len(analysis):,}")
    metrics[2].metric("Completed outcomes", f"{len(completed):,}")
    metrics[3].metric("After-start rows", f"{after_start_n:,}")
    metrics[4].metric("Retro-clean rows", f"{retro_clean_n:,}")

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

        st.markdown("### Calibration audit / manual override")
        min_rows = st.number_input("Minimum completed player-games", min_value=30, max_value=1000, value=75, step=25)
        auto_good_targets = []
        for _target in TARGETS:
            _fit = fit_linear_calibration(completed, _target, int(min_rows))
            if _fit.get("ok") and np.isfinite(_fit.get("holdout_raw_mae", np.nan)) and np.isfinite(_fit.get("holdout_calibrated_mae", np.nan)) and _fit["holdout_calibrated_mae"] < _fit["holdout_raw_mae"]:
                auto_good_targets.append(_target)
        selected_fit_targets = st.multiselect("Targets to include if manually applied", list(TARGETS), default=auto_good_targets)
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
                if st.button("Manually activate selected calibration", width="stretch"):
                    st.session_state["wnba_projection_calibration"] = bundle
                    save_active_calibration_db(bundle)
                    base = st.session_state.get("wnba_base_board", board)
                    calibrated = apply_projection_calibration(base, bundle)
                    calibrated = apply_market_quotes(calibrated, odds, odds_source_mode)
                    st.session_state["wnba_board"] = calibrated
                    st.rerun()

        calibration_history = calibration_history_db()
        if not calibration_history.empty:
            st.markdown("### Saved calibration history")
            st.dataframe(calibration_history, hide_index=True, width="stretch")

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
        - **Prob Edge** compares model over probability with the no-vig market over probability.
        - **Confidence** reflects game/minute sample depth, not certainty that the prop will win.
        - Building the board keeps a pregame snapshot in the current Streamlit session. Download the snapshot/master-history CSV for durable storage because Streamlit Cloud local storage can reset.
        - Upload saved snapshots/master history when you want to restore calibration history after a cloud restart. Grading can still run in-session when completed results are available.
        - Automatic promotion is target-specific and requires chronological holdout MAE to improve versus both raw projections and the currently active calibration. Download calibration JSON after promotion if you want to restore it later.

        Data sources: official WNBA Stats for completed game logs; official WNBA schedule/injury pages when available; The Odds API or PrizePicks uploads for optional prop lines.
        """
    )
