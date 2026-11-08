"""Polite, cached downloads from Basketball-Reference (CLAUDE.md "Data source rules").

The rules this module enforces:
  - at most one request every SECONDS_BETWEEN_REQUESTS (retries included)
  - a hard cap of MAX_REQUESTS requests per run
  - every page is cached on disk by date and never fetched twice the same day
  - timeouts, and retries with backoff on network errors and server (5xx) errors
  - a 403, a 429, or a bot-check page STOPS the whole run: no retry, no further requests
  - any other unexpected response fails with a clear one-line message
"""
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from pipeline import config


class FetchError(Exception):
    """A page could not be fetched (network trouble, server error, unexpected response, or the cap)."""


class Blocked(FetchError):
    """The site refused us (403/429 or a bot-check page). The run must stop and must not retry."""


def looks_like_bot_check(html: str) -> bool:
    """Return True if the page is a Cloudflare-style "verify you are human" page."""
    return "Just a moment..." in html or "cf-chl" in html or "challenge-platform" in html


class BRClient:
    """Fetches Basketball-Reference pages politely and caches them for the day.

    `session`, `sleep`, `monotonic`, and `today` can be replaced in tests so no real
    network access or waiting is needed.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        cache_dir: Path | None = None,
        max_requests: int = config.MAX_REQUESTS,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        today: date | None = None,
    ) -> None:
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = config.USER_AGENT
        self._session = session
        day = today or datetime.now(timezone.utc).date()
        self._cache_dir = cache_dir or config.CACHE_ROOT / day.isoformat()
        self._max_requests = max_requests
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None
        self._blocked_by: str | None = None
        self.requests_made = 0

    def get_html(self, path: str, cache_name: str) -> str:
        """Return the page at `path` (e.g. "/leagues/NBA_2027.html") as text.

        Uses today's cached copy `<cache_name>.html` if there is one; otherwise downloads the
        page, caches the raw bytes, and returns it decoded as UTF-8 (the site's real encoding;
        guessing it can garble accented names like Dončić).
        Raises Blocked if the site refused us (now or earlier in this run) and FetchError for
        any other failure. Nothing is cached on failure.
        """
        if self._blocked_by:
            raise Blocked(f"fetch {cache_name}: not sent, the site already blocked us ({self._blocked_by})")
        cache_file = self._cache_dir / f"{cache_name}.html"
        if cache_file.exists():
            return cache_file.read_bytes().decode("utf-8", errors="replace")

        body = self._download(config.BASE_URL + path, cache_name)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(body)
        return body.decode("utf-8", errors="replace")

    def _pace(self) -> None:
        """Wait until at least SECONDS_BETWEEN_REQUESTS have passed since the previous request."""
        if self._last_request_at is not None:
            wait = config.SECONDS_BETWEEN_REQUESTS - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def _download(self, url: str, cache_name: str) -> bytes:
        """GET `url` with a timeout, pacing, and retries; return the raw page bytes.

        Retries only network errors and 5xx responses (waiting 2s, then 4s). Every attempt
        counts toward the request cap.
        """
        last_problem = "unknown problem"
        for attempt in range(1, config.MAX_ATTEMPTS + 1):
            if attempt > 1:
                self._sleep(config.BACKOFF_SECONDS[attempt - 2])
            if self.requests_made >= self._max_requests:
                raise FetchError(f"fetch {cache_name}: request cap of {self._max_requests} reached; stopping")
            self._pace()
            self.requests_made += 1
            try:
                response = self._session.get(url, timeout=config.TIMEOUT_SECONDS)
            except requests.RequestException as err:
                last_problem = f"{type(err).__name__}"
                continue

            text_start = response.content[:20000].decode("utf-8", errors="replace")
            if response.status_code in config.BLOCK_STATUSES or looks_like_bot_check(text_start):
                self._blocked_by = f"HTTP {response.status_code}"
                raise Blocked(f"fetch {cache_name}: HTTP {response.status_code} (blocked); stopping, not retrying")
            if response.status_code >= 500:
                last_problem = f"HTTP {response.status_code}"
                continue
            if response.status_code != 200:
                raise FetchError(f"fetch {cache_name}: unexpected HTTP {response.status_code}; stopping")
            return response.content
        raise FetchError(f"fetch {cache_name}: gave up after {config.MAX_ATTEMPTS} attempts ({last_problem})")
