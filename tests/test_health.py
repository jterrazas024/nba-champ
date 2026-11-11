"""Tests for injury and return timelines (SPEC.md section 6.7): date reading, playoff credit, the team
adjustment, and the on/off switch (config.INJURY_TIMELINES_ENABLED)."""
from datetime import date

import pandas as pd
import pytest

from pipeline import build_odds, config, health
from pipeline.build_odds import build
from pipeline.parse import parse_injuries, parse_return_info
from pipeline.validate import validate_odds
from tests.synthetic import FakeClient, TEAMS, league_pages, player_id, table

UPDATE = date(2026, 10, 16)
TODAY = date(2026, 11, 10)
END_YEAR = 2027


# ---------- reading a return date from the text ----------

@pytest.mark.parametrize("text,status,category,expected", [
    ("Out (Knee) - could return sometime between January and February as he recovers", "Out", "dated", date(2027, 1, 30)),
    ("Out (Knee) - expected to return on Jan. 15 after surgery", "Out", "dated", date(2027, 1, 15)),
    ("Out (Knee) - out until Dec 3 with a sprain", "Out", "dated", date(2026, 12, 3)),
    ("Out (Foot) - he will be back in late March", "Out", "dated", date(2027, 3, 24)),
    ("Out (Ankle) - will miss approximately 4-6 weeks", "Out", "duration", date(2026, 11, 20)),
    ("Out (Knee) - will be re-evaluated in two weeks", "Out", "duration", date(2026, 11, 6)),       # 14 days + 7 buffer
    ("Out (Hamstring) - will miss at least 4 weeks", "Out", "duration", date(2026, 11, 20)),        # 28 x 1.25 = 35 days
    ("Out (Knee) - expected to miss up to two months", "Out", "duration", date(2026, 12, 15)),
    ("Out (Foot) - to be re-evaluated in 3-4 months", "Out", "duration", date(2027, 2, 5)),          # 105 days + 7
])
def test_return_dates_are_read_from_the_text(text, status, category, expected):
    when, kind, estimated = parse_return_info(text, UPDATE, status)
    assert (when, kind, estimated) == (expected, category, False), (when, kind)


def test_several_months_is_a_fixed_estimate():
    when, kind, estimated = parse_return_info("Out (Leg) - expected to be sidelined for several months", UPDATE, "Out")
    assert (when, kind, estimated) == (date(2027, 2, 13), "duration", True)


@pytest.mark.parametrize("text,status", [
    ("Out For Season (ACL) - torn right ACL", "Out For Season"),
    ("Out (Achilles) - will miss the 2026-27 season", "Out"),
    ("Out (Knee) - out for the rest of the season", "Out"),
])
def test_season_ending_injuries_have_no_return_date(text, status):
    assert parse_return_info(text, UPDATE, status) == (None, "season", False)


@pytest.mark.parametrize("text,status,category,days", [
    ("Out (Illness) - without a timetable for a return", "Out", "indefinite", 75),
    ("Out (Hip) - he is out indefinitely", "Out", "indefinite", 75),
    ("Out (Knee) - listed as week-to-week", "Out", "week_to_week", 21),
    ("Day To Day (Ankle) - did not play on Friday's (Oct. 16) game", "Day To Day", "day_to_day", 4),
    ("Out (Ankle) - Capela is OUT for Saturday's (Oct. 17) game", "Out", "single_game", 2),
    ("Out (Back) - sore", "Out", "unknown", 35),          # nothing usable in the text: the fixed prior
])
def test_without_a_date_a_fixed_prior_is_used(text, status, category, days):
    when, kind, estimated = parse_return_info(text, UPDATE, status)
    assert kind == category and estimated is True
    assert (when - UPDATE).days == days == config.INJURY_PRIOR_DAYS[category]


def test_a_date_just_before_the_update_is_the_injury_not_the_return():
    # "did not return on Friday's (Nov. 6) game": Nov 6 is the past, not a return date
    when, kind, _ = parse_return_info("Day To Day (Groin) - did not return on Friday's (Nov. 6) game", date(2026, 11, 7), "Day To Day")
    assert kind == "day_to_day" and when == date(2026, 11, 11)


