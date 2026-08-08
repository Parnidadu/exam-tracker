from rest_framework import serializers

from exams.models import StatusTrack

from .models import VerificationRecord
from .queue import ReasonCode


class VerifyStageSerializer(serializers.Serializer):
    track = serializers.ChoiceField(choices=StatusTrack.Track.choices)
    value = serializers.CharField(max_length=50)
    evidence_url = serializers.URLField(required=False, allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)


class QueueItemSerializer(serializers.ModelSerializer):
    # Both annotated by verification_queue(); declared explicitly so they
    # land in the OpenAPI schema as typed fields rather than untyped extras.
    reason_code = serializers.ChoiceField(choices=ReasonCode.choices, read_only=True)
    queue_priority = serializers.IntegerField(read_only=True)

    exam_stage_id = serializers.IntegerField(source="exam_stage.id", read_only=True)
    exam_slug = serializers.CharField(source="exam_stage.exam.slug", read_only=True)
    exam_name = serializers.CharField(source="exam_stage.exam.name", read_only=True)
    stage_type = serializers.CharField(source="exam_stage.stage_type", read_only=True)
    planned_start_date = serializers.DateField(
        source="exam_stage.planned_start_date", read_only=True
    )

    class Meta:
        model = StatusTrack
        fields = [
            "id",
            "reason_code",
            "queue_priority",
            "exam_stage_id",
            "exam_slug",
            "exam_name",
            "stage_type",
            "planned_start_date",
            "track",
            "machine_value",
            "machine_seen_at",
            "human_value",
            "verified_by",
            "verified_at",
            "effective_status",
        ]


class VerificationRecordSerializer(serializers.ModelSerializer):
    exam_stage_id = serializers.IntegerField(source="exam_stage.id", read_only=True)
    stage_type = serializers.CharField(source="exam_stage.stage_type", read_only=True)
    sequence = serializers.IntegerField(source="exam_stage.sequence", read_only=True)
    actor = serializers.SerializerMethodField()

    class Meta:
        model = VerificationRecord
        fields = [
            "id",
            "exam_stage_id",
            "stage_type",
            "sequence",
            "track",
            "value",
            "evidence_url",
            "note",
            "actor",
            "timestamp",
        ]

    def get_actor(self, record: VerificationRecord) -> str | None:
        """Who verified is shown to signed-in users only.

        The history itself is public for transparency, but the actor is a
        real staff email - publishing it to anonymous visitors would leak
        personal data that the accountability story doesn't require.
        """
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            return record.actor.email
        return None
