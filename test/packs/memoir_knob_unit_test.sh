#!/usr/bin/env bash
# Test Pack: memoir_knob_unit_test
#
# Memoir Store v1 — phase 0 (plan:
# .agents/shared/planning/ari-memoir-store/phase0-plan.md AC3/AC5/AC6).
#
# The pack runs the knob truth-table suite AND proves its MUTATION leg:
# the "garbage is repaired to the default instead of failing closed"
# defect is PLANTED in the source, the suite must go RED for the right
# reason, the source is RESTORED, and the suite must go GREEN again.
#
# Interpreter resolution (house convention): the repo's own .venv first.
# MEMOIR_PACK_PYTHON overrides it for worktree runs (the sibling
# worktrees have no .venv of their own).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: memoir_knob_unit_test ==="
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

TEST_FILE="tests/unit/test_memoir_knob_truth_table.py"
TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-300}"

run_suite() {
  # $1 = label ; prints the exit code, never aborts the script.
  local label="$1"
  echo "--- ${label} ---"
  timeout "${TIMEOUT_S}" "${PY}" -m pytest "${TEST_FILE}" \
    -q --tb=short -p no:randomly --override-ini="addopts=" 2>&1
  local rc=$?
  echo "--- ${label} exit=${rc} ---"
  return ${rc}
}

# ── Leg 1: the suite is GREEN ─────────────────────────────────────────────
run_suite "leg 1 baseline"
BASE_RC=$?
if [ ${BASE_RC} -ne 0 ]; then
  echo "RESULT: FAIL (baseline suite is not green: rc=${BASE_RC})"
  exit 1
fi

# ── Leg 2: the MUTATION leg must go RED ──────────────────────────────────
CONFIG_PY="daemon/config.py"
BACKUP="$(mktemp -t memoir-knob-mutation-XXXXXX.py)"
cp "${CONFIG_PY}" "${BACKUP}"
restore() {
  cp "${BACKUP}" "${CONFIG_PY}"
  rm -f "${BACKUP}"
}
trap restore EXIT INT TERM

echo "--- leg 2 mutation: plant 'garbage repaired to the default' ---"
"${PY}" - "${CONFIG_PY}" <<'PLANT'
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    text = handle.read()

old = "    return False, MEMOIR_RESOLUTION_INDETERMINATE, raw"
new = "    return True, MEMOIR_RESOLUTION_DEFAULT, None"
if text.count(old) != 1:
    sys.exit(
        f"MUTATION PLANT FAILED: expected exactly one occurrence of "
        f"{old!r}, found {text.count(old)}"
    )
with open(path, "w", encoding="utf-8") as handle:
    handle.write(text.replace(old, new))
print("mutation planted")
PLANT
PLANT_RC=$?
if [ ${PLANT_RC} -ne 0 ]; then
  echo "RESULT: FAIL (could not plant the mutation)"
  exit 1
fi

run_suite "leg 2 mutated"
MUT_RC=$?

restore
trap - EXIT INT TERM

if [ ${MUT_RC} -eq 0 ]; then
  echo "RESULT: FAIL (the mutation did NOT turn the suite RED — the"
  echo "        fail-closed assertion is not mutation-meaningful)"
  exit 1
fi
if [ ${MUT_RC} -eq 124 ]; then
  echo "RESULT: FAIL (the mutated suite TIMED OUT instead of failing)"
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
