"""Tests for the Polymarket blend (SPEC.md section 6.7). No network: a saved Polymarket-shaped response."""
import copy
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import requests

from pipeline import config, market
from pipeline.build_odds import build, check_before_publishing
from pipeline.validate import validate_odds
from tests.synthetic import FakeClient, league_pages, playoff_league_pages
from tests.test_playoffs import EAST, WEST, playin_rows, round_one_rows

FIXTURE = Path(__file__).parent / "fixtures" / "polymarket_nba_champion_sample.json"
SLUG = "nba-2027-champion"
IDS = [t.id for t in config.TEAMS]


@pytest.fixture()
def events() -> list:
    return json.loads(FIXTURE.read_text())


@pytest.fixture()
def data(events) -> market.MarketData:
    return market.MarketData(market.parse_prices(events, SLUG), SLUG, "2026-11-10T12:00:00Z")


def source(data):
    return lambda end_year, today: data


def failing_source(end_year, today):
    raise market.MarketError("polymarket: simulated outage")


# ---- name mapping ----

def test_all_30_teams_map_and_placeholders_are_ignored(events):
    prices = market.parse_prices(events, SLUG)
    assert sorted(prices) == sorted(IDS)
    assert prices["SAS"] == pytest.approx(0.215) and prices["LAC"] == pytest.approx(0.0005)


def test_a_missing_team_fails_loudly(events):
    events[0]["markets"] = [m for m in events[0]["markets"] if m["groupItemTitle"] != "Utah Jazz"]
    with pytest.raises(market.MarketError, match="UTA"):
        market.parse_prices(events, SLUG)


def test_an_unreadable_or_out_of_range_price_fails(events):
    atl = next(m for m in events[0]["markets"] if m["groupItemTitle"] == "Atlanta Hawks")
    atl["outcomePrices"] = None
    with pytest.raises(market.MarketError, match="ATL"):
        market.parse_prices(events, SLUG)
    atl["outcomePrices"] = '["1.5", "-0.5"]'
    with pytest.raises(market.MarketError, match="outside"):
        market.parse_prices(events, SLUG)


def test_the_wrong_event_or_an_empty_list_fails(events):
    with pytest.raises(market.MarketError):
        market.parse_prices(events, "nba-2099-champion")
    with pytest.raises(market.MarketError):
        market.parse_prices([], SLUG)


# ---- normalization and blend ----

def test_market_probs_sum_to_one_and_respect_the_floor(data):
    probs = market.market_probs(data.prices, IDS)
    assert probs.sum() == pytest.approx(1.0) and list(probs.index) == IDS
    assert probs.min() > 0
    zeros = dict(data.prices, ATL=0.0)
    assert market.market_probs(zeros, IDS)["ATL"] > 0           # a zero price is floored, not left at 0


def test_market_probs_over_live_teams_only(data):
    live = IDS[:8]
    probs = market.market_probs(data.prices, live)
    assert probs.sum() == pytest.approx(1.0)
    assert all(probs[t] == 0 for t in IDS[8:])


def test_blend_math_and_exact_sum():
    model_p = pd.Series([0.5, 0.3, 0.2, 0.0], index=list("ABCD"))
    market_p = pd.Series([0.1, 0.1, 0.8, 0.0], index=list("ABCD"))
    out = market.blend(model_p, market_p, 0.05)
    assert out["A"] == pytest.approx(0.95 * 0.5 + 0.05 * 0.1, abs=1e-6)
    assert out["C"] == pytest.approx(0.95 * 0.2 + 0.05 * 0.8, abs=1e-6)
    assert out["D"] == 0
    assert out.sum() == pytest.approx(1.0, abs=1e-12)


# ---- fetching ----

class FakeResponse:
    def __init__(self, status=200, body=b""):
        self.status_code, self.content = status, body


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params, timeout, headers))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_fetch_caches_for_the_day_and_sends_one_honest_request(tmp_path):
    body = FIXTURE.read_bytes()
    session = FakeSession(FakeResponse(200, body))
    first = market.get_market(2027, date(2026, 11, 10), session=session, cache_dir=tmp_path, sleep=lambda s: None)
    again = market.get_market(2027, date(2026, 11, 10), session=session, cache_dir=tmp_path, sleep=lambda s: None)
    assert first.prices == again.prices and len(session.calls) == 1           # second read came from the cache
    url, params, timeout, headers = session.calls[0]
    assert params == {"slug": SLUG} and timeout == config.TIMEOUT_SECONDS and headers["User-Agent"] == config.USER_AGENT


