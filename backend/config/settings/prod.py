"""Production settings. Staging uses this same module.

Staging differing from production only by environment is the point: a
staging environment configured differently is a rehearsal of something
you are not about to do. Everything that varies between the two - domain,
database, secrets, whether HTTPS is enforced - is read from the
environment, so the code path is identical.
"""

import os

from .base import *  # noqa: F401,F403


def _flag(name: str, default: str) -> bool:
    return (os.environ.get(name) or default).strip().lower() in {"1", "true", "yes", "on"}


DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

if not ALLOWED_HOSTS:  # noqa: F405
    raise RuntimeError("DJANGO_ALLOWED_HOSTS must be set in production")

# --- HTTPS ------------------------------------------------------------
# TLS terminates at the reverse proxy, so Django never sees an https://
# request directly. Without this it believes every request is insecure and
# SECURE_SSL_REDIRECT loops forever - redirecting to a URL it will again
# consider insecure.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

#: Off only for a local rehearsal of this configuration over plain HTTP.
#: In a real deployment the proxy already redirects, and this is the
#: backstop for anything that reaches Django another way.
SECURE_SSL_REDIRECT = _flag("SECURE_SSL_REDIRECT", "True")

#: The container healthcheck speaks plain HTTP to localhost:8000 - it is
#: inside the container, so there is no TLS to speak. Without this
#: exemption Django 301s it to https://localhost:8000, which nothing
#: serves, the healthcheck never passes, and the proxy waits forever on a
#: dependency that is actually fine. Found by deploying it.
#:
#: Safe to exempt: the endpoint returns {"status": "ok"} and nothing else.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]

#: Cookies that carry a session or a CSRF token must never be sent in
#: clear text. These are the two that let someone act as a verifier.
SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", "True")
CSRF_COOKIE_SECURE = _flag("CSRF_COOKIE_SECURE", "True")

#: HSTS tells browsers never to try http:// for this host again.
#:
#: Deliberately conservative by default, and worth understanding before
#: raising: a browser that has seen this header will refuse plain HTTP for
#: the whole max-age, and there is no way to retract it early. One hour is
#: safe to deploy; raise it to a year once you are confident, and only
#: then turn on preload.
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "3600") or 3600)
SECURE_HSTS_INCLUDE_SUBDOMAINS = _flag("SECURE_HSTS_INCLUDE_SUBDOMAINS", "False")
SECURE_HSTS_PRELOAD = _flag("SECURE_HSTS_PRELOAD", "False")

#: Django only trusts the Origin header for CSRF on unsafe methods, and
#: behind a proxy it must be told the public scheme+host.
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
] or [f"https://{host}" for host in ALLOWED_HOSTS if host not in {"*", "localhost"}]  # noqa: F405

# --- Static files -----------------------------------------------------
# Served by WhiteNoise from the application process. The proxy serves the
# built SPA, but Django still needs to serve its own admin CSS, and
# routing that through the proxy would mean a shared volume between two
# containers for the sake of a few files.
STATIC_ROOT = os.environ.get("STATIC_ROOT", "/app/staticfiles")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Immediately after SecurityMiddleware, which is where WhiteNoise expects
# to sit so it can serve a static file without the rest of the stack.
MIDDLEWARE.insert(  # noqa: F405
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,  # noqa: F405
    "whitenoise.middleware.WhiteNoiseMiddleware",
)
