#!/usr/bin/env bash
# Test Pack: orphan_active_job_recovery_suites_unit_test — Pattern (f) orphan
# ACTIVE-JobItem recovery + child_still_running_defer bus-emit + observer
# eventbus pairing diagnostics
# (feature/orphan-active-job-recovery @ ba39a40e, range b4dbfda2..ba39a40e)
# Created: 2026-08-29
# Timeout: 2 minutes 30 seconds (150s) — designed for outer `timeout 300`
#
# Branch-scoped merge gate for the four test files added by the
# feature/orphan-active-job-recovery branch on top of the Pattern (f) council
# work. Running all four green is the merge-gate view for this branch.
#
#   1. tests/job_queue/test_orphan_active_job_recovery.py (21 tests)
#      Pattern (f) orphan ACTIVE JobItem recovery (44d5b4cf) +
#      Pattern (f) council criticals (dee03665):
#        - f1 strict restart-wipe-dead predicate
#        - f2 missed-finalize-done + lock release + bus-pending gate + age floor
#        - healthy-shape exclusion (live ACTIVE JobItem stays ACTIVE)
#        - 15-min configurable grace period
#        - W1 mid-mint guard (regression)
#   2. tests/job_queue/test_w1_retry_child_lineage_conjunct.py (3 tests)
#      Pattern (f) W1-W4 council warnings — retry-child lineage conjunct +
#      test record honesty (ba39a40e):
#        - retry-child lineage conjunction with f1/f2 predicate
#        - test-record honesty assertions (no synthetic-job-id leakage)
#   3. tests/job_queue/test_job_feedback_observer_eventbus_pairing.py (9 tests)
#      Observer eventbus pairing diagnostics (68dd944d):
#        - restart empty-queue anomaly documented (not fixed)
#        - feedback-observer ↔ eventbus pairing invariants
#        - 97103462 backlog surfaced via diagnostics
#   4. tests/unit/test_child_still_running_defer_bus_terminal.py (8 tests)
#      child_still_running_defer fires bus terminal emits (ca9263c2,
#      unwatch 02fb2e01) + defer double-emit idempotency (c16b21e8, W4):
#        - WHERE-state guard delivers exactly one FollowUp
#        - bus terminal emit on defer completion
#        - no double-emit on re-entry
#
# Expected: 41 tests (21 + 3 + 9 + 8).
#
# NOT included here (covered elsewhere or out of branch scope):
#   - tests/job_queue/test_seam_invariants.py → owned by
#     wedge_fix_suites_unit_test.sh (branch-feature/reconciler-wedge-fix gate).
#   - tests/job_queue/test_job_recovery_service.py → council 158-scoped set,
#     not a branch-added file (production code touched, test unchanged).
#   - tests/unit/services/test_waiting_children_watchdog.py → dedicated
#     waiting_children_watchdog_unit_test pack.
#
# Overlap with broad pack: test/packs/job_queue_unit_test.sh runs the
# tests/job_queue/ directory, so files 1, 2, 3 are implicitly covered by the
# broad dir pack. File 4 (tests/unit/test_child_still_running_defer_bus_terminal.py)
# has NO existing pack coverage — this script is its dedicated gate.
# Overlap with the broad dir pack is intentional (same pattern as
# wedge_fix_suites_unit_test.sh re: seam_invariants).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 150s` pytest guard
#
# No deselection: no QUARANTINE.md entries for any of the four files
# (.agents/tester/QUARANTINE.md scanned 2026-08-29 — clean for this scope).
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: orphan_active_job_recovery_suites_unit_test ==="

cd "$PROJECT_DIR"

timeout 150s .venv/bin/pytest \
  tests/job_queue/test_orphan_active_job_recovery.py \
  tests/job_queue/test_w1_retry_child_lineage_conjunct.py \
  tests/job_queue/test_job_feedback_observer_eventbus_pairing.py \
  tests/unit/test_child_still_running_defer_bus_terminal.py \
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
