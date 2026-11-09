#!/usr/bin/env bash
# Test Pack: memoir_v2_persona_proposal_test
#
# Memoir Store v2 — phase 5, Persona Proposals and Human Lane (plan:
# .agents/shared/planning/ari-memoir-store/v2-phase5-plan.md §6 / T5.4;
# AC3, AC4; module tests/unit/test_memoir_v2_persona_proposal.py).
#
# Scope (AC3, AC4, AC6):
#   * Proposal creation and submission:
#     - Draft version created with status='draft' -> submit() moves to 'pending_approval';
#     - Pointer in memoir_persona remains at current approved version until approve();
#     - Sequential version increment: version = max(versions) + 1 (ND-17);
#   * Human session approval & rejection:
#     - approve() with actor.auth_method == "session" transitions to 'approved',
#       moves pointer in memoir_persona;
#     - reject() with session actor transitions status to 'rejected';
#   * Non-human approval refusal:
#     - approve() with bearer or internal actor raises HumanLaneRequiredError;
#   * Write purity pin:
#     - proposals created strictly from reflection service; tool module pure;
#   * Mutation leg B: removing _require_human_lane from approve() fails AC4.
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACK_NAME="memoir_v2_persona_proposal_test"
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

TARGET="tests/unit/test_memoir_v2_persona_proposal.py"
if [ ! -f "$TARGET" ]; then
  echo "RESULT: FAIL (missing test module '$TARGET' — the leg would be vacuous)"
  exit 1
fi

EXPECTED_PASSED_N=5

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
