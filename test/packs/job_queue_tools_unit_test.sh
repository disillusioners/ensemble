#!/usr/bin/env bash
# Test Pack: job_queue_tools_unit_test — job tool layer regression baseline (all tool entry points)
# Timeout: 2 minutes (120s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: job_queue_tools_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/test_job_queue_tools.py \
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