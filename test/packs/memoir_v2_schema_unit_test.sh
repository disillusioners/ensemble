#!/usr/bin/env bash
# Test Pack: memoir_v2_schema_unit_test
#
# Memoir Store v2 — phase 1, the SQLite / unit lane (plan:
# .agents/shared/planning/ari-memoir-store/v2-phase1-plan.md
# AC1 / AC2 / AC6; module tests/unit/test_memoir_v2_schema.py).
#
# Scope:
#   * the migration file is discovered without a prefix collision, and
#     the lane's migration census DELTA is exactly ONE new file;
#   * every CREATE TABLE carries IF NOT EXISTS, every ALTER uses the
#     PLAIN ADD COLUMN form (zero IF NOT EXISTS in the .sql), every
#     string column is TEXT, the DOWN section exists;
#   * the file applies cleanly on a throwaway SQLite database AND
#     through the REAL migration runner in the REAL boot order;
#   * the six v2 tables are in the SQLModel metadata, the CHECK
#     constraints are bound to the MODELS, and the
#     notification_status vocabulary is TEST-ENFORCED (all four tokens
#     accepted, a fifth REJECTED) in BOTH the .sql DDL and the model;
#   * the cutover-bounded legacy seed flips only the pre-cutover rows.
#
# Interpreter resolution (house convention): the repo's own .venv first.
# MEMOIR_PACK_PYTHON overrides it for worktree runs. Never fall back to
# a bare `python3` — that would import `daemon` from the MAIN checkout
# and report GREEN about a tree that is not this worktree.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACK_NAME="memoir_v2_schema_unit_test"
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

TARGET="tests/unit/test_memoir_v2_schema.py"
if [ ! -f "$TARGET" ]; then
  echo "RESULT: FAIL (missing test module '$TARGET' — the leg would be vacuous)"
  exit 1
fi

# The MEASURED node count at the pin (2026-11-09, this worktree).
# `-lt 1` would let a silently-shortened selection pass; an exact count
# turns "a test disappeared" into a RED instead of a quieter green.
EXPECTED_PASSED_N=28

TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-300}"

timeout "${TIMEOUT_S}" "${PY}" -m pytest "${TARGET}" \
  -q --tb=short -p no:randomly --override-ini="addopts=" 2>&1 | tee "${TMPDIR:-/tmp}/memoir_v2_schema_unit.log"
EXIT_CODE=${PIPESTATUS[0]}
if [ "$EXIT_CODE" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
fi

# Explicit collected-count guard — GREEN-AT-ZERO IS A FAILURE.
SUMMARY="$(grep -Eo '[0-9]+ passed' "${TMPDIR:-/tmp}/memoir_v2_schema_unit.log" | tail -1 || true)"
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
