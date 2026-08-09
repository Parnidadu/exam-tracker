import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Isolate every test from the response cache (EXT-034).

    The cache is process-global and deliberately outlives a request, so
    without this one test's cached response is served to the next - which
    shows up as unrelated API tests failing on data they never created.
    """
    cache.clear()
    yield
    cache.clear()
