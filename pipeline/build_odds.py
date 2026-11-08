"""Build data/odds.json from Basketball-Reference (SPEC.md sections 4, 6 and 7).

Fetches the pages (politely, through pipeline/fetch.py), computes the five model inputs
(pipeline/features.py), turns them into title probabilities (pipeline/model.py), and writes
odds.json in the shape the site reads.

Safety rules (CLAUDE.md "Data source rules"):
  - nothing is written unless the new file passes validation and the sanity checks
  - the file is written to a temporary name and swapped in, so a crash cannot leave half a file
  - any failure exits non-zero and leaves the previous odds.json exactly as it was
  - before the season starts, and after the regular season ends, the run does nothing (exit 0)

Run:  python pipeline/build_odds.py [--out PATH] [--today YYYY-MM-DD]
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

# Allow running as a plain script (python pipeline/build_odds.py) as well as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config, features, model  # noqa: E402
from pipeline.fetch import BRClient, Blocked, FetchError  # noqa: E402
from pipeline.parse import ParseError, parse_games, parse_injuries, parse_players, parse_ratings  # noqa: E402
from pipeline.validate import validate_odds  # noqa: E402

ODDS_PATH = Path(__file__).resolve().parent.parent / "data" / "odds.json"
MODEL_VERSION = "v0"
MIN_ROSTER_PLAYERS = 8          # sanity check: a team with fewer parsed players means a broken page
MAX_DAILY_MOVE = 0.15           # sanity check: no team's title probability moves more than this in one update
EARLY_SEASON_NOTE_GAMES = 10    # below this many games, add a note that net rating leans on last season
MAX_NOTES = 4
SEASON_MONTHS = ["october", "november", "december", "january", "february", "march", "april"]
MONTH_INDEX = {10: 0, 11: 1, 12: 2, 1: 3, 2: 4, 3: 5, 4: 6}

INPUT_LABELS = {
    "net_rating": "net rating", "star_power": "star power", "recent_form": "recent form",
    "star_availability": "star availability", "core_net_rating": "core net rating",
    "rotation_availability": "rotation availability", "clutch": "clutch",
}


class BuildError(Exception):
    """The data we fetched is not good enough to publish (a sanity check failed)."""


@dataclass
class BuildResult:
    """What a build produced: the odds (None if the run intentionally did nothing) and a message."""

    odds: dict | None
    message: str


# ---------- fetching ----------

def fetch_recent_games(client: BRClient, end_year: int, today: date, team_ids: list[str]) -> pd.DataFrame:
    """Fetch monthly results pages, newest month first, until every team has 20 finished games.

    Stops at October at the latest. Returns all finished games found (oldest first).
    """
    start = MONTH_INDEX.get(today.month, len(SEASON_MONTHS) - 1)
    frames = []
    for index in range(start, -1, -1):
        month = SEASON_MONTHS[index]
        html = client.get_html(f"/leagues/NBA_{end_year}_games-{month}.html", f"games_{end_year}_{month}")
        frames.append(parse_games(html))
        combined = pd.concat(frames, ignore_index=True)
        played = pd.concat([combined["home_id"], combined["away_id"]]).value_counts()
        if all(played.get(team, 0) >= config.FORM_GAMES for team in team_ids):
            break
    return pd.concat(frames, ignore_index=True).sort_values("date", kind="stable").reset_index(drop=True)


# ---------- building the file ----------

def build(client: BRClient, today: date, previous: dict | None) -> BuildResult:
    """Fetch, compute, and assemble the odds. Raises BuildError/ParseError/FetchError on trouble."""
    end_year = config.season_end_year(today)
    ratings = parse_ratings(client.get_html(f"/leagues/NBA_{end_year}.html", f"ratings_{end_year}"))
    if len(ratings) != len(config.TEAMS):
        raise BuildError(f"ratings page has {len(ratings)} teams, expected {len(config.TEAMS)}")
    games_played = features.team_games(ratings)
    if (games_played == 0).all():
        return BuildResult(None, "The season has not started; leaving odds.json unchanged.")
    if (games_played >= config.SEASON_GAMES).all():
        return BuildResult(None, "The regular season is over; leaving odds.json unchanged (playoff odds come in Phase 7).")

    prior = parse_ratings(client.get_html(f"/leagues/NBA_{end_year - 1}.html", f"ratings_{end_year - 1}"))
    seasons = [parse_players(client.get_html(f"/leagues/NBA_{year}_advanced.html", f"players_advanced_{year}"))
               for year in (end_year, end_year - 1, end_year - 2)]
    injuries = parse_injuries(client.get_html("/friv/injuries.fcgi", "injuries"))
    games = fetch_recent_games(client, end_year, today, list(games_played.index))

    roster = features.current_roster(seasons[0])
    counts = roster.groupby("team_id").size().reindex(games_played.index).fillna(0)
    thin = counts[counts < MIN_ROSTER_PLAYERS]
    if len(thin):
        raise BuildError(f"too few players parsed for: {', '.join(thin.index)} (the players page may have changed)")

    stars = features.star_power(roster, games_played)
    availability = features.star_availability(stars, [features.season_games(s) for s in seasons],
                                              games_played, injuries, today)
    values = {
        "net_rating": features.net_rating(ratings, prior),
        "star_power": pd.Series({t: r.value for t, r in stars.items()}, dtype=float),
        "recent_form": pd.Series(features.recent_form(games, list(games_played.index)), dtype=float),
        "star_availability": pd.Series({t: r.value for t, r in availability.items()}, dtype=float),
        "rotation_availability": pd.Series(features.rotation_availability(roster, games_played), dtype=float),
    }
    z = {key: model.zscores(series) for key, series in values.items()}
    percentile = {key: model.percentiles(series) for key, series in values.items()}
    composite, contributions, effective = model.composite_scores(z)
    if composite.isna().any():
        raise BuildError(f"no usable inputs for: {', '.join(composite.index[composite.isna()])}")
    penalty, penalty_notes = model.penalties(ratings, values["net_rating"], today, end_year)
    final = composite + penalty
    probs = model.round_probs(model.softmax(final))
    ranks = model.rank_teams(probs, final)

    previous_probs = _previous_probs(previous)
    teams = []
    for team in config.TEAMS:
        tid = team.id
        row = ratings.set_index("team_id").loc[tid]
        inputs = {}
        for key in config.INPUT_KEYS:
            ok = key in values and pd.notna(values[key].get(tid))
            inputs[key] = {
                "value": round(float(values[key][tid]), 2),
                "z": round(float(z[key][tid]), 2),
                "percentile": int(percentile[key][tid]),
                "contribution": round(float(contributions.loc[tid, key]), 3),
            } if ok else None
        teams.append({
            "id": tid, "name": team.name, "conference": team.conference,
            "record": f"{int(row['w'])}-{int(row['l'])}",
            "title_prob": float(probs[tid]), "rank": int(ranks[tid]),
            "prob_change_1d": round(float(probs[tid]) - previous_probs[tid], 4) if tid in previous_probs else None,
            "composite": round(float(final[tid]), 3),
            "inputs": inputs,
            "notes": _notes(int(games_played[tid]), penalty_notes[tid], stars[tid], availability[tid], inputs),
        })
    teams.sort(key=lambda t: t["rank"])

    odds = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season": config.season_label(end_year),
        "is_sample": False,
        "games_played_league_avg": round(float(games_played.mean()), 1),
        "model_version": MODEL_VERSION,
        "temperature": config.TEMPERATURE,
        "weights": dict(config.WEIGHTS),
        "teams": teams,
    }
    return BuildResult(odds, f"Built odds for {len(teams)} teams ({client.requests_made} requests).")


def _previous_probs(previous: dict | None) -> dict[str, float]:
    """Return {team_id: title_prob} from the previous file, or {} if it is sample data or an older model."""
    if not previous or previous.get("is_sample") or previous.get("model_version") != MODEL_VERSION:
        return {}
    return {t["id"]: t["title_prob"] for t in previous.get("teams", [])}


def _notes(games: int, penalty_notes: list[str], star: features.StarResult,
           availability: features.AvailabilityResult, inputs: dict) -> list[str]:
    """Return up to MAX_NOTES short notes explaining anything unusual about this team's numbers."""
    notes = []
    if games < EARLY_SEASON_NOTE_GAMES:
        notes.append(f"Early season: only {games} games played, so net rating leans on last season")
    notes += penalty_notes
    notes += list(availability.notes)
    notes += list(star.notes)
    missing = [INPUT_LABELS[k] for k in ("net_rating", "star_power", "recent_form", "star_availability",
                                         "rotation_availability") if inputs[k] is None]
    if missing:
        notes.append(f"No data for {', '.join(missing)}; weights renormalized")
    return notes[:MAX_NOTES]


