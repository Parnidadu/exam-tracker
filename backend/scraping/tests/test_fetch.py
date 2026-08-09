"""EXT-041: robots.txt, per-domain rate limiting, backoff, user agent.

No real network calls - requests.Session.get is stubbed, so these assert
the fetcher's own behaviour rather than a remote site's.
"""

import time
from unittest.mock import patch

import pytest
import requests
from django.core.cache import cache

from scraping.fetch import (
    FetchFailed,
    RateLimitTimeout,
    RobotsDisallowed,
    domain_of,
    fetch,
    is_allowed,
)

ALLOW_ALL = "User-agent: *\nAllow: /\n"
DISALLOW_NOTICES = "User-agent: *\nDisallow: /notices\n"


class FakeResponse:
    def __init__(self, status_code=200, text="", content=b"", headers=None):
        self.status_code = status_code
        self.text = text
        self.content = content or text.encode()
        self.headers = headers or {}


def make_get(robots_body=ALLOW_ALL, pages=None, error=None):
    """Stub for Session.get: serves robots.txt, then the queued pages.

    `pages` is consumed one per call, so a test can script a 500 followed
    by a 200 and assert the retry actually happened.
    """
    queue = list(pages or [FakeResponse(200, "ok")])
    calls: list[str] = []

    def _get(url, *args, **kwargs):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResponse(200, robots_body)
        if error is not None:
            raise error
        return queue.pop(0) if len(queue) > 1 else queue[0]

    _get.calls = calls  # type: ignore[attr-defined]
    return _get


@pytest.fixture(autouse=True)
def _fast_and_isolated(settings):
    """Keep the suite quick: real backoff and rate-limit pauses would add
    seconds per test without testing anything extra."""
    cache.clear()
    settings.SCRAPER_BACKOFF_BASE = 0
    settings.SCRAPER_RATE_LIMIT_SECONDS = 0
    settings.SCRAPER_MAX_ATTEMPTS = 3
    yield
    cache.clear()


# --- robots.txt -------------------------------------------------------


def test_a_disallowed_url_is_never_fetched():
    get = make_get(robots_body=DISALLOW_NOTICES)
    with patch.object(requests.Session, "get", side_effect=get):
        with pytest.raises(RobotsDisallowed):
            fetch("https://upsc.test/notices/1")

    # robots.txt was read, but the page itself was never requested.
    assert all(url.endswith("/robots.txt") for url in get.calls)


def test_an_allowed_url_is_fetched():
    get = make_get(robots_body=DISALLOW_NOTICES)
    with patch.object(requests.Session, "get", side_effect=get):
        result = fetch("https://upsc.test/results/1")

    assert result.status_code == 200
    assert "https://upsc.test/results/1" in get.calls


def test_is_allowed_reports_robots_rules():
    get = make_get(robots_body=DISALLOW_NOTICES)
    with patch.object(requests.Session, "get", side_effect=get):
        assert is_allowed("https://upsc.test/results/1") is True
        assert is_allowed("https://upsc.test/notices/1") is False


def test_robots_is_cached_rather_than_refetched_per_request():
    """Re-downloading robots.txt for every URL would itself be impolite."""
    get = make_get()
    with patch.object(requests.Session, "get", side_effect=get):
        fetch("https://upsc.test/a")
        fetch("https://upsc.test/b")

    assert sum(1 for url in get.calls if url.endswith("/robots.txt")) == 1


def test_an_unreachable_robots_txt_does_not_block_crawling():
    """A site that never serves robots.txt has stated no rules; treating
    that as a blanket ban would stop scraping entirely."""

    def get(url, *args, **kwargs):
        if url.endswith("/robots.txt"):
            raise requests.ConnectionError("no robots")
        return FakeResponse(200, "ok")

    with patch.object(requests.Session, "get", side_effect=get):
        assert fetch("https://upsc.test/a").status_code == 200


# --- user agent -------------------------------------------------------


def test_every_request_identifies_the_crawler(settings):
    settings.SCRAPER_USER_AGENT = "ExamTrackerBot/9.9 (+https://example.test)"
    seen = []

    def get(self, url, *args, **kwargs):
        seen.append(self.headers.get("User-Agent"))
        return FakeResponse(200, ALLOW_ALL if url.endswith("robots.txt") else "ok")

    with patch.object(requests.Session, "get", get):
        fetch("https://upsc.test/a")

    # Both the robots fetch and the page fetch carry it.
    assert seen == ["ExamTrackerBot/9.9 (+https://example.test)"] * 2


