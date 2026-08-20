#!/usr/bin/env bash
# Test Pack: turn_transitions_reconciler_unit_test — turn transitions + reconciler in-process suites
# Timeout: 3 minutes (180s)
#
# In-process suites (no daemon required):
#   - tests/repositories/test_turn_reconciler.py
#   - tests/property/test_named_transitions.py
#   - tests/property/test_turn_state_machine.py
#   - tests/e2e/test_full_chain_turn_reconciler.py
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 180s` pytest guard
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: turn_transitions_reconciler_unit_test ==="

cd "$PROJECT_DIR"

timeout 180s .venv/bin/pytest \
  tests/repositories/test_turn_reconciler.py \
  tests/property/test_named_transitions.py \
  tests/property/test_turn_state_machine.py \
  tests/e2e/test_full_chain_turn_reconciler.py \
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
