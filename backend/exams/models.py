from datetime import timedelta
from zoneinfo import available_timezones

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from simple_history.models import HistoricalRecords


class MachineOverwriteBlocked(Exception):
    """Raised when a write would let machine-observed data overwrite a
    human verification that is still fresh. See CLAUDE.md - status has two
    independent sources and they are never collapsed."""


def validate_timezone(value: str) -> None:
    if value not in available_timezones():
        raise ValidationError(f"{value!r} is not a valid IANA timezone name.")


class Board(models.Model):
    """A conducting authority, e.g. UPSC, SSC."""

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20, unique=True)
    official_url = models.URLField()
    timezone = models.CharField(max_length=64, default="UTC", validators=[validate_timezone])
    active = models.BooleanField(default=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Exam(models.Model):
    """One cycle of an exam, e.g. "UPSC CSE 2026"."""

    board = models.ForeignKey(Board, on_delete=models.PROTECT, related_name="exams")
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    cycle_year = models.PositiveIntegerField()
    category = models.CharField(max_length=100)
    slug = models.SlugField(max_length=255, unique=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["board", "code", "cycle_year"],
                name="unique_exam_board_code_cycle_year",
            ),
        ]
        ordering = ["-cycle_year", "board__name", "code"]

    def __str__(self) -> str:
        return f"{self.board.code} {self.code} {self.cycle_year}"

    def _generate_unique_slug(self) -> str:
        base = slugify(f"{self.board.code}-{self.code}-{self.cycle_year}")
        slug = base
        counter = 2
        while Exam.objects.exclude(pk=self.pk).filter(slug=slug).exists():
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)


class ExamStage(models.Model):
    """A single stage of an exam, e.g. prelims, mains, interview.

    Most exams are multi-stage and stages progress independently -
    status tracking lives here, not on Exam (added in EXT-013).
    """

    class StageType(models.TextChoices):
        PRELIMS = "prelims", "Prelims"
        MAINS = "mains", "Mains"
        INTERVIEW = "interview", "Interview"
        SKILL = "skill", "Skill"
        SINGLE = "single", "Single"

    #: The public timeline's milestones, in the order a candidate meets
    #: them. Each maps to a nullable date field below; any of them may be
    #: unknown, which the UI renders as "date not announced" rather than
    #: hiding the step.
    TIMELINE_MILESTONES = (
        ("notification", "Notification"),
        ("admit_card", "Admit card"),
        ("exam", "Exam date"),
        ("answer_key", "Answer key"),
        ("result", "Result"),
    )

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="stages")
    stage_type = models.CharField(max_length=20, choices=StageType.choices)
    sequence = models.PositiveIntegerField()
    planned_start_date = models.DateField(null=True, blank=True)
    planned_end_date = models.DateField(null=True, blank=True)

    # Timeline milestones. Separate from planned_start/end_date, which stay
    # as the scheduling window the date-range filter (EXT-017) queries.
    notification_date = models.DateField(null=True, blank=True)
    admit_card_date = models.DateField(null=True, blank=True)
    exam_date = models.DateField(null=True, blank=True)
    answer_key_date = models.DateField(null=True, blank=True)
    result_date = models.DateField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["exam", "sequence"],
                name="unique_examstage_exam_sequence",
            ),
        ]
        ordering = ["exam", "sequence"]

    def __str__(self) -> str:
        return f"{self.exam} - {self.get_stage_type_display()}"


class StatusTrack(models.Model):
    """One of an ExamStage's three independent status tracks.

    Status has two independent sources: what the scraper observed, and
    what a human confirmed. They are never collapsed into one column -
    see the machine_* / human_* fields below.
    """

    #: effective_status uses the human value while verification is this
    #: fresh, otherwise it falls back to the machine value.
    STALENESS_WINDOW = timedelta(days=14)

    class Track(models.TextChoices):
        CONDUCT = "conduct", "Conduct"
        RESULT = "result", "Result"
        INTEGRITY = "integrity", "Integrity"

    exam_stage = models.ForeignKey(
        ExamStage, on_delete=models.CASCADE, related_name="status_tracks"
    )
    track = models.CharField(max_length=20, choices=Track.choices)

    machine_value = models.CharField(max_length=50, blank=True)
    machine_confidence = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    machine_seen_at = models.DateTimeField(null=True, blank=True)

    human_value = models.CharField(max_length=50, blank=True)
    # A plain identifier (e.g. email), not a User FK: auth/roles (EXT-020)
    # don't exist yet, and FKing to auth.User now would make swapping in a
    # custom user model later a painful AUTH_USER_MODEL migration.
    verified_by = models.CharField(max_length=255, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["exam_stage", "track"],
                name="unique_statustrack_exam_stage_track",
            ),
        ]
        ordering = ["exam_stage", "track"]

    def __str__(self) -> str:
        return f"{self.exam_stage} - {self.get_track_display()}"

    @property
    def is_verification_fresh(self) -> bool:
        """Whether the human verification still wins over the machine.

        Single definition of "fresh": effective_status and the
        machine-overwrite guard below both read it, so the two can never
        disagree about which values a scraper is allowed to touch.
        """
        return (
            self.verified_at is not None
            and timezone.now() - self.verified_at <= self.STALENESS_WINDOW
        )

    @property
    def effective_status(self) -> str:
        """The human value when verification is fresh (<= 14 days), else
        the machine value. A resolver, not a column - see CLAUDE.md."""
        return self.human_value if self.is_verification_fresh else self.machine_value

    def save(self, *args, **kwargs):
        """Backstop for CLAUDE.md's core rule: a scrape run that
        contradicts a fresh human value must not write.

        verification.observations.apply_machine_observation() is the
        intended path and records a conflict instead of reaching here.
        This guard exists so that "never overwrites" holds even for code
        that writes the model directly - a future scraper, a management
        command, or the admin - rather than only for callers who remember
        to use the helper.
        """
        if self.pk is not None:
            previous = StatusTrack.objects.filter(pk=self.pk).first()
            if previous is not None and previous.is_verification_fresh:
                machine_changed = (
                    self.machine_value != previous.machine_value
                    or self.machine_confidence != previous.machine_confidence
                    or self.machine_seen_at != previous.machine_seen_at
                )
                # Agreeing with the human value is not a contradiction, so a
                # scraper is still free to re-confirm what a verifier said.
                contradicts_human = self.machine_value != previous.human_value
                if machine_changed and contradicts_human:
                    raise MachineOverwriteBlocked(
                        f"Machine value {self.machine_value!r} contradicts the fresh "
                        f"human value {previous.human_value!r} on {previous}. "
                        "Use verification.observations.apply_machine_observation(), "
                        "which records a conflict instead of overwriting."
                    )
        super().save(*args, **kwargs)
