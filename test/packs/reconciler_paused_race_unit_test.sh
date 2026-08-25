#!/usr/bin/env bash
# Test Pack: reconciler_paused_race_unit_test
#
# Ad-hoc pack created for the mandatory e2e merge gate on branch
# fix/reconciler-paused-race-job-cancel. Covers the two new test files
# that are NOT in any of the 5 registered packs (claim_guard_locks,
# concurrency_atomic, turn_transitions_reconciler, job_queue, api):
#
#   1. tests/repositories/test_turn_reconciler_paused_race.py
#      - TestPausedRaceTerminalWriteGuard (4 tests):
#          * test_paused_instance_suppresses_job_terminal_write
#          * test_running_instance_suppresses_job_terminal_write
#          * test_waiting_children_instance_suppresses_job_terminal_write
#          * test_terminal_instance_writes_job_terminal_state
#      - TestIncidentShapeNotRetryable (1 test):
#          * test_incident_shape_row_not_retryable
#      Drives the prod-incident shape: instance paused/running +
#      superseded-task cancel + JobItem active + lock held →
#      reconcile_turn_mirror must NOT stamp the live Job
#      (admission_state/terminal_reason/failed_at CASE branches +
#      job_locks DELETE all suppressed). Terminal instance
#      (completed) keeps the write-through. Also pins the
#      atomic_retry non-retryability of failed_at=NULL rows.
#
#   2. tests/test_observer_failed_at_stamp.py
#      - TestFailedAtStamp (2 tests):
#          * test_failed_path_stamps_failed_at_and_row_is_retryable
#          * test_completed_path_does_not_stamp_failed_at
#      - TestFinalizeActiveToDoneStamp (1 test):
#          * test_finalize_active_to_done_failed_branch_stamps_and_row_is_retryable
#      Drives the counter-part: the OBSERVER path stamps failed_at
#      on the FAILED branch only (Site 1 _finalize_job_db_sync +
#      Site 3 finalize_active_to_done). Cancelled/completed keeps
#      NULL. atomic_retry acceptance is asserted via the REAL
#      JobRepository.atomic_retry API.
#
# Together these two files pin the paused-race amendment's split:
# the OBSERVER stamps failed jobs, the RECONCILER leaves alive
# instances' jobs untouched. A failed_at=NULL row is correctly
# non-retryable.
#
# In-process suites (no daemon, no PG, no live install).
# Modeled on test/packs/job_queue_unit_test.sh (simplest existing
# pack).
#
# Script-internal timeout (Layer 2): 150s.
# Command-level timeout (Layer 1): caller wraps with `timeout 180`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: reconciler_paused_race_unit_test ==="

cd "$PROJECT_DIR"

timeout 150s .venv/bin/pytest \
  tests/repositories/test_turn_reconciler_paused_race.py \
  tests/test_observer_failed_at_stamp.py \
  --tb=short -q 2>&1

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
