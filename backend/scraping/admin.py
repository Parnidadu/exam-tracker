from django.contrib import admin
from django.utils.html import format_html
from django_celery_beat.models import PeriodicTask

from .health import is_stale, missed_runs
from .models import Snapshot, Source, SourceHealth
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
        "health_state",
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

    @admin.display(description="health")
    def health_state(self, obj: Source) -> str:
        """A pointer to the health dashboard, shown here because this is
        the page an operator is already on when a board looks wrong."""
        health = getattr(obj, "health", None)
        if health is None:
            return "-"
        return health_label(health)


def health_label(health: SourceHealth) -> str:
    """One scannable summary of a source's state.

    Shared by both admin pages rather than duplicated, so the source list
    and the health dashboard can never disagree about what "stale" means.

    Failing and stale are named differently on purpose: a failing source
    is shouting, a stale one has gone quiet, and the quiet one is easier
    to miss and usually the worse problem.
    """
    source = health.source
    if not source.enabled:
        return format_html('<span style="color:#888">{}</span>', "paused")
    if is_stale(source, health):
        label = (
            "never succeeded"
            if not health.has_ever_succeeded
            else f"{missed_runs(source, health)} runs missed"
        )
        return format_html('<b style="color:#b32d2e">stale - {}</b>', label)
    if health.consecutive_failures:
        return format_html(
            '<b style="color:#c9700a">failing ({})</b>', health.consecutive_failures
        )
    if not health.has_ever_succeeded:
        return format_html('<span style="color:#888">{}</span>', "not yet run")
    return format_html('<span style="color:#2b7d2b">{}</span>', "ok")


class StaleFilter(admin.SimpleListFilter):
    """Staleness is computed from each source's own cron, so it cannot be
    a database filter - this evaluates it in Python over the source list.

    Fine at this scale: sources are counted in tens, one per board per
    page being scraped, and the alternative would be caching a derived
    boolean that goes wrong the moment someone edits a cron.
    """

    title = "staleness"
    parameter_name = "stale"

    def lookups(self, request, model_admin):
        return [("yes", "Overdue"), ("no", "On schedule")]

    def queryset(self, request, queryset):
        if self.value() not in {"yes", "no"}:
            return queryset
        wanted = self.value() == "yes"
        matching = [
            health.pk
            for health in queryset.select_related("source")
            if is_stale(health.source, health) is wanted
        ]
        return queryset.filter(pk__in=matching)


@admin.register(SourceHealth)
class SourceHealthAdmin(admin.ModelAdmin):
    """The scraper health dashboard.

    Read-only throughout: every value here is something a scrape run
    observed. Editing one would be editing the record of what happened,
    which is the same reason Snapshot is read-only below.

    It lives in admin rather than the React app because this is an
    operator's view of infrastructure. The public dashboard answers "when
    is my exam", and a citizen has no use for a board's failure streak.
    """

    list_display = (
        "source",
        "board",
        "state",
        "last_success_at",
        "last_failure_at",
        "consecutive_failures",
        "last_error_summary",
    )
    list_filter = (StaleFilter, "source__board", "source__enabled")
    search_fields = ("source__name", "source__url", "last_error")
    readonly_fields = (
        "source",
        "state",
        "last_success_at",
        "last_failure_at",
        "consecutive_failures",
        "last_checked_at",
        "last_error",
        "missed_scheduled_runs",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("source", "source__board")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    @admin.display(description="board", ordering="source__board__name")
    def board(self, obj: SourceHealth) -> str:
        return obj.source.board.code

    @admin.display(description="state")
    def state(self, obj: SourceHealth) -> str:
        return health_label(obj)

    @admin.display(description="scheduled runs missed")
    def missed_scheduled_runs(self, obj: SourceHealth) -> str:
        return str(missed_runs(obj.source, obj))

    @admin.display(description="last error")
    def last_error_summary(self, obj: SourceHealth) -> str:
        if not obj.last_error:
            return "-"
        return obj.last_error if len(obj.last_error) <= 80 else obj.last_error[:77] + "..."


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
