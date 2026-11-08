"""Tests for reading the postseason (pipeline/playoffs.py) and for the simulator's play-in / series state.
Synthetic league only (tests/synthetic.py); no network."""
from datetime import date

import numpy as np
import pytest

from pipeline import config, playoffs, sim
from pipeline.fetch import Blocked, FetchError
from pipeline.parse import ParseError, parse_ratings
from tests.synthetic import postseason_pages, playin_row, ratings_page, seeded, series_rows, sim_league

IDS = [t.id for t in config.TEAMS]
INDEX = {t: i for i, t in enumerate(IDS)}
RATINGS = parse_ratings(ratings_page(82))
R1_START, R2_START, R3_START, FINALS_START = date(2027, 4, 17), date(2027, 5, 3), date(2027, 5, 19), date(2027, 6, 5)
EAST, WEST = 0, 1


def read(rows):
    """Read the postseason out of the synthetic pages holding `rows`."""
    pages = postseason_pages(rows)
    games = playoffs.parse_postseason_pages({m: pages[f"/leagues/NBA_2027_games-{m}.html"] for m in ("april", "may", "june")})
    return playoffs.read_postseason(games, RATINGS, IDS)


def playin_rows(c, g1_home_wins=True, g2_home_wins=True, g3_home_wins=True, games=(1, 2, 3)):
    """The play-in of conference c (seeds 7-10 by standing). Returns (rows, seed 7, seed 8) after the play-in."""
    s7, s8, s9, s10 = (seeded(c, n) for n in (7, 8, 9, 10))
    loser1, survivor = (s8 if g1_home_wins else s7), (s9 if g2_home_wins else s10)
    rows = []
    if 1 in games:
        rows.append(playin_row(date(2027, 4, 14), s7, s8, g1_home_wins))
    if 2 in games:
        rows.append(playin_row(date(2027, 4, 14), s9, s10, g2_home_wins))
    if 3 in games:
        rows.append(playin_row(date(2027, 4, 16), loser1, survivor, g3_home_wins))
    seed8 = loser1 if g3_home_wins else survivor
    return rows, (s7 if g1_home_wins else s8), seed8


def bracket(c):
    """The eight seeds of conference c when the standings hold and the favorites win the play-in."""
    _, s7, s8 = playin_rows(c)
    return [seeded(c, n) for n in range(1, 7)] + [s7, s8]


def round_one_rows(c, scores=None):
    """Round-1 games for conference c. `scores` maps slot -> (higher seed wins, lower seed wins); default 4-1."""
    seeds = bracket(c)
    rows = []
    for slot, (i, j) in enumerate(sim.R1_PAIRS):
        hw, lw = (scores or {}).get(slot, (4, 1))
        rows += series_rows(seeds[i], seeds[j], hw, lw, R1_START)
    return rows


def full_postseason():
    """Every game up to the conference finals, the better seed winning each series 4-1. Returns (rows, east champion, west champion)."""
    rows, champs = [], []
    for c in (EAST, WEST):
        seeds = bracket(c)
        rows += playin_rows(c)[0] + round_one_rows(c)
        rows += series_rows(seeds[0], seeds[3], 4, 1, R2_START)       # round 1 winners: seeds 1, 4, 3, 2
        rows += series_rows(seeds[1], seeds[2], 4, 1, R2_START)
        rows += series_rows(seeds[0], seeds[1], 4, 1, R3_START)
        champs.append(seeds[0])
    return rows, champs[0], champs[1]


# ---------- reading the stage of each page ----------

def test_may_and_june_pages_have_no_marker_but_are_all_playoffs():
    rows = series_rows(seeded(EAST, 1), seeded(EAST, 8), 1, 0, date(2027, 5, 3)) + series_rows(seeded(WEST, 1), seeded(WEST, 8), 1, 0, date(2027, 6, 5))
    pages = postseason_pages(rows)
    games = playoffs.parse_postseason_pages({m: pages[f"/leagues/NBA_2027_games-{m}.html"] for m in ("april", "may", "june")})
    assert set(games[games["date"] >= "2027-05-01"]["stage"]) == {"playoffs"}
    assert set(games[games["date"] < "2027-05-01"]["stage"]) == {"regular"}      # the April page's finished regular games


