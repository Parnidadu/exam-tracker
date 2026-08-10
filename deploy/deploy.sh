#!/usr/bin/env bash
# One command to deploy, for either environment.
#
#   ./deploy/deploy.sh staging
#   ./deploy/deploy.sh prod
#
# The order matters and is the reason this is a script rather than a
# sentence in a README: migrations must run before the new code serves
# traffic, and static files must exist before the proxy points at them.
# A deploy assembled from memory at 2am gets that order wrong.
set -euo pipefail

ENVIRONMENT="${1:-prod}"
ENV_FILE=".env.${ENVIRONMENT}"
# Namespaces the compose project, so staging and production on one host
# keep separate volumes rather than sharing a database.
export DEPLOY_ENV="${ENVIRONMENT}"
COMPOSE="docker compose -f docker-compose.prod.yml --env-file ${ENV_FILE}"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "No ${ENV_FILE}. Copy deploy/env.example and fill it in." >&2
    exit 1
fi

export ENV_FILE

echo "==> Building images (${ENVIRONMENT})"
${COMPOSE} build

echo "==> Starting the database and broker"
${COMPOSE} up -d db redis

echo "==> Applying migrations"
# Before anything serves traffic. run --rm rather than exec: the api
# container may not be running yet on a first deploy.
${COMPOSE} run --rm api python manage.py migrate --no-input

echo "==> Collecting static files"
${COMPOSE} run --rm api python manage.py collectstatic --no-input

echo "==> Checking the deployment settings"
# Fails the deploy rather than discovering a missing secret from the logs
# after the old release has already been replaced.
${COMPOSE} run --rm api python manage.py check --deploy --fail-level ERROR

echo "==> Starting everything"
${COMPOSE} up -d --remove-orphans

echo "==> Waiting for health"
for _ in $(seq 1 30); do
    if ${COMPOSE} ps --format '{{.Service}} {{.Health}}' | grep -q 'api healthy'; then
        echo "==> ${ENVIRONMENT} is up"
        ${COMPOSE} ps
        exit 0
    fi
    sleep 5
done

echo "api did not become healthy; leaving the stack up for inspection" >&2
${COMPOSE} ps
${COMPOSE} logs --tail 50 api >&2
exit 1
