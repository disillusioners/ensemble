#!/usr/bin/env bash
# Test Pack: admission_starvation_unit_test — JobProcessor admission starvation regression
#
# Purpose: Regression test for the JobProcessor admission starvation fix.
# Root cause: ``JobProcessor._process_next_job`` previously iterated
# ``self._project_repo.list_projects()`` (default ``limit=100, updated_at DESC``);
# in DBs with >100 projects, projects outside the top-100 — most notably the
# ``system_default_project`` — were silently excluded from the scan, leaving their
# queues permanently unvisited and JobItems stuck in ``admission_state='queued'``.
#
# Fix: ``JobProcessor._process_next_job`` now derives the scan set from the queue
# side via ``JobQueueRepository.list_queues_with_admittable_work`` (work-driven
# scan), bounded by ``limit=1000`` (configurable), ordered by oldest pending job
# per queue (``MIN(created_at) ASC``), and honouring the same two-level pause
# semantics (project-level ``job_queue_paused``, queue-level ``is_paused``).
#
# What this pack exercises:
#   - 120-project regression (TestWorkDrivenScanShape)
#     — proves system-default admits a job when 120 other projects have newer
#       ``updated_at`` (the precise 338-project ensemble_dev confound).
#   - Ordering invariant: ``MIN(created_at) ASC`` orders queues with oldest
#     backlog first (stable under multiple items per queue).
#   - Limit cap bounds the scan (``limit=1000``).
#   - Exclusion of ``dead`` and soft-deleted rows.
#   - Single-queue baseline (regression in TestListQueuesWithAdmittableWork).
#
# Expected test count: 6 tests
# Timeout: 2 minutes (120s, unit class)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: admission_starvation_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/job_queue/test_job_processor_admission_starvation.py \
  --override-ini="addopts=" --tb=short -q 2>&1

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
