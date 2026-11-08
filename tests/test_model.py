"""Tests for the model math. Expected values are worked out by hand in the comments."""
import math
import random
from datetime import date

import pandas as pd
import pytest

from pipeline import config
from pipeline.model import (composite_scores, ordinal, penalties, percentiles, rank_teams,
                            round_probs, softmax, zscores)


def series(**values):
    return pd.Series(values, dtype=float)


# ---------- ordinal ----------

@pytest.mark.parametrize("n,text", [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"),
                                    (12, "12th"), (13, "13th"), (21, "21st"), (22, "22nd"), (100, "100th")])
def test_ordinal(n, text):
    assert ordinal(n) == text


# ---------- z-scores and percentiles ----------

def test_zscores_use_the_population_standard_deviation():
    z = zscores(series(a=1, b=2, c=3))
    # mean 2, population std = sqrt(((1)^2 + 0 + (1)^2) / 3) = sqrt(2/3) = 0.8165
    assert z["a"] == pytest.approx(-1 / math.sqrt(2 / 3))
    assert z["b"] == pytest.approx(0.0)
    assert z["c"] == pytest.approx(1 / math.sqrt(2 / 3))


def test_zscores_skip_missing_values():
    z = zscores(pd.Series({"a": 1.0, "b": 3.0, "c": float("nan")}))
    assert (z["a"], z["b"]) == (pytest.approx(-1.0), pytest.approx(1.0))  # mean 2, std 1 over the two present
    assert math.isnan(z["c"])


def test_zscores_are_zero_when_every_team_is_equal():
    assert zscores(series(a=5, b=5, c=5)).tolist() == [0.0, 0.0, 0.0]


def test_percentiles_run_from_0_to_100():
    p = percentiles(series(a=10, b=20, c=30, d=40))
    assert p.tolist() == [0.0, 33.0, 67.0, 100.0]  # (rank - 1) / 3 * 100, rounded


def test_tied_values_share_a_percentile():
    p = percentiles(series(a=1, b=1, c=3))
    assert p.tolist() == [25.0, 25.0, 100.0]  # average rank 1.5 -> (0.5 / 2) * 100


def test_percentile_of_a_missing_value_is_missing():
    p = percentiles(pd.Series({"a": 1.0, "b": 2.0, "c": float("nan")}))
    assert p["a"] == 0.0 and p["b"] == 100.0 and math.isnan(p["c"])


# ---------- composite ----------

WEIGHTS = {"a": 0.75, "b": 0.25, "c": 0.0}


def test_composite_is_the_weighted_sum_of_z_scores():
    z = {"a": series(X=1.0, Y=2.0), "b": series(X=2.0, Y=0.0)}
    composite, contributions, _ = composite_scores(z, WEIGHTS)
    assert composite["X"] == pytest.approx(0.75 * 1.0 + 0.25 * 2.0)  # 1.25
    assert contributions.loc["X", "a"] == pytest.approx(0.75)
    assert composite["Y"] == pytest.approx(1.5)


def test_a_missing_input_is_dropped_and_the_remaining_weights_renormalized():
    z = {"a": series(X=1.0, Y=2.0), "b": pd.Series({"X": 2.0, "Y": float("nan")})}
    composite, _, effective = composite_scores(z, WEIGHTS)
    # Y has no "b": the weight of "a" becomes 0.75 / 0.75 = 1, so composite = 1 * 2.0
    assert composite["Y"] == pytest.approx(2.0)
    assert effective.loc["Y"].tolist() == [1.0, 0.0]
    assert effective.sum(axis=1).tolist() == pytest.approx([1.0, 1.0])


def test_inputs_with_zero_weight_are_ignored():
    z = {"a": series(X=1.0), "c": series(X=99.0)}
    composite, _, effective = composite_scores(z, WEIGHTS)
    assert composite["X"] == pytest.approx(1.0)
    assert "c" not in effective.columns


def test_a_team_with_no_inputs_has_no_composite():
    z = {"a": pd.Series({"X": 1.0, "Y": float("nan")})}
    composite, _, _ = composite_scores(z, WEIGHTS)
    assert math.isnan(composite["Y"])


# ---------- penalties ----------

def six_teams(games=30):
    # offense rank (higher ORtg is better): T1 > T2 > T3 > T4 > T5 > T6; defense rank (lower DRtg is better): T6 best
    return pd.DataFrame({
        "team_id": ["T1", "T2", "T3", "T4", "T5", "T6"],
        "games": games,
        "ortg": [120, 118, 116, 114, 112, 110],
        "drtg": [120, 118, 116, 114, 112, 110],
    })


@pytest.fixture
def small_league(monkeypatch):
    """Shrink the cutoffs so six teams are enough: 'weak' means ranked worse than 4th."""
    monkeypatch.setattr(config, "TWO_WAY_RANK_CUTOFF", 4)
    monkeypatch.setattr(config, "LATE_GATE_RANK_CUTOFF", 2)
    monkeypatch.setattr(config, "TWO_WAY_MIN_GAMES", 20)


NET = pd.Series({"T1": 6, "T2": 5, "T3": 4, "T4": 3, "T5": 2, "T6": 1}, dtype=float)
NOVEMBER, APRIL = date(2026, 11, 8), date(2027, 4, 1)


