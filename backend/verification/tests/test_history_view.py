import pytest

from exams.models import ExamStage, StatusTrack
from verification.models import VerificationRecord


def _record(exam_stage, actor, value, track=StatusTrack.Track.CONDUCT):
    return VerificationRecord.objects.create(
        exam_stage=exam_stage,
        track=track,
        value=value,
        evidence_url="https://upsc.gov.in/notice/1",
        note="",
        actor=actor,
    )


def _url(exam):
    return f"/api/exams/{exam.slug}/verifications/"


@pytest.mark.django_db
def test_returns_records_for_the_exam_newest_first(client, exam, exam_stage, actor):
    _record(exam_stage, actor, "scheduled")
    _record(exam_stage, actor, "postponed")
    _record(exam_stage, actor, "conducted")

    results = client.get(_url(exam)).json()["results"]

    assert [item["value"] for item in results] == ["conducted", "postponed", "scheduled"]


@pytest.mark.django_db
def test_includes_the_fields_the_history_ui_needs(client, exam, exam_stage, actor):
    _record(exam_stage, actor, "conducted")

    item = client.get(_url(exam)).json()["results"][0]

    assert item["track"] == StatusTrack.Track.CONDUCT
    assert item["value"] == "conducted"
    assert item["evidence_url"] == "https://upsc.gov.in/notice/1"
    assert item["exam_stage_id"] == exam_stage.pk
    assert item["stage_type"] == exam_stage.stage_type
    assert item["timestamp"]


@pytest.mark.django_db
def test_excludes_records_from_other_exams(client, exam, exam_stage, actor, board):
    from exams.models import Exam

    other_exam = Exam.objects.create(
        board=board, code="CDS", name="CDS", cycle_year=2026, category="Defence"
    )
    other_stage = ExamStage.objects.create(
        exam=other_exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1
    )
    _record(exam_stage, actor, "mine")
    _record(other_stage, actor, "theirs")

    results = client.get(_url(exam)).json()["results"]

    assert [item["value"] for item in results] == ["mine"]


@pytest.mark.django_db
def test_actor_is_hidden_from_anonymous_visitors(client, exam, exam_stage, actor):
    _record(exam_stage, actor, "conducted")

    item = client.get(_url(exam)).json()["results"][0]

    assert item["actor"] is None
    # The rest of the record stays public - only the identity is withheld.
    assert item["value"] == "conducted"


@pytest.mark.django_db
def test_actor_is_visible_to_signed_in_users(client, exam, exam_stage, actor):
    _record(exam_stage, actor, "conducted")
    client.force_login(actor)

    item = client.get(_url(exam)).json()["results"][0]

    assert item["actor"] == actor.email


@pytest.mark.django_db
def test_response_is_paginated(client, exam, exam_stage, actor):
    _record(exam_stage, actor, "conducted")

    body = client.get(_url(exam)).json()

    assert set(body.keys()) == {"count", "next", "previous", "results"}


@pytest.mark.django_db
def test_unknown_exam_returns_an_empty_list(client):
    body = client.get("/api/exams/does-not-exist/verifications/").json()

    assert body["count"] == 0
    assert body["results"] == []
