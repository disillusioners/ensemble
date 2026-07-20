#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: child_reports_unit_test ==="
cd "$PROJECT_DIR"

# Script-internal timeout guard (Layer 2): 110s — interrupts hung tests
# Command-level timeout (Layer 1): 120s via `timeout` wrapper below (unit pack ≤ 2 min)
timeout 110s .venv/bin/pytest \
  tests/unit/services/test_child_reports.py \
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
