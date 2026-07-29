#!/usr/bin/env bash
# Test Pack: job_queue_agent_tag_unit_test
# Tests: tests/job_queue/test_idempotent_enqueue.py
# Timeout: 2 minutes (120s)
#
# Covers S3 — job_queue_service version-aware agent_dir resolution:
#   - enqueue() with agent_tag resolves versioned agent_dir
#   - Backward compat: enqueue() without agent_tag (None) falls back to base agent_dir
#   - All callers pass agent_tag correctly
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: job_queue_agent_tag_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/job_queue/test_idempotent_enqueue.py \
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
