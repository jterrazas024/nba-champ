"""Tests for the playoff simulation (pipeline/sim.py, pipeline/seed.py). Synthetic leagues only; no network."""
import time
from datetime import date
from math import comb

import numpy as np
import pytest

from pipeline import config, sim
from pipeline.seed import daily_seed
from tests.synthetic import full_schedule, sim_league

NO_NOISE = sim.SimParams(uncertainty_points=0.0)
NEUTRAL = sim.SimParams(uncertainty_points=0.0, home_court_points=0.0)
EAST, WEST = 0, 1


def east_indexes(league):
    return np.where(league.conference == EAST)[0]


# ---------- seed ----------

def test_daily_seed_is_repeatable_and_changes_with_each_input():
    base = daily_seed(date(2026, 11, 8), "2026-27", "v0")
    assert base == daily_seed(date(2026, 11, 8), "2026-27", "v0")
    assert len({base, daily_seed(date(2026, 11, 9), "2026-27", "v0"), daily_seed(date(2026, 11, 8), "2027-28", "v0"),
                daily_seed(date(2026, 11, 8), "2026-27", "v1")}) == 4
    assert 0 <= base < 2 ** 64


def test_daily_seed_is_a_fixed_value_not_pythons_per_process_hash():
    assert daily_seed(date(2026, 11, 8), "2026-27", "v0") == 3094007971668112667   # SHA-256 based, same in every process


# ---------- one series ----------

def best_of_seven(p: float) -> float:
    """Closed form: the favorite wins 4 games before the other team wins k < 4 (k = 0..3)."""
    return sum(comb(3 + k, k) * p ** 4 * (1 - p) ** k for k in range(4))


def test_best_of_seven_matches_the_closed_form_for_p_06():
    rng = np.random.default_rng(1)
    n = 400_000
    zeros = np.zeros(n, dtype=int)
    won = sim.series_win_hi(rng, np.full(n, 0.6), np.full(n, 0.6), zeros, zeros).mean()
    assert abs(won - best_of_seven(0.6)) < 0.004          # 0.7102 expected


def test_up_three_nothing_wins_almost_always():
    rng = np.random.default_rng(2)
    n = 200_000
    won = sim.series_win_hi(rng, np.full(n, 0.5), np.full(n, 0.5), np.full(n, 3), np.zeros(n, dtype=int)).mean()
    assert abs(won - 0.9375) < 0.004                      # 1 - 0.5 ** 4


def test_a_finished_series_stays_finished():
    rng = np.random.default_rng(3)
    wins_hi = np.array([4, 0, 4, 2])
    wins_lo = np.array([1, 4, 2, 4])
    assert sim.series_win_hi(rng, np.full(4, .01), np.full(4, .01), wins_hi, wins_lo).tolist() == [True, False, True, False]


def test_home_court_helps_the_higher_seed_hosting_four_games():
    rng = np.random.default_rng(4)
    n = 300_000
    z = np.zeros(n, dtype=int)
    with_home = sim.series_win_hi(rng, np.full(n, 0.65), np.full(n, 0.55), z, z).mean()
    without = sim.series_win_hi(rng, np.full(n, 0.55), np.full(n, 0.65), z, z).mean()   # the same teams, home court reversed
    assert with_home > without + 0.02


# ---------- whole-league behavior ----------

def test_probabilities_sum_to_exactly_one_after_rounding():
    result = sim.simulate(sim_league(), runs=3000, seed=5)
    probs = result.title_probs()
    assert round(probs.sum() * 10 ** config.PROB_DECIMALS) == 10 ** config.PROB_DECIMALS
    assert (probs >= 0).all() and len(probs) == 30


def test_reach_shares_add_up_and_nest():
    result = sim.simulate(sim_league(), runs=3000, seed=6)
    assert result.title.sum() == pytest.approx(1.0)
    assert result.finals.sum() == pytest.approx(2.0)
    assert result.conf_finals.sum() == pytest.approx(4.0)             # four conference finalists per run
    assert result.playoffs.sum() == pytest.approx(16.0)
    assert (result.title <= result.finals + 1e-12).all() and (result.finals <= result.conf_finals + 1e-12).all()


