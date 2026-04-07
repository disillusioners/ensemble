#!/usr/bin/env bash
# Test Pack: api_unit_test — API and adapter unit tests
# Timeout: 2 minutes (120s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: api_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s pytest \
  tests/test_api.py \
  tests/test_scheduler_adapter.py \
  tests/test_scheduler_api.py \
  tests/test_scheduler_instance_mode.py \
  tests/test_spawn_instance_instructive_errors.py \
  tests/test_spawn_instance_validation.py \
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