def test_robots_rules_are_evaluated_against_our_user_agent(settings):
    """A site can ban this crawler specifically, and that must beat the
    wildcard rule.

    robots.txt names a *product token* - `User-agent: Googlebot`, not
    `Googlebot/2.1` - and urllib.robotparser matches by taking the part of
    our UA before the slash. So a full UA of "ExamTrackerBot/1.0 (+url)"
    is matched by a robots line reading "ExamTrackerBot".
    """
    settings.SCRAPER_USER_AGENT = "ExamTrackerBot/1.0 (+https://example.test)"
    body = "User-agent: ExamTrackerBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"

    with patch.object(requests.Session, "get", side_effect=make_get(robots_body=body)):
        assert is_allowed("https://upsc.test/anything") is False


# --- backoff / retries ------------------------------------------------


def test_a_5xx_is_retried_and_can_succeed():
    get = make_get(pages=[FakeResponse(503, "down"), FakeResponse(200, "ok")])
    with patch.object(requests.Session, "get", side_effect=get):
        result = fetch("https://upsc.test/a")

    assert result.status_code == 200
    assert sum(1 for url in get.calls if not url.endswith("robots.txt")) == 2


def test_persistent_5xx_eventually_raises(settings):
    settings.SCRAPER_MAX_ATTEMPTS = 3
    get = make_get(pages=[FakeResponse(500, "boom")])

    with patch.object(requests.Session, "get", side_effect=get):
        with pytest.raises(FetchFailed):
            fetch("https://upsc.test/a")

    assert sum(1 for url in get.calls if not url.endswith("robots.txt")) == 3


def test_connection_errors_are_retried():
    with patch.object(
        requests.Session, "get", side_effect=make_get(error=requests.ConnectionError("x"))
    ):
        with pytest.raises(FetchFailed):
            fetch("https://upsc.test/a")


def test_a_4xx_is_returned_rather_than_retried():
    """Repeating a 404 cannot change the answer and only adds load."""
    get = make_get(pages=[FakeResponse(404, "missing")])
    with patch.object(requests.Session, "get", side_effect=get):
        result = fetch("https://upsc.test/a")

    assert result.status_code == 404
    assert sum(1 for url in get.calls if not url.endswith("robots.txt")) == 1


def test_backoff_grows_exponentially(settings):
    settings.SCRAPER_BACKOFF_BASE = 1
    settings.SCRAPER_MAX_ATTEMPTS = 4
    slept: list[float] = []

    with patch.object(
        requests.Session, "get", side_effect=make_get(pages=[FakeResponse(500, "x")])
    ):
        with patch("scraping.fetch.time.sleep", slept.append):
            with pytest.raises(FetchFailed):
                fetch("https://upsc.test/a")

    # 1, 2, 4 - doubling - and no pause after the final attempt.
    assert slept == [1, 2, 4]


# --- rate limiting ----------------------------------------------------


def test_a_second_request_to_a_domain_waits_for_its_slot(settings):
    settings.SCRAPER_RATE_LIMIT_SECONDS = 5
    slept: list[float] = []

    with patch.object(requests.Session, "get", side_effect=make_get()):
        fetch("https://upsc.test/a")
        with patch("scraping.fetch.time.sleep", slept.append):
            # Second call finds the slot taken; let it time out rather than
            # actually sleeping through the interval.
            settings.SCRAPER_RATE_LIMIT_MAX_WAIT = 0
            with pytest.raises(RateLimitTimeout):
                fetch("https://upsc.test/b")


def test_the_rate_limit_is_per_domain_not_global(settings):
    settings.SCRAPER_RATE_LIMIT_SECONDS = 5
    settings.SCRAPER_RATE_LIMIT_MAX_WAIT = 0

    with patch.object(requests.Session, "get", side_effect=make_get()):
        assert fetch("https://upsc.test/a").status_code == 200
        # A different board must not be blocked by the first one's slot.
        assert fetch("https://ssc.test/a").status_code == 200


def test_waiting_for_a_slot_gives_up_rather_than_blocking_forever(settings):
    settings.SCRAPER_RATE_LIMIT_SECONDS = 60
    settings.SCRAPER_RATE_LIMIT_MAX_WAIT = 0.05

    with patch.object(requests.Session, "get", side_effect=make_get()):
        fetch("https://upsc.test/a")
        started = time.monotonic()
        with pytest.raises(RateLimitTimeout):
            fetch("https://upsc.test/b")

    assert time.monotonic() - started < 5


# --- misc -------------------------------------------------------------


def test_domain_of_ignores_path_and_query():
    assert domain_of("https://upsc.test/a/b?c=1") == "https://upsc.test"


def test_result_exposes_decoded_text():
    get = make_get(pages=[FakeResponse(200, content="héllo".encode())])
    with patch.object(requests.Session, "get", side_effect=get):
        assert fetch("https://upsc.test/a").text == "héllo"
