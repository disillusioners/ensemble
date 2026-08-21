#!/usr/bin/env bash
# Test Pack: admittable_work_pg_test — PostgreSQL dialect confirmation of
# JobQueueRepository.list_queues_with_admittable_work
#
# Purpose: PG-side counterpart to the SQLite coverage in
# ``tests/job_queue/test_job_processor_admission_starvation.py``
# (``TestWorkDrivenScanShape``) and ``tests/job_queue/test_queue_repository.py``
# (``TestListQueuesWithAdmittableWork``). The SQLite-only test path cannot
# catch regressions that only surface under the PostgreSQL ``GROUP BY`` /
# ``ORDER BY`` translation of ``func.min(JobItem.created_at).asc()`` — for
# example, dialect differences in how ``MIN()`` aggregates are evaluated
# against ``created_at`` columns.
#
# This module exists to pin the SELECT-side contract under the real PostgreSQL
# engine so a regression that flips the ``GROUP BY`` into ``DISTINCT`` (or
# vice-versa) becomes visible in CI's PG run.
#
# Notes:
# - This module does NOT install the Phase 2 constraint triggers
#   (``trg_job_queue_items_active_lock_guard``); its focus is the SELECT
#   filter, not the INSERT/UPDATE invariant.
# - Real PostgreSQL engine required. The ``pg_engine`` fixture in
#   ``tests/postgres/conftest.py`` skips the entire module cleanly when
#   PostgreSQL is not reachable. Test will skip if PG is unavailable.
#
# Expected test count: 5 tests
# Timeout: 3 minutes (180s) — 5-min-class pack, 3-min wrapper to leave margin.
# Marker: -m postgres (tests/postgres/conftest.py auto-applies via
#         pytest_collection_modifyitems; pytestmark = pytest.mark.postgres
#         at module level).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: admittable_work_pg_test ==="

cd "$PROJECT_DIR"

# PostgreSQL connection — primary dev/test DB (see .env + tests/postgres/conftest.py).
# Matches the established pattern in test/packs/wanderer_completion_pg_test.sh
# and c2_pg_manager_unit_test.sh (PG_TEST_HOST/PORT/DB/USER/PASSWORD).
export PG_TEST_HOST=localhost
export PG_TEST_PORT=5432
export PG_TEST_DB=ensemble_test
export PG_TEST_USER=ensemble
export PG_TEST_PASSWORD=ensemble_dev

timeout 180s .venv/bin/pytest \
  tests/postgres/test_list_queues_with_admittable_work_pg.py \
  --override-ini="addopts=" -m postgres --tb=short -q 2>&1

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
