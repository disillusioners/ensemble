#!/usr/bin/env bash
# test/packs/post_restart_arm_notify_journal_unit_test.sh
#
# Pack: post_restart_arm_notify_journal_unit_test
# Scope: Post-Restart Arm-Notify Phase 1 (arm-side durable record +
#   journal extension + lifecycle helpers) — daemon/tools/upgrade_journal.py
#   surface (``PendingWake`` schema, ``arm_pending_wake``, ``mark_wake_*``
#   helpers, ``list_pending_wakes``, ``latest_matching_event`` walker,
#   ``pending_wakes`` key on ``releases/state.json``, ``clear_pending_op``
#   + simulated ``restart.sh`` non-interference, ``reconcile_pending_op``
#   non-interference, arm-side live-outright-refusal). Daemon-only —
#   shell side unchanged. Tests in tests/unit/tools/test_post_restart_arm_
#   notify_journal.py.
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

PACK_NAME="post_restart_arm_notify_journal_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
    tests/unit/tools/test_post_restart_arm_notify_journal.py \
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