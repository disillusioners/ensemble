#!/bin/bash
# Helper script to run E2E migration tests against a PostgreSQL instance.
#
# By default this script uses docker compose to spin up a throwaway
# PostgreSQL 16 instance via docker-compose.test.yml, runs the E2E
# migration tests, and tears the container down at the end.
#
# If a local PostgreSQL is already running (with database "ensemble_test"
# and a user that can CREATE/DROP tables), set SKIP_DOCKER=1 to skip
# the docker step and run the tests against the existing instance.
#
# Usage:
#   ./scripts/test-migration.sh                    # docker compose (default)
#   SKIP_DOCKER=1 ./scripts/test-migration.sh      # use existing local PG
#   POSTGRES_USER=me ./scripts/test-migration.sh   # override user (default: $USER)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

POSTGRES_USER=${POSTGRES_USER:-${USER:-ensemble}}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-test_password}
POSTGRES_DB=${POSTGRES_DB:-ensemble_test}
POSTGRES_HOST=${POSTGRES_HOST:-localhost}
POSTGRES_PORT=${POSTGRES_PORT:-5432}

if [ "${SKIP_DOCKER:-0}" != "1" ]; then
  echo "[test-migration] Starting PostgreSQL via docker compose..."
  docker compose -f docker-compose.test.yml up -d --wait
  trap 'docker compose -f docker-compose.test.yml down -v' EXIT
  # When docker compose provisions the DB, the password is fixed to test_password
  POSTGRES_PASSWORD=test_password
fi

export POSTGRES_HOST
export POSTGRES_PORT
export POSTGRES_DB
export POSTGRES_USER
export POSTGRES_PASSWORD

echo "[test-migration] Running E2E migration tests..."
python -m pytest tests/e2e/test_migration_e2e.py -v --tb=short

echo "[test-migration] Done."
