#!/usr/bin/env bash
# Test Pack: m2_missions_runtime_contract_integration_test — M2 Mission Class probe
# Tests: tests/integration/test_m2_missions_runtime_contract.py (TBD by M2 implementer)
# Timeout: 5 minutes (300s)
#
# M2 Gate probe-pack wrapper. The pytest file is NOT YET COMMITTED — a
# later M2 implementer worker creates it at the exact path above; the
# wrapper pre-registers the slot so the gate pack inventory is complete
# before file land. If the file is absent at run time, pytest will
# report "no tests ran" (collect-only exit 5) and the pack will fail
# by design — that is the pre-registration signal, not a wrapper bug.
#
# Probe scope: M2 missions runtime contract — pinned behavior of the
# mission surface under ON-path flag (kill-switch enabled / post-flip
# state). Complements the OFF-path zero-query probe with a positive
# contract: ON-path accepts a missions payload, drives it through the
# resolver, and the integration tier verifies the runtime contract
# (route status codes, response shape, mirror settlement, dead-letter
# binding per work_resolver.py).
#
# Mission-class feature pack on `feature/mission-class` @ 8eddeb3d.
# Worktree-bound; relies on rev-parse bracket echo for drift guard.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: m2_missions_runtime_contract_integration_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Integration pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
timeout 280s .venv/bin/pytest \
  tests/integration/test_m2_missions_runtime_contract.py \
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
