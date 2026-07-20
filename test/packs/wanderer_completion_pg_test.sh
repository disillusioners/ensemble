#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: wanderer_completion_pg_test ==="
cd "$PROJECT_DIR"

# PostgreSQL connection — primary dev/test DB (see .env)
export DATABASE_URL="postgresql+psycopg://ensemble:ensemble_dev@localhost:5432/ensemble_test"
export PG_TEST_HOST=localhost
export PG_TEST_PORT=5432
export PG_TEST_DB=ensemble_test
export PG_TEST_USER=ensemble
export PG_TEST_PASSWORD=ensemble_dev

# Script-internal timeout guard (Layer 2): 280s — interrupts hung tests
# Command-level timeout (Layer 1): 300s via `timeout` wrapper below
timeout 280s .venv/bin/pytest \
  tests/postgres/test_wanderer_completion_reporting_pg.py \
  -v -m postgres --override-ini="addopts=" --tb=short -q 2>&1
EXIT_CODE=$?
if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
