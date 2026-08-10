"""EXT-063: nightly backups, and a restore that can be rehearsed safely.

The dump and restore themselves are exercised for real against Postgres
in the rehearsal (docs/backups.md); these cover the logic around them -
what gets pruned, what is refused, and how the tools are invoked.
"""

from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from ops import backups
from ops.backups import BackupError, create_backup, existing_backups, prune, restore


@pytest.fixture
def backup_dir(tmp_path, settings):
    settings.BACKUP_DIR = str(tmp_path)
    settings.BACKUP_RETENTION_DAYS = 14
    return tmp_path


def write_backup(directory: Path, name: str, age_days: int = 0, size: int = 100) -> Path:
    path = directory / name
    path.write_bytes(b"x" * size)
    if age_days:
        old = (timezone.now() - timedelta(days=age_days)).timestamp()
        import os

        os.utime(path, (old, old))
    return path


@pytest.fixture
def fake_pg(monkeypatch):
    """Records what would have been run, instead of running it."""
    calls: list[list[str]] = []
    envs: list[dict] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, env=None, **kwargs):
        calls.append(command)
        envs.append(env or {})
        # pg_dump writes to --file; stand in for that so the size check
        # downstream sees a plausible dump.
        for argument in command:
            if argument.startswith("--file="):
                Path(argument.removeprefix("--file=")).write_bytes(b"PGDMP" + b"\0" * 512)
        return Result()

    monkeypatch.setattr(backups.subprocess, "run", fake_run)
    return calls, envs


# --- taking a backup ----------------------------------------------------


def test_a_backup_is_written_with_a_sortable_timestamped_name(backup_dir, fake_pg):
    result = create_backup()

    assert result.path.parent == backup_dir
    assert result.path.name.startswith("examtracker-")
    assert result.path.suffix == ".dump"
    assert result.size_bytes > 0


def test_two_backups_on_the_same_day_do_not_overwrite_each_other(backup_dir, fake_pg):
    now = timezone.now()
    create_backup(now=now)
    create_backup(now=now + timedelta(seconds=1))

    assert len(existing_backups()) == 2


def test_the_backup_directory_is_created_if_missing(tmp_path, settings, fake_pg):
    settings.BACKUP_DIR = str(tmp_path / "not" / "there" / "yet")
    settings.BACKUP_RETENTION_DAYS = 14

    create_backup()

    assert Path(settings.BACKUP_DIR).is_dir()


def test_the_dump_is_taken_in_a_restorable_format(backup_dir, fake_pg):
    calls, _ = fake_pg
    create_backup()

    command = calls[0]
    assert command[0] == "pg_dump"
    # Custom format: compressed, and restorable selectively when what you
    # need back is one table rather than the whole database.
    assert "--format=custom" in command


def test_the_password_never_appears_on_the_command_line(backup_dir, fake_pg, settings):
    """argv is readable by any other process on the box via ps."""
    calls, envs = fake_pg
    settings.DATABASES["default"]["PASSWORD"] = "hunter2-not-in-argv"

    create_backup()

    assert not any("hunter2-not-in-argv" in argument for argument in calls[0])
    assert envs[0]["PGPASSWORD"] == "hunter2-not-in-argv"


