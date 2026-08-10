from croniter import croniter
from django.core.exceptions import ValidationError
from django.db import models
from simple_history.models import HistoricalRecords

from exams.models import Board, ExamStage

CRON_FIELDS = 5


def validate_cron(value: str) -> None:
    """Reject a cron expression that could never be scheduled.

    The point of this ticket is that schedules change from admin without a
    deploy - so a typo must fail loudly at save time rather than silently
    stopping a source from ever being fetched.

    Restricted to standard 5-field cron. croniter also accepts 6- and
    7-field forms (seconds, year), but the scheduler these feed - Celery
    Beat, in EXT-046 - does not. Accepting one here would save cleanly and
    then never fire, which is the exact failure this guard exists to stop.
    """
    if len(value.split()) != CRON_FIELDS:
        raise ValidationError(
            f"{value!r} must have exactly {CRON_FIELDS} fields "
            "(minute hour day-of-month month day-of-week)."
        )
    if not croniter.is_valid(value):
        raise ValidationError(f"{value!r} is not a valid cron expression.")


class Source(models.Model):
    """A scrape target: where to fetch, how to fetch it, how to parse it,
    and how often.

    Everything a scraper needs is stored here and editable in admin, so
    changing a board's URL or how often it is polled never requires a code
    deploy.
    """

    class FetchStrategy(models.TextChoices):
        #: Plain HTTP GET - correct for server-rendered notice boards.
        HTTP = "http", "HTTP GET"
        #: Render with a headless browser first, for pages that build their
        #: listing client-side.
        HEADLESS = "headless", "Headless browser"

    board = models.ForeignKey(Board, on_delete=models.PROTECT, related_name="sources")
    name = models.CharField(max_length=255, help_text="Human label, e.g. 'UPSC notices'.")
    url = models.URLField(max_length=500)
    fetch_strategy = models.CharField(
        max_length=20, choices=FetchStrategy.choices, default=FetchStrategy.HTTP
    )
    #: Free text, not choices: the parser registry lands in EXT-043, and
    #: pinning choices to an enum now would mean a deploy to add a parser -
    #: exactly what this ticket exists to avoid.
    parser_key = models.CharField(
        max_length=100, help_text="Key of the parser to use, e.g. 'upsc_notices'."
    )
    cron = models.CharField(
        max_length=100,
        default="0 * * * *",
        validators=[validate_cron],
        help_text="Standard 5-field cron, e.g. '0 */6 * * *' for every six hours.",
    )
    enabled = models.BooleanField(
        default=True, help_text="Uncheck to stop scraping without deleting the config."
    )

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["board", "parser_key", "url"],
                name="unique_source_board_parser_url",
            ),
        ]
        ordering = ["board__name", "name"]

    def __str__(self) -> str:
        return f"{self.board.code} - {self.name}"

    def clean(self) -> None:
        """Run the cron validator on full_clean() too.

        Field validators fire for admin and serializers, but not for a bare
        Source(...).save(); this keeps the check attached to the model's own
        validation entry point as well.
        """
        super().clean()
        if self.cron:
            validate_cron(self.cron)


class SourceHealth(models.Model):
    """Whether a source is actually working, as opposed to how it is
    configured.

    Deliberately not fields on `Source`. Source is human-edited config
    carrying HistoricalRecords, so writing scrape outcomes onto it would
    put a history row through the audit trail on every single run - and
    the audit trail exists to answer "who changed this config", a question
    a machine writing a timestamp every ten minutes makes unanswerable.
    Keeping the machine's observations in their own table is the same
    separation the status tracks make between machine and human values.
    """

    source = models.OneToOneField(Source, on_delete=models.CASCADE, related_name="health")

    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    #: Reset to zero by any success. A source failing twice a day for a
    #: week is a different problem from one that has failed 40 times in a
    #: row, and only this counter tells them apart.
    consecutive_failures = models.PositiveIntegerField(default=0)
    #: Why the most recent failure failed, kept so the admin dashboard can
    #: say what is wrong rather than only that something is.
    last_error = models.TextField(blank=True)
    #: Set on every run, success or failure. A source whose task is not
    #: running at all shows up as a stale last_checked_at even though
    #: neither of the two timestamps above has moved.
    last_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "source health"
        verbose_name_plural = "source health"
        ordering = ["-consecutive_failures", "source__name"]

    def __str__(self) -> str:
        return f"{self.source} health"

    @property
    def has_ever_succeeded(self) -> bool:
        return self.last_success_at is not None


