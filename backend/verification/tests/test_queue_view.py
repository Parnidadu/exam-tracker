from datetime import date, timedelta

import pytest
from django.utils import timezone

from accounts.models import Role, User
from exams.models import ExamStage, StatusTrack
from verification.queue import ReasonCode

URL = "/api/verification-queue/"


def _stage(exam, sequence, planned_start_date=None):
    return ExamStage.objects.create(
        exam=exam,
        stage_type=ExamStage.StageType.PRELIMS,
        sequence=sequence,
        planned_start_date=planned_start_date,
    )


@pytest.fixture
def changed_track(exam):
    return StatusTrack.objects.create(
        exam_stage=_stage(exam, 1),
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        machine_seen_at=timezone.now(),
        human_value="postponed",
        verified_at=timezone.now() - timedelta(days=1),
    )


@pytest.fixture
def no_update_track(exam):
    return StatusTrack.objects.create(
        exam_stage=_stage(exam, 2, planned_start_date=date.today() - timedelta(days=3)),
        track=StatusTrack.Track.CONDUCT,
    )


@pytest.fixture
def stale_track(exam):
    return StatusTrack.objects.create(
        exam_stage=_stage(exam, 3),
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        human_value="conducted",
        verified_at=timezone.now() - StatusTrack.STALENESS_WINDOW - timedelta(days=1),
    )


@pytest.mark.django_db
def test_every_item_carries_a_reason_code(client, changed_track, no_update_track, stale_track):
    response = client.get(URL)

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 3
    assert all(item["reason_code"] for item in results)
    assert {item["reason_code"] for item in results} == {
        ReasonCode.MACHINE_CHANGED,
        ReasonCode.DATE_ELAPSED_NO_UPDATE,
        ReasonCode.STALE_VERIFICATION,
    }


@pytest.mark.django_db
def test_reason_code_matches_the_condition_that_queued_each_item(
    client, changed_track, no_update_track, stale_track
):
    results = client.get(URL).json()["results"]
    by_id = {item["id"]: item["reason_code"] for item in results}

    assert by_id[changed_track.pk] == ReasonCode.MACHINE_CHANGED
    assert by_id[no_update_track.pk] == ReasonCode.DATE_ELAPSED_NO_UPDATE
    assert by_id[stale_track.pk] == ReasonCode.STALE_VERIFICATION


@pytest.mark.django_db
def test_results_are_ordered_by_priority(client, changed_track, no_update_track, stale_track):
    results = client.get(URL).json()["results"]

    assert [item["reason_code"] for item in results] == [
        ReasonCode.MACHINE_CHANGED,
        ReasonCode.DATE_ELAPSED_NO_UPDATE,
        ReasonCode.STALE_VERIFICATION,
    ]


@pytest.mark.django_db
def test_multi_reason_item_reports_its_highest_priority_reason(client, exam):
    # Both stale AND contradicted by a newer machine observation.
    track = StatusTrack.objects.create(
        exam_stage=_stage(exam, 1),
        track=StatusTrack.Track.CONDUCT,
        machine_value="postponed",
        machine_seen_at=timezone.now(),
        human_value="conducted",
        verified_at=timezone.now() - StatusTrack.STALENESS_WINDOW - timedelta(days=1),
    )

    results = client.get(URL).json()["results"]

    assert len(results) == 1
    assert results[0]["id"] == track.pk
    assert results[0]["reason_code"] == ReasonCode.MACHINE_CHANGED


@pytest.mark.django_db
def test_filter_by_reason_code(client, changed_track, no_update_track, stale_track):
    results = client.get(URL, {"reason_code": ReasonCode.STALE_VERIFICATION}).json()["results"]

    assert [item["id"] for item in results] == [stale_track.pk]


@pytest.mark.django_db
def test_items_not_needing_review_are_absent(client, exam):
    StatusTrack.objects.create(
        exam_stage=_stage(exam, 1),
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        human_value="conducted",
        verified_at=timezone.now() - timedelta(days=1),
    )

    assert client.get(URL).json()["results"] == []


@pytest.mark.django_db
def test_item_includes_context_needed_to_action_it(client, changed_track):
    item = client.get(URL).json()["results"][0]

    assert item["exam_stage_id"] == changed_track.exam_stage.pk
    assert item["exam_slug"] == changed_track.exam_stage.exam.slug
    assert item["track"] == StatusTrack.Track.CONDUCT
    assert item["machine_value"] == "conducted"
    assert item["human_value"] == "postponed"


@pytest.mark.django_db
def test_response_is_paginated(client, changed_track):
    body = client.get(URL).json()

    assert set(body.keys()) == {"count", "next", "previous", "results"}


@pytest.mark.django_db
def test_queue_is_readable_by_a_viewer(client, changed_track):
    viewer = User.objects.create_user(
        email="viewer@example.com", password="pw", role=Role.VIEWER
    )
    client.force_login(viewer)

    assert client.get(URL).status_code == 200