def test_postseason_months_arrive_as_the_calendar_does():
    assert playoffs.postseason_months(date(2027, 4, 13)) == ("april",)
    assert playoffs.postseason_months(date(2027, 5, 2)) == ("april", "may")
    assert playoffs.postseason_months(date(2027, 6, 1)) == ("april", "may", "june")
    assert playoffs.postseason_months(date(2027, 7, 15)) == ("april", "may", "june")


class Pages:
    """A client that serves pages by path and fails with FetchError (or Blocked) for chosen ones."""

    def __init__(self, pages, missing=(), blocked=()):
        self.pages, self.missing, self.blocked, self.asked = pages, set(missing), set(blocked), []

    def get_html(self, path, cache_name):
        self.asked.append(cache_name)
        if any(m in path for m in self.blocked):
            raise Blocked("blocked")
        if any(m in path for m in self.missing):
            raise FetchError("HTTP 404")
        return self.pages[path]


def test_a_month_page_that_does_not_exist_yet_counts_as_no_games():
    client = Pages(postseason_pages(playin_rows(EAST)[0]), missing=["june"])
    games = playoffs.fetch_postseason(client, 2027, date(2027, 6, 2))
    assert client.asked == ["games_2027_april", "games_2027_may", "games_2027_june"]
    assert (games["stage"] == "playin").sum() == 3


def test_a_missing_april_page_or_a_block_is_an_error():
    pages = postseason_pages([])
    with pytest.raises(FetchError):
        playoffs.fetch_postseason(Pages(pages, missing=["april"]), 2027, date(2027, 5, 2))
    with pytest.raises(Blocked):
        playoffs.fetch_postseason(Pages(pages, blocked=["may"]), 2027, date(2027, 5, 2))


# ---------- the play-in ----------

def test_right_after_game_82_the_play_in_teams_are_the_seventh_to_tenth():
    post = read([])
    assert post.phase == "playin" and post.champion is None
    for c in (EAST, WEST):
        assert post.state.seeds[c] == [INDEX[seeded(c, n)] for n in range(1, 7)]
        assert post.state.playin[c] == [INDEX[seeded(c, n)] for n in range(7, 11)]
    assert post.state.playin_results == {} and post.state.scores == {}
    assert len(post.alive(IDS)) == 20
    assert all(post.out[t] == playoffs.MISSED for t in IDS if t not in post.alive(IDS))


def test_play_in_games_played_so_far_are_results_and_the_losers_of_game_2_are_out():
    rows = playin_rows(EAST, g2_home_wins=False, games=(1, 2))[0] + playin_rows(WEST, games=(1, 2))[0]
    post = read(rows)
    assert post.phase == "playin"
    s7, s8, s9, s10 = (seeded(EAST, n) for n in (7, 8, 9, 10))
    assert post.state.playin_results[(EAST, 1)] == INDEX[s7] and post.state.playin_results[(EAST, 2)] == INDEX[s10]
    assert post.out[s9] == playoffs.LOST_PLAYIN
    assert s8 not in post.out                                      # the loser of game 1 still has game 3
    assert len(post.alive(IDS)) == 18


def test_only_the_first_play_in_game_listed_still_tells_which_game_it_is():
    post = read([playin_row(date(2027, 4, 14), seeded(WEST, 7), seeded(WEST, 8), False)])
    assert post.state.playin_results == {(WEST, 1): INDEX[seeded(WEST, 8)]}
    assert post.state.playin[WEST][:2] == [INDEX[seeded(WEST, 7)], INDEX[seeded(WEST, 8)]]


def test_an_unplayed_play_in_game_is_not_a_result():
    rows = [playin_row(date(2027, 4, 14), seeded(EAST, 7), seeded(EAST, 8), None)]
    assert read(rows).state.playin_results == {}


