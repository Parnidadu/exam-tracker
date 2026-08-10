from django.apps import AppConfig


class OpsConfig(AppConfig):
    """Operational tooling: backups, and the commands an operator runs
    when something has gone wrong.

    No models. It exists because Django only discovers management commands
    inside an installed app, and `backup_database` has no business living
    in `exams` next to the domain.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "ops"
