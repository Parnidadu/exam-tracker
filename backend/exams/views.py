from calendar import monthrange
from datetime import date

from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.exceptions import ParseError

from config.caching import PublicCacheMixin

from .models import Board, Exam, ExamStage, StatusTrack
from .serializers import (
    BoardSummarySerializer,
    CalendarEntrySerializer,
    ExamDetailSerializer,
    ExamSerializer,
)


def _parse_date(value: str, param_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ParseError(f"{param_name} must be an ISO date (YYYY-MM-DD).") from exc


@extend_schema(
    parameters=[
        OpenApiParameter(
            name="board",
            type=str,
            description="Filter by Board.code, e.g. UPSC.",
        ),
        OpenApiParameter(
            name="search",
            type=str,
            description="Case-insensitive substring match on the exam name.",
        ),
        OpenApiParameter(
            name="conduct_status",
            type=str,
            description=(
                "Filter by the conduct track's effective_status (the human-verified "
                "value if fresh, else the machine-observed value). Matches when any "
                "stage of the exam has that status."
            ),
        ),
        OpenApiParameter(
            name="result_status",
            type=str,
            description=(
                "Filter by the result track's effective_status. Matches when any "
                "stage of the exam has that status - not necessarily the same stage "
                "that satisfies conduct_status."
            ),
        ),
        OpenApiParameter(
            name="status",
            type=str,
            deprecated=True,
            description="Deprecated alias for conduct_status, kept for compatibility.",
        ),
        OpenApiParameter(
            name="start_date",
            type=str,
            description=(
                "ISO date. Matches exams with at least one stage whose planned date "
                "range overlaps [start_date, end_date]."
            ),
        ),
        OpenApiParameter(
            name="end_date",
            type=str,
            description=(
                "ISO date. Matches exams with at least one stage whose planned date "
                "range overlaps [start_date, end_date]."
            ),
        ),
    ]
)
class ExamListView(PublicCacheMixin, generics.ListAPIView):
    """GET /api/exams/ - paginated, filterable by board, status, and date range."""

    serializer_class = ExamSerializer
    queryset = Exam.objects.select_related("board").prefetch_related("stages__status_tracks")

    def get_queryset(self) -> QuerySet[Exam]:
        queryset = super().get_queryset()

        board = self.request.query_params.get("board")
        if board:
            queryset = queryset.filter(board__code=board)

        start_date = self.request.query_params.get("start_date")
        end_date = self.request.query_params.get("end_date")
        if start_date or end_date:
            queryset = self._filter_by_date_range(queryset, start_date, end_date)

        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(name__icontains=search)

        # "status" is EXT-017's original conduct-only parameter; keep it
        # working so the documented contract doesn't break under callers.
        conduct_status = self.request.query_params.get(
            "conduct_status"
        ) or self.request.query_params.get("status")
        if conduct_status:
            queryset = self._filter_by_track_status(
                queryset, StatusTrack.Track.CONDUCT, conduct_status
            )

        result_status = self.request.query_params.get("result_status")
        if result_status:
            queryset = self._filter_by_track_status(
                queryset, StatusTrack.Track.RESULT, result_status
            )

        return queryset

    @staticmethod
    def _filter_by_date_range(
        queryset: QuerySet[Exam], start_date: str | None, end_date: str | None
    ) -> QuerySet[Exam]:
        stage_filter = Q(stages__planned_start_date__isnull=False) & Q(
            stages__planned_end_date__isnull=False
        )
        if end_date:
            stage_filter &= Q(stages__planned_start_date__lte=_parse_date(end_date, "end_date"))
        if start_date:
            stage_filter &= Q(stages__planned_end_date__gte=_parse_date(start_date, "start_date"))
        return queryset.filter(stage_filter).distinct()

    @staticmethod
    def _filter_by_track_status(
        queryset: QuerySet[Exam], track_name: str, value: str
    ) -> QuerySet[Exam]:
        """Keep exams where *any* stage's given track resolves to `value`.

        Applied per track, so combining conduct_status and result_status
        does not require one stage to satisfy both - an exam whose prelims
        were conducted and whose mains results are awaited matches both.

        Evaluated in Python rather than SQL because effective_status is a
        resolver (EXT-014), not a column; re-expressing its staleness rule
        as an ORM predicate would let the two definitions drift.
        """
        matching_ids = set()
        for exam in queryset:
            for stage in exam.stages.all():
                for track in stage.status_tracks.all():
                    if track.track == track_name and track.effective_status == value:
                        matching_ids.add(exam.pk)
        return queryset.filter(pk__in=matching_ids)


class ExamDetailView(PublicCacheMixin, generics.RetrieveAPIView):
    """GET /api/exams/<slug>/ - exam with all stages and each stage's three
    status tracks, in a small constant number of queries (no N+1: one for
    the exam, one for its stages, one for all of those stages' status
    tracks) regardless of how many stages or tracks exist."""

    serializer_class = ExamDetailSerializer
    lookup_field = "slug"
    queryset = Exam.objects.select_related("board").prefetch_related(
        Prefetch(
            "stages",
            queryset=ExamStage.objects.order_by("sequence").prefetch_related(
                Prefetch("status_tracks", queryset=StatusTrack.objects.order_by("track"))
            ),
        )
    )


class BoardListView(PublicCacheMixin, generics.ListAPIView):
    """GET /api/boards/ - active boards, for populating the public list's
    board filter. Without this the UI could only offer boards that happen
    to appear on the current page of results."""

    serializer_class = BoardSummarySerializer
    pagination_class = None
    queryset = Board.objects.filter(active=True).order_by("name")


@extend_schema(
    parameters=[
        OpenApiParameter(
            name="month",
            type=str,
            description="Month to fetch, as YYYY-MM. Defaults to the current month.",
        )
    ],
    responses=CalendarEntrySerializer(many=True),
)
class CalendarView(PublicCacheMixin, generics.ListAPIView):
    """GET /api/calendar/?month=YYYY-MM - every stage milestone falling in
    that month, one entry per (stage, milestone).

    Flattened server-side because the calendar needs date -> entries; the
    exam-detail shape would make the client unpick five nullable date
    fields across every stage of every exam just to fill a grid.
    """

    serializer_class = CalendarEntrySerializer
    pagination_class = None

    def get_queryset(self):
        first, last = self._month_bounds()
        milestone_fields = [key for key, _ in ExamStage.TIMELINE_MILESTONES]

        # One row per stage that has at least one milestone in range.
        date_filter = Q()
        for key in milestone_fields:
            date_filter |= Q(**{f"{key}_date__range": (first, last)})

        stages = (
            ExamStage.objects.filter(date_filter)
            .select_related("exam", "exam__board")
            .order_by("exam__name", "sequence")
        )

        entries = []
        for stage in stages:
            for key, label in ExamStage.TIMELINE_MILESTONES:
                value = getattr(stage, f"{key}_date")
                if value is None or not (first <= value <= last):
                    continue
                entries.append(
                    {
                        "date": value,
                        "milestone": key,
                        "milestone_label": label,
                        "exam_slug": stage.exam.slug,
                        "exam_name": stage.exam.name,
                        "board_code": stage.exam.board.code,
                        "stage_type": stage.stage_type,
                    }
                )
        entries.sort(key=lambda e: (e["date"], e["exam_name"], e["milestone"]))
        return entries

    def _month_bounds(self) -> tuple[date, date]:
        raw = self.request.query_params.get("month")
        today = timezone.now().date()
        if not raw:
            year, month = today.year, today.month
        else:
            try:
                year_str, month_str = raw.split("-")
                year, month = int(year_str), int(month_str)
                if not 1 <= month <= 12:
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise ParseError("month must be in YYYY-MM format.") from exc

        first = date(year, month, 1)
        last = date(year, month, monthrange(year, month)[1])
        return first, last
