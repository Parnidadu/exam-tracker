"""Celery tasks for scraping.

The pipeline, end to end: fetch a source, store the snapshot, and - only
when the body actually changed - parse it, match each observation to an
ExamStage and either write it or queue it for a human.

The short-circuit on an unchanged body is the point of EXT-042: most
polls find nothing new, and re-parsing an identical page would re-run the
matcher over observations already dealt with.
"""

from __future__ import annotations

import logging

from celery import shared_task

from .fetch import FetchError
from .health import record_failure, record_success, touch
from .matching import link_observation
from .models import Source
from .parsers import ParserNotFound, get_parser
from .snapshots import SnapshotResult, fetch_and_store
from .triage import queue_observation

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
    reconciled = _reconcile(source, result)
    return {
        "source_id": source_id,
        "status": "ok",
        "changed": result.changed,
        "should_parse": result.should_parse,
        "content_hash": result.snapshot.content_hash,
        **reconciled,
    }


def _reconcile(source: Source, result: SnapshotResult) -> dict[str, object]:
    """Parse a changed page and route each observation.

    Every failure here is contained. A broken parser, a board that
    redesigned its markup, or a `parser_key` that names nothing must not
    turn a successful fetch into a failed run - the snapshot is already
    stored, and losing it would mean losing the evidence of what the board
    actually served.
    """
    if not result.should_parse:
        return {"parsed": False}

    try:
        parser = get_parser(source.parser_key)
    except ParserNotFound:
        # Source.parser_key is free text by design (EXT-040), so this is a
        # configuration mistake rather than a bug, and it is fixed in
        # admin without a deploy.
        logger.warning(
            "scrape_source: %s names parser %r, which is not registered",
            source,
            source.parser_key,
        )
        return {"parsed": False, "parser_missing": source.parser_key}

    try:
        observations = parser.parse(result.snapshot.content)
    except Exception:
        logger.exception("scrape_source: parser %r failed on %s", source.parser_key, source)
        return {"parsed": False, "parser_failed": source.parser_key}

    linked = queued = 0
    for observation in observations:
        match, _applied = link_observation(observation, board=source.board)
        if match.matched:
            linked += 1
        else:
            queue_observation(observation, match, source=source)
            queued += 1

    logger.info(
        "scrape_source: %s produced %s observations - %s linked, %s queued for triage",
        source,
        len(observations),
        linked,
        queued,
    )
    return {
        "parsed": True,
        "observations": len(observations),
        "linked": linked,
        "queued_for_triage": queued,
    }