# ---------- checking and writing ----------

def check_before_publishing(odds: dict, previous: dict | None) -> list[str]:
    """Return the reasons this file must NOT be published (empty list means it is fine)."""
    problems = validate_odds(odds)
    try:
        json.dumps(odds, allow_nan=False)
    except ValueError:
        problems.append("the file contains NaN or infinite numbers")
    for tid, before in _previous_probs(previous).items():
        now = next((t["title_prob"] for t in odds["teams"] if t["id"] == tid), None)
        if now is not None and abs(now - before) > MAX_DAILY_MOVE:
            problems.append(f"{tid} moved {abs(now - before):.1%} in one update (limit {MAX_DAILY_MOVE:.0%})")
    return problems


def write_atomically(odds: dict, path: Path) -> None:
    """Write `odds` as JSON to `path` by writing a temporary file and swapping it in."""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(odds, indent=1) + "\n")
    os.replace(temp, path)


def _read_previous(path: Path) -> dict | None:
    """Return the existing odds file as a dict, or None if it is missing or unreadable."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def run(client: BRClient, today: date, out_path: Path) -> BuildResult:
    """Build, check, and (if everything passes) write the file. The previous file is read first
    so we can compute changes and compare. Raises BuildError if the checks fail."""
    previous = _read_previous(out_path)
    result = build(client, today, previous)
    if result.odds is None:
        return result
    problems = check_before_publishing(result.odds, previous)
    if problems:
        raise BuildError("; ".join(problems))
    write_atomically(result.odds, out_path)
    return result


def main() -> int:
    """Command-line entry point. Returns 0 (done or nothing to do), 1 (failed), or 2 (blocked by the site)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=ODDS_PATH, help="where to write the file (default: data/odds.json)")
    parser.add_argument("--today", type=date.fromisoformat, default=None, help="override today's date (YYYY-MM-DD)")
    args = parser.parse_args()
    today = args.today or datetime.now(timezone.utc).date()

    client = BRClient()
    try:
        result = run(client, today, args.out)
    except Blocked as err:
        print(f"ERROR {err}; previous {args.out.name} kept", file=sys.stderr)
        return 2
    except (FetchError, ParseError, BuildError, ValueError, OSError) as err:
        print(f"ERROR {err}; previous {args.out.name} kept", file=sys.stderr)
        return 1
    print(result.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
