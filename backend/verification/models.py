from django.conf import settings
from django.db import models

from exams.models import ExamStage, StatusTrack


class VerificationRecordQuerySet(models.QuerySet):
    """Blocks the bulk-operation escape hatch: QuerySet.update()/delete()
    bypass Model.save()/delete(), so those need blocking separately."""

    def update(self, *args, **kwargs):
        raise TypeError("VerificationRecord is append-only and cannot be updated.")

    def delete(self, *args, **kwargs):
        raise TypeError("VerificationRecord is append-only and cannot be deleted.")


class VerificationRecordManager(
    models.Manager.from_queryset(VerificationRecordQuerySet)  # type: ignore[misc]
):
    pass


class VerificationRecord(models.Model):
    """A single append-only verification event. Never updated or deleted
    once created - this is the audit trail itself (EXT-025 reads it),
    not a mutable record of "current" state (that's StatusTrack)."""

    exam_stage = models.ForeignKey(
        ExamStage, on_delete=models.PROTECT, related_name="verification_records"
    )
    track = models.CharField(max_length=20, choices=StatusTrack.Track.choices)
    value = models.CharField(max_length=50)
    evidence_url = models.URLField(blank=True)
    note = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="verification_records"
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    objects = VerificationRecordManager()

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self) -> str:
        return f"{self.exam_stage} - {self.track} - {self.value} ({self.timestamp})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise TypeError("VerificationRecord is append-only and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError("VerificationRecord is append-only and cannot be deleted.")