class TriageItem(models.Model):
    """An observation the matcher would not link on its own.

    The observation itself is a plain value object (scraping.parsers), so
    its fields are copied in rather than referenced - by the time an
    operator opens the queue the parse that produced it is long gone, and
    a queue item that cannot show what it saw is not reviewable.

    One row per *distinct* unmatched observation, not per scrape run. The
    same unresolved notice reappears on every poll, and without the
    fingerprint below a board nobody has got round to configuring would
    bury the queue in copies of one problem within a day.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Needs triage"
        LINKED = "linked", "Linked to a stage"
        CREATED = "created", "New exam created"
        DISMISSED = "dismissed", "Dismissed"

    class Reason(models.TextChoices):
        """Mirrors scraping.matching.TriageReason. Stored rather than
        recomputed: the exam list changes, and an item should still say
        why it landed here when it did."""

        NO_CANDIDATES = "no_candidates", "No exam matched"
        BELOW_THRESHOLD = "below_threshold", "Best match scored too low"
        AMBIGUOUS = "ambiguous", "Two candidates scored too close"
        NO_STAGE = "no_stage", "Matched an exam but not a stage"

    source = models.ForeignKey(
        Source,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triage_items",
        help_text="Which source produced this. Kept if the source is later deleted.",
    )

    # --- what was observed --------------------------------------------
    exam_name = models.CharField(max_length=500)
    track = models.CharField(max_length=20)
    value = models.CharField(max_length=50)
    stage_hint = models.CharField(max_length=100, blank=True)
    observed_date = models.DateField(null=True, blank=True)
    #: The parser's confidence in the *reading*, not in the match.
    parser_confidence = models.FloatField(default=1.0)
    source_url = models.URLField(max_length=500, blank=True)
    raw_text = models.TextField(blank=True)

    # --- why it needs a human -----------------------------------------
    reason = models.CharField(max_length=30, choices=Reason.choices)
    #: The best score the matcher managed. Zero when nothing matched.
    match_confidence = models.FloatField(default=0.0)
    #: Candidates the matcher considered, best first, as
    #: [{"exam_stage_id": int, "label": str, "score": float}]. Stored so
    #: the operator is offered the same shortlist the matcher had, without
    #: re-running scoring against an exam list that has since changed.
    candidates = models.JSONField(default=list, blank=True)

    # --- resolution ----------------------------------------------------
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    exam_stage = models.ForeignKey(
        ExamStage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triage_items",
        help_text="Set once an operator links or creates.",
    )
    resolved_by = models.CharField(max_length=255, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)

    # --- recurrence -----------------------------------------------------
    #: sha256 over the source and the observation's identifying fields.
    fingerprint = models.CharField(max_length=64, unique=True, db_index=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    #: How many scrape runs have produced this same unmatched observation.
    #: A high count is a signal in itself - it means a real notice nobody
    #: has resolved, not a one-off oddity.
    times_seen = models.PositiveIntegerField(default=1)

    history = HistoricalRecords()

    class Meta:
        ordering = ["status", "-times_seen", "-last_seen_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-last_seen_at"]),
            models.Index(fields=["status", "reason"]),
        ]

    def __str__(self) -> str:
        return f"{self.exam_name} ({self.get_reason_display()})"

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING

    @property
    def is_resolved(self) -> bool:
        return not self.is_pending


class Snapshot(models.Model):
    """A distinct version of a source's raw HTML.

    One row per *distinct* body, not per fetch. A fetch that returns
    identical content bumps `last_seen_at` on the existing row instead of
    inserting a near-duplicate, which is what makes the store's size track
    how often a board actually changes rather than how often it is polled.
    """

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="snapshots")
    url = models.URLField(max_length=500)
    #: sha256 of the raw bytes, before any decoding - decoding first would
    #: let two different byte sequences collapse to the same "text".
    content_hash = models.CharField(max_length=64, db_index=True)
    content = models.TextField()
    status_code = models.PositiveSmallIntegerField()

    first_seen_at = models.DateTimeField(auto_now_add=True)
    #: Refreshed every time this same content comes back, so an unchanged
    #: page still records that the source was reachable.
    last_seen_at = models.DateTimeField(auto_now_add=True)
    #: How many fetches have returned this exact body.
    times_seen = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "content_hash"],
                name="unique_snapshot_source_content_hash",
            ),
        ]
        # -id breaks ties: two snapshots stored in the same instant would
        # otherwise order arbitrarily, making "the latest" nondeterministic.
        ordering = ["-last_seen_at", "-id"]
        indexes = [models.Index(fields=["source", "-last_seen_at"])]

    def __str__(self) -> str:
        return f"{self.source} @ {self.content_hash[:12]}"
