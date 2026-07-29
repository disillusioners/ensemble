#!/usr/bin/env bash
# Test Pack: retry_versioned_agent_unit_test
# Tests: tests/job_queue/test_retry_versioned_agent.py
# Timeout: 2 minutes (120s)
#
# Covers F8: retry-of-versioned-job preserves agent_dir. New test file (276 lines).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: retry_versioned_agent_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/job_queue/test_retry_versioned_agent.py \
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
