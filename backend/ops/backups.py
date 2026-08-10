"""Database backups, and restoring one.

A backup nobody has restored is a hope, not a backup. The restore path
here is a first-class command for that reason - rehearsing it means
running the same thing an operator would run at 3am, not a sequence of
pg_restore flags reconstructed under pressure.

Format is pg_dump's custom format (-Fc): compressed, and restorable
selectively, which matters when the thing you need back is one table
rather than the whole database.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Timestamped so backups sort chronologically by name, and so two runs on
#: the same day never overwrite each other.
FILENAME_FORMAT = "examtracker-%Y%m%d-%H%M%S.dump"
FILENAME_GLOB = "examtracker-*.dump"


class BackupError(Exception):
    """A backup or restore did not complete."""


@dataclass(frozen=True)
class BackupResult:
    path: Path
    size_bytes: int
    pruned: int = 0


def backup_dir() -> Path:
    return Path(str(settings.BACKUP_DIR))


def retention_days() -> int:
    return int(settings.BACKUP_RETENTION_DAYS)


def _db() -> dict[str, str]:
    config = settings.DATABASES["default"]
    return {
        "name": config["NAME"],
        "user": config["USER"],
        "password": config["PASSWORD"],
        "host": config.get("HOST") or "localhost",
        "port": str(config.get("PORT") or "5432"),
    }


def _env_with_password(password: str) -> dict[str, str]:
    """Pass the password through the environment, never on the command
    line - argv is readable by any other process on the box via `ps`."""
    return {**os.environ, "PGPASSWORD": password}


def _run(command: list[str], password: str, what: str) -> None:
    result = subprocess.run(
        command,
        env=_env_with_password(password),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # stderr, not stdout: pg_dump writes the dump itself to stdout when
        # asked to, and including it in an exception message would be both
        # useless and enormous.
        raise BackupError(f"{what} failed ({result.returncode}): {result.stderr.strip()}")


def existing_backups() -> list[Path]:
    """Newest first."""
    if not backup_dir().exists():
        return []
    return sorted(backup_dir().glob(FILENAME_GLOB), reverse=True)


def prune(now: datetime | None = None) -> int:
    """Delete backups older than the retention window.

    Never deletes the most recent one, however old it is. A backup job
    that stopped running a month ago should not also quietly destroy the
    last copy it made - that turns one failure into two, and the second is
    unrecoverable.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(days=retention_days())

    backups = existing_backups()
    removed = 0
    for path in backups[1:]:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.get_current_timezone())
        if modified < cutoff:
            path.unlink()
            removed += 1
    return removed


def create_backup(now: datetime | None = None) -> BackupResult:
    """Dump the database to the backup directory."""
    now = now or timezone.now()
    db = _db()
    directory = backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / now.strftime(FILENAME_FORMAT)

    _run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--host={db['host']}",
            f"--port={db['port']}",
            f"--username={db['user']}",
            f"--file={path}",
            db["name"],
        ],
        db["password"],
        "pg_dump",
    )

    if not path.exists() or path.stat().st_size == 0:
        # pg_dump can exit 0 having written nothing if the target is
        # wrong. An empty file that looks like a backup is worse than no
        # file, because it is only discovered during a restore.
        raise BackupError(f"pg_dump produced no data at {path}")

    pruned = prune(now)
    size = path.stat().st_size
    logger.info("database backup written: %s (%s bytes), %s pruned", path, size, pruned)
    return BackupResult(path=path, size_bytes=size, pruned=pruned)


def latest_backup() -> Path | None:
    backups = existing_backups()
    return backups[0] if backups else None


def restore(dump: Path, into: str, *, allow_live: bool = False) -> None:
    """Restore a dump into `into`, creating that database if needed.

    Refuses to touch the configured application database unless told
    explicitly. Rehearsing a restore is only safe if the rehearsal cannot
    become the disaster - and the moment to discover you typed the live
    database name is not after pg_restore has finished.
    """
    db = _db()
    if into == db["name"] and not allow_live:
        raise BackupError(
            f"{into!r} is the live database. Pass a scratch name to rehearse, "
            "or allow_live=True if you really mean to overwrite it."
        )
    if not dump.exists():
        raise BackupError(f"no such dump: {dump}")

    base = [f"--host={db['host']}", f"--port={db['port']}", f"--username={db['user']}"]

    # Dropped and recreated rather than restored over: a restore into a
    # database that still holds rows leaves a mixture of old and new, which
    # is the hardest possible state to reason about afterwards.
    _run(
        ["psql", *base, "--dbname=postgres", "-c", f'DROP DATABASE IF EXISTS "{into}"'],
        db["password"],
        "drop scratch database",
    )
    _run(
        ["psql", *base, "--dbname=postgres", "-c", f'CREATE DATABASE "{into}"'],
        db["password"],
        "create scratch database",
    )
    _run(
        ["pg_restore", *base, f"--dbname={into}", "--no-owner", "--no-privileges", str(dump)],
        db["password"],
        "pg_restore",
    )
    logger.info("restored %s into %s", dump, into)
