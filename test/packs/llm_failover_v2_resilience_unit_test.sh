#!/usr/bin/env bash
# Test Pack: llm_failover_v2_resilience_unit_test — LLM failover v2 resilience verification (latency caps, sticky-on-success, retry budget exhaustion, nested facade, transient/timeout/IndexError paths)
# Timeout: 5 minutes (300s)
# Internal timer: 290s (leaves 10s buffer for cleanup)
# Target file: tests/unit/test_llm_failover_v2_resilience.py (does not exist yet — script will be used once authored)
set -euo pipefail

INTERNAL_TIMEOUT=290
PACK_NAME="llm_failover_v2_resilience_unit_test"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ${PACK_NAME} ==="

cd "$PROJECT_DIR"

# Run the tests with internal timeout
timeout ${INTERNAL_TIMEOUT}s .venv/bin/pytest \
  tests/unit/test_llm_failover_v2_resilience.py \
  --tb=short -q \
  --override-ini="addopts=" \
  --override-ini="timeout=300" \
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
