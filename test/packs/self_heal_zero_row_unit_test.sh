#!/usr/bin/env bash
# Test Pack: self_heal_zero_row_unit_test — sweep no_row_backstop
# self-heal for zero-row stuck pairs (Debug Phase 4 fix #3, commit
# e9aac370, branch feature/fix-report-delivery-ensure-deferred).
#
# Fix part 3 under test: ReportDeliveryRecoveryService's Lane 2
# (``_run_no_row_backstop_lane``) heals zero-row stuck pairs on the
# next sweep (INSERT → PENDING → reconcile), making the ~300s
# same-triple "racing delivery won" flap structurally impossible:
#   * incident shape (leader b7ead8a4 / giter d90b18f9): parent
#     WAITING_CHILDREN + child COMPLETED with COMPLETED message +
#     ZERO report_injections rows + CANCELLED/unenqueued watcher;
#   * pass 1: pair recovered (row created, DEFERRED→PENDING,
#     recovery_attempted_at stamped, re-enter fired with
#     source="sweep_no_row_backstop");
#   * pass 2: NO candidates (anti-flap), no re-enter, still 1 row;
#   * mutation proof: pre-fix no-op behavior is exercised to show
#     the flap signature would fire if the false positive returned.
#
# NOTE ON PATHS: this file path matches the dispatch verbatim
# (tests/unit/test_report_delivery_self_heal_zero_row.py exists in
# commit e9aac370).
#
# Files covered:
#   1. tests/unit/test_report_delivery_self_heal_zero_row.py — 4 tests
#      (collect-verified 2026-09-07: lane finds zero-row pair,
#      self-heal + anti-flap, flap-signature mutation proof, W6
#      duplicate None → already_recovered skip).
#
# Branch-under-test: feature/fix-report-delivery-ensure-deferred
# (fix commit e9aac370, parent bb052fce).
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

echo "=== Test Pack: self_heal_zero_row_unit_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(sweep self-heal: zero-row pair → INSERT → PENDING → reconcile, anti-flap)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 120s hard cap on the pytest process.
# set +e around the timed command so the RESULT: FAIL branch below is
# reachable under `set -e`.
set +e
timeout 120s .venv/bin/pytest \
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
