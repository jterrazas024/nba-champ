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
