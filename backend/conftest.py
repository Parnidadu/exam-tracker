import pytest
from django.core.cache import cache

from config.celery import app as celery_app


@pytest.fixture(autouse=True, scope="session")
def celery_runs_inline():
    """Run Celery tasks in-process for the whole suite (EXT-052).

    Without this, anything that calls `.delay()` blocks trying to reach a
    broker that is not running - the suite went from seconds to a ten
    minute timeout the moment a status change started queueing follow-up
    work. CI has no Redis service, and adding one to test a task that can
    be called directly would buy nothing.

    Set on `app.conf` rather than through the Django setting: the Celery
    app copies its configuration at import time, so changing
    CELERY_TASK_ALWAYS_EAGER afterwards has no effect on it.
    """
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield


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
