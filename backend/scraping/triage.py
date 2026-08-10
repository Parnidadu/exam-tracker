"""The triage queue: observations the matcher would not link on its own.

EXT-050 decides; this holds what it declined to decide, and gives an
operator the three ways out:

* **link** it to a stage that already exists - the matcher was unsure, a
  human is not;
* **create** the exam it refers to and link it - the usual answer to "no
  exam matched", because a new cycle has to be entered by someone;
* **dismiss** it - the notice is real but is not about a tracked exam.

Nothing here writes status data directly. Linking goes through
`apply_machine_observation`, exactly as an automatic match does, so an
operator resolving a queue item still cannot overwrite a fresh human
verification - it records a conflict, and says so.
"""

from __future__ import annotations

import hashlib
import logging

from django.db import transaction
from django.db.models import F, QuerySet
from django.utils import timezone

from exams.models import Board, Exam, ExamStage
from verification.observations import ObservationResult, apply_machine_observation

from .matching import Decision, MatchResult, TriageReason
from .models import Source, TriageItem
from .parsers import Observation

logger = logging.getLogger(__name__)

#: matching.TriageReason -> the stored choice. Kept explicit rather than
#: relying on the two enums happening to share member names, so renaming
#: one fails here instead of silently writing a blank reason.
_REASONS: dict[TriageReason, str] = {
    TriageReason.NO_CANDIDATES: TriageItem.Reason.NO_CANDIDATES,
    TriageReason.BELOW_THRESHOLD: TriageItem.Reason.BELOW_THRESHOLD,
    TriageReason.AMBIGUOUS: TriageItem.Reason.AMBIGUOUS,
    TriageReason.NO_STAGE: TriageItem.Reason.NO_STAGE,
}


class TriageError(Exception):
    """An operator action that cannot be carried out as asked."""


class AlreadyResolved(TriageError):
    """Two operators reached for the same item."""


