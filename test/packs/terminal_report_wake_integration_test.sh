#!/usr/bin/env bash
# Test Pack: terminal_report_wake_integration_test — claim lane proof
# + wake-vs-claim exactly-once races for the PROCESS_REPORT wake
# lane (Debug Phase 4 fix #1, ee66f0eb).
#
# Purpose: single-pytest-invocation regression over the two new
# integration files that pin the priority-lane invariant. Both
# files use file-backed SQLite at ``tmp_path`` with NullPool +
# WAL + busy_timeout=10000 per the project Testing & QC
# conventions — no PG, no live daemon, no production DB.
#
# Files covered:
#   1. tests/integration/test_report_wake_priority_claim.py — 5 tests
#      (report task claims ahead of older PENDING under saturation;
#      FIFO preserved within process_message tier; FIFO preserved
#      between two report tasks; pause gate still dominates the
#      wake lane; busy instance still blocks sibling report claim).
#   2. tests/integration/test_wake_vs_claim_exactly_once.py — 4 tests
#      (wake-lane claim vs natural claim single winner; task
#      delivery vs live drain exactly one terminal transition;
#      double task-delivery claim exactly once; delivery obligation
#      is write-once).
#
# Why a single pytest invocation: the two files share the same
# ``engine`` fixture shape (file-backed SQLite + NullPool), and
# pytest's tmp_path state is clean per-function. Running them
# together keeps the budget under 280s while exercising both the
# ranking and the exactly-once arbitration paths.
#
# Branch-under-test: feature/fix-terminal-report-wake @ ee66f0eb.
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

echo "=== Test Pack: terminal_report_wake_integration_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(claim lane proof + wake-vs-claim exactly-once races)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 280s hard cap on the pytest process.
# 9 tests total (5 + 4), realistic run <30s; 280s is margin-rich.
timeout 280s .venv/bin/pytest \
  tests/integration/test_report_wake_priority_claim.py \
  tests/integration/test_wake_vs_claim_exactly_once.py \
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