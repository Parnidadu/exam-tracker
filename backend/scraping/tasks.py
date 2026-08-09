"""Celery tasks for scraping.

Scope note: this task fetches a source and stores the snapshot. It
deliberately stops there. Turning a changed snapshot into observations,
matching them to an ExamStage and queueing the unmatched ones is EXT-050
and EXT-051; wiring it in here would pull that work forward.
"""

from __future__ import annotations

import logging

from celery import shared_task

from .fetch import FetchError
from .health import record_failure, record_success, touch
from .models import Source
from .snapshots import fetch_and_store

logger = logging.getLogger(__name__)


@shared_task(name="scraping.tasks.scrape_source")
def scrape_source(source_id: int) -> dict[str, object]:
    """Fetch one source and store what came back.

    Returns a small summary rather than nothing, so a run's outcome is
    visible in the result backend and in Flower without reading logs.

    Every failure path returns instead of raising. A board being down,
    blocked by robots.txt, or deleted between the schedule firing and the
    task running are all normal operating conditions - raising would mark
    the periodic task as failed and bury the genuinely unexpected errors
    in noise. EXT-047 is where these outcomes become health signals.
    """
    source = Source.objects.filter(pk=source_id).first()
    if source is None:
        # The schedule outliving the source is a real race: beat may have
        # queued this tick before the row was deleted.
        logger.warning("scrape_source: source %s no longer exists", source_id)
        return {"source_id": source_id, "status": "missing"}

    if not source.enabled:
        # Belt and braces. Disabling a source disables its PeriodicTask, so
        # this should not normally be reached - but a task already sitting
        # in the queue when the toggle happened would otherwise still run.
        # Recorded as neither success nor failure (EXT-047): pausing a
        # source deliberately is not a fault.
        logger.info("scrape_source: source %s is disabled, skipping", source_id)
        touch(source)
        return {"source_id": source_id, "status": "disabled"}

    try:
        result = fetch_and_store(source)
    except FetchError as exc:
        logger.warning("scrape_source: %s failed: %s", source, exc)
        health = record_failure(source, str(exc))
        return {
            "source_id": source_id,
            "status": "failed",
            "error": str(exc),
            "consecutive_failures": health.consecutive_failures,
        }
    except Exception as exc:
        # An unexpected error is still a failed run, and health that
        # ignored it would show a source as fine while it broke on every
        # tick. Recorded and re-raised: EXT-046 lets these propagate on
        # purpose, so the traceback still reaches Sentry.
        record_failure(source, f"{type(exc).__name__}: {exc}")
        raise

    record_success(source)
    logger.info(
        "scrape_source: %s stored snapshot %s (changed=%s)",
        source,
        result.snapshot.content_hash[:12],
        result.changed,
    )
    return {
        "source_id": source_id,
        "status": "ok",
        "changed": result.changed,
        "should_parse": result.should_parse,
        "content_hash": result.snapshot.content_hash,
    }
