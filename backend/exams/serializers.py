from rest_framework import serializers

from .models import Board, Discrepancy, Exam, ExamStage, StatusTrack


class BoardSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Board
        fields = ["id", "name", "code"]


class ExamSerializer(serializers.ModelSerializer):
    board = BoardSummarySerializer(read_only=True)

    class Meta:
        model = Exam
        fields = ["id", "board", "code", "name", "cycle_year", "category", "slug"]


class StatusTrackSerializer(serializers.ModelSerializer):
    effective_status = serializers.ReadOnlyField()
    # Exposed so the UI's freshness indicator doesn't have to reimplement
    # STALENESS_WINDOW in JavaScript, where it could drift from the rule the
    # resolver and the machine-overwrite guard both read.
    is_verification_fresh = serializers.ReadOnlyField()

    class Meta:
        model = StatusTrack
        fields = [
            "track",
            "machine_value",
            "machine_confidence",
            "machine_seen_at",
            "human_value",
            "verified_by",
            "verified_at",
            "effective_status",
            "is_verification_fresh",
        ]


class ExamStageSerializer(serializers.ModelSerializer):
    status_tracks = StatusTrackSerializer(many=True, read_only=True)

    class Meta:
        model = ExamStage
        fields = [
            "id",
            "stage_type",
            "sequence",
            "planned_start_date",
            "planned_end_date",
            "notification_date",
            "admit_card_date",
            "exam_date",
            "answer_key_date",
            "result_date",
            "status_tracks",
        ]


class ExamDetailSerializer(serializers.ModelSerializer):
    board = BoardSummarySerializer(read_only=True)
    stages = ExamStageSerializer(many=True, read_only=True)

    class Meta:
        model = Exam
        fields = ["id", "board", "code", "name", "cycle_year", "category", "slug", "stages"]


class CalendarEntrySerializer(serializers.Serializer):
    """One milestone falling on one date. Flattened deliberately: the
    calendar needs date -> entries, not exam -> stages -> dates."""

    date = serializers.DateField(read_only=True)
    milestone = serializers.CharField(read_only=True)
    milestone_label = serializers.CharField(read_only=True)
    exam_slug = serializers.CharField(read_only=True)
    exam_name = serializers.CharField(read_only=True)
    board_code = serializers.CharField(read_only=True)
    stage_type = serializers.CharField(read_only=True)


class DiscrepancySerializer(serializers.ModelSerializer):
    """The verifier-facing shape of a discrepancy.

    `evidence_url` is required here even though the model allows it blank.
    The model stays permissive so a future import or backfill is not
    blocked by rows whose source was lost; this API is the only way a
    person creates one, and a person filing "the paper leaked" without
    saying where that came from is filing a rumour.
    """

    evidence_url = serializers.URLField(max_length=500, required=True, allow_blank=False)
    exam_stage = serializers.PrimaryKeyRelatedField(queryset=ExamStage.objects.all())

    exam_slug = serializers.CharField(source="exam_stage.exam.slug", read_only=True)
    exam_name = serializers.CharField(source="exam_stage.exam.name", read_only=True)
    board_code = serializers.CharField(source="exam_stage.exam.board.code", read_only=True)
    stage_type = serializers.CharField(source="exam_stage.stage_type", read_only=True)
    reported_by = serializers.EmailField(source="reported_by.email", read_only=True)
    resolved_by = serializers.EmailField(source="resolved_by.email", read_only=True)
    is_open = serializers.ReadOnlyField()
    #: What this discrepancy may become next, so the UI can offer exactly
    #: the transitions that will succeed rather than offering all of them
    #: and reporting a failure after the fact.
    available_transitions = serializers.SerializerMethodField()

    class Meta:
        model = Discrepancy
        fields = [
            "id",
            "exam_stage",
            "exam_slug",
            "exam_name",
            "board_code",
            "stage_type",
            "discrepancy_type",
            "severity",
            "status",
            "description",
            "evidence_url",
            "evidence_note",
            "occurred_on",
            "reported_by",
            "reported_at",
            "resolved_by",
            "resolved_at",
            "resolution_note",
            "is_open",
            "available_transitions",
        ]
        read_only_fields = [
            "status",
            "reported_by",
            "reported_at",
            "resolved_by",
            "resolved_at",
            "resolution_note",
        ]

    def get_available_transitions(self, discrepancy: Discrepancy) -> list[str]:
        return sorted(
            str(option) for option in Discrepancy.TRANSITIONS.get(discrepancy.status, set())
        )


class DiscrepancyTransitionSerializer(serializers.Serializer):
    """Moving a discrepancy along its lifecycle.

    Status is not editable through the ordinary update endpoint: the
    lifecycle has rules, and a PATCH that happens to include `status`
    would let a client walk past them by accident. Making the move its own
    action keeps "edit the details" and "change what this claim means"
    from looking like the same operation.
    """

    status = serializers.ChoiceField(choices=Discrepancy.Status.choices)
    #: Required when closing. A dismissed leak claim with no explanation is
    #: indistinguishable from one nobody bothered to look at.
    resolution_note = serializers.CharField(required=False, allow_blank=True)
    #: Optional new evidence - the notice announcing the re-exam, or the
    #: order vacating the stay. The previous value is not lost: Discrepancy
    #: carries HistoricalRecords.
    evidence_url = serializers.URLField(
        max_length=500, required=False, allow_blank=False
    )

    def validate(self, attrs: dict) -> dict:
        discrepancy: Discrepancy = self.context["discrepancy"]
        new_status = attrs["status"]

        if not discrepancy.can_become(new_status):
            # str(), not the member itself: interpolating a TextChoices
            # member into a list renders "Discrepancy.Status.CONFIRMED"
            # into a message whose whole job is to say what to send.
            allowed = sorted(
                str(option) for option in Discrepancy.TRANSITIONS.get(discrepancy.status, set())
            )
            raise serializers.ValidationError(
                {
                    "status": (
                        f"A {discrepancy.status} discrepancy cannot become {new_status}. "
                        f"Allowed: {allowed or 'nothing - it is closed'}."
                    )
                }
            )

        if new_status in Discrepancy.TERMINAL and not attrs.get("resolution_note", "").strip():
            raise serializers.ValidationError(
                {"resolution_note": "Say why this was resolved or dismissed."}
            )
        return attrs
