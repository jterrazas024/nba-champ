"""End-to-end tests for playoff mode in build_odds (SPEC.md section 6.6). Synthetic league; no network."""
import copy
import json
from datetime import date

import pytest

from pipeline import build_odds, config
from pipeline.build_odds import build, check_before_publishing, run
from pipeline.fetch import Blocked
from pipeline.parse import ParseError
from pipeline.validate import validate_odds
from tests.synthetic import FakeClient, league_pages, playoff_league_pages
from tests.test_playoffs import (EAST, FINALS_START, WEST, full_postseason, playin_rows, round_one_rows, series_rows)
from tests.synthetic import seeded

IDS = [t.id for t in config.TEAMS]


def playoff_build(rows, today=date(2027, 4, 13), previous=None):
    return build(FakeClient(playoff_league_pages(rows)), today, previous).odds


@pytest.fixture(scope="module")
def regular_season_file():
    """A regular-season file (softmax headline), the 'previous' file on the transition day."""
    return build(FakeClient(league_pages(games=24)), date(2026, 11, 8), None).odds


@pytest.fixture(scope="module")
def after_game_82(regular_season_file):
    return playoff_build([], date(2027, 4, 13), regular_season_file)


def test_the_regular_season_ending_switches_to_playoff_mode_not_a_no_op(after_game_82):
    odds = after_game_82
    assert odds is not None and validate_odds(odds) == []
    assert odds["phase"] == "playin" and odds["headline_source"] == "simulation"
    assert odds["sim"]["mode"] == "playin"
    assert all(t["title_prob"] == t["sim_prob"] for t in odds["teams"])
    assert sum(t["title_prob"] for t in odds["teams"]) == pytest.approx(1.0, abs=1e-9)


def test_teams_that_missed_the_playoffs_have_exactly_zero_and_a_note(after_game_82):
    out = [t for t in after_game_82["teams"] if t["status"] == "eliminated"]
    assert len(out) == 10
    assert all(t["title_prob"] == 0 and t["notes"][0] == "Missed the playoffs" for t in out)
    assert all("softmax_prob" in t for t in after_game_82["teams"])        # the old number stays for comparison


def test_the_transition_day_passes_the_guard_and_has_no_misleading_change(regular_season_file, after_game_82):
    previous = copy.deepcopy(regular_season_file)
    previous["teams"][0]["title_prob"] = 0.6          # a 45+ point swing would stop a regular-season update
    assert check_before_publishing(after_game_82, previous) == []
    assert all(t["prob_change_1d"] is None for t in after_game_82["teams"])      # model switch, not a real move
    assert len(after_game_82["history_dates"]) == 2                              # history still carries over


def test_the_guard_still_applies_to_the_regular_season(regular_season_file):
    nxt = copy.deepcopy(regular_season_file)
    nxt["teams"][0]["title_prob"] += 0.2
    assert any("moved" in p for p in check_before_publishing(nxt, regular_season_file))


def test_a_team_that_was_out_cannot_come_back(after_game_82):
    nxt = copy.deepcopy(after_game_82)
    dead = next(t for t in nxt["teams"] if t["status"] == "eliminated")
    nxt["teams"][0]["title_prob"] -= 0.01
    dead["title_prob"] = dead["sim_prob"] = 0.01
    assert any("eliminated in the previous update" in p for p in check_before_publishing(nxt, after_game_82))


def test_mid_playoffs_conditions_on_the_series_played():
    rows = playin_rows(EAST)[0] + playin_rows(WEST)[0] + round_one_rows(EAST, {0: (4, 0)}) + round_one_rows(WEST, {0: (0, 4)})
    odds = playoff_build(rows, date(2027, 4, 30))
    assert validate_odds(odds) == [] and odds["phase"] == "playoffs"
    by_id = {t["id"]: t for t in odds["teams"]}
    east8, west1 = seeded(EAST, 8), seeded(WEST, 1)
    assert by_id[west1]["title_prob"] == 0 and by_id[west1]["notes"][0] == "Lost in the first round"
    assert by_id[east8]["status"] != "eliminated" or by_id[east8]["title_prob"] == 0
    assert by_id[seeded(WEST, 8)]["title_prob"] > 0                      # the 8 seed that won
    assert sum(t["title_prob"] for t in odds["teams"]) == pytest.approx(1.0, abs=1e-9)


