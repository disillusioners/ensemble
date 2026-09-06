#!/usr/bin/env bash
# Test Pack: terminal_report_wake_unit_test — bus regression for the
# terminal-emission path on the wake-lane fix (Debug Phase 4 fix #1,
# ee66f0eb).
#
# Purpose: single-file regression over the terminal-emission bus
# semantics that back the parent's PROCESS_REPORT wake. The
# ``child_reports.py`` truthful-log correction (replacing the
# misleading "bus callback owns completion" text) is documented
# alongside the task-id mismatch proof (task 7807e521 / d77727cf):
#
#   * the bus is a pure state machine — it flips PENDING watchers to
#     FIRED (bookkeeping only) and never wakes the parent;
#   * the parent's terminal transition rides the natural
#     PROCESS_REPORT task delivery (claim_for_task_delivery / live
#     drain / JobFeedbackObserver._process_event);
#   * ``TaskRepository.claim_pending_task`` carries the priority
#     lane that decides how soon that report is claimed.
#
# These 4 unit tests pin the bus-side surface so a future "wake via
# bus callback" regression is caught at PR-time without spinning PG
# or the live daemon.
#
# Files covered:
#   1. tests/unit/services/test_terminal_report_wake_bus.py — 4 tests
#      (task-id fires parent watchers via pair-emit, duplicate
#      emission is exactly-once, emitted outcome is status-agnostic,
#      bus singleton absent is fail-safe).
#
# Branch-under-test: feature/fix-terminal-report-wake @ ee66f0eb
# (parent 77ce4ae8, 13 tests across 3 new files).
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

echo "=== Test Pack: terminal_report_wake_unit_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(bus regression: terminal-emission + task-id mismatch + exactly-once)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 120s hard cap on the pytest process.
# 4 unit tests, <5s typical; 120s leaves wide margin for CI cold-start.
timeout 120s .venv/bin/pytest \
  tests/unit/services/test_terminal_report_wake_bus.py \
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