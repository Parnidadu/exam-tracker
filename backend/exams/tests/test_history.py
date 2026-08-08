import json

import pytest
from django.utils import timezone

from accounts.models import Role, User
from exams.models import Board, Exam, ExamStage, StatusTrack


@pytest.fixture
def status_track(exam_stage):
    return StatusTrack.objects.create(
        exam_stage=exam_stage,
        track=StatusTrack.Track.CONDUCT,
        machine_value="scheduled",
        human_value="scheduled",
    )


@pytest.mark.parametrize(
    "model_name",
    ["Board", "Exam", "ExamStage", "StatusTrack"],
)
def test_every_mutable_domain_model_has_history(model_name):
    model = {"Board": Board, "Exam": Exam, "ExamStage": ExamStage, "StatusTrack": StatusTrack}[
        model_name
    ]
    assert hasattr(model, "history")


def test_user_has_history():
    assert hasattr(User, "history")


@pytest.mark.django_db
def test_user_history_excludes_password():
    user = User.objects.create_user(email="someone@example.com", password="pw")
    assert not hasattr(user.history.first(), "password")


@pytest.mark.django_db
def test_status_change_records_what_and_when(status_track):
    before = timezone.now()
    status_track.human_value = "postponed"
    status_track.save()

    latest = status_track.history.first()

    assert latest.human_value == "postponed"
    assert latest.history_type == "~"  # update
    assert latest.history_date >= before


@pytest.mark.django_db
def test_status_change_preserves_the_old_value(status_track):
    status_track.human_value = "postponed"
    status_track.save()

    latest, previous = status_track.history.all()[:2]

    assert latest.human_value == "postponed"
    assert previous.human_value == "scheduled"

    delta = latest.diff_against(previous)
    changed = {change.field: (change.old, change.new) for change in delta.changes}
    assert changed["human_value"] == ("scheduled", "postponed")


@pytest.mark.django_db
def test_status_change_records_who_when_made_over_http(client, exam_stage):
    verifier = User.objects.create_user(
        email="who@example.com", password="pw", role=Role.VERIFIER
    )
    client.force_login(verifier)

    response = client.post(
        f"/api/stages/{exam_stage.pk}/verify/",
        data=json.dumps({"track": "conduct", "value": "conducted"}),
        content_type="application/json",
    )
    assert response.status_code == 201

    status_track = StatusTrack.objects.get(exam_stage=exam_stage, track="conduct")
    assert status_track.history.first().history_user == verifier


@pytest.mark.django_db
def test_history_accumulates_one_row_per_change(status_track):
    for value in ["postponed", "conducted", "cancelled"]:
        status_track.human_value = value
        status_track.save()

    # 1 create + 3 updates
    assert status_track.history.count() == 4
    assert [h.human_value for h in status_track.history.all()] == [
        "cancelled",
        "conducted",
        "postponed",
        "scheduled",
    ]
