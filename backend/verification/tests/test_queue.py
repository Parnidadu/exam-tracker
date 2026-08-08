from datetime import date, timedelta

import pytest
from django.utils import timezone

from exams.models import ExamStage, StatusTrack
from verification.queue import (
    CHANGED_PRIORITY,
    NO_UPDATE_PRIORITY,
    STALE_PRIORITY,
    verification_queue,
)


def _stage(exam, sequence, planned_start_date=None, stage_type=ExamStage.StageType.PRELIMS):
    return ExamStage.objects.create(
        exam=exam,
        stage_type=stage_type,
        sequence=sequence,
        planned_start_date=planned_start_date,
    )


@pytest.mark.django_db
def test_queue_includes_machine_changed_item(exam):
    stage = _stage(exam, 1)
    track = StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        machine_seen_at=timezone.now(),
        human_value="postponed",
        verified_at=timezone.now() - timedelta(days=5),
    )

    items = list(verification_queue())

    assert track in items
    assert next(i for i in items if i.pk == track.pk).queue_priority == CHANGED_PRIORITY


@pytest.mark.django_db
def test_queue_excludes_machine_value_that_merely_reconfirms_human_value(exam):
    stage = _stage(exam, 1)
    track = StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        machine_seen_at=timezone.now(),
        human_value="conducted",
        verified_at=timezone.now() - timedelta(days=1),
    )

    assert track not in list(verification_queue())


@pytest.mark.django_db
def test_queue_includes_date_elapsed_no_update_item_for_conduct_track(exam):
    stage = _stage(exam, 1, planned_start_date=date.today() - timedelta(days=3))
    track = StatusTrack.objects.create(exam_stage=stage, track=StatusTrack.Track.CONDUCT)

    items = list(verification_queue())

    assert track in items
    assert next(i for i in items if i.pk == track.pk).queue_priority == NO_UPDATE_PRIORITY


@pytest.mark.django_db
def test_queue_excludes_date_elapsed_no_update_for_non_conduct_track(exam):
    stage = _stage(exam, 1, planned_start_date=date.today() - timedelta(days=3))
    track = StatusTrack.objects.create(exam_stage=stage, track=StatusTrack.Track.RESULT)

    assert track not in list(verification_queue())


@pytest.mark.django_db
def test_queue_excludes_conduct_track_with_future_planned_date(exam):
    stage = _stage(exam, 1, planned_start_date=date.today() + timedelta(days=3))
    track = StatusTrack.objects.create(exam_stage=stage, track=StatusTrack.Track.CONDUCT)

    assert track not in list(verification_queue())


@pytest.mark.django_db
def test_queue_includes_stale_item(exam):
    stage = _stage(exam, 1)
    track = StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        human_value="conducted",
        verified_at=timezone.now() - StatusTrack.STALENESS_WINDOW - timedelta(days=1),
    )

    items = list(verification_queue())

    assert track in items
    assert next(i for i in items if i.pk == track.pk).queue_priority == STALE_PRIORITY


@pytest.mark.django_db
def test_queue_excludes_freshly_verified_unchanged_item(exam):
    stage = _stage(exam, 1)
    track = StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        human_value="conducted",
        verified_at=timezone.now() - timedelta(days=1),
    )

    assert track not in list(verification_queue())


@pytest.mark.django_db
def test_queue_item_matching_both_changed_and_stale_ranks_as_changed(exam):
    stage = _stage(exam, 1)
    track = StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.CONDUCT,
        machine_value="postponed",
        machine_seen_at=timezone.now(),
        human_value="conducted",
        verified_at=timezone.now() - StatusTrack.STALENESS_WINDOW - timedelta(days=1),
    )

    items = list(verification_queue())
    matched = next(i for i in items if i.pk == track.pk)

    assert matched.queue_priority == CHANGED_PRIORITY


@pytest.mark.django_db
def test_queue_orders_by_priority_regardless_of_insertion_order(exam):
    stale_stage = _stage(exam, 1)
    stale = StatusTrack.objects.create(
        exam_stage=stale_stage,
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        human_value="conducted",
        verified_at=timezone.now() - StatusTrack.STALENESS_WINDOW - timedelta(days=1),
    )

    no_update_stage = _stage(exam, 2, planned_start_date=date.today() - timedelta(days=1))
    no_update = StatusTrack.objects.create(
        exam_stage=no_update_stage, track=StatusTrack.Track.CONDUCT
    )

    changed_stage = _stage(exam, 3)
    changed = StatusTrack.objects.create(
        exam_stage=changed_stage,
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        machine_seen_at=timezone.now(),
        human_value="postponed",
        verified_at=timezone.now() - timedelta(days=1),
    )

    ordered_pks = [item.pk for item in verification_queue()]

    assert ordered_pks.index(changed.pk) < ordered_pks.index(no_update.pk)
    assert ordered_pks.index(no_update.pk) < ordered_pks.index(stale.pk)
