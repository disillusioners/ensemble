#!/usr/bin/env bash
# ri_guard_enforcement_unit_test.sh
#
# Scope 4+5 — P2 report-integrity (b)-guard predicate exactness + dormant
# enforcement correctness gate (branch feature/wc-wake-report-integrity @ f8c5ce8f
# + test-only gate commits ddfc5fc6/f96a239f/22a6df4b; HEAD = 22a6df4b).
#
# Wraps FIVE test files in a SINGLE pytest invocation (per W1 lesson:
# module-global caches like _B_NOTICE_LEDGER / _B_GUARD_ENABLED /
# DependencyBus singleton are reset by per-file autouse fixtures —
# test_b_fail_open.py:_reset_notice_ledger (autouse),
# test_report_integrity_guard.py:_flag_on/_flag_off (manual but
# applied at every TestEnforcementAction/TestKillSwitchRevertProof
# test), wire_bus teardown):
#
#   * tests/unit/services/test_report_integrity_guard.py (34 tests) —
#     PRIMARY signal (PENDING/DEFERRED + terminal child), CORROBORATING
#     signal (FIRED ∧ enqueued_at IS NULL), same-tx repo variant, B.S.7
#     bus/tasks gates, log helper (B.S.1-ii), enforcement action
#     (B.S.1-iii — notice content contract + dedupe + episode close +
#     kill-switch revert), S1 marker-citation gate.
#
#   * tests/unit/services/test_b_fail_open.py (9 tests) — 4 fail-OPEN
#     playbooks: predicate raises → completion proceeds;
#     malformed result → completion proceeds; 5s budget → completion
#     proceeds (NOTICE_ENQUEUE_BUDGET_SECONDS == 5.0 pinned);
#     enqueue exception → completion proceeds. Plus None-report and
#     empty-report no-op edges + log-helper return-value hand-off.
#
#   * tests/unit/services/test_b_kill_switch_registry.py (11 tests) —
#     (b) env-name pinned in constants.py, config wiring (default
#     falsy = LOG-ONLY ship state C2-D2.5), env-name derivation
#     (no literal fork between config.py and constants.py), env
#     flip reaches fresh settings instance, resolver reads the
#     constant; (a) reserved-unused (constants exists, no config
#     field, only-occurrence-in-daemon scan); S8 split-versioning
#     independence (marker-suppressed prompt guidance still intact;
#     predicate/enforcement does NOT read SANITY_FLAG_VERSION —
#     bounded to constants_marker_text() citation helper).
#
#   * tests/unit/services/test_observer_finalize_no_job.py (11 tests) —
#     Post-D13 _finalize_job_db_sync(job_id=None) transitions; the
#     observer-finalize-jobs site integration shape. Stage-ii log
#     fires at observer_finalize_job stamp (parent-COMPLETED with
#     terminal child + PENDING → one [ReportIntegrityGuard] line);
#     healthy silent; ERROR stamp does NOT log; **bus-pending > 0
#     (early + in-session) and tasks-pending > 0 (in-session) gates
#     skip predicate** (B.S.7 ordering: bus > tasks > (b)).
#
#   * tests/unit/services/test_w2_dead_site_symmetry.py (4 tests) —
#     W2 dead-site symmetry attaches (council 2026-08-30):
#     _finalize_instance_db_sync attach (incident shape fires
#     observer_finalize_instance_db_sync context_tag); dead-twin
#     else-branch attach verified by source-grep (bus=None raises
#     A8 BEFORE control reaches else-branch).
#
# Total: 69 tests (per dev review reference).
#
# Why ONE pytest invocation (not five separate commands): the test
# harness W1 lesson is "if the 5 files share module-global caches,
# running them in ONE pytest process must stay green (report if
# ordering matters)" — and the per-file autouse fixtures
# (_reset_notice_ledger, bus teardown, _flag_on/_flag_off) are
# designed for shared-process collection.
#
# Constraints honored (test-pack skill, MANDATORY):
#   * Single pack — this is the ONLY pack we run.
#   * Dual-layer timeout — script-internal 180s (Layer 2), caller wraps
#     with `timeout 300` (Layer 1, ≤ 5 min hard cap). Estimated runtime
#     ~30-60s; 180s leaves 3× headroom.
#   * Test code only — no production touchpoints.
#   * daemon/ is read-only — never executes the daemon.
#   * No -x (continue on first failure — we want the full count).
#   * Single-process collection (no -n auto).
#
# Expected: 69P/0F (dev review reference). Exit 0=PASS, 1=FAIL, 124=TIMEOUT.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: ri_guard_enforcement_unit_test ==="
echo "Surface: 5 test files, 69 tests (scope-4 predicate exactness +"
echo "         scope-5 dormant enforcement)"
echo "Files:"
echo "  - tests/unit/services/test_report_integrity_guard.py (34)"
echo "  - tests/unit/services/test_b_fail_open.py (9)"
echo "  - tests/unit/services/test_b_kill_switch_registry.py (11)"
echo "  - tests/unit/services/test_observer_finalize_no_job.py (11)"
echo "  - tests/unit/services/test_w2_dead_site_symmetry.py (4)"
cd "$PROJECT_DIR"

# Script-internal timeout guard (Layer 2): 180s — interrupts hung tests.
# Command-level timeout (Layer 1): caller wraps with `timeout 300`.
timeout 180s .venv/bin/pytest \
  tests/unit/services/test_report_integrity_guard.py \
  tests/unit/services/test_b_fail_open.py \
  tests/unit/services/test_b_kill_switch_registry.py \
  tests/unit/services/test_observer_finalize_no_job.py \
  tests/unit/services/test_w2_dead_site_symmetry.py \
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