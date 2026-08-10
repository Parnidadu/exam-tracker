"""EXT-062: per-IP throttling on the public API."""

import pytest
from rest_framework.settings import api_settings
from rest_framework.test import APIClient
from rest_framework.throttling import (
    AnonRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
)

from accounts.models import Role, User
from exams.models import Board

pytestmark = pytest.mark.django_db

PUBLIC = "/api/boards/"


@pytest.fixture
def tight(monkeypatch):
    """Squeeze the limits down so a test can reach them in a few calls.

    Patched onto SimpleRateThrottle rather than through override_settings:
    THROTTLE_RATES is read off the class, and DRF binds it at import, so
    overriding REST_FRAMEWORK afterwards leaves the already-bound dict in
    place and the test would quietly assert nothing.
    """
    monkeypatch.setattr(
        SimpleRateThrottle, "THROTTLE_RATES", {"anon": "3/min", "user": "5/min"}
    )


@pytest.fixture(autouse=True)
def a_board():
    Board.objects.create(
        name="Union Public Service Commission", code="UPSC", official_url="https://u"
    )


def hammer(client, count, path=PUBLIC, **extra):
    return [client.get(path, **extra).status_code for _ in range(count)]


# --- per-IP throttle on public endpoints --------------------------------


def test_an_anonymous_caller_is_throttled(tight):
    codes = hammer(APIClient(), 5)

    assert codes == [200, 200, 200, 429, 429]


def test_the_throttle_answers_429_not_a_server_error(tight):
    client = APIClient()
    hammer(client, 3)

    response = client.get(PUBLIC)

    assert response.status_code == 429


def test_a_throttled_response_says_how_long_to_wait(tight):
    """A 429 with no Retry-After leaves a well-behaved client guessing,
    and a badly-behaved one retrying immediately."""
    client = APIClient()
    hammer(client, 3)

    response = client.get(PUBLIC)

    assert response.has_header("Retry-After")
    assert int(response["Retry-After"]) > 0


@pytest.mark.parametrize(
    "path",
    ["/api/boards/", "/api/exams/", "/api/discrepancy-feed/", "/api/calendar/?month=2026-06"],
)
def test_every_public_endpoint_is_covered(tight, path):
    """Applied as a DRF default rather than per view, so a new public
    endpoint is protected the day it is written rather than the day
    someone remembers."""
    codes = hammer(APIClient(), 5, path=path)

    assert 429 in codes


def test_two_different_addresses_do_not_share_a_budget(tight):
    """Per IP, not global: one heavy visitor must not lock everyone else
    out, which would turn a rate limit into a denial of service."""
    first = APIClient()
    second = APIClient()

    assert hammer(first, 4, REMOTE_ADDR="203.0.113.10") == [200, 200, 200, 429]
    assert hammer(second, 3, REMOTE_ADDR="203.0.113.11") == [200, 200, 200]


def test_the_budget_follows_the_address_not_the_client_object(tight):
    client = APIClient()
    hammer(client, 3, REMOTE_ADDR="203.0.113.20")

    assert client.get(PUBLIC, REMOTE_ADDR="203.0.113.20").status_code == 429
    assert client.get(PUBLIC, REMOTE_ADDR="203.0.113.21").status_code == 200


# --- the header that would have made it decorative ----------------------


def test_a_forged_forwarded_for_header_does_not_buy_a_fresh_budget(tight):
    """DRF's default identifies a client by the whole X-Forwarded-For
    header when one is present - and that header comes from the client.
    Varying it per request would hand an attacker an unlimited supply of
    throttle buckets, which is a rate limit in name only.

    NUM_PROXIES=0 pins identity to REMOTE_ADDR instead.
    """
    client = APIClient()

    codes = [
        client.get(
            PUBLIC, REMOTE_ADDR="203.0.113.30", HTTP_X_FORWARDED_FOR=f"10.0.0.{i}"
        ).status_code
        for i in range(6)
    ]

    assert codes == [200, 200, 200, 429, 429, 429]


def test_the_proxy_count_is_configured_explicitly():
    """Not left at DRF's default of None, which is the permissive one."""
    assert api_settings.NUM_PROXIES is not None
    assert api_settings.NUM_PROXIES == 0


# --- staff are throttled separately -------------------------------------


def signed_in(email="v@example.gov.in"):
    """A real session login, not force_authenticate.

    force_authenticate patches only DRF's request object, which is built
    inside DRF's dispatch - and the cached path answers before that. A
    session login puts the user on the plain Django request via
    AuthenticationMiddleware, which is how the frontend actually
    authenticates, so this is the path worth testing.
    """
    user = User.objects.create_user(email=email, password="x", role=Role.VERIFIER)
    client = APIClient()
    client.force_login(user)
    return client


def test_a_signed_in_user_gets_the_higher_ceiling(tight):
    """A verifier working the queue by keyboard makes bursts that would
    look abusive from an anonymous client. Rate-limiting your own
    operators out of the console during an incident is its own outage."""
    codes = hammer(signed_in(), 5)

    assert codes == [200] * 5


def test_staff_are_still_throttled_eventually(tight):
    codes = hammer(signed_in(), 7)

    assert 429 in codes


def test_two_users_do_not_share_a_budget(tight):
    """Keyed on the account, so one busy verifier cannot throttle
    another - even from the same office IP."""
    hammer(signed_in("a@example.gov.in"), 6)

    assert signed_in("b@example.gov.in").get(PUBLIC).status_code == 200


# --- configuration ------------------------------------------------------


def test_both_throttles_are_installed_by_default():
    installed = {cls.__name__ for cls in api_settings.DEFAULT_THROTTLE_CLASSES}

    assert {"AnonRateThrottle", "UserRateThrottle"} <= installed


def test_the_rates_are_configurable(settings):
    assert AnonRateThrottle.THROTTLE_RATES["anon"] == settings.THROTTLE_ANON_RATE
    assert UserRateThrottle.THROTTLE_RATES["user"] == settings.THROTTLE_USER_RATE


def test_the_shipped_anonymous_rate_is_a_real_limit(settings):
    """A limit nobody could reach is decoration."""
    count, _, period = settings.THROTTLE_ANON_RATE.partition("/")

    assert int(count) > 0
    assert period in {"s", "sec", "m", "min", "h", "hour", "d", "day"}


def test_the_health_check_is_not_throttled(client, tight):
    """Load balancers poll it constantly, and throttling it turns a
    healthy service into an unhealthy-looking one."""
    codes = [client.get("/health/").status_code for _ in range(10)]

    assert codes == [200] * 10
