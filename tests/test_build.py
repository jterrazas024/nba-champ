"""End-to-end tests for build_odds using a synthetic league (tests/synthetic.py). No network."""
import json
from datetime import date

import pandas as pd
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
        "/friv/injuries.fcgi", "/leagues/NBA_2027_games-november.html", "/leagues/NBA_2027_games-october.html",
        # the remaining schedule for the simulation: December through April (November is shared)
        "/leagues/NBA_2027_games-december.html", "/leagues/NBA_2027_games-january.html",
        "/leagues/NBA_2027_games-february.html", "/leagues/NBA_2027_games-march.html",
        "/leagues/NBA_2027_games-april.html"])
    assert client.requests_made == 13 <= config.MAX_REQUESTS


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


# ---------- month handling for recent form (May and June were added for the playoff phase) ----------

def _never_enough_games_client(months: list[str]) -> FakeClient:
    """A client serving one empty-ish results page per month, so no team ever reaches 20 games."""
    from tests.synthetic import games_page
    return FakeClient({f"/leagues/NBA_2027_games-{m}.html": games_page(date(2026, 10, 20), 1) for m in months})


@pytest.mark.parametrize("today,expected_first", [
    (date(2026, 10, 25), "october"), (date(2026, 11, 8), "november"), (date(2027, 1, 15), "january"),
    (date(2027, 4, 10), "april"),
    (date(2026, 8, 20), "april"),   # offseason months fall back to April, exactly as before May/June existed
])
def test_recent_form_month_walk_is_unchanged_during_the_regular_season(today, expected_first):
    client = _never_enough_games_client(list(build_odds.SEASON_MONTHS))
    build_odds.fetch_recent_games(client, 2027, today, [t.id for t in config.TEAMS])
    first = client.paths[0].rsplit("-", 1)[1].removesuffix(".html")
    assert first == expected_first
    assert client.paths[-1].endswith("october.html")   # always walks back to October when games are scarce


def test_may_and_june_have_their_own_pages():
    client = _never_enough_games_client(list(build_odds.SEASON_MONTHS))
    build_odds.fetch_recent_games(client, 2027, date(2027, 5, 20), [t.id for t in config.TEAMS])
    assert client.paths[0].endswith("may.html")


# ---------- the playoff simulation in shadow mode (SPEC.md sections 6.4, 7, 9) ----------

HEADLINE_TEAM_KEYS = ("id", "name", "conference", "record", "title_prob", "rank", "prob_change_1d", "history",
                      "composite", "inputs", "notes")


def _headline(odds: dict) -> str:
    """Everything the site shows today, as one string: the file without the sim fields and the timestamp."""
    top = {k: v for k, v in odds.items() if k not in ("sim", "generated_at")}
    top["teams"] = [{k: v for k, v in t.items() if k not in ("sim", "sim_prob")} for t in odds["teams"]]
    return json.dumps(top, sort_keys=True)


@pytest.fixture(scope="module")
def plain():
    """The same build with the simulation switched off."""
    return build(FakeClient(league_pages()), TODAY, None, with_sim=False).odds


def test_sim_fields_are_present_and_valid(built):
    odds = built[1].odds
    assert validate_odds(odds) == []
    assert odds["sim"]["mode"] == "regular_season" and odds["sim"]["seed_date"] == "2026-11-08"
    assert odds["sim"]["runs"] == 400 and odds["sim"]["margin_per_z"] == config.MARGIN_PER_Z
    assert sum(t["sim_prob"] for t in odds["teams"]) == pytest.approx(1.0, abs=1e-9)
    for t in odds["teams"]:
        assert set(t["sim"]) == {"finals", "conf_finals", "make_playoffs", "seed_avg"}
        assert 0 <= t["sim"]["finals"] <= t["sim"]["conf_finals"] <= t["sim"]["make_playoffs"] <= 1
        assert t["sim"]["seed_avg"] is None or 1 <= t["sim"]["seed_avg"] <= 8
    assert len(json.dumps(odds, indent=1).encode()) < 50 * 1024


def test_the_headline_is_byte_identical_with_or_without_the_simulation(built, plain):
    assert _headline(built[1].odds) == _headline(plain)
    assert "sim" not in plain and all("sim_prob" not in t for t in plain["teams"])


def test_the_strong_teams_have_the_highest_sim_prob(built):
    ranked = sorted(built[1].odds["teams"], key=lambda t: -t["sim_prob"])
    strong = {t.id for t in config.TEAMS[22:]}                 # the 8 strongest synthetic teams
    assert ranked[0]["id"] in strong
    assert sum(t["sim_prob"] for t in ranked if t["id"] in strong) > 0.7
    assert ranked[-1]["id"] in {t.id for t in config.TEAMS[:8]}


