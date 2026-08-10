"""Run one source's scrape now, and say what happened.

Exists for the runbook. Diagnosing a broken source otherwise means
opening a Django shell and calling the task by hand, which is not a step
a second person can follow unaided - and the moment you need it is the
moment you least want to be composing Python.
"""

from django.core.management.base import BaseCommand, CommandError

from scraping.models import Source


class Command(BaseCommand):
    help = "Scrape one source immediately and report the outcome."

    def add_arguments(self, parser):
        parser.add_argument("source_id", nargs="?", type=int, help="Source ID. Omit to list them.")
        parser.add_argument(
            "--list", action="store_true", help="List sources with their health, and exit."
        )

    def handle(self, *args, **options):
        if options["list"] or options["source_id"] is None:
            self._list()
            return

        try:
            source = Source.objects.get(pk=options["source_id"])
        except Source.DoesNotExist as error:
            raise CommandError(
                f"No source with id {options['source_id']}. Run with --list to see them."
            ) from error

        self.stdout.write(f"Scraping {source} ({source.url}) ...")

        # Called directly rather than through .delay(): the operator wants
        # the answer here, not a task id and a trip to the worker logs.
        from scraping.tasks import scrape_source

        result = scrape_source(source.pk)

        for key, value in result.items():
            self.stdout.write(f"  {key:20} {value}")

        style = self.style.SUCCESS if result.get("status") == "ok" else self.style.ERROR
        self.stdout.write(style(f"status: {result.get('status')}"))

    def _list(self) -> None:
        sources = Source.objects.select_related("board").order_by("board__code", "name")
        if not sources:
            self.stdout.write("No sources configured.")
            return

        self.stdout.write(f"{'id':>4}  {'board':6} {'enabled':8} {'health':28} name")
        for source in sources:
            health = getattr(source, "health", None)
            if health is None:
                state = "no health record"
            elif health.consecutive_failures:
                state = f"failing x{health.consecutive_failures}"
            elif health.last_success_at:
                state = f"ok, last {health.last_success_at:%Y-%m-%d %H:%M}"
            else:
                state = "never run"
            enabled = "yes" if source.enabled else "PAUSED"
            self.stdout.write(
                f"{source.pk:>4}  {source.board.code:6} {enabled:8} "
                f"{state:28} {source.name}"
            )
