#!/usr/bin/env bash
# LCA Completion Check Note removal merge gate — MATRIX pack (pack 1/9, Set A unit+tools slice A)
# Pack: lcancheck_matrix_1_unit_a_test
# Gate: feature/lca-remove-check-note @ ff9eb849 (delta 6bf7bed7..ff9eb849, 1 commit).
# Splitter-generated 2026-09-23. 17 files / 341 collected tests; est 120-180s.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcancheck_matrix_1_unit_a_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Drift pin: must be on the gate branch with ff9eb849 as ancestor and zero daemon/scripts diff
BR=$(git rev-parse --abbrev-ref HEAD)
if [ "$BR" != "feature/lca-remove-check-note" ]; then
  echo "RESULT: FAIL (DRIFT — branch $BR != feature/lca-remove-check-note)"; exit 1
fi
if ! git merge-base --is-ancestor ff9eb849 HEAD; then
  echo "RESULT: FAIL (DRIFT — ff9eb849 not ancestor of HEAD)"; exit 1
fi
if [ -n "$(git diff ff9eb849 HEAD -- daemon/ scripts/)" ]; then
  echo "RESULT: FAIL (DRIFT — daemon/ or scripts/ has diff vs ff9eb849)"; exit 1
fi

PACK="lcancheck_matrix_1_unit_a_test"
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
