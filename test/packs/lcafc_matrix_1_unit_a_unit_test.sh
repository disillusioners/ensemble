#!/usr/bin/env bash
# LCA false-completion merge gate — MATRIX pack (pack 1/9, Set A unit+router slice A)
# Pack: lcafc_matrix_1_unit_a_unit_test
# Gate: feature/lca-false-complete-fixes @ d5c50994 (delta 316a849b..d5c50994, 1 commit;
# incident 7d4a3bd9 attestation false-completion fixes; 32 files +2981/-161).
# Splitter-generated 2026-09-26. 17 files / 347 collected tests; est 3-8s (unit tests ms-fast).
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcafc_matrix_1_unit_a_unit_test.sh  (from worktree root)
# Composition: alphabetical attest_* a-l (incl. DELTA-TOUCHED judge_resolver 52t,
# judge_wiring 26t, gate 48t, ledger 23t, ledger_failopen 14t, marker_scanner 54t).
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

PACK="lcafc_matrix_1_unit_a_unit_test"
FILES=(
tests/unit/test_attestation_attest_first_contract.py
tests/unit/test_attestation_b_redaction_stage3.py
tests/unit/test_attestation_conditional_gate_outcomes.py
tests/unit/test_attestation_conditional_scanner.py
tests/unit/test_attestation_construction_site_sweep.py
tests/unit/test_attestation_dry_logging.py
tests/unit/test_attestation_epoch_replay.py
tests/unit/test_attestation_fail_open.py
tests/unit/test_attestation_fused_judge.py
tests/unit/test_attestation_fused_judge_truncation.py
tests/unit/test_attestation_gate.py
tests/unit/test_attestation_judge_resolver.py
tests/unit/test_attestation_judge_wiring.py
tests/unit/test_attestation_lca_note_removed.py
tests/unit/test_attestation_ledger.py
tests/unit/test_attestation_ledger_failopen.py
tests/unit/test_attestation_marker_scanner.py
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
