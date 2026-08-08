#!/usr/bin/env bash
# Test Pack: mock_job_queue_test — Mock job queue API test
# Timeout: 5 minutes (300s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: mock_job_queue_test ==="

cd "$PROJECT_DIR"

# Run with PYTHONPATH set so the bare ``python tests/...`` invocation
# can import the ``daemon`` package (other packs use ``.venv/bin/pytest``
# which already handles this; this script is run via raw ``python``).
PYTHONPATH="$PROJECT_DIR" timeout 300s .venv/bin/python tests/mock_test_job_queue_api.py 2>&1

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
