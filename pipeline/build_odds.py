"""Build data/odds.json from Basketball-Reference (SPEC.md sections 4, 6 and 7).

Fetches the pages (politely, through pipeline/fetch.py), computes the five model inputs
(pipeline/features.py), turns them into title probabilities (pipeline/model.py), and writes
odds.json in the shape the site reads.

Safety rules (CLAUDE.md "Data source rules"):
  - nothing is written unless the new file passes validation and the sanity checks
  - the file is written to a temporary name and swapped in, so a crash cannot leave half a file
  - any failure exits non-zero and leaves the previous odds.json exactly as it was
  - before the season starts the run does nothing (exit 0)
  - once every team has played 82 games the build switches to playoff mode (SPEC.md section 6.6):
    the headline comes from the simulation conditioned on the play-in and the series played so far

Run:  python pipeline/build_odds.py [--out PATH] [--today YYYY-MM-DD]
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

# Allow running as a plain script (python pipeline/build_odds.py) as well as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config, features, market, model, playoffs, sim, sim_inputs  # noqa: E402
from pipeline.fetch import BRClient, Blocked, FetchError  # noqa: E402
from pipeline.parse import ParseError, parse_games, parse_injuries, parse_players, parse_ratings  # noqa: E402
from pipeline.seed import daily_seed  # noqa: E402
from pipeline.validate import check_sim, validate_odds  # noqa: E402

ODDS_PATH = Path(__file__).resolve().parent.parent / "data" / "odds.json"
MODEL_VERSION = "v0"
MarketSource = Callable[[int, date], market.MarketData]   # returns the Polymarket read for (end_year, today)
MIN_ROSTER_PLAYERS = 8         # sanity check: a team with fewer parsed players means a broken page
MAX_DAILY_MOVE = 0.15           # sanity check: no team's title probability moves more than this in one update
HISTORY_DAYS = 30               # how many daily values each team keeps in `history` (SPEC.md section 7)
CHANGE_WINDOW_DAYS = 3          # prob_change_1d needs an earlier history entry at most this many days back
EARLY_SEASON_NOTE_GAMES = 10    # below this many games, add a note that net rating leans on last season
MAX_NOTES = 4
SEASON_MONTHS = ["october", "november", "december", "january", "february", "march", "april", "may", "june"]
MONTH_INDEX = {10: 0, 11: 1, 12: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 7, 6: 8}
PLAYOFF_MONTH_INDEX = 7        # SEASON_MONTHS[7] is may: from here on a month page may not exist yet
DEFAULT_MONTH_INDEX = 6         # any other month (offseason) starts from April, as before May and June were added

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

    Stops at October at the latest. Returns all finished games found (oldest first). A May or June
    page that does not exist yet (the month's first game is not on the calendar) is skipped.
    """
    start = MONTH_INDEX.get(today.month, DEFAULT_MONTH_INDEX)
    frames = []
    for index in range(start, -1, -1):
        month = SEASON_MONTHS[index]
        try:
            html = client.get_html(f"/leagues/NBA_{end_year}_games-{month}.html", f"games_{end_year}_{month}")
        except Blocked:
            raise
        except FetchError:
            if index < PLAYOFF_MONTH_INDEX:
                raise
            continue
        frames.append(parse_games(html))
        combined = pd.concat(frames, ignore_index=True)
        played = pd.concat([combined["home_id"], combined["away_id"]]).value_counts()
        if all(played.get(team, 0) >= config.FORM_GAMES for team in team_ids):
            break
    return pd.concat(frames, ignore_index=True).sort_values("date", kind="stable").reset_index(drop=True)


# ---------- building the file ----------

