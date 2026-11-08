"""Download the historical Basketball-Reference pages the backtest needs (one-off, resumable).

Rules (CLAUDE.md "Data source rules"): only through pipeline/fetch.py's BRClient (one request
every 4 seconds), every page cached under data/raw/backtest/ and never fetched twice, and any
403/429/bot-check page stops everything at once with what is already cached kept.

Pages per season Y: ratings (also Y-1 for the prior), player advanced stats (Y, Y-1, Y-2),
the playoffs page (series results and the champion), and the last monthly results pages until
every team has 20 regular-season games (usually March and April).

Run:  python pipeline/backtest/fetch_history.py [--first 2001] [--last 2026] [--max-requests 200]
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pipeline import config  # noqa: E402
from pipeline.backtest import history  # noqa: E402
from pipeline.fetch import Blocked, BRClient, FetchError  # noqa: E402


MIN_TEAMS = 29   # the league had 29 teams from 1999-2000 to 2003-04, then 30


def planned_pages(first: int, last: int) -> list[tuple[str, str]]:
    """Return (path, cache_name) for every fixed page; the monthly pages are decided as we go."""
    pages = []
    for year in range(first - 1, last + 1):
        pages.append((f"/leagues/NBA_{year}.html", f"ratings_{year}"))
    for year in range(first - 2, last + 1):
        pages.append((f"/leagues/NBA_{year}_advanced.html", f"players_advanced_{year}"))
    for year in range(first, last + 1):
        pages.append((f"/playoffs/NBA_{year}.html", f"playoffs_{year}"))
    return pages


def fetch_month_pages(client: BRClient, year: int) -> None:
    """Fetch results pages newest month first until every team has 20 regular-season games."""
    names, _ = history.team_maps(year)
    frames = []
    for month in history.season_months(year):
        try:
            html = client.get_html(f"/leagues/NBA_{year}_games-{month}.html", f"games_{year}_{month}")
        except Blocked:
            raise
        except FetchError as err:
            print(f"  skipped {year} {month}: {err}")
            continue
        frames.append(history.parse_regular_games(html, names))
        counts = history.team_game_counts(pd.concat(frames, ignore_index=True))
        if len(counts) >= MIN_TEAMS and (counts >= config.FORM_GAMES).all():
            return
    print(f"  note {year}: not every team reached {config.FORM_GAMES} games in the months tried")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--first", type=int, default=history.FIRST_SEASON)
    parser.add_argument("--last", type=int, default=history.LAST_SEASON)
    parser.add_argument("--max-requests", type=int, default=200)
    args = parser.parse_args()

    client = BRClient(cache_dir=history.CACHE_DIR, max_requests=args.max_requests)
    try:
        for path, name in planned_pages(args.first, args.last):
            before = client.requests_made
            client.get_html(path, name)
            if client.requests_made > before:
                print(f"fetched {name} (request {client.requests_made})", flush=True)
        for year in range(args.first, args.last + 1):
            before = client.requests_made
            fetch_month_pages(client, year)
            print(f"games {year} done (requests so far {client.requests_made}, +{client.requests_made - before})", flush=True)
    except Blocked as err:
        print(f"STOPPED, the site blocked us: {err}. Cached pages are kept.", file=sys.stderr)
        return 2
    except FetchError as err:
        print(f"STOPPED: {err}. Cached pages are kept; run again to resume.", file=sys.stderr)
        return 1
    print(f"Done. {client.requests_made} requests this run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
