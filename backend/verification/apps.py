from django.apps import AppConfig


class VerificationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "verification"

    def ready(self) -> None:
        """EXT-052: emit a StatusChange whenever a machine value moves."""
        from . import changes

        changes.connect()
