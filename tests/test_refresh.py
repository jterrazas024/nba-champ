"""Tests for the odds.json validator and the timestamp refresher."""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline.refresh_timestamp import refresh
from pipeline.validate import validate_odds

REAL_FILE = Path(__file__).resolve().parent.parent / "data" / "odds.json"


@pytest.fixture
def odds_copy(tmp_path) -> Path:
    """A scratch copy of data/odds.json, so tests never touch the real file."""
    copy = tmp_path / "odds.json"
    shutil.copy(REAL_FILE, copy)
    return copy


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


def test_refresh_updates_only_the_timestamp(odds_copy):
    before = json.loads(odds_copy.read_text())
    now = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    stamp = refresh(odds_copy, now=now)

    after = json.loads(odds_copy.read_text())
    assert stamp == "2030-01-02T03:04:05Z"
    assert after["generated_at"] == stamp
    before["generated_at"] = stamp
    assert after == before  # nothing else changed, including is_sample


def test_refresh_refuses_an_invalid_file_and_leaves_it_alone(odds_copy):
    odds = json.loads(odds_copy.read_text())
    odds["teams"].pop()
    broken = json.dumps(odds)
    odds_copy.write_text(broken)

    with pytest.raises(ValueError):
        refresh(odds_copy)

    assert odds_copy.read_text() == broken
