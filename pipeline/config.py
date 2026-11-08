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


# ---- Model inputs and weights (SPEC.md section 6; decisions in section 13) ----
INPUT_KEYS = ("net_rating", "star_power", "recent_form", "star_availability",
              "core_net_rating", "rotation_availability", "clutch")

# Starting weights as raw points. Clutch and core net rating have no usable data source (decided
# Nov 8, 2026), so they are 0. The rest are renormalized to sum to 1.0: each of 38/25/10/10/5 over 88.
WEIGHT_POINTS = {"net_rating": 38, "star_power": 25, "recent_form": 10, "star_availability": 10,
                 "core_net_rating": 0, "rotation_availability": 5, "clutch": 0}


def normalized_weights(points: dict[str, float]) -> dict[str, float]:
    """Return the weights scaled so they sum to 1.0 (an input with 0 points keeps weight 0.0)."""
    total = sum(points.values())
    return {key: points[key] / total for key in INPUT_KEYS}


WEIGHTS = normalized_weights(WEIGHT_POINTS)

# Net rating (SPEC 6.1, 6.3): early in the season, lean on last season.
PRIOR_BLEND_GAMES = 20     # weight on the current season = games / (games + 20)
SOS_FULL_AT = 40           # schedule strength is phased in as min(1, games / 40): early SOS is noisy
PRIOR_REGRESSION = 0.7     # last season's rating is pulled 30% of the way to the league mean

# Star power (SPEC 6.1): the two best players by BPM who actually play.
STAR_WEIGHTS = (0.6, 0.4)
STAR_MIN_MPG = 15              # minimum minutes per game
STAR_MIN_GAMES_FRAC = 0.4      # must have played at least 40% of the team's games...
STAR_MIN_GAMES_FLOOR = 3       # ...but never fewer than 3...
STAR_MIN_GAMES_CAP = 20        # ...and never more than 20 are required

# Star availability (SPEC 6.1): games played over the last three seasons, most recent first.
AVAILABILITY_SEASON_WEIGHTS = (0.5, 0.3, 0.2)
INJURY_FACTORS = {"Out For Season": 0.25, "Out": 0.6, "Day To Day": 0.9}  # multiplies a star's availability
DAY_TO_DAY_MAX_AGE_DAYS = 14   # an older "Day To Day" entry is treated as stale and ignored

# Rotation availability and recent form (SPEC 6.1)
ROTATION_SIZE = 9
FORM_GAMES = 20
FORM_MIN_GAMES = 5             # fewer finished games than this: recent form is "not available"

# ---- From inputs to odds (SPEC.md sections 6.2 and 6.3) ----
TEMPERATURE = 0.6              # softmax temperature: lower = favorites get a bigger share
PROB_DECIMALS = 6              # title_prob is stored to 6 decimals and sums to exactly 1.0

# Penalties are subtracted from a team's composite score (in z-score units).
TWO_WAY_RANK_CUTOFF = 17       # offense OR defense ranked worse than 17th...
TWO_WAY_PENALTY = 0.5          # ...costs 0.5 (once, even if both are bad)
TWO_WAY_MIN_GAMES = 20         # not applied until a team has played 20 games
LATE_GATE_RANK_CUTOFF = 10     # from March 1, net rating ranked worse than 10th...
LATE_GATE_PENALTY = 1.5        # ...costs 1.5
LATE_GATE_START = (3, 1)       # (month, day) in the year the season ends

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

# ---- Playoff simulation (SPEC.md section 6.4; runs in the daily build in SHADOW MODE: published in
# separate fields, the headline title_prob stays the softmax) ----
# A team's strength in points of margin against an average team is MARGIN_PER_Z * composite.
# MARGIN_PER_Z was fitted once, on 2026-11-08, by `python pipeline/calibrate_sim.py`: the value (grid of
# 15 values, refined to 0.1) that minimizes the sum of squared gaps between the simulation's title
# probabilities and the live softmax's on that day's real data. Backtest check (docs/simulation-report.md):
# the best margin on 26 past seasons was 3.0 (after the Nov 8 backtest-loader fix), and leave-one-season-out picks ranged 3.0 to 4.0.
# It is NOT refit in the daily run.
MARGIN_PER_Z = 3.3
HOME_COURT_POINTS = 2.5        # added to the home team's margin in every game
GAME_LOGISTIC_SCALE = 6.5      # p_home = 1 / (1 + exp(-(r_home - r_away + HCA) / scale)); ~ a 12-point game sd
SIM_RUNS = 20000               # simulated seasons per run
RATING_UNCERTAINTY_POINTS = 3.0   # sd of the per-simulation rating noise at 0 games played
UNCERTAINTY_HALF_GAMES = 20       # noise sd = RATING_UNCERTAINTY_POINTS * sqrt(20 / (games + 20))
# The simulation models seeding and the play-in itself, so its strength is the composite BEFORE the
# penalties (the two-way and late-season penalties exist to approximate playoff-bracket effects in
# the softmax). Set to True to feed it the composite AFTER penalties instead (the way MARGIN_PER_Z
# was fitted on 2026-11-08).
SIM_USES_PENALTIES = False
