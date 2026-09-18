#!/usr/bin/env bash
# LCA advisory-note-removal merge gate — MATRIX pack (pack 6/10, Set A integration, REAL-JUDGE-LLM heavyweight)
# Pack: lcan_matrix_6_intf_s1_test
# Gate: feature/lca-remove-advisory-note @ 0a4fccb1 (delta 858b1038..0a4fccb1, 3 commits).
# Splitter-generated 2026-09-18. 2 files / 4 collected tests; measured 201s @ per-test 150s
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcan_matrix_6_intf_s1_test.sh  (from worktree root)
# Per-test override: timeout=240
# Probe evidence 2026-09-18: idle_orphan 123s (timed out @90s outer, PASSED @150s retry), revive_after_escalation 78s (per-test timeout @80s, PASSED @150s retry). Precedent: lcau_matrix_intf_s1 (timeout=240 real-LLM class). Judge must stay ENABLED — do NOT disable via env.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcan_matrix_6_intf_s1_test"
FILES=(
tests/integration/test_attestation_idle_orphan_incident.py
tests/integration/test_attestation_revive_after_escalation.py
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
