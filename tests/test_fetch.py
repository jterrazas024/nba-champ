"""Tests for the polite fetch layer. A fake session and fake clock: no network, no real waiting."""
from datetime import date

import pytest
import requests

from pipeline import config
from pipeline.fetch import BRClient, Blocked, FetchError


class FakeResponse:
    def __init__(self, status_code: int = 200, content: bytes = b"<html>ok</html>"):
        self.status_code = status_code
        self.content = content


class FakeSession:
    """Serves queued responses (or raises queued exceptions) and records each request."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests: list[str] = []

    def get(self, url, timeout):
        assert timeout == config.TIMEOUT_SECONDS, "every request needs a timeout"
        self.requests.append(url)
        item = self.queue.pop(0) if self.queue else FakeResponse()
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock:
    """Time only moves when someone sleeps, so the spacing rule can be checked exactly."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(tmp_path, *responses, **kwargs):
    session = FakeSession(*responses)
    clock = FakeClock()
    client = BRClient(session=session, cache_dir=tmp_path / "cache", sleep=clock.sleep,
                      monotonic=clock.monotonic, today=date(2026, 11, 8), **kwargs)
    return client, session, clock


def test_returns_utf8_text_and_caches_the_raw_bytes(tmp_path):
    page = "<html>Dončić</html>".encode("utf-8")
    client, _, _ = make_client(tmp_path, FakeResponse(200, page))
    assert "Dončić" in client.get_html("/x.html", "page")
    assert (tmp_path / "cache" / "page.html").read_bytes() == page


def test_cached_page_makes_no_request(tmp_path):
    client, session, _ = make_client(tmp_path, FakeResponse(200, b"<html>one</html>"))
    client.get_html("/x.html", "page")
    client.get_html("/x.html", "page")
    assert len(session.requests) == 1
    assert client.requests_made == 1


def test_403_stops_with_no_retry_and_no_cache(tmp_path):
    client, session, _ = make_client(tmp_path, FakeResponse(403, b"Access Denied"))
    with pytest.raises(Blocked):
        client.get_html("/x.html", "page")
    assert len(session.requests) == 1
    assert not (tmp_path / "cache" / "page.html").exists()


def test_after_a_block_no_further_requests_are_sent(tmp_path):
    client, session, _ = make_client(tmp_path, FakeResponse(429, b"slow down"))
    with pytest.raises(Blocked):
        client.get_html("/a.html", "a")
    with pytest.raises(Blocked):
        client.get_html("/b.html", "b")
    assert len(session.requests) == 1


def test_a_bot_check_page_with_status_200_is_a_block(tmp_path):
    client, session, _ = make_client(tmp_path, FakeResponse(200, b"<title>Just a moment...</title>"))
    with pytest.raises(Blocked):
        client.get_html("/x.html", "page")
    assert len(session.requests) == 1


def test_server_error_is_retried_with_backoff(tmp_path):
    client, session, clock = make_client(tmp_path, FakeResponse(503), FakeResponse(200, b"<html>ok</html>"))
    assert "ok" in client.get_html("/x.html", "page")
    assert len(session.requests) == 2
    assert config.BACKOFF_SECONDS[0] in clock.sleeps


def test_network_errors_are_retried_then_give_up_with_a_clear_message(tmp_path):
    boom = requests.ConnectionError("down")
    client, session, _ = make_client(tmp_path, boom, boom, boom)
    with pytest.raises(FetchError, match="gave up after 3 attempts"):
        client.get_html("/x.html", "page")
    assert len(session.requests) == config.MAX_ATTEMPTS


def test_404_fails_immediately_without_retry(tmp_path):
    client, session, _ = make_client(tmp_path, FakeResponse(404))
    with pytest.raises(FetchError, match="unexpected HTTP 404"):
        client.get_html("/x.html", "page")
    assert len(session.requests) == 1


def test_requests_are_spaced_at_least_four_seconds_apart(tmp_path):
    client, session, clock = make_client(tmp_path)
    times = []
    original_get = session.get

    def recording_get(url, timeout):
        times.append(clock.now)
        return original_get(url, timeout)

    session.get = recording_get
    for name in ("a", "b", "c"):
        client.get_html(f"/{name}.html", name)
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]
    assert all(gap >= config.SECONDS_BETWEEN_REQUESTS for gap in gaps)


def test_request_cap_stops_the_run_before_sending(tmp_path):
    client, session, _ = make_client(tmp_path, max_requests=2)
    client.get_html("/a.html", "a")
    client.get_html("/b.html", "b")
    with pytest.raises(FetchError, match="request cap"):
        client.get_html("/c.html", "c")
    assert len(session.requests) == 2


def test_retries_count_toward_the_cap(tmp_path):
    client, session, _ = make_client(tmp_path, FakeResponse(500), FakeResponse(500), max_requests=2)
    with pytest.raises(FetchError, match="request cap"):
        client.get_html("/a.html", "a")
    assert len(session.requests) == 2
