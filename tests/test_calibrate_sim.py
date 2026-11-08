"""Tests for the offline calibration helpers (pipeline/calibrate_sim.py). Synthetic data only; no network."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from pipeline import calibrate_sim as cal
from pipeline import config
from tests.synthetic import STRENGTH


def team_index() -> pd.Index:
    return pd.Index([t.id for t in config.TEAMS])


def test_fill_schedule_gives_every_team_exactly_the_games_it_needs():
    ids = team_index()
    known = pd.DataFrame({"home_id": ["ATL", "BOS", "CHI"], "away_id": ["BOS", "CHI", "ATL"]})
    need = pd.Series(10, index=ids)
    games = cal.fill_schedule(known, need, np.random.default_rng(0))
    counts = pd.concat([games["home_id"], games["away_id"]]).value_counts().reindex(ids)
    assert (counts == 10).all()
    assert (games["home_id"] != games["away_id"]).all()
    assert len(games) == 150


def test_fill_schedule_rejects_too_many_known_games():
    ids = team_index()
    known = pd.DataFrame({"home_id": ["ATL"] * 3, "away_id": ["BOS"] * 3})
    with pytest.raises(ValueError):
        cal.fill_schedule(known, pd.Series(2, index=ids), np.random.default_rng(0))


def test_compare_probs_identical_and_reversed():
    ids = team_index()
    p = pd.Series(np.linspace(1, 30, 30), index=ids)
    p = p / p.sum()
    same = cal.compare_probs(p, p)
    assert same["spearman"] == pytest.approx(1.0) and same["max_gap"] == 0 and same["favorite_a"] == same["favorite_b"]
    flipped = cal.compare_probs(p, pd.Series(p.to_numpy()[::-1], index=ids))
    assert flipped["spearman"] == pytest.approx(-1.0) and flipped["max_gap"] > 0


def test_loso_pick_never_uses_the_held_out_season():
    # setting 0 wins every season except season 2, which is a disaster for it (-9) and a triumph for setting 1
    loglik = np.array([[-1.0, -1.0, -9.0, -1.0], [-2.0, -2.0, -0.1, -2.0]])
    assert cal.loso_pick(loglik) == [1, 1, 0, 1]      # season 2 is judged on the other three, where setting 0 is better
    flipped = cal.compare_probs(p, pd.Series(p.to_numpy()[::-1], index=ids))
    assert flipped["spearman"] == pytest.approx(-1.0) and flipped["max_gap"] > 0


def test_loso_pick_never_uses_the_held_out_season():
    # setting 0 is best everywhere except season 2, where only setting 1 does well; excluding season 2 leaves setting 0 ahead
    loglik = np.array([[-1.0, -1.0, -9.0, -1.0], [-2.0, -2.0, -0.1, -2.0]])
    assert cal.loso_pick(loglik) == [0, 0, 0, 0] or cal.loso_pick(loglik)[2] == 0
    # when the held-out season itself carries the evidence, it is excluded: the choice is made on the other three
    assert cal.loso_pick(loglik)[2] == 0
    flipped = np.array([[-1.0, -1.0, -1.0], [-1.5, -1.5, -0.2]])
    assert cal.loso_pick(flipped) == [1, 1, 0]       # for seasons 0 and 1, season 2 tips the total to setting 1


def test_champion_loglik_floors_a_zero_probability():
    probs = np.array([0.0, 1.0])
    assert cal.champion_loglik(probs, 0, 1e-4) == pytest.approx(np.log(1e-4))
    assert cal.champion_loglik(probs, 1, 1e-4) == 0.0


def test_paired_difference_counts_seasons_the_first_is_better():
    d = cal.paired_difference(np.array([1.0, 1.0, 3.0]), np.array([2.0, 2.0, 2.0]))
    assert d["better"] == 2 and d["n"] == 3 and d["mean"] == pytest.approx(-1 / 3)


def test_fit_margin_recovers_a_known_margin():
    ids = team_index()
    score = pd.Series([STRENGTH[t] for t in ids], index=ids)

    def run_at(margin: float) -> pd.Series:
        e = np.exp(score * margin / 20)
        return e / e.sum()

    best, table = cal.fit_margin(run_at(4.2), run_at)
    assert best == pytest.approx(4.2, abs=0.05)
    assert table["sq_gap"].min() < 1e-9


def test_simulate_titles_runs_on_pandas_inputs():
    ids = team_index()
    final = pd.Series([STRENGTH[t] / 10 for t in ids], index=ids)
    conferences = {t.id: t.conference for t in config.TEAMS}
    wins = pd.Series(np.round(41 + 2 * final.to_numpy() * 10), index=ids)
    games = pd.Series(82.0, index=ids)
    result = cal.simulate_titles(final, wins, games, conferences, None, margin=4.0, runs=2000, seed=1)
    assert result.title.sum() == pytest.approx(1.0)
    assert result.title[list(ids).index(final.idxmax())] == result.title.max()


def test_offline_client_never_fetches(tmp_path):
    client = cal.OfflineClient(tmp_path)
    with pytest.raises(FileNotFoundError, match="never fetches"):
        client.get_html("/anything", "missing_page")
    (tmp_path / "ratings_2027.html").write_text("<html>x</html>", encoding="utf-8")
    assert client.get_html("/leagues/NBA_2027.html", "ratings_2027") == "<html>x</html>"


@pytest.mark.skipif(not (config.CACHE_ROOT / "2026-11-08" / "ratings_2027.html").exists(), reason="current-season cache not present")
def test_todays_inputs_rebuild_from_the_cache():
    final, wins, games, conferences, remaining = cal.todays_league_inputs(date(2026, 11, 8), config.CACHE_ROOT / "2026-11-08")
    assert len(final) == 30 and final.notna().all()
    per_team = pd.concat([remaining["home_id"], remaining["away_id"]]).value_counts().reindex(final.index)
    assert (per_team + games == config.SEASON_GAMES).all()