def test_two_way_penalty_applies_once_when_offense_or_defense_is_weak(small_league):
    amounts, notes = penalties(six_teams(), NET, NOVEMBER, 2027)
    # T1: best offense (1st) but worst defense (6th: 120 points allowed) -> weak defense -> -0.5
    # T6: worst offense (6th) but best defense (1st) -> weak offense -> -0.5
    # T5: offense 5th (weak) and defense 2nd -> -0.5.   T2: offense 2nd, defense 5th (weak) -> -0.5
    # T3: offense 3rd, defense 4th -> fine.   T4: offense 4th, defense 3rd -> fine
    assert amounts.to_dict() == {"T1": -0.5, "T2": -0.5, "T3": 0.0, "T4": 0.0, "T5": -0.5, "T6": -0.5}
    assert notes["T1"] == ["Defense ranks 6th: -0.5"]
    assert notes["T3"] == []


def test_two_way_penalty_is_not_doubled_when_both_are_weak(small_league, monkeypatch):
    monkeypatch.setattr(config, "TWO_WAY_RANK_CUTOFF", 1)
    ratings = six_teams()
    ratings["drtg"] = ratings["ortg"].map(lambda x: 230 - x)  # best offense is also best defense
    amounts, notes = penalties(ratings, NET, NOVEMBER, 2027)
    assert amounts["T6"] == -0.5
    assert notes["T6"] == ["Offense ranks 6th and defense ranks 6th: -0.5"]


def test_two_way_penalty_waits_for_20_games(small_league):
    amounts, _ = penalties(six_teams(games=19), NET, NOVEMBER, 2027)
    assert (amounts == 0).all()


def test_late_season_gate_only_starts_on_march_first(small_league):
    before, _ = penalties(six_teams(), NET, date(2027, 2, 28), 2027)
    after, notes = penalties(six_teams(), NET, date(2027, 3, 1), 2027)
    assert not (before <= -1.5).any()
    # net rating rank: T1 1st, T2 2nd are safe; T3..T6 are worse than 2nd -> -1.5 each
    assert after["T3"] == pytest.approx(-1.5)          # T3 has no two-way penalty
    assert after["T6"] == pytest.approx(-0.5 - 1.5)    # T6 has both
    assert notes["T3"] == ["Net rating ranks 3rd late in the season: -1.5"]
    assert after["T1"] == pytest.approx(-0.5)          # only the two-way penalty


def test_the_late_gate_uses_the_year_the_season_ends(small_league):
    amounts, _ = penalties(six_teams(), NET, date(2026, 12, 1), 2027)  # Dec 2026 is before March 1, 2027
    assert not (amounts <= -1.5).any()
    assert penalties(six_teams(), NET, APRIL, 2027)[0]["T3"] <= -1.5


# ---------- softmax, rounding, ranks ----------

def test_softmax_matches_a_hand_calculation():
    T = 0.6
    p = softmax(pd.Series({"a": 0.0, "b": T * math.log(2)}), temperature=T)
    # exp(0 / T) = 1, exp(ln 2) = 2 -> probabilities 1/3 and 2/3
    assert p["a"] == pytest.approx(1 / 3)
    assert p["b"] == pytest.approx(2 / 3)


def test_softmax_sums_to_one_and_ignores_a_constant_shift():
    scores = pd.Series({"a": 0.3, "b": -0.2, "c": 1.1})
    p = softmax(scores)
    assert p.sum() == pytest.approx(1.0)
    assert softmax(scores + 5).tolist() == pytest.approx(p.tolist())


def test_a_lower_temperature_gives_the_leader_more():
    scores = pd.Series({"a": 1.0, "b": 0.0})
    assert softmax(scores, 0.3)["a"] > softmax(scores, 0.6)["a"] > softmax(scores, 1.2)["a"]


def test_round_probs_sums_to_exactly_one():
    rounded = round_probs(pd.Series({"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}))
    # each rounds down to 333333; the one leftover unit goes to the first of the equal remainders
    assert (rounded * 1_000_000).round().astype(int).tolist() == [333334, 333333, 333333]
    assert (rounded * 1_000_000).round().sum() == 1_000_000


def test_round_probs_gives_leftover_units_to_the_largest_remainders():
    rounded = round_probs(pd.Series({"a": 0.5000004, "b": 0.4999996}), decimals=6)
    # floors are 500000 and 499999; the leftover unit goes to b, whose remainder (0.6) is larger than a's (0.4)
    assert (rounded * 1_000_000).round().astype(int).tolist() == [500000, 500000]


def test_round_probs_always_sums_to_one_and_stays_close():
    rng = random.Random(1)
    for _ in range(200):
        raw = pd.Series([rng.random() ** 3 for _ in range(30)], index=[f"T{i}" for i in range(30)])
        probs = raw / raw.sum()
        rounded = round_probs(probs)
        assert round((rounded * 1_000_000).sum()) == 1_000_000
        assert (rounded - probs).abs().max() <= 1.000001e-6


def test_rank_teams_orders_by_probability_then_composite():
    probs = pd.Series({"a": 0.2, "b": 0.5, "c": 0.2, "d": 0.1})
    composite = pd.Series({"a": 1.0, "b": 2.0, "c": 1.5, "d": 0.0})
    ranks = rank_teams(probs, composite)
    assert ranks.to_dict() == {"b": 1, "c": 2, "a": 3, "d": 4}  # a and c tie on probability; c has the higher composite
