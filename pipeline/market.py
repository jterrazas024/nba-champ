"""Market consensus from Polymarket's public Gamma API (SPEC.md section 6.7).

One GET to the "NBA: <year> Champion" event (no key, no login). The raw response is cached under
data/raw/<date>/ and never fetched twice in a day. Each team's market probability is its "Yes"
price, normalized to sum to 1 over the teams that can still win. A 403/429 stops immediately (no
retry, no workaround). Every problem raises MarketError; the build catches it and publishes the
pure model instead (weight 0), so the market can never break the daily refresh.
"""
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import requests

from pipeline import config, model


class MarketError(Exception):
    """The market data could not be fetched or is not usable (network, bad shape, missing team)."""


class MarketBlocked(MarketError):
    """Polymarket refused us (403/429). Stop; never retry or disguise the request."""


@dataclass
class MarketData:
    """A usable market read: raw Yes price per team id (all 30), where it came from, and when."""

    prices: dict[str, float]
    slug: str
    fetched_at: str


def fetch_event_json(end_year: int, today: date, session: requests.Session | None = None,
                     cache_dir: Path | None = None, sleep: Callable[[float], None] = time.sleep) -> tuple[list, str]:
    """Return (parsed event list, fetched_at UTC timestamp) for the champion event, cached for the day.

    Up to MAX_ATTEMPTS tries with backoff on network errors and 5xx; 403/429 raises MarketBlocked at once.
    """
    slug = config.MARKET_SLUG_TEMPLATE.format(end_year=end_year)
    cache_file = (cache_dir or config.CACHE_ROOT / today.isoformat()) / f"polymarket_{slug}.json"
    if cache_file.exists():
        stamp = datetime.fromtimestamp(cache_file.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return _load(cache_file.read_bytes(), slug), stamp
    session = session or requests.Session()
    last = "unknown problem"
    for attempt in range(1, config.MAX_ATTEMPTS + 1):
        if attempt > 1:
            sleep(config.BACKOFF_SECONDS[attempt - 2])
        try:
            response = session.get(config.MARKET_API_URL, params={"slug": slug}, timeout=config.TIMEOUT_SECONDS,
                                   headers={"User-Agent": config.USER_AGENT})
        except requests.RequestException as err:
            last = type(err).__name__
            continue
        if response.status_code in config.BLOCK_STATUSES:
            raise MarketBlocked(f"polymarket {slug}: HTTP {response.status_code} (blocked); stopping, not retrying")
        if response.status_code >= 500:
            last = f"HTTP {response.status_code}"
            continue
        if response.status_code != 200:
            raise MarketError(f"polymarket {slug}: unexpected HTTP {response.status_code}")
        events = _load(response.content, slug)       # validate before caching
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(response.content)
        return events, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raise MarketError(f"polymarket {slug}: gave up after {config.MAX_ATTEMPTS} attempts ({last})")


def _load(body: bytes, slug: str) -> list:
    """Return the JSON list in `body`; raise MarketError if it is not a non-empty list."""
    try:
        events = json.loads(body)
    except ValueError as err:
        raise MarketError(f"polymarket {slug}: response is not JSON") from err
    if not isinstance(events, list) or not events:
        raise MarketError(f"polymarket {slug}: no such event (empty response)")
    return events


def parse_prices(events: list, slug: str) -> dict[str, float]:
    """Return {team_id: Yes price} for the event with `slug`. Raises MarketError unless exactly 30 teams match.

    Markets whose name is not a team (placeholders like "Team A", "Other") are ignored. A matched team
    with a missing, unreadable or out-of-range price, or a team listed twice, is an error.
    """
    event = next((e for e in events if isinstance(e, dict) and e.get("slug") == slug), None)
    if event is None or not isinstance(event.get("markets"), list):
        raise MarketError(f"polymarket {slug}: event or its markets not found")
    prices: dict[str, float] = {}
    for market in event["markets"]:
        tid = config.MARKET_TEAM_IDS.get(market.get("groupItemTitle"))
        if tid is None:
            continue
        if tid in prices:
            raise MarketError(f"polymarket {slug}: {tid} is listed twice")
        prices[tid] = _yes_price(market, tid)
    missing = [t.id for t in config.TEAMS if t.id not in prices]
    if missing:
        raise MarketError(f"polymarket {slug}: no market for {', '.join(missing)} "
                          f"(found {len(prices)} of {len(config.TEAMS)} teams)")
    return prices


def _yes_price(market: dict, team_id: str) -> float:
    """Return the Yes price (the outcomePrices entry paired with the outcome "Yes") as a float in [0, 1]."""
    try:
        outcomes = json.loads(market["outcomes"]) if isinstance(market["outcomes"], str) else market["outcomes"]
        raw = json.loads(market["outcomePrices"]) if isinstance(market["outcomePrices"], str) else market["outcomePrices"]
        price = float(raw[list(outcomes).index("Yes")])
    except (KeyError, TypeError, ValueError, IndexError) as err:
        raise MarketError(f"polymarket: {team_id} has no readable Yes price") from err
    if not 0 <= price <= 1:        # also false for NaN
        raise MarketError(f"polymarket: {team_id} price {price} is outside 0-1")
    return price


def get_market(end_year: int, today: date, **kwargs) -> MarketData:
    """Fetch (or read the day's cache) and parse the market. Raises MarketError on any problem."""
    slug = config.MARKET_SLUG_TEMPLATE.format(end_year=end_year)
    events, fetched_at = fetch_event_json(end_year, today, **kwargs)
    return MarketData(parse_prices(events, slug), slug, fetched_at)


def market_probs(prices: dict[str, float], live_ids: Iterable[str]) -> pd.Series:
    """Return each team's market probability: Yes prices floored at MARKET_MIN_PRICE, then normalized
    to sum to 1 over `live_ids`; every other team is exactly 0. Indexed in config.TEAMS order."""
    live = set(live_ids)
    raw = pd.Series({t.id: max(prices[t.id], config.MARKET_MIN_PRICE) if t.id in live else 0.0 for t in config.TEAMS})
    if raw.sum() <= 0:
        raise MarketError("no live team to normalize the market over")
    return raw / raw.sum()


def blend(model_probs: pd.Series, market: pd.Series, weight: float) -> pd.Series:
    """Return (1 - weight) * model + weight * market, rounded with model.round_probs so the total is exactly 1.0.

    A team with 0 in both (eliminated) stays exactly 0. Blends probabilities, not z-scores (SPEC.md 6.7).
    """
    return model.round_probs((1 - weight) * model_probs + weight * market.reindex(model_probs.index))
