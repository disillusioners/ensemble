#!/usr/bin/env bash
# test/packs/post_restart_arm_notify_edge_cases_unit_test.sh
#
# Pack: post_restart_arm_notify_edge_cases_unit_test
# Scope: Post-Restart Arm-Notify Phase 3 edge cases (long-downtime
#   double-arm coalescing to ONE wake with a run-list; paused-instance
#   defer (wake delivered, Task held PENDING until resume); terminal-
#   instance revival (wake's enqueue_message triggers the
#   terminal→RUNNING flip for COMPLETED/TERMINATED/ERROR/FAILED);
#   kill-switch re-enable-no-stale-flood (after an OFF period that
#   abandoned records, re-enabling delivers nothing stale). Tests in
#   tests/job_queue/test_post_restart_arm_notify_edge_cases.py.
#
# Internal watchdog (Layer 2): 110s — unit-type limit per test-pack skill.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Transparent wrapper: no test deselection, no modification; inner pytest
# exit code is propagated as-is.

set -u
cd "$(dirname "$0")/../.." || {
    echo "FAIL: cannot cd to repo root"
    echo "RESULT: FAIL"
    exit 1
}

PACK_NAME="post_restart_arm_notify_edge_cases_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
    tests/job_queue/test_post_restart_arm_notify_edge_cases.py \
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
