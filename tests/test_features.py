"""Tests for the model inputs. Expected values are worked out by hand in the comments."""
from datetime import date

import pandas as pd
import pytest

from pipeline import config
from pipeline.features import (current_roster, net_rating, recent_form, rotation_availability,
                               season_games, star_availability, star_power, team_games)

TODAY = date(2026, 11, 8)


def ratings(rows):
    """rows: (team_id, games, nrtg, sos)"""
    return pd.DataFrame(rows, columns=["team_id", "games", "nrtg", "sos"])


def roster(rows):
    """rows: (player_id, team_id, g, mp, bpm)"""
    df = pd.DataFrame(rows, columns=["player_id", "team_id", "g", "mp", "bpm"])
    df["player"] = df["player_id"].str.upper()
    return df


def injuries(rows):
    """rows: (player_id, status, 'YYYY-MM-DD')"""
    df = pd.DataFrame(rows, columns=["player_id", "status", "update"])
    df["update"] = pd.to_datetime(df["update"])
    return df


# ---------- net rating ----------

def test_net_rating_blends_current_and_prior_early_in_the_season():
    now = ratings([("A", 10, 10.0, 4.0), ("B", 10, -10.0, -4.0)])
    last = ratings([("A", 82, 6.0, 0.0), ("B", 82, -6.0, 0.0)])
    result = net_rating(now, last)
    # current = nrtg + sos * (10/40) = 10 + 1 = 11.   prior = 0.7 * 6 = 4.2.   w = 10/30 = 1/3
    # value = 11/3 + 4.2 * 2/3 = 3.6667 + 2.8 = 6.4667
    assert result["A"] == pytest.approx(6.46667, abs=1e-4)
    assert result["B"] == pytest.approx(-6.46667, abs=1e-4)


def test_net_rating_with_no_games_is_the_prior_alone():
    now = ratings([("A", 0, 0.0, 0.0), ("B", 0, 0.0, 0.0)])
    last = ratings([("A", 82, 6.0, 0.0), ("B", 82, -6.0, 0.0)])
    assert net_rating(now, last)["A"] == pytest.approx(4.2)


def test_net_rating_phases_in_schedule_strength_fully_by_40_games():
    now = ratings([("A", 40, 10.0, 4.0), ("B", 40, -10.0, -4.0)])
    last = ratings([("A", 82, 6.0, 0.0), ("B", 82, -6.0, 0.0)])
    # current = 10 + 4 = 14.   w = 40/60 = 2/3.   value = 14 * 2/3 + 4.2 * 1/3 = 9.3333 + 1.4
    assert net_rating(now, last)["A"] == pytest.approx(10.7333, abs=1e-4)


def test_prior_is_measured_from_the_league_mean():
    now = ratings([("A", 0, 0.0, 0.0), ("B", 0, 0.0, 0.0)])
    last = ratings([("A", 82, 8.0, 0.0), ("B", 82, -4.0, 0.0)])  # mean is 2, so A is 6 above it
    assert net_rating(now, last)["A"] == pytest.approx(0.7 * 6)
    assert net_rating(now, last)["B"] == pytest.approx(0.7 * -6)


def test_net_rating_without_a_prior_uses_the_current_value():
    now = ratings([("A", 10, 10.0, 4.0)])
    assert net_rating(now, None)["A"] == pytest.approx(11.0)


# ---------- roster and season games ----------

def player_rows():
    cols = ["player_id", "player", "team_id", "is_total", "g", "mp", "bpm"]
    return pd.DataFrame([
        ("h1", "Harden", None, True, 70, 2400, 3.9),     # total row for a traded player
        ("h1", "Harden", "LAC", False, 44, 1559, 3.8),
        ("h1", "Harden", "CLE", False, 26, 841, 4.0),
        ("s1", "Single", "BOS", False, 50, 1500, 1.0),
    ], columns=cols)


def test_traded_player_uses_total_numbers_and_his_latest_team():
    roster_ = current_roster(player_rows()).set_index("player_id")
    assert roster_.loc["h1", "team_id"] == "CLE"
    assert (roster_.loc["h1", "g"], roster_.loc["h1", "mp"], roster_.loc["h1", "bpm"]) == (70, 2400, 3.9)
    assert roster_.loc["s1", "team_id"] == "BOS"


def test_season_games_uses_the_total_row_for_traded_players():
    games = season_games(player_rows())
    assert games["h1"] == 70 and games["s1"] == 50


def test_team_games_is_indexed_by_team():
    assert team_games(ratings([("A", 9, 0, 0)]))["A"] == 9


