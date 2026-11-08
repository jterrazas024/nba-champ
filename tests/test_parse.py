"""Tests for the Basketball-Reference parsers.

Most use small fixtures sliced from the real pages (tests/fixtures/), a few use tiny inline
tables for odd cases, and one group checks the full real pages when they are cached locally.
"""
import glob
from pathlib import Path

import pandas as pd
import pytest

from pipeline.parse import ParseError, parse_games, parse_injuries, parse_players, parse_ratings

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def table(table_id: str, headers: list[str], rows: list[list[str]]) -> str:
    """Build a minimal page with one table. Cells may contain raw HTML such as <a href=...>."""
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<html><body><table id="{table_id}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></body></html>'


# ---------- ratings ----------

def test_ratings_fixture_has_clean_numeric_columns():
    ratings = parse_ratings(fixture("ratings_sample.html"))
    assert list(ratings.columns) == ["team_id", "name", "w", "l", "games", "mov", "sos", "srs", "ortg", "drtg", "nrtg"]
    assert len(ratings) == 8
    assert pd.api.types.is_numeric_dtype(ratings["nrtg"])


def test_ratings_drop_league_average_and_map_to_site_ids():
    ratings = parse_ratings(fixture("ratings_sample.html"))
    assert "League Average" not in ratings["name"].tolist()
    assert set(ratings["team_id"]) >= {"BKN", "CHA", "PHX"}  # site ids, not BRK/CHO/PHO


def test_ratings_games_is_wins_plus_losses():
    boston = parse_ratings(fixture("ratings_sample.html")).set_index("team_id").loc["BOS"]
    assert boston["games"] == boston["w"] + boston["l"] == 9


def test_ratings_strip_trailing_star_from_team_names():
    page = table("advanced-team", ["Team", "W", "L", "MOV", "SOS", "SRS", "ORtg", "DRtg", "NRtg"],
                 [["Boston Celtics*", "50", "20", "5.0", "0.1", "5.1", "118", "110", "8.0"]])
    assert parse_ratings(page)["team_id"].tolist() == ["BOS"]


def test_ratings_unknown_team_name_fails_loudly():
    page = table("advanced-team", ["Team", "W", "L", "MOV", "SOS", "SRS", "ORtg", "DRtg", "NRtg"],
                 [["Seattle SuperSonics", "1", "1", "0", "0", "0", "100", "100", "0"]])
    with pytest.raises(ParseError, match="Seattle SuperSonics"):
        parse_ratings(page)


def test_missing_table_fails_loudly():
    with pytest.raises(ParseError, match="not found"):
        parse_ratings("<html><body><p>nothing</p></body></html>")


def test_missing_column_fails_loudly():
    page = table("advanced-team", ["Team", "W", "L"], [["Boston Celtics", "1", "1"]])
    with pytest.raises(ParseError, match="missing columns"):
        parse_ratings(page)


# ---------- players ----------

def test_players_get_stable_ids_from_links():
    players = parse_players(fixture("players_sample.html"))
    assert players.loc[players["player"] == "Amen Thompson", "player_id"].iloc[0] == "thompam01"


def test_traded_player_has_a_total_row_then_one_row_per_team():
    harden = parse_players(fixture("players_sample.html")).query("player_id == 'hardeja01'")
    assert harden["team_br"].tolist() == ["2TM", "LAC", "CLE"]  # page order is kept
    assert harden["is_total"].tolist() == [True, False, False]
    assert pd.isna(harden["team_id"].iloc[0])
    assert harden["team_id"].tolist()[1:] == ["LAC", "CLE"]
    assert harden["g"].iloc[0] == harden["g"].iloc[1:].sum()  # total = sum of the stints


def test_accented_names_survive():
    assert "Nikola Jokić" in parse_players(fixture("players_sample.html"))["player"].tolist()


def test_players_drop_league_average_and_keep_small_samples():
    players = parse_players(fixture("players_sample.html"))
    assert "League Average" not in players["player"].tolist()
    assert players["g"].min() <= 3  # the tiny-sample player is still there; the model decides what to do


def test_players_use_site_team_ids():
    players = parse_players(fixture("players_sample.html"))
    assert set(players["team_id"].dropna()) == {"HOU", "LAC", "CLE", "DEN", "OKC", "UTA"}