@pytest.fixture(scope="module")
def frozen():
    rows, east, west = full_postseason()
    rows += series_rows(east, west, 4, 2, FINALS_START)
    previous = playoff_build(rows[:-6], date(2027, 6, 12))        # finals not finished the day before
    odds = playoff_build(rows, date(2027, 6, 23), previous)
    return odds, previous, east


def test_once_there_is_a_champion_the_file_freezes_and_validates(frozen):
    odds, previous, east = frozen
    assert validate_odds(odds) == [] and odds["phase"] == "complete" and odds["sim"]["mode"] == "complete"
    by_id = {t["id"]: t for t in odds["teams"]}
    assert by_id[east]["title_prob"] == 1.0 and by_id[east]["status"] == "champion" and by_id[east]["rank"] == 1
    assert by_id[east]["notes"][0] == "Won the championship"
    assert sum(1 for t in odds["teams"] if t["title_prob"] == 0) == 29
    assert check_before_publishing(odds, previous) == []             # a 50+ point jump is legitimate here


def test_the_frozen_file_keeps_building_the_next_day(frozen):
    odds, _, east = frozen
    rows, east2, west2 = full_postseason()
    rows += series_rows(east2, west2, 4, 2, FINALS_START)
    again = playoff_build(rows, date(2027, 6, 24), odds)
    assert validate_odds(again) == [] and check_before_publishing(again, odds) == []
    assert next(t for t in again["teams"] if t["id"] == east)["title_prob"] == 1.0
    assert all(t["prob_change_1d"] in (0, 0.0, None) for t in again["teams"])      # nothing moved


def test_run_writes_the_frozen_file(tmp_path, frozen):
    rows, east, west = full_postseason()
    rows += series_rows(east, west, 4, 2, FINALS_START)
    out = tmp_path / "odds.json"
    run(FakeClient(playoff_league_pages(rows)), date(2027, 6, 23), out)
    assert validate_odds(json.loads(out.read_text())) == []


def test_a_june_page_that_does_not_exist_yet_does_not_stop_the_build():
    class NoJune(FakeClient):
        def get_html(self, path, cache_name):
            if "june" in path:
                from pipeline.fetch import FetchError
                raise FetchError("HTTP 404")
            return super().get_html(path, cache_name)

    rows, east, west = full_postseason()
    odds = build(NoJune(playoff_league_pages(rows)), date(2027, 6, 2), None).odds
    assert validate_odds(odds) == [] and odds["phase"] == "playoffs"


def test_a_block_while_reading_the_postseason_fails_the_build():
    class Blocking(FakeClient):
        def get_html(self, path, cache_name):
            if "may" in path:
                raise Blocked("blocked")
            return super().get_html(path, cache_name)

    with pytest.raises(Blocked):
        build(Blocking(playoff_league_pages([])), date(2027, 5, 2), None)


def test_games_that_do_not_fit_a_bracket_fail_the_build_instead_of_publishing_a_guess():
    rows = playin_rows(EAST)[0] + playin_rows(WEST)[0] + round_one_rows(EAST)
    rows += series_rows(seeded(EAST, 1), seeded(EAST, 3), 1, 0, date(2027, 5, 3))
    with pytest.raises(ParseError):
        playoff_build(rows, date(2027, 5, 4))


def test_before_the_season_nothing_is_built_and_playoff_mode_needs_all_82_games():
    assert build(FakeClient(league_pages(games=0)), date(2026, 11, 8), None).odds is None
    odds = build(FakeClient(league_pages(games=24)), date(2026, 11, 8), None).odds     # not all 82 games: softmax headline
    assert odds["headline_source"] == "softmax" and odds["phase"] == "regular_season" and "status" not in odds["teams"][0]
