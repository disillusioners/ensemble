#!/usr/bin/env bash
# Test Pack: wedge_fix_suites_unit_test — reconciler wedge-fix branch suites (29898ee2..ae837a98)
# Timeout: 2 minutes (120s) — target < 2 min runtime
#
# Runs ALL test files added/modified by feature/reconciler-wedge-fix:
#   1. tests/unit/test_reconciler_wedge_fix.py (A, 10 tests)
#      - T3 sub-shape (c) carrier-revival, sync + async
#        (TestSubshapeCCarrierRevivalSync / ...Async)
#      - T4 wedge backstop: notice when no carrier, silent with live
#        carrier, idempotent across ticks, SILENT with live children
#        (Y1 children gate, ae837a98: test_wedge_silent_when_live_children_present),
#        notice content pin (TestWedgeBackstop)
#      - T2b ALIVE membership pins (Y2, ae837a98:
#        TestAliveInstanceStatusesMembership — membership + frozenset)
#   2. tests/job_queue/test_seam_invariants.py (M, +329, 68 tests)
#      - T1/T2/T2b Pattern (d) regressions in TestPeriodicDriftReconciler:
#        T1  ::test_reconciler_pattern_d_skips_jobitemless_process_report
#        T2b ::test_reconciler_pattern_d_skips_alive_instance_with_terminal_job
#      - plus the pre-existing seam-invariant suite in the same file.
#
# NOT included here (out of this pack's scope, covered elsewhere):
#   - tests/unit/services/test_waiting_children_watchdog.py → dedicated
#     waiting_children_watchdog_unit_test pack (no double-inclusion).
#   - tests/job_queue/test_job_recovery_service.py (branch touched
#     production job_recovery_service.py but not its test file; part of
#     the council's 158-scoped set, not a branch-added suite).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 120s` pytest guard
#
# No deselection: no QUARANTINE.md entries for either file (2026-08-29).
# Note: pre-existing pack claim_guard_locks_unit_test.sh also runs the
# full seam file — overlap is intentional there, this pack is the
# branch-scoped gate view.
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: wedge_fix_suites_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/unit/test_reconciler_wedge_fix.py \
  tests/job_queue/test_seam_invariants.py \
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
