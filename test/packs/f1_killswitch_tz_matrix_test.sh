#!/usr/bin/env bash
# Test Pack: f1_killswitch_tz_matrix_test — f1-misfire merge gate,
# scopes 4 (kill-switch env spellings) + 5 (tz correctness).
#
# Branch: feature/f1-misfire-fix @ e6cd5fc8
# Created: 2026-09-01
# Timeout: 3 minutes 20 seconds (200s) — designed for outer `timeout 300`
#
# Coverage:
#   Matrix A — kill-switch env spellings (7 spellings via parametrize;
#              plus 4 bonus truth-table rows in the resolver-only probe)
#   Matrix B — f2 mirror under switch ON/OFF + recovery 33P both-state
#              parity + a-e code-body byte-untouched structural check
#              (git diff hunk intersection probe)
#   Matrix C — 4-shape tz correctness (AWARE STALE, NAIVE STALE,
#              FRESH NAIVE, MALFORMED)
#
# Wrapped dev tests (explicit node IDs, never broad directories):
#   * TestPatternF1KillSwitch::test_kill_switch_off_makes_f1_inert
#     (existing — test_orphan_active_job_recovery.py:3468, validates
#     "0" spelling on the dev fixture; probe re-pins it across the
#     full 7-spelling matrix)
#   * TestPatternF1KillSwitch::test_kill_switch_off_leaves_f2_working
#     (existing — test_orphan_active_job_recovery.py:3533, OFF-state
#     f2 mirror; probe re-pins it as TestF2MirrorUnderSwitch::
#     test_kill_switch_off_leaves_f2_working_wrapped)
#   * TestPatternF1SubtreeAliveGuard::
#     test_f1_zombie_fires_with_tz_naive_stale_tree_activity
#     (existing — test_orphan_active_job_recovery.py:3241, NAIVE
#     STALE shape; probe re-pins it independently as
#     TestTzGuardMatrix::test_naive_stale_tree_activity_zombie_fires)
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 200s` pytest guard
#
# No PACKS.md registration (per the recon: "No PACKS.md registration").
#
# Env overrides (intentional, documented inline):
#   - No SLIM/TestAccel knobs needed for this scope. The probe keeps
#     its own file-backed SQLite fixture per test (via tmp_path), so
#     no shared-engine concurrency concerns.
set -euo pipefail

# ─── SSL cleanup (parity with other job_queue packs) ─────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: f1_killswitch_tz_matrix_test ==="
echo "Branch: feature/f1-misfire-fix @ e6cd5fc8"
echo "Scope: Matrix A (kill-switch env) + Matrix B (f2/a-e parity) + Matrix C (tz shapes)"
echo

cd "$PROJECT_DIR"

# Probe-level tests (Matrices A/B/C).
PROBE_FILE="tests/job_queue/test_f1_killswitch_tz_matrix.py"

# Wrapped dev tests (existing surface — listed explicitly by node ID
# so any future test addition in either file is NOT silently picked up
# by this pack; see the test-pack skill's "single pack" rule).
WRAPPED_DEV_TESTS=(
    "tests/job_queue/test_orphan_active_job_recovery.py::TestPatternF1KillSwitch::test_kill_switch_off_makes_f1_inert"
    "tests/job_queue/test_orphan_active_job_recovery.py::TestPatternF1KillSwitch::test_kill_switch_off_leaves_f2_working"
    "tests/job_queue/test_orphan_active_job_recovery.py::TestPatternF1SubtreeAliveGuard::test_f1_zombie_fires_with_tz_naive_stale_tree_activity"
)

START=$(date +%s)

timeout 200s .venv/bin/pytest \
    "$PROBE_FILE" \
    "${WRAPPED_DEV_TESTS[@]}" \
    --tb=short \
    -q \
    2>&1

EXIT_CODE=$?

END=$(date +%s)
RUNTIME=$((END - START))

if [ $EXIT_CODE -eq 124 ]; then
    echo
    echo "RESULT: TIMEOUT (after ${RUNTIME}s — script-internal 200s cap)"
    exit 124
elif [ $EXIT_CODE -eq 0 ]; then
    echo
    echo "RESULT: PASS (runtime=${RUNTIME}s)"
    exit 0
else
    echo
    echo "RESULT: FAIL (runtime=${RUNTIME}s, pytest_exit=${EXIT_CODE})"
    exit 1
fi
