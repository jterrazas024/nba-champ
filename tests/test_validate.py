"""Tests for the odds.json validator (pipeline/validate.py)."""
import json
from pathlib import Path

from pipeline.validate import validate_odds

REAL_FILE = Path(__file__).resolve().parent.parent / "data" / "odds.json"


def test_real_file_is_valid():
    assert validate_odds(json.loads(REAL_FILE.read_text())) == []


def test_validator_catches_probabilities_that_do_not_sum_to_one():
    odds = json.loads(REAL_FILE.read_text())
    odds["teams"][0]["title_prob"] += 0.05
    assert any("sum" in p for p in validate_odds(odds))


def test_validator_catches_missing_team():
    odds = json.loads(REAL_FILE.read_text())
    odds["teams"].pop()
    assert any("30 teams" in p for p in validate_odds(odds))


def test_validator_catches_bad_timestamp():
    odds = json.loads(REAL_FILE.read_text())
    odds["generated_at"] = "yesterday"
    assert any("generated_at" in p for p in validate_odds(odds))


def _with_sim() -> dict:
    odds = json.loads(REAL_FILE.read_text())
    odds["sim"] = {"runs": 20000, "seed_date": "2026-11-08", "mode": "regular_season", "margin_per_z": 3.3}
    for team in odds["teams"]:
        team["sim_prob"] = 1 / 30
        team["sim"] = {"finals": 0.1, "conf_finals": 0.2, "make_playoffs": 0.5, "seed_avg": 4.5}
    return odds


def test_simulation_fields_are_optional_and_checked_when_present():
    assert validate_odds(_with_sim()) == []
    bad = _with_sim(); bad["teams"][0]["sim_prob"] = 0.5
    assert any("sim_prob" in p and "sum" in p for p in validate_odds(bad))
    bad = _with_sim(); bad["teams"][0]["sim"]["finals"] = 1.2
    assert any("sim.finals" in p for p in validate_odds(bad))
    bad = _with_sim(); bad["teams"][0]["sim"]["seed_avg"] = 16
    assert any("seed_avg" in p for p in validate_odds(bad))
    ok = _with_sim(); ok["teams"][0]["sim"]["seed_avg"] = None          # never made the playoffs
    assert validate_odds(ok) == []
    for runs in (0, -5, 2.5, "x"):
        bad = _with_sim(); bad["sim"]["runs"] = runs
        assert any("sim.runs" in p for p in validate_odds(bad))
    partial = _with_sim(); del partial["teams"][0]["sim_prob"]
    assert any("sim_prob" in p for p in validate_odds(partial))


def _playoff_file() -> dict:
    """The real file turned into a valid playoff-mode file: team 0 is champion, everyone else out."""
    odds = _with_sim()
    odds["phase"], odds["headline_source"] = "complete", "simulation"
    for i, team in enumerate(odds["teams"]):
        team["title_prob"] = team["sim_prob"] = 1.0 if i == 0 else 0.0
        team["status"] = "champion" if i == 0 else "eliminated"
        team["rank"] = i + 1
    return odds


def test_playoff_mode_fields_are_checked_when_present():
    assert validate_odds(_playoff_file()) == []
    bad = _playoff_file(); bad["teams"][1]["status"] = "alive"; bad["teams"][1]["title_prob"] = 0.0
    assert validate_odds(bad) == []                                  # alive with zero odds is allowed (lost by chance, not yet marked)
    bad = _playoff_file(); bad["teams"][0]["sim_prob"] = 0.5
    assert any("equal sim_prob" in p for p in validate_odds(bad))
    bad = _playoff_file(); bad["teams"][1]["status"] = "champion"
    assert any("more than one champion" in p for p in validate_odds(bad))
    bad = _playoff_file(); bad["phase"] = "playoffs"
    assert any("phase is not complete" in p for p in validate_odds(bad))
    bad = _playoff_file(); bad["phase"] = "later"
    assert any("phase must be" in p for p in validate_odds(bad))
    bad = _playoff_file(); del bad["teams"][5]["status"]
    assert any("no status" in p for p in validate_odds(bad))
