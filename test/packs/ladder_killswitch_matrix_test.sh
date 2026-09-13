#!/usr/bin/env bash
# Test Pack: ladder_killswitch_matrix_test
# Phase-1 verification + 2×2 (loop×empty-guard) joint matrix + partition/canary/doc invariants.
#   - P1e kill-switch OFF byte-identity (daemon-side flag read)
#   - P1f 2×2 matrix (loop-ladder × empty-guard), 4 arms incl. W1 hardening
#   - P1-R2 canary + P-10 mid-superstep invariants
#   - T-11 doc invariants (id / hoist / verbatim)
#   - K5 joint integration: loop storm DURING continuous-empty provider
#
# Dual-layer timeout: inner 180s (pack-internal hard ceiling) + outer 300s (command-level cap).
# Run ONLY this pack; do not run any other pack or suite.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ladder_killswitch_matrix_test ==="

cd "$PROJECT_DIR"

# Pack-internal timer (Layer 2). SIGTERM the pytest tree at 180s so the
# outer 300s cap is a true backstop, not the only line of defense.
( sleep 180; kill -TERM -$$ 2>/dev/null ) &
INTERNAL_WATCHDOG_PID=$!

cleanup_internal() {
  if kill -0 "$INTERNAL_WATCHDOG_PID" 2>/dev/null; then
    kill "$INTERNAL_WATCHDOG_PID" 2>/dev/null || true
  fi
}
trap cleanup_internal EXIT

# Layer 1: command-level cap.
timeout 300s .venv/bin/pytest \
  tests/unit/test_symptom_repair_partition.py \
  tests/unit/test_symptom_repair_mid_superstep_canary.py \
  tests/unit/test_symptom_repair_doc.py \
  tests/test_ladder_loop_x_empty_guard_integration.py \
  --tb=short -q \
  --override-ini="addopts=" \
  2>&1

EXIT_CODE=$?

cleanup_internal

if [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
elif [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
else
  echo "RESULT: FAIL"
  exit 1
fi