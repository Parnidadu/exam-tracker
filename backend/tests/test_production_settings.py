"""EXT-064: the production settings module, loaded as production loads it.

The suite runs on `config.settings.dev`, so these import the production
module directly with a controlled environment. Without them the HTTPS
settings are only exercised by a deploy, and the next person to debug a
redirect loop can turn one off and nothing will notice.
"""

import importlib
import os
from contextlib import contextmanager

import pytest

REQUIRED = {
    "DJANGO_ALLOWED_HOSTS": "exams.example.gov.in",
    "DJANGO_SECRET_KEY": "x" * 60,
    "POSTGRES_DB": "exam_tracker",
    "POSTGRES_USER": "exam_tracker",
    "POSTGRES_PASSWORD": "secret",
    "POSTGRES_HOST": "db",
}


@contextmanager
def production(**overrides):
    """Import config.settings.prod under a given environment."""
    previous = dict(os.environ)
    os.environ.update({**REQUIRED, **{k: str(v) for k, v in overrides.items()}})
    for key in overrides:
        if overrides[key] is None:
            os.environ.pop(key, None)
    try:
        # base first: prod does `from .base import *`, and values like
        # ALLOWED_HOSTS are computed there. Reloading only prod would keep
        # whatever base read the first time it was imported - which, in
        # this suite, is the dev environment.
        importlib.reload(importlib.import_module("config.settings.base"))
        yield importlib.reload(importlib.import_module("config.settings.prod"))
    finally:
        os.environ.clear()
        os.environ.update(previous)
        # Put the module back the way the rest of the suite expects.
        importlib.reload(importlib.import_module("config.settings.base"))


# --- HTTPS --------------------------------------------------------------


def test_https_is_enforced_by_default():
    with production() as settings:
        assert settings.SECURE_SSL_REDIRECT is True
        assert settings.SESSION_COOKIE_SECURE is True
        assert settings.CSRF_COOKIE_SECURE is True


def test_django_is_told_the_scheme_the_proxy_saw():
    """TLS terminates at Caddy, so Django never sees an https:// request.
    Without this it believes every request is insecure and the SSL
    redirect loops forever."""
    with production() as settings:
        assert settings.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")


def test_the_health_endpoint_is_exempt_from_the_ssl_redirect():
    """The container healthcheck speaks plain HTTP to itself; without the
    exemption Django 301s it, the check never passes, and the proxy waits
    forever on a dependency that is actually fine. This happened."""
    with production() as settings:
        assert any("health" in pattern for pattern in settings.SECURE_REDIRECT_EXEMPT)


def test_hsts_is_on_but_conservative_by_default():
    """A browser that has seen HSTS refuses plain HTTP for the whole
    max-age, and it cannot be retracted early. An hour is safe to deploy;
    a year is a decision someone should make deliberately."""
    with production() as settings:
        assert settings.SECURE_HSTS_SECONDS > 0
        assert settings.SECURE_HSTS_INCLUDE_SUBDOMAINS is False
        assert settings.SECURE_HSTS_PRELOAD is False


def test_the_full_hsts_policy_can_be_opted_into():
    with production(
        SECURE_HSTS_SECONDS=31536000,
        SECURE_HSTS_INCLUDE_SUBDOMAINS="True",
        SECURE_HSTS_PRELOAD="True",
    ) as settings:
        assert settings.SECURE_HSTS_SECONDS == 31536000
        assert settings.SECURE_HSTS_INCLUDE_SUBDOMAINS is True
        assert settings.SECURE_HSTS_PRELOAD is True


def test_https_can_be_relaxed_only_deliberately():
    """A local rehearsal over plain HTTP has to be possible, but it has to
    be asked for."""
    with production(SECURE_SSL_REDIRECT="False") as settings:
        assert settings.SECURE_SSL_REDIRECT is False


# --- what production refuses to start without ---------------------------


def test_production_refuses_to_start_without_allowed_hosts():
    with pytest.raises(RuntimeError, match="DJANGO_ALLOWED_HOSTS"):
        with production(DJANGO_ALLOWED_HOSTS=""):
            pass


def test_csrf_trusted_origins_defaults_to_the_configured_hosts():
    """Django only trusts the Origin header for CSRF on unsafe methods,
    and behind a proxy it must be told the public scheme and host - or
    every verifier's POST is rejected."""
    with production() as settings:
        assert "https://exams.example.gov.in" in settings.CSRF_TRUSTED_ORIGINS


def test_csrf_trusted_origins_can_be_set_explicitly():
    with production(CSRF_TRUSTED_ORIGINS="https://a.example.gov.in,https://b.example.gov.in") as s:
        assert s.CSRF_TRUSTED_ORIGINS == ["https://a.example.gov.in", "https://b.example.gov.in"]


# --- static files -------------------------------------------------------


def test_static_files_are_served_by_the_application():
    """The proxy serves the SPA, but Django still needs its own admin CSS,
    and routing that through the proxy would mean a shared volume between
    two containers for the sake of a few files."""
    with production() as settings:
        assert "whitenoise" in settings.STORAGES["staticfiles"]["BACKEND"].lower()
        assert "whitenoise.middleware.WhiteNoiseMiddleware" in settings.MIDDLEWARE


def test_whitenoise_sits_directly_after_the_security_middleware():
    """Where it can answer for a static file without running the rest of
    the stack, and after SecurityMiddleware has done the redirect."""
    with production() as settings:
        security = settings.MIDDLEWARE.index("django.middleware.security.SecurityMiddleware")
        whitenoise = settings.MIDDLEWARE.index("whitenoise.middleware.WhiteNoiseMiddleware")
        assert whitenoise == security + 1


# --- staging is the same module -----------------------------------------


def test_staging_and_production_differ_only_by_environment():
    """Staging that is configured differently rehearses something you are
    not about to do. Same module, same defaults; only the values change."""
    with production(DJANGO_ALLOWED_HOSTS="staging.exams.example.gov.in") as staging:
        staging_flags = (
            staging.SECURE_SSL_REDIRECT,
            staging.SESSION_COOKIE_SECURE,
            staging.CSRF_COOKIE_SECURE,
        )
        staging_hosts = list(staging.ALLOWED_HOSTS)

    with production() as prod:
        assert staging_flags == (
            prod.SECURE_SSL_REDIRECT,
            prod.SESSION_COOKIE_SECURE,
            prod.CSRF_COOKIE_SECURE,
        )
        assert staging_hosts != list(prod.ALLOWED_HOSTS)
