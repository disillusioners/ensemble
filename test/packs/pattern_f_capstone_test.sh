#!/usr/bin/env bash
# Test Pack: pattern_f_capstone_test — Pattern (f) 092c5ed3-class E2E capstone
# (feature/orphan-active-job-recovery @ ba39a40e)
# Created: 2026-08-29
# Timeout: outer 300s guard (Layer 1) + inner 270s signal.alarm (Layer 2)
#
# Branch-scoped E2E capstone for Pattern (f) — runs the composed story
# on REAL engine components (no mocks below the repository/service seam):
# seed BOTH zombie shapes + watchers + defer-queue job C → run ONE real
# reconcile_drift_states → assert shape-1 DEAD, shape-2 DONE, locks
# released, watcher fired, and the defer-queue job ADMITS via a real
# claim (the 092c5ed3 incident class).
#
# Spec: .agents/tester/MOCK_TESTS.md → "E2E Capstone — 092c5ed3-class
# Zombie Active JobItems".
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): signal.alarm(270) inside the script
set -euo pipefail

# ─── SSL cleanup (mirror orphan_active_job_recovery_suites_unit_test.sh)
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: pattern_f_capstone_test ==="

cd "$PROJECT_DIR"

# Outer command-level timeout = 300s (the hard cap from the spec).
# Inner signal.alarm = 270s inside the script (defense in depth).
timeout 300 .venv/bin/python test/packs/pattern_f_capstone_test.py

EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
