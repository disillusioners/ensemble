#!/usr/bin/env bash
# LCA advisory-note-removal merge gate — MATRIX pack (pack 5/10, Set A integration, probe-verified mid tier)
# Pack: lcan_matrix_5_intf_mid_test
# Gate: feature/lca-remove-advisory-note @ 0a4fccb1 (delta 858b1038..0a4fccb1, 3 commits).
# Splitter-generated 2026-09-18. 4 files / 8 collected tests; measured 242s total @ per-test 80s
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcan_matrix_5_intf_mid_test.sh  (from worktree root)
# Per-test override: timeout=120
# Probe evidence 2026-09-18: mode_tri_state 49s, delegation_allow 49s, bound_escalation 60s, incident_acceptance_lca 84s (all rc=0 standalone).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcan_matrix_5_intf_mid_test"
FILES=(
tests/integration/test_attestation_mode_tri_state.py
tests/integration/test_attestation_delegation_allow.py
tests/integration/test_attestation_bound_escalation.py
tests/integration/test_attestation_incident_acceptance_lca.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 280 uv run python -m pytest "${FILES[@]}" --tb=short -q --override-ini="timeout=120"
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
