import json
from unittest.mock import patch

import pytest

from accounts.models import Role, User
from exams.models import StatusTrack
from verification.models import VerificationRecord


def _url(exam_stage):
    return f"/api/stages/{exam_stage.pk}/verify/"


def _post(client, exam_stage, **overrides):
    payload = {"track": "conduct", "value": "conducted", "evidence_url": "", "note": ""}
    payload.update(overrides)
    return client.post(_url(exam_stage), data=json.dumps(payload), content_type="application/json")


@pytest.fixture
def viewer(db):
    return User.objects.create_user(email="viewer@example.com", password="pw", role=Role.VIEWER)


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(email="admin@example.com", password="pw", role=Role.ADMIN)


@pytest.mark.django_db
def test_verify_writes_record_and_updates_status_track(client, exam_stage, actor):
    client.force_login(actor)

    response = _post(
        client,
        exam_stage,
        track="conduct",
        value="conducted",
        evidence_url="https://upsc.gov.in/notice/1",
        note="Confirmed via notice",
    )

    assert response.status_code == 201

    record = VerificationRecord.objects.get()
    assert record.exam_stage == exam_stage
    assert record.track == "conduct"
    assert record.value == "conducted"
    assert record.evidence_url == "https://upsc.gov.in/notice/1"
    assert record.note == "Confirmed via notice"
    assert record.actor == actor

    status_track = StatusTrack.objects.get(exam_stage=exam_stage, track="conduct")
    assert status_track.human_value == "conducted"
    assert status_track.verified_by == actor.email
    assert status_track.verified_at == record.timestamp


@pytest.mark.django_db
def test_verify_creates_status_track_when_none_exists(client, exam_stage, actor):
    client.force_login(actor)
    assert not StatusTrack.objects.filter(exam_stage=exam_stage, track="result").exists()

    response = _post(client, exam_stage, track="result", value="declared")

    assert response.status_code == 201
    assert StatusTrack.objects.filter(
        exam_stage=exam_stage, track="result", human_value="declared"
    ).exists()


@pytest.mark.django_db
def test_verify_updates_existing_status_track_and_appends_new_record(client, exam_stage, actor):
    client.force_login(actor)
    _post(client, exam_stage, track="conduct", value="conducted")
    _post(client, exam_stage, track="conduct", value="postponed")

    assert VerificationRecord.objects.filter(exam_stage=exam_stage, track="conduct").count() == 2
    status_track = StatusTrack.objects.get(exam_stage=exam_stage, track="conduct")
    assert status_track.human_value == "postponed"


@pytest.mark.django_db
def test_verify_is_atomic_rolls_back_record_if_status_track_save_fails(client, exam_stage, actor):
    client.force_login(actor)

    with patch.object(StatusTrack, "save", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            _post(client, exam_stage, track="conduct", value="conducted")

    assert VerificationRecord.objects.count() == 0
    assert not StatusTrack.objects.filter(exam_stage=exam_stage, track="conduct").exists()


@pytest.mark.django_db
def test_verify_returns_403_for_viewer(client, exam_stage, viewer):
    client.force_login(viewer)
    response = _post(client, exam_stage)
    assert response.status_code == 403
    assert VerificationRecord.objects.count() == 0


@pytest.mark.django_db
def test_verify_returns_403_for_anonymous(client, exam_stage):
    # Not 401: DRF downgrades NotAuthenticated to 403 whenever the first
    # configured authenticator (SessionAuthentication here) can't issue a
    # WWW-Authenticate challenge header. Confirmed via the response body
    # ("Authentication credentials were not provided.") rather than assumed.
    response = _post(client, exam_stage)
    assert response.status_code == 403
    assert VerificationRecord.objects.count() == 0


@pytest.mark.django_db
def test_verify_allowed_for_admin(client, exam_stage, admin_user):
    client.force_login(admin_user)
    response = _post(client, exam_stage)
    assert response.status_code == 201


@pytest.mark.django_db
def test_verify_rejects_invalid_track(client, exam_stage, actor):
    client.force_login(actor)
    response = _post(client, exam_stage, track="not-a-track")
    assert response.status_code == 400
    assert VerificationRecord.objects.count() == 0


@pytest.mark.django_db
def test_verify_404_for_unknown_stage(client, actor):
    client.force_login(actor)
    response = client.post(
        "/api/stages/999999/verify/",
        data=json.dumps({"track": "conduct", "value": "conducted"}),
        content_type="application/json",
    )
    assert response.status_code == 404
