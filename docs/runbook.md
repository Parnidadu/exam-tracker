# Runbook

Three things that go wrong, and what to do about each. Written to be
followed by someone who did not build this — every step is a command you
can paste, and every scenario ends with how to tell it worked.

## Before you start

Everything below runs from the checkout on the host, and assumes the
stack is up.

```bash
cd /path/to/exam-tracker
docker compose ps                       # development
docker compose -f docker-compose.prod.yml --env-file .env.prod ps    # deployed
```

Every `docker compose exec api …` below is the same in both; only the
compose flags differ. Set this once and reuse it:

```bash
alias dc='docker compose -f docker-compose.prod.yml --env-file .env.prod'
```

Two facts that make the rest make sense:

- **A human verification beats a scraper for 14 days.** Correcting
  something as a verifier is the fastest safe fix — it wins over whatever
  the scraper says and cannot be overwritten by the next poll.
- **Public reads are cached for 60 seconds.** A verification busts that
  cache immediately; nothing else does. If you change data another way,
  expect up to a minute of staleness.

---

## Scenario 1 — a source stopped working

**You are here if:** a "source is stale" alert arrived, the health page
shows a source failing, or a board's exams have stopped updating.

1. **List the sources and their health.**

   ```bash
   dc exec api python manage.py scrape_source --list
   ```

   Note the `id` of the one that is failing or paused.

2. **Read the last error.** Open
   `https://<your-domain>/admin/scraping/sourcehealth/` — it shows last
   success, last failure, the failure streak and the error text. If the
   site is down, use:

   ```bash
   dc exec api python manage.py shell -c "
   from scraping.models import Source
   from scraping.health import health_for, is_stale
   for s in Source.objects.select_related('board'):
       h = health_for(s)
       print(s.pk, s.board.code, 'enabled=', s.enabled, 'fails=', h.consecutive_failures,
             'stale=', is_stale(s, h), repr(h.last_error[:120]))
   "
   ```

3. **Reproduce it now**, rather than waiting for the next scheduled run.

   ```bash
   dc exec api python manage.py scrape_source <id>
   ```

   This prints exactly what the scheduled job would do.

4. **Match the output to a cause.**

   | What you see | What it means | Fix |
   |---|---|---|
   | `status failed`, error mentions robots | The board's robots.txt now forbids us | Stop here. Do not work around it — go to step 7 and raise it with whoever owns the relationship with that board. |
   | `status failed`, 404 / connection error | The board moved or removed the page | Step 5, change the URL |
   | `parser_missing` | `parser_key` names a parser that does not exist | Step 5, correct the key |
   | `parser_failed` | The parser raised — usually a redesigned page | Step 6 |
   | `status ok`, `parsed True`, `observations 0` | The page loads but nothing is recognisable on it — a redesign | Step 6 |
   | `status ok`, observations > 0 | It is working now; the failure was transient | Step 8 |

5. **Correct the configuration.** Open
   `https://<your-domain>/admin/scraping/source/`, click the source, and
   change the `url` or `parser_key`. Save. Then repeat step 3. Valid
   parser keys:

   ```bash
   dc exec api python manage.py shell -c "
   from scraping.parsers import registered_keys; print(registered_keys())"
   ```

6. **If the board redesigned its page**, the parser needs code changes —
   that is a development task, not a runbook one. Do step 7 so the source
   stops alerting, open an issue with the failing URL, and stop here.

7. **Pause the source** so it stops failing and stops alerting nightly.
   In `https://<your-domain>/admin/scraping/source/`, untick `enabled`
   and save. It takes effect without a restart. Paused sources are
   excluded from stale alerts, and the schedule is restored intact when
   you re-enable it.

8. **Confirm it is fixed.** Repeat step 3 and check `status ok` with
   `observations` above zero, then:

   ```bash
   dc exec api python manage.py scrape_source --list
   ```

   The source should read `ok, last <today>`.

9. **Check what it queued while broken.** A source that came back may
   have produced observations nobody has matched:

   ```bash
   dc exec api python manage.py shell -c "
   from scraping.models import TriageItem
   print('pending triage items:', TriageItem.objects.filter(status='pending').count())"
   ```

   Work them at `https://<your-domain>/admin/scraping/triageitem/`.

---

## Scenario 2 — bad data is showing publicly

**You are here if:** the dashboard is showing a wrong exam status, or
someone has reported it.

Speed matters more than tidiness here. Step 2 fixes what the public sees;
everything after it stops the problem recurring.

1. **Find the exam and note the stage and track.** Open the exam on the
   public site — the URL ends in its slug — or:

   ```bash
   dc exec api python manage.py shell -c "
   from exams.models import StatusTrack
   for t in StatusTrack.objects.select_related('exam_stage__exam').filter(
           exam_stage__exam__slug='<slug>'):
       print(t.exam_stage, t.track, 'machine=', repr(t.machine_value),
             'human=', repr(t.human_value), 'effective=', repr(t.effective_status))
   "
   ```

