#!/usr/bin/env bash
# LCA user-intent merge gate — MATRIX INTEGRATION-SLOW lane (2 heavyweight files)
# Pack: lcau_matrix_ints_integration_test
#   - tests/integration/test_attestation_in_graph_nudge_flow.py  (1 test, ~27s)
#   - tests/integration/test_attestation_live_descendants.py     (42 tests, slow)
# Per-test override: timeout=150s (bounds single-test runaways below the 280s pack cap).
# NOTE on timeout=150: this is tighter than repo precedent lca2_matrix_15 (which uses
#   timeout=300 for live_descendants alone). We pack two heavyweight files in one
#   script, so a 150s per-test ceiling matches the 280s inner-pack cap.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcau_matrix_ints_integration_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcau_matrix_ints_integration_test"
FILES=(
  tests/integration/test_attestation_in_graph_nudge_flow.py
  tests/integration/test_attestation_live_descendants.py
)
echo "=== Test Pack: ${PACK} ==="
echo "Files: ${FILES[*]}"
START=$(date +%s)
timeout 280 uv run python -m pytest "${FILES[@]}" --tb=short -q --override-ini="timeout=150"
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
