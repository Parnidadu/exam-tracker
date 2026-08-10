# Deploying

## Deploying is one command

```bash
./deploy/deploy.sh staging      # or: make deploy-staging
./deploy/deploy.sh prod         # or: make deploy ENV=prod
```

That script is the deploy. It builds, starts the database and broker,
migrates, collects static files, runs `check --deploy`, then brings
everything up and waits for health. The order is why it is a script and
not a paragraph: migrations must land before new code serves traffic, and
`check --deploy` must fail the deploy rather than being discovered in the
logs after the old release has already gone.

Or merge to `main`: `.github/workflows/deploy.yml` builds and publishes
both images and then runs **the same script** over SSH. The automated and
manual paths are the same path, so neither is exercised for the first
time during an incident.

Staging deploys on every merge. **Production is a manual run** of that
workflow — "merged" and "ready for candidates to see" are different
decisions, and this app publishes exam status to the public.

## Staging mirrors production

Same compose file, same settings module, same images, same healthchecks.
Only the environment file differs:

| Differs | Identical |
|---|---|
| `SITE_ADDRESS`, `DJANGO_ALLOWED_HOSTS` | services, images, healthchecks |
| database name, credentials, `DJANGO_SECRET_KEY` | `DJANGO_SETTINGS_MODULE=config.settings.prod` |
| `SENTRY_DSN`, HSTS values | gunicorn, Caddy, TLS handling, the deploy steps |

```bash
cp deploy/env.example .env.staging     # edit
cp deploy/env.example .env.prod        # edit
```

A staging environment assembled differently rehearses something you are
not about to do, so the only supported difference is values.

The compose project is namespaced per environment
(`exam-tracker-${DEPLOY_ENV}`). Compose otherwise derives the project name
from the directory, so production, staging and a developer's local stack
in one checkout would share volumes — one database between all three.

## HTTPS

Caddy terminates TLS and obtains certificates from Let's Encrypt on its
own, renewing them without a cron job — the commonest way a small
deployment ends up serving an expired certificate.

Django is told the original scheme via `X-Forwarded-Proto`. Without that
it believes every request arrived over plain HTTP and `SECURE_SSL_REDIRECT`
redirects in a loop.

`/health/` is exempt from the SSL redirect. The container healthcheck
speaks plain HTTP to itself — there is no TLS inside the container — and
without the exemption Django 301s it, the healthcheck never passes, and
the proxy waits forever on a dependency that is fine. The endpoint returns
`{"status": "ok"}` and nothing else.

### HSTS

Defaults to one hour, without `includeSubDomains` or `preload`. A browser
that has seen HSTS will refuse plain HTTP for the whole `max-age`, and
there is no way to retract it early. Raise `SECURE_HSTS_SECONDS` to
`31536000` once you are confident; turn on the other two only when every
subdomain is certainly HTTPS.

`manage.py check --deploy` reports two warnings at the conservative
default and none once all three are enabled — the remaining warnings are
a deliberate choice, not an oversight.

## Rehearsing the whole thing locally

The HTTPS path can be exercised without a domain. In `.env.staging`:

```
SITE_ADDRESS=https://localhost
TLS_DIRECTIVE=tls internal
```

Caddy then issues a locally-trusted certificate from its own CA, and
everything else behaves as it does in production.

```
$ ./deploy/deploy.sh staging
==> staging is up

  http://localhost/                ->  308  ->  https://localhost/
  https://localhost/               ->  200      (the built SPA)
  https://localhost/api/boards/    ->  200
  https://localhost/admin/         ->  302
  https://localhost/exams/<slug>   ->  200      (deep link survives a refresh)

  Strict-Transport-Security: max-age=3600
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Referrer-Policy: strict-origin-when-cross-origin

  issuer=CN=Caddy Local Authority - ECC Intermediate
```

The per-IP throttle was checked through the proxy at the same time: 70
requests against the 60/min anonymous limit gave 58 served and 12
refused, so `TRUSTED_PROXY_COUNT=1` is identifying the real client rather
than the proxy.

## Rolling back

Images are tagged with the commit SHA as well as a moving tag, so a
rollback names an exact build rather than "whatever `main` was
yesterday":

```bash
git checkout <previous-sha> && ./deploy/deploy.sh prod
```

Migrations are not rolled back automatically. If the release included
one, decide deliberately whether to reverse it — see
[backups.md](backups.md) for restoring instead.

## What this does not do

- **No off-site backups.** Dumps sit on a volume beside the database, so
  a host that loses its disk loses both. See [backups.md](backups.md).
- **Single host.** No load balancer, no replicas. Sized for a dashboard,
  not a national portal; EXT-065 measures whether that holds.
- **Secrets live in `.env` files on the host.** Fine for one machine,
  and the point to revisit if this ever grows past one.
