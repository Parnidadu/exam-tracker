from django.contrib import admin

from exams.models import Board


def test_board_registered_in_admin():
    assert admin.site.is_registered(Board)
