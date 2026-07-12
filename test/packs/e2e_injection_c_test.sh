#!/usr/bin/env bash
# Test Pack: e2e_injection_c_test — New injection E2E tests 9-11 (waiting_children, auto_resume, query)
# Timeout: 20 minutes (1200s)
#
# Runs the last 3 new injection E2E tests against the live daemon.
# These validate WAITING_CHILDREN injection (W3), PAUSED auto-resume
# regression guard (C4), and the injection query endpoint lifecycle.
#
# Prerequisites: daemon must be running on localhost:8079 (./dev.sh)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: e2e_injection_c_test (Tests 9-11) ==="

cd "$PROJECT_DIR"

# ─── SKIP gate: daemon must be running ────────────────────────────────────────
if ! curl -s -m 5 http://localhost:8079/api/health >/dev/null 2>&1; then
    echo "SKIP: Daemon not running on localhost:8079 — start with ./dev.sh"
    echo "RESULT: SKIP"
    exit 0
fi

# ─── Run tests with timeout ───────────────────────────────────────────────────
# Tests 9-11: test_injection_into_waiting_children,
#             test_paused_auto_resume_unchanged,
#             test_injection_query_endpoint
# Estimated: ~650s total (each test ~200s due to LONG_PROMPT LLM latency)
timeout 1200 .venv/bin/python -m pytest \
    tests/e2e/test_e2e_workflows.py \
    -v --tb=short -s \
    --override-ini="addopts=" \
    -m integration \
    -k "test_injection_into_waiting_children or test_paused_auto_resume_unchanged or test_injection_query_endpoint" \
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
