from datetime import date, datetime

from django.db import models
from django.db.models import Case, CharField, F, IntegerField, Q, QuerySet, Value, When
from django.utils import timezone

from exams.models import StatusTrack

# Lower number = higher priority. Exported so EXT-024's reason-code API
# can reuse these exact predicates instead of re-deriving the business
# logic (and risking drift from this queue's own definitions).
CHANGED_PRIORITY = 1
NO_UPDATE_PRIORITY = 2
STALE_PRIORITY = 3


class ReasonCode(models.TextChoices):
    """Why an item is in the queue. Ordered here highest-priority first,
    matching the *_PRIORITY constants above - the two are annotated from
    the same predicates so a reason can never disagree with its rank."""

    MACHINE_CHANGED = "machine_changed", "Machine observation contradicts the record"
    DATE_ELAPSED_NO_UPDATE = "date_elapsed_no_update", "Planned date passed with no observation"
    STALE_VERIFICATION = "stale_verification", "Verification has aged past the staleness window"


def machine_changed_q() -> Q:
    """The machine has observed something since the last human check, and
    it disagrees with what's on record (including "nothing is on record
    yet" - human_value blank counts as a disagreement too)."""
    return (
        Q(machine_seen_at__isnull=False)
        & (Q(verified_at__isnull=True) | Q(machine_seen_at__gt=F("verified_at")))
        & ~Q(machine_value=F("human_value"))
    )


def date_elapsed_no_update_q(today: date) -> Q:
    """The conduct track's stage was supposed to start by now, and nothing
    - machine or human - has ever been recorded for it."""
    return (
        Q(track=StatusTrack.Track.CONDUCT)
        & Q(exam_stage__planned_start_date__lt=today)
        & Q(machine_seen_at__isnull=True)
        & Q(verified_at__isnull=True)
    )


def stale_q(staleness_cutoff: datetime) -> Q:
    """Verified once, but the verification has aged past the staleness
    window - effective_status has already fallen back to machine_value."""
    return Q(verified_at__isnull=False) & Q(verified_at__lt=staleness_cutoff)


def verification_queue() -> QuerySet[StatusTrack]:
    """StatusTrack rows needing a verifier's attention, ordered by
    priority: machine contradicting the record first, then stages whose
    date has passed with zero observation, then merely stale
    verifications. An item matching more than one reason is queued once,
    at its highest-priority reason's rank.
    """
    now = timezone.now()
    today = now.date()
    staleness_cutoff = now - StatusTrack.STALENESS_WINDOW

    changed = machine_changed_q()
    no_update = date_elapsed_no_update_q(today)
    stale = stale_q(staleness_cutoff)

    return (
        StatusTrack.objects.select_related("exam_stage", "exam_stage__exam")
        .filter(changed | no_update | stale)
        .annotate(
            queue_priority=Case(
                When(changed, then=Value(CHANGED_PRIORITY)),
                When(no_update, then=Value(NO_UPDATE_PRIORITY)),
                When(stale, then=Value(STALE_PRIORITY)),
                default=Value(STALE_PRIORITY + 1),
                output_field=IntegerField(),
            ),
            # Same When() order as queue_priority above, off the same Q
            # objects: an item's reason is always the one its rank came
            # from, never a second independent guess at "why".
            reason_code=Case(
                When(changed, then=Value(ReasonCode.MACHINE_CHANGED)),
                When(no_update, then=Value(ReasonCode.DATE_ELAPSED_NO_UPDATE)),
                When(stale, then=Value(ReasonCode.STALE_VERIFICATION)),
                default=Value(""),
                output_field=CharField(),
            ),
        )
        .order_by("queue_priority", "exam_stage", "track")
    )
