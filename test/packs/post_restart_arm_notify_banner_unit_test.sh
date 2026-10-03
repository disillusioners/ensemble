#!/usr/bin/env bash
# test/packs/post_restart_arm_notify_banner_unit_test.sh
#
# Pack: post_restart_arm_notify_banner_unit_test
# Scope: Post-Restart Arm-Notify Phase 4 T4.7 banner-text regression pin
#   (phase4-plan D5, r4 fold W3 sixth pack): the arm-return banners in
#   daemon/tools/upgrade_tools.py carry the auto-wake prose (both
#   system_restart + system_upgrade), the obsolete pull-model
#   "ask me to run `upgrade_status`" instruction is ABSENT (D-FA1.2
#   supersession close-out), and the kill-switch env doc (ADR-044) is
#   on every banner line. Tests in
#   tests/unit/tools/test_post_restart_arm_notify_banner.py.
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

PACK_NAME="post_restart_arm_notify_banner_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
    tests/unit/tools/test_post_restart_arm_notify_banner.py \
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
