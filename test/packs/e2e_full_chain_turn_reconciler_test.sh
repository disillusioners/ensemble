#!/usr/bin/env bash
# Test Pack: e2e_full_chain_turn_reconciler_test — E2E turn reconciler tests
# Timeout: 5 minutes (300s hard cap)
#
# Runs individual tests from tests/e2e/test_full_chain_turn_reconciler.py
# against the live daemon (localhost:8079, started via ./dev.sh).
#
# Usage: bash test/packs/e2e_full_chain_turn_reconciler_test.sh <test_name>
#   where <test_name> is the -k selector (e.g., test_full_chain_claim_process_pause_resume_answer_complete)
#
# Tests must run ONE BY ONE (real LLM calls; combined exceeds 5-min cap per ensure.md).
# This script runs exactly ONE test per invocation.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): bash `timeout 300` outer guard
#   - Layer 2 (script-internal): `PYTEST_TIMEOUT=280` — pytest-timeout inner guard
#
# Prerequisites: daemon must be running on localhost:8079 (start with `./dev.sh`).
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

if [ $# -lt 1 ]; then
    echo "ERROR: Usage: $0 <test_name_k_selector>"
    echo "Example: $0 test_full_chain_claim_process_pause_resume_answer_complete"
    exit 1
fi

TEST_NAME="$1"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: e2e_full_chain_turn_reconciler_test ($TEST_NAME) ==="

cd "$PROJECT_DIR"

PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest \
    tests/e2e/test_full_chain_turn_reconciler.py \
    --override-ini="addopts=" \
    -k "$TEST_NAME" \
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
