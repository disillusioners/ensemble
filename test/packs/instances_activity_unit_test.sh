#!/usr/bin/env bash
# Test Pack: instances_activity_unit_test — BE acceptance gate for
# the panel-activity fix (order=activity on /api/instances).
#
# Exercises the 4 files derived from the diff range
# 445b4b08..bbe8d0c0 on fix/job-queue-panel-activity-ordering:
#   * tests/test_instance_list_order_api.py         (NEW)  — API route
#   * tests/unit/test_instance_list_order_repository.py (NEW) — repository
#   * tests/test_api.py                              (REPAIRED) — broad API
#   * tests/unit/test_hide_kb_instances.py           (REPAIRED) — KB filter
#
# Expected baseline (pre-existing, OUT-OF-SCOPE): up to 2 known failures in
# tests/test_api.py — test_send_message_success + test_global_exception_handler
# (Mock-await class, attributed since 2026-09-03).
#
# Unit pack — 2 min hard limit (dual-layer):
#   Layer 2 (script-internal): 150s — interrupts hung tests
#   Layer 1 (command-level):   300s via outer `timeout` wrapper
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

ACTUAL_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
ACTUAL_COMMIT="$(git rev-parse --short HEAD)"
echo "=== Test Pack: instances_activity_unit_test ==="
echo "branch=${ACTUAL_BRANCH} commit=${ACTUAL_COMMIT}"

# Drift guard — pin to the panel-activity branch + commit.
EXPECTED_BRANCH="${EXPECTED_BRANCH:-fix/job-queue-panel-activity-ordering}"
EXPECTED_COMMIT="${EXPECTED_COMMIT:-bbe8d0c0}"
if [[ "${ACTUAL_BRANCH}" != "${EXPECTED_BRANCH}" || "${ACTUAL_COMMIT}" != "${EXPECTED_COMMIT}" ]]; then
  echo "RESULT: DRIFT (expected ${EXPECTED_BRANCH} @ ${EXPECTED_COMMIT}, got ${ACTUAL_BRANCH} @ ${ACTUAL_COMMIT})"
  exit 1
fi
echo "RESULT: BRANCH-CHECK (expected ${EXPECTED_BRANCH} @ ${EXPECTED_COMMIT}, got ${ACTUAL_BRANCH} @ ${ACTUAL_COMMIT})"

EXIT_CODE=0
timeout 150 .venv/bin/pytest \
  tests/test_instance_list_order_api.py \
  tests/unit/test_instance_list_order_repository.py \
  tests/test_api.py \
  tests/unit/test_hide_kb_instances.py \
  --tb=short -q 2>&1 || EXIT_CODE=$?

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
