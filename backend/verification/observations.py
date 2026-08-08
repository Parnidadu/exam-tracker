from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from exams.models import ExamStage, StatusTrack

from .models import MachineObservationConflict


@dataclass(frozen=True)
class ObservationResult:
    """Outcome of offering a machine observation to a status track."""

    status_track: StatusTrack
    conflict: MachineObservationConflict | None

    @property
    def written(self) -> bool:
        return self.conflict is None


@transaction.atomic
def apply_machine_observation(
    exam_stage: ExamStage,
    track: str,
    value: str,
    confidence: float | None = None,
    seen_at: datetime | None = None,
) -> ObservationResult:
    """Record what a scraper observed, without ever overwriting a fresh
    human verification.

    This is the only path scrapers (S4) should use to write machine data.
    When the observation contradicts a human value that is still fresh it
    is refused and a MachineObservationConflict is recorded instead; the
    status track is left exactly as it was.

    Re-confirming a fresh human value is not a contradiction and writes
    normally - otherwise a scraper agreeing with a verifier would be
    treated as a conflict.
    """
    seen_at = seen_at or timezone.now()

    status_track, _ = StatusTrack.objects.select_for_update().get_or_create(
        exam_stage=exam_stage, track=track
    )

    # `verified_at is not None` is implied by is_verification_fresh, but
    # stating it here narrows the type for the conflict record below, whose
    # verified_at is non-nullable. The staleness policy itself still lives
    # in exactly one place - the property.
    verified_at = status_track.verified_at
    if (
        verified_at is not None
        and status_track.is_verification_fresh
        and value != status_track.human_value
    ):
        conflict = MachineObservationConflict.objects.create(
            exam_stage=exam_stage,
            track=track,
            machine_value=value,
            machine_confidence=confidence,
            machine_seen_at=seen_at,
            human_value=status_track.human_value,
            verified_by=status_track.verified_by,
            verified_at=verified_at,
        )
        return ObservationResult(status_track=status_track, conflict=conflict)

    status_track.machine_value = value
    status_track.machine_confidence = confidence
    status_track.machine_seen_at = seen_at
    status_track.save()
    return ObservationResult(status_track=status_track, conflict=None)
