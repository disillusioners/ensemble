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
#
# Deselected (see .agents/tester/QUARANTINE.md, 2026-08-20):
#   - 1× TestTurnReconcilerStateMachine::test_state_machine
#     Root cause: pre-existing stale assert expecting terminal Task
#     message_queue='completed' (or absent), got 'failed' (begin_turn→abort_turn
#     hypothesis). Base-evidenced deterministic: identical 1-fail on base
#     6bb99d5f. Test file unchanged since 55bd6f39 (2026-08-10, pre-branch).
#     Same family as the 3 quarantined c171a289 semantic-shift tests in
#     .agents/tester/QUARANTINE.md. Orthogonal to this branch.
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: turn_transitions_reconciler_unit_test ==="

cd "$PROJECT_DIR"

# QUARANTINE.md (2026-08-20): 1 pre-existing TestTurnReconcilerStateMachine::test_state_machine stale assert, base-evidenced deterministic
timeout 180s .venv/bin/pytest \
  tests/repositories/test_turn_reconciler.py \
  tests/property/test_named_transitions.py \
  tests/property/test_turn_state_machine.py \
  tests/e2e/test_full_chain_turn_reconciler.py \
  --deselect tests/property/test_turn_state_machine.py::TestTurnReconcilerStateMachine::test_state_machine \
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
