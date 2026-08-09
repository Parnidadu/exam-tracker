"""Polite HTTP fetching for scrape sources.

Four rules, all enforced here rather than left to each parser:

* robots.txt is consulted before every request, and a disallowed URL is
  never fetched;
* one request per domain per `SCRAPER_RATE_LIMIT_SECONDS`, coordinated
  through the shared cache so it holds across worker processes;
* 5xx responses and connection errors are retried with exponential
  backoff, while 4xx are not (retrying a 404 just annoys the server);
* every request identifies this crawler by user agent.
"""

import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests
from django.conf import settings
from django.core.cache import cache

#: Distinct prefixes so a robots entry can never collide with a rate-limit
#: key for the same domain.
_ROBOTS_CACHE_PREFIX = "scraper:robots:"
_RATE_LIMIT_PREFIX = "scraper:ratelimit:"


class FetchError(Exception):
    """Base class for every reason a fetch did not produce a response."""


class RobotsDisallowed(FetchError):
    """robots.txt forbids this crawler from fetching the URL."""


class RateLimitTimeout(FetchError):
    """The domain's slot did not free up within the allowed wait."""


class FetchFailed(FetchError):
    """The request kept failing after the last retry."""


@dataclass(frozen=True)
class FetchResult:
    url: str
    status_code: int
    content: bytes
    headers: dict[str, str]

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def domain_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _robots_for(domain: str, session: requests.Session):
    """Fetch and cache a domain's robots.txt.

    Cached because it is consulted before every single request; without
    this a run of 50 URLs would re-download the same file 50 times, which
    is itself impolite.
    """
    cached = cache.get(_ROBOTS_CACHE_PREFIX + domain)
    if cached is not None:
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(cached.splitlines())
        return parser

    parser = urllib.robotparser.RobotFileParser()
    try:
        response = session.get(urljoin(domain, "/robots.txt"), timeout=settings.SCRAPER_TIMEOUT)
        body = response.text if response.status_code == 200 else ""
    except requests.RequestException:
        # An unreachable robots.txt is treated as "no rules stated" rather
        # than as a block: the same as a site serving 404 for it.
        body = ""

    cache.set(_ROBOTS_CACHE_PREFIX + domain, body, settings.SCRAPER_ROBOTS_TTL)
    parser.parse(body.splitlines())
    return parser


def is_allowed(url: str, session: requests.Session | None = None) -> bool:
    """Whether robots.txt permits this crawler to fetch `url`."""
    owned = session is None
    session = session or _build_session()
    try:
        return _robots_for(domain_of(url), session).can_fetch(settings.SCRAPER_USER_AGENT, url)
    finally:
        if owned:
            session.close()


def _wait_for_slot(domain: str) -> None:
    """Hold off until this domain's next request is due.

    Uses cache.add, which is atomic in both Redis and locmem, so two
    workers racing for the same domain cannot both win the slot.
    """
    key = _RATE_LIMIT_PREFIX + domain
    interval = settings.SCRAPER_RATE_LIMIT_SECONDS
    deadline = time.monotonic() + settings.SCRAPER_RATE_LIMIT_MAX_WAIT

    while True:
        if cache.add(key, "1", interval):
            return
        if time.monotonic() >= deadline:
            raise RateLimitTimeout(
                f"Waited {settings.SCRAPER_RATE_LIMIT_MAX_WAIT}s for a slot on {domain}."
            )
        time.sleep(min(interval, 0.5))


def _build_session() -> requests.Session:
    session = requests.Session()
    # Identifying the crawler is a hard requirement, so it is set on the
    # session rather than per call - no request can accidentally omit it.
    session.headers["User-Agent"] = settings.SCRAPER_USER_AGENT
    return session


def fetch(url: str, session: requests.Session | None = None) -> FetchResult:
    """Fetch `url` politely, or raise a FetchError explaining why not.

    Retries 5xx and connection failures with exponential backoff; a 4xx is
    returned to the caller as-is, since repeating it will not change the
    answer.
    """
    owned = session is None
    session = session or _build_session()
    domain = domain_of(url)

    try:
        if not _robots_for(domain, session).can_fetch(settings.SCRAPER_USER_AGENT, url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")

        last_error: str | None = None
        for attempt in range(settings.SCRAPER_MAX_ATTEMPTS):
            _wait_for_slot(domain)

            try:
                response = session.get(url, timeout=settings.SCRAPER_TIMEOUT)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code < 500:
                    return FetchResult(
                        url=url,
                        status_code=response.status_code,
                        content=response.content,
                        headers=dict(response.headers),
                    )
                last_error = f"HTTP {response.status_code}"

            # Don't sleep after the final attempt - nothing follows it.
            if attempt < settings.SCRAPER_MAX_ATTEMPTS - 1:
                time.sleep(settings.SCRAPER_BACKOFF_BASE * (2**attempt))

        raise FetchFailed(
            f"{url} failed after {settings.SCRAPER_MAX_ATTEMPTS} attempts ({last_error})."
        )
    finally:
        if owned:
            session.close()
