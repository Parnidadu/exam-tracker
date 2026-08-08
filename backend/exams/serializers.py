from rest_framework import serializers

from .models import Board, Exam, ExamStage, StatusTrack


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
            "status_tracks",
        ]


class ExamDetailSerializer(serializers.ModelSerializer):
    board = BoardSummarySerializer(read_only=True)
    stages = ExamStageSerializer(many=True, read_only=True)

    class Meta:
        model = Exam
        fields = ["id", "board", "code", "name", "cycle_year", "category", "slug", "stages"]
