#!/usr/bin/env bash
# Test Pack: has_instance_busy_pins_unit_test — pre-existing pin
# suite for ``has_instance_busy`` sister contract (spot-checked
# during the terminal-report wake fix).
#
# Purpose: regression spot-check on the ``has_instance_busy``
# sister-contract unit tests. These tests pre-date the wake-lane
# fix (ee66f0eb) but live in the same code neighborhood
# (``TaskRepository`` claim-side guard) and were explicitly
# re-checked during the fix to ensure the new CASE ranking did
# not perturb the per-instance busy semantics. This pack pins
# them as a single-file backstop for any future
# ``claim_pending_task`` change.
#
# Files covered:
#   1. tests/unit/test_has_instance_busy.py — 16 collected tests
#      (TestHasInflightTaskSisterContract: returns false when only
#      paused; returns true when running; returns true when pending;
#      + 13 sibling contract tests). All SQLite. No PG, no live
#      daemon.
#
# Branch-under-test: feature/fix-terminal-report-wake @ ee66f0eb.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 120s` on the pytest process
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: has_instance_busy_pins_unit_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(has_instance_busy sister-contract regression spot-check)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 120s hard cap on the pytest process.
# 16 collected unit tests, <10s typical; 120s leaves wide margin.
timeout 120s .venv/bin/pytest \
  tests/unit/test_has_instance_busy.py \
  --tb=short -q 2>&1

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