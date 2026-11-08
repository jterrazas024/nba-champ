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
    yesterday = json.loads(json.dumps(first))
    yesterday["teams"][0]["history"] = [yesterday["teams"][0]["title_prob"] - 0.01]
    yesterday["history_dates"] = ["2026-11-07"]
    for t in yesterday["teams"][1:]:
        t["history"] = [t["title_prob"]]
    changed = build(FakeClient(league_pages()), TODAY, yesterday).odds
    assert changed["teams"][0]["prob_change_1d"] == pytest.approx(0.01, abs=1e-6)
    assert changed["teams"][1]["prob_change_1d"] == 0.0


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


# ---------- history (SPEC.md section 7) ----------

def _odds_for(day: date, previous: dict | None = None, **kw) -> dict:
    return build(FakeClient(league_pages(**kw)), day, previous).odds


def _shape_ok(odds: dict, n: int) -> None:
    assert len(odds["history_dates"]) == n
    assert all(len(t["history"]) == n for t in odds["teams"])
    assert validate_odds(odds) == []


def test_first_run_starts_history_with_just_today(built):
    odds = built[1].odds
    assert odds["history_dates"] == ["2026-11-08"]
    assert all(t["history"] == [t["title_prob"]] for t in odds["teams"])


def test_same_day_rerun_overwrites_instead_of_duplicating(built):
    again = _odds_for(TODAY, built[1].odds)
    _shape_ok(again, 1)
    assert all(t["prob_change_1d"] is None for t in again["teams"])


def test_next_day_appends_and_computes_the_change(built):
    first = built[1].odds
    nudged = json.loads(json.dumps(first))
    nudged["teams"][0]["history"] = [first["teams"][0]["title_prob"] + 0.02]
    second = _odds_for(date(2026, 11, 9), nudged)
    _shape_ok(second, 2)
    assert second["history_dates"] == ["2026-11-08", "2026-11-09"]
    top = second["teams"][0]
    assert top["history"][0] == pytest.approx(first["teams"][0]["title_prob"] + 0.02)
    assert top["history"][1] == top["title_prob"]
    assert top["prob_change_1d"] == pytest.approx(top["title_prob"] - top["history"][0], abs=1e-4)


def test_gaps_are_left_as_gaps_and_the_change_respects_the_three_day_limit(built):
    first = built[1].odds
    three_days = _odds_for(date(2026, 11, 11), first)
    assert three_days["history_dates"] == ["2026-11-08", "2026-11-11"]   # no backfill
    assert all(t["prob_change_1d"] is not None for t in three_days["teams"])
    four_days = _odds_for(date(2026, 11, 12), first)
    _shape_ok(four_days, 2)
    assert all(t["prob_change_1d"] is None for t in four_days["teams"])


def test_history_is_capped_at_30_days_and_the_file_stays_small(built):
    odds, day = built[1].odds, TODAY
    pages = league_pages()
    for month in ("december", "january"):   # later months reuse the November page (only the dates in it matter)
        pages[f"/leagues/NBA_2027_games-{month}.html"] = pages["/leagues/NBA_2027_games-november.html"]
    for _ in range(35):
        day = date.fromordinal(day.toordinal() + 1)
        odds = build(FakeClient(pages), day, odds).odds
    _shape_ok(odds, 30)
    assert odds["history_dates"][-1] == day.isoformat()
    assert odds["history_dates"][0] == date.fromordinal(day.toordinal() - 29).isoformat()
    assert len(json.dumps(odds, indent=1).encode()) < 60 * 1024


def _real_file_without_history(built) -> dict:
    old = json.loads(json.dumps(built[1].odds))
    old.pop("history_dates")
    for t in old["teams"]:
        t.pop("history")
    old["generated_at"] = "2026-11-07T10:00:00Z"
    return old


def test_a_real_file_without_history_is_bootstrapped(built):
    old = _real_file_without_history(built)
    new = _odds_for(TODAY, old)
    _shape_ok(new, 2)
    assert new["history_dates"] == ["2026-11-07", "2026-11-08"]
    old_prob = {t["id"]: t["title_prob"] for t in old["teams"]}
    assert all(t["history"][0] == old_prob[t["id"]] for t in new["teams"])
    assert all(t["prob_change_1d"] is not None for t in new["teams"])


def test_a_history_less_file_from_the_same_date_gives_null_changes(built):
    old = _real_file_without_history(built)
    old["generated_at"] = "2026-11-08T01:00:00Z"
    new = _odds_for(TODAY, old)
    _shape_ok(new, 1)
    assert all(t["prob_change_1d"] is None for t in new["teams"])


def test_history_starts_fresh_for_sample_missing_corrupt_or_other_model(built, tmp_path):
    base = built[1].odds
    sample = json.loads(json.dumps(base)); sample["is_sample"] = True
    other = json.loads(json.dumps(base)); other["model_version"] = "v9"
    other["history_dates"] = ["2026-11-01", "2026-11-02"]
    for t in other["teams"]:
        t["history"] = [0.1, 0.2]
    broken = json.loads(json.dumps(base)); broken["teams"][3]["history"] = [0.1, 0.2, 0.3]
    garbage = json.loads(json.dumps(base)); garbage["history_dates"] = ["not-a-date"]
    for previous in (None, sample, other, broken, garbage):
        yesterday = date(2026, 11, 9)
        odds = _odds_for(yesterday, previous)
        _shape_ok(odds, 1)
        assert odds["history_dates"] == ["2026-11-09"]
        assert all(t["prob_change_1d"] is None for t in odds["teams"])
    out = tmp_path / "odds.json"
    out.write_text("{not json")
    run(FakeClient(league_pages()), TODAY, out)
    _shape_ok(json.loads(out.read_text()), 1)


def test_validator_history_rules(built):
    def fresh() -> dict:
        return json.loads(json.dumps(built[1].odds))

    assert validate_odds(fresh()) == []
    old_style = fresh(); old_style.pop("history_dates")
    for t in old_style["teams"]:
        t.pop("history")
    assert validate_odds(old_style) == []           # older files without history are still valid

    bad = fresh(); bad["history_dates"] = ["2026-13-45"]
    assert any("YYYY-MM-DD" in p for p in validate_odds(bad))
    bad = fresh(); bad["history_dates"] = ["2026-1-8"]
    assert any("YYYY-MM-DD" in p for p in validate_odds(bad))
    two = fresh(); two["history_dates"] = ["2026-11-08", "2026-11-08"]
    for t in two["teams"]:
        t["history"] = [0.1, 0.1]
    assert any("increasing" in p for p in validate_odds(two))
    short = fresh(); short["teams"][0]["history"] = []
    assert any("length" in p for p in validate_odds(short))
    out_of_range = fresh(); out_of_range["teams"][0]["history"] = [1.5]
    assert any("between 0 and 1" in p for p in validate_odds(out_of_range))
    many = fresh(); many["history_dates"] = [f"2026-12-{d:02d}" for d in range(1, 32)]
    for t in many["teams"]:
        t["history"] = [0.0] * 31
    assert any("at most 30" in p for p in validate_odds(many))
