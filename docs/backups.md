# Database backups and restore

## What runs, and when

A Celery Beat job takes a `pg_dump` of the application database every
night at **02:30** (`CELERY_TIMEZONE`, UTC by default), before either of
the morning jobs, so the day's alerts and digest go out against a
database that has already been backed up.

| | |
|---|---|
| Task | `ops.tasks.backup_database` |
| Written to | `BACKUP_DIR`, default `/backups` — a named Docker volume, so dumps outlive the container that wrote them |
| Format | `pg_dump --format=custom` — compressed, and restorable selectively when what you need back is one table |
| Named | `examtracker-YYYYmmdd-HHMMSS.dump` |
| Retention | `BACKUP_RETENTION_DAYS`, default 14 |

Two things the job refuses to do quietly:

- **A dump that wrote nothing is an error.** `pg_dump` can exit 0 having
  produced an empty file if the target is wrong, and an empty file that
  looks like a backup is worse than no file — you find out during a
  restore.
- **The most recent backup is never pruned**, however old it is. A backup
  job that stopped running a month ago must not also destroy the last
  copy it made; that turns one failure into two, and the second is
  unrecoverable.

A failing backup raises, so it surfaces as a task failure and a Sentry
event rather than a silent gap.

## Taking one by hand

```bash
docker compose exec api python manage.py backup_database
```

## Restoring

```bash
# Rehearsal, or recovering data into somewhere safe to inspect:
docker compose exec api python manage.py restore_database --into exam_tracker_scratch

# A specific dump rather than the most recent:
docker compose exec api python manage.py restore_database \
    --into exam_tracker_scratch --dump /backups/examtracker-20260810-142508.dump

# A real recovery, over the live database. Refused without the flag.
docker compose exec api python manage.py restore_database \
    --into exam_tracker --allow-live
```

The target is **dropped and recreated** before restoring. Restoring into a
database that still holds rows leaves a mixture of old and new, which is
the hardest possible state to reason about afterwards.

Restoring over the configured application database is refused unless you
pass `--allow-live`. Rehearsing is only safe if the rehearsal cannot
become the disaster, and the moment to notice you typed the live database
name is not after `pg_restore` has finished.

## Rehearsal, 10 August 2026

Run against `postgres:16-alpine` with the seeded dataset, using the same
commands documented above — not a hand-assembled sequence of `pg_restore`
flags, because the procedure that gets rehearsed should be the procedure
that gets used.

```
### 1. Back up
Wrote /backups/examtracker-20260810-142508.dump (139,102 bytes); pruned 0.

### 2. Guard
CommandError: 'exam_tracker' is the live database. Pass a scratch name to
rehearse, or allow_live=True if you really mean to overwrite it.

### 3. Restore
Restored /backups/examtracker-20260810-142508.dump into exam_tracker_scratch.

### 4. Verify the restored copy matches
                       boards | exams | stages | migrations
exam_tracker                3 |    10 |     22 |         58
exam_tracker_scratch        3 |    10 |     22 |         58

### 5. Django can run against the restored copy
migrate --check: schema is current, no pending migrations
sample: UPSC CSE 2026
```

Point 5 matters as much as the row counts: a restore that produces a
database Django cannot run against is not a restore. `migrate --check`
passing means the schema came back complete, not just the rows.

### What the rehearsal caught

Debian trixie ships the PostgreSQL **17** client, while the server is
**16**. Dumping a 16 server with the 17 client emits
`SET transaction_timeout = 0` — a 17 setting the 16 server rejects — and
`pg_restore` reported `errors ignored on restore: 1`.

The data was intact when checked (the failing statement is a session
setting, not content), but a mismatched client is exactly the sort of
thing that stays harmless until the restore you actually need. The image
now installs `postgresql-client-16` from PGDG, pinned via `PG_MAJOR` in
the Dockerfile.

**Bump `PG_MAJOR` whenever the `postgres` image in `docker-compose.yml`
changes**, and re-run this rehearsal.

## What is not covered here

- Off-site copies. The dumps live on a Docker volume next to the
  database; a host that loses its disk loses both. Shipping them
  elsewhere belongs with the production deploy (EXT-064).
- Point-in-time recovery. These are nightly snapshots, so the worst case
  is losing up to a day of verifications.