def test_identical_teams_in_a_symmetric_field_tie():
    league = sim_league(rating=np.zeros(30), wins=np.full(30, 41))
    result = sim.simulate(league, runs=20_000, seed=7)
    assert np.abs(result.title - 1 / 30).max() < 0.012     # about 6 standard errors


def test_two_identical_favorites_in_different_conferences_tie():
    rating = np.zeros(30)
    east, west = east_indexes(sim_league())[0], np.where(sim_league().conference == WEST)[0][0]
    rating[[east, west]] = 8.0
    league = sim_league(rating=rating, wins=np.full(30, 41) + np.isin(np.arange(30), [east, west]) * 20)
    result = sim.simulate(league, runs=20_000, seed=8, params=NO_NOISE)
    assert abs(result.title[east] - result.title[west]) < 0.015
    assert result.title[east] > 0.2


def test_stronger_teams_win_more_in_a_synthetic_league():
    league = sim_league()
    result = sim.simulate(league, runs=20_000, seed=9)
    order = np.argsort(league.rating)
    p = result.title[order]
    for i in range(30):
        for j in range(i + 1, 30):
            assert p[i] <= p[j] + 0.01, (i, j)                  # never meaningfully backwards
    assert p[-3:].mean() > 5 * p[:15].mean() + 0.01
    assert result.title[np.argmax(league.rating)] > 0.1


def test_same_seed_same_answer_and_different_seed_differs():
    league = sim_league()
    a = sim.simulate(league, runs=2000, seed=11)
    b = sim.simulate(league, runs=2000, seed=11)
    c = sim.simulate(league, runs=2000, seed=12)
    assert np.array_equal(a.title, b.title) and np.array_equal(a.avg_seed, b.avg_seed, equal_nan=True)
    assert not np.array_equal(a.title, c.title)


def test_remaining_games_move_the_standings():
    """A team that has won every game on a short schedule ends up with a better seed than one that lost them all."""
    schedule = [(0, 1)] * 20       # team 0 hosts team 1 twenty times
    weak = sim_league(rating=np.where(np.arange(30) == 0, 12.0, np.where(np.arange(30) == 1, -12.0, 0.0)),
                      wins=np.full(30, 40), remaining=schedule, games=60)
    result = sim.simulate(weak, runs=2000, seed=13, params=NO_NOISE)
    assert result.avg_seed[0] < result.avg_seed[1] or np.isnan(result.avg_seed[1])
    assert result.playoffs[0] > result.playoffs[1]


# ---------- play-in and the top-8 format ----------

def test_play_in_sends_only_seeds_7_to_10_into_the_last_two_spots():
    rng = np.random.default_rng(14)
    runs, teams = 5000, 15
    order = np.tile(np.arange(teams), (runs, 1))
    rr = rng.standard_normal((runs, teams))
    bracket = sim._play_in(rng, rr, order, sim.SimParams())
    assert (bracket[:, :6] == np.arange(6)).all()                       # seeds 1-6 untouched
    assert set(np.unique(bracket[:, 6:])) <= {6, 7, 8, 9}               # spots 7 and 8 come from teams ranked 7-10
    assert (bracket[:, 6] != bracket[:, 7]).all()
    assert set(np.unique(bracket[:, 6])) == {6, 7}                      # only the 7v8 game feeds the 7 seed


def test_with_a_finished_season_only_the_top_ten_can_make_the_playoffs():
    league = sim_league()
    result = sim.simulate(league, runs=4000, seed=15)
    by_wins = {c: east_indexes(league) if c == EAST else np.where(league.conference == WEST)[0] for c in (EAST, WEST)}
    for members in by_wins.values():
        ranked = members[np.argsort(-league.wins[members])]
        assert (result.playoffs[ranked[:6]] == 1.0).all()
        assert ((result.playoffs[ranked[6:10]] > 0) & (result.playoffs[ranked[6:10]] < 1)).all()
        assert (result.playoffs[ranked[10:]] == 0.0).all()
        assert result.playoffs[ranked[6:10]].sum() == pytest.approx(2.0)


