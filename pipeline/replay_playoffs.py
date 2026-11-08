"""Replay a past postseason at chosen cut-off dates, offline (SPEC.md section 6.6).

Reads cached Basketball-Reference pages (no network): the season's ratings page and the April, May
and June results pages. Games after the cut-off date are dropped, the bracket is read with
`pipeline.playoffs`, and the state-conditioned simulation is run with each team's strength set to its
SRS, scaled like the live composite (a plain stand-in for it). Prints the top teams and checks the odds sum to 1.

Run:  python pipeline/replay_playoffs.py 2025 2025-04-13 2025-04-25 --pages data/raw/playoff_replay data/raw/backtest
(`--pages` lists folders to look in for games_<year>_<month>.html and ratings_<year>.html)
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config, model, playoffs, sim  # noqa: E402
from pipeline.parse import parse_ratings  # noqa: E402


def _find(folders: list[Path], name: str) -> str:
    for folder in folders:
        if (folder / name).exists():
            return (folder / name).read_text()
    raise FileNotFoundError(f"{name} not found in {[str(f) for f in folders]}")


def replay(year: int, cutoff: pd.Timestamp, folders: list[Path], runs: int = 20000) -> tuple[playoffs.Postseason, pd.Series]:
    """Return (postseason as of `cutoff`, title probabilities by team id) for the season ending in `year`."""
    ratings = parse_ratings(_find(folders, f"ratings_{year}.html"))
    pages = {}
    for month in playoffs.POSTSEASON_MONTHS:
        try:
            pages[month] = _find(folders, f"games_{year}_{month}.html")
        except FileNotFoundError:
            if month == "april":
                raise
    games = playoffs.parse_postseason_pages(pages)
    games = games[games["date"] <= cutoff]
    # Games after the cut-off do not exist yet; the regular season must be over for the state to apply.
    ids = list(ratings["team_id"])
    post = playoffs.read_postseason(games, ratings, ids)
    by_team = ratings.set_index("team_id")
    conferences = {t.id: t.conference for t in config.TEAMS}
    srs = by_team["srs"].astype(float)
    strength = (srs - srs.mean()) / srs.std() * config.MARGIN_PER_Z       # same scale as the live composite
    league = sim.league_from_frames(strength, pd.Series(82.0, index=ids), by_team["w"].astype(float), conferences)
    result = sim.simulate(league, runs=runs, seed=1, fmt="playin", state=post.state)
    return post, result.title_probs()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("year", type=int, help="season end year, e.g. 2025 for 2024-25")
    parser.add_argument("cutoffs", nargs="+", type=pd.Timestamp, help="cut-off dates (YYYY-MM-DD)")
    parser.add_argument("--pages", nargs="+", type=Path, required=True, help="folders holding the cached pages")
    args = parser.parse_args()
    for cutoff in args.cutoffs:
        post, probs = replay(args.year, cutoff, args.pages)
        top = ", ".join(f"{t} {p:.1%}" for t, p in probs.sort_values(ascending=False).head(5).items())
        alive = len(post.alive(list(probs.index)))
        print(f"{args.year} as of {cutoff.date()}: phase={post.phase} alive={alive} sum={probs.sum():.6f} "
              f"champion={post.champion} | {top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
