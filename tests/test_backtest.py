"""Tests for the backtest (pipeline/backtest/): history parsing, scoring, fitting. No network."""
import numpy as np
import pandas as pd
import pytest

from pipeline import config, model
from pipeline.backtest import fetch_history, fit, history, metrics
from pipeline.backtest.scoring import INPUTS, SeasonInputs, probabilities
from pipeline.parse import ParseError, parse_ratings
from tests.synthetic import table


# ---------- history: names, pages ----------

def test_franchise_names_map_to_todays_team_ids():
    names_2001, codes_2001 = history.team_maps(2001)
    assert names_2001["Charlotte Hornets"].id == "NOP"      # the original Hornets became New Orleans
    assert names_2001["Vancouver Grizzlies"].id == "MEM"
    assert names_2001["Seattle SuperSonics"].id == "OKC"
    assert codes_2001["NJN"].id == "BKN" and codes_2001["CHH"].id == "NOP"
    names_2010, codes_2010 = history.team_maps(2010)
    assert names_2010["Charlotte Bobcats"].id == "CHA"
    assert names_2010["Charlotte Hornets"].id == "CHA"      # after 2002 the name means today's team
    assert codes_2010["CHA"].id == "CHA" and codes_2001["CHA"].id == "NOP"


def test_parse_ratings_accepts_a_season_specific_name_map():
    names, _ = history.team_maps(2001)
    html = table("advanced-team", ["Team", "W", "L", "MOV", "SOS", "SRS", "ORtg", "DRtg", "NRtg"],
                 [["Seattle SuperSonics*", "44", "38", "1.0", "0.1", "1.1", "105", "104", "1.0"]])
    assert parse_ratings(html, names)["team_id"].tolist() == ["OKC"]
    with pytest.raises(ParseError):
        parse_ratings(html)   # today's names do not include the Sonics


def games_page(include_playoffs: bool = True) -> str:
    rows = [
        "<tr><th>Fri, Apr 6, 2001</th><td>Boston Celtics</td><td>100</td><td>Miami Heat</td><td>90</td><td></td></tr>",
        "<tr><th>Sat, Apr 7, 2001</th><td>Boston Celtics</td><td>80</td><td>Miami Heat</td><td>85</td><td>Play-In Game</td></tr>",
    ]
    separator = "<tr class='thead'><th colspan='12'>Playoffs</th></tr>"
    after = "<tr><th>Sat, Apr 21, 2001</th><td>Boston Celtics</td><td>99</td><td>Miami Heat</td><td>98</td><td></td></tr>"
    body = "".join(rows) + (separator + after if include_playoffs else "")
    head = "".join(f"<th>{h}</th>" for h in ["Date", "Visitor/Neutral", "PTS", "Home/Neutral", "PTS", "Notes"])
    return f'<html><body><table id="schedule"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></body></html>'


def test_regular_games_stop_at_the_playoffs_row_and_skip_play_in():
    names, _ = history.team_maps(2001)
    games = history.parse_regular_games(games_page(), names)
    assert len(games) == 1 and games.loc[0, "home_pts"] == 90
    assert len(history.parse_regular_games(games_page(include_playoffs=False), names)) == 1


def test_game_counts():
    names, _ = history.team_maps(2001)
    counts = history.team_game_counts(history.parse_regular_games(games_page(), names))
    assert counts["BOS"] == 1 and counts["MIA"] == 1


def playoffs_page() -> str:
    rows = [
        ["Finals", "Los Angeles Lakers over Philadelphia 76ers  (4-1)", "Series Stats", "", "", ""],
        ["Game 1 Wed, June 6 ...", "Game 1 Wed, June 6 ...", "", "", "", ""],
        ["Eastern Conference Finals", "Philadelphia 76ers over Milwaukee Bucks  (4-3)", "Series Stats", "", "", ""],
        ["Western Conference First Round", "Seattle SuperSonics over Utah Jazz  (3-2)", "Series Stats", "", "", ""],
    ]
    return table("all_playoffs", ["a", "b", "c", "d", "e", "f"], rows)


