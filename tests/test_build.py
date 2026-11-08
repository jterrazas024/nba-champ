"""End-to-end tests for build_odds using a synthetic league (tests/synthetic.py). No network."""
import json
from datetime import date

import pytest

from pipeline import build_odds, config
from pipeline.build_odds import BuildError, build, check_before_publishing, run
from pipeline.fetch import Blocked
from pipeline.validate import validate_odds
from tests.synthetic import FakeClient, league_pages

TODAY = date(2026, 11, 8)


@pytest.fixture(scope="module")
def built():
    client = FakeClient(league_pages())
    return client, build(client, TODAY, None)


def test_build_produces_a_valid_complete_file(built):
    _, result = built
    odds = result.odds
    assert validate_odds(odds) == []
    assert odds["is_sample"] is False and odds["model_version"] == "v0" and odds["season"] == "2026-27"
    assert len(odds["teams"]) == 30
    assert abs(sum(t["title_prob"] for t in odds["teams"]) - 1.0) < 1e-9
    json.dumps(odds, allow_nan=False)  # no NaN anywhere
    assert len(json.dumps(odds, indent=1).encode()) < 100 * 1024


def test_stronger_teams_rank_higher_and_the_weakest_is_last(built):
    teams = built[1].odds["teams"]
    strongest = {t.id for t in config.TEAMS[27:]}                  # the three strongest teams
    assert teams[0]["id"] in strongest
    assert teams[-1]["id"] == config.TEAMS[0].id                    # the weakest team
    assert [t["rank"] for t in teams] == list(range(1, 31))


def test_an_injured_star_costs_the_team_availability(built):
    by_id = {t["id"]: t for t in built[1].odds["teams"]}
    injured, healthy = by_id[config.TEAMS[29].id], by_id[config.TEAMS[28].id]  # the injury report lists TEAMS[29]'s best player as Out
    assert injured["inputs"]["star_availability"]["value"] < healthy["inputs"]["star_availability"]["value"]
    assert injured["rank"] > 1  # the strongest team is held back from the top by its injured star


def test_it_asks_for_exactly_the_pages_it_needs_and_stops_reading_months_early(built):
    client, _ = built
    assert sorted(client.paths) == sorted([
        "/leagues/NBA_2027.html", "/leagues/NBA_2026.html",
        "/leagues/NBA_2027_advanced.html", "/leagues/NBA_2026_advanced.html", "/leagues/NBA_2025_advanced.html",
        "/friv/injuries.fcgi", "/leagues/NBA_2027_games-november.html", "/leagues/NBA_2027_games-october.html"])
    assert client.requests_made == 8


def test_dropped_inputs_are_null_and_weights_sum_to_one(built):
    odds = built[1].odds
    assert all(t["inputs"]["clutch"] is None and t["inputs"]["core_net_rating"] is None for t in odds["teams"])
    assert odds["weights"]["clutch"] == 0.0 and odds["weights"]["core_net_rating"] == 0.0
    assert abs(sum(odds["weights"].values()) - 1.0) < 1e-9
    assert set(odds["teams"][0]["inputs"]) == set(config.INPUT_KEYS)


def test_active_inputs_have_value_z_percentile_and_contribution(built):
    top = built[1].odds["teams"][0]
    for key in ("net_rating", "star_power", "recent_form", "star_availability", "rotation_availability"):
        entry = top["inputs"][key]
        assert set(entry) == {"value", "z", "percentile", "contribution"}
        assert 0 <= entry["percentile"] <= 100


def test_the_composite_is_the_sum_of_contributions(built):
    team = built[1].odds["teams"][5]
    assert team["composite"] == pytest.approx(sum(i["contribution"] for i in team["inputs"].values() if i), abs=0.01)


def test_star_injuries_show_up_in_notes(built):
    notes = {t["id"]: t["notes"] for t in built[1].odds["teams"]}
    assert any("(star): Out" in n for n in notes[config.TEAMS[29].id])


def test_the_site_fields_are_all_present(built):
    """Everything app.js reads (see the contract in SPEC.md section 7)."""
    odds = built[1].odds
    for key in ("generated_at", "season", "is_sample", "weights", "teams"):
        assert key in odds
    for team in odds["teams"]:
        for key in ("id", "name", "conference", "record", "title_prob", "rank", "prob_change_1d", "composite", "inputs", "notes"):
            assert key in team
        assert isinstance(team["notes"], list) and len(team["notes"]) <= 4
        assert team["conference"] in ("East", "West")


