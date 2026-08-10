from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from django_celery_beat.models import PeriodicTask

from exams.models import Board, ExamStage

from .health import is_stale, missed_runs
from .models import Snapshot, Source, SourceHealth, TriageItem
from .schedules import schedule_name
from .triage import (
    AlreadyResolved,
    create_exam_and_link,
    dismiss,
    link_to_stage,
    suggested_stages,
)


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


class LinkToStageForm(forms.Form):
    """Offers the matcher's own shortlist first, then anything else.

    A plain stage dropdown across every exam is unusable once there are a
    few hundred stages, and the shortlist is exactly what the matcher
    already thought plausible.
    """

    exam_stage = forms.ModelChoiceField(queryset=ExamStage.objects.none(), label="Link to stage")
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, item: TriageItem, **kwargs):
        super().__init__(*args, **kwargs)
        # Narrowed rather than indexed straight off self.fields, whose
        # value type is the base Field - only ModelChoiceField has a
        # queryset, and mypy is right to say so.
        field: forms.ModelChoiceField = self.fields["exam_stage"]  # type: ignore[assignment]
        field.queryset = ExamStage.objects.select_related("exam", "exam__board").order_by(
            "exam__cycle_year", "exam__code", "sequence"
        )
        suggestions = suggested_stages(item)
        if suggestions:
            field.initial = suggestions[0].pk
            field.help_text = "The matcher suggested: " + ", ".join(
                str(stage) for stage in suggestions
            )


class CreateExamForm(forms.Form):
    board = forms.ModelChoiceField(queryset=Board.objects.all())
    code = forms.CharField(max_length=50)
    name = forms.CharField(max_length=255)
    cycle_year = forms.IntegerField(min_value=1990, max_value=2100)
    stage_type = forms.ChoiceField(
        choices=ExamStage.StageType.choices,
        initial=ExamStage.StageType.SINGLE,
        help_text=(
            "One stage is created. Which stages this exam really has is "
            "a domain fact - add the rest on the exam itself."
        ),
    )
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class DismissForm(forms.Form):
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=(
            "Why this is not about a tracked exam. Worth recording - the "
            "same notice will keep arriving."
        ),
    )


