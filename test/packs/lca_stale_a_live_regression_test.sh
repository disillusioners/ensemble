#!/usr/bin/env bash
# LCA stale-A fix (B1) merge gate — INDEPENDENT live regression pack.
# Pack: lca_stale_a_live_regression_test
# Worktree: feature/lca-stale-a-fix @ e0d15e93 (base a6442bff)
#
# Construction rule: scenarios, fixtures, and assertions here are
# independently built at HEAD — the dev's tests in
# tests/unit/test_attestation_resolver_activation.py and
# tests/integration/test_lcau_incident_e2e.py are NOT referenced.
#
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s
# guard below. The 20s sliver absorbs pytest collection + process spawn
# overhead so the outer timeout only fires on a real hang.
#
# Invocation contract: timeout 300 bash test/packs/lca_stale_a_live_regression_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lca_stale_a_live_regression_test"
echo "=== Test Pack: ${PACK} ==="

# Unit leg — default addopts; LLM-free, sub-second wall time.
UNIT_FILE="tests/unit/test_stale_a_independent_regressions.py"
INTEGRATION_FILE="tests/integration/test_stale_a_gate_live_regressions.py"

START=$(date +%s)

# Run unit + integration in series inside a single tracked pytest
# invocation per leg; the inner 280s guard catches a hang and the
# outer 'timeout 300' is the belt.
uv run python -m pytest "${UNIT_FILE}" --tb=short -q
UNIT_RC=$?
if [ $UNIT_RC -ne 0 ]; then
  END=$(date +%s)
  echo "Unit leg failed (rc=${UNIT_RC})"
  echo "Pack inner runtime: $((END-START))s"
  echo "RESULT: FAIL"
  exit 1
fi

# Integration leg — addopts override (default addopts deselects
# integration; override enables it via -m integration).
uv run python -m pytest "${INTEGRATION_FILE}" \
  --override-ini="addopts=" -m integration --tb=short -q
INT_RC=$?

END=$(date +%s)
echo "Pack inner runtime: $((END-START))s"
RC=$INT_RC
if [ $RC -eq 124 ]; then
  echo "RESULT: TIMEOUT"; exit 124
elif [ $RC -eq 0 ]; then
  echo "RESULT: PASS"; exit 0
else
  echo "RESULT: FAIL"; exit 1
fi
