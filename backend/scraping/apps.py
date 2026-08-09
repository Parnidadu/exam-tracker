import pkgutil
from importlib import import_module

from django.apps import AppConfig


class ScrapingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "scraping"

    def ready(self) -> None:
        """Import every parser module so its @register call runs.

        Without this the registry is empty unless something happened to
        import the module first - a parser would exist in the tree and
        still be "not found" at runtime.
        """
        from . import board_parsers

        for module in pkgutil.iter_modules(board_parsers.__path__):
            import_module(f"{board_parsers.__name__}.{module.name}")

        # EXT-046: keep Beat's schedule in step with the Source table.
        from . import schedules

        schedules.connect()

        # EXT-047: give every source a health row from the moment it exists.
        from . import health

        health.connect()
