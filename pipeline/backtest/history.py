"""Read historical Basketball-Reference pages for the backtest (SPEC.md section 6.5).

Everything here is pure: functions take page HTML (or the cache folder) and return tables. No
network access. Historical franchises are mapped onto today's 30 team ids (a franchise keeps one
id through moves and renames: Sonics -> OKC, Nets -> BKN, Bobcats -> CHA, and so on), so the
pipeline's feature and model code can be used unchanged.
"""
import io
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from pipeline import config
from pipeline.parse import ParseError, parse_games, parse_players, parse_ratings

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "backtest"
FIRST_SEASON = 2001      # first season we score (season ending in 2001 = "2000-01")
LAST_SEASON = 2026

# Regular-season months to read, newest first, for the "last 20 games" recent-form input.
STANDARD_MONTHS = ("april", "march", "february", "january", "december", "november", "october")
SPECIAL_MONTHS = {
    2020: ("august", "july", "march", "february", "january", "december"),   # bubble restart
    2021: ("may", "april", "march", "february", "january"),                 # season ended in May
}


def season_months(end_year: int) -> tuple[str, ...]:
    """Return the month names to try, newest first, for this season's results pages."""
    return SPECIAL_MONTHS.get(end_year, STANDARD_MONTHS)


def team_maps(end_year: int) -> tuple[dict, dict]:
    """Return (names, codes): maps from a season's team names / codes to today's Team objects."""
    by_id = config.TEAM_BY_ID
    names = dict(config.TEAM_BY_NAME)
    codes = dict(config.TEAM_BY_BR_CODE)
    names.update({
        "LA Clippers": by_id["LAC"], "Charlotte Bobcats": by_id["CHA"],
        "New Orleans Hornets": by_id["NOP"], "New Orleans/Oklahoma City Hornets": by_id["NOP"],
        "Seattle SuperSonics": by_id["OKC"], "New Jersey Nets": by_id["BKN"],
        "Vancouver Grizzlies": by_id["MEM"],
    })
    if end_year <= 2002:   # the original Charlotte Hornets are today's New Orleans franchise
        names["Charlotte Hornets"] = by_id["NOP"]
    codes.update({
        "NJN": by_id["BKN"], "SEA": by_id["OKC"], "VAN": by_id["MEM"], "NOH": by_id["NOP"],
        "NOK": by_id["NOP"], "NOP": by_id["NOP"], "CHH": by_id["NOP"], "CHA": by_id["CHA"],
        "PHX": by_id["PHX"], "BRK": by_id["BKN"], "CHO": by_id["CHA"],
    })
    if end_year <= 2002:
        codes["CHA"] = by_id["NOP"]
    return names, codes


def regular_season_html(html: str) -> str:
    """Return a monthly results page cut off at its "Playoffs" separator row, if it has one."""
    match = re.search(r"<tr[^>]*>\s*<th[^>]*>\s*Playoffs\s*</th>", html)
    return html[:match.start()] + "</tbody></table></body></html>" if match else html


def parse_regular_games(html: str, names: dict) -> pd.DataFrame:
    """Finished regular-season games on a monthly results page (play-in games are dropped)."""
    games = parse_games(regular_season_html(html), names)
    return games[~games["notes"].str.contains("Play-In", case=False, na=False)].reset_index(drop=True)


def parse_conferences(html: str, names: dict) -> dict[str, str]:
    """Return {team_id: "East" | "West"} from the season page's standings tables."""
    uncommented = html.replace("<!--", "").replace("-->", "")
    result: dict[str, str] = {}
    for code, conference in (("E", "East"), ("W", "West")):
        for table_id in (f"confs_standings_{code}", f"divs_standings_{code}"):
            try:
                table = pd.read_html(io.StringIO(uncommented), attrs={"id": table_id}, flavor="lxml")[0]
            except ValueError:
                continue
            for cell in table.iloc[:, 0].astype(str):
                name = cell.rstrip("*").strip()
                if name in names:
                    result[names[name].id] = conference
            break
    return result


@dataclass(frozen=True)
class Series:
    """One playoff series: round name, winner and loser (team ids), and the games each won."""

    round: str
    winner: str
    loser: str
    winner_games: int
    loser_games: int


