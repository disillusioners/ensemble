#!/usr/bin/env bash
# test/packs/auto_continue_boot_pass_unit_test.sh
#
# Pack: auto_continue_boot_pass_unit_test
# Scope: Boot auto-continue pass (feature/auto-continue-running-after-restart).
#   - Pass orchestration (selection → checkpoint → resume → CAS stamp)
#   - Kill-switch (ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART)
#   - Boot-epoch None SKIP (Δ2 / D19)
#   - Per-instance / sweep-level / lifespan isolation
#   - Reboot-loop idempotency
#   - Candidate-selection + CAS SQL (M1 / M2 / M3 / M17)
#   - Δ1 / Δ2 / Δ4 / Δ5 units
#   - Structural grep-proofs: zero enqueue_message calls; zero
#     datetime.now in the pass module; pass contains no reaper.
#   - Tests in tests/unit/services/test_auto_continue_boot_pass.py +
#     tests/unit/repositories/test_auto_continue_candidates.py
#   - Delta1 terminalizer call-site gate (M18) in
#     tests/unit/services/test_auto_continue_terminalizer.py
#
# Internal watchdog (Layer 2): 110s — unit-type limit per test-pack skill.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Transparent wrapper: no test deselection, no modification; inner pytest
# exit code is propagated as-is. Mirrors the post_restart_arm_notify_sweep
# pack wrapper (verbatim structure, D15).

set -u
cd "$(dirname "$0")/../.." || {
    echo "FAIL: cannot cd to repo root"
    echo "RESULT: FAIL"
    exit 1
}

PACK_NAME="auto_continue_boot_pass_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
    tests/unit/services/test_auto_continue_boot_pass.py \
    tests/unit/repositories/test_auto_continue_candidates.py \
    tests/unit/services/test_auto_continue_terminalizer.py \
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
