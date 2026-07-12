#!/usr/bin/env bash
# Test Pack: e2e_existing_ab_test — Existing E2E tests 1-3 (happy path, pause/resume, terminate/revive)
# Timeout: 20 minutes (1200s)
#
# Runs the first 3 existing E2E workflow tests against the live daemon.
# These are regression tests — they must pass before the new injection
# tests are considered valid.
#
# Prerequisites: daemon must be running on localhost:8079 (./dev.sh)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: e2e_existing_ab_test (Tests 1-3) ==="

cd "$PROJECT_DIR"

# ─── SKIP gate: daemon must be running ────────────────────────────────────────
if ! curl -s -m 5 http://localhost:8079/api/health >/dev/null 2>&1; then
    echo "SKIP: Daemon not running on localhost:8079 — start with ./dev.sh"
    echo "RESULT: SKIP"
    exit 0
fi

# ─── Run tests with timeout ───────────────────────────────────────────────────
# Tests 1-3: test_parent_child_workflow_happy_path, test_pause_after_spawn_then_resume,
#            test_terminate_after_spawn_then_revive
# Estimated: ~650s total (each test 200-240s due to LONG_PROMPT LLM latency)
timeout 1200 .venv/bin/python -m pytest \
    tests/e2e/test_e2e_workflows.py \
    -v --tb=short -s \
    --override-ini="addopts=" \
    -m integration \
    -k "test_parent_child_workflow_happy_path or test_pause_after_spawn_then_resume or test_terminate_after_spawn_then_revive" \
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
