#!/usr/bin/env bash
# test/packs/auto_continue_interleaving_unit_test.sh
#
# Pack: auto_continue_interleaving_unit_test
# Scope: AC4 interleaving 6-row matrix pinning
#   (architecture-recommendation.md Focus 3, rows 1-6).
#   - Row 1: WS→CP (same instance) — continue-in-place, claim-guard
#     holds the wake FIFO-behind the continued turn
#   - Row 2: CP→late-WS tick mid-turn — pass schedules first, late
#     wake-tick lands mid-turn; wake stays PENDING (claim-guard held);
#     no re-delivery loop (exactly one wake Task)
#   - Row 3: WS→CP→turn fails — fail_task opens the claim window
#   - Row 4: WS→CP→turn succeeds — Δ1 / D18 r3 call-site terminalizer
#     fires and opens the claim window immediately (no STR reap delay)
#   - Row 5: turn running at +10 min — Δ3 / D24 documented contract
#     (structural grep-proof; no code change)
#   - Row 6: epoch=None STR-reap mid-pass — Δ2 / D19 SKIP path
#   Plus:
#     - M14 row 1: terminalize-early rejection (negative lock-out) +
#       structural source-scan (pass contains no force_cancel /
#       cancel_task / find_stale_running_tasks)
#     - M14 row 2: Δ1 complement (regression-catches accidental
#       removal of Δ1; asserts the call-site gate is present in
#       daemon/manager.py and that the shared complete_task SQL
#       at repository.py:2803 is untouched)
#     - M15: PAUSED / terminal / WC / cancel_requested carve-outs
#     - M16: api.py boot-order placement pin (pass call between
#       sweep_wake_records() and upgrade_journal_sweep.start())
#   Tests in tests/unit/services/test_auto_continue_interleaving.py.
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

PACK_NAME="auto_continue_interleaving_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
    tests/unit/services/test_auto_continue_interleaving.py \
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
