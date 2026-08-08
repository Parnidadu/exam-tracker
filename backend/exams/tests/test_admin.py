from django.contrib import admin

from exams.models import Board, Exam


def test_board_registered_in_admin():
    assert admin.site.is_registered(Board)


def test_exam_registered_in_admin():
    assert admin.site.is_registered(Exam)


def test_exam_admin_is_filterable_by_board():
    exam_admin = admin.site._registry[Exam]
    assert "board" in exam_admin.list_filter
