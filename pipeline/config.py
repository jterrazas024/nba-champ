"""Shared settings for the odds pipeline: fetch politeness, season helpers, and the team table.

Model weights and thresholds are added here in a later step (SPEC.md section 6). Keeping every
tunable value in this one file means a change is made in exactly one place.
"""
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# ---- Fetching (CLAUDE.md "Data source rules"; SPEC.md section 5) ----
BASE_URL = "https://www.basketball-reference.com"
USER_AGENT = "nba-champ-odds-project (hobby project, one daily run; github.com/jterrazas024/nba-champ)"
SECONDS_BETWEEN_REQUESTS = 4.0   # the site's hard limit is 20/min and robots.txt asks for 3s
TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 3                 # per page: 1 try + 2 retries on network errors or 5xx
BACKOFF_SECONDS = (2, 4)         # waits before the 2nd and 3rd attempt
MAX_REQUESTS = 20                # hard cap on requests (including retries) in one run
BLOCK_STATUSES = {403, 429}      # the site refused us: stop the whole run, never retry
CACHE_ROOT = Path(__file__).resolve().parent.parent / "data" / "raw"

# ---- Season helpers ----
SEASON_GAMES = 82


def season_end_year(today: date) -> int:
    """Return the calendar year in which the current NBA season ends (2026-27 -> 2027).

    The season starts in October, so from August on we are in the season that ends next year.
    The site names its pages by this year, e.g. /leagues/NBA_2027.html.
    """
    return today.year + 1 if today.month >= 8 else today.year


def season_label(end_year: int) -> str:
    """Return the season as shown in odds.json, e.g. 2027 -> "2026-27"."""
    return f"{end_year - 1}-{str(end_year)[-2:]}"


# ---- Teams ----
@dataclass(frozen=True)
class Team:
    """One NBA team. `id` is the code our site and odds.json use; `br_code` is Basketball-Reference's."""

    id: str
    br_code: str
    name: str
    conference: str


TEAMS = (
    Team("ATL", "ATL", "Atlanta Hawks", "East"),
    Team("BOS", "BOS", "Boston Celtics", "East"),
    Team("BKN", "BRK", "Brooklyn Nets", "East"),
    Team("CHA", "CHO", "Charlotte Hornets", "East"),
    Team("CHI", "CHI", "Chicago Bulls", "East"),
    Team("CLE", "CLE", "Cleveland Cavaliers", "East"),
    Team("DAL", "DAL", "Dallas Mavericks", "West"),
    Team("DEN", "DEN", "Denver Nuggets", "West"),
    Team("DET", "DET", "Detroit Pistons", "East"),
    Team("GSW", "GSW", "Golden State Warriors", "West"),
    Team("HOU", "HOU", "Houston Rockets", "West"),
    Team("IND", "IND", "Indiana Pacers", "East"),
    Team("LAC", "LAC", "Los Angeles Clippers", "West"),
    Team("LAL", "LAL", "Los Angeles Lakers", "West"),
    Team("MEM", "MEM", "Memphis Grizzlies", "West"),
    Team("MIA", "MIA", "Miami Heat", "East"),
    Team("MIL", "MIL", "Milwaukee Bucks", "East"),
    Team("MIN", "MIN", "Minnesota Timberwolves", "West"),
    Team("NOP", "NOP", "New Orleans Pelicans", "West"),
    Team("NYK", "NYK", "New York Knicks", "East"),
    Team("OKC", "OKC", "Oklahoma City Thunder", "West"),
    Team("ORL", "ORL", "Orlando Magic", "East"),
    Team("PHI", "PHI", "Philadelphia 76ers", "East"),
    Team("PHX", "PHO", "Phoenix Suns", "West"),
    Team("POR", "POR", "Portland Trail Blazers", "West"),
    Team("SAC", "SAC", "Sacramento Kings", "West"),
    Team("SAS", "SAS", "San Antonio Spurs", "West"),
    Team("TOR", "TOR", "Toronto Raptors", "East"),
    Team("UTA", "UTA", "Utah Jazz", "West"),
    Team("WAS", "WAS", "Washington Wizards", "East"),
)

TEAM_BY_ID = {t.id: t for t in TEAMS}
TEAM_BY_BR_CODE = {t.br_code: t for t in TEAMS}
TEAM_BY_NAME = {t.name: t for t in TEAMS}