# ---------- star power ----------

TEAM_G = pd.Series({"T": 10})  # 10 games played: a star needs at least max(3, 0.4 * 10) = 4 of them


def test_star_power_is_weighted_best_two_bpm():
    team = roster([
        ("a", "T", 10, 300, 8.0),    # 30 mpg
        ("b", "T", 10, 200, 6.0),    # 20 mpg
        ("c", "T", 2, 100, 12.0),    # only 2 games: not enough
        ("d", "T", 10, 100, 10.0),   # only 10 mpg: below the 15 minute rule
    ])
    result = star_power(team, TEAM_G)["T"]
    assert result.value == pytest.approx(0.6 * 8.0 + 0.4 * 6.0)  # 7.2
    assert [s.player_id for s in result.stars] == ["a", "b"]
    assert result.notes == ()


def test_star_power_relaxes_the_minutes_rule_to_fill_a_missing_slot():
    team = roster([("a", "T", 10, 300, 8.0), ("d", "T", 10, 100, 10.0), ("c", "T", 2, 100, 12.0)])
    result = star_power(team, TEAM_G)["T"]
    # a qualifies; d fills the second slot despite 10 mpg. Best BPM first: d (10), a (8).
    assert result.value == pytest.approx(0.6 * 10.0 + 0.4 * 8.0)  # 9.2
    assert any("relaxed" in n for n in result.notes)


def test_star_power_with_one_qualifier_is_his_bpm_alone():
    team = roster([("a", "T", 10, 300, 8.0), ("c", "T", 2, 100, 12.0)])
    result = star_power(team, TEAM_G)["T"]
    assert result.value == pytest.approx(8.0)
    assert any("one qualifying star" in n for n in result.notes)


def test_star_power_is_none_when_nobody_qualifies():
    team = roster([("c", "T", 2, 100, 12.0)])
    assert star_power(team, TEAM_G)["T"].value is None


def test_star_power_ties_go_to_the_player_with_more_minutes():
    team = roster([("a", "T", 10, 300, 5.0), ("b", "T", 10, 200, 5.0), ("c", "T", 10, 250, 5.0)])
    assert [s.player_id for s in star_power(team, TEAM_G)["T"].stars] == ["a", "c"]


def test_the_games_requirement_never_exceeds_the_games_played():
    team = roster([("a", "T", 2, 80, 4.0), ("b", "T", 2, 70, 2.0)])
    assert star_power(team, pd.Series({"T": 2}))["T"].value == pytest.approx(0.6 * 4.0 + 0.4 * 2.0)


# ---------- star availability ----------

def stars_for(team, *players):
    """A star_power() result built from (player_id, bpm) pairs on a roster that qualifies them all."""
    rows = [(pid, team, 10, 300, bpm) for pid, bpm in players]
    return star_power(roster(rows), pd.Series({team: 10}))


def games_of(**by_player):
    return pd.Series(by_player, dtype=float)


NO_INJURIES = pd.DataFrame({"player_id": [], "status": [], "update": pd.Series([], dtype="datetime64[ns]")})


def test_rookie_two_years_ago_skips_seasons_before_his_first_game():
    stars = stars_for("T", ("p", 5.0))
    seasons = [games_of(p=9), games_of(p=41), games_of()]  # this season, last season, the season before
    result = star_availability(stars, seasons, TEAM_G, NO_INJURIES, TODAY)["T"]
    # this season 9/10 = 0.9, last season 41/82 = 0.5; the year before was before his first game.
    # (0.5 * 0.9 + 0.3 * 0.5) / (0.5 + 0.3) = 0.6 / 0.8 = 0.75
    assert result.value == pytest.approx(0.75)


def test_a_missed_season_after_his_first_counts_as_zero():
    stars = stars_for("T", ("p", 5.0))
    seasons = [games_of(p=9), games_of(), games_of(p=82)]
    result = star_availability(stars, seasons, TEAM_G, NO_INJURIES, TODAY)["T"]
    assert result.value == pytest.approx(0.5 * 0.9 + 0.3 * 0.0 + 0.2 * 1.0)  # 0.65


def test_a_rookie_this_season_uses_only_this_season():
    stars = stars_for("T", ("p", 5.0))
    seasons = [games_of(p=9), games_of(), games_of()]
    assert star_availability(stars, seasons, TEAM_G, NO_INJURIES, TODAY)["T"].value == pytest.approx(0.9)


