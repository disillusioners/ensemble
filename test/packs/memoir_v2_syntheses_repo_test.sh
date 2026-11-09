#!/usr/bin/env bash
# Test Pack: memoir_v2_syntheses_repo_test
#
# Memoir Store v2 — phase 1, the syntheses repository lane (plan:
# .agents/shared/planning/ari-memoir-store/v2-phase1-plan.md AC5;
# module tests/unit/test_memoir_v2_syntheses_repo.py).
#
# Scope:
#   * MANDATORY CITATIONS — a synthesis without source episodes is
#     refused with ValueError (both None and []);
#   * the CLOSED verb vocabulary — a verb outside SYNTHESIS_VERBS is
#     refused with ValueError;
#   * the SCAN-WINDOW DEDUP (SF4 / V2-30) — the same claim written
#     twice inside the 2-day window returns the EXISTING row, keyed on
#     NFC + whitespace-collapse + casefold;
#   * the ATOMIC SUPERSESSION WRITER (V2-D 1.8) — the insert, the old
#     row's superseded_by and the new row's supersedes land together, or
#     not at all.
#
# Interpreter resolution (house convention): the repo's own .venv first.
# MEMOIR_PACK_PYTHON overrides it for worktree runs. Never fall back to
# a bare `python3` — that would import `daemon` from the MAIN checkout
# and report GREEN about a tree that is not this worktree.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACK_NAME="memoir_v2_syntheses_repo_test"
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

TARGET="tests/unit/test_memoir_v2_syntheses_repo.py"
if [ ! -f "$TARGET" ]; then
  echo "RESULT: FAIL (missing test module '$TARGET' — the leg would be vacuous)"
  exit 1
fi

# The MEASURED node count at the pin (2026-11-09, this worktree).
EXPECTED_PASSED_N=21

TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-300}"
LOG="${TMPDIR:-/tmp}/memoir_v2_syntheses_repo.log"

timeout "${TIMEOUT_S}" "${PY}" -m pytest "${TARGET}" \
  -q --tb=short -p no:randomly --override-ini="addopts=" 2>&1 | tee "${LOG}"
EXIT_CODE=${PIPESTATUS[0]}
if [ "$EXIT_CODE" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
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
