from django.core.management.base import BaseCommand

from ops.backups import create_backup


class Command(BaseCommand):
    help = "Dump the database to BACKUP_DIR and prune old backups."

    def handle(self, *args, **options):
        result = create_backup()
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {result.path} ({result.size_bytes:,} bytes); pruned {result.pruned}."
            )
        )
