from rest_framework import serializers

from .models import Board, Exam


class BoardSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Board
        fields = ["id", "name", "code"]


class ExamSerializer(serializers.ModelSerializer):
    board = BoardSummarySerializer(read_only=True)

    class Meta:
        model = Exam
        fields = ["id", "board", "code", "name", "cycle_year", "category", "slug"]
