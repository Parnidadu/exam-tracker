"""Short-TTL response caching for the public read endpoints.

Invalidation is by version prefix rather than by deleting specific keys:
`bump_public_cache_version()` increments a counter that every cache key
embeds, so one call retires every cached public response at once.

That bluntness is deliberate. A single verification changes more than the
one exam it touches - the list endpoint's conduct_status / result_status
filters (EXT-030) resolve through effective_status, so a write can move an
exam in or out of arbitrary filtered result sets. Working out precisely
which cached pages that affects is far easier to get subtly wrong than to
throw the lot away every time someone verifies something.
"""

from django.conf import settings
from django.core.cache import cache
from django.utils.decorators import method_decorator
from django.views.decorators.vary import vary_on_headers

PUBLIC_CACHE_VERSION_KEY = "public-cache-version"


def public_cache_version() -> int:
    """Current version. Seeded rather than defaulted so concurrent readers
    agree on a starting value."""
    version = cache.get(PUBLIC_CACHE_VERSION_KEY)
    if version is None:
        cache.add(PUBLIC_CACHE_VERSION_KEY, 1)
        version = cache.get(PUBLIC_CACHE_VERSION_KEY) or 1
    return int(version)


def bump_public_cache_version() -> int:
    """Retire every cached public response.

    Uses the backend's atomic incr where possible so two simultaneous
    verifications can't both read-modify-write the same value.
    """
    try:
        return int(cache.incr(PUBLIC_CACHE_VERSION_KEY))
    except ValueError:
        # Key absent (never seeded, or evicted): nothing cached under the
        # old version matters, so simply establish one.
        cache.set(PUBLIC_CACHE_VERSION_KEY, 1)
        return 1


class PublicCacheMixin:
    """Caches GET responses for `settings.PUBLIC_CACHE_TTL` seconds.

    The cache key includes whether the caller is authenticated. The
    verification-history endpoint (EXT-027) returns the actor's email only
    to signed-in users, so a shared key would serve one audience's response
    to the other - either leaking a staff email to anonymous visitors or
    hiding it from signed-in ones.
    """

    def _cache_key(self, request) -> str:
        # Sorted, so ?a=1&b=2 and ?b=2&a=1 hit the same entry.
        query = "&".join(f"{key}={value}" for key, value in sorted(request.GET.items()))
        audience = "auth" if request.user.is_authenticated else "anon"
        return f"public:{public_cache_version()}:{audience}:{request.path}?{query}"

    @method_decorator(vary_on_headers("Cookie"))
    def dispatch(self, request, *args, **kwargs):
        if request.method != "GET":
            return super().dispatch(request, *args, **kwargs)

        key = self._cache_key(request)
        cached = cache.get(key)
        if cached is not None:
            cached["X-Cache"] = "HIT"
            return cached

        response = super().dispatch(request, *args, **kwargs)

        # Only cache successful reads; an error page must not stick around
        # for a minute after the cause is fixed.
        if response.status_code == 200:
            response.add_post_render_callback(
                lambda rendered: cache.set(key, rendered, settings.PUBLIC_CACHE_TTL)
            )
        response["X-Cache"] = "MISS"
        return response
