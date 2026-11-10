#!/usr/bin/env bash
# Test Pack: memoir_v2_persona_composition_test
#
# Memoir Store v2 — phase 5, Persona Composition (plan:
# .agents/shared/planning/ari-memoir-store/v2-phase5-plan.md §6 / T5.3;
# AC2, AC3; module tests/unit/test_memoir_v2_persona_composition.py).
#
# Scope (AC2, AC3):
#   * Persona document composition within 2000-token ceiling
#     (MEMOIR_PERSONA_TOKEN_BUDGET, memoir_briefing.py:133);
#   * Metering via daemon.loader.estimate_tokens (cl100k tiktoken);
#   * Serialized metadata comment format;
#   * Unbuildable diff / over-budget parking in memoir_jev_ledger:
#     - diff > 2000 tokens parked with last_error='diff_over_budget', status='pending';
#     - empty/malformed diff parked with status='pending';
#     - terminalizes after 5 attempts as status='terminal', last_error='terminal:unbuildable_diff';
#   * Composition budget ceiling: MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS = 1.
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACK_NAME="memoir_v2_persona_composition_test"
echo "=== Test Pack: ${PACK_NAME} ==="
cd "$PROJECT_DIR" || {
  echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"
  exit 1
}

PY=""
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  PY="$PROJECT_DIR/.venv/bin/python"
elif [ -n "${MEMOIR_PACK_PYTHON:-}" ] && [ -x "${MEMOIR_PACK_PYTHON}" ]; then
  PY="${MEMOIR_PACK_PYTHON}"
fi
if [ -z "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
echo "Interpreter: $PY"

TARGET="tests/unit/test_memoir_v2_persona_composition.py"
if [ ! -f "$TARGET" ]; then
  echo "RESULT: FAIL (missing test module '$TARGET' — the leg would be vacuous)"
  exit 1
fi

EXPECTED_PASSED_N=13

# The MEASURED node count at the pin (2026-11-10, this worktree):
# TestPersonaComposition (1) + TestUnbuildableDiffParking (3)
# + TestCompositionBudgetPerPass (2) + TestRaisePathParkSemantics (5)
# + TestCompositionBudgetChargedAtCallSite (1) + TestParkLoopIsolation (1).
#
# THE DELTA'S OWN LEGS ARE SELECTOR-REGISTERED (the pack-orphan class):
# the selector below names EVERY class in the module EXPLICITLY and the
# node-count pin moves IN THE SAME COMMIT as the legs.
#
# THE FIX-ROUND LEG (2026-11-10): TestCompositionBudgetPerPass now holds
# the PRODUCTION-SHAPED budget leg — one pass with two gated candidates
# parks the second as 'pending' (deferred:composition_budget) and the NEXT
# pass (with NO new syntheses) picks it up FROM THE LEDGER.  The leg it
# replaced re-passed the candidate explicitly, a shape production never
# produces, and asserted the candidate was NEVER TOUCHED — which is the
# data loss itself.
#
# THE RAISE-PATH LEGS (2026-11-10, this fix round):
# TestRaisePathParkSemantics replaces the leg that RATIFIED the stall
# (it asserted only status=='pending' for a raising candidate, so the
# discarded classification, the frozen attempt counter and the
# head-of-line stall all passed).  Its five legs pin the post-fix
# semantics: the raise is CONTAINED and JOURNALED as 'gate_error: …'
# (never the deferral marker), the attempt is consumed, the retry
# ladder TERMINALIZES at MAX_ATTEMPTS, the pass CONTINUES (a later clean
# candidate still drafts in the SAME pass), the PAID classification is
# PRESERVED (the retry does not re-pay the classifier), and the two
# markers stay DISTINGUISHABLE.

TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-280}"
LOG="${TMPDIR:-/tmp}/${PACK_NAME}.log"

timeout "${TIMEOUT_S}" "${PY}" -m pytest "${TARGET}" \
  -k "TestPersonaComposition or TestUnbuildableDiffParking or TestCompositionBudgetPerPass or TestRaisePathParkSemantics or TestCompositionBudgetChargedAtCallSite or TestParkLoopIsolation" \
  -q --tb=short -p no:randomly --override-ini="addopts=" 2>&1 | tee "${LOG}"
EXIT_CODE=${PIPESTATUS[0]}
if [ "$EXIT_CODE" -eq 124 ]; then
  echo "RESULT: TIMEOUT (internal ${TIMEOUT_S}s watchdog exceeded)"
  exit 124
fi

SUMMARY="$(grep -Eo '[0-9]+ passed' "${LOG}" | tail -1 || true)"
case "${SUMMARY}" in
  [0-9]*) PASSED_N="${SUMMARY%% *}" ;;
  *)      PASSED_N="" ;;
esac
if [ -z "${PASSED_N}" ]; then
  echo "RESULT: FAIL (rc=${EXIT_CODE}, no \"N passed\" in summary — collection/selection failure or a fully-skipped module; GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "${PASSED_N}" -ne "${EXPECTED_PASSED_N}" ]; then
  echo "RESULT: FAIL (rc=${EXIT_CODE}, passed=${PASSED_N} != EXPECTED_PASSED_N=${EXPECTED_PASSED_N} — the node count moved without the pin moving)"
  exit 1
fi
if [ "$EXIT_CODE" -ne 0 ]; then
  echo "RESULT: FAIL (rc=${EXIT_CODE}, ${PASSED_N} passed before failure)"
  exit 1
fi

echo "RESULT: PASS (${PASSED_N} passed, rc=${EXIT_CODE})"
exit 0
