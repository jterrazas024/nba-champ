"""Inputs for the playoff simulation, shared by the daily build and the offline calibration (SPEC.md section 6.4).

The simulation needs the games each team still has to play. Basketball-Reference's monthly results
pages list every game of the month, played or not, so the remaining schedule is "the unplayed
regular-season games on the pages from the current month through April".
"""
from typing import Iterable

import pandas as pd

from pipeline import config
from pipeline.schedule import parse_schedule

LAST_SCHEDULE_MONTH_INDEX = 6        # SEASON_MONTHS[6] is april: the last regular-season month
MAX_GAMES_OFF = 1                    # a team's remaining games may differ from 82 - played by this much (the NBA Cup final does not count)


def unplayed_regular_games(pages: Iterable[str]) -> pd.DataFrame:
    """Return the unplayed regular-season games on the given monthly results pages (HTML text).

    Columns home_id and away_id. The NBA Cup final is dropped: it is played in December but does
    not count toward the 82 games.
    """
    frames = []
    for html in pages:
        sched = parse_schedule(html)
        keep = ~sched["is_played"] & (sched["stage"] == "regular") & sched["home_id"].notna() & sched["away_id"].notna()
        cup_final = sched["notes"].str.contains("Cup", case=False) & sched["notes"].str.contains("Final", case=False) \
            & ~sched["notes"].str.contains("Semi|Quarter", case=False)
        frames.append(sched[keep & ~cup_final][["home_id", "away_id"]])
    if not frames:
        return pd.DataFrame({"home_id": [], "away_id": []})
    return pd.concat(frames, ignore_index=True)


def fetch_remaining_schedule(client, end_year: int, month_index: int) -> pd.DataFrame:
    """Fetch the monthly pages from SEASON_MONTHS[month_index] through April and return the unplayed games.

    `client` is the fetch client; the cache name matches fetch_recent_games, so the current month's
    page is shared with the recent-form fetch and costs no extra request. Any fetch or parse
    failure propagates to the caller (the build treats it as "no simulation today").
    """
    from pipeline.build_odds import SEASON_MONTHS     # imported here: build_odds imports this module
    pages = [client.get_html(f"/leagues/NBA_{end_year}_games-{month}.html", f"games_{end_year}_{month}")
             for month in SEASON_MONTHS[month_index:LAST_SCHEDULE_MONTH_INDEX + 1]]
    return unplayed_regular_games(pages)


def check_remaining(remaining: pd.DataFrame, games_played: pd.Series) -> None:
    """Raise ValueError unless every team's remaining games match 82 minus games played (within MAX_GAMES_OFF).

    A mismatch means a month page was missing, cut short, or unreadable.
    """
    counts = pd.concat([remaining["home_id"], remaining["away_id"]]).value_counts().reindex(games_played.index).fillna(0)
    off = (counts - (config.SEASON_GAMES - games_played)).abs()
    if (off > MAX_GAMES_OFF).any():
        bad = ", ".join(f"{t} ({int(counts[t])} left, expected {int(config.SEASON_GAMES - games_played[t])})" for t in off.index[off > MAX_GAMES_OFF][:5])
        raise ValueError(f"the remaining schedule does not match games played for: {bad}")
