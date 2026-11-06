#!/usr/bin/env bash
# Test Pack: memoir_persona_lane_unit_test
#
# Memoir Store v1 — phase 1 (plan:
# .agents/shared/planning/ari-memoir-store/phase1-plan.md AC5).
#
# The persona transition vocabulary (draft -> pending_approval ->
# approved is permitted; draft -> approved is REFUSED) and the
# human-lane guard on approve()/reject().
#
# ONE MUTATION LEG, proved here: the human-lane guard removed -> the
# approve-without-a-human assertion must go RED.
#
# Interpreter resolution (house convention): the repo's own .venv first.
# MEMOIR_PACK_PYTHON overrides it for worktree runs.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: memoir_persona_lane_unit_test ==="
cd "$PROJECT_DIR"

PY=""
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  PY="$PROJECT_DIR/.venv/bin/python"
elif [ -n "${MEMOIR_PACK_PYTHON:-}" ] && [ -x "${MEMOIR_PACK_PYTHON}" ]; then
  PY="${MEMOIR_PACK_PYTHON}"
fi
if [ -z "$PY" ]; then
  # FAIL LOUD (the false-green class): a bare `python3` fallback can
  # import `daemon` from the MAIN checkout, so the pack would test a
  # tree that is not this worktree and report GREEN. Never fall back.
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
echo "Interpreter: $PY"

TEST_FILE="tests/unit/test_memoir_persona_lane.py"
REPO_PY="daemon/repositories/memoir/repository.py"
TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-300}"

run_suite() {
  local label="$1"
  echo "--- ${label} ---"
  timeout "${TIMEOUT_S}" "${PY}" -m pytest "${TEST_FILE}" \
    -q --tb=short -p no:randomly --override-ini="addopts=" 2>&1
  local rc=$?
  echo "--- ${label} exit=${rc} ---"
  return ${rc}
}

BACKUP="$(mktemp -t memoir-persona-mutation-XXXXXX.py)"
cp "${REPO_PY}" "${BACKUP}"
restore() {
  cp "${BACKUP}" "${REPO_PY}"
}
cleanup() {
  restore
  rm -f "${BACKUP}"
}
trap cleanup EXIT INT TERM

# ── Leg 1: the suite is GREEN ─────────────────────────────────────────────
run_suite "leg 1 baseline"
BASE_RC=$?
if [ ${BASE_RC} -ne 0 ]; then
  echo "RESULT: FAIL (baseline suite is not green: rc=${BASE_RC})"
  exit 1
fi

# ── Leg 2: MUTATION — the human-lane guard removed ───────────────────────
echo "--- leg 2 mutation: the human-lane guard is removed ---"
"${PY}" - "${REPO_PY}" <<'PLANT'
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    text = handle.read()

old = '        if auth_method != "session":'
new = "        if False:"
if text.count(old) != 1:
    sys.exit(
        f"MUTATION PLANT FAILED: expected exactly one occurrence of the "
        f"human-lane guard, found {text.count(old)}"
    )
with open(path, "w", encoding="utf-8") as handle:
    handle.write(text.replace(old, new))
print("mutation planted")
PLANT
if [ $? -ne 0 ]; then
  echo "RESULT: FAIL (could not plant the mutation)"
  exit 1
fi

run_suite "leg 2 mutated"
MUT_RC=$?
restore
if [ ${MUT_RC} -eq 0 ]; then
  echo "RESULT: FAIL (the mutation did NOT turn the suite RED — the"
  echo "        human-lane guard assertion is not mutation-meaningful)"
  exit 1
fi
echo "MUTATION LEG: RED as required (rc=${MUT_RC})"

# ── Leg 3: after the restore the suite is GREEN again ────────────────────
run_suite "leg 3 restored"
RESTORE_RC=$?
if [ ${RESTORE_RC} -ne 0 ]; then
  echo "RESULT: FAIL (the suite is not green after the restore: rc=${RESTORE_RC})"
  exit 1
fi

echo "RESULT: PASS"
exit 0
