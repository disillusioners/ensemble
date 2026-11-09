#!/usr/bin/env bash
# Test Pack: memoir_v2_jev_classifier_test
#
# Memoir Store v2 — phase 5, the JEV Classifier (plan:
# .agents/shared/planning/ari-memoir-store/v2-phase5-plan.md §6 / T5.1, T5.2;
# AC1, AC2; module tests/unit/test_memoir_v2_jev_classifier.py).
#
# Scope (AC1, AC2, AC6):
#   * Stage 1 heuristic filter: trivial pleasantries/operations classified
#     as 'none' with 0 LLM calls; verbatim/corroborations classified as
#     'reinforcement'; novel claims deferred to LLM;
#   * Stage 2 LLM classifier: taxonomy classification into 5 categories;
#   * Candidate parking in memoir_jev_ledger: classifier failures park candidate
#     with classification=NULL, status='pending', attempts++; terminalizes
#     after 5 attempts as status='terminal', last_error='terminal:gate_unavailable';
#   * Once-marker discipline: suppressed records not re-evaluated;
#   * Mutation leg A: defaulting to 'none' on classifier exception fails AC2.
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACK_NAME="memoir_v2_jev_classifier_test"
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

TARGET="tests/unit/test_memoir_v2_jev_classifier.py"
if [ ! -f "$TARGET" ]; then
  echo "RESULT: FAIL (missing test module '$TARGET' — the leg would be vacuous)"
  exit 1
fi

EXPECTED_PASSED_N=14

TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-280}"
LOG="${TMPDIR:-/tmp}/${PACK_NAME}.log"

timeout "${TIMEOUT_S}" "${PY}" -m pytest "${TARGET}" \
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
