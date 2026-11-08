"""Phase 3 spike: can we politely reach Basketball-Reference, and does it have what the model needs?

For each page we record the HTTP status, size, time, how many tables parse, and whether the
columns the model needs are present. Nothing here feeds the live site; it answers "which source
works, from where" (SPEC.md section 5).

Politeness rules (CLAUDE.md "Data source rules"):
  - at most one request every 4 seconds (the site's robots.txt asks for 3; its hard limit is 20/min)
  - every page is cached under data/raw/<date>/ and never fetched twice in a day
  - timeouts and retries with backoff on network/server errors
  - a 403 or 429 (or a bot-check page) STOPS the whole run: never retry a block

Run:   python pipeline/spike/probe_sources.py            (makes up to 7 requests)
       python pipeline/spike/probe_sources.py --dry-run  (prints the plan, makes none)
"""
import argparse
import io
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://www.basketball-reference.com"
USER_AGENT = "nba-champ-odds-project (hobby project, one daily run; github.com/jterrazas024/nba-champ)"
SECONDS_BETWEEN_REQUESTS = 4.0
TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 3
BLOCK_STATUSES = {403, 429}
SEASON_END_YEAR = 2027  # the 2026-27 season is "NBA_2027" on the site
CACHE_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "raw"


@dataclass(frozen=True)
class Probe:
    """One page to test. `expected` lists columns the model needs ("A|B" means either name)."""

    name: str
    path: str
    purpose: str
    expected: tuple[str, ...]


PROBES = [
    Probe("team_ratings", f"/leagues/NBA_{SEASON_END_YEAR}.html",
          "net rating, SRS, schedule strength (SPEC 6.1: net rating, 38%)",
          ("ORtg", "DRtg", "NRtg", "SRS", "SOS")),
    Probe("players_advanced_now", f"/leagues/NBA_{SEASON_END_YEAR}_advanced.html",
          "BPM, minutes, games played (star power, rotation availability)",
          ("Player", "Team|Tm", "G", "MP", "BPM")),
    Probe("players_advanced_prev1", f"/leagues/NBA_{SEASON_END_YEAR - 1}_advanced.html",
          "last season: preseason prior and 3-year availability",
          ("Player", "Team|Tm", "G", "MP", "BPM")),
    Probe("players_advanced_prev2", f"/leagues/NBA_{SEASON_END_YEAR - 2}_advanced.html",
          "two seasons ago: 3-year availability",
          ("Player", "Team|Tm", "G", "MP", "BPM")),
    Probe("injuries", "/friv/injuries.fcgi",
          "current injury status (star availability)",
          ("Player", "Team", "Update")),
    Probe("games_october", f"/leagues/NBA_{SEASON_END_YEAR}_games-october.html",
          "game results for recent form (last 20 games)",
          ("Date", "Visitor/Neutral", "Home/Neutral", "PTS")),
    Probe("games_november", f"/leagues/NBA_{SEASON_END_YEAR}_games-november.html",
          "game results for recent form (last 20 games)",
          ("Date", "Visitor/Neutral", "Home/Neutral", "PTS")),
]


class Blocked(Exception):
    """The site refused us (403/429 or a bot-check page). We stop and do not retry."""


@dataclass
class Result:
    """Outcome for one probe (what gets printed in the summary table)."""

    name: str
    status: str = "not run"   # ok | missing columns | blocked | error | not run
    http: str = "-"
    seconds: float = 0.0
    kib: float = 0.0
    tables: int = 0
    missing: tuple[str, ...] = ()
    note: str = ""


def looks_like_bot_check(html: str) -> bool:
    """Return True if the page is a Cloudflare-style "verify you are human" page."""
    return "Just a moment..." in html or "cf-chl" in html or "challenge-platform" in html


def parse_tables(html: str) -> list[pd.DataFrame]:
    """Return every HTML table on the page as a DataFrame with flat column names.

    Basketball-Reference hides some tables inside HTML comments, so comment markers are
    removed first. Returns [] if the page has no tables.
    """
    uncommented = html.replace("<!--", "").replace("-->", "")
    try:
        tables = pd.read_html(io.StringIO(uncommented), flavor="lxml")  # lxml only: no fallback parser
    except ValueError:  # pandas raises this when there are no tables
        return []
    for table in tables:
        if isinstance(table.columns, pd.MultiIndex):  # keep the bottom header row only
            table.columns = [str(col[-1]) for col in table.columns]
        else:
            table.columns = [str(col) for col in table.columns]
    return tables


