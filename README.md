# Exam Tracker

Tracks scheduled examinations through their lifecycle: whether each stage was
conducted, whether a discrepancy occurred, and whether results were declared.
Data arrives by scraping official exam board sites, and a human verifies it.

## Stack

Django + DRF + Celery + PostgreSQL + Redis (backend), React + Vite +
TypeScript + Tailwind (frontend). Everything runs in Docker Compose.

```
backend/     Django project, apps, Celery tasks, scrapers
frontend/    React app
.github/     CI workflows, issue templates
```

## Local setup

```bash
git clone https://github.com/parnidadu/exam-tracker.git
cd exam-tracker
cp .env.example .env
docker compose up -d --build
docker compose exec api python manage.py migrate
```

The app is then available at:

- API: http://localhost:8000
- Frontend: http://localhost:5173

## Scheduled scraping

Compose runs a Celery `worker` and a `beat` scheduler. Beat's schedule is not
configured in code — it is derived from the `Source` rows in
`/admin/scraping/source/`, so changing when a board is polled, or pausing one
that is misbehaving, is an admin edit that takes effect on the running
scheduler. No restart, no deploy.

```bash
docker compose logs -f beat worker
```

## Deploying

Staging and production share one compose file and one settings module;
only the environment file differs. Deploying is one command, or a merge
to `main`. See [docs/deploy.md](docs/deploy.md).

```bash
make deploy-staging
make deploy ENV=prod
```

## Performance

The public dashboard was load-tested against the production
configuration: p95 **411ms at 100 concurrent requests**, saturating at
~287 req/s with three gunicorn workers. Method, full results and caveats
in [docs/load-test.md](docs/load-test.md).

## Backups

A nightly `pg_dump` runs from Celery Beat into a Docker volume, with a
restore command that is refused against the live database unless you ask
for it explicitly. The procedure, and the record of the last restore
rehearsal, are in [docs/backups.md](docs/backups.md).

```bash
docker compose exec api python manage.py backup_database
docker compose exec api python manage.py restore_database --into exam_tracker_scratch
```

## Tests

```bash
docker compose exec api pytest
docker compose exec web npm test
```

## Logs

```bash
docker compose logs -f api
```
