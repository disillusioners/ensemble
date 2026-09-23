#!/usr/bin/env bash
# LCA Completion Check Note removal merge gate — MATRIX pack (pack 2/9, Set A unit+tools slice B)
# Pack: lcancheck_matrix_2_unit_b_test
# Gate: feature/lca-remove-check-note @ ff9eb849 (delta 6bf7bed7..ff9eb849, 1 commit).
# Splitter-generated 2026-09-23. 18 files / 467 collected tests; est 150-240s.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcancheck_matrix_2_unit_b_test.sh  (from worktree root)
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

PACK="lcancheck_matrix_2_unit_b_test"
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
tests/unit/test_lcan_nonote_census.py
tests/unit/test_lcau_anchor_security.py
tests/unit/test_lcau_caps_redaction.py
tests/unit/tools/test_attestation_prompt_contract.py
tests/unit/tools/test_attestation_registration.py
tests/unit/tools/test_attestation_surface_chain.py
tests/unit/tools/test_attestation_tool.py
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