def test_fetch_retries_network_errors_then_gives_up(tmp_path):
    session = FakeSession(requests.ConnectionError(), FakeResponse(503), requests.Timeout())
    waits = []
    with pytest.raises(market.MarketError, match="gave up after 3 attempts"):
        market.get_market(2027, date(2026, 11, 10), session=session, cache_dir=tmp_path, sleep=waits.append)
    assert len(session.calls) == 3 and waits == list(config.BACKOFF_SECONDS)
    assert not list(tmp_path.iterdir())                                         # nothing cached on failure


@pytest.mark.parametrize("status", [403, 429])
def test_a_block_stops_at_once_without_retrying(tmp_path, status):
    session = FakeSession(FakeResponse(status), FakeResponse(200, FIXTURE.read_bytes()))
    with pytest.raises(market.MarketBlocked):
        market.get_market(2027, date(2026, 11, 10), session=session, cache_dir=tmp_path, sleep=lambda s: None)
    assert len(session.calls) == 1


# ---- in the build ----

@pytest.fixture(scope="module")
def pure_model():
    return build(FakeClient(league_pages(games=24)), date(2026, 11, 8), None).odds


def test_build_blends_at_five_percent_and_sums_to_one(data):
    odds = build(FakeClient(league_pages(games=24)), date(2026, 11, 8), None, market_source=source(data)).odds
    assert validate_odds(odds) == []
    assert odds["market"] == {"source": "polymarket", "fetched_at": "2026-11-10T12:00:00Z", "weight": 0.05,
                              "used": True, "event": SLUG}
    assert sum(t["title_prob"] for t in odds["teams"]) == pytest.approx(1.0, abs=1e-9)
    assert sum(odds["weights"].values()) == pytest.approx(1.0)                  # model input weights untouched
    for t in odds["teams"]:
        want = 0.95 * t["model_prob"] + 0.05 * t["market_prob"]
        assert t["title_prob"] == pytest.approx(want, abs=2e-6)


@pytest.mark.parametrize("src", [failing_source, lambda e, t: market.MarketData({"ATL": 0.5}, SLUG, "x"), None])
def test_a_failed_or_malformed_market_publishes_pure_model_odds(pure_model, src, capsys):
    odds = build(FakeClient(league_pages(games=24)), date(2026, 11, 8), None, market_source=src).odds
    assert validate_odds(odds) == []
    assert odds["market"]["used"] is False and odds["market"]["weight"] == 0.0 and odds["market"]["reason"]
    assert all("market_prob" not in t and "model_prob" not in t for t in odds["teams"])
    assert [t["title_prob"] for t in odds["teams"]] == [t["title_prob"] for t in pure_model["teams"]]
    if src is not None:
        assert "WARNING market blend skipped" in capsys.readouterr().err


def test_the_daily_guard_still_applies_to_blended_odds(data, pure_model):
    odds = build(FakeClient(league_pages(games=24)), date(2026, 11, 8), None, market_source=source(data)).odds
    assert check_before_publishing(odds, pure_model) == []                      # the blend itself is a small move
    previous = copy.deepcopy(pure_model)
    previous["teams"][0]["title_prob"] += 0.2
    assert any("moved" in p for p in check_before_publishing(odds, previous))


def test_playoff_mode_blends_over_live_teams_and_eliminated_stay_zero(data):
    rows = playin_rows(EAST)[0] + playin_rows(WEST)[0] + round_one_rows(EAST, {0: (4, 0)}) + round_one_rows(WEST, {0: (0, 4)})
    pages = playoff_league_pages(rows)
    odds = build(FakeClient(pages), date(2027, 4, 30), None, market_source=source(data)).odds
    assert validate_odds(odds) == [] and odds["market"]["used"] is True
    out = [t for t in odds["teams"] if t["status"] == "eliminated"]
    assert out and all(t["title_prob"] == 0 and t["market_prob"] == 0 and t["model_prob"] == 0 for t in out)
    live = [t for t in odds["teams"] if t["status"] != "eliminated"]
    assert sum(t["market_prob"] for t in live) == pytest.approx(1.0, abs=1e-5)
    assert sum(t["title_prob"] for t in odds["teams"]) == pytest.approx(1.0, abs=1e-9)
    assert all(t["title_prob"] == pytest.approx(0.95 * t["sim_prob"] + 0.05 * t["market_prob"], abs=2e-6) for t in live)
    pure = build(FakeClient(pages), date(2027, 4, 30), None).odds
    assert all(t["title_prob"] == t["sim_prob"] for t in pure["teams"])         # without the market: unchanged
