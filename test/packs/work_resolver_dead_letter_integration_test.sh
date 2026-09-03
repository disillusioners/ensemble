#!/usr/bin/env bash
# Test Pack: work_resolver_dead_letter_integration_test — M2 Mission Class acceptance
# Tests: tests/integration/test_work_resolver_dead_letter_binding.py (4 tests)
# Timeout: 5 minutes (300s)
#
# M2 Gate acceptance pack — work-resolver dead-letter binding (integration).
# Mission-class feature pack on `feature/mission-class` @ 8eddeb3d.
# Worktree-bound; relies on rev-parse bracket echo for drift guard.
#
# M1-amended expectation: S4 dead-letter ON-path surfaces 'dead_letter'
# (doc §8.3:1096) per the work_resolver.py binding contract; this
# integration pin is the binding-level coverage that surfaces the M1
# class-skip defect (M1 gate verdict: S4 fix at 7852aeab).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: work_resolver_dead_letter_integration_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Integration pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
timeout 280s .venv/bin/pytest \
  tests/integration/test_work_resolver_dead_letter_binding.py \
  --tb=short -q -rf 2>&1
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
