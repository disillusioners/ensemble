#!/usr/bin/env bash
# Test Pack: memoir_seed_unit_test
#
# Memoir Store v1 — phase 1 (plan:
# .agents/shared/planning/ari-memoir-store/phase1-plan.md AC4).
#
# Byte identity against design.md §11, the seeded row's shape, the
# idempotent re-boot, the knob-independence, and the failure tolerance
# (a seed failure logs a WARNING and the boot continues).
#
# TWO MUTATION LEGS, both proved here:
#   A. the seed text altered by one character  -> the byte-identity
#      assertion must go RED;
#   B. a seed failure that propagates instead of degrading -> the
#      failure-tolerance assertion must go RED.
#
# Interpreter resolution (house convention): the repo's own .venv first.
# MEMOIR_PACK_PYTHON overrides it for worktree runs.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: memoir_seed_unit_test ==="
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

TEST_FILE="tests/unit/test_memoir_persona_seed.py"
SEED_PY="daemon/repositories/memoir/seed.py"
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

BACKUP="$(mktemp -t memoir-seed-mutation-XXXXXX.py)"
cp "${SEED_PY}" "${BACKUP}"
restore() {
  cp "${BACKUP}" "${SEED_PY}"
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

# ── Leg 2: MUTATION A — the seed text altered by one character ───────────
echo "--- leg 2 mutation A: seed text altered by one character ---"
"${PY}" - "${SEED_PY}" <<'PLANT'
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    text = handle.read()

old = '"# Who We Are\\n"'
new = '"# Who We Ar\\n"'
if text.count(old) != 1:
    sys.exit(
        f"MUTATION PLANT FAILED: expected exactly one occurrence of "
        f"{old!r}, found {text.count(old)}"
    )
with open(path, "w", encoding="utf-8") as handle:
    handle.write(text.replace(old, new))
print("mutation A planted")
PLANT
if [ $? -ne 0 ]; then
  echo "RESULT: FAIL (could not plant mutation A)"
  exit 1
fi

run_suite "leg 2 mutated A"
MUT_A_RC=$?
restore
if [ ${MUT_A_RC} -eq 0 ]; then
  echo "RESULT: FAIL (mutation A did NOT turn the suite RED — the"
  echo "        byte-identity assertion is not mutation-meaningful)"
  exit 1
fi
echo "MUTATION LEG A: RED as required (rc=${MUT_A_RC})"

# ── Leg 3: MUTATION B — a seed failure that aborts the boot ──────────────
echo "--- leg 3 mutation B: the seed failure propagates ---"
"${PY}" - "${SEED_PY}" <<'PLANT'
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    text = handle.read()

old = (
    '        logger.warning("[Memoir] persona seed failed: %s", exc)\n'
    "        return None"
)
new = "        raise"
if text.count(old) != 1:
    sys.exit(
        f"MUTATION PLANT FAILED: expected exactly one occurrence of the "
        f"seed failure handler, found {text.count(old)}"
    )
with open(path, "w", encoding="utf-8") as handle:
    handle.write(text.replace(old, new))
print("mutation B planted")
PLANT
if [ $? -ne 0 ]; then
  echo "RESULT: FAIL (could not plant mutation B)"
  exit 1
fi

run_suite "leg 3 mutated B"
MUT_B_RC=$?
restore
if [ ${MUT_B_RC} -eq 0 ]; then
  echo "RESULT: FAIL (mutation B did NOT turn the suite RED — the"
  echo "        failure-tolerance assertion is not mutation-meaningful)"
  exit 1
fi
echo "MUTATION LEG B: RED as required (rc=${MUT_B_RC})"

# ── Leg 4: after the restore the suite is GREEN again ────────────────────
run_suite "leg 4 restored"
RESTORE_RC=$?
if [ ${RESTORE_RC} -ne 0 ]; then
  echo "RESULT: FAIL (the suite is not green after the restore: rc=${RESTORE_RC})"
  exit 1
fi

echo "RESULT: PASS"
exit 0
