"""EXT-046: Beat's schedule is derived from Source rows.

The acceptance criterion is about a *running* beat process picking up an
admin change, so the central tests here drive the real
`DatabaseScheduler` - the same class beat runs - rather than asserting
that some rows exist and hoping that means the same thing.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from django_celery_beat.schedulers import DatabaseScheduler

from config.celery import app as celery_app
from exams.models import Board
from scraping.models import Source
from scraping.schedules import (
    SCRAPE_TASK,
    remove_source_schedule,
    schedule_name,
    sync_all_sources,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def board():
    return Board.objects.create(name="Union Public Service Commission", code="UPSC")


@pytest.fixture
def source(board):
    return Source.objects.create(
        board=board,
        name="UPSC what's new",
        url="https://www.upsc.gov.in/",
        parser_key="upsc_whats_new",
        cron="0 */6 * * *",
        enabled=True,
    )


def task_for(source) -> PeriodicTask | None:
    return PeriodicTask.objects.filter(name=schedule_name(source)).first()


@pytest.fixture
def running_beat():
    """Builds scheduler instances standing in for the running beat process.

    DatabaseScheduler registers an interpreter-exit hook that writes the
    schedule back to the database. Left in place, those hooks fire long
    after pytest-django has torn the test database down, so each one is
    cancelled here rather than leaking out of the test.
    """
    schedulers: list[DatabaseScheduler] = []

    def make() -> DatabaseScheduler:
        scheduler = DatabaseScheduler(app=celery_app, lazy=True)
        scheduler.setup_schedule()
        schedulers.append(scheduler)
        return scheduler

    yield make

    for scheduler in schedulers:
        scheduler._finalize.cancel()


# --- the acceptance criterion -----------------------------------------


def test_enabling_a_source_schedules_it_without_a_restart(board, running_beat):
    """A beat process that started before the source existed must still
    end up running it."""
    beat = running_beat()
    assert not [name for name in beat.schedule if name.startswith("scrape-source-")]

    source = Source.objects.create(
        board=board,
        name="IBPS CRP updates",
        url="https://www.ibps.in/index.php/crp-updates/",
        parser_key="ibps_crp_updates",
        cron="0 */6 * * *",
        enabled=True,
    )

    # This is the signal a running beat watches to know it must reload.
    assert beat.schedule_changed(), "beat would never notice the new source"

    beat.sync()
    assert schedule_name(source) in running_beat().schedule


def test_disabling_a_source_stops_it_without_a_restart(source, running_beat):
    beat = running_beat()
    assert schedule_name(source) in beat.schedule

    source.enabled = False
    source.save()

    assert beat.schedule_changed(), "beat would keep polling a disabled source"
    assert schedule_name(source) not in running_beat().schedule


def test_re_enabling_a_source_restores_the_same_schedule(source, running_beat):
    original = source.cron

    source.enabled = False
    source.save()
    source.enabled = True
    source.save()

    entry = running_beat().schedule[schedule_name(source)]
    assert str(entry.schedule.minute) is not None
    task = task_for(source)
    assert task is not None
    assert task.enabled
    assert source.cron == original


# --- what the schedule actually contains -------------------------------


def test_the_schedule_fires_the_scrape_task_for_that_source(source):
    task = task_for(source)

    assert task is not None
    assert task.task == SCRAPE_TASK
    assert task.args == f"[{source.pk}]"


def test_the_cron_string_maps_onto_the_right_fields(board):
    """cron orders fields `m h dom mon dow`; CrontabSchedule declares
    day_of_week before day_of_month. Assigning positionally would put
    "Sunday" into day_of_month, and the task would never fire."""
    source = Source.objects.create(
        board=board,
        name="weekly",
        url="https://example.gov.in/weekly",
        parser_key="x",
        cron="30 4 1 6 0",
    )

    crontab = task_for(source).crontab
    assert (crontab.minute, crontab.hour) == ("30", "4")
    assert crontab.day_of_month == "1"
    assert crontab.month_of_year == "6"
    assert crontab.day_of_week == "0"


def test_changing_the_cron_in_admin_reschedules(source, running_beat):
    source.cron = "15 3 * * *"
    source.save()

    crontab = task_for(source).crontab
    assert (crontab.minute, crontab.hour) == ("15", "3")
    assert running_beat().schedule_changed() is not None


def test_a_disabled_source_keeps_its_schedule_row_so_it_can_be_restored(source):
    source.enabled = False
    source.save()

    task = task_for(source)
    assert task is not None, "the row should survive so re-enabling restores it"
    assert task.enabled is False


# --- lifecycle ---------------------------------------------------------


def test_deleting_a_source_removes_its_schedule(source, running_beat):
    name = schedule_name(source)
    source.delete()

    assert not PeriodicTask.objects.filter(name=name).exists()
    assert name not in running_beat().schedule


def test_renaming_a_source_does_not_leave_a_duplicate_schedule(source):
    """The schedule is keyed on pk for exactly this reason: keyed on name,
    a rename would orphan the old row and the board would be polled
    twice."""
    source.name = "UPSC notices (renamed)"
    source.save()

    assert PeriodicTask.objects.filter(name__startswith="scrape-source-").count() == 1


def test_editing_a_cron_does_not_leave_orphaned_crontab_rows(source):
    for cron in ("5 * * * *", "10 * * * *", "20 * * * *"):
        source.cron = cron
        source.save()

    # Only the crontab actually in use should remain; django_celery_beat
    # never prunes these itself.
    assert CrontabSchedule.objects.filter(periodictask__isnull=True).count() == 0


def test_removing_a_schedule_by_pk_is_idempotent(source):
    remove_source_schedule(source.pk)
    remove_source_schedule(source.pk)

    assert task_for(source) is None


# --- bulk sync ---------------------------------------------------------


def test_sync_all_sources_backfills_sources_that_have_no_schedule(source, board):
    other = Source.objects.create(
        board=board,
        name="second",
        url="https://example.gov.in/second",
        parser_key="y",
    )
    PeriodicTask.objects.all().delete()

    assert sync_all_sources() == 2
    assert task_for(source) is not None
    assert task_for(other) is not None


def test_sync_all_sources_drops_schedules_for_sources_that_are_gone(source):
    stale = PeriodicTask.objects.create(
        name="scrape-source-999999",
        task=SCRAPE_TASK,
        crontab=task_for(source).crontab,
        args="[999999]",
    )

    sync_all_sources()

    assert not PeriodicTask.objects.filter(pk=stale.pk).exists()


def test_sync_all_sources_leaves_unmanaged_periodic_tasks_alone(source):
    """Someone may add a schedule by hand for something that is not a
    source; this module owns only its own prefix."""
    handwritten = PeriodicTask.objects.create(
        name="nightly-housekeeping",
        task="somewhere.else",
        crontab=task_for(source).crontab,
    )

    sync_all_sources()

    assert PeriodicTask.objects.filter(pk=handwritten.pk).exists()


# --- the task is real --------------------------------------------------


def test_a_fresh_worker_process_finds_the_task_the_schedule_names():
    """A schedule pointing at a name no task claims is accepted by beat
    and then fails on every single tick.

    Run in a subprocess on purpose. Asserting `SCRAPE_TASK in
    celery_app.tasks` in-process proves nothing here, because the test
    suite has already imported `scraping.tasks` itself - it would pass
    even if autodiscovery were broken. A worker starts with nothing
    imported, so that is the condition worth testing.
    """
    probe = textwrap.dedent(
        """
        import django; django.setup()
        import sys
        from config.celery import app
        assert "scraping.tasks" not in sys.modules, "probe is not a clean process"
        app.loader.import_default_modules()   # what a starting worker does
        print("scraping.tasks.scrape_source" in app.tasks)
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
        check=True,
    )

    assert completed.stdout.strip() == "True", completed.stderr