def test_playoff_series_and_champion():
    names, _ = history.team_maps(2001)
    series = history.parse_playoff_series(playoffs_page(), names)
    assert [(s.winner, s.loser, s.winner_games, s.loser_games) for s in series] == [
        ("LAL", "PHI", 4, 1), ("PHI", "MIL", 4, 3), ("OKC", "UTA", 3, 2)]
    assert history.champion_of(series) == "LAL"
    with pytest.raises(ParseError):
        history.champion_of(series[1:])
    with pytest.raises(ParseError):
        history.parse_playoff_series("<html></html>", names)


def test_conferences_and_record_seeds_from_division_tables():
    names, _ = history.team_maps(2001)
    east = table("divs_standings_E", ["Eastern Conference", "W", "L"], [
        ["Atlantic Division", "Atlantic Division", "Atlantic Division"],
        ["Philadelphia 76ers*", "56", "26"], ["Miami Heat*", "50", "32"]])
    west = table("divs_standings_W", ["Western Conference", "W", "L"], [
        ["Pacific Division", "Pacific Division", "Pacific Division"], ["Los Angeles Lakers*", "56", "26"]])
    conferences = history.parse_conferences(east + west, names)
    assert conferences == {"PHI": "East", "MIA": "East", "LAL": "West"}
    ratings = pd.DataFrame({"team_id": ["MIA", "PHI", "LAL"], "w": [50, 56, 56], "srs": [1.0, 3.0, 5.0]})
    seeds = history.seeds_by_record(ratings, conferences).set_index("team_id")["seed"]
    assert seeds.to_dict() == {"PHI": 1, "MIA": 2, "LAL": 1}


def test_season_months_cover_the_odd_seasons():
    assert history.season_months(2020)[0] == "august" and history.season_months(2021)[0] == "may"
    assert history.season_months(2015)[:2] == ("april", "march")


def test_read_cached_names_the_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="fetch_history"):
        history.read_cached(tmp_path, "ratings_2001")


# ---------- fetch plan ----------

def test_planned_pages_cover_ratings_players_and_playoffs():
    names = [name for _, name in fetch_history.planned_pages(2001, 2003)]
    assert {"ratings_2000", "ratings_2003", "players_advanced_1999", "players_advanced_2003", "playoffs_2001"} <= set(names)
    assert len(names) == len(set(names)) == 4 + 5 + 3


class StubClient:
    """Serves the same two-team results page for any month and counts requests."""

    def __init__(self, pages_needed_games: int):
        self.requests = []
        self.games = pages_needed_games

    def get_html(self, path, name):
        self.requests.append(name)
        rows = "".join(f"<tr><th>Fri, Apr {d}, 2001</th><td>Boston Celtics</td><td>100</td><td>Miami Heat</td><td>90</td><td></td></tr>"
                       for d in range(1, self.games + 1))
        head = "".join(f"<th>{h}</th>" for h in ["Date", "Visitor/Neutral", "PTS", "Home/Neutral", "PTS", "Notes"])
        return f'<html><body><table id="schedule"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></body></html>'


def test_month_fetching_stops_once_every_team_has_20_games(monkeypatch):
    monkeypatch.setattr(fetch_history, "MIN_TEAMS", 2)
    client = StubClient(12)
    fetch_history.fetch_month_pages(client, 2001)
    assert client.requests == ["games_2001_april", "games_2001_march"]   # 12 + 12 >= 20


# ---------- scoring and fitting ----------

def make_inputs(year: int, seed: int, champion_rank: int = 0, missing: bool = False) -> SeasonInputs:
    rng = np.random.default_rng(seed)
    ids = [t.id for t in config.TEAMS]
    values = pd.DataFrame(rng.normal(size=(30, 5)), index=ids, columns=list(INPUTS))
    if missing:
        values.loc[ids[3], "recent_form"] = np.nan
    z = values.apply(model.zscores)
    penalty = pd.Series(0.0, index=ids)
    penalty.iloc[:4] = [-0.5, -1.5, -2.0, 0.0]
    order = (z * pd.Series(config.WEIGHTS)[list(INPUTS)]).sum(axis=1).sort_values(ascending=False).index
    return SeasonInputs(year, values, z, penalty, pd.DataFrame(), order[champion_rank])


def test_numpy_scores_match_the_live_model_including_missing_inputs():
    seasons = [make_inputs(2001, 1, missing=True), make_inputs(2002, 2)]
    t = fit.build_tensors(seasons, {})
    for s, season in enumerate(seasons):
        live = probabilities(season).to_numpy()
        mine = fit.season_probs(t, s, fit.current_weights(), config.TEMPERATURE)
        assert np.allclose(live, mine, atol=1e-12)


