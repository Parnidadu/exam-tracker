from django.contrib import admin

from .models import Board, Exam


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "timezone", "active")
    list_filter = ("active",)
    search_fields = ("name", "code")


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ("name", "board", "code", "cycle_year", "category", "slug")
    list_filter = ("board", "category")
    search_fields = ("name", "code")
