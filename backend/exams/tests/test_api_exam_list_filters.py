"""Filters added in EXT-030: name search, per-track status, and the
interaction between conduct_status and result_status."""

from datetime import timedelta

import pytest
from django.utils import timezone

from exams.models import Exam, ExamStage, StatusTrack

URL = "/api/exams/"


def _stage_with_tracks(exam, sequence, conduct=None, result=None):
    stage = ExamStage.objects.create(
        exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=sequence
    )
    fresh = timezone.now() - timedelta(days=1)
    if conduct is not None:
        StatusTrack.objects.create(
            exam_stage=stage,
            track=StatusTrack.Track.CONDUCT,
            human_value=conduct,
            verified_at=fresh,
        )
    if result is not None:
        StatusTrack.objects.create(
            exam_stage=stage,
            track=StatusTrack.Track.RESULT,
            human_value=result,
            verified_at=fresh,
        )
    return stage


def _codes(response):
    return sorted(item["code"] for item in response.json()["results"])


@pytest.mark.django_db
def test_search_matches_exam_name_case_insensitively(client, board):
    Exam.objects.create(
        board=board, code="CSE", name="Civil Services Examination", cycle_year=2026, category="c"
    )
    Exam.objects.create(
        board=board, code="CDS", name="Combined Defence Services", cycle_year=2026, category="d"
    )

    assert _codes(client.get(URL, {"search": "civil"})) == ["CSE"]
    assert _codes(client.get(URL, {"search": "SERVICES"})) == ["CDS", "CSE"]


@pytest.mark.django_db
def test_search_returns_nothing_when_no_name_matches(client, exam):
    assert client.get(URL, {"search": "zzz-nothing"}).json()["results"] == []


@pytest.mark.django_db
def test_filter_by_conduct_status(client, exam, board):
    _stage_with_tracks(exam, 1, conduct="conducted")
    other = Exam.objects.create(
        board=board, code="CDS", name="CDS", cycle_year=2026, category="d"
    )
    _stage_with_tracks(other, 1, conduct="postponed")

    assert _codes(client.get(URL, {"conduct_status": "conducted"})) == ["CSE"]


@pytest.mark.django_db
def test_filter_by_result_status(client, exam, board):
    _stage_with_tracks(exam, 1, result="declared")
    other = Exam.objects.create(
        board=board, code="CDS", name="CDS", cycle_year=2026, category="d"
    )
    _stage_with_tracks(other, 1, result="awaited")

    assert _codes(client.get(URL, {"result_status": "awaited"})) == ["CDS"]


@pytest.mark.django_db
def test_conduct_and_result_filters_may_be_satisfied_by_different_stages(client, exam):
    """An exam whose prelims were conducted and whose mains results are
    awaited matches both filters together."""
    _stage_with_tracks(exam, 1, conduct="conducted", result="declared")
    _stage_with_tracks(exam, 2, conduct="postponed", result="awaited")

    response = client.get(URL, {"conduct_status": "conducted", "result_status": "awaited"})

    assert _codes(response) == ["CSE"]


@pytest.mark.django_db
def test_combined_status_filters_still_exclude_non_matching_exams(client, exam, board):
    _stage_with_tracks(exam, 1, conduct="conducted", result="declared")
    other = Exam.objects.create(
        board=board, code="CDS", name="CDS", cycle_year=2026, category="d"
    )
    _stage_with_tracks(other, 1, conduct="postponed", result="awaited")

    response = client.get(URL, {"conduct_status": "conducted", "result_status": "awaited"})

    assert response.json()["results"] == []


@pytest.mark.django_db
def test_status_alias_still_filters_the_conduct_track(client, exam, board):
    """EXT-017 documented `status`; it must keep working."""
    _stage_with_tracks(exam, 1, conduct="conducted")
    other = Exam.objects.create(
        board=board, code="CDS", name="CDS", cycle_year=2026, category="d"
    )
    _stage_with_tracks(other, 1, conduct="postponed")

    assert _codes(client.get(URL, {"status": "conducted"})) == ["CSE"]


@pytest.mark.django_db
def test_status_filter_respects_the_staleness_resolver(client, exam):
    """A stale human value must not win - the machine value is effective."""
    stage = ExamStage.objects.create(
        exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1
    )
    StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        human_value="postponed",
        verified_at=timezone.now() - StatusTrack.STALENESS_WINDOW - timedelta(days=1),
    )

    assert _codes(client.get(URL, {"conduct_status": "conducted"})) == ["CSE"]
    assert client.get(URL, {"conduct_status": "postponed"}).json()["results"] == []


@pytest.mark.django_db
def test_search_combines_with_board_and_status(client, exam, board):
    _stage_with_tracks(exam, 1, conduct="conducted")
    other = Exam.objects.create(
        board=board,
        code="CDS",
        name="Civil Defence Services",
        cycle_year=2026,
        category="d",
    )
    _stage_with_tracks(other, 1, conduct="postponed")

    response = client.get(
        URL, {"search": "civil", "board": "UPSC", "conduct_status": "conducted"}
    )

    assert _codes(response) == ["CSE"]
