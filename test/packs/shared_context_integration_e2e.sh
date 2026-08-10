#!/usr/bin/env bash
# Test Pack: shared_context_integration_e2e — End-to-end injection path
# Timeout: 5 minutes (300s)
#
# Validates the full round-trip: spawn an instance, write a KV via the
# ``shared_context_metadata`` tool, spawn a child instance, and assert
# the child's composed system prompt carries the KV inside the
# ``<shared_context_metadata>`` data fence.
#
# This pack SKIPs under any of these conditions:
#   - ``OPENAI_API_KEY`` is not set (real LLM calls required)
#   - ``config.yaml`` is missing (daemon cannot bootstrap)
#   - the spawned test takes longer than 300s
#
# When SKIPped, the pack exits 0 with ``RESULT: SKIP`` so the gate
# does not turn red on infrastructure-unavailable runs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: shared_context_integration_e2e ==="

cd "$PROJECT_DIR"

# ─── SKIP gates ───────────────────────────────────────────────────────────────
# Mirror the ``pytest.mark.skipif`` guards in test_agent_bootstrap.py so
# the pack-level SKIP is consistent with the test-level SKIP.

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "SKIP: OPENAI_API_KEY is not set — E2E requires real LLM access"
  echo "RESULT: SKIP"
  exit 0
fi

if [ ! -f "$PROJECT_DIR/config.yaml" ]; then
  echo "SKIP: config.yaml not found at $PROJECT_DIR — E2E cannot bootstrap daemon"
  echo "RESULT: SKIP"
  exit 0
fi

# ─── Run the E2E test ──────────────────────────────────────────────────────────
# Uses .venv/bin/pytest because the system pytest is broken on this host.

export TESTING=1

timeout 300s .venv/bin/pytest \
  tests/integration/test_shared_meta_kv_e2e.py \
  -m integration \
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