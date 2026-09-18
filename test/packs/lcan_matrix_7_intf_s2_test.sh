#!/usr/bin/env bash
# LCA advisory-note-removal merge gate — MATRIX pack (pack 7/10, Set A integration, live_descendants (42t) + mock-fast gates)
# Pack: lcan_matrix_7_intf_s2_test
# Gate: feature/lca-remove-advisory-note @ 0a4fccb1 (delta 858b1038..0a4fccb1, 3 commits).
# Splitter-generated 2026-09-18. 6 files / 121 collected tests; measured ~130-160s est
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcan_matrix_7_intf_s2_test.sh  (from worktree root)
# Per-test override: timeout=240
# Probe evidence 2026-09-18: live_descendants 115s @ per-test 150s (outer-timeout @90s, PASSED @150s retry). runbook_drift + must_not_break calibrated 3s EACH (mock-fast).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcan_matrix_7_intf_s2_test"
FILES=(
tests/integration/test_attestation_live_descendants.py
tests/integration/test_attestation_runbook_drift.py
tests/integration/test_attestation_must_not_break.py
tests/integration/test_attestation_stage2_failopen.py
tests/integration/test_attestation_stage2_killswitch.py
tests/integration/test_attestation_c2_both_branches.py
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
