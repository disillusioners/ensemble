#!/usr/bin/env bash
# LCA false-completion merge gate — MATRIX pack (pack 5/9, Set A integration, fast-medium slice B)
# Pack: lcafc_matrix_5_intf_fast_b_integration_test
# Gate: feature/lca-false-complete-fixes @ d5c50994 (delta 316a849b..d5c50994, 1 commit;
# incident 7d4a3bd9 attestation false-completion fixes; 32 files +2981/-161).
# Splitter-generated 2026-09-26. 9 files / 40 collected tests; est 60-180s.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcafc_matrix_5_intf_fast_b_integration_test.sh  (from worktree root)
# Per-test override: timeout=120
# Composition: 3-9 test files (dry_mode, hold_semantics, in_graph_nudge_flow, incident_acceptance_lca,
# marker_bound_enforcement_lca, mid_work_report_testcase, mode_tri_state, nudge_supersede_lca,
# observability). DELTA-TOUCHED in scope (incident 7d4a3bd9): test_attestation_in_graph_nudge_flow.py
# (+5 lines, 1t), test_attestation_marker_bound_enforcement_lca.py (-115/-115, 7t),
# test_attestation_mid_work_report_testcase.py (+23, 6t).
# lcancheck_matrix_5_intf_fast_b analog (2026-09-23, 33 t / 220s after afdee41f inner-deadline
# quick-fix 240s→280s; splitter-estimate was 2.5-6× under actual — conservative cap applied).
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

PACK="lcafc_matrix_5_intf_fast_b_integration_test"
FILES=(
tests/integration/test_attestation_dry_mode.py
tests/integration/test_attestation_hold_semantics_independent.py
tests/integration/test_attestation_in_graph_nudge_flow.py
tests/integration/test_attestation_incident_acceptance_lca.py
tests/integration/test_attestation_marker_bound_enforcement_lca.py
tests/integration/test_attestation_mid_work_report_testcase.py
tests/integration/test_attestation_mode_tri_state.py
tests/integration/test_attestation_nudge_supersede_lca.py
tests/integration/test_attestation_observability.py
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