def fingerprint(source: Source | None, observation: Observation) -> str:
    """Identity of an unmatched observation, for recurrence counting.

    Built from what the observation *claims*, not from the snippet it was
    read out of: a board that reformats its notice text is still saying
    the same unresolved thing, and a queue that treated that as new would
    hand the operator the same problem twice.
    """
    parts = [
        str(source.pk if source else ""),
        observation.exam_name.strip().lower(),
        observation.track,
        observation.value,
        observation.stage_hint.strip().lower(),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _candidate_payload(result: MatchResult) -> list[dict[str, object]]:
    return [
        {
            "exam_stage_id": candidate.exam_stage.pk,
            "label": str(candidate.exam_stage),
            "score": candidate.score,
            "components": candidate.components,
        }
        for candidate in result.candidates
    ]


@transaction.atomic
def queue_observation(
    observation: Observation, result: MatchResult, *, source: Source | None = None
) -> TriageItem:
    """Record an unmatched observation, or note that it recurred.

    Re-queueing something an operator already resolved deliberately does
    *not* reopen it. A dismissed notice reappears on every single poll,
    and reopening it each time would make "dismiss" mean "hide until
    tomorrow". Only `times_seen` and `last_seen_at` move.
    """
    if result.decision is not Decision.TRIAGE:
        raise TriageError("only observations the matcher declined belong in the queue")

    digest = fingerprint(source, observation)
    now = timezone.now()

    existing = TriageItem.objects.select_for_update().filter(fingerprint=digest).first()
    if existing is not None:
        TriageItem.objects.filter(pk=existing.pk).update(
            last_seen_at=now,
            times_seen=F("times_seen") + 1,
            # The match may well have improved since - an operator may have
            # added the exam - so the score and shortlist are refreshed
            # even though the resolution is not touched.
            match_confidence=result.confidence,
            candidates=_candidate_payload(result),
        )
        existing.refresh_from_db()
        return existing

    return TriageItem.objects.create(
        source=source,
        exam_name=observation.exam_name,
        track=observation.track,
        value=observation.value,
        stage_hint=observation.stage_hint,
        observed_date=observation.observed_date,
        parser_confidence=observation.confidence,
        source_url=observation.source_url,
        raw_text=observation.raw_text,
        reason=_REASONS[result.reason] if result.reason else TriageItem.Reason.NO_CANDIDATES,
        match_confidence=result.confidence,
        candidates=_candidate_payload(result),
        fingerprint=digest,
    )


def pending() -> QuerySet[TriageItem]:
    """The queue itself, worst first.

    Ordered by how often something has recurred rather than by when it
    arrived: a notice seen forty times is a board actively publishing
    something nobody has mapped, which matters more than whatever landed
    most recently.
    """
    return (
        TriageItem.objects.filter(status=TriageItem.Status.PENDING)
        .select_related("source", "source__board")
        .order_by("-times_seen", "-last_seen_at", "-id")
    )


def _claim(item: TriageItem) -> TriageItem:
    """Re-read under a lock and refuse if someone got there first."""
    locked = TriageItem.objects.select_for_update().get(pk=item.pk)
    if locked.status != TriageItem.Status.PENDING:
        raise AlreadyResolved(
            f"{locked} was already {locked.get_status_display().lower()} "
            f"by {locked.resolved_by or 'someone'}"
        )
    return locked


def _as_observation(item: TriageItem) -> Observation:
    return Observation(
        exam_name=item.exam_name,
        track=item.track,
        value=item.value,
        stage_hint=item.stage_hint,
        observed_date=item.observed_date,
        confidence=item.parser_confidence,
        source_url=item.source_url,
        raw_text=item.raw_text,
    )


# --- the three ways out ------------------------------------------------


@transaction.atomic
def link_to_stage(
    item: TriageItem, exam_stage: ExamStage, *, actor: str, note: str = ""
) -> tuple[TriageItem, ObservationResult]:
    """Attach the observation to a stage that already exists.

    Writes through the same guarded path an automatic match uses, so the
    operator's decision to link does not become a decision to overwrite a
    verification someone else made this week.
    """
    locked = _claim(item)

    applied = apply_machine_observation(
        exam_stage=exam_stage,
        track=locked.track,
        value=locked.value,
        # The parser's confidence in the reading. Linking says which stage
        # this is about; it says nothing new about how clearly the page
        # stated the value, so that number is passed through unchanged.
        confidence=locked.parser_confidence,
    )

    locked.status = TriageItem.Status.LINKED
    locked.exam_stage = exam_stage
    locked.resolved_by = actor
    locked.resolved_at = timezone.now()
    locked.resolution_note = note
    locked.save()

    if applied.conflict is not None:
        logger.info(
            "triage: %s linked to %s but the value was refused - fresh human verification",
            locked.pk,
            exam_stage,
        )
    return locked, applied


@transaction.atomic
def create_exam_and_link(
    item: TriageItem,
    *,
    board: Board,
    code: str,
    name: str,
    cycle_year: int,
    stage_type: str = ExamStage.StageType.SINGLE,
    actor: str,
    note: str = "",
) -> tuple[TriageItem, ExamStage, ObservationResult]:
    """Create the exam this observation refers to, then link to it.

    The usual answer to "no exam matched": a board has announced a cycle
    nobody has entered yet, and the observation is the first evidence of
    it. Creating the exam by hand and then finding the queue item again
    is the same work with more steps and more chances to mistype the
    year.

    One stage is created, not a full ladder. Which stages an exam has is a
    domain fact the operator knows and this code does not, and inventing
    prelims/mains/interview for a single-stage recruitment would put rows
    on the public timeline that never happen.
    """
    locked = _claim(item)

    exam, _ = Exam.objects.get_or_create(
        board=board,
        code=code,
        cycle_year=cycle_year,
        defaults={"name": name, "category": ""},
    )
    exam_stage, _ = ExamStage.objects.get_or_create(
        exam=exam,
        stage_type=stage_type,
        defaults={"sequence": (exam.stages.count() or 0) + 1},
    )

    applied = apply_machine_observation(
        exam_stage=exam_stage,
        track=locked.track,
        value=locked.value,
        confidence=locked.parser_confidence,
    )

    locked.status = TriageItem.Status.CREATED
    locked.exam_stage = exam_stage
    locked.resolved_by = actor
    locked.resolved_at = timezone.now()
    locked.resolution_note = note
    locked.save()
    return locked, exam_stage, applied


@transaction.atomic
def dismiss(item: TriageItem, *, actor: str, note: str = "") -> TriageItem:
    """Mark an item as not about a tracked exam. Writes no status data.

    A dismissal is permanent for that fingerprint - the same notice
    recurring only bumps its counter. Otherwise every poll would resurrect
    it and "dismiss" would mean "hide until tomorrow".
    """
    locked = _claim(item)
    locked.status = TriageItem.Status.DISMISSED
    locked.resolved_by = actor
    locked.resolved_at = timezone.now()
    locked.resolution_note = note
    locked.save()
    return locked


def suggested_stages(item: TriageItem) -> list[ExamStage]:
    """The shortlist the matcher had, for the operator to pick from.

    Read back from the stored candidates rather than re-scored, so what is
    offered is what the matcher actually considered. Stages deleted since
    are simply absent.
    """
    ids = [entry.get("exam_stage_id") for entry in item.candidates or []]
    if not ids:
        return []
    by_id = ExamStage.objects.in_bulk(ids)
    return [by_id[pk] for pk in ids if pk in by_id]
