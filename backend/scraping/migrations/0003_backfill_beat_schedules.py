"""EXT-046: give every existing Source a Beat schedule.

Sources created before this ticket have no PeriodicTask, so without a
backfill they would stay unscheduled until someone happened to re-save
them in admin - a source silently never being polled, which is the worst
possible failure for this app.

Deliberately not written with `scraping.schedules.sync_all_sources()`:
migrations must keep working against the historical model state, and a
helper that imports the live models would break the first time Source
gains or loses a field.
"""

from django.db import migrations

NAME_PREFIX = "scrape-source-"
SCRAPE_TASK = "scraping.tasks.scrape_source"


def backfill(apps, schema_editor):
    Source = apps.get_model("scraping", "Source")
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    for source in Source.objects.all():
        minute, hour, day_of_month, month_of_year, day_of_week = source.cron.split()
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            day_of_week=day_of_week,
        )
        PeriodicTask.objects.update_or_create(
            name=f"{NAME_PREFIX}{source.pk}",
            defaults={
                "task": SCRAPE_TASK,
                "crontab": schedule,
                "args": f"[{source.pk}]",
                "enabled": source.enabled,
                "description": f"Scrape {source.name} ({source.url})",
            },
        )


def unbackfill(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name__startswith=NAME_PREFIX).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("scraping", "0002_snapshot_and_more"),
        # The schedule tables have to exist before rows can go into them.
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(backfill, unbackfill)]
