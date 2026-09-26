#!/usr/bin/env bash
# LCA false-completion merge gate — MATRIX pack (pack 9/9, Set A integration, REAL-LLM-JUDGE heavyweight)
# Pack: lcafc_matrix_9_intf_real_judge_integration_test
# Gate: feature/lca-false-complete-fixes @ d5c50994 (delta 316a849b..d5c50994, 1 commit;
# incident 7d4a3bd9 attestation false-completion fixes; 32 files +2981/-161).
# Splitter-generated 2026-09-26. 2 files / 4 collected tests; est 100-220s (real-LLM judge is slow).
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcafc_matrix_9_intf_real_judge_integration_test.sh  (from worktree root)
# Per-test override: timeout=240
# Judge MUST stay ENABLED — do NOT disable via env. The real fused judge (deny-band RESCUER
# semantics, intended Stage-2 behavior) is exercised live.
# EXPECTED RED (pre-existing, NOT caused by this delta): exactly 1 failure —
# test_attestation_revive_after_escalation.py::test_terminal_reset_and_fresh_episode_rearm_next_mission
# (live-LLM verdict stochasticity: the real fused judge occasionally answers verdict=complete
# on the deny band → rescue flips deny→allow → escalation flag never written → test's
# unconditional completion_gate_escalated=True assert fails. Fails at ANY commit whenever
# the live judge says "complete" in latency; passes when it says not_complete/timeout.
# Base-identical per QUARANTINE.md lca3_matrix_15 row: 2026-09-17 LCA Stage-3 gate.)
# Runners: treat this single red as PASS-equivalent for gate math; any OTHER failure in
# this pack is a real regression.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Drift pin: must be on the gate branch with d5c50994 as ancestor and zero production diff
BR=$(git rev-parse --abbrev-ref HEAD)
if [ "$BR" != "feature/lca-false-complete-fixes" ]; then
  echo "RESULT: FAIL (DRIFT — branch $BR != feature/lca-false-complete-fixes)"; exit 1
fi
if ! git merge-base --is-ancestor d5c50994 HEAD; then
  echo "RESULT: FAIL (DRIFT — d5c50994 not ancestor of HEAD)"; exit 1
fi
if [ -n "$(git diff d5c50994 HEAD -- daemon/ frontend/ scripts/ migrations/)" ]; then
  echo "RESULT: FAIL (DRIFT — daemon/, frontend/, scripts/, or migrations/ has diff vs d5c50994)"; exit 1
fi

PACK="lcafc_matrix_9_intf_real_judge_integration_test"
FILES=(
tests/integration/test_attestation_revive_after_escalation.py
tests/integration/test_attestation_idle_orphan_incident.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 280 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python -m pytest "${FILES[@]}" --tb=short -q --override-ini="timeout=240"
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
