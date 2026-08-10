"""Detecting machine status changes and emitting an event for each.

Hooked onto StatusTrack's own save rather than onto
`apply_machine_observation`. That helper is the intended write path, but
the model already carries a save-time backstop for exactly this reason -
so that the rule holds "even for code that writes the model directly: a
future scraper, a management command, or the admin". A change log that
only saw one caller would quietly under-report the moment anything else
touched a machine value, and an under-reporting change log is worse than
none because it looks complete.

What counts as a change
-----------------------
`machine_value` moving to something different. Specifically not:

* the same value being re-observed - most polls confirm what is already
  there, and a row per source per run would tell a verifier nothing about
  what moved;
* confidence shifting on its own - the value is the status, and 0.8 to
  0.9 on "declared" is not a transition;
* a human verifying - that writes human_value and is not a machine
  observation at all.

Going from nothing to something *is* a change: a status appeared where
there was none, and that is exactly what a verifier should look at.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.db.models.signals import post_save, pre_save

from exams.models import StatusTrack

from .models import StatusChange

logger = logging.getLogger(__name__)

#: Where the pre-save value is parked between the two signals. Named with
#: a leading underscore because it is an implementation detail of this
#: module living on someone else's instance.
_STASH = "_previous_machine_state"


def _remember_previous(
    sender: type[StatusTrack], instance: StatusTrack, **kwargs: Any
) -> None:
    """Stash what the row looked like before this save.

    Read from the database rather than from the instance: the instance's
    attributes are already the *new* values by the time anything saves,
    so it cannot answer what they used to be.
    """
    if instance.pk is None:
        setattr(instance, _STASH, ("", None))
        return

    previous = StatusTrack.objects.filter(pk=instance.pk).values(
        "machine_value", "machine_confidence"
    ).first()
    if previous is None:
        setattr(instance, _STASH, ("", None))
    else:
        setattr(instance, _STASH, (previous["machine_value"], previous["machine_confidence"]))


def _emit_if_changed(
    sender: type[StatusTrack], instance: StatusTrack, created: bool, **kwargs: Any
) -> None:
    previous_value, previous_confidence = getattr(instance, _STASH, ("", None))
    if previous_value == instance.machine_value:
        return

    record_change(
        instance,
        previous_value=previous_value,
        previous_confidence=previous_confidence,
    )


def record_change(
    status_track: StatusTrack,
    *,
    previous_value: str,
    previous_confidence: float | None,
) -> StatusChange:
    """Write the event and queue the follow-up.

    The task is enqueued through `transaction.on_commit`, so a write that
    is rolled back never produces work for a change that did not happen.
    Enqueueing inline would also hand the worker an id that is not yet
    visible to it - the row is uncommitted - and the task would fail on a
    change that is about to become perfectly real.
    """
    change = StatusChange.objects.create(
        status_track=status_track,
        previous_value=previous_value,
        new_value=status_track.machine_value,
        previous_confidence=previous_confidence,
        new_confidence=status_track.machine_confidence,
        observed_at=status_track.machine_seen_at,
    )

    transaction.on_commit(lambda: _enqueue(change.pk))
    logger.info(
        "status change %s: %s %r -> %r",
        change.pk,
        status_track,
        previous_value,
        status_track.machine_value,
    )
    return change


def _enqueue(change_id: int) -> None:
    """Hand the change to the worker.

    Import here rather than at module level: this module is imported from
    an AppConfig.ready(), and pulling in the tasks module - which imports
    the Celery app - at that point would run before the app registry is
    finished loading.

    A broker that is unreachable is logged and swallowed. The StatusChange
    row is already committed and is the durable artefact; raising here
    would push a Redis outage back up into whatever caused the status to
    move - a scrape run, or an operator resolving a triage item - and fail
    work that actually succeeded. The change is still on record, and still
    findable by its null `needs_verification`.
    """
    from .tasks import process_status_change

    try:
        # retry=False so an unreachable broker fails now rather than being
        # retried for over a minute while the caller waits. Measured: with
        # kombu's default retry policy this blocked for 79 seconds per
        # change, which a scrape emitting several of them would turn into
        # minutes and push past the task time limit. Retrying the publish
        # buys nothing anyway - the row is already committed.
        async_result = process_status_change.apply_async(args=[change_id], retry=False)
    except Exception:
        logger.exception(
            "status change %s written, but the follow-up task could not be queued",
            change_id,
        )
        return

    StatusChange.objects.filter(pk=change_id).update(
        verification_task_id=str(async_result.id or "")
    )


def connect() -> None:
    """Wire the signals. Called from VerificationConfig.ready()."""
    pre_save.connect(
        _remember_previous, sender=StatusTrack, dispatch_uid="status_change_remember"
    )
    post_save.connect(_emit_if_changed, sender=StatusTrack, dispatch_uid="status_change_emit")
