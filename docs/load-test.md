# Load test: the public dashboard

## The question

Can the public dashboard serve an exam-results-day crowd with p95 latency
under 500ms, and where does it stop being able to?

**Target concurrency: 100 simultaneous in-flight requests.**

That number is a planning assumption, so here is the reasoning rather
than just the figure. Opening the dashboard fires three API calls
(exams, boards, discrepancy feed). At the measured throughput of ~287
requests/second that is roughly **95 dashboard page-loads per second**;
with a reader spending ten or twenty seconds on a page, 100 concurrent
requests corresponds to something on the order of a thousand people
using the site at once. For a tracker covering state and national exam
boards, that is a defensible peak. It is not a measured expectation - the
service has no traffic yet - and it should be revisited against real
numbers once it does.

## Result

**Pass.** At the target of 100 concurrent, p95 was **411ms**, under the
500ms threshold. The limit is between 100 and 150.

| Concurrency | p50 | **p95** | p99 | Throughput | p95 < 500ms |
|---|---|---|---|---|---|
| 25  | 91 ms  | **163 ms** | 237 ms | 243 req/s | pass |
| 50  | 169 ms | **193 ms** | 220 ms | 287 req/s | pass |
| **100** | 329 ms | **411 ms** | 432 ms | 287 req/s | **pass** |
| 150 | 513 ms | **630 ms** | 670 ms | 263 req/s | fail |
| 200 | 578 ms | **732 ms** | 758 ms | 284 req/s | fail |

600 requests per level, no warm-up included, zero non-200 responses.

Per endpoint at the target concurrency, nothing stands out as a hot spot -
the cost is queueing, not any one query:

| Endpoint | p50 | p95 |
|---|---|---|
| `/api/exams/` (list, paged) | 176 ms | 201 ms |
| `/api/exams/?search=` (filtered) | 171 ms | 188 ms |
| `/api/exams/<slug>/` (detail) | 175 ms | 205 ms |
| `/api/boards/` | 176 ms | 205 ms |
| `/api/discrepancy-feed/` | 176 ms | 200 ms |
| `/api/calendar/?month=` | 180 ms | 206 ms |

## What the numbers say

Throughput flattens at **~287 req/s** from 50 concurrent onward, and
latency then grows almost exactly linearly with concurrency: 50 → 169ms,
100 → 329ms, 150 → 513ms, 200 → 578ms. That is queueing, not the
application getting slower. Three gunicorn workers saturate, and
everything beyond that waits.

So the ceiling is worker count, not query cost. The first lever if more
headroom is needed is `GUNICORN_WORKERS`, then a second application
container; the database was not the constraint at any level tested.

## Method

```bash
docker compose -f docker-compose.prod.yml --env-file .env.staging exec api \
    python manage.py seed_load_data --exams 500
python backend/scripts/load_test.py --url https://localhost --insecure \
    --concurrency 25 50 100 150 200 --requests 600
```

Four choices worth stating, because each one would flatter the result if
made the other way:

**The dashboard's own request mix**, not one URL in a loop. Weighted
towards the three calls the home page makes, with detail views and the
calendar behind them. Hammering the cheapest endpoint would produce a
number that says nothing about what a visitor experiences.

**Query strings vary per request.** Public reads are cached for a minute
(EXT-034), and a fixed URL would measure the cache rather than the
application. A real crowd is filtering and paging, and those are misses.

**A realistic dataset** - 500 exams, 996 stages, 1,992 status tracks, via
`seed_load_data`. The ten exams `seed` creates would flatter every query:
a list endpoint paginating twenty rows out of ten is not the endpoint
that runs in production.

**The rate limit was raised for the run.** The anonymous throttle is
60/min per IP (EXT-062) and every request here comes from one address, so
at the shipped rate this would have measured how fast the server can say
429. `THROTTLE_ANON_RATE` was set high in `.env.staging` for the test
only. The throttle itself is verified separately by
`scripts/verify_throttle.py`.

## Environment

Measured against the **production configuration** - gunicorn behind Caddy
over HTTPS, `config.settings.prod`, Postgres 16, Redis - brought up with
`./deploy/deploy.sh staging`. Not `runserver`, whose numbers would mean
nothing.

| | |
|---|---|
| Host | development laptop, Docker Desktop, 8 CPUs / 15.4 GiB available to Docker |
| Application | gunicorn, 3 sync workers |
| Load generator | the same host as the server |

**These are not production hardware numbers.** The load generator shares
CPU with the server, which understates achievable throughput, and a real
host would have different storage and network characteristics. The shape
of the curve - flat throughput, linear latency past saturation - is the
transferable finding; the absolute figures should be re-measured on the
real host after EXT-064's first deploy.

## Not covered

- **Sustained load.** These are short bursts. Anything that degrades over
  minutes - connection pool exhaustion, memory growth - would not show up.
- **The write path.** Verification and triage are staff actions at human
  speed, not a crowd.
- **The scrape pipeline running concurrently.** A poll that lands during
  peak competes for database connections; not measured.
- **Cold cache at scale.** The warm-up primes some entries; a true cold
  start under full load was not measured separately.
