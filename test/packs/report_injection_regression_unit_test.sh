#!/usr/bin/env bash
# Test Pack: report_injection_regression_unit_test — neighboring
# regression suite for the report_injection repository around the
# ensure_deferred INSERT-ON-MISSING fix (commit e9aac370, branch
# feature/fix-report-delivery-ensure-deferred).
#
# Purpose: the fix rewrote ensure_deferred's write path (terminal
# pre-check, insert-on-missing, guarded in-place reason UPDATE). This
# pack proves the PRE-EXISTING repository contract still holds:
# claim guards, exactly-once delivery, DEFERRED→PENDING transitions,
# migration parity.
#
# Suite inventory (collect-verified 2026-09-07): 61 tests total —
#   1. tests/repositories/test_report_injection.py — 35 tests
#   2. tests/repositories/test_report_injection_migration_parity.py — 26 tests
# 61 << 300 → single script, NO split needed.
#
# Branch-under-test: feature/fix-report-delivery-ensure-deferred
# (fix commit e9aac370, parent bb052fce).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 280s` on the pytest process
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: report_injection_regression_unit_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(report_injection repository neighboring regression: 61 tests, 2 files)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 280s hard cap on the pytest process.
# set +e around the timed command so the RESULT: FAIL branch below is
# reachable under `set -e`.
set +e
timeout 280s .venv/bin/pytest \
  tests/repositories/test_report_injection.py \
  tests/repositories/test_report_injection_migration_parity.py \
  --tb=short -q 2>&1
EXIT_CODE=$?
set -e

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