def test_champion_logprob_matches_manual_softmax_and_ignores_padding_teams():
    season = make_inputs(2001, 3, champion_rank=2)
    season.z, season.values = season.z.iloc[:29], season.values.iloc[:29]   # a 29-team league
    season.penalty = season.penalty.iloc[:29]
    season.champion = season.z.index[5]
    t = fit.build_tensors([season], {})
    got = fit.champion_logprob(fit.scores(t, fit.current_weights()[None]), t, np.array([0.6]))[0, 0, 0]
    p = fit.season_probs(t, 0, fit.current_weights(), 0.6)
    assert len(p) == 29 and p.sum() == pytest.approx(1.0)
    assert got == pytest.approx(np.log(p[5]))


def test_series_logprob_is_logistic_of_score_difference():
    season = make_inputs(2001, 4)
    a, b = season.z.index[0], season.z.index[1]
    t = fit.build_tensors([season], {2001: [(a, b)]})
    w = fit.current_weights()[None]
    got = fit.series_logprob(fit.scores(t, w), t, np.array([0.5]))[0, 0, 0]
    diff = fit.scores(t, w)[0, 0, 0] - fit.scores(t, w)[0, 0, 1]
    assert got == pytest.approx(-np.log1p(np.exp(-diff / 0.5)))


def test_weight_grid_sums_to_one_and_is_complete():
    grid = fit.weight_grid()
    assert grid.shape == (10626, 5)       # C(24, 4) ways to split 20 units of 5% into 5 inputs
    assert np.allclose(grid.sum(axis=1), 1.0) and (grid >= 0).all()


def test_current_weights_match_config():
    assert fit.current_weights().sum() == pytest.approx(1.0)
    assert fit.current_weights()[0] == pytest.approx(config.WEIGHTS["net_rating"])


def planted_seasons(n: int = 12) -> list[SeasonInputs]:
    """Seasons where the champion is always the team with the best star power (input 1)."""
    seasons = []
    for i in range(n):
        s = make_inputs(2001 + i, 100 + i)
        s.champion = s.z["star_power"].idxmax()
        seasons.append(s)
    return seasons


def test_fit_moves_toward_the_truth_without_a_prior_and_stays_put_with_a_strong_one():
    t = fit.build_tensors(planted_seasons(), {})
    grid = fit.evaluate_grid(t)
    train = list(range(len(t.years)))
    k, _ = fit.fit(grid, train, kappa=0.0)
    assert grid.weights[k][INPUTS.index("star_power")] >= 0.6          # data says star power
    k_strong, ti = fit.fit(grid, train, kappa=1e6)
    assert k_strong == 0 and fit.TEMPERATURES[ti] == pytest.approx(0.6)   # the current model wins


def test_fit_results_are_valid_weights():
    t = fit.build_tensors(planted_seasons(), {})
    grid = fit.evaluate_grid(t)
    k, _ = fit.fit(grid, list(range(12)), kappa=1.0)
    assert grid.weights[k].sum() == pytest.approx(1.0) and (grid.weights[k] >= 0).all()


def test_choose_kappa_returns_one_of_the_options():
    t = fit.build_tensors(planted_seasons(8), {})
    grid = fit.evaluate_grid(t)
    assert fit.choose_kappa(grid, list(range(8))) in fit.KAPPAS


# ---------- metrics ----------

def test_champion_scores_on_a_hand_checked_example():
    probs = np.array([0.5, 0.3, 0.2])
    m = metrics.champion_scores(probs, 1)
    assert m["rank"] == 2 and m["prob"] == pytest.approx(0.3)
    assert m["log_loss"] == pytest.approx(-np.log(0.3))
    assert m["brier"] == pytest.approx(0.25 + 0.49 + 0.04)


def test_summary_hit_rates_and_uniform_baseline():
    rows = [metrics.champion_scores(np.array([0.5, 0.3, 0.2]), c) for c in (0, 1, 2)]
    s = metrics.summarize(rows)
    assert s["top1"] == pytest.approx(1 / 3) and s["top3"] == 1.0 and s["median_rank"] == 2
    uniform = metrics.summarize([metrics.champion_scores(metrics.uniform_probs(30), 4)])
    assert uniform["log_loss"] == pytest.approx(np.log(30))
