from datetime import timedelta

from django.utils import timezone

from exams.models import StatusTrack


def _track(exam_stage, **overrides):
    fields = {
        "exam_stage": exam_stage,
        "track": StatusTrack.Track.CONDUCT,
        "machine_value": "conducted",
        "human_value": "postponed",
    }
    fields.update(overrides)
    return StatusTrack.objects.create(**fields)


def test_effective_status_returns_human_value_when_verification_is_fresh(exam_stage):
    track = _track(exam_stage, verified_at=timezone.now() - timedelta(days=13))
    assert track.effective_status == "postponed"


def test_effective_status_returns_human_value_just_inside_the_staleness_window(exam_stage):
    track = _track(
        exam_stage, verified_at=timezone.now() - timedelta(days=14) + timedelta(minutes=1)
    )
    assert track.effective_status == "postponed"


def test_effective_status_returns_machine_value_when_verification_is_stale(exam_stage):
    track = _track(exam_stage, verified_at=timezone.now() - timedelta(days=15))
    assert track.effective_status == "conducted"


def test_effective_status_returns_machine_value_just_outside_the_staleness_window(exam_stage):
    track = _track(
        exam_stage, verified_at=timezone.now() - timedelta(days=14) - timedelta(minutes=1)
    )
    assert track.effective_status == "conducted"


def test_effective_status_returns_machine_value_when_never_verified(exam_stage):
    track = _track(exam_stage, verified_at=None)
    assert track.effective_status == "conducted"
