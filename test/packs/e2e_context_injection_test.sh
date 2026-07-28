#!/usr/bin/env bash
# === Test Pack: e2e_context_injection_test ===
# Verifies hybrid context injection: project context persistent (first-message-only),
# skills ephemeral (re-injected each turn).
# Requires: daemon running on localhost:8079, real LLM API key.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTEST="${SCRIPT_DIR}/../../.venv/bin/pytest"

cd "${SCRIPT_DIR}/../.."

timeout 300 "${PYTEST}" tests/e2e/test_context_injection_hybrid.py \
    --override-ini="addopts=" \
    --override-ini="timeout=280" \
    -m integration \
    -s --tb=short -q \
    2>&1

echo "=== Test Pack: e2e_context_injection_test ==="
echo "RESULT: PASS"
exit 0
