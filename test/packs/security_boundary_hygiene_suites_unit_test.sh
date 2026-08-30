#!/usr/bin/env bash
# Test Pack: security_boundary_hygiene_suites_unit_test — security-boundary-hygiene
# branch suites (reserved-origin contract + dict-attr 500 fix + PG param-type pins)
# (feature/security-boundary-hygiene @ a77647bf, range ba55eabc..a77647bf)
# Created: 2026-08-30
# Timeout: 2 minutes 30 seconds (150s) — designed for outer `timeout 300`
#
# Branch-scoped merge gate for the three test files added by the
# feature/security-boundary-hygiene branch. Running all three green is the
# merge-gate view for this branch.
#
#   1. tests/unit/routers/test_source_reservation.py (27 tests)
#      Reserved-origin contract — sources must register before their
#      send_message reservations are honored; non-registered origins fall
#      through to the default path without exposing internal handles
#      (security-boundary-hygiene batch on top of ba55eabc).
#   2. tests/unit/routers/test_message_status_endpoint.py (14 tests)
#      GET /messages/{id}/status dict-attr 500 fix — fallback path must
#      tolerate QueueStats-as-plain-dict, plus three real-path regression
#      tests covering the task-repo lookup exception branch and the
#      stats-key-tolerance contract.
#   3. tests/job_queue/test_pattern_f_bus_pending_param_type.py (4 tests)
#      Pattern (f) PG param-type pins — task_id must travel as `str` across
#      the bus helper seam even when callers pass `int`, preventing the
#      PG text-vs-integer coercion mismatch seen in earlier P2.x fences.
#
# Expected: 45 tests (27 + 14 + 4).
#
# NOT included here (covered elsewhere or out of branch scope):
#   - tests/job_queue/test_seam_invariants.py → owned by
#     wedge_fix_suites_unit_test.sh (reconciler-wedge-fix gate).
#   - tests/job_queue/test_orphan_active_job_recovery.py → owned by
#     orphan_active_job_recovery_suites_unit_test.sh (orphan-recovery gate;
#     header count 21 — pre-batch +2 from 07f1f488).
#   - tests/job_queue/test_w1_retry_child_lineage_conjunct.py,
#     tests/job_queue/test_job_feedback_observer_eventbus_pairing.py,
#     tests/unit/test_child_still_running_defer_bus_terminal.py → covered
#     by orphan_active_job_recovery_suites_unit_test.sh.
#
# Overlap with broad pack: test/packs/job_queue_unit_test.sh runs the
# tests/job_queue/ directory, so file 3 is implicitly covered by the broad
# dir pack. Files 1 and 2 live under tests/unit/routers/, which has no
# existing branch-scoped pack — this script is their dedicated gate.
# Overlap with the broad dir pack is intentional (same pattern as
# orphan_active_job_recovery_suites_unit_test.sh re: job_queue files).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 150s` pytest guard
#
# No deselection: no QUARANTINE.md entries for any of the three files
# (.agents/tester/QUARANTINE.md scanned 2026-08-30 — clean for this scope).
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: security_boundary_hygiene_suites_unit_test ==="

cd "$PROJECT_DIR"

timeout 150s .venv/bin/pytest \
  tests/unit/routers/test_source_reservation.py \
  tests/unit/routers/test_message_status_endpoint.py \
  tests/job_queue/test_pattern_f_bus_pending_param_type.py \
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