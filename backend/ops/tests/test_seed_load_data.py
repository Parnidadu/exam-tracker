"""EXT-065: the dataset generator the load test measures against."""

import pytest
from django.core.management import call_command

from exams.models import Board, Exam, ExamStage, StatusTrack

pytestmark = pytest.mark.django_db


def test_it_creates_a_dataset_worth_measuring_against(capsys):
    """Ten exams would flatter every query - a list endpoint paginating
    twenty rows out of ten is not the endpoint that runs in production."""
    call_command("seed_load_data", "--exams", "40")

    assert Exam.objects.count() == 40
    assert ExamStage.objects.count() > 40
    assert StatusTrack.objects.count() > ExamStage.objects.count()
    assert Board.objects.count() > 1


def test_every_exam_gets_a_slug(capsys):
    """bulk_create bypasses save(), which is where slugs are normally
    generated - so a missed slug here means every detail URL 404s."""
    call_command("seed_load_data", "--exams", "25")

    assert not Exam.objects.filter(slug="").exists()
    assert Exam.objects.values("slug").distinct().count() == 25


def test_the_dataset_is_repeatable(capsys):
    """Same seed, same data - so two runs of the load test are comparable
    rather than being measured against different databases."""
    call_command("seed_load_data", "--exams", "20", "--seed", "1234")
    first = list(Exam.objects.order_by("slug").values_list("slug", flat=True))

    Exam.objects.all().delete()
    call_command("seed_load_data", "--exams", "20", "--seed", "1234")

    assert list(Exam.objects.order_by("slug").values_list("slug", flat=True)) == first


def test_it_spreads_across_boards_years_and_stage_shapes(capsys):
    """A dataset of one board in one year with identical stages would not
    exercise the filters the dashboard actually offers."""
    call_command("seed_load_data", "--exams", "60")

    assert Exam.objects.values("board").distinct().count() > 1
    assert Exam.objects.values("cycle_year").distinct().count() > 1
    assert ExamStage.objects.values("stage_type").distinct().count() > 1


def test_running_it_twice_does_not_collide(capsys):
    """The (board, code, cycle_year) constraint is real; a generator that
    trips it is useless for topping up an existing database."""
    call_command("seed_load_data", "--exams", "20")
    call_command("seed_load_data", "--exams", "20")

    assert Exam.objects.count() == 40
