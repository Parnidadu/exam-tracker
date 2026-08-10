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


class StatusChange(models.Model):
    """One machine-observed status transition on one track.

    Emitted when `machine_value` actually *changes*, not every time a
    scraper confirms it. Most polls re-observe the same value; a change
    log that recorded those would grow by a row per source per run and
    tell a verifier nothing about what moved.

    Distinct from MachineObservationConflict above, which records a write
    that was *refused*. This records one that happened.
    """

    status_track = models.ForeignKey(
        StatusTrack, on_delete=models.CASCADE, related_name="changes"
    )

    #: Blank when the machine had never observed this track before - which
    #: is itself a change worth reviewing, since a status appeared where
    #: there was none.
    previous_value = models.CharField(max_length=50, blank=True)
    new_value = models.CharField(max_length=50, blank=True)
    previous_confidence = models.FloatField(null=True, blank=True)
    new_confidence = models.FloatField(null=True, blank=True)

    #: When the scraper saw it, as opposed to when this row was written.
    observed_at = models.DateTimeField(null=True, blank=True)
    detected_at = models.DateTimeField(auto_now_add=True)

    #: Celery id of the follow-up task, kept so a change can be traced to
    #: the work it kicked off rather than only asserted to have done so.
    verification_task_id = models.CharField(max_length=255, blank=True)

    #: Filled in by that task. Null means it has not run yet, which is a
    #: different thing from "ran and found nothing to verify".
    needs_verification = models.BooleanField(null=True, blank=True)
    queue_reason = models.CharField(max_length=40, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    objects: models.Manager["StatusChange"]

    class Meta:
        ordering = ["-detected_at", "-id"]
        indexes = [
            models.Index(fields=["-detected_at"]),
            models.Index(fields=["needs_verification", "-detected_at"]),
        ]

    def __str__(self) -> str:
        before = self.previous_value or "(none)"
        return f"{self.status_track}: {before} -> {self.new_value or '(none)'}"


class ElapsedDateAlert(models.Model):
    """Records that a stage's planned date passed with nothing recorded.

    Exists so the alert fires **once per stage**. A daily job with no
    memory would re-alert every morning for as long as the stage stayed
    unattended - which is precisely the stage nobody is attending to, so
    the repeat lands on the same unread pile every day and the whole
    signal is lost.

    OneToOne rather than a flag on ExamStage: "once" is then a database
    constraint rather than a promise made by the one code path that
    happens to check first.
    """

    exam_stage = models.OneToOneField(
        ExamStage, on_delete=models.CASCADE, related_name="elapsed_date_alert"
    )
    #: The date that had passed when this fired. Kept for context - the
    #: stage's planned date may since have moved.
    planned_date = models.DateField()
    sent_at = models.DateTimeField(auto_now_add=True)
    #: How many people were told. Zero is worth recording: it means the
    #: alert condition was reached with nobody configured to hear it.
    notified = models.PositiveIntegerField(default=0)

    objects: models.Manager["ElapsedDateAlert"]

    class Meta:
        ordering = ["-sent_at", "-id"]

    def __str__(self) -> str:
        return f"{self.exam_stage} - planned {self.planned_date}, no update"
