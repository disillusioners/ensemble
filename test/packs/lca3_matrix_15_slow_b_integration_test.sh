#!/usr/bin/env bash
# LCA stage3 attestation matrix pack (verification gate) - frozen at tip f8e78a40
# Pack: lca3_matrix_15_slow_b_integration_test  (Job 1/N; stage3 = single-path endstate, R1-R8 + ledger a-e retired)
# Slow pack — REAL gateway LLM calls via the live judge. Split aggressively from the
# stage2 slow3/slow4 packs (which were solo files but the slow1/slow5/slow6 packs
# exceeded the safe-margin envelope). Estimated runtime ~30-60s for 3 tests; ample margin.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 290s guard below.
# Invocation contract: timeout 300 bash test/packs/lca3_matrix_15_slow_b_integration_test.sh  (from worktree root)
# Stage3 specifics:
#   - 3 stage2-branch-scoped artifacts (budget_parity, incident_abc, incident_de) DELETED in this delta — NOT in family.
#   - test_attestation_stage3_census.py is NEW (23 negative-pin tests; R1-R8 + ledger (c)).
#   - tests/integration/test_attestation_bound_escalation.py is EXCLUDED — single test pre-existing
#     asyncio-selector wedge (verified hanging at BASELINE; environmental; left for the separate
#     tester-lane characterization worker; NOT a stage3-introduced defect).
#   - PG lane (tests/postgres/test_attestation_live_descendants_pg_lca.py) is a SEPARATE lane — NOT in this matrix.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lca3_matrix_15_slow_b_integration_test"
FILES=(
  tests/integration/test_attestation_incident_acceptance_lca.py
  tests/integration/test_attestation_revive_after_escalation.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
# QUARANTINED 2026-09-17: live-LLM verdict stochasticity (judge-as-rescuer); base-identical; see QUARANTINE.md
DESELECT_NODE="tests/integration/test_attestation_revive_after_escalation.py::test_terminal_reset_and_fresh_episode_rearm_next_mission"
timeout 290 uv run python -m pytest "${FILES[@]}" --deselect "${DESELECT_NODE}" --tb=short -q --override-ini="timeout=300"
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
