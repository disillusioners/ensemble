#!/usr/bin/env bash
# Test Pack: claim_guard_locks_unit_test — claim-side guard + job lock primitives
# + seam invariants
#
# Purpose: Single-pack regression over the three primitives that back the
# JobProcessor admission path. The claim-side NOT EXISTS guard
# (``TaskRepository.claim_pending_task``) interacts with job-locks and
# admission-state transitions, so the three test files are run together as
# one repo-layer blast-radius pack.
#
# Files covered:
#   1. tests/message_queue_redesign/test_task_repository.py
#      — claim_pending_task guard semantics, queue-admission guarantee
#        (the ``NOT EXISTS queued JobItem WHERE job_id = task.work_id``
#        predicate that refuses every claim when queued JobItems exist).
#   2. tests/job_queue/test_lock_repository.py
#      — JobLock primitives (acquire/release/observe/serial integrity).
#   3. tests/job_queue/test_seam_invariants.py
#      — cross-component seam invariants (admission/lock/task boundaries).
#
# This pack is the single-source-of-truth for the claim-side blast radius of
# the admission-starvation fix. If ``claim_pending_task`` semantics or the
# lock/admission seam drifts, this pack fails.
#
# Expected test count: across the three files (varies by file; do not pin).
# Timeout: 3 minutes (180s) — repo-layer pack (no PG, no live daemon).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: claim_guard_locks_unit_test ==="

cd "$PROJECT_DIR"

timeout 180s .venv/bin/pytest \
  tests/message_queue_redesign/test_task_repository.py \
  tests/job_queue/test_lock_repository.py \
  tests/job_queue/test_seam_invariants.py \
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
