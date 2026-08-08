from django.contrib import admin

from .models import Board, Exam, ExamStage


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "timezone", "active")
    list_filter = ("active",)
    search_fields = ("name", "code")


class ExamStageInline(admin.TabularInline):
    model = ExamStage
    extra = 1
    # Timeline milestones are editable here too - without this, the fields
    # exist but staff have no way to fill them in short of the shell, which
    # is the gap EXT-015 exists to prevent.
    fields = (
        "stage_type",
        "sequence",
        "planned_start_date",
        "planned_end_date",
        "notification_date",
        "admit_card_date",
        "exam_date",
        "answer_key_date",
        "result_date",
    )


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ("name", "board", "code", "cycle_year", "category", "slug")
    list_filter = ("board", "category")
    search_fields = ("name", "code")
    inlines = [ExamStageInline]


@admin.register(ExamStage)
class ExamStageAdmin(admin.ModelAdmin):
    list_display = ("exam", "stage_type", "sequence", "planned_start_date", "planned_end_date")
    list_filter = ("exam", "stage_type")
