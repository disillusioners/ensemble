#!/usr/bin/env bash
# LCA false-completion merge gate — MATRIX pack (pack 2/9, Set A unit+router slice B)
# Pack: lcafc_matrix_2_unit_b_unit_test
# Gate: feature/lca-false-complete-fixes @ d5c50994 (delta 316a849b..d5c50994, 1 commit;
# incident 7d4a3bd9 attestation false-completion fixes; 32 files +2981/-161).
# Splitter-generated 2026-09-26. 16 files / 416 collected tests; est 4-10s (unit tests ms-fast).
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcafc_matrix_2_unit_b_unit_test.sh  (from worktree root)
# Composition: alphabetical attest_* m-z + lca_false_complete_fixes (NEW 35t, DELTA-TOUCHED)
# + lcan_nonote_census + lcau_anchor_security + lcau_caps_redaction +
# routers/test_jobs_streaming_resolver (DELTA-TOUCHED, 10t).
# DELTA-TOUCHED surface (incident 7d4a3bd9): test_lca_false_complete_fixes (35t),
# test_attestation_resolver_stage2 (30t), test_jobs_streaming_resolver (10t).
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

PACK="lcafc_matrix_2_unit_b_unit_test"
FILES=(
tests/unit/test_attestation_marker_supersede_lca.py
tests/unit/test_attestation_marker_wiring.py
tests/unit/test_attestation_nudge_inject.py
tests/unit/test_attestation_report_judge.py
tests/unit/test_attestation_resolver.py
tests/unit/test_attestation_resolver_activation.py
tests/unit/test_attestation_resolver_stage2.py
tests/unit/test_attestation_resolver_user_intent.py
tests/unit/test_attestation_scanner.py
tests/unit/test_attestation_stage3_census.py
tests/unit/test_attestation_user_answer_pending_decide.py
tests/unit/test_lca_false_complete_fixes.py
tests/unit/test_lcan_nonote_census.py
tests/unit/test_lcau_anchor_security.py
tests/unit/test_lcau_caps_redaction.py
tests/unit/routers/test_jobs_streaming_resolver.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 280 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python -m pytest "${FILES[@]}" --tb=short -q
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
