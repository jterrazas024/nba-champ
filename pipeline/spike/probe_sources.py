"""Phase 3 spike: can we politely reach Basketball-Reference, and does it have what the model needs?

For each page we record the HTTP status, size, time, how many tables parse, and whether the
columns the model needs are present. Nothing here feeds the live site; it answers "which source
works, from where" (SPEC.md section 5). All fetching goes through pipeline/fetch.py, which
enforces the politeness rules (pacing, daily cache, request cap, stop on any block).

Run:   python pipeline/spike/probe_sources.py            (makes up to 7 requests)
       python pipeline/spike/probe_sources.py --dry-run  (prints the plan, makes none)
"""
import argparse
import io
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# Allow running as a plain script (python pipeline/spike/probe_sources.py) as well as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pipeline import config  # noqa: E402
from pipeline.fetch import BRClient, Blocked, FetchError, looks_like_bot_check  # noqa: E402,F401

SEASON_END_YEAR = 2027  # the 2026-27 season is "NBA_2027" on the site


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


@dataclass
class Result:
    """Outcome for one probe (what gets printed in the summary table)."""

    name: str
    status: str = "not run"   # ok | missing columns | blocked | error | not run
    seconds: float = 0.0
    kib: float = 0.0
    tables: int = 0
    missing: tuple[str, ...] = ()
    note: str = ""


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


def run_probe(probe: Probe, client: BRClient) -> Result:
    """Fetch (or load from today's cache) one page through `client` and check its tables."""
    result = Result(probe.name)
    started = time.monotonic()
    html = client.get_html(probe.path, probe.name)
    result.seconds = time.monotonic() - started
    result.kib = len(html.encode()) / 1024
    tables = parse_tables(html)
    result.tables = len(tables)
    result.missing = missing_columns(tables, probe.expected)
    result.status = "ok" if tables and not result.missing else "missing columns"
    return result


def print_summary(results: list[Result]) -> None:
    """Print one line per probe, then a verdict."""
    print("\n" + "=" * 78)
    print(f"{'probe':<24}{'result':<17}{'sec':>5}{'KiB':>8}{'tables':>8}  notes")
    for r in results:
        notes = ("missing: " + ", ".join(r.missing)) if r.missing else r.note
        print(f"{r.name:<24}{r.status:<17}{r.seconds:>5.1f}{r.kib:>8.0f}{r.tables:>8}  {notes}")
    ok = sum(r.status == "ok" for r in results)
    print(f"\n{ok} of {len(results)} probes fully OK.")


def main() -> int:
    """Run the probes. Returns 0 if every probe is OK, 1 if some failed, 2 if we were blocked."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan and make no requests")
    args = parser.parse_args()

    if args.dry_run:
        for probe in PROBES:
            print(f"{probe.name:<24}{config.BASE_URL + probe.path}\n{'':<24}{probe.purpose}")
        print(f"\n{len(PROBES)} requests, one every {config.SECONDS_BETWEEN_REQUESTS:.0f}s (about "
              f"{len(PROBES) * config.SECONDS_BETWEEN_REQUESTS:.0f}s). Nothing was fetched.")
        return 0

    client = BRClient()
    results = [Result(p.name) for p in PROBES]
    exit_code = 0

    for index, probe in enumerate(PROBES):
        print(f"[{index + 1}/{len(PROBES)}] {probe.name} ...", flush=True)
        try:
            results[index] = run_probe(probe, client)
        except Blocked as err:
            results[index].status, results[index].note = "blocked", str(err)
            print(f"\nSTOPPING: {err}", file=sys.stderr)
            exit_code = 2
            break
        except (FetchError, OSError) as err:
            results[index].status, results[index].note = "error", str(err)
            print(f"  error: {err}", file=sys.stderr)
            exit_code = 1

    print_summary(results)
    print(f"requests made: {client.requests_made}")
    if exit_code == 0 and any(r.status != "ok" for r in results):
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
