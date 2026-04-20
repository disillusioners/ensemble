#!/usr/bin/env bash
# Test Pack: compaction_unit_test — Compaction and idle timeout unit tests
# Timeout: 2 minutes (120s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: compaction_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s pytest \
  tests/unit/test_compaction.py \
  tests/unit/test_find_near_instance.py \
  tests/unit/test_graph_retry_integration.py \
  tests/unit/test_llm_error_classifier.py \
  tests/unit/test_response_validation.py \
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