def test_top8_format_sends_the_top_eight_in():
    league = sim_league()
    result = sim.simulate(league, runs=3000, seed=16, fmt="top8")
    for members in (east_indexes(league), np.where(league.conference == WEST)[0]):
        ranked = members[np.argsort(-league.wins[members])]
        assert (result.playoffs[ranked[:8]] == 1.0).all() and (result.playoffs[ranked[8:]] == 0.0).all()
    assert result.avg_seed.shape == (30,)


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError):
        sim.simulate(sim_league(), runs=10, fmt="best-of-nine")


# ---------- mid-playoff conditioning ----------

def bracket_state(league, scores=None):
    seeds = {c: [int(i) for i in (east_indexes(league) if c == EAST else np.where(league.conference == WEST)[0])
                 [np.argsort(-league.wins[east_indexes(league) if c == EAST else np.where(league.conference == WEST)[0]])][:8]]
             for c in (EAST, WEST)}
    return sim.PlayoffState(seeds, scores or {})


def test_an_eliminated_team_has_exactly_zero_title_chance_even_if_it_is_the_best_team():
    rating = np.zeros(30)
    league = sim_league(rating=rating)
    state = bracket_state(league, {(1, EAST, 0): (0, 4)})          # the East 1 seed lost 0-4 to the 8 seed
    result = sim.simulate(league, runs=3000, seed=17, state=state)
    one_seed = state.seeds[EAST][0]
    assert result.title[one_seed] == 0.0 and result.finals[one_seed] == 0.0 and result.conf_finals[one_seed] == 0.0
    missed = set(range(30)) - {t for s in state.seeds.values() for t in s}
    assert all(result.title[t] == 0.0 and result.playoffs[t] == 0.0 for t in missed)
    assert result.playoffs[one_seed] == 1.0                         # still counts as a playoff team that round


def test_a_team_up_three_nothing_in_the_finals_usually_wins_the_title():
    league = sim_league(rating=np.zeros(30))
    state = bracket_state(league)
    east_champ, west_champ = state.seeds[EAST][0], state.seeds[WEST][0]
    scores = {}
    for c in (EAST, WEST):
        for slot, win in enumerate([(4, 0)] * 4):
            scores[(1, c, slot)] = win                              # the better seed always won round 1
        scores[(2, c, 0)], scores[(2, c, 1)], scores[(3, c, 0)] = (4, 0), (4, 0), (4, 0)
    scores[(4, 0, 0)] = (3, 0)
    result = sim.simulate(league, runs=20_000, seed=18, state=sim.PlayoffState(state.seeds, scores), params=NEUTRAL)
    assert abs(result.title[east_champ] - 0.9375) < 0.01
    assert result.title[east_champ] + result.title[west_champ] == pytest.approx(1.0)
    assert result.finals[east_champ] == 1.0 and result.finals[west_champ] == 1.0


def test_up_three_nothing_in_round_one_beats_a_much_better_team_most_of_the_time():
    rating = np.zeros(30)
    league = sim_league(rating=rating)
    state = bracket_state(league, {(1, EAST, 0): (0, 3)})          # the 8 seed leads the 1 seed 3-0
    eight, one = state.seeds[EAST][7], state.seeds[EAST][0]
    result = sim.simulate(league, runs=10_000, seed=19, state=state, params=NEUTRAL)
    assert result.conf_finals[eight] > 0 and result.conf_finals[one] < 0.1
    assert result.conf_finals[one] + result.conf_finals[eight] < 0.6    # whoever wins still has to win round 2 too


def test_a_state_with_a_later_series_started_before_its_feeders_is_rejected():
    league = sim_league()
    with pytest.raises(ValueError, match="not finished"):
        sim.simulate(league, runs=10, state=bracket_state(league, {(2, EAST, 0): (1, 0)}))


def test_a_state_needs_a_finished_regular_season():
    league = sim_league(remaining=[(0, 1)])
    with pytest.raises(ValueError, match="finished"):
        sim.simulate(league, runs=10, state=bracket_state(league))


# ---------- speed ----------

def test_twenty_thousand_runs_with_a_full_schedule_take_under_ten_seconds():
    league = sim_league(games=0, wins=np.zeros(30), remaining=full_schedule())
    start = time.perf_counter()
    result = sim.simulate(league, runs=config.SIM_RUNS, seed=20)
    assert time.perf_counter() - start < 10
    assert result.title.sum() == pytest.approx(1.0)
