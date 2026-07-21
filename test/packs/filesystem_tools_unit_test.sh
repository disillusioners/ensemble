#!/usr/bin/env bash
# Test Pack: filesystem_tools_unit_test — end-to-end filesystem tool tests
# Timeout: 2 minutes (120s)
# Internal timer: 110s (leaves 10s buffer for cleanup)
set -euo pipefail

INTERNAL_TIMEOUT=110
PACK_NAME="filesystem_tools_unit_test"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ${PACK_NAME} ==="

cd "$PROJECT_DIR"

# Run the tests with internal timeout
timeout ${INTERNAL_TIMEOUT}s .venv/bin/pytest \
  tests/test_tools.py \
  --tb=short -q \
  --override-ini="addopts=" \
  2>&1
EXIT_CODE=$?

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
