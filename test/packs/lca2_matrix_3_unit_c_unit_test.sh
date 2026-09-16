#!/usr/bin/env bash
# LCA stage2 attestation matrix pack (Job 1/9) - frozen at tip f926de24
# Pack: lca2_matrix_3_unit_c_unit_test
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 290s guard below.
# Invocation contract: timeout 300 bash test/packs/lca2_matrix_3_unit_c_unit_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lca2_matrix_3_unit_c_unit_test"
FILES=(
  tests/unit/test_attestation_nudge_inject.py
  tests/unit/test_attestation_report_judge.py
  tests/unit/test_attestation_resolver.py
  tests/unit/test_attestation_resolver_activation.py
  tests/unit/test_attestation_resolver_stage2.py
  tests/unit/test_attestation_scanner.py
  tests/unit/test_attestation_user_answer_pending_decide.py
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
