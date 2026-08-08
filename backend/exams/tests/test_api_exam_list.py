from datetime import date, timedelta

import pytest
from django.utils import timezone

from exams.models import Board, Exam, ExamStage, StatusTrack

URL = "/api/exams/"


@pytest.fixture
def ssc(db):
    return Board.objects.create(
        name="Staff Selection Commission",
        code="SSC",
        official_url="https://ssc.nic.in",
        timezone="Asia/Kolkata",
    )


@pytest.fixture
def cgl(ssc):
    return Exam.objects.create(
        board=ssc,
        code="CGL",
        name="Combined Graduate Level Examination",
        cycle_year=2026,
        category="Graduate Level",
    )


@pytest.mark.django_db
def test_response_is_paginated(client, exam):
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"count", "next", "previous", "results"}
    assert body["count"] == 1


@pytest.mark.django_db
def test_filter_by_board(client, exam, cgl):
    response = client.get(URL, {"board": "UPSC"})
    codes = [item["code"] for item in response.json()["results"]]
    assert codes == ["CSE"]


@pytest.mark.django_db
def test_filter_by_date_range_matches_overlapping_stage(client, exam):
    ExamStage.objects.create(
        exam=exam,
        stage_type=ExamStage.StageType.PRELIMS,
        sequence=1,
        planned_start_date=date(2026, 6, 1),
        planned_end_date=date(2026, 6, 5),
    )

    response = client.get(URL, {"start_date": "2026-05-01", "end_date": "2026-06-02"})
    assert [item["code"] for item in response.json()["results"]] == ["CSE"]


@pytest.mark.django_db
def test_filter_by_date_range_excludes_non_overlapping_stage(client, exam):
    ExamStage.objects.create(
        exam=exam,
        stage_type=ExamStage.StageType.PRELIMS,
        sequence=1,
        planned_start_date=date(2026, 6, 1),
        planned_end_date=date(2026, 6, 5),
    )

    response = client.get(URL, {"start_date": "2026-07-01", "end_date": "2026-07-05"})
    assert response.json()["results"] == []


@pytest.mark.django_db
def test_filter_by_date_range_rejects_malformed_date(client, exam):
    response = client.get(URL, {"start_date": "not-a-date"})
    assert response.status_code == 400


@pytest.mark.django_db
def test_filter_by_status_uses_conduct_track_effective_status(client, exam):
    stage = ExamStage.objects.create(
        exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1
    )
    StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        human_value="postponed",
        verified_at=timezone.now() - timedelta(days=1),
    )

    fresh_human = client.get(URL, {"status": "postponed"})
    assert [item["code"] for item in fresh_human.json()["results"]] == ["CSE"]

    machine_fallback = client.get(URL, {"status": "conducted"})
    assert machine_fallback.json()["results"] == []


@pytest.mark.django_db
def test_filters_are_combinable(client, exam, cgl):
    ExamStage.objects.create(
        exam=exam,
        stage_type=ExamStage.StageType.PRELIMS,
        sequence=1,
        planned_start_date=date(2026, 6, 1),
        planned_end_date=date(2026, 6, 5),
    )
    ExamStage.objects.create(
        exam=cgl,
        stage_type=ExamStage.StageType.PRELIMS,
        sequence=1,
        planned_start_date=date(2026, 6, 1),
        planned_end_date=date(2026, 6, 5),
    )

    response = client.get(
        URL, {"board": "UPSC", "start_date": "2026-05-01", "end_date": "2026-06-10"}
    )
    assert [item["code"] for item in response.json()["results"]] == ["CSE"]


@pytest.mark.django_db
def test_schema_endpoint_documents_exam_list(client):
    response = client.get("/api/schema/")
    assert response.status_code == 200
    schema = response.content.decode()
    assert "/api/exams/" in schema
