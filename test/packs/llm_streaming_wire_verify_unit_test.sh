#!/usr/bin/env bash
# Test Pack: llm_streaming_wire_verify_unit_test — wire-level SSE verification for streaming activation (CF 524 fix)
#   Real ThinkingChatOpenAI + clean_llm_config against httpx.MockTransport (in-process, no network/ports):
#   V1 wire flag (plain/reasoning/tool flows), V2 streaming vs non-streaming semantic equivalence,
#   V3 tool-call delta aggregation, V4 startup opt-out via __main__.main + api.lifespan,
#   V5 env coercion edges, V6 clobber-safety at wire level.
# Timeout: 2 minutes (120s)
# Internal timer: 110s (leaves 10s buffer for cleanup)
set -euo pipefail

INTERNAL_TIMEOUT=110
PACK_NAME="llm_streaming_wire_verify_unit_test"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ${PACK_NAME} ==="

cd "$PROJECT_DIR"

# Run the tests with internal timeout
timeout ${INTERNAL_TIMEOUT}s .venv/bin/pytest \
  tests/unit/test_llm_streaming_wire_verify.py \
  --tb=short -q \
  --override-ini="addopts=" \
  --override-ini="timeout=120" \
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