def test_a_team_with_no_recent_games_loses_only_that_input():
    skipped = config.TEAMS[3].id
    client = FakeClient(league_pages(skip_form_team=skipped))
    odds = build(client, TODAY, None).odds
    team = next(t for t in odds["teams"] if t["id"] == skipped)
    assert team["inputs"]["recent_form"] is None
    assert team["inputs"]["net_rating"] is not None
    assert any("No data for recent form" in n for n in team["notes"])
    assert validate_odds(odds) == []


def test_early_season_note_when_few_games_played():
    odds = build(FakeClient(league_pages(games=8)), date(2026, 10, 30), None).odds
    assert all(any(n.startswith("Early season") for n in t["notes"]) for t in odds["teams"])


# ---------- change since yesterday ----------

def test_prob_change_is_null_without_a_previous_real_file(built):
    assert all(t["prob_change_1d"] is None for t in built[1].odds["teams"])
    sample = {"is_sample": True, "model_version": "sample", "teams": [{"id": "BOS", "title_prob": 0.5}]}
    assert build(FakeClient(league_pages()), TODAY, sample).odds["teams"][0]["prob_change_1d"] is None


def test_prob_change_compares_with_the_previous_real_file(built):
    first = built[1].odds
    again = build(FakeClient(league_pages()), TODAY, first).odds
    assert all(t["prob_change_1d"] == 0.0 for t in again["teams"])
    nudged = json.loads(json.dumps(first))
    nudged["teams"][0]["title_prob"] -= 0.01
    changed = build(FakeClient(league_pages()), TODAY, nudged).odds
    assert changed["teams"][0]["prob_change_1d"] == pytest.approx(0.01, abs=1e-6)


# ---------- season states ----------

def test_before_the_season_nothing_is_built():
    result = build(FakeClient(league_pages(games=0)), TODAY, None)
    assert result.odds is None and "not started" in result.message


def test_after_the_regular_season_nothing_is_built():
    pages = league_pages(games=82)
    result = build(FakeClient(pages), date(2027, 4, 20), None)
    assert result.odds is None and "over" in result.message


# ---------- sanity checks ----------

def test_a_page_with_too_few_players_stops_the_build():
    with pytest.raises(BuildError, match="too few players"):
        build(FakeClient(league_pages(players_per_team=5)), TODAY, None)


def test_a_big_overnight_move_is_not_published(built):
    odds = built[1].odds
    previous = json.loads(json.dumps(odds))
    previous["teams"][0]["title_prob"] = odds["teams"][0]["title_prob"] + 0.3  # the real run is 30 points lower
    assert any("moved" in p for p in check_before_publishing(odds, previous))


def test_nan_is_never_published(built):
    odds = json.loads(json.dumps(built[1].odds))
    odds["teams"][0]["composite"] = float("nan")
    assert any("NaN" in p for p in check_before_publishing(odds, None))


def test_a_healthy_file_has_no_problems(built):
    assert check_before_publishing(built[1].odds, None) == []


# ---------- writing and failing safely ----------

def test_run_writes_the_file_atomically(tmp_path):
    out = tmp_path / "odds.json"
    result = run(FakeClient(league_pages()), TODAY, out)
    assert json.loads(out.read_text())["is_sample"] is False
    assert not (tmp_path / "odds.json.tmp").exists()
    assert "Built odds" in result.message


def test_a_blocked_run_leaves_the_previous_file_untouched(tmp_path):
    out = tmp_path / "odds.json"
    out.write_text("PREVIOUS")

    class BlockedClient:
        requests_made = 0

        def get_html(self, path, cache_name):
            raise Blocked("fetch ratings: HTTP 403 (blocked)")

    with pytest.raises(Blocked):
        run(BlockedClient(), TODAY, out)
    assert out.read_text() == "PREVIOUS"


def test_a_failed_sanity_check_leaves_the_previous_file_untouched(tmp_path):
    out = tmp_path / "odds.json"
    good = build(FakeClient(league_pages()), TODAY, None).odds
    absurd = json.loads(json.dumps(good))
    absurd["teams"][0]["title_prob"] += 0.3   # the run below will be 30 points away from this "previous" file
    out.write_text(json.dumps(absurd))
    before = out.read_text()
    with pytest.raises(BuildError, match="moved"):
        run(FakeClient(league_pages()), TODAY, out)
    assert out.read_text() == before


def test_main_returns_codes_for_blocked_and_failed_runs(tmp_path, monkeypatch):
    out = tmp_path / "odds.json"
    monkeypatch.setattr("sys.argv", ["build_odds", "--out", str(out), "--today", "2026-11-08"])

    def fail_with(error):
        def fake_run(*args, **kwargs):
            raise error
        return fake_run

    monkeypatch.setattr(build_odds, "run", fail_with(Blocked("blocked")))
    assert build_odds.main() == 2
    monkeypatch.setattr(build_odds, "run", fail_with(BuildError("bad data")))
    assert build_odds.main() == 1
