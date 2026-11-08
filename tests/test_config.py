"""Tests for the shared config: season helpers and the team table."""
import json
from datetime import date
from pathlib import Path

from pipeline import config

ODDS_PATH = Path(__file__).resolve().parent.parent / "data" / "odds.json"


def test_season_end_year_switches_in_august():
    assert config.season_end_year(date(2026, 7, 31)) == 2026
    assert config.season_end_year(date(2026, 8, 1)) == 2027
    assert config.season_end_year(date(2026, 11, 8)) == 2027
    assert config.season_end_year(date(2027, 4, 20)) == 2027


def test_season_label():
    assert config.season_label(2027) == "2026-27"
    assert config.season_label(2030) == "2029-30"


def test_there_are_30_unique_teams_split_evenly_by_conference():
    assert len(config.TEAMS) == 30
    for field in ("id", "br_code", "name"):
        assert len({getattr(t, field) for t in config.TEAMS}) == 30
    assert sum(t.conference == "East" for t in config.TEAMS) == 15


def test_team_codes_that_differ_from_basketball_reference():
    assert config.TEAM_BY_BR_CODE["BRK"].id == "BKN"
    assert config.TEAM_BY_BR_CODE["CHO"].id == "CHA"
    assert config.TEAM_BY_BR_CODE["PHO"].id == "PHX"


def test_team_table_matches_the_ids_and_names_the_site_uses():
    odds = json.loads(ODDS_PATH.read_text())
    site = {t["id"]: (t["name"], t["conference"]) for t in odds["teams"]}
    mine = {t.id: (t.name, t.conference) for t in config.TEAMS}
    assert site == mine
