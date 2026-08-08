import pytest

from accounts.models import Role, User
from exams.models import Board, Exam, ExamStage


@pytest.fixture
def board(db):
    return Board.objects.create(
        name="Union Public Service Commission",
        code="UPSC",
        official_url="https://upsc.gov.in",
        timezone="Asia/Kolkata",
    )


@pytest.fixture
def exam(board):
    return Exam.objects.create(
        board=board,
        code="CSE",
        name="Civil Services Examination",
        cycle_year=2026,
        category="Civil Services",
    )


@pytest.fixture
def exam_stage(exam):
    return ExamStage.objects.create(
        exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1
    )


@pytest.fixture
def actor(db):
    return User.objects.create_user(
        email="verifier@example.com", password="pw", role=Role.VERIFIER
    )
