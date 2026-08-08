from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from exams.models import StatusTrack


def test_status_track_valid_tracks_are_accepted(exam_stage):
    for track, _ in StatusTrack.Track.choices:
        status_track = StatusTrack(exam_stage=exam_stage, track=track)
        status_track.full_clean()


def test_status_track_rejects_invalid_track(exam_stage):
    status_track = StatusTrack(exam_stage=exam_stage, track="rescheduled")
    with pytest.raises(ValidationError):
        status_track.full_clean()


def test_status_track_stores_machine_and_human_fields(exam_stage):
    machine_seen_at = datetime(2026, 1, 5, tzinfo=dt_timezone.utc)
    verified_at = datetime(2026, 1, 6, tzinfo=dt_timezone.utc)

    status_track = StatusTrack.objects.create(
        exam_stage=exam_stage,
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        machine_confidence=0.92,
        machine_seen_at=machine_seen_at,
        human_value="conducted",
        verified_by="verifier@example.com",
        verified_at=verified_at,
    )
    status_track.refresh_from_db()

    assert status_track.machine_value == "conducted"
    assert status_track.machine_confidence == pytest.approx(0.92)
    assert status_track.machine_seen_at == machine_seen_at
    assert status_track.human_value == "conducted"
    assert status_track.verified_by == "verifier@example.com"
    assert status_track.verified_at == verified_at


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_status_track_rejects_confidence_outside_zero_to_one(exam_stage, confidence):
    status_track = StatusTrack(
        exam_stage=exam_stage,
        track=StatusTrack.Track.CONDUCT,
        machine_confidence=confidence,
    )
    with pytest.raises(ValidationError):
        status_track.full_clean()


def test_status_track_unique_per_exam_stage_and_track(exam_stage):
    StatusTrack.objects.create(exam_stage=exam_stage, track=StatusTrack.Track.CONDUCT)
    with pytest.raises(IntegrityError):
        StatusTrack.objects.create(exam_stage=exam_stage, track=StatusTrack.Track.CONDUCT)


def test_status_track_str(exam_stage):
    status_track = StatusTrack.objects.create(
        exam_stage=exam_stage, track=StatusTrack.Track.RESULT
    )
    assert str(status_track) == f"{exam_stage} - Result"
