"""The daily digest of items waiting for a verifier.

Reads the queue from `verification_queue()` rather than re-deriving "needs
attention" - those predicates are the definition the API and the console
already use, and a second copy in an email would drift from the screen a
verifier opens when they click through.

The rule that shapes everything here: **an empty queue sends nothing**. A
daily "0 items" email trains people to delete the message unread, and the
first day it does matter it gets deleted with the rest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMessage, get_connection

from accounts.models import Role
from exams.models import StatusTrack

from .queue import ReasonCode, verification_queue

logger = logging.getLogger(__name__)


def max_items() -> int:
    """How many items to name individually before summarising.

    A digest is a prompt to go and look, not the work itself; past a
    screenful it stops being read.
    """
    return int(getattr(settings, "VERIFICATION_DIGEST_MAX_ITEMS", 20))


def console_url() -> str:
    base = str(getattr(settings, "FRONTEND_BASE_URL", "")).rstrip("/")
    return f"{base}/verify" if base else ""


@dataclass(frozen=True)
class Digest:
    """What is waiting, ready to render."""

    total: int = 0
    #: reason code -> count, highest-priority reason first.
    counts: dict[str, int] = field(default_factory=dict)
    #: The individual rows named in the body, worst first.
    items: tuple[StatusTrack, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.total == 0


def build_digest() -> Digest:
    """Snapshot the queue.

    Ordering comes from the queue itself, so the email leads with whatever
    the console would put at the top - a verifier reading down the message
    and then opening the console sees the same thing in the same order.
    """
    queue = list(verification_queue())
    if not queue:
        return Digest()

    counts: dict[str, int] = {}
    for track in queue:
        reason = getattr(track, "reason_code", "") or ""
        counts[reason] = counts.get(reason, 0) + 1

    # Ordered by the queue's own priority, not by size: three leaked-status
    # contradictions matter more than forty stale verifications, and a
    # digest sorted by count would bury them.
    ordered = {
        reason.value: counts[reason.value] for reason in ReasonCode if reason.value in counts
    }

    return Digest(total=len(queue), counts=ordered, items=tuple(queue[: max_items()]))


def recipients() -> list[str]:
    """Active verifiers.

    Admins are not included by default. They can verify, but the digest is
    a daily work prompt for the people whose job the queue is, and an
    unsolicited daily email is the fastest way to make a system feel like
    spam. An admin who wants it can be given the verifier role.
    """
    User = get_user_model()
    return list(
        User.objects.filter(role=Role.VERIFIER, is_active=True)
        .exclude(email="")
        .order_by("email")
        .values_list("email", flat=True)
    )


def _reason_label(track: StatusTrack) -> str:
    reason = getattr(track, "reason_code", "") or ""
    return ReasonCode(reason).label if reason in ReasonCode.values else reason


def _describe(track: StatusTrack) -> str:
    """One queue item, named the way a reader outside the app needs it.

    Spelled out rather than leaning on ExamStage.__str__, which renders
    "UPSC CSE 2026 - Prelims". That is fine in admin, where the codes are
    familiar, but someone skimming this at 07:00 is looking for "Civil
    Services Examination".
    """
    exam = track.exam_stage.exam
    machine = track.machine_value or "-"
    human = track.human_value or "-"
    return (
        f"- {exam.board.code} {exam.name} {exam.cycle_year}"
        f" - {track.exam_stage.stage_type} [{track.track}]\n"
        f"    {_reason_label(track)}\n"
        f"    machine: {machine}   human: {human}"
    )


def render(digest: Digest) -> tuple[str, str]:
    """Subject and plain-text body.

    Plain text on purpose: it renders in every client, is readable in a
    terminal, and nothing here needs styling. The subject carries the
    count so the queue's size is visible without opening anything.
    """
    one = digest.total == 1
    subject = f"Exam Tracker: {digest.total} item{'' if one else 's'} to verify"

    lines = [
        f"{digest.total} item{'' if one else 's'} {'is' if one else 'are'} "
        "waiting for a verifier.",
        "",
    ]

    for reason, count in digest.counts.items():
        label = ReasonCode(reason).label if reason in ReasonCode.values else reason
        lines.append(f"  {count:>4}  {label}")
    lines.append("")

    lines.extend(_describe(track) for track in digest.items)

    hidden = digest.total - len(digest.items)
    if hidden > 0:
        lines += ["", f"...and {hidden} more."]

    if console_url():
        lines += ["", f"Work the queue: {console_url()}"]

    return subject, "\n".join(lines)


def send_digest() -> dict[str, object]:
    """Build and send. Returns what happened, for the task to report.

    Two ways to send nothing, kept distinct because they need different
    fixes: nothing to do, versus nobody to tell.
    """
    digest = build_digest()
    if digest.is_empty:
        logger.info("verification digest: queue is empty, sending nothing")
        return {"status": "skipped", "reason": "empty_queue", "total": 0}

    to = recipients()
    if not to:
        # Worth an error: a queue that is building up with nobody assigned
        # to look at it is a configuration problem, not a quiet day.
        logger.error(
            "verification digest: %s items waiting but no active verifier to send to",
            digest.total,
        )
        return {"status": "skipped", "reason": "no_recipients", "total": digest.total}

    subject, body = render(digest)
    connection = get_connection()
    # One message per recipient rather than one with everyone in To:
    # a verifier's colleagues' addresses are not theirs to collect, and a
    # shared header hands them over on every send.
    messages = [
        EmailMessage(subject=subject, body=body, to=[address], connection=connection)
        for address in to
    ]
    connection.send_messages(messages)

    logger.info("verification digest: %s items sent to %s verifiers", digest.total, len(to))
    return {"status": "sent", "total": digest.total, "recipients": len(to)}
