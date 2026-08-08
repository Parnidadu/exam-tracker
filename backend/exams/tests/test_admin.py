import pytest
from django.contrib import admin

from exams.models import Board, Exam, ExamStage


def test_board_registered_in_admin():
    assert admin.site.is_registered(Board)


def test_exam_registered_in_admin():
    assert admin.site.is_registered(Exam)


def test_examstage_registered_in_admin():
    assert admin.site.is_registered(ExamStage)


def test_exam_admin_is_filterable_by_board():
    exam_admin = admin.site._registry[Exam]
    assert "board" in exam_admin.list_filter


def test_exam_admin_has_examstage_inline():
    exam_admin = admin.site._registry[Exam]
    assert any(inline.model is ExamStage for inline in exam_admin.inlines)


@pytest.mark.django_db
def test_staff_can_create_board_exam_and_stages_via_admin(admin_client):
    board_response = admin_client.post(
        "/admin/exams/board/add/",
        {
            "name": "Union Public Service Commission",
            "code": "UPSC",
            "official_url": "https://upsc.gov.in",
            "timezone": "Asia/Kolkata",
            "active": "on",
        },
    )
    assert board_response.status_code == 302
    board = Board.objects.get(code="UPSC")

    exam_response = admin_client.post(
        "/admin/exams/exam/add/",
        {
            "board": board.pk,
            "code": "CSE",
            "name": "Civil Services Examination",
            "cycle_year": 2026,
            "category": "Civil Services",
            "slug": "",
            "stages-TOTAL_FORMS": "1",
            "stages-INITIAL_FORMS": "0",
            "stages-MIN_NUM_FORMS": "0",
            "stages-MAX_NUM_FORMS": "1000",
            "stages-0-id": "",
            "stages-0-stage_type": "prelims",
            "stages-0-sequence": "1",
            "stages-0-planned_start_date": "",
            "stages-0-planned_end_date": "",
        },
    )
    assert exam_response.status_code == 302
    exam = Exam.objects.get(board=board, code="CSE", cycle_year=2026)
    assert exam.slug == "upsc-cse-2026"

    stage = ExamStage.objects.get(exam=exam)
    assert stage.stage_type == ExamStage.StageType.PRELIMS
    assert stage.sequence == 1
