#!/usr/bin/env bash
# Test Pack: report_delivery_recovery_regression_unit_test — neighboring
# regression suite for the report-delivery recovery service around the
# ensure_deferred / self-heal fix (commit e9aac370, branch
# feature/fix-report-delivery-ensure-deferred).
#
# Purpose: the fix changed the Lane-2 no_row_backstop semantics (row
# is None now REQUIRES positive evidence). This pack proves the rest
# of the recovery service contract still holds: lane routing, retries,
# per-row error isolation, W6 duplicate skips.
#
# Suite inventory (collect-verified 2026-09-07): 27 tests total —
#   1. tests/unit/test_report_delivery_recovery_service.py — 23 tests
#   2. tests/unit/test_report_delivery_self_heal_zero_row.py — 4 tests
#      (the new fix file; included so the pack is self-contained for
#      the recovery surface)
#
# DELIBERATELY EXCLUDED (PG-marked, need the postgres stack + `-m
# postgres`; run via tests/postgres infra or the PG smoke pack):
#   * tests/postgres/test_report_delivery_recovery_pg.py (PG-marked;
#     deselected without `-m postgres`)
#   * tests/postgres/test_dependency_bus_pg.py (PG-marked)
#   * tests/integration/test_report_delivery_double_delivery_pg.py
#     (14 tests, PG-marked; deselected without `-m postgres`)
# PG-level validation of the fix lives in
# test/packs/ensure_deferred_pg_smoke_integration_test.sh (4 legs).
#
# OVERLAP NOTE: tests/test_dependency_bus.py (68 tests) is NOT here —
# it is already covered by test/packs/completion_regression_test.sh.
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

echo "=== Test Pack: report_delivery_recovery_regression_unit_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(report_delivery_recovery neighboring regression: 27 tests, 2 files)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 280s hard cap on the pytest process.
# set +e around the timed command so the RESULT: FAIL branch below is
# reachable under `set -e`.
set +e
timeout 280s .venv/bin/pytest \
  tests/unit/test_report_delivery_recovery_service.py \
  tests/unit/test_report_delivery_self_heal_zero_row.py \
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
