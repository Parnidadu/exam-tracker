import pytest

from exams.models import Board, Exam


@pytest.mark.django_db
def test_exam_list_still_public_after_default_permission_wiring(client):
    Board.objects.create(
        name="Union Public Service Commission",
        code="UPSC",
        official_url="https://upsc.gov.in",
        timezone="Asia/Kolkata",
    )
    response = client.get("/api/exams/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_exam_detail_still_public_after_default_permission_wiring(client):
    board = Board.objects.create(
        name="Union Public Service Commission",
        code="UPSC",
        official_url="https://upsc.gov.in",
        timezone="Asia/Kolkata",
    )
    exam = Exam.objects.create(
        board=board,
        code="CSE",
        name="Civil Services Examination",
        cycle_year=2026,
        category="Civil Services",
    )
    response = client.get(f"/api/exams/{exam.slug}/")
    assert response.status_code == 200
