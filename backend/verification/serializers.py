from rest_framework import serializers

from exams.models import StatusTrack


class VerifyStageSerializer(serializers.Serializer):
    track = serializers.ChoiceField(choices=StatusTrack.Track.choices)
    value = serializers.CharField(max_length=50)
    evidence_url = serializers.URLField(required=False, allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)