def test_parse_injuries_adds_the_return_columns():
    page = table("injuries", ["Player", "Team", "Update", "Description"], [
        ['<a href="/players/b/butleji01.html">Jimmy Butler</a>', "Golden State Warriors", "Fri, Oct 16, 2026",
         "Out (Knee) - could return sometime between January and February"],
        ['<a href="/players/s/smithjo01.html">Jo Smith</a>', "Boston Celtics", "Fri, Oct 16, 2026", "Out For Season (ACL) - torn ACL"],
    ])
    injuries = parse_injuries(page)
    assert injuries["expected_return"].tolist() == [date(2027, 1, 30), None]
    assert injuries["return_category"].tolist() == ["dated", "season"]
    assert injuries["return_estimated"].tolist() == [False, False]


# ---------- the playoff-health logic ----------

def credit(ret, category="dated", today=TODAY):
    return health.playoff_credit(ret, category, today, END_YEAR)


def test_out_for_the_season_is_zero_credit():
    assert credit(None, "season") == 0.0


def test_back_well_before_the_playoffs_costs_little():
    assert credit(date(2027, 1, 30)) == config.HEALTH_CREDIT_CEILING   # fully back: as available as an average healthy player
    assert credit(date(2026, 11, 20)) >= credit(date(2027, 2, 15)) > credit(date(2027, 3, 30))   # later return, less credit


def test_credit_is_capped_and_discounted_when_the_return_is_a_guess():
    assert credit(date(2026, 12, 1), "day_to_day") == config.HEALTH_CREDIT_CEILING
    assert credit(date(2026, 12, 1), "unknown") < credit(date(2026, 12, 1), "dated")
    assert credit(date(2026, 12, 1), "unknown") <= config.HEALTH_CREDIT_CEILING


def test_back_just_before_the_playoffs_gets_partial_credit():
    assert 0.35 < credit(date(2027, 4, 10)) < 0.7


def test_back_during_the_playoffs_fades_to_nothing():
    early = credit(date(2027, 4, 25))
    late = credit(date(2027, 5, 20))
    assert 0 < early < credit(date(2027, 4, 10))
    assert late == 0.0


def test_credit_never_leaves_zero_to_one():
    for month in range(11, 13):
        for day in (1, 15):
            assert 0.0 <= credit(date(2026, month, day)) <= 1.0
    for ret in (date(2027, m, 1) for m in range(1, 8)):
        assert 0.0 <= credit(ret) <= 1.0


def test_a_past_short_absence_is_healthy_but_a_past_timed_injury_is_overdue():
    assert credit(date(2026, 11, 5), "day_to_day") == config.HEALTH_CREDIT_CEILING
    assert 0.8 < credit(date(2026, 11, 5), "duration") <= config.HEALTH_CREDIT_CEILING   # assumed back in INJURY_OVERDUE_DAYS, still before the playoffs


def test_once_the_playoffs_have_started_credit_is_measured_from_today():
    midway = date(2027, 5, 1)
    assert credit(date(2027, 5, 3), today=midway) > 0.2      # back in 2 days: still most of the playoffs ahead
    assert credit(date(2027, 6, 20), today=midway) == 0.0


# ---------- the team adjustment ----------

def roster_table(rows):
    """rows: (player_id, team_id, g, mp, bpm)"""
    df = pd.DataFrame(rows, columns=["player_id", "team_id", "g", "mp", "bpm"])
    df["player"] = df["player_id"]
    return df


def injury_table(rows):
    """rows: (player_id, team_id, status, update, category, expected_return)"""
    df = pd.DataFrame(rows, columns=["player_id", "team_id", "status", "update", "return_category", "expected_return"])
    df["player"] = df["player_id"]
    df["update"] = pd.to_datetime(df["update"])
    return df


GAMES = pd.Series({"AAA": 10, "BBB": 10})
LAST = roster_table([("star", "AAA", 70, 2400, 6.0), ("other", "BBB", 70, 2400, 6.0)])


