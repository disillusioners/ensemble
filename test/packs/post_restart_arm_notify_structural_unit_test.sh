#!/usr/bin/env bash
# test/packs/post_restart_arm_notify_structural_unit_test.sh
#
# Pack: post_restart_arm_notify_structural_unit_test
# Scope: Post-Restart Arm-Notify Phase 3 AC6 structural non-regression
#   pins: no new journal file in <install_dir>/releases/; no new HTTP
#   endpoint (router URL prefix list UNCHANGED); no new SQLModel table
#   (metadata table list UNCHANGED); the `arm_pending_wake` call site
#   sits INSIDE the lock-holding `try` at BOTH arm sites (architecture
#   delta #3 — silent torn-write regression pin); the wired helper
#   `_arm_pending_wake_for_op` is used (r4 fold C2); `sweep_wake_records`
#   is a bound method on the service (r4 fold W3); test fixtures use
#   the REAL journal history shape `{ts, event, detail}` (r4 fold C1).
#   Tests in tests/unit/test_post_restart_arm_notify_no_parallel.py.
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

PACK_NAME="post_restart_arm_notify_structural_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
    tests/unit/test_post_restart_arm_notify_no_parallel.py \
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
