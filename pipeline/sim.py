"""Monte Carlo simulation of the rest of the season and the playoffs (SPEC.md section 6.4).

Pure functions on numpy arrays: no network, no files. Teams are rows 0..N-1 of a `League`.
All simulations run at once (arrays have a leading "simulation" axis), so 20,000 runs take a
few seconds.

How one simulated season works:
  1. Each team's rating (points of margin vs an average team) gets random noise, drawn once per
     simulation, with sd RATING_UNCERTAINTY_POINTS * sqrt(20 / (games + 20)): early-season
     ratings are less certain.
  2. Every remaining regular-season game is played: p(home wins) = 1 / (1 + exp(-(r_home - r_away
     + HCA) / scale)). Wins are added to the current wins.
  3. Teams are ranked inside their conference by wins. Ties are broken at random (a simplification:
     the real NBA tiebreakers are not modeled). Seeds 1-6 go straight in; 7-10 play the play-in
     (2020-21 format) or, with fmt="top8", seeds 1-8 go in.
  4. Bracket: round 1 is 1v8, 4v5, 3v6, 2v7 inside each conference, then conference finals, then
     the Finals. Every series is best of 7 with home court 2-2-1-1-1 (the team with the better
     record hosts games 1, 2, 5 and 7; in round 1 that is the better seed).
  5. Tally who made the playoffs, reached the conference finals and the Finals, and won.

Mid-playoff input: a `PlayoffState` gives the bracket seeds and the score of every series
already started; finished series put the loser out (their title chance is exactly zero).
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from pipeline import config, model

# Home games for the higher seed in a best-of-7, by game number (1-based 1, 2, 5, 7).
HOME_PATTERN = np.array([True, True, False, False, True, False, True])
R1_PAIRS = ((0, 7), (3, 4), (2, 5), (1, 6))      # seed positions (0-based) facing each other in round 1
CHUNK = 2500                                     # simulations processed together (bounds memory)


@dataclass(frozen=True)
class SimParams:
    """The tunable constants of the simulation (defaults come from pipeline/config.py)."""

    home_court_points: float = config.HOME_COURT_POINTS
    scale: float = config.GAME_LOGISTIC_SCALE
    uncertainty_points: float = config.RATING_UNCERTAINTY_POINTS
    half_games: float = config.UNCERTAINTY_HALF_GAMES


@dataclass
class League:
    """Everything the simulation needs to know about the league at one moment.

    ids: team ids. rating: points of margin vs an average team. games: games played so far.
    wins: current wins. conference: 0 or 1 per team. home / away: team INDEXES of every remaining
    regular-season game (empty arrays if the regular season is over).
    """

    ids: list[str]
    rating: np.ndarray
    games: np.ndarray
    wins: np.ndarray
    conference: np.ndarray
    home: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    away: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))

    @property
    def n(self) -> int:
        return len(self.ids)


@dataclass
class PlayoffState:
    """A bracket that is already set, possibly with series under way.

    seeds[c] lists the eight team INDEXES of conference c in seed order (1st to 8th).
    scores maps (round, conference, slot) -> (wins_a, wins_b), for series already started:
      round 1: slots 0..3 are 1v8, 4v5, 3v6, 2v7 (a = the better seed);
      round 2: slot 0 = winner of R1 slot 0 (a) vs winner of slot 1 (b); slot 1 = R1 slot 2 vs slot 3;
      round 3: the conference finals, slot 0 (a = winner of R2 slot 0);
      round 4: the Finals, conference 0, slot 0 (a = conference 0's champion).
    A series missing from `scores` has not started (0-0). Reaching 4 wins ends a series.
    """

    seeds: dict[int, list[int]]
    scores: dict[tuple[int, int, int], tuple[int, int]] = field(default_factory=dict)


@dataclass
class SimResult:
    """Simulation output; every array has one entry per team (same order as League.ids)."""

    ids: list[str]
    runs: int
    title: np.ndarray            # share of runs the team won the title (unrounded)
    finals: np.ndarray           # reached the Finals
    conf_finals: np.ndarray      # reached the conference finals
    playoffs: np.ndarray         # was in the 16-team bracket
    avg_seed: np.ndarray         # mean bracket seed (1-8) over the runs in which it made the playoffs; NaN if never

    def title_probs(self) -> pd.Series:
        """Title probabilities rounded to config.PROB_DECIMALS so they sum to exactly 1.0."""
        return model.round_probs(pd.Series(self.title, index=self.ids))


def rating_noise_sd(games: np.ndarray, params: SimParams = SimParams()) -> np.ndarray:
    """Return each team's rating-noise sd: uncertainty_points * sqrt(half_games / (games + half_games))."""
    return params.uncertainty_points * np.sqrt(params.half_games / (games + params.half_games))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


# ---------- series and games ----------

def series_win_hi(rng: np.random.Generator, p_hi_home: np.ndarray, p_hi_away: np.ndarray,
                  wins_hi: np.ndarray, wins_lo: np.ndarray) -> np.ndarray:
    """Play best-of-7 series to the end, starting from wins_hi - wins_lo; return True where the higher seed wins.

    p_hi_home / p_hi_away: chance the higher seed wins a game it hosts / visits. Game number
    (wins_hi + wins_lo + 1) decides the venue: the higher seed hosts games 1, 2, 5, 7. All inputs
    are arrays with one entry per simulated series.
    """
    hi, lo = np.array(wins_hi, dtype=int), np.array(wins_lo, dtype=int)
    draws = rng.random((7, len(hi)))
    for game in range(7):
        live = (hi < 4) & (lo < 4)
        if not live.any():
            break
        at_home = HOME_PATTERN[np.minimum(hi + lo, 6)]
        hi_wins_game = draws[game] < np.where(at_home, p_hi_home, p_hi_away)
        hi += live & hi_wins_game
        lo += live & ~hi_wins_game
    return hi >= 4


def _single_game(rng: np.random.Generator, rr: np.ndarray, host: np.ndarray, guest: np.ndarray,
                 params: SimParams) -> np.ndarray:
    """Play one game per simulation; return True where the host wins. rr is (runs, teams)."""
    rows = np.arange(len(host))
    p = _sigmoid((rr[rows, host] - rr[rows, guest] + params.home_court_points) / params.scale)
    return rng.random(len(host)) < p


def _series(rng: np.random.Generator, rr: np.ndarray, key: np.ndarray, a: np.ndarray, b: np.ndarray,
            score: tuple[int, int], by_seed: bool, params: SimParams) -> np.ndarray:
    """Play one best-of-7 per simulation between teams a and b; return the winners' indexes.

    `score` is (wins_a, wins_b) so far. The host of games 1, 2, 5, 7 is `a` when by_seed (round 1,
    where a is the better seed), otherwise whichever team has the higher `key` (the better record).
    """
    rows = np.arange(len(a))
    a_is_hi = np.ones(len(a), dtype=bool) if by_seed else key[rows, a] >= key[rows, b]
    hi, lo = np.where(a_is_hi, a, b), np.where(a_is_hi, b, a)
    w_hi, w_lo = np.where(a_is_hi, score[0], score[1]), np.where(a_is_hi, score[1], score[0])
    diff = (rr[rows, hi] - rr[rows, lo]) / params.scale
    hca = params.home_court_points / params.scale
    hi_won = series_win_hi(rng, _sigmoid(diff + hca), _sigmoid(diff - hca), w_hi, w_lo)
    return np.where(hi_won, hi, lo)


# ---------- the season ----------

def _play_in(rng: np.random.Generator, rr: np.ndarray, order: np.ndarray, params: SimParams) -> np.ndarray:
    """Return the eight playoff teams (runs, 8) from a conference ranking `order` (runs, n) of team indexes.

    2020-21 format: 7 hosts 8 (winner is the 7 seed); 9 hosts 10; the loser of the first game
    hosts the winner of the second for the 8 seed.
    """
    t7, t8, t9, t10 = order[:, 6], order[:, 7], order[:, 8], order[:, 9]
    seven_wins = _single_game(rng, rr, t7, t8, params)
    seed7, loser = np.where(seven_wins, t7, t8), np.where(seven_wins, t8, t7)
    nine_wins = _single_game(rng, rr, t9, t10, params)
    survivor = np.where(nine_wins, t9, t10)
    loser_wins = _single_game(rng, rr, loser, survivor, params)
    seed8 = np.where(loser_wins, loser, survivor)
    return np.column_stack([order[:, :6], seed7, seed8])


def _conference_bracket(rng, rr, key, seeds, state_scores, conf, params):
    """Play rounds 1-3 of one conference. Returns (round-2 winners (runs,2), champion (runs,))."""
    def score(rnd, slot):
        return state_scores.get((rnd, conf, slot), (0, 0))

    r1 = [_series(rng, rr, key, seeds[:, i], seeds[:, j], score(1, slot), True, params)
          for slot, (i, j) in enumerate(R1_PAIRS)]
    r2 = [_series(rng, rr, key, r1[0], r1[1], score(2, 0), False, params),
          _series(rng, rr, key, r1[2], r1[3], score(2, 1), False, params)]
    champion = _series(rng, rr, key, r2[0], r2[1], score(3, 0), False, params)
    return np.column_stack(r2), champion


def _check_state(state: PlayoffState, league: League) -> None:
    """Raise ValueError if the state cannot be right (bad seeds, or a later series started before its feeders finished)."""
    if sorted(state.seeds) != [0, 1] or any(len(s) != 8 for s in state.seeds.values()):
        raise ValueError("PlayoffState.seeds needs eight teams for each of conferences 0 and 1")
    done = lambda rnd, c, slot: max(state.scores.get((rnd, c, slot), (0, 0))) >= 4   # noqa: E731
    for (rnd, c, slot), (a, b) in state.scores.items():
        if min(a, b) < 0 or max(a, b) > 4 or a == b == 4:
            raise ValueError(f"impossible series score {(a, b)} for {(rnd, c, slot)}")
        feeders = {2: [(1, c, 2 * slot), (1, c, 2 * slot + 1)], 3: [(2, c, 0), (2, c, 1)],
                   4: [(3, 0, 0), (3, 1, 0)]}.get(rnd, [])
        if (a or b) and not all(done(*f) for f in feeders):
            raise ValueError(f"series {(rnd, c, slot)} has games played but its earlier round is not finished")


def simulate(league: League, runs: int = config.SIM_RUNS, seed: int = 0, fmt: str = "playin",
             state: PlayoffState | None = None, params: SimParams = SimParams()) -> SimResult:
    """Simulate the rest of the season `runs` times and return title / Finals / conference-finals /
    playoff shares and average seed per team. The same seed gives the same result.

    fmt: "playin" (seeds 7-10 play the 2020-21 play-in) or "top8" (seeds 1-8 go straight in, the
    format before 2020). With `state`, the regular season must be over (no remaining games) and
    the bracket is taken from the state instead of the standings.
    """
    if fmt not in ("playin", "top8"):
        raise ValueError(f"unknown format {fmt!r}")
    if state is not None:
        _check_state(state, league)
        if len(league.home):
            raise ValueError("a PlayoffState needs the regular season to be finished")
    members = [np.where(league.conference == c)[0] for c in (0, 1)]
    if min(len(m) for m in members) < (10 if fmt == "playin" else 8) and state is None:
        raise ValueError("each conference needs enough teams for the playoff format")

    rng = np.random.default_rng(seed)
    n = league.n
    sd = rating_noise_sd(league.games, params)
    home_hot = np.zeros((len(league.home), n), dtype=np.float32)   # one-hot: game -> home team
    away_hot = np.zeros_like(home_hot)
    home_hot[np.arange(len(league.home)), league.home] = 1.0
    away_hot[np.arange(len(league.away)), league.away] = 1.0
    totals = {k: np.zeros(n) for k in ("title", "finals", "conf_finals", "playoffs", "seed_sum")}

    for start in range(0, runs, CHUNK):
        m = min(CHUNK, runs - start)
        rr = league.rating + rng.standard_normal((m, n)) * sd
        wins = np.tile(league.wins.astype(float), (m, 1))
        if len(league.home):
            p = _sigmoid((rr[:, league.home] - rr[:, league.away] + params.home_court_points) / params.scale)
            home_won = (rng.random(p.shape) < p).astype(np.float32)
            wins += home_won @ home_hot + (1.0 - home_won) @ away_hot
        key = wins + 0.5 * rng.random((m, n))                     # random tiebreak, never overrides a win

        bracket, champs, r2_all = [], [], []
        for c in (0, 1):
            if state is not None:
                seeds = np.tile(np.array(state.seeds[c]), (m, 1))
                key[:, seeds[0]] = league.wins[seeds[0]] + 0.001 * (8 - np.arange(8))   # seed breaks record ties
            else:
                order = members[c][np.argsort(-key[:, members[c]], axis=1)]
                seeds = _play_in(rng, rr, order, params) if fmt == "playin" else order[:, :8]
            scores = state.scores if state is not None else {}
            r2, champ = _conference_bracket(rng, rr, key, seeds, scores, c, params)
            bracket.append(seeds), champs.append(champ), r2_all.append(r2)
        winner = _series(rng, rr, key, champs[0], champs[1], (state.scores if state else {}).get((4, 0, 0), (0, 0)),
                         False, params)

        count = lambda teams: np.bincount(teams.ravel(), minlength=n)   # noqa: E731
        for seeds in bracket:
            totals["playoffs"] += count(seeds)
            totals["seed_sum"] += np.bincount(seeds.ravel(), weights=np.tile(np.arange(1, 9), m), minlength=n)
        for r2 in r2_all:
            totals["conf_finals"] += count(r2)
        totals["finals"] += count(np.column_stack(champs))
        totals["title"] += count(winner)

    shares = {k: v / runs for k, v in totals.items()}
    with np.errstate(invalid="ignore", divide="ignore"):
        avg_seed = totals["seed_sum"] / totals["playoffs"]
    return SimResult(league.ids, runs, shares["title"], shares["finals"], shares["conf_finals"],
                     shares["playoffs"], avg_seed)


def league_from_frames(ratings_pts: pd.Series, games: pd.Series, wins: pd.Series, conferences: dict[str, str],
                       remaining: pd.DataFrame | None = None) -> League:
    """Build a League from pandas objects indexed by team id.

    conferences maps team id -> "East" or "West" (East = 0). `remaining` has home_id and away_id
    columns, one row per game still to play (None or empty when the regular season is over).
    """
    ids = list(ratings_pts.index)
    index = {t: i for i, t in enumerate(ids)}
    home = np.array([index[t] for t in remaining["home_id"]], dtype=int) if remaining is not None and len(remaining) else np.zeros(0, dtype=int)
    away = np.array([index[t] for t in remaining["away_id"]], dtype=int) if remaining is not None and len(remaining) else np.zeros(0, dtype=int)
    return League(ids, ratings_pts.to_numpy(float), games.reindex(ids).to_numpy(float), wins.reindex(ids).to_numpy(float),
                  np.array([0 if conferences[t] == "East" else 1 for t in ids]), home, away)
