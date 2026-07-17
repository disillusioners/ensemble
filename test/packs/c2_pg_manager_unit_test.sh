#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: c2_pg_manager_unit_test ==="
cd "$PROJECT_DIR"
# Set PG env vars so InstanceManager uses PostgreSQL
export PG_TEST_HOST=localhost
export PG_TEST_PORT=5432
export PG_TEST_DB=ensemble_test
export PG_TEST_USER=ensemble
export PG_TEST_PASSWORD=ensemble_dev
# Use the PG URL override — InstanceManager reads DATABASE_URL
export DATABASE_URL="postgresql+psycopg://ensemble:ensemble_dev@localhost:5432/ensemble_test"
timeout 300s .venv/bin/pytest \
  tests/test_manager.py \
  tests/unit/test_question_deferred_pause_callback.py \
  tests/unit/test_question_deferred_pause_edge_cases.py \
  tests/unit/test_pause_instance_cascade.py \
  --tb=short -q --override-ini="addopts=" -m "postgres or not postgres" 2>&1
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