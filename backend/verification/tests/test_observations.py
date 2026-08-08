from datetime import timedelta

import pytest
from django.utils import timezone

from exams.models import MachineOverwriteBlocked, StatusTrack
from verification.models import MachineObservationConflict
from verification.observations import apply_machine_observation


def _verified_track(exam_stage, human_value="postponed", age=timedelta(days=1)):
    """A track carrying a human verification of the given age."""
    return StatusTrack.objects.create(
        exam_stage=exam_stage,
        track=StatusTrack.Track.CONDUCT,
        human_value=human_value,
        verified_by="verifier@example.com",
        verified_at=timezone.now() - age,
    )


@pytest.mark.django_db
def test_machine_does_not_overwrite_a_fresh_human_value(exam_stage):
    track = _verified_track(exam_stage, human_value="postponed")

    result = apply_machine_observation(
        exam_stage, StatusTrack.Track.CONDUCT, "conducted", confidence=0.9
    )

    track.refresh_from_db()
    assert result.written is False
    assert track.machine_value == ""
    assert track.human_value == "postponed"
    assert track.effective_status == "postponed"


@pytest.mark.django_db
def test_a_conflict_record_is_raised_instead(exam_stage):
    _verified_track(exam_stage, human_value="postponed")
    seen_at = timezone.now()

    result = apply_machine_observation(
        exam_stage, StatusTrack.Track.CONDUCT, "conducted", confidence=0.9, seen_at=seen_at
    )

    conflict = MachineObservationConflict.objects.get()
    assert result.conflict == conflict
    # The rejected observation is preserved in full...
    assert conflict.machine_value == "conducted"
    assert conflict.machine_confidence == pytest.approx(0.9)
    assert conflict.machine_seen_at == seen_at
    # ...alongside the human value it contradicted.
    assert conflict.human_value == "postponed"
    assert conflict.verified_by == "verifier@example.com"
    assert conflict.verified_at is not None


@pytest.mark.django_db
def test_machine_writes_normally_when_the_verification_is_stale(exam_stage):
    track = _verified_track(
        exam_stage, human_value="postponed", age=StatusTrack.STALENESS_WINDOW + timedelta(days=1)
    )

    result = apply_machine_observation(exam_stage, StatusTrack.Track.CONDUCT, "conducted")

    track.refresh_from_db()
    assert result.written is True
    assert track.machine_value == "conducted"
    assert MachineObservationConflict.objects.count() == 0


@pytest.mark.django_db
def test_machine_writes_normally_when_there_is_no_human_value(exam_stage):
    result = apply_machine_observation(exam_stage, StatusTrack.Track.CONDUCT, "conducted")

    assert result.written is True
    assert result.status_track.machine_value == "conducted"
    assert MachineObservationConflict.objects.count() == 0


@pytest.mark.django_db
def test_machine_reconfirming_a_fresh_human_value_is_not_a_conflict(exam_stage):
    track = _verified_track(exam_stage, human_value="conducted")

    result = apply_machine_observation(exam_stage, StatusTrack.Track.CONDUCT, "conducted")

    track.refresh_from_db()
    assert result.written is True
    assert track.machine_value == "conducted"
    assert MachineObservationConflict.objects.count() == 0


@pytest.mark.django_db
def test_conflict_does_not_disturb_the_existing_verification(exam_stage):
    track = _verified_track(exam_stage, human_value="postponed")
    before = (track.human_value, track.verified_by, track.verified_at)

    apply_machine_observation(exam_stage, StatusTrack.Track.CONDUCT, "conducted")

    track.refresh_from_db()
    assert (track.human_value, track.verified_by, track.verified_at) == before


@pytest.mark.django_db
def test_repeated_contradictions_each_raise_their_own_conflict(exam_stage):
    _verified_track(exam_stage, human_value="postponed")

    apply_machine_observation(exam_stage, StatusTrack.Track.CONDUCT, "conducted")
    apply_machine_observation(exam_stage, StatusTrack.Track.CONDUCT, "cancelled")

    assert MachineObservationConflict.objects.count() == 2


@pytest.mark.django_db
def test_direct_model_save_cannot_bypass_the_guard(exam_stage):
    """The helper is the intended path, but "never overwrites" must hold
    even for code that writes StatusTrack directly."""
    track = _verified_track(exam_stage, human_value="postponed")

    track.machine_value = "conducted"
    track.machine_seen_at = timezone.now()

    with pytest.raises(MachineOverwriteBlocked):
        track.save()

    track.refresh_from_db()
    assert track.machine_value == ""


@pytest.mark.django_db
def test_guard_allows_a_human_verification_to_be_recorded(exam_stage):
    """The guard must not block the verify endpoint, which updates the
    human side while leaving machine_* untouched."""
    track = _verified_track(exam_stage, human_value="postponed")

    track.human_value = "cancelled"
    track.verified_at = timezone.now()
    track.save()

    track.refresh_from_db()
    assert track.human_value == "cancelled"


@pytest.mark.django_db
def test_guard_allows_machine_writes_on_a_stale_track(exam_stage):
    track = _verified_track(
        exam_stage, human_value="postponed", age=StatusTrack.STALENESS_WINDOW + timedelta(days=1)
    )

    track.machine_value = "conducted"
    track.save()

    track.refresh_from_db()
    assert track.machine_value == "conducted"
