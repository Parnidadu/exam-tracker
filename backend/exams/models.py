from datetime import timedelta
from zoneinfo import available_timezones

from django.conf import settings
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


class InvalidDiscrepancyTransition(Exception):
    """Raised when a discrepancy is moved to a state it cannot reach from
    where it is. See Discrepancy.TRANSITIONS."""


class Discrepancy(models.Model):
    """Something that went wrong with a stage: it was postponed, cancelled,
    a paper leaked, an answer key was wrong, it has to be re-run, or a
    court has stayed it.

    Human-entered throughout. A scraper may hint that something happened,
    but "this exam's paper leaked" is a claim with consequences for real
    candidates, and it is not one this system makes on its own.

    Attached to an ExamStage rather than an Exam because that is where the
    domain puts everything else that can go wrong - stages progress
    independently, and a leak in prelims says nothing about mains.
    """

    class Type(models.TextChoices):
        POSTPONEMENT = "postponement", "Postponement"
        CANCELLATION = "cancellation", "Cancellation"
        PAPER_LEAK = "paper_leak", "Paper leak"
        KEY_ERROR = "key_error", "Answer key error"
        RE_EXAM = "re_exam", "Re-examination"
        COURT_STAY = "court_stay", "Court stay"
        #: Deliberate escape hatch. Without it, anything the list does not
        #: name has to be filed under a type it is not - a centre change
        #: recorded as a "postponement" is worse than one recorded as
        #: "other", because the first is wrong where the second is only
        #: vague. `description` is required, so an "other" still says what
        #: happened.
        OTHER = "other", "Other"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        """The lifecycle. Reported is where everything starts; confirmed
        means someone checked the evidence; resolved means the situation
        has run its course. Dismissed is for a claim that turned out not
        to be one."""

        REPORTED = "reported", "Reported"
        CONFIRMED = "confirmed", "Confirmed"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    #: What each state may become. Resolving straight from `reported` is
    #: deliberately absent: it would close something nobody ever checked,
    #: and "resolved" would stop meaning that the claim was real.
    TRANSITIONS: dict[str, set[str]] = {
        Status.REPORTED: {Status.CONFIRMED, Status.DISMISSED},
        Status.CONFIRMED: {Status.RESOLVED, Status.DISMISSED},
        Status.RESOLVED: set(),
        Status.DISMISSED: set(),
    }

    #: States that end the lifecycle.
    TERMINAL = {Status.RESOLVED, Status.DISMISSED}

    #: The only states a member of the public may see (EXT-055).
    #:
    #: `reported` is excluded because it is an unchecked claim - naming a
    #: real board and a real exam, and possibly wrong. `dismissed` is
    #: excluded because it is a claim someone checked and found *not* to
    #: be true, and republishing "we looked into the leak allegation" is
    #: how a rumour outlives its own correction.
    #:
    #: Defined here rather than in the feed's queryset so there is exactly
    #: one answer to "is this public", wherever it is asked from.
    PUBLIC = {Status.CONFIRMED, Status.RESOLVED}

    exam_stage = models.ForeignKey(
        ExamStage, on_delete=models.PROTECT, related_name="discrepancies"
    )
    discrepancy_type = models.CharField(max_length=20, choices=Type.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.REPORTED)

    #: What happened, in words. Required: a type and a severity alone tell
    #: a candidate nothing about their exam.
    description = models.TextField()

    # --- evidence ------------------------------------------------------
    #: The official notice, court order or press report this rests on. A
    #: discrepancy without a source a reader can check is a rumour, and
    #: this app exists to be the opposite of that.
    evidence_url = models.URLField(max_length=500, blank=True)
    evidence_note = models.TextField(blank=True)

    #: When it actually happened, as opposed to when someone recorded it.
    #: Nullable because a leak often surfaces long after the fact and the
    #: date may genuinely not be known yet.
    occurred_on = models.DateField(null=True, blank=True)

    # --- lifecycle -----------------------------------------------------
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reported_discrepancies",
    )
    reported_at = models.DateTimeField(auto_now_add=True)

    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="resolved_discrepancies",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    #: Why it was resolved or dismissed. Required for both: a dismissed
    #: leak claim with no explanation is indistinguishable from one nobody
    #: bothered to look at.
    resolution_note = models.TextField(blank=True)

    history = HistoricalRecords()

    objects: models.Manager["Discrepancy"]

    class Meta:
        verbose_name_plural = "discrepancies"
        ordering = ["-reported_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-reported_at"]),
            models.Index(fields=["exam_stage", "status"]),
            models.Index(fields=["discrepancy_type", "-reported_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.exam_stage} - {self.get_discrepancy_type_display()} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status not in self.TERMINAL

    def can_become(self, new_status: str) -> bool:
        return new_status in self.TRANSITIONS.get(self.status, set())

    def clean(self) -> None:
        super().clean()
        if self.status in self.TERMINAL and not self.resolution_note.strip():
            raise ValidationError(
                {"resolution_note": "Say why this was resolved or dismissed."}
            )

    def save(self, *args, **kwargs):
        """Guards the lifecycle wherever the write comes from.

        Same reasoning as StatusTrack's own save-time backstop: the API
        (EXT-054) will be the intended path, but a management command or
        the admin writing a nonsense transition would corrupt the one
        thing this model exists to record. Checking here means "resolved
        implies someone confirmed it" holds for every caller.
        """
        if self.pk is not None:
            previous = (
                Discrepancy.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            )
            if previous is not None and previous != self.status:
                # .value, not the member: interpolating the enum renders
                # "Discrepancy.Status.CONFIRMED" into a message whose whole
                # job is to tell someone what to send instead.
                allowed = sorted(str(s) for s in self.TRANSITIONS.get(previous, set()))
                if self.status not in self.TRANSITIONS.get(previous, set()):
                    raise InvalidDiscrepancyTransition(
                        f"A {previous} discrepancy cannot become {self.status}. "
                        f"Allowed from {previous}: "
                        f"{allowed or 'nothing - it is closed'}."
                    )
                # Stamping this here rather than trusting callers keeps
                # "closed" and "has a closing time" from drifting apart.
                if self.status in self.TERMINAL and self.resolved_at is None:
                    self.resolved_at = timezone.now()

        super().save(*args, **kwargs)
