#!/usr/bin/env bash
# Test Pack: memoir_v2_tool_registration_test
#
# Memoir Store v2 — phase 3, TOOL REGISTRATION integrity (plan:
# .agents/shared/planning/ari-memoir-store/v2-phase3-plan.md §6 / T3.6;
# AC4 + AC5; modules tests/unit/test_memoir_registration.py +
# tests/unit/test_memoir_about_tool.py::TestRegistration).
#
# Scope (AC4 + AC5):
#   * ``len(MEMOIR_TOOL_NAMES) == 6`` and source discovery includes
#     ``memoir_about``;
#   * the registry factory yields the six-tool surface for Ari; the
#     runtime layer-2 gate refuses the same invocation for another agent;
#     the layer-1 category admission (the agents' ``tools.allow``) is
#     Ari-only;
#   * the privileged-category census pins (the existing registration
#     module, already updated to six memoir tools by the landed commit);
#   * the memoir category ordinal is GREP-DERIVED at test time from
#     ``.agents/shared/metrics/tool-categories.json`` — zero hardcoded
#     ordinals anywhere (V2-D 3.7 / AC5).
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
PACK_NAME="memoir_v2_tool_registration_test"
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

TARGETS=(
  "tests/unit/test_memoir_registration.py"
  "tests/unit/test_memoir_about_tool.py"
)
for t in "${TARGETS[@]}"; do
  if [ ! -f "$t" ]; then
    echo "RESULT: FAIL (missing test module '$t' — the leg would be vacuous)"
    exit 1
  fi
done

# The MEASURED node count at the pin (2026-11-09, this worktree):
# test_memoir_registration.py (20) + TestRegistration (6).
EXPECTED_PASSED_N=26

# Layer 2 watchdog — keep under the caller's outer cap.
TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-280}"
LOG="${TMPDIR:-/tmp}/${PACK_NAME}.log"

timeout "${TIMEOUT_S}" "${PY}" -m pytest \
  "tests/unit/test_memoir_registration.py" \
  "tests/unit/test_memoir_about_tool.py::TestRegistration" \
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
