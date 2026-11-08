"""Inputs for the playoff simulation, shared by the daily build and the offline calibration (SPEC.md section 6.4).

The simulation needs the games each team still has to play. Basketball-Reference's monthly results
pages list every game of the month, played or not, so the remaining schedule is "the unplayed
regular-season games on the pages from the current month through April".
"""
from typing import Iterable

import numpy as np
import pandas as pd

from pipeline import config
from pipeline.schedule import parse_schedule

LAST_SCHEDULE_MONTH_INDEX = 6        # SEASON_MONTHS[6] is april: the last regular-season month
MAX_SURPLUS = 1                      # a team may have this many MORE scheduled games than 82 - played (the NBA Cup final does not count)
MAX_SHORTFALL = 4                    # ...or this many fewer: some games are not on the pages yet (non-Cup-knockout teams get 2 added later)


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


def fill_schedule(known: pd.DataFrame, need: pd.Series, rng: np.random.Generator) -> pd.DataFrame:
    """Return `known` (home_id, away_id) plus made-up games so every team has `need[team]` more games in total.

    The made-up games pair random teams (no team plays itself; home and away are a coin flip). It
    stands in for months of the schedule we do not have cached, so opponents are an average draw
    and home/away is not balanced per team.
    """
    have = pd.concat([known["home_id"], known["away_id"]]).value_counts().reindex(need.index).fillna(0)
    extra = (need - have).astype(int)
    if (extra < 0).any() or extra.sum() % 2:
        raise ValueError("the known games already exceed a team's games left, or the leftover total is odd")
    stubs = np.repeat(np.array(extra.index), extra.to_numpy())
    rng.shuffle(stubs)
    for _ in range(10_000):                                  # repair pairs where a team meets itself
        pairs = stubs.reshape(-1, 2)
        clash = np.where(pairs[:, 0] == pairs[:, 1])[0]
        if not len(clash):
            break
        for i in clash:
            j = int(rng.integers(len(stubs)))
            stubs[2 * i], stubs[j] = stubs[j], stubs[2 * i]
    pairs = stubs.reshape(-1, 2)
    flip = rng.random(len(pairs)) < 0.5
    made_up = pd.DataFrame({"home_id": np.where(flip, pairs[:, 0], pairs[:, 1]),
                            "away_id": np.where(flip, pairs[:, 1], pairs[:, 0])})
    return pd.concat([known[["home_id", "away_id"]], made_up], ignore_index=True)


def complete_remaining(remaining: pd.DataFrame, games_played: pd.Series, rng: np.random.Generator) -> pd.DataFrame:
    """Return `remaining` plus made-up games so every team has 82 - played games left (within a few games).

    The published schedule can be a game or two short for some teams (games added once the NBA Cup
    knockout rounds are set), so the few missing games are filled with random opponents
    (fill_schedule). Raises ValueError if any team is more than MAX_SHORTFALL short or MAX_SURPLUS
    over, which means a monthly page was missing, cut short, or counted twice.
    """
    have = pd.concat([remaining["home_id"], remaining["away_id"]]).value_counts().reindex(games_played.index).fillna(0)
    gap = (config.SEASON_GAMES - games_played) - have            # positive = games missing from the pages
    off = gap[(gap > MAX_SHORTFALL) | (gap < -MAX_SURPLUS)]
    if len(off):
        bad = ", ".join(f"{t} ({int(have[t])} found, expected {int(config.SEASON_GAMES - games_played[t])})" for t in off.index[:5])
        raise ValueError(f"the remaining schedule does not match games played for: {bad}")
    need = have + gap.clip(lower=0)
    if int((need - have).sum()) % 2:                            # games come in pairs: let the biggest shortfall be one less
        need[gap.idxmax()] -= 1
    return fill_schedule(remaining, need, rng)
