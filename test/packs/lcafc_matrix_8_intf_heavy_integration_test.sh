#!/usr/bin/env bash
# LCA false-completion merge gate — MATRIX pack (pack 8/9, Set A integration, HEAVY slice)
# Pack: lcafc_matrix_8_intf_heavy_integration_test
# Gate: feature/lca-false-complete-fixes @ d5c50994 (delta 316a849b..d5c50994, 1 commit;
# incident 7d4a3bd9 attestation false-completion fixes; 32 files +2981/-161).
# Splitter-generated 2026-09-26. 5 files / 103 collected tests; est 120-240s.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 290s guard below.
# Invocation contract: timeout 300 bash test/packs/lcafc_matrix_8_intf_heavy_integration_test.sh  (from worktree root)
# Per-test override: timeout=240 (heavy live_descendants 42t + runbook_drift 22t + must_not_break 18t
# + stage2_killswitch 11t + c2_both_branches 10t; the 240s per-test ceiling protects against
# individual test stalls under the inner 290s guard).
# Composition: delta-adjacent heavy (live_descendants 42t, runbook_drift 22t, must_not_break 18t) +
# remaining heavy by file size (stage2_killswitch 11t, c2_both_branches 10t).
# Prior-gate analog lcancheck_matrix_9_intf_s2 (2026-09-23, 114 t / 203s = ~1.8s/t; 280s inner).
# Estimator caveat: live_descendants is the heaviest single integration file at 42 tests; per-test
# rate is empirically low (<5s) on prior runs, but the cap is set conservatively.
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

PACK="lcafc_matrix_8_intf_heavy_integration_test"
FILES=(
tests/integration/test_attestation_c2_both_branches.py
tests/integration/test_attestation_live_descendants.py
tests/integration/test_attestation_must_not_break.py
tests/integration/test_attestation_runbook_drift.py
tests/integration/test_attestation_stage2_killswitch.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 290 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python -m pytest "${FILES[@]}" --tb=short -q --override-ini="timeout=240"
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
