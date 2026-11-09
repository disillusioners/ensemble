#!/usr/bin/env bash
# Test Pack: memoir_v2_constellation_service_test
#
# Memoir Store v2 — phase 3, the Constellation SERVICE (plan:
# .agents/shared/planning/ari-memoir-store/v2-phase3-plan.md §6 / T3.6;
# AC1 + AC7; module tests/unit/test_memoir_constellation.py).
#
# Scope (AC1 + AC7):
#   * entity extraction from ``stars[]`` (canonical stars + memoir_line
#     edges), alias normalization ("the Emperor" → "The Emperor"), and
#     the ``last_pass_id`` crash-recovery guard;
#   * background extraction NEVER increments the stored heat (DD-12);
#   * the bounded historical seed (C2 / V2-27): the nightly pass walks
#     the pre-cutover backlog OLDEST-FIRST under the nightly cap, and the
#     seeded "The Emperor" star is queryable through memoir_about.
#
# Dual-layer timeout: Layer 1 = the caller's outer timeout (recommended
# 300 s — EVERY command on this lane is timeout-wrapped); Layer 2 = the
# internal ``timeout`` below (280 s, under the outer cap, so this pack's
# own RESULT verdict always lands first).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACK_NAME="memoir_v2_constellation_service_test"
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

TARGET="tests/unit/test_memoir_constellation.py"
if [ ! -f "$TARGET" ]; then
  echo "RESULT: FAIL (missing test module '$TARGET' — the leg would be vacuous)"
  exit 1
fi

# The MEASURED node count at the pin (2026-11-09, this worktree):
# TestExtractionAndAlias (5) + TestSeed (1) + TestThePerPassCallBudget (1)
# + TestTheHeatHonesty (2) + TestThePerPurposeBudget (3)
# + TestTheDurablePassIdentity (2) + TestTheBudgetRefusal (2)
# + TestTheSeedSurvivesAParseFailure (1).
#
# THE DELTA'S OWN LEGS ARE SELECTOR-REGISTERED (the pack-orphan class):
# TestThePerPassCallBudget and TestTheHeatHonesty were selected by NO
# pack, so a tester running only the packs would have PASSED with both
# regressions restored.  The selector below names them EXPLICITLY and the
# node-count pin moves IN THE SAME COMMIT as the legs.
EXPECTED_PASSED_N=17

# Layer 2 watchdog — keep under the caller's outer cap.
TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-280}"
LOG="${TMPDIR:-/tmp}/${PACK_NAME}.log"

timeout "${TIMEOUT_S}" "${PY}" -m pytest "${TARGET}" \
  -k "TestExtractionAndAlias or TestSeed or TestThePerPassCallBudget or TestTheHeatHonesty or TestThePerPurposeBudget or TestTheDurablePassIdentity or TestTheBudgetRefusal or TestTheSeedSurvivesAParseFailure" \
  -q --tb=short -p no:randomly --override-ini="addopts=" 2>&1 | tee "${LOG}"
EXIT_CODE=${PIPESTATUS[0]}
if [ "$EXIT_CODE" -eq 124 ]; then
  echo "RESULT: TIMEOUT (internal ${TIMEOUT_S}s watchdog exceeded)"
  exit 124
fi

# Explicit collected-count guard — GREEN-AT-ZERO IS A FAILURE.
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
