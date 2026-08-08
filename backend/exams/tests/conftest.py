import pytest

from exams.models import Board


@pytest.fixture
def board(db):
    return Board.objects.create(
        name="Union Public Service Commission",
        code="UPSC",
        official_url="https://upsc.gov.in",
        timezone="Asia/Kolkata",
    )