def test_a_star_out_through_the_playoffs_costs_the_team():
    now = roster_table([("star", "AAA", 9, 300, 6.0)])
    injuries = injury_table([("star", "AAA", "Out For Season", "2026-11-09", "season", None)])
    result = health.team_health(injuries, now, LAST, GAMES, TODAY, END_YEAR)
    assert result["AAA"].adjustment < -0.2
    assert result["BBB"].adjustment == 0.0


def test_a_star_out_all_year_who_returns_before_the_playoffs_lifts_the_team():
    """The Jimmy Butler case: no minutes this season, back in late January, very good last season."""
    now = roster_table([("star", "AAA", 0, 0, float("nan"))])
    injuries = injury_table([("star", "AAA", "Out", "2026-10-16", "dated", date(2027, 1, 30))])
    result = health.team_health(injuries, now, LAST, GAMES, TODAY, END_YEAR)["AAA"]
    assert result.adjustment > 0.1
    assert result.players[0].credit == config.HEALTH_CREDIT_CEILING and result.notes and "Jan 30" in result.notes[0]


def test_the_same_star_is_worth_less_the_later_he_returns():
    now = roster_table([("star", "AAA", 0, 0, float("nan"))])
    adjustments = []
    for ret in (date(2026, 12, 15), date(2027, 3, 30), date(2027, 4, 25), None):
        category = "dated" if ret else "season"
        injuries = injury_table([("star", "AAA", "Out", "2026-10-16", category, ret)])
        adjustments.append(health.team_health(injuries, now, LAST, GAMES, TODAY, END_YEAR)["AAA"].adjustment)
    assert adjustments == sorted(adjustments, reverse=True)
    assert adjustments[-1] == 0.0 or adjustments[-1] < 0.01   # out all year and still out: already in the ratings


def test_a_short_absence_is_ignored_and_a_stale_day_to_day_entry_too():
    now = roster_table([("star", "AAA", 8, 300, 6.0)])
    short = injury_table([("star", "AAA", "Day To Day", "2026-11-08", "day_to_day", date(2026, 11, 12))])
    stale = injury_table([("star", "AAA", "Day To Day", "2026-10-01", "day_to_day", date(2026, 10, 5))])
    assert health.team_health(short, now, LAST, GAMES, TODAY, END_YEAR)["AAA"].adjustment == 0.0
    assert health.team_health(stale, now, LAST, GAMES, TODAY, END_YEAR)["AAA"].adjustment == 0.0


def test_players_below_replacement_or_off_the_rotation_do_not_count():
    now = roster_table([("scrub", "AAA", 9, 90, -3.0), ("bench", "AAA", 9, 80, 5.0)])   # 10 mpg
    injuries = injury_table([("scrub", "AAA", "Out For Season", "2026-11-01", "season", None),
                             ("bench", "AAA", "Out For Season", "2026-11-01", "season", None)])
    assert health.team_health(injuries, now, None, GAMES, TODAY, END_YEAR)["AAA"].adjustment == 0.0


def test_one_team_cannot_swing_more_than_the_cap():
    rows = [(f"s{i}", "AAA", 9, 320, 9.0) for i in range(5)]
    now = roster_table(rows)
    injuries = injury_table([(f"s{i}", "AAA", "Out For Season", "2026-11-01", "season", None) for i in range(5)])
    assert health.team_health(injuries, now, None, GAMES, TODAY, END_YEAR)["AAA"].adjustment == -config.HEALTH_ADJ_MAX_DOWN
    back = injury_table([(f"s{i}", "AAA", "Out", "2026-10-16", "dated", date(2027, 1, 30)) for i in range(5)])
    gone = roster_table([(f"s{i}", "AAA", 0, 0, float("nan")) for i in range(5)])
    last = roster_table([(f"s{i}", "AAA", 70, 2400, 6.0) for i in range(5)])
    assert health.team_health(back, gone, last, GAMES, TODAY, END_YEAR)["AAA"].adjustment == config.HEALTH_ADJ_MAX_UP


