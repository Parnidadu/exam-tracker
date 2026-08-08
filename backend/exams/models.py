from zoneinfo import available_timezones

from django.core.exceptions import ValidationError
from django.db import models


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