def test_team_availability_averages_its_two_stars():
    stars = stars_for("T", ("p", 5.0), ("q", 4.0))
    seasons = [games_of(p=10, q=5), games_of(), games_of()]
    assert star_availability(stars, seasons, TEAM_G, NO_INJURIES, TODAY)["T"].value == pytest.approx((1.0 + 0.5) / 2)


@pytest.mark.parametrize("status,date_text,expected", [
    ("Out", "2026-11-01", 0.9 * 0.6),
    ("Out For Season", "2026-09-01", 0.9 * 0.25),
    ("Day To Day", "2026-11-06", 0.9 * 0.9),   # 2 days old: counts
    ("Day To Day", "2026-10-15", 0.9),         # 24 days old: stale, ignored
    ("Probable", "2026-11-07", 0.9),            # not a status we adjust for
])
def test_injury_status_scales_availability(status, date_text, expected):
    stars = stars_for("T", ("p", 5.0))
    seasons = [games_of(p=9), games_of(), games_of()]
    result = star_availability(stars, seasons, TEAM_G, injuries([("p", status, date_text)]), TODAY)["T"]
    assert result.value == pytest.approx(expected)


def test_only_the_latest_injury_entry_counts():
    stars = stars_for("T", ("p", 5.0))
    seasons = [games_of(p=9), games_of(), games_of()]
    log = injuries([("p", "Out", "2026-10-01"), ("p", "Day To Day", "2026-11-06")])
    assert star_availability(stars, seasons, TEAM_G, log, TODAY)["T"].value == pytest.approx(0.9 * 0.9)


def test_injury_adds_a_note_naming_the_star():
    stars = stars_for("T", ("p", 5.0))
    seasons = [games_of(p=9), games_of(), games_of()]
    result = star_availability(stars, seasons, TEAM_G, injuries([("p", "Out", "2026-11-01")]), TODAY)["T"]
    assert result.notes == ("P (star): Out",)


def test_team_without_stars_has_no_availability():
    stars = star_power(roster([("c", "T", 2, 100, 12.0)]), TEAM_G)
    assert star_availability(stars, [games_of()] * 3, TEAM_G, NO_INJURIES, TODAY)["T"].value is None


# ---------- rotation availability ----------

def test_rotation_availability_is_minutes_weighted():
    team = roster([("a", "T", 10, 100, 0.0), ("b", "T", 5, 100, 0.0)])
    # (100 * 10/10 + 100 * 5/10) / 200 = 0.75
    assert rotation_availability(team, TEAM_G)["T"] == pytest.approx(0.75)


def test_rotation_availability_only_counts_the_top_nine_by_minutes():
    rows = [(f"p{i}", "T", 10, 100, 0.0) for i in range(9)] + [("tenth", "T", 0, 50, 0.0)]
    assert rotation_availability(roster(rows), TEAM_G)["T"] == pytest.approx(1.0)  # the 10th (0 games) is left out


def test_a_traded_players_share_is_capped_at_one():
    team = roster([("a", "T", 12, 100, 0.0)])  # 12 games across two teams, this team has played 10
    assert rotation_availability(team, TEAM_G)["T"] == pytest.approx(1.0)


def test_rotation_availability_is_none_for_a_team_with_no_players():
    assert rotation_availability(roster([("a", "OTHER", 10, 100, 0.0)]), TEAM_G)["T"] is None


# ---------- recent form ----------

def games(rows):
    """rows: (date, away_id, away_pts, home_id, home_pts)"""
    df = pd.DataFrame(rows, columns=["date", "away_id", "away_pts", "home_id", "home_pts"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def test_recent_form_uses_only_the_last_20_games():
    days = pd.date_range("2026-10-20", periods=25).strftime("%Y-%m-%d")
    rows = []
    for i, day in enumerate(days):
        margin = -100 if i < 5 else 10            # five awful games, then twenty wins by 10
        rows.append((day, "B", 100, "A", 100 + margin) if i % 2 == 0 else (day, "A", 100 + margin, "B", 100))
    result = recent_form(games(rows), ["A", "B"])
    assert result["A"] == pytest.approx(10.0)       # the first five games are outside the window
    assert result["B"] == pytest.approx(-10.0)


def test_recent_form_needs_at_least_five_games():
    rows = [("2026-10-2%d" % i, "B", 100, "A", 110) for i in range(4)]
    assert recent_form(games(rows), ["A"])["A"] is None


def test_recent_form_is_none_for_a_team_with_no_games():
    assert recent_form(games([("2026-10-20", "B", 100, "A", 110)]), ["Z"])["Z"] is None
