"""Alerting when a stage's planned date passes and nothing was recorded.

This is the quiet failure the app exists to catch. A board that postpones
an exam without saying so produces no scrape failure, no conflict and no
error - the date simply arrives, passes, and the record still says what it
said last month. Nothing else in the system notices, because nothing went
wrong; something merely failed to happen.

Why this does not reuse `verification_queue()`
----------------------------------------------
The queue's `date_elapsed_no_update_q` looks the same, but it runs over
StatusTrack rows - so a stage that has *no* status track at all cannot
match it, and that is the most complete "no update" there is. Verified
against a live database before writing this, not assumed.

So the condition here is asked of the ExamStage itself: its planned date
has passed, and no conduct track carries either a machine observation or
a human verification.
"""

from __future__ import annotations

import logging
from datetime import date

from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from exams.models import ExamStage, StatusTrack

from .digest import console_url, recipients
from .models import ElapsedDateAlert

logger = logging.getLogger(__name__)


def stages_with_elapsed_dates(today: date | None = None) -> QuerySet[ExamStage]:
    """Stages whose planned date has passed with nothing recorded, and
    which have not already been alerted about.

    Already-alerted stages are excluded here rather than at send time, so
    "what would alert now" and "what will be sent" cannot disagree.
    """
    today = today or timezone.now().date()

    # Exists() rather than exclude() across a multi-valued relation: the
    # semantics of excluding on two conditions over the same reverse FK
    # are subtle enough to be worth not relying on.
    has_update = StatusTrack.objects.filter(
        exam_stage=OuterRef("pk"), track=StatusTrack.Track.CONDUCT
    ).filter(Q(machine_seen_at__isnull=False) | Q(verified_at__isnull=False))

    already_alerted = ElapsedDateAlert.objects.filter(exam_stage=OuterRef("pk"))

    return (
        ExamStage.objects.filter(planned_start_date__lt=today)
        .annotate(_has_update=Exists(has_update), _alerted=Exists(already_alerted))
        .filter(_has_update=False, _alerted=False)
        .select_related("exam", "exam__board")
        .order_by("planned_start_date", "exam__code", "sequence")
    )


def _describe(stage: ExamStage) -> str:
    exam = stage.exam
    return (
        f"- {exam.board.code} {exam.name} {exam.cycle_year} - {stage.stage_type}\n"
        f"    planned for {stage.planned_start_date}, and nothing has been recorded since"
    )


def render(stages: list[ExamStage]) -> tuple[str, str]:
    count = len(stages)
    one = count == 1
    subject = (
        f"Exam Tracker: {count} exam date{'' if one else 's'} passed with no update"
    )

    lines = [
        f"{count} stage{'' if one else 's'} {'was' if one else 'were'} due to start, and "
        "nothing - machine or human - has been recorded for "
        f"{'it' if one else 'them'}.",
        "",
        "This usually means a board moved a date without announcing it, or",
        "announced it somewhere the scraper does not read.",
        "",
    ]
    lines.extend(_describe(stage) for stage in stages)
    if console_url():
        lines += ["", f"Verify: {console_url()}"]
    return subject, "\n".join(lines)


@transaction.atomic
def send_elapsed_date_alerts(today: date | None = None) -> dict[str, object]:
    """Alert on every stage whose date has passed unremarked.

    One email for the run, one ElapsedDateAlert row per stage, and one log
    record per stage. The row is what makes it fire once; the per-stage
    log is what makes each one a distinct event in Sentry, where a single
    batched message would be one alert however many exams it named.

    Sending happens before the rows are written, and the whole thing is
    one transaction. If the write fails after the mail has gone out,
    everything rolls back and the next run alerts again - a duplicate
    email. The other order risks marking a stage alerted and then failing
    to send, which loses the alert entirely, and a missed overdue exam is
    the failure this exists to prevent.
    """
    stages = list(stages_with_elapsed_dates(today))
    if not stages:
        logger.info("elapsed-date alerts: nothing overdue")
        return {"status": "skipped", "reason": "nothing_overdue", "stages": 0}

    to = recipients()
    subject, body = render(stages)

    if to:
        # Imported here rather than at module scope so tests that stub the
        # mail backend see the same object Django hands the rest of the app.
        from django.core.mail import EmailMessage, get_connection

        connection = get_connection()
        connection.send_messages(
            [
                EmailMessage(subject=subject, body=body, to=[address], connection=connection)
                for address in to
            ]
        )
    else:
        logger.error(
            "elapsed-date alerts: %s stage(s) overdue but no active verifier to tell",
            len(stages),
        )

    records = []
    for stage in stages:
        planned = stage.planned_start_date
        if planned is None:
            # Unreachable through stages_with_elapsed_dates(), which
            # filters on planned_start_date__lt - a NULL never matches a
            # comparison. Stated rather than assumed so the non-null
            # column below is guaranteed by this function, not by a filter
            # somewhere else that a later edit might relax.
            continue

        # ERROR per stage: this is the signal an on-call operator should
        # see as one event per exam, not one event per morning.
        logger.error(
            "planned date %s passed with no update: %s",
            planned,
            stage,
            extra={"exam_stage_id": stage.pk, "exam_slug": stage.exam.slug},
        )
        records.append(
            ElapsedDateAlert(exam_stage=stage, planned_date=planned, notified=len(to))
        )

    ElapsedDateAlert.objects.bulk_create(records)

    return {
        "status": "sent" if to else "logged",
        "stages": len(stages),
        "recipients": len(to),
    }
