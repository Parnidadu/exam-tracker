import pytest
from django.db import IntegrityError

from exams.models import Exam


def _make_exam(board, **overrides):
    fields = {
        "board": board,
        "code": "CSE",
        "name": "Civil Services Examination",
        "cycle_year": 2026,
        "category": "Civil Services",
    }
    fields.update(overrides)
    return Exam.objects.create(**fields)


def test_exam_slug_is_auto_generated(board):
    exam = _make_exam(board)
    assert exam.slug == "upsc-cse-2026"


def test_exam_slug_is_not_overwritten_if_already_set(board):
    exam = _make_exam(board, slug="custom-slug")
    assert exam.slug == "custom-slug"


def test_exam_slug_collision_gets_a_unique_suffix(board):
    first = _make_exam(board)
    # A different code that slugifies to the same string as "CSE" - the
    # punctuation is stripped, so this collides with `first`'s slug.
    second = _make_exam(board, code="CSE!!!")
    assert first.slug == "upsc-cse-2026"
    assert second.slug == "upsc-cse-2026-2"


@pytest.mark.django_db
def test_exam_unique_together_board_code_cycle_year(board):
    _make_exam(board)
    with pytest.raises(IntegrityError):
        _make_exam(board)


def test_exam_str(board):
    exam = _make_exam(board)
    assert str(exam) == "UPSC CSE 2026"
