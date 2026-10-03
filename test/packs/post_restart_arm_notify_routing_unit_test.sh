#!/usr/bin/env bash
# test/packs/post_restart_arm_notify_routing_unit_test.sh
#
# Pack: post_restart_arm_notify_routing_unit_test
# Scope: Post-Restart Arm-Notify Phase 2 routing surface (the wake's
#   ``source`` matches the recorded source verbatim; the user-origin
#   window is re-stamped for the wake turn; the empty-source fall-back
#   uses the ``"api"`` sentinel; the metadata carries the system_context
#   the report-delivery path inspects). Tests in tests/job_queue/test_
#   post_restart_arm_notify_routing.py.
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

PACK_NAME="post_restart_arm_notify_routing_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
    tests/job_queue/test_post_restart_arm_notify_routing.py \
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