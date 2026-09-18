#!/usr/bin/env bash
# LCA user-intent matrix pack (INTEGRATION sub-pack intf-s1, 1 heavyweight real-LLM file)
# Pack: lcau_matrix_intf_s1_integration_test
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcau_matrix_intf_s1_integration_test.sh  (from worktree root)
# Per-test override --override-ini="timeout=240": file makes REAL judge-LLM calls
# (~10-40s/call, measured ~113s total under this file); default 30s per-test cap
# would false-TIMEOUT. Repo precedent: lca2_matrix_10..15 use timeout=300 for this class.
# Judge must stay ENABLED (tests assert judge_invoked=True) — do NOT disable via env.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcau_matrix_intf_s1_integration_test"
FILES=(
  tests/integration/test_attestation_idle_orphan_incident.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 280 uv run python -m pytest "${FILES[@]}" --tb=short -q --override-ini="timeout=240"
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
