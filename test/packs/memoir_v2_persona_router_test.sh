#!/usr/bin/env bash
# Test Pack: memoir_v2_persona_router_test
#
# Memoir Store v2 — phase 5, Minimal REST Surface (plan:
# .agents/shared/planning/ari-memoir-store/v2-phase5-plan.md §6 / T5.5;
# AC5, AC6; module tests/unit/test_memoir_v2_persona_router.py).
#
# Scope (AC5, AC6):
#   * Minimal REST surface under /api/memoir/persona:
#     - GET /pending: list pending versions only;
#     - POST /{id}/approve: human session approval, moves pointer row;
#     - POST /{id}/reject: human session rejection;
#   * Human lane enforcement: 403 HUMAN_LANE_REQUIRED on bearer tokens;
#   * Scope discipline: zero create/edit/delete routes;
#   * Auth-OFF posture: 503 AUTH_NOT_CONFIGURED when auth is disabled;
#   * Hermetic auth environment: ambient ENSEMBLE_AUTH_* scrubbed;
#   * Mutation leg C: bearer approval refused with 403.
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACK_NAME="memoir_v2_persona_router_test"
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

TARGET="tests/unit/test_memoir_v2_persona_router.py"
if [ ! -f "$TARGET" ]; then
  echo "RESULT: FAIL (missing test module '$TARGET' — the leg would be vacuous)"
  exit 1
fi

EXPECTED_PASSED_N=12

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
