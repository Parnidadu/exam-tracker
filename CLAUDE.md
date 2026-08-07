# Exam Tracker

Tracks scheduled examinations through their lifecycle: whether each stage was
conducted, whether a discrepancy occurred, and whether results were declared.
Data arrives by scraping official exam board sites, and a human verifies it.

## The rule that governs the data model

Status has two independent sources: what the scraper observed, and what a human
confirmed. **They are never collapsed into one column.** Every status track on a
stage stores both:

```
machine_value, machine_confidence, machine_seen_at
human_value,   verified_by,        verified_at
```

`effective_status` is a resolver, not a column — the human value when the
verification is fresh (<= 14 days), otherwise the machine value. A scrape run
that contradicts a fresh human value raises a conflict; it does not write.

If a change you are making would let a scraper overwrite a verification, stop
and flag it rather than proceeding.

## Domain model

- `Board` — conducting authority
- `Exam` — one cycle, e.g. "UPSC CSE 2026"
- `ExamStage` — prelims / mains / interview. **Status lives here, not on Exam.**
  Most exams are multi-stage and stages progress independently.
- `Source` — scrape config (URL, parser key, cron), editable from admin
- `VerificationRecord` — append-only; actor, track, value, evidence URL, note
- `Discrepancy` — postponement, leak, key error, re-exam, court stay

Three independent status tracks per stage: conduct, result, integrity.

## Stack

Django + DRF + Celery + PostgreSQL + Redis (backend), React + Vite +
TypeScript + Tailwind (frontend). Everything runs in Docker Compose.

```
backend/     Django project, apps, Celery tasks, scrapers
frontend/    React app
.github/     CI workflows, issue templates
```

## Conventions

- Branch as `ext-NNN-short-description`, matching the issue ID
- Every PR description contains `Closes #NN`
- Configuration comes from environment variables only. New setting means a new
  line in `.env.example` in the same commit. Never commit `.env`.
- Migrations are committed alongside the model change
- Tests accompany the change; do not defer them to a later ticket

## Working method

Work one issue at a time. Read the issue first:

```bash
gh issue view NN --repo parnidadu/exam-tracker
```

Implement exactly the scope described. If the issue's tasks appear to require
work belonging to another ticket, say so instead of expanding scope — the sprint
plan depends on tickets staying separate.

Before declaring done, restate each acceptance criterion and say concretely how
it was met, or that it was not.

## Commands

```bash
docker compose up -d --build
docker compose exec api python manage.py migrate
docker compose exec api pytest
docker compose exec web npm test
docker compose logs -f api
```

## Sprint order

S0 foundation · S1 domain models and read API · S2 verification workflow and
audit · S3 public dashboard · S4 scraper framework · S5 reconciliation and
discrepancies · S6 notifications and hardening.

Built manual-first on purpose: the app is usable with hand-entered data before
any scraper exists. Do not pull scraping work forward.
