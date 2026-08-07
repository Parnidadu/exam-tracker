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

## Tests

```bash
docker compose exec api pytest
docker compose exec web npm test
```

## Logs

```bash
docker compose logs -f api
```