def missing_columns(tables: list[pd.DataFrame], expected: tuple[str, ...]) -> tuple[str, ...]:
    """Return the expected columns that no table on the page has ("A|B" is satisfied by either)."""
    present = {col for table in tables for col in table.columns}
    return tuple(want for want in expected if not any(option in present for option in want.split("|")))


def fetch(session: requests.Session, url: str) -> tuple[int, str]:
    """GET `url` with a timeout and retries; return (status code, page text).

    Retries only network errors and 5xx responses, waiting 2s then 4s.
    Raises Blocked on 403/429 or a bot-check page, and RuntimeError if all attempts fail.
    """
    last_error = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(url, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as err:
            last_error = f"{type(err).__name__}: {err}"
        else:
            if response.status_code in BLOCK_STATUSES or looks_like_bot_check(response.text):
                raise Blocked(f"HTTP {response.status_code} from {url}")
            if response.status_code < 500:
                return response.status_code, response.text
            last_error = f"HTTP {response.status_code}"
        if attempt < MAX_ATTEMPTS:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"gave up on {url} after {MAX_ATTEMPTS} attempts ({last_error})")


def run_probe(probe: Probe, session: requests.Session, cache_dir: Path, last_request: list[float]) -> Result:
    """Fetch (or load from today's cache) one page and check its tables. `last_request` holds the
    time of the previous network request, so we can keep the spacing."""
    result = Result(probe.name)
    cache_file = cache_dir / f"{probe.name}.html"
    if cache_file.exists():
        html, result.http, result.note = cache_file.read_text(), "cached", "from today's cache"
    else:
        wait = SECONDS_BETWEEN_REQUESTS - (time.monotonic() - last_request[0])
        if wait > 0:
            time.sleep(wait)
        started = time.monotonic()
        last_request[0] = started
        status, html = fetch(session, BASE_URL + probe.path)
        result.seconds = time.monotonic() - started
        result.http = str(status)
        if status != 200:
            result.status, result.note = "error", f"unexpected HTTP {status}"
            return result
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(html)

    result.kib = len(html.encode()) / 1024
    tables = parse_tables(html)
    result.tables = len(tables)
    result.missing = missing_columns(tables, probe.expected)
    result.status = "ok" if tables and not result.missing else "missing columns"
    return result


def print_summary(results: list[Result]) -> None:
    """Print one line per probe, then a verdict."""
    print("\n" + "=" * 78)
    print(f"{'probe':<24}{'result':<17}{'http':<8}{'sec':>5}{'KiB':>8}{'tables':>8}  notes")
    for r in results:
        notes = ("missing: " + ", ".join(r.missing)) if r.missing else r.note
        print(f"{r.name:<24}{r.status:<17}{r.http:<8}{r.seconds:>5.1f}{r.kib:>8.0f}{r.tables:>8}  {notes}")
    ok = sum(r.status == "ok" for r in results)
    print(f"\n{ok} of {len(results)} probes fully OK.")


def main() -> int:
    """Run the probes. Returns 0 if every probe is OK, 1 if some failed, 2 if we were blocked."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan and make no requests")
    args = parser.parse_args()

    if args.dry_run:
        for probe in PROBES:
            print(f"{probe.name:<24}{BASE_URL + probe.path}\n{'':<24}{probe.purpose}")
        print(f"\n{len(PROBES)} requests, one every {SECONDS_BETWEEN_REQUESTS:.0f}s (about "
              f"{len(PROBES) * SECONDS_BETWEEN_REQUESTS:.0f}s). Nothing was fetched.")
        return 0

    cache_dir = CACHE_ROOT / date.today().isoformat()
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    results = [Result(p.name) for p in PROBES]
    last_request = [0.0]
    exit_code = 0

    for index, probe in enumerate(PROBES):
        print(f"[{index + 1}/{len(PROBES)}] {probe.name} ...", flush=True)
        try:
            results[index] = run_probe(probe, session, cache_dir, last_request)
        except Blocked as err:
            results[index].status, results[index].note = "blocked", str(err)
            print(f"\nSTOPPING: the site blocked us ({err}). Not retrying; not sending more requests.", file=sys.stderr)
            exit_code = 2
            break
        except (RuntimeError, OSError) as err:
            results[index].status, results[index].note = "error", str(err)
            print(f"  error: {err}", file=sys.stderr)
            exit_code = 1

    print_summary(results)
    if exit_code == 0 and any(r.status != "ok" for r in results):
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
