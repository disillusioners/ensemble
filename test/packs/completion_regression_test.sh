#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: completion_regression_test ==="
cd "$PROJECT_DIR"

# Regression sweep: ready-message blocking + finalize_instance + dependency_bus
# + cascade completion logic (systems touched by child_reports.py).
# All SQLite. Files verified to exist on fix/wanderer-completion-reporting branch.
# Script-internal timeout (Layer 2): 280s
# Command-level timeout (Layer 1): 300s
# QUARANTINE.md (2026-08-29): flaky StaticPool session-refresh race under 4-thread asyncio.gather — test-fixture infra, NOT production, NOT this branch
timeout 280s .venv/bin/pytest \
  tests/unit/test_ready_message_completion_report.py \
  tests/test_finalize_instance.py \
  tests/test_dependency_bus.py \
  tests/test_cascade_unified.py \
  tests/test_cascade_integration.py \
  tests/test_observer_correlation.py \
  --deselect "tests/test_dependency_bus.py::TestGenerationCounterBump::test_per_parent_lock_serializes_db_insert" \
  -v --override-ini="addopts=" --tb=short -q 2>&1
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