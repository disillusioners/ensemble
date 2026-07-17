#!/usr/bin/env bash
# Test Pack: loop_breaker_integration_test — Full integration: detection+repair+agent_node+cleanup
# Timeout: 5 minutes (300s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: loop_breaker_integration_test ==="

cd "$PROJECT_DIR"

timeout 300s .venv/bin/pytest \
  tests/test_loop_breaker_integration.py \
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