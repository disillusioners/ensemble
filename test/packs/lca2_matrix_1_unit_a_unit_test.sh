#!/usr/bin/env bash
# LCA stage2 attestation matrix pack (Job 1/9) - frozen at tip f926de24
# Pack: lca2_matrix_1_unit_a_unit_test
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 290s guard below.
# Invocation contract: timeout 300 bash test/packs/lca2_matrix_1_unit_a_unit_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lca2_matrix_1_unit_a_unit_test"
FILES=(
  tests/unit/test_attestation_conditional_gate_outcomes.py
  tests/unit/test_attestation_conditional_scanner.py
  tests/unit/test_attestation_construction_site_sweep.py
  tests/unit/test_attestation_dry_logging.py
  tests/unit/test_attestation_epoch_replay.py
  tests/unit/test_attestation_fail_open.py
  tests/unit/test_attestation_fused_judge.py
  tests/unit/test_attestation_gate.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 290 uv run python -m pytest "${FILES[@]}" --tb=short -q
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
