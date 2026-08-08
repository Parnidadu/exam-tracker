from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response

from exams.models import ExamStage, StatusTrack
from exams.serializers import StatusTrackSerializer

from .models import VerificationRecord
from .serializers import VerifyStageSerializer


@extend_schema(request=VerifyStageSerializer, responses=StatusTrackSerializer)
class VerifyStageView(generics.GenericAPIView):
    """POST /api/stages/<id>/verify/ - records a human verification for one
    of a stage's tracks and updates that track's current human_value.
    Writing the VerificationRecord and updating StatusTrack happen in one
    transaction: either both land or neither does.

    Permission is inherited from the project-wide default
    (IsVerifierOrAdminOrReadOnly, EXT-020): POST isn't a safe method, so
    viewers get 403 and anonymous requests get 401 without any extra code
    here.
    """

    queryset = ExamStage.objects.all()
    serializer_class = VerifyStageSerializer

    def post(self, request, *args, **kwargs):
        exam_stage = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            record = VerificationRecord.objects.create(
                exam_stage=exam_stage,
                track=data["track"],
                value=data["value"],
                evidence_url=data.get("evidence_url", ""),
                note=data.get("note", ""),
                actor=request.user,
            )
            status_track, _ = StatusTrack.objects.get_or_create(
                exam_stage=exam_stage, track=data["track"]
            )
            status_track.human_value = data["value"]
            status_track.verified_by = request.user.email
            status_track.verified_at = record.timestamp
            status_track.save()

        return Response(StatusTrackSerializer(status_track).data, status=status.HTTP_201_CREATED)
