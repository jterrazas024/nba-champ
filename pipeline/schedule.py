"""Read a Basketball-Reference monthly results page: every game, played or not, with its stage.

`parse_schedule` is the full reader; `pipeline.parse.parse_games` is a thin wrapper that keeps only
finished games. What the cached pages taught us about the layout (checked on every cached April
page, 2001-2026, plus May 2021 and August 2020):

* Up to the 2020 season (and 2020-21 pages of the old shape), playoff games follow a separator
  row whose cells all say "Playoffs" (`<tr class="thead"><th colspan=12>Playoffs</th></tr>`).
* Since the play-in tournament began (2020 bubble, 2020-21 on), the separator row is GONE. Play-in
  games carry the note "Play-In Game" and the playoff games that follow are unmarked and look just
  like regular-season rows. We tell them apart by date: every game after the last play-in game
  is a playoff game.
* A page with neither marker (for example a June page, or an April page read before the play-in)
  is "regular" unless the caller passes `playoffs_from`, or every game is in June (all playoffs).

Stages are therefore "regular", "playin" (notes contain "Play-In") and "playoffs".
"""
import pandas as pd

from pipeline.config import TEAM_BY_NAME
from pipeline.parse import _date, _numbers, _read_table

SCHEDULE_COLUMNS = ["date", "away_id", "home_id", "away_pts", "home_pts", "notes", "stage", "is_played"]


def _team_ids(names: pd.Series, team_names: dict) -> pd.Series:
    """Map team names to site ids; a name we do not know becomes None."""
    return names.map(lambda n: team_names[n].id if n in team_names else None)


def _stages(text: pd.DataFrame, dates: pd.Series, notes: pd.Series, playoffs_from) -> pd.Series:
    """Return each row's stage ("regular", "playin" or "playoffs") using the rules in the module docstring.

    `text` is the raw table (to find the separator row); rows with no date (separators) are
    handled here and dropped by the caller.
    """
    separator = text["Date"].astype(str).str.strip().eq("Playoffs")
    stage = pd.Series("regular", index=text.index)
    playin = notes.str.contains("Play-In", case=False, na=False) & dates.notna()
    stage[playin] = "playin"
    if separator.any():
        stage[separator.cummax()] = "playoffs"           # everything from the separator row on
    elif playin.any():
        stage[dates > dates[playin].max()] = "playoffs"   # play-in era: playoff games follow the last play-in game
    elif playoffs_from is not None:
        stage[dates >= pd.Timestamp(playoffs_from)] = "playoffs"
    elif dates.notna().any() and (dates.dropna().dt.month == 6).all():
        stage[:] = "playoffs"                             # a June page holds only the Finals
    return stage


def parse_schedule(html: str, team_names: dict = TEAM_BY_NAME, playoffs_from=None) -> pd.DataFrame:
    """Every game on a monthly results page's `schedule` table, oldest first.

    Returns date, away_id, home_id, away_pts, home_pts (NaN until played), notes, stage and
    is_played. Separator rows ("Playoffs") are not games and are not returned. A team name we do
    not know gives an id of None. `playoffs_from` (a date) marks later games as playoffs on pages
    that carry no marker of their own.
    """
    text, _ = _read_table(html, "schedule", {"Date", "Visitor/Neutral", "PTS", "Home/Neutral", "PTS.1"})
    dates = _date(text["Date"])
    notes = text["Notes"].fillna("").astype(str) if "Notes" in text.columns else pd.Series("", index=text.index)
    out = pd.DataFrame({
        "date": dates,
        "away_id": _team_ids(text["Visitor/Neutral"], team_names),
        "home_id": _team_ids(text["Home/Neutral"], team_names),
        "away_pts": _numbers(text["PTS"]),
        "home_pts": _numbers(text["PTS.1"]),
        "notes": notes,
        "stage": _stages(text, dates, notes, playoffs_from),
    })
    out["is_played"] = out["away_pts"].notna() & out["home_pts"].notna()
    out = out[out["date"].notna()]
    return out.sort_values("date", kind="stable").reset_index(drop=True)[SCHEDULE_COLUMNS]
