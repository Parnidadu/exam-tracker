"""Recording and judging source health.

Two separate questions, and they are not the same one:

* **Is it failing?** - the run raised, and `consecutive_failures` counts
  how many in a row. Loud and obvious.
* **Is it stale?** - no run has *succeeded* for longer than its schedule
  says it should have. This is the quiet failure, and the dangerous one:
  a source whose beat schedule was disabled, or whose task never got
  queued, reports no failures at all. Counting failures alone would call
  that source perfectly healthy right up until someone noticed the exam
  data was months old.

Staleness is judged against each source's own cron rather than one global
timeout, because "hasn't succeeded in 6 hours" means nothing for a source
polled weekly and means something is badly wrong for one polled hourly.
"""

from __future__ import annotations

import logging
from datetime import datetime

from croniter import croniter
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Source, SourceHealth

logger = logging.getLogger(__name__)


def health_for(source: Source) -> SourceHealth:
    health, _ = SourceHealth.objects.get_or_create(source=source)
    return health


def record_success(source: Source) -> SourceHealth:
    """A run completed. Clears the failure streak."""
    now = timezone.now()
    SourceHealth.objects.update_or_create(
        source=source,
        defaults={
            "last_success_at": now,
            "last_checked_at": now,
            "consecutive_failures": 0,
            "last_error": "",
        },
    )
    return health_for(source)


@transaction.atomic
def record_failure(source: Source, error: str) -> SourceHealth:
    """A run failed. Increments the streak and may raise an alert."""
    now = timezone.now()
    health, created = SourceHealth.objects.select_for_update().get_or_create(
        source=source,
        defaults={
            "last_failure_at": now,
            "last_checked_at": now,
            "consecutive_failures": 1,
            "last_error": error,
        },
    )
    if not created:
        # F() rather than health.consecutive_failures + 1: two workers
        # failing on the same source at once would otherwise both read the
        # same value and write the same increment, losing one.
        SourceHealth.objects.filter(pk=health.pk).update(
            last_failure_at=now,
            last_checked_at=now,
            consecutive_failures=F("consecutive_failures") + 1,
            last_error=error,
        )
        health.refresh_from_db()

    _alert_if_failing(source, health)
    return health


def touch(source: Source) -> SourceHealth:
    """Record that a run happened without judging it.

    Used for outcomes that are neither - a disabled source, or one deleted
    between beat queueing the task and the worker picking it up. Counting
    those as failures would make a tidy admin action look like a broken
    board.
    """
    SourceHealth.objects.update_or_create(
        source=source, defaults={"last_checked_at": timezone.now()}
    )
    return health_for(source)


# --- staleness ---------------------------------------------------------


def missed_runs(source: Source, health: SourceHealth, *, now: datetime | None = None) -> int:
    """How many scheduled runs have come and gone since the last success.

    Counted by walking the source's own cron forward from the last
    success, so an irregular schedule ("0 9 * * 1-5") is handled exactly
    rather than approximated by an average gap.

    Counting stops once the threshold is exceeded. Without that cap, a
    source last successful a year ago on a per-minute cron would spin
    through half a million iterations to answer a yes/no question.
    """
    now = now or timezone.now()
    since = health.last_success_at

    if since is None:
        # Never succeeded. Measure from the first time we ever looked, so
        # a source added moments ago is not instantly stale.
        since = health.last_checked_at
        if since is None:
            return 0

    if since >= now:
        return 0

    cap = alert_threshold() + 1
    iterator = croniter(source.cron, since)
    count = 0
    while count < cap:
        if iterator.get_next(datetime) > now:
            break
        count += 1
    return count


def alert_threshold() -> int:
    """How many runs may go wrong before a source is worth shouting about.

    One number governs both faults on purpose: whether a run went wrong
    loudly (it raised) or quietly (it never happened), N of them in a row
    means the same thing to an operator. Two separate knobs would only
    invite them to drift apart.
    """
    return int(getattr(settings, "SOURCE_STALE_AFTER_MISSED_RUNS", 3))


def is_stale(source: Source, health: SourceHealth, *, now: datetime | None = None) -> bool:
    """True when the source has missed more consecutive scheduled runs
    than the threshold allows."""
    return missed_runs(source, health, now=now) > alert_threshold()


def stale_sources() -> list[tuple[Source, SourceHealth]]:
    """Every enabled source that is overdue.

    Disabled sources are excluded on purpose: one paused deliberately is
    not a fault, and reporting it as one trains operators to ignore the
    list.
    """
    now = timezone.now()
    overdue = []
    for source in Source.objects.filter(enabled=True).select_related("board"):
        health = health_for(source)
        if is_stale(source, health, now=now):
            overdue.append((source, health))
    return overdue


# --- alerting ----------------------------------------------------------


def _alert_if_failing(source: Source, health: SourceHealth) -> None:
    """Log at ERROR once a source crosses the failure threshold.

    ERROR rather than WARNING because Sentry's logging integration turns
    ERROR into an event - so this is a real alert on the path that already
    exists, not a line in a file nobody reads. Building an email or Slack
    route belongs to the notifications epic (EXT-060).

    Fires on the crossing only. Logging every subsequent failure would
    send one alert per run for as long as a board stays down, which is how
    alerting gets muted.
    """
    threshold = alert_threshold()
    if health.consecutive_failures == threshold + 1:
        logger.error(
            "Source %s has failed %s times in a row: %s",
            source,
            health.consecutive_failures,
            health.last_error,
            extra={"source_id": source.pk, "source_name": str(source)},
        )


# --- keeping a health row per source -----------------------------------


def on_source_saved(
    sender: type[Source], instance: Source, created: bool, **kwargs: object
) -> None:
    """Give every source a health row as soon as it exists.

    Without this the dashboard only lists sources that have already run,
    so a newly added source - exactly the one most likely to be
    misconfigured - would be invisible until it either worked or failed.
    """
    if created:
        SourceHealth.objects.get_or_create(source=instance)


def connect() -> None:
    """Wire the signal. Called from ScrapingConfig.ready()."""
    from django.db.models.signals import post_save

    post_save.connect(on_source_saved, sender=Source, dispatch_uid="create_source_health")


def report_stale_sources() -> list[tuple[Source, SourceHealth]]:
    """Log an alert for every overdue source and return them.

    The counterpart to `_alert_if_failing`: that one catches sources that
    fail loudly, this one catches sources that have simply gone quiet.
    """
    overdue = stale_sources()
    for source, health in overdue:
        logger.error(
            "Source %s is stale: last success %s, %s scheduled runs missed",
            source,
            health.last_success_at or "never",
            missed_runs(source, health),
            extra={"source_id": source.pk, "source_name": str(source)},
        )
    return overdue