def test_same_day_reruns_give_identical_simulation_numbers(built):
    again = _odds_for(TODAY)
    assert [t["sim_prob"] for t in again["teams"]] == [t["sim_prob"] for t in built[1].odds["teams"]]
    assert [t["sim"] for t in again["teams"]] == [t["sim"] for t in built[1].odds["teams"]]


def test_a_different_date_gives_different_simulation_numbers(built):
    other = _odds_for(date(2026, 11, 9))
    assert other["sim"]["seed_date"] == "2026-11-09"
    assert [t["sim_prob"] for t in other["teams"]] != [t["sim_prob"] for t in built[1].odds["teams"]]


def test_the_penalty_flag_changes_the_strength_the_simulation_uses(monkeypatch):
    seen = []
    real = build_odds.sim.league_from_frames

    def spy(ratings_pts, *args, **kwargs):
        seen.append(ratings_pts.copy())
        return real(ratings_pts, *args, **kwargs)

    monkeypatch.setattr(build_odds.sim, "league_from_frames", spy)
    odds_off = _odds_for(TODAY, games=30)
    monkeypatch.setattr(config, "SIM_USES_PENALTIES", True)
    odds_on = _odds_for(TODAY, games=30)
    assert len(seen) == 2 and "sim" in odds_off and "sim" in odds_on
    composites = pd.Series({t["id"]: t["composite"] for t in odds_off["teams"]})   # published composite = after penalties
    assert (seen[1] / config.MARGIN_PER_Z - composites).abs().max() < 1e-3          # flag on: the published (after-penalty) composite
    assert (seen[0] - seen[1]).abs().max() > 0                                      # flag off: strength differs (penalties were applied)
    assert _headline(odds_off) == _headline(odds_on)


def _warns(capsys) -> list[str]:
    return [line for line in capsys.readouterr().err.splitlines() if line.startswith("WARNING")]


def test_a_simulator_error_still_publishes_the_standard_odds(plain, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("the simulator broke")

    monkeypatch.setattr(build_odds.sim, "simulate", boom)
    odds = build(FakeClient(league_pages()), TODAY, None).odds
    assert _headline(odds) == _headline(plain) and "sim" not in odds
    assert all("sim_prob" not in t and "sim" not in t for t in odds["teams"])
    assert validate_odds(odds) == []
    warnings = _warns(capsys)
    assert len(warnings) == 1 and "the simulator broke" in warnings[0]


def test_a_missing_schedule_page_still_publishes_the_standard_odds(plain, capsys):
    pages = league_pages()
    del pages["/leagues/NBA_2027_games-february.html"]
    odds = build(FakeClient(pages), TODAY, None).odds
    assert json.dumps(odds, sort_keys=True).count("sim_prob") == 0
    assert _headline(odds) == _headline(plain)
    assert len(_warns(capsys)) == 1


def test_a_partial_schedule_fails_the_sanity_check_and_skips_the_simulation(plain, capsys):
    pages = league_pages()
    pages["/leagues/NBA_2027_games-april.html"] = pages["/leagues/NBA_2027_games-march.html"]   # wrong page: games counted twice
    odds = build(FakeClient(pages), TODAY, None).odds
    assert "sim" not in odds and _headline(odds) == _headline(plain)
    warnings = _warns(capsys)
    assert len(warnings) == 1 and "remaining schedule" in warnings[0]


def test_a_blocked_schedule_fetch_keeps_the_odds_and_stops_further_requests(plain, capsys):
    class BlockedAfterModelPages(FakeClient):
        """Serves the model pages, then the site blocks us on the first extra month and refuses all after."""

        def get_html(self, path, cache_name):
            if self.blocked or (path.endswith("games-december.html")):
                self.blocked = True
                self.refused += 1
                raise Blocked("fetch: HTTP 403 (blocked); stopping, not retrying")
            return super().get_html(path, cache_name)

        blocked = False
        refused = 0

    client = BlockedAfterModelPages(league_pages())
    odds = build(client, TODAY, None).odds
    assert "sim" not in odds and _headline(odds) == _headline(plain)
    assert client.refused == 1 and not any("january" in p for p in client.paths)   # nothing more was asked for
    warnings = _warns(capsys)
    assert len(warnings) == 1 and "blocked" in warnings[0].lower()


def test_run_writes_a_file_with_sim_fields_and_main_message_mentions_it(tmp_path):
    out = tmp_path / "odds.json"
    result = run(FakeClient(league_pages()), TODAY, out)
    assert "sim" in json.loads(out.read_text()) and "with simulation" in result.message



def test_a_schedule_a_few_games_short_is_filled_and_the_simulation_still_runs(monkeypatch, capsys):
    real = build_odds.sim_inputs.fetch_remaining_schedule
    monkeypatch.setattr(build_odds.sim_inputs, "fetch_remaining_schedule", lambda *a, **k: real(*a, **k).iloc[:-30])  # 2 days short
    odds = _odds_for(TODAY)
    assert "sim" in odds and validate_odds(odds) == [] and not _warns(capsys)