def build(client: BRClient, today: date, previous: dict | None, with_sim: bool = True,
          market_source: MarketSource | None = None) -> BuildResult:
    """Fetch, compute, and assemble the odds. Raises BuildError/ParseError/FetchError on trouble.

    With `with_sim`, the playoff simulation is run afterwards and published in extra fields
    (shadow mode, SPEC.md section 7). That stage is optional: if it fails for any reason, the
    softmax odds are returned without the extra fields and a WARNING goes to stderr.

    Once every team has 82 games the build is in playoff mode: the simulation is required (it IS
    the headline, SPEC.md section 6.6) and any problem with it fails the build, which leaves the
    previous file in place.

    `market_source(end_year, today)` returns a market.MarketData (the Polymarket read, SPEC.md section
    6.7) and is blended in at config.MARKET_WEIGHT. If it is None or fails in any way, the weight is 0
    and the odds are the pure model's; the market can never fail the build.
    """
    end_year = config.season_end_year(today)
    ratings = parse_ratings(client.get_html(f"/leagues/NBA_{end_year}.html", f"ratings_{end_year}"))
    if len(ratings) != len(config.TEAMS):
        raise BuildError(f"ratings page has {len(ratings)} teams, expected {len(config.TEAMS)}")
    games_played = features.team_games(ratings)
    if (games_played == 0).all():
        return BuildResult(None, "The season has not started; leaving odds.json unchanged.")
    playoff_mode = bool((games_played >= config.SEASON_GAMES).all())

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
    softmax_probs = model.round_probs(model.softmax(final))
    post, playoff_sim = None, None
    if playoff_mode:
        post, per_team_sim, sim_top = _run_playoff_simulation(client, today, end_year, ratings, games_played,
                                                              final if config.SIM_USES_PENALTIES else composite)
        playoff_sim = (per_team_sim, sim_top)
        probs = pd.Series({tid: v["sim_prob"] for tid, v in per_team_sim.items()}).reindex(softmax_probs.index)
    else:
        probs = softmax_probs
    model_probs = probs
    live_ids = post.alive(list(probs.index)) if playoff_mode else list(probs.index)
    market_block, market_series = _blend_market(market_source, end_year, today, live_ids)
    if market_series is not None:
        probs = market.blend(model_probs, market_series, config.MARKET_WEIGHT)
    ranks = model.rank_teams(probs, final)

    history_dates, history = build_history(previous, today, {tid: float(p) for tid, p in probs.items()})
    source_changed = bool(previous) and previous.get("headline_source", "softmax") != ("simulation" if playoff_mode else "softmax")
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
        entry = {
            "id": tid, "name": team.name, "conference": team.conference,
            "record": f"{int(row['w'])}-{int(row['l'])}",
            "title_prob": float(probs[tid]), "rank": int(ranks[tid]),
            "prob_change_1d": None if source_changed else _change_from_history(history_dates, history[tid], today),
            "history": history[tid],
            "composite": round(float(final[tid]), 3),
            "inputs": inputs,
            "notes": _notes(int(games_played[tid]), penalty_notes[tid], stars[tid], availability[tid], inputs),
        }
        if market_series is not None:
            entry["model_prob"] = float(model_probs[tid])
            entry["market_prob"] = round(float(market_series[tid]), 6)
        if playoff_mode:
            entry.update(playoff_sim[0][tid])
            entry["softmax_prob"] = float(softmax_probs[tid])
            entry["status"] = _status(tid, post)
            entry["notes"] = (_playoff_notes(tid, post) + entry["notes"])[:MAX_NOTES]
        teams.append(entry)
    teams.sort(key=lambda t: t["rank"])

    odds = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season": config.season_label(end_year),
        "is_sample": False,
        "games_played_league_avg": round(float(games_played.mean()), 1),
        "model_version": MODEL_VERSION,
        "phase": post.phase if playoff_mode else "regular_season",
        "headline_source": "simulation" if playoff_mode else "softmax",
        "temperature": config.TEMPERATURE,
        "weights": dict(config.WEIGHTS),
        "market": market_block,
        "history_dates": history_dates,
        "teams": teams,
    }
    simulated = False
    if playoff_mode:
        odds["sim"] = playoff_sim[1]
        simulated = True
    elif with_sim:
        simulated = _add_simulation(odds, client, today, end_year, ratings, games_played,
                                    final if config.SIM_USES_PENALTIES else composite)
    note = "with" if simulated else "without"
    return BuildResult(odds, f"Built odds for {len(teams)} teams ({client.requests_made} requests, {note} simulation).")