@admin.register(TriageItem)
class TriageItemAdmin(admin.ModelAdmin):
    """The triage queue.

    Read-only as a form: an operator resolves an item through one of the
    three actions, not by editing its fields. Letting someone retype
    `exam_name` would quietly change the fingerprint and split one
    recurring problem into two queue items.
    """

    list_display = (
        "exam_name",
        "board",
        "track",
        "value",
        "reason",
        "match_confidence",
        "times_seen",
        "status",
        "actions_column",
    )
    list_filter = ("status", "reason", "track", "source__board")
    search_fields = ("exam_name", "raw_text", "source_url")
    readonly_fields = tuple(
        field.name for field in TriageItem._meta.fields if field.name != "id"
    ) + ("evidence",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("source", "source__board", "exam_stage")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    @admin.display(description="board", ordering="source__board__name")
    def board(self, obj: TriageItem) -> str:
        return obj.source.board.code if obj.source else "-"

    @admin.display(description="evidence")
    def evidence(self, obj: TriageItem) -> str:
        if not obj.source_url:
            return obj.raw_text or "-"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a><br><br>{}',
            obj.source_url,
            obj.source_url,
            obj.raw_text,
        )

    @admin.display(description="triage")
    def actions_column(self, obj: TriageItem) -> str:
        if obj.is_resolved:
            return format_html(
                "{} by {}", obj.get_status_display(), obj.resolved_by or "-"
            )
        return format_html(
            '<a class="button" href="{}">Link</a> '
            '<a class="button" href="{}">Create exam</a> '
            '<a class="button" href="{}">Dismiss</a>',
            reverse("admin:scraping_triageitem_link", args=[obj.pk]),
            reverse("admin:scraping_triageitem_create", args=[obj.pk]),
            reverse("admin:scraping_triageitem_dismiss", args=[obj.pk]),
        )

    def get_urls(self):
        return [
            path(
                "<int:pk>/link/",
                self.admin_site.admin_view(self.link_view),
                name="scraping_triageitem_link",
            ),
            path(
                "<int:pk>/create-exam/",
                self.admin_site.admin_view(self.create_view),
                name="scraping_triageitem_create",
            ),
            path(
                "<int:pk>/dismiss/",
                self.admin_site.admin_view(self.dismiss_view),
                name="scraping_triageitem_dismiss",
            ),
        ] + super().get_urls()

    # --- the three actions ---------------------------------------------

    def _render(self, request, item, form, title):
        return render(
            request,
            "admin/scraping/triageitem/action.html",
            {
                **self.admin_site.each_context(request),
                "title": title,
                "item": item,
                "form": form,
                "opts": self.model._meta,
            },
        )

    def _actor(self, request) -> str:
        return request.user.get_username()

    def link_view(self, request, pk):
        item = self.get_object(request, pk)
        form = LinkToStageForm(request.POST or None, item=item)
        if request.method == "POST" and form.is_valid():
            try:
                resolved, applied = link_to_stage(
                    item,
                    form.cleaned_data["exam_stage"],
                    actor=self._actor(request),
                    note=form.cleaned_data["note"],
                )
            except AlreadyResolved as exc:
                self.message_user(request, str(exc), messages.WARNING)
            else:
                if applied.conflict is not None:
                    # Linking succeeded; writing the value did not. Saying
                    # only "linked" would leave the operator believing the
                    # status had been updated when it deliberately was not.
                    self.message_user(
                        request,
                        f"Linked to {resolved.exam_stage}, but the value was not written: "
                        "a recent human verification says otherwise. A conflict has been "
                        "recorded for review.",
                        messages.WARNING,
                    )
                else:
                    self.message_user(request, f"Linked to {resolved.exam_stage}.")
            return redirect("admin:scraping_triageitem_changelist")
        return self._render(request, item, form, "Link observation to a stage")

    def create_view(self, request, pk):
        item = self.get_object(request, pk)
        initial = {"name": item.exam_name[:255], "cycle_year": _guess_year(item.exam_name)}
        if item.source is not None:
            initial["board"] = item.source.board_id
        form = CreateExamForm(request.POST or None, initial=initial)
        if request.method == "POST" and form.is_valid():
            try:
                resolved, stage, _ = create_exam_and_link(
                    item,
                    board=form.cleaned_data["board"],
                    code=form.cleaned_data["code"],
                    name=form.cleaned_data["name"],
                    cycle_year=form.cleaned_data["cycle_year"],
                    stage_type=form.cleaned_data["stage_type"],
                    actor=self._actor(request),
                    note=form.cleaned_data["note"],
                )
            except AlreadyResolved as exc:
                self.message_user(request, str(exc), messages.WARNING)
            else:
                self.message_user(request, f"Created {stage.exam} and linked to {stage}.")
            return redirect("admin:scraping_triageitem_changelist")
        return self._render(request, item, form, "Create a new exam from this observation")

    def dismiss_view(self, request, pk):
        item = self.get_object(request, pk)
        form = DismissForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                dismiss(item, actor=self._actor(request), note=form.cleaned_data["note"])
            except AlreadyResolved as exc:
                self.message_user(request, str(exc), messages.WARNING)
            else:
                self.message_user(request, "Dismissed.")
            return redirect("admin:scraping_triageitem_changelist")
        return self._render(request, item, form, "Dismiss this observation")


def _guess_year(text: str) -> int | None:
    """Prefill only. The operator confirms it, so a wrong guess costs a
    keystroke rather than creating an exam under the wrong cycle."""
    from .matching import _year_in

    return _year_in(text)


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
