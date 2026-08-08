from zoneinfo import available_timezones

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify


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

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="stages")
    stage_type = models.CharField(max_length=20, choices=StageType.choices)
    sequence = models.PositiveIntegerField()
    planned_start_date = models.DateField(null=True, blank=True)
    planned_end_date = models.DateField(null=True, blank=True)

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