# ---------- market consensus (SPEC.md section 6.7) ----------

def default_market_source(end_year: int, today: date) -> market.MarketData:
    """Fetch the Polymarket champion market (one cached GET). Used by the command line."""
    return market.get_market(end_year, today)


def _blend_market(source: MarketSource | None, end_year: int, today: date,
                  live_ids: list[str]) -> tuple[dict, pd.Series | None]:
    """Return (the `market` block of odds.json, market probabilities or None).

    Any failure (no source, network, a block, a malformed or incomplete response) gives weight 0 and
    the reason in the block, plus one WARNING line on stderr. Never raises.
    """
    block = {"source": config.MARKET_SOURCE, "fetched_at": None, "weight": 0.0, "used": False}
    if source is None:
        block["reason"] = "market not requested"
        return block, None
    try:
        data = source(end_year, today)
        series = market.market_probs(data.prices, live_ids)
    except Exception as err:  # noqa: BLE001  (deliberately broad: the market must never stop the model's odds)
        reason = " ".join(f"{type(err).__name__}: {err}".split())
        print(f"WARNING market blend skipped, publishing pure model odds ({reason})", file=sys.stderr)
        block["reason"] = reason[:200]
        return block, None
    block.update(fetched_at=data.fetched_at, weight=config.MARKET_WEIGHT, used=True, event=data.slug)
    return block, series


# ---------- the playoff simulation, shadow mode (SPEC.md sections 6.4 and 7) ----------

def _run_simulation(client: BRClient, today: date, end_year: int, ratings: pd.DataFrame,
                    games_played: pd.Series, composite: pd.Series) -> tuple[dict[str, dict], dict]:
    """Simulate the rest of the season. Returns ({team_id: per-team sim fields}, top-level `sim` dict).

    Strength = MARGIN_PER_Z x composite; the schedule is every unplayed regular-season game from
    this month through April; the random seed depends only on the date (pipeline/seed.py), so a
    same-day rerun with the same inputs gives the same numbers. Raises on any problem.
    """
    month_index = MONTH_INDEX.get(today.month, DEFAULT_MONTH_INDEX)
    remaining = sim_inputs.fetch_remaining_schedule(client, end_year, month_index)
    seed = daily_seed(today, config.season_label(end_year), MODEL_VERSION)
    remaining = sim_inputs.complete_remaining(remaining, games_played, np.random.default_rng(seed))
    by_team = ratings.set_index("team_id")
    league = sim.league_from_frames(composite * config.MARGIN_PER_Z, games_played, by_team["w"].astype(float),
                                    {t.id: t.conference for t in config.TEAMS}, remaining)
    result = sim.simulate(league, runs=config.SIM_RUNS, seed=seed, fmt="playin")
    return _sim_fields(result), {"runs": int(result.runs), "seed_date": today.isoformat(), "mode": "regular_season",
                                 "margin_per_z": config.MARGIN_PER_Z}


def _sim_fields(result: sim.SimResult) -> dict[str, dict]:
    """Return {team_id: {"sim_prob": ..., "sim": {...}}}: the per-team simulation fields of odds.json."""
    title = result.title_probs()
    per_team = {}
    for i, tid in enumerate(result.ids):
        seed_avg = result.avg_seed[i]
        per_team[tid] = {
            "sim_prob": float(title[tid]),
            "sim": {"finals": round(float(result.finals[i]), 4), "conf_finals": round(float(result.conf_finals[i]), 4),
                    "make_playoffs": round(float(result.playoffs[i]), 4),
                    "seed_avg": None if pd.isna(seed_avg) else round(float(seed_avg), 2)},
        }
    return per_team


