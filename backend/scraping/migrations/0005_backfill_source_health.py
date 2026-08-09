"""EXT-047: give every existing Source a health row.

New sources get one from a post_save signal, but sources created before
this ticket have none - so without a backfill the health dashboard would
list only the sources that happened to run after deploy, and a board that
had already gone quiet would be missing from exactly the page built to
find it.

Rows are created empty. Claiming a source had succeeded at migration time
would be inventing an observation, and the dashboard reads "not yet run"
until a real scrape says otherwise.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    Source = apps.get_model("scraping", "Source")
    SourceHealth = apps.get_model("scraping", "SourceHealth")

    existing = set(SourceHealth.objects.values_list("source_id", flat=True))
    SourceHealth.objects.bulk_create(
        [
            SourceHealth(source_id=pk)
            for pk in Source.objects.values_list("pk", flat=True)
            if pk not in existing
        ]
    )


def unbackfill(apps, schema_editor):
    # The rows only exist because of this migration; reversing 0004 drops
    # the table anyway, so this just keeps the pair symmetrical.
    SourceHealth = apps.get_model("scraping", "SourceHealth")
    SourceHealth.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("scraping", "0004_sourcehealth")]

    operations = [migrations.RunPython(backfill, unbackfill)]