def test_a_player_with_no_stats_anywhere_is_skipped():
    now = roster_table([("someone", "BBB", 9, 300, 1.0)])
    injuries = injury_table([("rookie", "AAA", "Out For Season", "2026-11-01", "season", None)])
    assert health.team_health(injuries, now, LAST, GAMES, TODAY, END_YEAR)["AAA"].adjustment == 0.0


# ---------- the switch and the build ----------

def pages_without_injuries():
    pages = league_pages()
    pages["/friv/injuries.fcgi"] = table("injuries", ["Player", "Team", "Update", "Description"], [
        ["Nobody", TEAMS[0].name, "Sat, Nov 7, 2026", "Day To Day (Ankle) - sore"]])
    return pages


def build_with(flag, monkeypatch, pages):
    monkeypatch.setattr(config, "INJURY_TIMELINES_ENABLED", flag)
    return build(FakeClient(pages), date(2026, 11, 8), None).odds


def test_with_the_flag_off_there_are_no_injury_fields_and_the_old_star_factor_applies(monkeypatch):
    odds = build_with(False, monkeypatch, league_pages())
    assert all("health_adjustment" not in t and "injuries" not in t for t in odds["teams"])
    injured = next(t for t in odds["teams"] if t["id"] == TEAMS[29].id)
    assert any("(star): Out" in n for n in injured["notes"])


def test_with_no_injuries_that_matter_on_and_off_give_identical_odds(monkeypatch):
    off = build_with(False, monkeypatch, pages_without_injuries())
    on = build_with(True, monkeypatch, pages_without_injuries())
    strip = lambda odds: [{k: v for k, v in t.items() if k not in ("health_adjustment", "injuries")} for t in odds["teams"]]   # noqa: E731
    assert strip(on) == strip(off)
    assert all(t["health_adjustment"] == 0.0 and t["injuries"] == [] for t in on["teams"])


def test_with_the_flag_on_an_injured_star_moves_the_odds_and_probabilities_still_sum_to_one(monkeypatch):
    pages = league_pages()
    pages["/friv/injuries.fcgi"] = table("injuries", ["Player", "Team", "Update", "Description"], [
        [f'<a href="/players/x/{player_id(TEAMS[29], 0)}.html">{TEAMS[29].id} Player 0</a>', TEAMS[29].name,
         "Sat, Nov 7, 2026", "Out For Season (Knee) - torn ACL"]])
    off = build_with(False, monkeypatch, pages)
    on = build_with(True, monkeypatch, pages)
    assert validate_odds(on) == [] and validate_odds(off) == []
    assert abs(sum(t["title_prob"] for t in on["teams"]) - 1.0) < 1e-9
    assert abs(sum(t["sim_prob"] for t in on["teams"]) - 1.0) < 1e-9
    team = next(t for t in on["teams"] if t["id"] == TEAMS[29].id)
    assert team["health_adjustment"] != 0 and team["injuries"][0]["player"] == f"{TEAMS[29].id} Player 0"
    assert team["health_adjustment"] < 0 and team["injuries"][0]["expected_return"] is None
    contributions = sum(i["contribution"] for i in team["inputs"].values() if i)
    assert team["composite"] == pytest.approx(contributions + team["health_adjustment"], abs=0.003)   # the strongest team has no penalties


def test_the_published_file_stays_small(monkeypatch):
    import json
    on = build_with(True, monkeypatch, league_pages())
    assert len(json.dumps(on, indent=1).encode()) < 100 * 1024


def test_the_validator_rejects_a_malformed_injuries_entry(monkeypatch):
    on = build_with(True, monkeypatch, league_pages())
    on["teams"][0]["injuries"] = [{"player": "X", "status": "Out", "expected_return": "soon", "playoff_credit": 0.5, "adjustment": 0.1}]
    assert any("malformed injuries" in p for p in validate_odds(on))


def test_the_module_exposes_the_flag_default():
    assert config.INJURY_TIMELINES_ENABLED is True
    assert build_odds.health is health