# ---------- playoff mode (SPEC.md section 6.6) ----------

def _run_playoff_simulation(client: BRClient, today: date, end_year: int, ratings: pd.DataFrame,
                            games_played: pd.Series, composite: pd.Series):
    """Read the postseason so far and simulate the rest. Returns (Postseason, per-team sim fields, top-level `sim`).

    Strength is the same as before the playoffs (MARGIN_PER_Z x composite). Unlike the shadow-mode
    simulation this stage is required: it raises on any problem, including a bracket that does not
    fit the games or a team that is out but still has title odds.
    """
    games = playoffs.fetch_postseason(client, end_year, today)
    ids = list(composite.index)
    post = playoffs.read_postseason(games, ratings, ids)
    seed = daily_seed(today, config.season_label(end_year), MODEL_VERSION)
    by_team = ratings.set_index("team_id")
    league = sim.league_from_frames(composite * config.MARGIN_PER_Z, games_played, by_team["w"].astype(float),
                                    {t.id: t.conference for t in config.TEAMS})
    result = sim.simulate(league, runs=config.SIM_RUNS, seed=seed, fmt="playin", state=post.state)
    per_team = _sim_fields(result)
    alive = set(post.alive(ids))
    zombies = [t for t in ids if t not in alive and per_team[t]["sim_prob"] != 0]
    if zombies:
        raise BuildError(f"teams that are out still have title odds: {', '.join(zombies)}")
    if post.champion and per_team[post.champion]["sim_prob"] != 1.0:
        raise BuildError(f"{post.champion} won the title but its odds are {per_team[post.champion]['sim_prob']}")
    top = {"runs": int(result.runs), "seed_date": today.isoformat(), "mode": post.phase,
           "margin_per_z": config.MARGIN_PER_Z}
    return post, per_team, top


def _status(team_id: str, post: playoffs.Postseason) -> str:
    """Return "champion", "alive" or "eliminated" for the `status` field."""
    if team_id == post.champion:
        return "champion"
    return "eliminated" if team_id in post.out else "alive"


def _playoff_notes(team_id: str, post: playoffs.Postseason) -> list[str]:
    """Return the short note that goes first in a team's notes in playoff mode (empty for a team still alive)."""
    if team_id == post.champion:
        return ["Won the championship"]
    return [post.out[team_id]] if team_id in post.out else []


def _add_simulation(odds: dict, client: BRClient, today: date, end_year: int, ratings: pd.DataFrame,
                    games_played: pd.Series, composite: pd.Series) -> bool:
    """Add the simulation fields to `odds` in place. Returns True if added.

    Failure isolation: the simulation is optional in shadow mode. Any problem (a schedule page that
    cannot be fetched or read, a block by the site, an error in the simulator, a failed sanity
    check) leaves `odds` exactly as the softmax produced it and prints one WARNING line to stderr.
    """
    try:
        per_team, top = _run_simulation(client, today, end_year, ratings, games_played, composite)
        trial = {"teams": [{**t, **per_team[t["id"]]} for t in odds["teams"]], "sim": top}
        problems = check_sim(trial)
        if problems:
            raise BuildError("; ".join(problems))
    except Exception as err:  # noqa: BLE001  (deliberately broad: nothing here may stop the standard odds)
        reason = " ".join(f"{type(err).__name__}: {err}".split())
        print(f"WARNING playoff simulation skipped, publishing the standard odds without sim fields ({reason})",
              file=sys.stderr)
        return False
    odds["teams"] = trial["teams"]
    odds["sim"] = top
    return True


