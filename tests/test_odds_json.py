"""Contract tests for data/odds.json (SPEC.md section 7).

The site reads only this file, so these checks protect the page from bad data.
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

ODDS_PATH = Path(__file__).resolve().parent.parent / "data" / "odds.json"


@pytest.fixture(scope="module")
def odds() -> dict:
    """Return the parsed contents of data/odds.json."""
    return json.loads(ODDS_PATH.read_text())


def test_has_30_unique_teams(odds):
    ids = [t["id"] for t in odds["teams"]]
    assert len(ids) == 30
    assert len(set(ids)) == 30


def test_title_probs_sum_to_one(odds):
    total = sum(t["title_prob"] for t in odds["teams"])
    assert abs(total - 1.0) < 1e-6


def test_probs_are_between_zero_and_one(odds):
    assert all(0 <= t["title_prob"] <= 1 for t in odds["teams"])


def test_ranks_run_1_to_30_in_probability_order(odds):
    ranked = sorted(odds["teams"], key=lambda t: t["rank"])
    assert [t["rank"] for t in ranked] == list(range(1, 31))
    probs = [t["title_prob"] for t in ranked]
    assert probs == sorted(probs, reverse=True)


def test_generated_at_is_utc_iso_timestamp(odds):
    stamp = odds["generated_at"]
    assert stamp.endswith("Z")
    datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")  # raises if malformed


def test_weights_sum_to_one(odds):
    assert abs(sum(odds["weights"].values()) - 1.0) < 1e-9


def test_is_sample_flag_is_a_boolean(odds):
    assert isinstance(odds["is_sample"], bool)


def test_file_is_under_100_kb():
    assert ODDS_PATH.stat().st_size < 100 * 1024
