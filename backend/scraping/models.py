from croniter import croniter
from django.core.exceptions import ValidationError
from django.db import models
from simple_history.models import HistoricalRecords

from exams.models import Board

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
