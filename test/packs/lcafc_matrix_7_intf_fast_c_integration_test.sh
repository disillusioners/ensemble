#!/usr/bin/env bash
# LCA false-completion merge gate — MATRIX pack (pack 7/9, Set A integration, fast slice C)
# Pack: lcafc_matrix_7_intf_fast_c_integration_test
# Gate: feature/lca-false-complete-fixes @ d5c50994 (delta 316a849b..d5c50994, 1 commit;
# incident 7d4a3bd9 attestation false-completion fixes; 32 files +2981/-161).
# Splitter-generated 2026-09-26. 9 files / 49 collected tests; est 50-150s.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcafc_matrix_7_intf_fast_c_integration_test.sh  (from worktree root)
# Per-test override: timeout=120
# Composition: 1-11 test files (o1_boot_assert, performance, stage3_zoo, stale_watermark,
# user_answer_pending_lca, wakeups_helper, lcan_legacy_checkpoint, lcancheck_family_separation,
# lcau_incident_e2e). lcancheck_matrix_6_intf_fast_c analog (2026-09-23, 32 t / 39s
# = ~1.2s/t — lighter end; LCAFC slice sits slightly heavier on wakeups_helper 11t
# + lcancheck_family_separation 9t).
# EXPECTED RED (pre-existing, NOT caused by this delta): per prior-gate evidence
# (lcancheck_matrix_6 2026-09-23 PASS* with 2 base-proven reds: legacy_checkpoint s1 R4
# stale-contract + lcau scenario_b fixture under-can). Runners: those base-identical reds
# are PASS-equivalent for gate math; any OTHER failure is a real regression.
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

PACK="lcafc_matrix_7_intf_fast_c_integration_test"
FILES=(
tests/integration/test_attestation_o1_boot_assert.py
tests/integration/test_attestation_performance.py
tests/integration/test_attestation_stage3_zoo_test.py
tests/integration/test_attestation_stale_watermark.py
tests/integration/test_attestation_user_answer_pending_lca.py
tests/integration/test_attestation_wakeups_helper.py
tests/integration/test_lcan_legacy_checkpoint.py
tests/integration/test_lcancheck_family_separation.py
tests/integration/test_lcau_incident_e2e.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 280 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python -m pytest "${FILES[@]}" --tb=short -q --override-ini="timeout=120"
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
