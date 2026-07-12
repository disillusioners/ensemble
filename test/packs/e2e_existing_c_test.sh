#!/usr/bin/env bash
# Test Pack: e2e_existing_c_test — Existing E2E tests 4-5 (wave spawn+defer, pause blocks defer)
# Timeout: 20 minutes (1200s)
#
# Runs tests 4-5 (the longer existing E2E tests) against the live daemon.
# These are regression tests for wave spawning and defer queue behavior.
#
# Prerequisites: daemon must be running on localhost:8079 (./dev.sh)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: e2e_existing_c_test (Tests 4-5) ==="

cd "$PROJECT_DIR"

# ─── SKIP gate: daemon must be running ────────────────────────────────────────
if ! curl -s -m 5 http://localhost:8079/api/health >/dev/null 2>&1; then
    echo "SKIP: Daemon not running on localhost:8079 — start with ./dev.sh"
    echo "RESULT: SKIP"
    exit 0
fi

# ─── Run tests with timeout ───────────────────────────────────────────────────
# Tests 4-5: test_wave_spawn_with_defer_queue, test_pause_blocks_defer_queue
# Estimated: ~650s total (each test 200-240s due to LONG_PROMPT LLM latency)
timeout 1200 .venv/bin/python -m pytest \
    tests/e2e/test_e2e_workflows.py \
    -v --tb=short -s \
    --override-ini="addopts=" \
    -m integration \
    -k "test_wave_spawn_with_defer_queue or test_pause_blocks_defer_queue" \
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