def _previous_history(previous: dict | None, team_ids: list[str]) -> dict[str, dict[str, float]]:
    """Return {team_id: {YYYY-MM-DD: title_prob}} carried over from the previous file.

    Empty if the previous file is missing, sample data, or from a different model version. A real
    file without history is seeded with its own date and title_prob values (the one-time bootstrap).
    Never raises: any problem with the old file means "start fresh" (SPEC.md section 7).
    """
    try:
        if not previous or previous.get("is_sample") or previous.get("model_version") != MODEL_VERSION:
            return {}
        by_id = {t["id"]: t for t in previous["teams"]}
        dates = previous.get("history_dates")
        if dates is None:
            seed = datetime.strptime(previous["generated_at"], "%Y-%m-%dT%H:%M:%SZ").date().isoformat()
            return {tid: {seed: float(by_id[tid]["title_prob"])} for tid in team_ids}
        result = {}
        for tid in team_ids:
            values = by_id[tid]["history"]
            if len(values) != len(dates):
                return {}
            result[tid] = {d: float(v) for d, v in zip(dates, values)}
        for d in dates:
            date.fromisoformat(d)
        return result
    except (KeyError, TypeError, ValueError, AttributeError):
        return {}


def build_history(previous: dict | None, today: date, probs: dict[str, float]) -> tuple[list[str], dict[str, list[float]]]:
    """Return (history_dates, {team_id: history}): the last HISTORY_DAYS daily title_probs, oldest first.

    Carries the previous file's history forward and sets today's value (a second run on the same
    date overwrites it). Gaps stay gaps. Implements SPEC.md section 7 ("history").
    """
    today_text = today.isoformat()
    carried = _previous_history(previous, list(probs))
    per_team = {tid: {d: v for d, v in carried.get(tid, {}).items() if d < today_text} for tid in probs}
    for tid, value in probs.items():
        per_team[tid][today_text] = value
    dates = sorted(per_team[next(iter(probs))])[-HISTORY_DAYS:]
    return dates, {tid: [per_team[tid][d] for d in dates] for tid in probs}


def _change_from_history(dates: list[str], values: list[float], today: date) -> float | None:
    """Today's value minus the most recent earlier entry, if that entry is within CHANGE_WINDOW_DAYS days."""
    if len(dates) < 2 or dates[-1] != today.isoformat():
        return None
    gap = (today - date.fromisoformat(dates[-2])).days
    return round(values[-1] - values[-2], 4) if 0 < gap <= CHANGE_WINDOW_DAYS else None


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
    playoff_mode = odds.get("headline_source") == "simulation"
    for tid, before in _previous_probs(previous).items():
        now = next((t["title_prob"] for t in odds["teams"] if t["id"] == tid), None)
        # In playoff mode a big move is real (a team out, a game 7 won), so the 15-point limit does not
        # apply; the check below catches the mistake that matters there (a team coming back from the dead).
        if now is not None and not playoff_mode and abs(now - before) > MAX_DAILY_MOVE:
            problems.append(f"{tid} moved {abs(now - before):.1%} in one update (limit {MAX_DAILY_MOVE:.0%})")
    if playoff_mode:
        problems += _revived_teams(odds, previous)
    return problems


def _revived_teams(odds: dict, previous: dict | None) -> list[str]:
    """Return a problem for each team the previous playoff-mode file marked eliminated that has title odds now.

    Only applies when the previous file is real, from the same season, and itself in playoff mode.
    """
    if not previous or previous.get("is_sample") or previous.get("season") != odds.get("season") \
            or previous.get("headline_source") != "simulation":
        return []
    out = {t["id"] for t in previous.get("teams", []) if t.get("status") in ("eliminated",)}
    return [f"{t['id']} was eliminated in the previous update but has title odds now"
            for t in odds["teams"] if t["id"] in out and t["title_prob"] > 0]


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


def run(client: BRClient, today: date, out_path: Path, market_source: MarketSource | None = None) -> BuildResult:
    """Build, check, and (if everything passes) write the file. The previous file is read first
    so we can compute changes and compare. Raises BuildError if the checks fail."""
    previous = _read_previous(out_path)
    result = build(client, today, previous, market_source=market_source)
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
        result = run(client, today, args.out, market_source=default_market_source)
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
