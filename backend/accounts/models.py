from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from simple_history.models import HistoricalRecords


class Role(models.TextChoices):
    ADMIN = "admin", "Admin"
    VERIFIER = "verifier", "Verifier"
    VIEWER = "viewer", "Viewer"


class UserManager(BaseUserManager):
    """Mirrors Django's default UserManager, but keyed on email instead
    of username - this project has no username field."""

    def _create_user(self, email: str | None, password: str | None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)  # type: ignore[attr-defined]
        user.save(using=self._db)
        return user

    def create_user(self, email: str | None = None, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(
        self, email: str | None = None, password: str | None = None, **extra_fields
    ):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", Role.ADMIN)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Auth is by email, not username - there is no username field."""

    username = None  # type: ignore[assignment]
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []  # type: ignore[misc]

    # password is excluded deliberately: copying hashes into a second,
    # rarely-purged table widens where they live for no audit benefit -
    # "the password changed" is already evident from the history row.
    history = HistoricalRecords(excluded_fields=["password"])

    objects = UserManager()  # type: ignore[misc,assignment]

    def __str__(self) -> str:
        return self.email
