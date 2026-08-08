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


class MachineObservationConflict(models.Model):
    """A machine observation refused because it contradicted a still-fresh
    human verification.

    Raised instead of writing. The observation's own values are kept here
    so nothing the scraper claimed is lost, and a verifier can compare it
    against the human value it disagreed with.
    """

    exam_stage = models.ForeignKey(
        ExamStage, on_delete=models.PROTECT, related_name="machine_conflicts"
    )
    track = models.CharField(max_length=20, choices=StatusTrack.Track.choices)

    # What the machine claimed, and was not allowed to write.
    machine_value = models.CharField(max_length=50)
    machine_confidence = models.FloatField(null=True, blank=True)
    machine_seen_at = models.DateTimeField(null=True, blank=True)

    # The fresh human verification it contradicted, captured as it stood at
    # the moment of the conflict.
    human_value = models.CharField(max_length=50)
    verified_by = models.CharField(max_length=255, blank=True)
    verified_at = models.DateTimeField()

    detected_at = models.DateTimeField(auto_now_add=True)

    # Annotation only, no assignment: Django still installs the default
    # manager at runtime, but django-stubs doesn't synthesise `objects` for
    # this model on its own (it does for models that declare one, like
    # VerificationRecord above).
    objects: models.Manager["MachineObservationConflict"]

    class Meta:
        ordering = ["-detected_at"]

    def __str__(self) -> str:
        return (
            f"{self.exam_stage} - {self.track}: machine {self.machine_value!r} "
            f"vs human {self.human_value!r}"
        )
