"""Scheduled operational work."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="ops.tasks.backup_database")
def backup_database() -> dict[str, object]:
    """Nightly database backup.

    Failure is raised, not swallowed. A backup that quietly does not
    happen is the worst kind: everything looks fine until the day it
    matters, so this surfaces as a task failure and a Sentry event.
    """
    from .backups import create_backup

    result = create_backup()
    return {
        "status": "ok",
        "path": str(result.path),
        "size_bytes": result.size_bytes,
        "pruned": result.pruned,
    }
