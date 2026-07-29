#!/usr/bin/env bash
# Test Pack: message_job_serialization_unit_test
# Tests: tests/test_message_job_serialization.py
# Timeout: 2 minutes (120s)
#
# Covers message job serialization with agent_tag threading:
#   - Message jobs correctly serialize/deserialize version-tag metadata
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: message_job_serialization_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/test_message_job_serialization.py \
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
