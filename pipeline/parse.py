"""Turn Basketball-Reference pages into clean tables (pandas DataFrames).

Each function takes a page's HTML text and returns a DataFrame with plain column names.
They only read and tidy: no model logic lives here. They raise ParseError when a page is not
shaped the way we expect (a missing table or column), so a layout change fails loudly instead
of quietly producing wrong odds. Whether a result is *complete* (30 teams, enough players) is
checked later, in the build step.
"""
import io
import re
from datetime import date, timedelta

import pandas as pd

from pipeline import config
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


def parse_ratings(html: str, team_names: dict = TEAM_BY_NAME) -> pd.DataFrame:
    """Team ratings from the season page's `advanced-team` table.

    `team_names` maps a team name on the page to a Team (the default is today's names; the backtest
    passes a season-specific map for franchises that moved or were renamed).

    Returns one row per team: team_id, name, w, l, games, mov, sos, srs, ortg, drtg, nrtg.
    The "League Average" row is dropped and a trailing "*" on a team name is removed.
    """
    text, _ = _read_table(html, "advanced-team", {"Team", "W", "L", "MOV", "SOS", "SRS", "ORtg", "DRtg", "NRtg"})
    names = text["Team"].astype("string").str.rstrip("*").str.strip()
    keep = names.notna() & (names != "League Average")
    names = names[keep]
    unknown = sorted(set(names) - set(team_names))
    if unknown:
        raise ParseError(f"ratings page has team names we do not know: {unknown}")

    rows = text[keep]
    out = pd.DataFrame({
        "team_id": names.map(lambda n: team_names[n].id),
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


def parse_players(html: str, team_codes: dict = TEAM_BY_BR_CODE) -> pd.DataFrame:
    """Player advanced stats from the season's `advanced` table.

    Returns one row per table row: player_id, player, team_br, team_id, is_total, g, mp, bpm.
    A player traded mid-season has a total row (team_br like "2TM", is_total True, team_id None)
    followed by one row per team he played for, in the order he played for them. Rows are kept
    in page order. The "League Average" row and any repeated header rows are dropped.
    `team_codes` maps the site's team codes to Team (default: today's codes; the backtest passes
    season-specific ones).
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
        "team_id": team_br.map(lambda c: team_codes[c].id if c in team_codes else None),
        "is_total": team_br.str.fullmatch(r"\dTM").fillna(False).astype(bool),
        "g": _numbers(text["G"]),
        "mp": _numbers(text["MP"]),
        "bpm": _numbers(text["BPM"]),
    })
    return out.reset_index(drop=True)


_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
           "nov": 11, "dec": 12}
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_NUMBER = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_RETURN_WORDS = r"return|back|ready|available|cleared|until|re-?evaluat|reassess|timetable"
_SEASON_ENDING = (r"out for (?:the )?(?:rest of the |remainder of the )?(?:\d{4}\s*-\s*\d{2,4} )?season|season-ending|"
                  r"(?:miss|misses|sidelined for|out for) (?:the )?(?:rest|remainder) of the|"
                  r"miss(?:es)? the (?:entire |\d{4}\s*-\s*\d{2,4} )+season|miss the season")


def _month_number(text: str) -> int:
    return _MONTHS[text[:3].lower()]


def _next_date(month: int, day: int, after: date) -> date:
    """Return the first date with this month and day on or after `after`."""
    when = date(after.year, month, min(day, 28 if month == 2 else 30 if month in (4, 6, 9, 11) else 31))
    return when if when >= after else when.replace(year=after.year + 1)


def _count(token: str) -> float:
    return float(_WORDS.get(token.lower(), token) if not token.isdigit() else token)


def parse_return_info(description: str, update: date | None, status: str) -> tuple[date | None, str, bool]:
    """Read an expected return from an injury entry (SPEC.md section 6.7).

    Returns (expected_return, category, estimated). `category` is one of "season" (out for the season; no
    date), "dated" (a date or a month in the text), "duration" (e.g. "re-evaluated in two weeks", "3-4 months"),
    "week_to_week", "indefinite", "single_game", "day_to_day", "long", "unknown". For the last six
    the date is the update date plus a fixed prior (config.INJURY_PRIOR_DAYS) and `estimated` is True.
    Without an update date no date can be computed (expected_return is None, but "season" still is recognized).
    """
    text = " ".join(description.lower().split())
    if status == "Out For Season" or re.search(_SEASON_ENDING, text):
        return None, "season", False
    if update is None:
        return None, "unknown", True

    # 1. An explicit date after a return word: "return on Jan 15", "out until Feb. 3".
    for match in re.finditer(rf"(?:{_RETURN_WORDS})\D{{0,40}}?\b{_MONTH}\s+(\d{{1,2}})\b", text):
        found = _next_date(_month_number(match.group(1)), int(match.group(2)), update)
        if found > update and (found - update).days < 330:   # a date just before the update is the injury date, not the return
            return found, "dated", False

    # 2. A month or a range of months: "return sometime between January and February", "back in late March".
    month_match = re.search(rf"\b(?:between|in|during|by|until|around|before)\s+(early |late |mid-?)?{_MONTH}"
                            rf"(?:\s+(?:and|to|or|through|-)\s+(early |late |mid-?)?{_MONTH})?\b(?!\s*\d)", text)
    if month_match and re.search(_RETURN_WORDS, text):
        first_mod, first, second_mod, second = month_match.groups()
        start = _next_date(_month_number(first), {"early ": 7, "late ": 24}.get(first_mod or "", 15), update)
        end = _next_date(_month_number(second), {"early ": 7, "late ": 24}.get(second_mod or "", 15), start) if second else start
        found = start + (end - start) / 2
        if found > update:
            return found, "dated", False

    # 3. A length of time: "re-evaluated in approximately six weeks", "at least 3-4 weeks", "up to two months".
    duration = re.search(rf"(at least|up to|approximately|about|around|roughly|another|nearly|next|within|over)?\s*"
                         rf"{_NUMBER}(?:\s*(?:-to-|-|\u2013|to)\s*{_NUMBER})?[\s-]*(day|week|month)s?\b", text)
    if duration:
        qualifier, low, high, unit = duration.groups()
        count = (_count(low) + _count(high or low)) / 2
        days = count * {"day": 1, "week": 7, "month": 30}[unit]
        if qualifier == "at least":
            days *= config.INJURY_AT_LEAST_STRETCH
        if re.search(r"re-?evaluat|reassess", text):
            days += config.INJURY_REEVAL_EXTRA_DAYS
        return update + timedelta(days=round(days)), "duration", False
    if re.search(r"several months", text):
        return update + timedelta(days=config.SEVERAL_MONTHS_DAYS), "duration", True
    if re.search(r"few weeks|couple of weeks", text):
        return update + timedelta(days=config.FEW_WEEKS_DAYS), "duration", True

    # 4. Phrases with no number, then the status itself.
    for category, pattern in (("week_to_week", r"week-to-week|week to week"),
                              ("indefinite", r"indefinite|no timetable|without a timetable|no (?:set |definitive )?timeline|"
                                             r"not yet been cleared|no updates? on a timetable"),
                              ("long", r"extended (?:period|time)|month-to-month|several months|"
                                      r"(?:return|back) (?:this season|during the \d{4}\s*-\s*\d{2,4} season)"),
                              ("single_game", r"(?:out|miss\w*|sidelined) for (?:\w+day|tonight)['\u2019]?s? ?(?:\([^)]*\) )?"
                                              r"(?:game|contest)|is out for (?:\w+day|tonight)")):
        if re.search(pattern, text):
            return update + timedelta(days=config.INJURY_PRIOR_DAYS[category]), category, True
    category = "day_to_day" if status == "Day To Day" else "unknown"
    return update + timedelta(days=config.INJURY_PRIOR_DAYS[category]), category, True


def parse_injuries(html: str) -> pd.DataFrame:
    """Current injuries from the `injuries` table.

    Returns one row per entry: player_id, player, team_id, update (a date), status, description, and
    the return reading from `parse_return_info`: expected_return (a date or None), return_category,
    return_estimated (True when the date is a fixed prior, not something the report said).
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
    readings = [parse_return_info(d, None if pd.isna(u) else u.date(), s)
                for d, u, s in zip(out["description"], out["update"], out["status"])]
    out["expected_return"] = pd.Series([r[0] for r in readings], dtype=object, index=out.index)
    out["return_category"] = [r[1] for r in readings]
    out["return_estimated"] = [r[2] for r in readings]
    return out.reset_index(drop=True)


def parse_games(html: str, team_names: dict = TEAM_BY_NAME) -> pd.DataFrame:
    """Finished games from a monthly results page's `schedule` table.

    A thin wrapper over `pipeline.schedule.parse_schedule`. Returns one row per completed game,
    oldest first: date, away_id, away_pts, home_id, home_pts, notes. Games with no score yet
    (future or postponed), separator rows such as "Playoffs", and games involving a team we do not
    know are dropped. It does NOT filter by stage: finished play-in and playoff games on the page
    are included (the backtest cuts a page at its "Playoffs" row itself, and play-in-era pages have
    no such row; see the schedule module docstring).
    """
    from pipeline.schedule import parse_schedule   # imported here: schedule.py imports helpers from this module
    games = parse_schedule(html, team_names)
    done = games[games["is_played"]].dropna(subset=["away_id", "home_id"]).copy()
    done[["away_pts", "home_pts"]] = done[["away_pts", "home_pts"]].astype(int)
    return done[["date", "away_id", "away_pts", "home_id", "home_pts", "notes"]].reset_index(drop=True)
