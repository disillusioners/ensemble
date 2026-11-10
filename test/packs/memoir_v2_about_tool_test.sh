#!/usr/bin/env bash
# Test Pack: memoir_v2_about_tool_test
#
# Memoir Store v2 — phase 3, the ``memoir_about`` TOOL (plan:
# .agents/shared/planning/ari-memoir-store/v2-phase3-plan.md §6 / T3.6;
# AC3; module tests/unit/test_memoir_about_tool.py).
#
# Scope (AC3):
#   * the plan §3.2 gate ORDER (auth-first, then the kill-switch, then
#     argument validation, then the DB) — measured behaviorally;
#   * the frozen 19-code refusal vocabulary (no 20th code);
#   * the zero-episode empty-dossier SUCCESS render (V2-D 3.6) and the
#     ND-23 rule that an empty lookup does NOT increment heat;
#   * the populated dossier (name / type / description / recent episodes /
#     1-hop neighbors) and the +1.0 explicit-recall heat increment;
#   * the render-time heat decay worked numbers (V2-D 3.3).
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
PACK_NAME="memoir_v2_about_tool_test"
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

# The pack covers TWO modules: the ``memoir_about`` tool surface AND the
# heat gate (V2-D 3.3) that the tool implements.  The heat-honesty leg
# lives in the constellation MODULE (it needs the constellation fixtures),
# so it is named EXPLICITLY here — a pack-orphaned leg is invisible to a
# pack-only tester.
TARGETS=(
  "tests/unit/test_memoir_about_tool.py"
  "tests/unit/test_memoir_constellation.py::TestTheHeatHonesty"
)
for t in "${TARGETS[@]}"; do
  # A target may name a NODE (``file::Class``); the existence guard
  # checks the FILE part only.
  target_file="${t%%::*}"
  if [ ! -f "$target_file" ]; then
    echo "RESULT: FAIL (missing test module '$target_file' — the leg would be vacuous)"
    exit 1
  fi
done

# The MEASURED node count at the pin (2026-11-09, this worktree):
# test_memoir_about_tool.py (27) + TestTheHeatHonesty (2).
EXPECTED_PASSED_N=29

# Layer 2 watchdog — keep under the caller's outer cap.
TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-280}"
LOG="${TMPDIR:-/tmp}/${PACK_NAME}.log"

timeout "${TIMEOUT_S}" "${PY}" -m pytest \
  "tests/unit/test_memoir_about_tool.py" \
  "tests/unit/test_memoir_constellation.py::TestTheHeatHonesty" \
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
