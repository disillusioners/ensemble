#!/usr/bin/env bash
# Test Pack: e2e_injection_ab_test — New injection E2E tests 6-8 (consumed, cleared, replacement)
# Timeout: 20 minutes (1200s)
#
# Runs the first 3 new injection E2E tests against the live daemon.
# These validate the core injection flow, pause-clear behavior (W6),
# and replacement semantics.
#
# Prerequisites: daemon must be running on localhost:8079 (./dev.sh)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: e2e_injection_ab_test (Tests 6-8) ==="

cd "$PROJECT_DIR"

# ─── SKIP gate: daemon must be running ────────────────────────────────────────
if ! curl -s -m 5 http://localhost:8079/api/health >/dev/null 2>&1; then
    echo "SKIP: Daemon not running on localhost:8079 — start with ./dev.sh"
    echo "RESULT: SKIP"
    exit 0
fi

# ─── Run tests with timeout ───────────────────────────────────────────────────
# Tests 6-8: test_injection_consumed_by_running_instance,
#            test_injection_cleared_on_pause,
#            test_injection_replacement
# Estimated: ~650s total (each test 200-240s due to LONG_PROMPT LLM latency)
timeout 1200 .venv/bin/python -m pytest \
    tests/e2e/test_e2e_workflows.py \
    -v --tb=short -s \
    --override-ini="addopts=" \
    -m integration \
    -k "test_injection_consumed_by_running_instance or test_injection_cleared_on_pause or test_injection_replacement" \
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