_SERIES = re.compile(r"^(?P<w>.+?) over (?P<l>.+?)\s*\((?P<a>\d+)-(?P<b>\d+)\)\s*$")


def parse_playoff_series(html: str, names: dict) -> list[Series]:
    """Return the series in the page's `all_playoffs` table. Raises ParseError if it is missing."""
    uncommented = html.replace("<!--", "").replace("-->", "")
    try:
        table = pd.read_html(io.StringIO(uncommented), attrs={"id": "all_playoffs"}, flavor="lxml")[0]
    except ValueError as err:
        raise ParseError("table 'all_playoffs' not found on the page") from err
    out = []
    for _, row in table.iterrows():
        label, text = str(row.iloc[0]), str(row.iloc[1])
        match = _SERIES.match(text)
        if not match or match["w"] not in names or match["l"] not in names:
            continue
        out.append(Series(label, names[match["w"]].id, names[match["l"]].id, int(match["a"]), int(match["b"])))
    return out


def champion_of(series: list[Series]) -> str:
    """Return the team id that won the Finals. Raises ParseError if there is no Finals row."""
    finals = [s for s in series if s.round.strip() == "Finals"]
    if len(finals) != 1:
        raise ParseError(f"expected one Finals series, found {len(finals)}")
    return finals[0].winner


def seeds_by_record(ratings: pd.DataFrame, conferences: dict[str, str]) -> pd.DataFrame:
    """Return team_id, conference, seed: each team's rank in its conference by wins (then SRS).

    This is a "record seed": it ignores the old division-winner seeding rule and tie-breakers.
    """
    table = ratings[["team_id", "w", "srs"]].copy()
    table["conference"] = table["team_id"].map(conferences)
    table = table.sort_values(["w", "srs"], ascending=False, kind="stable")
    table["seed"] = table.groupby("conference").cumcount() + 1
    return table[["team_id", "conference", "seed"]].reset_index(drop=True)


@dataclass
class SeasonData:
    """Everything the backtest knows about one season at the end of the regular season."""

    end_year: int
    ratings: pd.DataFrame
    prior: pd.DataFrame
    seasons: list[pd.DataFrame]          # parsed player tables: this season, last, the one before
    games: pd.DataFrame                  # regular-season games from the last months, oldest first
    conferences: dict[str, str]
    series: list[Series]
    champion: str


def read_cached(cache_dir: Path, name: str) -> str:
    """Return a cached page as text. Raises FileNotFoundError with a hint if it was never fetched."""
    path = cache_dir / f"{name}.html"
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; run pipeline/backtest/fetch_history.py first")
    return path.read_bytes().decode("utf-8", errors="replace")


def team_game_counts(games: pd.DataFrame) -> pd.Series:
    """Return how many finished games each team has in `games`."""
    return pd.concat([games["home_id"], games["away_id"]]).value_counts()


def load_season(end_year: int, cache_dir: Path = CACHE_DIR) -> SeasonData:
    """Assemble a season's data from the cached pages (no network)."""
    names, codes = team_maps(end_year)
    ratings = parse_ratings(read_cached(cache_dir, f"ratings_{end_year}"), names)
    prior_names, _ = team_maps(end_year - 1)
    prior = parse_ratings(read_cached(cache_dir, f"ratings_{end_year - 1}"), prior_names)
    seasons = []
    for year in (end_year, end_year - 1, end_year - 2):
        _, year_codes = team_maps(year)
        seasons.append(parse_players(read_cached(cache_dir, f"players_advanced_{year}"), year_codes))

    frames = []
    for month in season_months(end_year):
        path = cache_dir / f"games_{end_year}_{month}.html"
        if not path.exists():
            break
        frames.append(parse_regular_games(path.read_bytes().decode("utf-8", errors="replace"), names))
    games = (pd.concat(frames, ignore_index=True).sort_values("date", kind="stable").reset_index(drop=True)
             if frames else pd.DataFrame(columns=["date", "away_id", "away_pts", "home_id", "home_pts", "notes"]))

    page = read_cached(cache_dir, f"ratings_{end_year}")
    series = parse_playoff_series(read_cached(cache_dir, f"playoffs_{end_year}"), names)
    return SeasonData(end_year, ratings, prior, seasons, games, parse_conferences(page, names),
                      series, champion_of(series))
