"""Follow-up work for a machine status change.

A change is emitted synchronously with the write that caused it; deciding
what it means for a verifier is done off the request/scrape path, because
a scrape run should not get slower because a board moved a status.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from .models import StatusChange
from .queue import verification_queue

logger = logging.getLogger(__name__)


@shared_task(name="verification.tasks.process_status_change")
def process_status_change(change_id: int) -> dict[str, object]:
    """Work out whether this change actually needs a verifier, and record
    the answer on the change.

    Not every machine change does. A scraper confirming what a human
    already said moves `machine_value` - which is a real transition worth
    logging - without putting the track in front of anyone, and marking it
    as needing verification would pad the queue with agreement.

    Membership is read from `verification_queue()` rather than re-derived
    here. The queue's predicates are the definition of "needs a verifier",
    and a second implementation of that rule would drift from the one the
    API and the console actually use.
    """
    change = StatusChange.objects.filter(pk=change_id).select_related("status_track").first()
    if change is None:
        # The stage was deleted between the write and the worker picking
        # this up. A normal race, not an error worth paging anyone over.
        logger.warning("process_status_change: change %s no longer exists", change_id)
        return {"change_id": change_id, "status": "missing"}

    queued = verification_queue().filter(pk=change.status_track_id).first()

    change.needs_verification = queued is not None
    # reason_code is annotated onto the queryset rather than being a field,
    # so it is read off the instance - django-stubs cannot resolve an
    # annotation as a .values() keyword, and it would be right to object.
    change.queue_reason = getattr(queued, "reason_code", "") if queued else ""
    change.processed_at = timezone.now()
    change.save(update_fields=["needs_verification", "queue_reason", "processed_at"])

    logger.info(
        "process_status_change: %s needs_verification=%s reason=%r",
        change,
        change.needs_verification,
        change.queue_reason,
    )
    return {
        "change_id": change_id,
        "status": "ok",
        "needs_verification": change.needs_verification,
        "queue_reason": change.queue_reason,
    }


@shared_task(name="verification.tasks.send_verification_digest")
def send_verification_digest() -> dict[str, object]:
    """Daily digest of everything waiting for a verifier.

    Scheduled from CELERY_BEAT_SCHEDULE rather than from a Source row: the
    Source-derived schedules (EXT-046) are per-board scrape jobs, and this
    is a fixed system job that exists whether or not any board is
    configured.
    """
    from .digest import send_digest

    return send_digest()


@shared_task(name="verification.tasks.send_elapsed_date_alerts")
def send_elapsed_date_alerts() -> dict[str, object]:
    """Alert on stages whose planned date passed with nothing recorded.

    Separate from the digest on purpose. The digest is a routine prompt to
    work a queue; this is "something that should have happened did not",
    which is a different thing to be told and fires once per stage rather
    than every morning.
    """
    from .alerts import send_elapsed_date_alerts as run

    return run()
