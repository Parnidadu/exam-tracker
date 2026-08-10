from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ops.backups import BackupError, latest_backup, restore


class Command(BaseCommand):
    help = (
        "Restore a dump into a scratch database. This is the command a "
        "restore rehearsal runs, so that the rehearsed procedure and the "
        "real one are the same procedure."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--into",
            required=True,
            help="Database to restore into. Dropped and recreated first.",
        )
        parser.add_argument(
            "--dump",
            help="Dump file to restore. Defaults to the most recent backup.",
        )
        parser.add_argument(
            "--allow-live",
            action="store_true",
            help=(
                "Permit restoring over the configured application database. "
                "Required for a real recovery; refused by default so a "
                "rehearsal cannot become the disaster."
            ),
        )

    def handle(self, *args, **options):
        dump = Path(options["dump"]) if options["dump"] else latest_backup()
        if dump is None:
            raise CommandError("No backups found, and no --dump given.")

        try:
            restore(dump, options["into"], allow_live=options["allow_live"])
        except BackupError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(self.style.SUCCESS(f"Restored {dump} into {options['into']}."))
