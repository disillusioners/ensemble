#!/usr/bin/env bash
# test/packs/discard_on_startup_dependency_bus_race_unit_test.sh
#
# Pack: discard_on_startup_dependency_bus_race_unit_test
# Scope: F-1 (durability-f1-f2 / phase1) bus gate + wipe-side
#   predicate — reworked per ITERATION-002 Issue-1 + Issue-2
#   evidence bar (plan §1, §1a, §2, §13b, §13c):
#   * S1-S7 use REAL TaskRepository.clear_all SQL on a real
#     DB session (file-backed SQLite, F9 parity harness) —
#     NOT a Python re-implementation.
#   * Bus logic tests (S2, S3) stay mock-level
#     (DependencyBus does not require SQL).
#   * Real two-boot test (S1) exercises clear_all +
#     mark_task_auto_continued twice in sequence on a real
#     DB session (no double-continue across restarts).
#   * FP1 JobItem-anchor clause keep-green pin
#     (test_fp1_jobitem_anchor_clause_keeps_pending_task).
#
# Tests in tests/unit/services/test_discard_on_startup_dependency_bus_race.py:
#   * _has_truthy_error helper (plan §1.2) — 5 tests
#   * test_none_error_does_not_flip_parent_error (S2; plan §1)
#   * test_real_error_flips_parent_error (S3; plan §1)
#   * test_terminal_auto_continued_survives_clear (S4; plan §2 — REAL SQL)
#   * test_terminal_no_marker_deleted_by_clear (S5; plan §2 — REAL SQL)
#   * test_boot_sequence_arm3_survival_pin (S6; plan §2 — REAL SQL)
#   * test_double_restart_no_double_continue (S1; plan §2 — REAL two-boot SQL)
#   * test_boot_auto_continued_preserve_kill_switch (S7; W-3 / §13c
#     — pins BOTH ON (preserve) AND OFF (delete) paths for
#     ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE — REAL SQL)
#   * test_fp1_jobitem_anchor_clause_keeps_pending_task (FP1 keep-green
#     pin — REAL SQL)
#
# Queue-side 2-arm disjunction coverage (plan-overview S5, currently
# zero coverage per the approver's Issue-1 observation):
#   * tests/unit/repositories/test_message_queue_clear_all_2arm_disjunction.py
#     — test_message_queue_clear_all_2arm_disjunction (REAL SQL on
#     MessageQueueRepository.clear_all)
#
# Internal watchdog (Layer 2): 150s — unit-type limit per test-pack
# skill. The reworked suite is larger than the prior 12-test pack
# (the real-SQL tests are heavier than the Python re-implementation)
# so the inner timeout is raised from 110s to 150s. The actual
# observed runtime is ~5-6s on the worktree; 150s leaves ample
# headroom for slower environments.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Transparent wrapper: no test deselection, no modification; inner pytest
# exit code is propagated as-is. Mirrors the
# auto_continue_boot_pass_unit_test pack wrapper (verbatim structure).

set -u
cd "$(dirname "$0")/../.." || {
    echo "FAIL: cannot cd to repo root"
    echo "RESULT: FAIL"
    exit 1
}

PACK_NAME="discard_on_startup_dependency_bus_race_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
EXIT_CODE=0
timeout 150s .venv/bin/pytest \
    tests/unit/services/test_discard_on_startup_dependency_bus_race.py \
    tests/unit/repositories/test_message_queue_clear_all_2arm_disjunction.py \
    --tb=short -q 2>&1 | tee "$OUT"
EXIT_CODE=$?
rm -f "$OUT"

echo
if [ "$EXIT_CODE" -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
elif [ "$EXIT_CODE" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL (exit=${EXIT_CODE})"
    exit 1
fi
