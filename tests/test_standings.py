"""Tests for the Standings page data: parsing the standings and per-game pages, building the `standings` and
`leaders` fields of odds.json, validating them, and leaving them out (not failing) when they cannot be built."""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from pipeline import build_odds
from pipeline.build_odds import build, build_leaders, build_standings, team_results, _record_and_streak
from pipeline.fetch import Blocked
from pipeline.parse import ParseError, parse_per_game, parse_standings
from pipeline.validate import validate_odds
from tests.synthetic import FakeClient, league_pages, table

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TODAY = date(2026, 11, 8)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------- parsing ----------

def test_standings_fixture_gives_15_ranked_teams_per_conference():
    table_ = parse_standings(fixture("standings_sample.html"))
    assert list(table_.columns) == ["team_id", "conference", "rank", "w", "l", "pct", "gb"]
    for conference in ("East", "West"):
        rows = table_[table_["conference"] == conference]
        assert rows["rank"].tolist() == list(range(1, 16))
        assert rows["gb"].iloc[0] == 0.0 and rows["gb"].is_monotonic_increasing
    assert table_.iloc[0]["team_id"] == "PHI" and table_.iloc[0]["w"] == 8      # the bracketed seed is not part of the name
    assert table_["team_id"].nunique() == 30


def test_standings_missing_table_or_unknown_team_is_a_parse_error():
    with pytest.raises(ParseError):
        parse_standings("<html></html>")
    bad = fixture("standings_sample.html").replace("Philadelphia 76ers", "Philadelphia Cheesesteaks")
    with pytest.raises(ParseError, match="Cheesesteaks"):
        parse_standings(bad)


def test_per_game_fixture_has_clean_numbers():
    players = parse_per_game(fixture("per_game_sample.html"))
    assert len(players) == 6 and players["team_id"].notna().all()
    assert players.iloc[0]["player"] == "Luka Dončić" and players.iloc[0]["pts"] == 39.3 and players.iloc[0]["team_id"] == "LAL"
    assert pd.api.types.is_numeric_dtype(players["trb"])


def test_per_game_traded_player_keeps_the_total_row_and_the_last_team():
    html = table("per_game_stats", ["Player", "Team", "G", "PTS", "AST", "TRB", "STL", "BLK"], [
        ['<a href="/players/t/tradeda01.html">Trade Guy</a>', "2TM", "20", "21.0", "5.0", "4.0", "1.0", "0.5"],
        ['<a href="/players/t/tradeda01.html">Trade Guy</a>', "BOS", "12", "20.0", "5.0", "4.0", "1.0", "0.5"],
        ['<a href="/players/t/tradeda01.html">Trade Guy</a>', "BRK", "8", "22.5", "5.0", "4.0", "1.0", "0.5"],
        ["Player", "Team", "G", "PTS", "AST", "TRB", "STL", "BLK"],
        ["League Average", "", "", "", "", "", "", ""]])
    players = parse_per_game(html)
    assert len(players) == 1
    row = players.iloc[0]
    assert (row["g"], row["pts"], row["team_id"]) == (20, 21.0, "BKN")   # BR code BRK is our BKN; the later team row


# ---------- building the fields ----------

def test_record_and_streak():
    assert _record_and_streak([]) == (None, None)
    assert _record_and_streak([True] * 3 + [False] * 2) == ("3-2", "L2")
    assert _record_and_streak([False] * 5 + [True] * 12) == ("10-0", "W12")


def test_team_results_follow_game_order():
    games = pd.DataFrame({"date": pd.to_datetime(["2026-11-02", "2026-11-01"]), "away_id": ["BOS", "BOS"],
                          "away_pts": [90, 100], "home_id": ["NYK", "NYK"], "home_pts": [100, 90]})
    assert team_results(games) == {"NYK": [False, True], "BOS": [True, False]}


@pytest.fixture(scope="module")
def built():
    return build(FakeClient(league_pages()), TODAY, None).odds


def test_a_build_includes_valid_standings_and_leaders(built):
    assert validate_odds(built) == []
    for key in ("east", "west"):
        rows = built["standings"][key]
        assert [r["rank"] for r in rows] == list(range(1, 16))
        assert rows[0]["gb"] == 0.0 and rows[0]["last10"] is not None and rows[0]["streak"][0] in "WL"
    leaders = built["leaders"]
    assert leaders["min_games"] == 17                       # 70% of 24 games, rounded up
    for key in ("pts", "ast", "reb", "stl", "blk"):
        assert len(leaders[key]) == 5
    assert leaders["pts"][0]["team"] == "WAS" and leaders["pts"][0]["value"] == 39.0   # the strongest team's best scorer
    assert len(__import__("json").dumps(built, indent=1).encode()) < 100 * 1024


def test_leaders_skip_players_below_the_games_minimum():
    html = table("per_game_stats", ["Player", "Team", "G", "PTS", "AST", "TRB", "STL", "BLK"], [
        ['<a href="/players/a/aaaaaa01.html">Hot Start</a>', "BOS", "3", "40.0", "9.0", "9.0", "3.0", "3.0"],
        ['<a href="/players/b/bbbbbb01.html">Regular</a>', "NYK", "20", "25.0", "5.0", "5.0", "1.0", "1.0"]])
    out = build_leaders(html, pd.Series([20.0, 20.0, 20.0]))
    assert out["min_games"] == 14 and [r["player"] for r in out["pts"]] == ["Regular"]


def test_playoff_mode_standings_have_no_last_10():
    standings = build_standings(league_pages()["/leagues/NBA_2027.html"], None)
    assert standings["east"][0]["last10"] is None and standings["east"][0]["streak"] is None


def test_a_broken_per_game_page_leaves_leaders_out_but_the_build_succeeds(capsys):
    pages = league_pages()
    pages["/leagues/NBA_2027_per_game.html"] = "<html>layout changed</html>"
    odds = build(FakeClient(pages), TODAY, None).odds
    assert "leaders" not in odds and "standings" in odds and validate_odds(odds) == []
    assert "leaders left out" in capsys.readouterr().err


def test_a_broken_standings_table_leaves_standings_out_but_the_build_succeeds(capsys):
    pages = league_pages()
    pages["/leagues/NBA_2027.html"] = pages["/leagues/NBA_2027.html"].replace("confs_standings_W", "renamed")
    odds = build(FakeClient(pages), TODAY, None).odds
    assert "standings" not in odds and "leaders" in odds
    assert "standings left out" in capsys.readouterr().err


def test_a_block_on_the_extra_page_still_stops_the_run():
    class Blocking(FakeClient):
        def get_html(self, path, cache_name):
            if path.endswith("_per_game.html"):
                raise Blocked("HTTP 403")
            return super().get_html(path, cache_name)
    with pytest.raises(Blocked):
        build(Blocking(league_pages()), TODAY, None)


# ---------- validation ----------

def test_validator_catches_bad_standings_and_leaders(built):
    import copy
    odds = copy.deepcopy(built)
    odds["standings"]["east"][3]["rank"] = 9
    odds["standings"]["west"].pop()
    odds["leaders"]["pts"].reverse()
    odds["leaders"]["blk"][0]["team"] = "XXX"
    odds["leaders"]["min_games"] = 0
    problems = " | ".join(validate_odds(odds))
    for part in ("standings.east ranks", "standings.west must list 15", "leaders.pts must be sorted", "leaders.blk entries",
                 "min_games"):
        assert part in problems


def test_files_without_standings_or_leaders_still_validate(built):
    odds = {k: v for k, v in built.items() if k not in ("standings", "leaders")}
    assert validate_odds(odds) == []
