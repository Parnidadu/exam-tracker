import pytest

from exams.models import ExamStage, StatusTrack


@pytest.mark.django_db
def test_detail_returns_exam_with_nested_stages_and_status_tracks(client, exam):
    stage = ExamStage.objects.create(
        exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1
    )
    StatusTrack.objects.create(exam_stage=stage, track=StatusTrack.Track.CONDUCT)
    StatusTrack.objects.create(exam_stage=stage, track=StatusTrack.Track.RESULT)
    StatusTrack.objects.create(exam_stage=stage, track=StatusTrack.Track.INTEGRITY)

    response = client.get(f"/api/exams/{exam.slug}/")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "CSE"
    assert len(body["stages"]) == 1
    assert len(body["stages"][0]["status_tracks"]) == 3
    tracks = {t["track"] for t in body["stages"][0]["status_tracks"]}
    assert tracks == {"conduct", "result", "integrity"}


@pytest.mark.django_db
def test_detail_stages_are_ordered_by_sequence(client, exam):
    ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.INTERVIEW, sequence=3)
    ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.MAINS, sequence=2)
    ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1)

    response = client.get(f"/api/exams/{exam.slug}/")

    sequences = [s["sequence"] for s in response.json()["stages"]]
    assert sequences == [1, 2, 3]


@pytest.mark.django_db
def test_detail_404_for_unknown_slug(client):
    response = client.get("/api/exams/does-not-exist/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_detail_uses_a_constant_number_of_queries_regardless_of_stage_count(
    client, exam, django_assert_num_queries
):
    for sequence in range(1, 4):
        stage = ExamStage.objects.create(
            exam=exam, stage_type=ExamStage.StageType.SINGLE, sequence=sequence
        )
        for track in StatusTrack.Track.values:
            StatusTrack.objects.create(exam_stage=stage, track=track)

    # 1 query for the exam (board is select_related), 1 for its stages,
    # 1 for all of those stages' status tracks - not one query per stage
    # or per track.
    with django_assert_num_queries(3):
        response = client.get(f"/api/exams/{exam.slug}/")

    assert response.status_code == 200
    assert len(response.json()["stages"]) == 3
    assert all(len(s["status_tracks"]) == 3 for s in response.json()["stages"])


@pytest.mark.django_db
def test_detail_exposes_verification_freshness_for_the_status_badge(client, exam):
    """The UI badge reads freshness from the API rather than re-deriving
    STALENESS_WINDOW in JavaScript, where it could drift."""
    from datetime import timedelta

    from django.utils import timezone

    stage = ExamStage.objects.create(
        exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1
    )
    StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.CONDUCT,
        human_value="conducted",
        verified_at=timezone.now() - timedelta(days=1),
    )
    StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.RESULT,
        human_value="awaited",
        verified_at=timezone.now() - StatusTrack.STALENESS_WINDOW - timedelta(days=1),
    )

    tracks = client.get(f"/api/exams/{exam.slug}/").json()["stages"][0]["status_tracks"]
    freshness = {t["track"]: t["is_verification_fresh"] for t in tracks}

    assert freshness["conduct"] is True
    assert freshness["result"] is False
