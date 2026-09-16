#!/usr/bin/env bash
# LCA stage2 attestation matrix pack (Job 1/9) - frozen at tip f926de24
# Pack: lca2_matrix_13_integration_slow4_integration_test  (rev2: judge-latency-aware re-partition, measured runtimes)
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 290s guard below.
# Invocation contract: timeout 300 bash test/packs/lca2_matrix_13_integration_slow4_integration_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lca2_matrix_13_integration_slow4_integration_test"
FILES=(
  tests/integration/test_attestation_incident_acceptance_lca.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 290 uv run python -m pytest "${FILES[@]}" --tb=short -q --override-ini="timeout=300"
RC=$?
END=$(date +%s)
echo "Pack inner runtime: $((END-START))s"
if [ $RC -eq 124 ]; then
  echo "RESULT: TIMEOUT"; exit 124
elif [ $RC -eq 0 ]; then
  echo "RESULT: PASS"; exit 0
else
  echo "RESULT: FAIL"; exit 1
fi
