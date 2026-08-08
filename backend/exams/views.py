from datetime import date

from django.db.models import Q, QuerySet
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.exceptions import ParseError

from .models import Exam, StatusTrack
from .serializers import ExamSerializer


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
            name="status",
            type=str,
            description=(
                "Filter by the conduct track's effective_status "
                "(the human-verified value if fresh, else the machine-observed value)."
            ),
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
class ExamListView(generics.ListAPIView):
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

        status_value = self.request.query_params.get("status")
        if status_value:
            queryset = self._filter_by_status(queryset, status_value)

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
    def _filter_by_status(queryset: QuerySet[Exam], value: str) -> QuerySet[Exam]:
        matching_ids = set()
        for exam in queryset:
            for stage in exam.stages.all():
                for track in stage.status_tracks.all():
                    if (
                        track.track == StatusTrack.Track.CONDUCT
                        and track.effective_status == value
                    ):
                        matching_ids.add(exam.pk)
        return queryset.filter(pk__in=matching_ids)