def test_a_failing_pg_dump_is_an_error_not_a_silent_success(backup_dir, monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "could not connect to server"

    monkeypatch.setattr(backups.subprocess, "run", lambda *a, **k: Failed())

    with pytest.raises(BackupError, match="could not connect"):
        create_backup()


def test_a_dump_that_wrote_nothing_is_rejected(backup_dir, monkeypatch):
    """pg_dump can exit 0 having written nothing. An empty file that looks
    like a backup is worse than no file, because it is only discovered
    during a restore."""

    class Empty:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        for argument in command:
            if argument.startswith("--file="):
                Path(argument.removeprefix("--file=")).write_bytes(b"")
        return Empty()

    monkeypatch.setattr(backups.subprocess, "run", fake_run)

    with pytest.raises(BackupError, match="no data"):
        create_backup()


# --- retention ----------------------------------------------------------


def test_backups_past_the_retention_window_are_pruned(backup_dir):
    write_backup(backup_dir, "examtracker-20260801-020000.dump", age_days=0)
    write_backup(backup_dir, "examtracker-20260701-020000.dump", age_days=40)
    write_backup(backup_dir, "examtracker-20260615-020000.dump", age_days=60)

    removed = prune()

    assert removed == 2
    assert len(existing_backups()) == 1


def test_recent_backups_are_kept(backup_dir):
    write_backup(backup_dir, "examtracker-20260810-020000.dump", age_days=0)
    write_backup(backup_dir, "examtracker-20260809-020000.dump", age_days=1)

    assert prune() == 0
    assert len(existing_backups()) == 2


def test_the_most_recent_backup_is_never_pruned_however_old(backup_dir):
    """A backup job that stopped running a month ago must not also destroy
    the last copy it made - that turns one failure into two, and the
    second is unrecoverable."""
    write_backup(backup_dir, "examtracker-20250101-020000.dump", age_days=400)

    assert prune() == 0
    assert len(existing_backups()) == 1


def test_pruning_leaves_unrelated_files_alone(backup_dir):
    write_backup(backup_dir, "examtracker-20260101-020000.dump", age_days=300)
    write_backup(backup_dir, "examtracker-20250101-020000.dump", age_days=400)
    stranger = backup_dir / "notes.txt"
    stranger.write_text("someone put this here")

    prune()

    assert stranger.exists()


def test_backups_are_listed_newest_first(backup_dir):
    write_backup(backup_dir, "examtracker-20260101-020000.dump")
    write_backup(backup_dir, "examtracker-20260810-020000.dump")

    assert existing_backups()[0].name == "examtracker-20260810-020000.dump"


def test_an_absent_backup_directory_is_not_an_error(tmp_path, settings):
    settings.BACKUP_DIR = str(tmp_path / "nothing here")

    assert existing_backups() == []
    assert backups.latest_backup() is None


# --- restoring ----------------------------------------------------------


def test_restoring_over_the_live_database_is_refused(backup_dir, fake_pg, settings):
    """Rehearsing a restore is only safe if the rehearsal cannot become
    the disaster."""
    dump = write_backup(backup_dir, "examtracker-20260810-020000.dump")
    live = settings.DATABASES["default"]["NAME"]

    with pytest.raises(BackupError, match="live database"):
        restore(dump, into=live)


def test_the_live_database_can_still_be_restored_deliberately(backup_dir, fake_pg, settings):
    """Refusing by default must not mean a real recovery is impossible."""
    dump = write_backup(backup_dir, "examtracker-20260810-020000.dump")

    restore(dump, into=settings.DATABASES["default"]["NAME"], allow_live=True)

    calls, _ = fake_pg
    assert any(command[0] == "pg_restore" for command in calls)


def test_a_missing_dump_is_reported_rather_than_half_restored(backup_dir, fake_pg):
    with pytest.raises(BackupError, match="no such dump"):
        restore(backup_dir / "not-there.dump", into="scratch")


def test_the_target_is_dropped_and_recreated_before_restoring(backup_dir, fake_pg):
    """Restoring into a database that still holds rows leaves a mixture of
    old and new, which is the hardest state to reason about afterwards."""
    dump = write_backup(backup_dir, "examtracker-20260810-020000.dump")

    restore(dump, into="scratch")

    calls, _ = fake_pg
    statements = " ".join(argument for command in calls for argument in command)
    assert "DROP DATABASE IF EXISTS" in statements
    assert "CREATE DATABASE" in statements
    assert calls[-1][0] == "pg_restore"


# --- the commands an operator actually runs -----------------------------


def test_the_backup_command_reports_what_it_wrote(backup_dir, fake_pg, capsys):
    call_command("backup_database")

    assert "Wrote" in capsys.readouterr().out


def test_the_restore_command_defaults_to_the_latest_backup(backup_dir, fake_pg, capsys):
    write_backup(backup_dir, "examtracker-20260101-020000.dump")
    latest = write_backup(backup_dir, "examtracker-20260810-020000.dump")

    call_command("restore_database", "--into", "scratch")

    assert latest.name in capsys.readouterr().out


def test_the_restore_command_refuses_the_live_database(backup_dir, fake_pg, settings):
    write_backup(backup_dir, "examtracker-20260810-020000.dump")

    with pytest.raises(CommandError, match="live database"):
        call_command("restore_database", "--into", settings.DATABASES["default"]["NAME"])


def test_the_restore_command_says_so_when_there_is_nothing_to_restore(backup_dir, fake_pg):
    with pytest.raises(CommandError, match="No backups found"):
        call_command("restore_database", "--into", "scratch")


# --- scheduling ---------------------------------------------------------


def test_the_backup_runs_nightly(settings):
    entry = settings.CELERY_BEAT_SCHEDULE["database-backup"]

    assert entry["task"] == "ops.tasks.backup_database"
    assert entry["schedule"].hour == {2}


def test_the_backup_runs_before_the_morning_jobs(settings):
    """So the day's alerts and digest go out against a database that has
    already been backed up."""
    schedule = settings.CELERY_BEAT_SCHEDULE
    backup = schedule["database-backup"]["schedule"]
    alerts = schedule["elapsed-date-alerts"]["schedule"]

    assert min(backup.hour) < min(alerts.hour)


def test_the_task_returns_what_it_wrote(backup_dir, fake_pg):
    from ops.tasks import backup_database

    result = backup_database()

    assert result["status"] == "ok"
    assert result["size_bytes"] > 0


def test_a_failed_backup_surfaces_rather_than_being_swallowed(backup_dir, monkeypatch):
    """A backup that quietly does not happen is the worst kind: everything
    looks fine until the day it matters."""
    from ops.tasks import backup_database

    class Failed:
        returncode = 1
        stdout = ""
        stderr = "disk full"

    monkeypatch.setattr(backups.subprocess, "run", lambda *a, **k: Failed())

    with pytest.raises(BackupError):
        backup_database()
