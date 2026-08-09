"""Keeps Celery Beat's schedule in step with the `Source` table.

A `Source` already carries everything a schedule needs - a cron expression
and an `enabled` flag - so Beat's schedule is derived from those rows
rather than written out anywhere. Saving a Source writes the matching
`PeriodicTask`; the running beat process notices and reloads.

Why the database scheduler rather than `CELERY_BEAT_SCHEDULE`
-------------------------------------------------------------
The default scheduler reads a Python dict once, at startup. A source
enabled in admin would then sit idle until someone restarted beat - and a
source *disabled* because it was hammering a board would keep hammering
it. Since the acceptance criterion is precisely "without a restart", the
schedule has to live somewhere beat can re-read. That is what
`django_celery_beat`'s `DatabaseScheduler` does, and how it learns a
change happened is by watching a single `PeriodicTasks.last_update` row
that every `PeriodicTask.save()` bumps.

So the rule for this module: **always go through the ORM**. A bulk
`update()` or raw SQL would change the schedule rows without bumping that
timestamp, and beat would happily keep running the old schedule.
"""

from __future__ import annotations

from django_celery_beat.models import CrontabSchedule, PeriodicTask

from .models import Source

#: Dotted path of the task a source's schedule fires.
SCRAPE_TASK = "scraping.tasks.scrape_source"

#: Prefix for the PeriodicTask rows this module owns. Anything with this
#: prefix is managed here and safe to delete; anything without it was
#: created by hand and is left alone.
NAME_PREFIX = "scrape-source-"


def schedule_name(source: Source) -> str:
    """Keyed on pk, not name.

    Renaming a source in admin would otherwise orphan its schedule and
    silently create a second one, leaving the board polled twice.
    """
    return f"{NAME_PREFIX}{source.pk}"


def _crontab_for(cron: str) -> CrontabSchedule:
    """Turn a 5-field cron string into a CrontabSchedule row.

    Fields are assigned by name because the two orderings disagree: cron
    is `minute hour dom month dow`, while CrontabSchedule declares
    day_of_week before day_of_month. Positional assignment would put a
    "Sunday" into day_of_month and schedule something for the 0th of the
    month, which never fires.
    """
    minute, hour, day_of_month, month_of_year, day_of_week = cron.split()
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
    )
    return schedule


def _prune_orphan_crontabs() -> int:
    """Delete CrontabSchedule rows nothing points at any more.

    django_celery_beat never cleans these up itself, so editing one
    source's cron every week leaves a trail of dead rows that make the
    admin schedule pages steadily less readable.
    """
    orphans = CrontabSchedule.objects.filter(periodictask__isnull=True)
    deleted, _ = orphans.delete()
    return deleted


def sync_source_schedule(source: Source) -> PeriodicTask:
    """Create or update the schedule for one source.

    `enabled` is mirrored rather than used to decide whether the row
    exists: keeping a disabled source's PeriodicTask around means
    re-enabling it restores the same schedule, and means admin can still
    show what a paused source *would* run.
    """
    schedule = _crontab_for(source.cron)

    task, _ = PeriodicTask.objects.update_or_create(
        name=schedule_name(source),
        defaults={
            "task": SCRAPE_TASK,
            "crontab": schedule,
            "interval": None,
            "args": f"[{source.pk}]",
            "enabled": source.enabled,
            "description": f"Scrape {source} ({source.url})",
        },
    )
    _prune_orphan_crontabs()
    return task


def remove_source_schedule(source_pk: int) -> None:
    """Drop a deleted source's schedule.

    Takes a pk rather than an instance: by the time post_delete has fired
    the row is gone, and the pk is all that identifies the schedule.
    """
    PeriodicTask.objects.filter(name=f"{NAME_PREFIX}{source_pk}").delete()
    _prune_orphan_crontabs()


def sync_all_sources() -> int:
    """Rebuild every source's schedule, and drop schedules for sources
    that no longer exist.

    Used by the data migration that backfills existing rows, and useful by
    hand if the two ever drift.
    """
    sources = list(Source.objects.all())
    for source in sources:
        sync_source_schedule(source)

    live = {schedule_name(source) for source in sources}
    stale = PeriodicTask.objects.filter(name__startswith=NAME_PREFIX).exclude(name__in=live)
    stale.delete()
    _prune_orphan_crontabs()
    return len(sources)


def on_source_saved(sender: type[Source], instance: Source, **kwargs: object) -> None:
    """post_save hook. Runs inside the caller's transaction on purpose.

    If the save is rolled back the schedule change rolls back with it -
    which would not happen if this were deferred to on_commit and the
    surrounding transaction later failed.
    """
    sync_source_schedule(instance)


def on_source_deleted(sender: type[Source], instance: Source, **kwargs: object) -> None:
    remove_source_schedule(instance.pk)


def connect() -> None:
    """Wire the signals. Called from ScrapingConfig.ready()."""
    from django.db.models.signals import post_delete, post_save

    post_save.connect(on_source_saved, sender=Source, dispatch_uid="sync_source_schedule")
    post_delete.connect(
        on_source_deleted, sender=Source, dispatch_uid="remove_source_schedule"
    )


__all__ = [
    "SCRAPE_TASK",
    "connect",
    "remove_source_schedule",
    "schedule_name",
    "sync_all_sources",
    "sync_source_schedule",
]
