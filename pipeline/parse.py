"""Turn Basketball-Reference pages into clean tables (pandas DataFrames).

Each function takes a page's HTML text and returns a DataFrame with plain column names.
They only read and tidy: no model logic lives here. They raise ParseError when a page is not
shaped the way we expect (a missing table or column), so a layout change fails loudly instead
of quietly producing wrong odds. Whether a result is *complete* (30 teams, enough players) is
checked later, in the build step.
"""
import io
import re

import pandas as pd

from pipeline.config import TEAM_BY_BR_CODE, TEAM_BY_NAME


class ParseError(Exception):
    """The page does not have the table or columns we expect."""


def _read_table(html: str, table_id: str, needed: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (text, links) for the table with this HTML id.

    `text` holds each cell's text; `links` holds each cell's link target (or None), which is how
    we get stable player ids. Raises ParseError if the table or any needed column is missing.
    Some tables are hidden in HTML comments on the real site, so comment markers are removed first.
    """
    uncommented = html.replace("<!--", "").replace("-->", "")
    try:
        raw = pd.read_html(io.StringIO(uncommented), attrs={"id": table_id}, flavor="lxml",
                           extract_links="body")[0]
    except ValueError as err:
        raise ParseError(f"table '{table_id}' not found on the page") from err

    columns = [str(c[-1]) if isinstance(c, tuple) else str(c) for c in raw.columns]  # bottom header row
    raw.columns = columns
    missing = needed - set(columns)
    if missing:
        raise ParseError(f"table '{table_id}' is missing columns: {sorted(missing)}")
    text = raw.map(lambda cell: cell[0] if isinstance(cell, tuple) else cell)
    links = raw.map(lambda cell: cell[1] if isinstance(cell, tuple) else None)
    return text, links


def _numbers(series: pd.Series) -> pd.Series:
    """Convert a column to numbers; anything that is not a number becomes NaN."""
    return pd.to_numeric(series, errors="coerce")


def _player_id(link: str | None) -> str | None:
    """Return the site's player id from a link like "/players/t/thompam01.html" ("thompam01")."""
    match = re.search(r"/players/\w/(\w+)\.html", link or "")
    return match.group(1) if match else None


def _date(series: pd.Series) -> pd.Series:
    """Parse dates like "Fri, Nov 6, 2026"; unparseable values become NaT."""
    return pd.to_datetime(series, format="%a, %b %d, %Y", errors="coerce")


def parse_ratings(html: str) -> pd.DataFrame:
    """Team ratings from the season page's `advanced-team` table.

    Returns one row per team: team_id, name, w, l, games, mov, sos, srs, ortg, drtg, nrtg.
    The "League Average" row is dropped and a trailing "*" on a team name is removed.
    """
    text, _ = _read_table(html, "advanced-team", {"Team", "W", "L", "MOV", "SOS", "SRS", "ORtg", "DRtg", "NRtg"})
    names = text["Team"].astype("string").str.rstrip("*").str.strip()
    keep = names.notna() & (names != "League Average")
    names = names[keep]
    unknown = sorted(set(names) - set(TEAM_BY_NAME))
    if unknown:
        raise ParseError(f"ratings page has team names we do not know: {unknown}")

    rows = text[keep]
    out = pd.DataFrame({
        "team_id": names.map(lambda n: TEAM_BY_NAME[n].id),
        "name": names,
        "w": _numbers(rows["W"]),
        "l": _numbers(rows["L"]),
        "mov": _numbers(rows["MOV"]),
        "sos": _numbers(rows["SOS"]),
        "srs": _numbers(rows["SRS"]),
        "ortg": _numbers(rows["ORtg"]),
        "drtg": _numbers(rows["DRtg"]),
        "nrtg": _numbers(rows["NRtg"]),
    })
    out.insert(4, "games", out["w"] + out["l"])
    return out.reset_index(drop=True)


def parse_players(html: str) -> pd.DataFrame:
    """Player advanced stats from the season's `advanced` table.

    Returns one row per table row: player_id, player, team_br, team_id, is_total, g, mp, bpm.
    A player traded mid-season has a total row (team_br like "2TM", is_total True, team_id None)
    followed by one row per team he played for, in the order he played for them. Rows are kept
    in page order. The "League Average" row and any repeated header rows are dropped.
    """
    text, links = _read_table(html, "advanced", {"Player", "Team", "G", "MP", "BPM"})
    keep = text["Player"].notna() & ~text["Player"].isin(["League Average", "Player"])
    text, links = text[keep], links[keep]

    ids = links["Player"].map(_player_id)
    if ids.isna().any():
        bad = text.loc[ids.isna(), "Player"].tolist()
        raise ParseError(f"no player id found (page layout changed?) for: {bad[:5]}")

    team_br = text["Team"].astype("string")
    out = pd.DataFrame({
        "player_id": ids,
        "player": text["Player"],
        "team_br": team_br,
        "team_id": team_br.map(lambda c: TEAM_BY_BR_CODE[c].id if c in TEAM_BY_BR_CODE else None),
        "is_total": team_br.str.fullmatch(r"\dTM").fillna(False).astype(bool),
        "g": _numbers(text["G"]),
        "mp": _numbers(text["MP"]),
        "bpm": _numbers(text["BPM"]),
    })
    return out.reset_index(drop=True)


def parse_injuries(html: str) -> pd.DataFrame:
    """Current injuries from the `injuries` table.

    Returns one row per entry: player_id, player, team_id, update (a date), status, description.
    `status` is the text before the first " (" or " - ", e.g. "Out", "Out For Season", "Day To Day".
    """
    text, links = _read_table(html, "injuries", {"Player", "Team", "Update", "Description"})
    text = text[text["Player"].notna()]
    links = links.loc[text.index]
    description = text["Description"].fillna("").astype(str)
    out = pd.DataFrame({
        "player_id": links["Player"].map(_player_id),
        "player": text["Player"],
        "team_id": text["Team"].map(lambda n: TEAM_BY_NAME[n].id if n in TEAM_BY_NAME else None),
        "update": _date(text["Update"]),
        "status": description.map(lambda d: re.split(r" \(| - ", d, maxsplit=1)[0].strip()),
        "description": description,
    })
    return out.reset_index(drop=True)


def parse_games(html: str) -> pd.DataFrame:
    """Finished games from a monthly results page's `schedule` table.

    Returns one row per completed game, oldest first: date, away_id, away_pts, home_id, home_pts, notes.
    Games with no score yet (future or postponed), separator rows such as "Playoffs", and games
    involving a team we do not know are dropped.
    """
    text, _ = _read_table(html, "schedule", {"Date", "Visitor/Neutral", "PTS", "Home/Neutral", "PTS.1"})
    out = pd.DataFrame({
        "date": _date(text["Date"]),
        "away_id": text["Visitor/Neutral"].map(lambda n: TEAM_BY_NAME[n].id if n in TEAM_BY_NAME else None),
        "away_pts": _numbers(text["PTS"]),
        "home_id": text["Home/Neutral"].map(lambda n: TEAM_BY_NAME[n].id if n in TEAM_BY_NAME else None),
        "home_pts": _numbers(text["PTS.1"]),
        "notes": text["Notes"].fillna("").astype(str) if "Notes" in text.columns else "",
    })
    done = out.dropna(subset=["date", "away_id", "away_pts", "home_id", "home_pts"]).copy()
    done[["away_pts", "home_pts"]] = done[["away_pts", "home_pts"]].astype(int)
    return done.sort_values("date", kind="stable").reset_index(drop=True)
