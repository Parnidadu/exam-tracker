from datetime import date

import pytest
from django.utils import timezone

from exams.models import Exam, ExamStage

URL = "/api/calendar/"


def _stage(exam, sequence=1, **dates):
    return ExamStage.objects.create(
        exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=sequence, **dates
    )


@pytest.mark.django_db
def test_returns_one_entry_per_milestone_in_the_month(client, exam):
    _stage(
        exam,
        notification_date=date(2026, 6, 1),
        admit_card_date=date(2026, 6, 20),
        exam_date=date(2026, 6, 25),
    )

    entries = client.get(URL, {"month": "2026-06"}).json()

    assert [e["milestone"] for e in entries] == [
        "notification",
        "admit_card",
        "exam",
    ]
    assert [e["date"] for e in entries] == ["2026-06-01", "2026-06-20", "2026-06-25"]


@pytest.mark.django_db
def test_entry_carries_what_the_calendar_needs_to_render_and_link(client, exam):
    _stage(exam, exam_date=date(2026, 6, 25))

    entry = client.get(URL, {"month": "2026-06"}).json()[0]

    assert entry["exam_name"] == exam.name
    assert entry["exam_slug"] == exam.slug
    assert entry["board_code"] == "UPSC"
    assert entry["stage_type"] == "prelims"
    assert entry["milestone_label"] == "Exam date"


@pytest.mark.django_db
def test_excludes_milestones_outside_the_requested_month(client, exam):
    _stage(
        exam,
        sequence=1,
        exam_date=date(2026, 5, 31),
        result_date=date(2026, 7, 1),
        notification_date=date(2026, 6, 15),
    )

    entries = client.get(URL, {"month": "2026-06"}).json()

    assert [e["milestone"] for e in entries] == ["notification"]


@pytest.mark.django_db
def test_month_boundaries_are_inclusive(client, exam):
    _stage(exam, notification_date=date(2026, 6, 1), result_date=date(2026, 6, 30))

    entries = client.get(URL, {"month": "2026-06"}).json()

    assert [e["date"] for e in entries] == ["2026-06-01", "2026-06-30"]


@pytest.mark.django_db
def test_handles_february_in_a_leap_year(client, exam):
    _stage(exam, result_date=date(2024, 2, 29))

    entries = client.get(URL, {"month": "2024-02"}).json()

    assert [e["date"] for e in entries] == ["2024-02-29"]


@pytest.mark.django_db
def test_entries_from_several_exams_are_sorted_by_date(client, exam, board):
    other = Exam.objects.create(
        board=board, code="CDS", name="A CDS Exam", cycle_year=2026, category="d"
    )
    _stage(exam, exam_date=date(2026, 6, 20))
    _stage(other, exam_date=date(2026, 6, 5))

    entries = client.get(URL, {"month": "2026-06"}).json()

    assert [e["date"] for e in entries] == ["2026-06-05", "2026-06-20"]


@pytest.mark.django_db
def test_stages_with_no_dates_produce_no_entries(client, exam):
    _stage(exam)

    assert client.get(URL, {"month": "2026-06"}).json() == []


@pytest.mark.django_db
def test_rejects_a_malformed_month(client):
    assert client.get(URL, {"month": "not-a-month"}).status_code == 400
    assert client.get(URL, {"month": "2026-13"}).status_code == 400


@pytest.mark.django_db
def test_defaults_to_the_current_month(client, exam):
    today = timezone.now().date()
    _stage(exam, exam_date=today)

    entries = client.get(URL).json()

    assert [e["date"] for e in entries] == [today.isoformat()]