def test_a_finished_play_in_gives_eight_seeds_and_the_last_two_are_the_winners():
    rows = playin_rows(EAST, g1_home_wins=False, g3_home_wins=False)[0] + playin_rows(WEST)[0]
    post = read(rows)
    s7, s8, s9, s10 = (seeded(EAST, n) for n in (7, 8, 9, 10))
    assert EAST not in post.state.playin and WEST not in post.state.playin
    assert post.state.seeds[EAST][6:] == [INDEX[s8], INDEX[s9]]    # 8 beat 7; 7 then lost to 9
    assert post.out[s7] == playoffs.LOST_PLAYIN and post.out[s10] == playoffs.LOST_PLAYIN
    assert post.phase == "playin" and len(post.alive(IDS)) == 16


def test_a_third_play_in_game_between_the_wrong_teams_is_an_error():
    rows = [playin_row(date(2027, 4, 14), seeded(EAST, 7), seeded(EAST, 8), True),
            playin_row(date(2027, 4, 14), seeded(EAST, 9), seeded(EAST, 10), True),
            playin_row(date(2027, 4, 16), seeded(EAST, 7), seeded(EAST, 10), True)]
    with pytest.raises(ParseError):
        read(rows)


# ---------- the series ----------

def test_series_scores_are_read_from_the_games_with_the_better_seed_first():
    rows = playin_rows(EAST)[0] + playin_rows(WEST)[0] + round_one_rows(EAST, {0: (2, 1), 1: (4, 3)})
    post = read(rows)
    assert post.phase == "playoffs"
    assert post.state.scores[(1, EAST, 0)] == (2, 1) and post.state.scores[(1, EAST, 1)] == (4, 3)
    seeds = bracket(EAST)
    assert post.out[seeds[4]] == "Lost in the first round"          # the 5 seed lost 4-3 to the 4 seed
    assert seeds[7] not in post.out                                  # 1 v 8 is not over
    assert (1, WEST, 0) not in post.state.scores                     # no west games yet


def test_the_bracket_is_corrected_when_the_first_round_games_show_a_different_seeding():
    c = EAST
    t = [seeded(c, n) for n in range(1, 7)]
    _, s7, s8 = playin_rows(c)
    # a tie-break the ratings page cannot show put the second team first: it hosts the 8 seed, the other hosts the 7
    rows = playin_rows(c)[0] + playin_rows(WEST)[0]
    rows += series_rows(t[1], s8, 1, 0, R1_START) + series_rows(t[0], s7, 1, 0, R1_START)
    post = read(rows)
    assert post.state.seeds[c] == [INDEX[x] for x in (t[1], t[0], t[2], t[3], t[4], t[5], s7, s8)]
    assert post.state.scores[(1, c, 0)] == (1, 0) and post.state.scores[(1, c, 3)] == (1, 0)


def test_a_game_that_cannot_be_in_the_bracket_stops_the_read():
    rows = playin_rows(EAST)[0] + playin_rows(WEST)[0] + round_one_rows(EAST)
    rows += series_rows(seeded(EAST, 1), seeded(EAST, 3), 1, 0, R2_START)    # round 2 pairs 1 with 4 and 3 with 2
    with pytest.raises(ParseError, match="do not fit the bracket"):
        read(rows)


def test_a_first_round_series_that_cannot_fit_any_seeding_is_an_error():
    _, s7, _ = playin_rows(EAST)
    # the 7 seed can never host a first-round game against a team that qualified directly
    rows = playin_rows(EAST)[0] + playin_rows(WEST)[0] + series_rows(s7, seeded(EAST, 1), 1, 0, R1_START)
    with pytest.raises(ParseError, match="do not fit any bracket"):
        read(rows)


def test_a_finished_postseason_names_the_champion_and_everyone_else_is_out():
    rows, east, west = full_postseason()
    rows += series_rows(east, west, 4, 2, FINALS_START)
    post = read(rows)
    assert post.phase == "complete" and post.champion == east
    assert post.alive(IDS) == [east]
    assert post.out[west] == "Lost in the Finals" and len(post.out) == 29
    assert post.state.scores[(4, 0, 0)] == (4, 2)


def test_the_finals_in_progress_leave_two_teams_alive():
    rows, east, west = full_postseason()
    rows += series_rows(east, west, 2, 3, FINALS_START)
    post = read(rows)
    assert post.phase == "playoffs" and post.champion is None
    assert sorted(post.alive(IDS)) == sorted([east, west])
    assert post.state.scores[(4, 0, 0)] == (2, 3)