2. **Correct it as a verifier.** Sign in at
   `https://<your-domain>/verify`, find the stage, set the correct value
   with an evidence URL, and submit.

   This is the fastest safe fix: a fresh human verification beats the
   machine value for 14 days, so the next scrape cannot undo it, and the
   write busts the public cache immediately.

3. **Confirm the public sees the correction.**

   ```bash
   curl -s https://<your-domain>/api/exams/<slug>/ | grep -o '"effective_status":"[^"]*"'
   ```

   If it still shows the old value, wait 60 seconds for the cache and try
   once more. If it is still wrong, the verification did not save — repeat
   step 2 and check for an error in the console.

4. **Find out where it came from.** In the output from step 1: if
   `machine` holds the wrong value, a scraper produced it — continue. If
   only `human` was wrong, it was a mistaken verification; step 2 has
   already fixed it, so skip to step 7.

5. **Stop it recurring.** Identify the source for that board and pause it
   (Scenario 1, step 7). A source that published one wrong status will
   publish it again on its next run, and your correction only holds for
   14 days.

6. **If a discrepancy was wrongly published**, dismiss it at the
   discrepancy console, `https://<your-domain>/discrepancies`, with a
   note saying why. Only `confirmed` and `resolved` discrepancies are
   public, so dismissing removes it from the feed. Confirm:

   ```bash
   curl -s https://<your-domain>/api/discrepancy-feed/ | grep -o '"id"' | wc -l
   ```

   (`grep -o | wc -l`, not `grep -c` — the response is a single line of
   JSON, so `-c` would answer 1 however many entries it holds.)

7. **Write down what happened** on the issue tracker: which exam, what it
   said, where it came from, and what you changed. The next person to see
   the same board misbehave needs that more than they need this runbook.

---

## Scenario 3 — restore from backup

**You are here if:** data has been lost or corrupted too broadly to
correct by hand. If a handful of statuses are wrong, use Scenario 2
instead — restoring loses every verification made since the backup.

Nightly backups and the format are described in [backups.md](backups.md).

1. **Back up the broken state first**, while the app is still running.
   You may need it — to recover something the older backup predates, or
   to work out what went wrong.

   ```bash
   dc exec api python manage.py backup_database
   ```

2. **Stop everything that talks to the database.** All three, not just
   the schedulers: the restore drops and recreates the database, and
   Postgres refuses while any session is connected. `api` holds
   connections through gunicorn, so leaving it up fails step 6 with
   *"database is being accessed by other users"*.

   ```bash
   dc stop api worker beat
   ```

3. **List what you can restore from.** Newest last. `run --rm` starts a
   throwaway container, because `exec` needs a running one and you just
   stopped it.

   ```bash
   dc run --rm api ls -lh /backups
   ```

4. **Restore into a scratch database and check it** before touching the
   live one.

   ```bash
   dc run --rm api python manage.py restore_database        --into exam_tracker_scratch --dump /backups/<file>.dump
   ```

5. **Verify the scratch copy has what you expect.**

   ```bash
   dc exec db sh -c 'psql -U "$POSTGRES_USER" -d exam_tracker_scratch -c      "select (select count(*) from exams_exam) as exams,
             (select count(*) from verification_verificationrecord) as verifications;"'
   ```

   The quoting matters: `sh -c` runs inside the container, where compose
   has already set `POSTGRES_USER`. Do not `source .env.prod` in your own
   shell to get it — compose accepts values bash will not, and sourcing
   the file errors out on the user-agent line.

   If the numbers look wrong, go back to step 4 with an older dump.

6. **Restore over the live database.** This is the irreversible step, and
   the flag is deliberately required.

   ```bash
   dc run --rm api sh -c 'python manage.py restore_database        --into "$POSTGRES_DB" --dump /backups/<file>.dump --allow-live'
   ```

   If this reports *"being accessed by other users"*, something is still
   connected — repeat step 2 and check `dc ps`.

7. **Check the schema matches the running code.**

   ```bash
   dc run --rm api python manage.py migrate --check
   ```

   Silence means there is nothing pending. If it reports pending
   migrations, the backup predates a deploy — apply them:

   ```bash
   dc run --rm api python manage.py migrate
   ```

8. **Start everything again.**

   ```bash
   dc start api worker beat
   ```

9. **Confirm the site works and the data is there.**

   ```bash
   curl -s -o /dev/null -w "%{http_code}
" https://<your-domain>/api/exams/
   dc exec api python manage.py scrape_source --list
   ```

10. **Tell the verifiers.** Anything verified between the backup and now
    is gone and will reappear in their queue. They need to know that
    before they wonder why.

---

## When to stop and escalate

- A board's robots.txt now disallows us — that is a relationship
  question, not a configuration one.
- A parser needs code changes to match a redesigned page.
- A restore that fails at step 7 with migrations that will not apply.
- Anything where the fix would mean writing a status nobody has verified.
  This app exists to be trustworthy about exam status; guessing to make a
  page look right is the one thing not to do.
