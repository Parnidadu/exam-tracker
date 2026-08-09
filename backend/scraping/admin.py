from django.contrib import admin
from django_celery_beat.models import PeriodicTask

from .models import Snapshot, Source
from .schedules import schedule_name


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    """Every field a scraper reads is editable here.

    That is the whole point of the ticket: changing a board's URL or its
    polling schedule is an admin edit, not a deploy.
    """

    list_display = (
        "name",
        "board",
        "url",
        "fetch_strategy",
        "parser_key",
        "cron",
        "enabled",
        "schedule_state",
    )
    list_filter = ("board", "fetch_strategy", "enabled")
    list_editable = ("url", "cron", "enabled")
    search_fields = ("name", "url", "parser_key")
    fields = ("board", "name", "url", "fetch_strategy", "parser_key", "cron", "enabled")
    readonly_fields = ("schedule_state",)

    @admin.display(description="beat schedule")
    def schedule_state(self, obj: Source) -> str:
        """Shows what Beat will actually do with this source.

        Without it, ticking `enabled` gives no feedback at all - the
        operator has to trust that something happened somewhere else.
        The schedule is derived from this row (see scraping.schedules), so
        this is a readout, not a second place to edit it.
        """
        task = PeriodicTask.objects.filter(name=schedule_name(obj)).first()
        if task is None:
            return "not scheduled"
        if not task.enabled:
            return "paused"
        if task.last_run_at is None:
            return f"{task.crontab} - not yet run"
        return f"{task.crontab} - last run {task.last_run_at:%Y-%m-%d %H:%M} UTC"


@admin.register(Snapshot)
class SnapshotAdmin(admin.ModelAdmin):
    """Read-only: a snapshot is captured evidence of what a site served.

    Editing one would quietly rewrite the record the parsers and any later
    investigation rely on, so this exists to inspect, not to change.
    """

    list_display = ("source", "short_hash", "status_code", "times_seen", "last_seen_at")
    list_filter = ("source", "status_code")
    search_fields = ("content_hash", "url")
    readonly_fields = (
        "source",
        "url",
        "content_hash",
        "content",
        "status_code",
        "first_seen_at",
        "last_seen_at",
        "times_seen",
    )

    @admin.display(description="hash")
    def short_hash(self, obj: Snapshot) -> str:
        return obj.content_hash[:12]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