# ---------- the simulator with a play-in under way ----------

def test_a_known_play_in_result_is_never_overturned():
    league = sim_league()
    idx = lambda c, n: INDEX[seeded(c, n)]     # noqa: E731
    state = sim.PlayoffState(
        {c: [idx(c, n) for n in range(1, 7)] for c in (EAST, WEST)},
        playin={c: [idx(c, n) for n in range(7, 11)] for c in (EAST, WEST)},
        playin_results={(EAST, 1): idx(EAST, 8), (EAST, 2): idx(EAST, 9), (EAST, 3): idx(EAST, 7)})
    result = sim.simulate(league, runs=4000, seed=3, state=state)
    made = dict(zip(IDS, result.playoffs))
    assert made[seeded(EAST, 8)] == 1.0                       # won game 1: the 7 seed
    assert made[seeded(EAST, 7)] == 1.0                       # lost game 1, beat the game-2 winner: the 8 seed
    assert made[seeded(EAST, 9)] == 0.0 and made[seeded(EAST, 10)] == 0.0
    assert 0 < made[seeded(WEST, 7)] < 1 and made[seeded(WEST, 10)] < made[seeded(WEST, 7)]
    assert result.title.sum() == pytest.approx(1.0)


def test_play_in_games_played_remove_teams_but_keep_the_rest_open():
    league = sim_league()
    idx = lambda c, n: INDEX[seeded(c, n)]     # noqa: E731
    state = sim.PlayoffState(
        {c: [idx(c, n) for n in range(1, 7)] for c in (EAST, WEST)},
        playin={c: [idx(c, n) for n in range(7, 11)] for c in (EAST, WEST)},
        playin_results={(EAST, 2): idx(EAST, 10)})
    result = sim.simulate(league, runs=4000, seed=3, state=state)
    made = dict(zip(IDS, result.playoffs))
    assert made[seeded(EAST, 9)] == 0.0 and made[seeded(EAST, 10)] > 0
    assert 0 < made[seeded(EAST, 7)] < 1


def test_bad_play_in_states_are_refused():
    league = sim_league()
    idx = lambda c, n: INDEX[seeded(c, n)]     # noqa: E731
    base = dict(seeds={c: [idx(c, n) for n in range(1, 7)] for c in (EAST, WEST)},
                playin={c: [idx(c, n) for n in range(7, 11)] for c in (EAST, WEST)})
    with pytest.raises(ValueError, match="not one of the two teams"):
        sim.simulate(league, runs=10, state=sim.PlayoffState(**base, playin_results={(EAST, 1): idx(EAST, 10)}))
    with pytest.raises(ValueError, match="before games 1 and 2"):
        sim.simulate(league, runs=10, state=sim.PlayoffState(**base, playin_results={(EAST, 3): idx(EAST, 7)}))
    with pytest.raises(ValueError, match="play-in is not finished"):
        sim.simulate(league, runs=10, state=sim.PlayoffState(**base, scores={(1, EAST, 0): (1, 0)}))


def test_with_the_finals_won_the_champion_has_exactly_all_the_odds():
    league = sim_league()
    post_rows, east, west = full_postseason()
    post = read(post_rows + series_rows(east, west, 4, 1, FINALS_START))
    result = sim.simulate(league, runs=500, seed=1, state=post.state)
    probs = result.title_probs()
    assert probs[east] == 1.0 and probs.drop(east).eq(0).all()
    assert result.finals[INDEX[west]] == 1.0 and result.conf_finals[INDEX[east]] == 1.0


def test_eliminated_teams_get_exactly_zero_in_every_run():
    league = sim_league()
    rows, east, west = full_postseason()
    post = read(rows)       # conference finals done, Finals not started
    probs = sim.simulate(league, runs=800, seed=2, state=post.state).title_probs()
    assert probs[east] + probs[west] == pytest.approx(1.0)
    assert all(probs[t] == 0 for t in post.out)
    assert np.isclose(probs.sum(), 1.0)
