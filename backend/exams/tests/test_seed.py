import pytest
from django.core.management import call_command

from exams.models import Board, Exam, ExamStage


@pytest.mark.django_db
def test_seed_populates_three_boards_and_ten_exams():
    call_command("seed")

    assert Board.objects.count() == 3
    assert set(Board.objects.values_list("code", flat=True)) == {"UPSC", "SSC", "IBPS"}
    assert Exam.objects.count() == 10
    assert ExamStage.objects.count() > 0


@pytest.mark.django_db
def test_seed_is_idempotent():
    call_command("seed")
    call_command("seed")

    assert Board.objects.count() == 3
    assert Exam.objects.count() == 10
    stage_count = ExamStage.objects.count()

    call_command("seed")
    assert ExamStage.objects.count() == stage_count