def test_player_without_a_link_fails_loudly():
    page = table("advanced", ["Player", "Team", "G", "MP", "BPM"], [["No Link", "BOS", "10", "200", "1.0"]])
    with pytest.raises(ParseError, match="No Link"):
        parse_players(page)


def test_player_ids_and_numbers_parse_from_inline_table():
    page = table("advanced", ["Player", "Team", "G", "MP", "BPM"],
                 [['<a href="/players/s/smithjo01.html">Jo Smith</a>', "BRK", "5", "100", "-1.5"]])
    row = parse_players(page).iloc[0]
    assert (row["player_id"], row["team_id"], row["g"], row["bpm"]) == ("smithjo01", "BKN", 5, -1.5)


# ---------- injuries ----------

def test_injury_statuses_are_extracted():
    injuries = parse_injuries(fixture("injuries_sample.html"))
    assert set(injuries["status"]) == {"Out", "Day To Day", "Out For Season"}


def test_injury_rows_have_ids_teams_and_dates():
    injuries = parse_injuries(fixture("injuries_sample.html"))
    gilbert = injuries[injuries["player"] == "Keshon Gilbert"].iloc[0]
    assert gilbert["player_id"] == "gilbeke01"
    assert gilbert["team_id"] == "ATL"
    assert gilbert["update"] == pd.Timestamp("2026-11-06")


def test_injury_status_stops_at_a_dash_or_a_parenthesis():
    page = table("injuries", ["Player", "Team", "Update", "Description"], [
        ['<a href="/players/a/aaaaaa01.html">A</a>', "Boston Celtics", "Sat, Nov 7, 2026", "Out - rest of season"],
        ['<a href="/players/b/bbbbbb01.html">B</a>', "Boston Celtics", "Sat, Nov 7, 2026", "Day To Day (Ankle) - sore"],
    ])
    assert parse_injuries(page)["status"].tolist() == ["Out", "Day To Day"]


# ---------- games ----------

def test_games_keep_only_finished_games():
    games = parse_games(fixture("games_sample.html"))
    assert len(games) == 4  # the fixture also holds 3 future games with no score
    assert games["away_pts"].dtype.kind == "i"


def test_games_are_sorted_and_keep_cup_notes():
    games = parse_games(fixture("games_sample.html"))
    assert games["date"].is_monotonic_increasing
    assert games["notes"].tolist().count("NBA Cup") == 2


def test_games_ignore_separator_rows_and_unknown_teams():
    page = table("schedule", ["Date", "Visitor/Neutral", "PTS", "Home/Neutral", "PTS.1", "Notes"], [
        ["Sat, Apr 17, 2027", "Boston Celtics", "100", "Miami Heat", "90", ""],
        ["Playoffs", "", "", "", "", ""],
        ["Sun, Apr 18, 2027", "Boston Celtics", "", "Miami Heat", "", ""],
        ["Sun, Apr 18, 2027", "Some Foreign Club", "80", "Miami Heat", "99", ""],
    ])
    games = parse_games(page)
    assert games[["away_id", "home_id"]].values.tolist() == [["BOS", "MIA"]]


# ---------- the full real pages (only when cached locally; git-ignored) ----------

def latest_cached(name: str) -> str | None:
    found = sorted(glob.glob(str(RAW / "*" / f"{name}.html")))
    return Path(found[-1]).read_bytes().decode("utf-8", errors="replace") if found else None


@pytest.mark.parametrize("name,parser,check", [
    ("team_ratings", parse_ratings, lambda df: len(df) == 30 and df["team_id"].nunique() == 30),
    ("players_advanced_now", parse_players, lambda df: len(df) > 300),
    ("players_advanced_prev1", parse_players, lambda df: len(df) > 300 and df["is_total"].any()),  # a full season has traded players
    ("injuries", parse_injuries, lambda df: len(df) > 10 and df["status"].isin(["Out", "Day To Day", "Out For Season"]).mean() > 0.9),
    ("games_november", parse_games, lambda df: len(df) > 10),
])
def test_full_real_pages_parse_sanely(name, parser, check):
    html = latest_cached(name)
    if html is None:
        pytest.skip(f"no cached copy of {name} (data/raw/ is git-ignored)")
    assert check(parser(html))
