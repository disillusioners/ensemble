#!/usr/bin/env bash
# Test Pack: gii_throttle_regression_test — GII throttle + loop breaker coexistence regression
# Timeout: 2 minutes (120s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: gii_throttle_regression_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/test_gii_throttle.py \
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