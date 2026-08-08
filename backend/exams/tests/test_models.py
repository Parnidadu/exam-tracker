import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from exams.models import Board


@pytest.mark.django_db
def test_board_str_returns_name():
    board = Board.objects.create(
        name="Union Public Service Commission",
        code="UPSC",
        official_url="https://upsc.gov.in",
        timezone="Asia/Kolkata",
    )
    assert str(board) == "Union Public Service Commission"


@pytest.mark.django_db
def test_board_defaults_to_active():
    board = Board.objects.create(
        name="Union Public Service Commission",
        code="UPSC",
        official_url="https://upsc.gov.in",
        timezone="Asia/Kolkata",
    )
    assert board.active is True


@pytest.mark.django_db
def test_board_code_must_be_unique():
    Board.objects.create(
        name="Union Public Service Commission",
        code="UPSC",
        official_url="https://upsc.gov.in",
        timezone="Asia/Kolkata",
    )
    with pytest.raises(IntegrityError):
        Board.objects.create(
            name="Staff Selection Commission",
            code="UPSC",
            official_url="https://ssc.nic.in",
            timezone="Asia/Kolkata",
        )


@pytest.mark.django_db
def test_board_rejects_invalid_timezone():
    board = Board(
        name="Test Board",
        code="TEST",
        official_url="https://example.com",
        timezone="Not/ARealZone",
    )
    with pytest.raises(ValidationError):
        board.full_clean()
