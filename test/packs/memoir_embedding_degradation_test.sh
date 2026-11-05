#!/usr/bin/env bash
# Test Pack: memoir_embedding_degradation_test.sh
#
# Memoir Store v1 — phase 3 (plan:
# .agents/shared/planning/ari-memoir-store/phase3-plan.md AC7 / T3.7).
#
# The pack runs the degradation suite (restart catch-up, graceful
# lane-down with the wave-2 lexical recall + footer disclosure, the
# runbook requeue procedure) AND proves ONE mutation leg:
#
#   M6  the terminal predicate disabled (rows stop being terminal)
#       ⇒ the runbook requeue test goes RED (the AM-30 class)
#
#   W-2 PLANT-TARGET NOTE (why the SQL form): the terminal rule is
#   SINGLE-SOURCED in the repository since the council fix cycle
#   (f012b4787) — the SQL form ``memoir_embedding_terminal_predicate``
#   feeds BOTH the pending-scan exclusion AND ``_count_terminal``, and
#   the worker's Python belt (``_is_terminal_row`` →
#   ``memoir_row_is_terminal``) upholds the skip INDEPENDENTLY as
#   defence-in-depth. Disabling the Python form alone is therefore
#   VACUOUS (the SQL exclusion still hides terminal rows from the scan,
#   so nothing re-embeds); the guard's reachable trip is the SQL form —
#   the pack plants it to match nothing and the guard's terminal-count
#   assertion (``_count_terminal() == 1``) stops holding. The pre-W-2
#   plant keyed on the worker's INLINE predicate body, which the W-2
#   delegation removed (the MT-01 lockstep class: a standing mutation
#   instrument's literals move in lockstep with the code it mutates).
#
# EXIT-CODE CONTRACT (the house shape): pack 0 = PASS · 1 = FAIL.
#
# FENCES: per-variable env scrub, `-p no:randomly`, addopts clear,
# dual-layer timeout. The suite runs on throwaway SQLite only.
#
# Interpreter: MEMOIR_PACK_PYTHON wins, then the worktree-local .venv;
# FAIL LOUD on a missing interpreter — never bare python3.
set -uo pipefail

PACK_START=${SECONDS:-0}
PACK_BUDGET_S=900

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="${MEMOIR_PACK_PYTHON:-}"
if [ -z "$PY" ] && [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PY="$REPO_ROOT/.venv/bin/python"
fi
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — set MEMOIR_PACK_PYTHON to the"
  echo "        venv python; this worktree has no .venv of its own and a"
  echo "        bare-python3 fallback is banned)"
  exit 1
fi
echo "=== Test Pack: memoir_embedding_degradation_test ==="
echo "Interpreter: $PY"

TEST_FILE="tests/unit/test_memoir_embedding_degradation.py"
TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-300}"
LOG_DIR="${MEMOIR_EMBED_LOG_DIR:-/tmp/memoir-embed-gate}"
mkdir -p "$LOG_DIR"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT)

run_suite() {
  local label="$1"; shift
  echo "--- ${label} ---"
  timeout "${TIMEOUT_S}" "${SCRUB[@]}" "${PY}" -m pytest "$@" \
    -q --tb=short -p no:randomly --override-ini="addopts=" 2>&1
  local rc=$?
  echo "--- ${label} exit=${rc} ---"
  return ${rc}
}

PLANT_TARGET="daemon/repositories/memoir/repository.py"

# ── Leg 1: the baseline suite is GREEN ───────────────────────────────────
run_suite "leg 1 baseline (full suite)" "${TEST_FILE}"
BASE_RC=$?
if [ ${BASE_RC} -ne 0 ]; then
  echo "RESULT: FAIL (baseline suite is not green: rc=${BASE_RC})"
  exit 1
fi

# ── Leg 2: the mutation leg must go RED ──────────────────────────────────
BACKUP="$(mktemp -t memoir-deg-mutation-XXXXXX.py)"
cp "${PLANT_TARGET}" "${BACKUP}"
restore() {
  cp "${BACKUP}" "${PLANT_TARGET}"
  rm -f "${BACKUP}"
}
trap restore EXIT INT TERM

echo "--- leg 2 mutation: plant the terminal predicate disabled ---"
"${PY}" - "${PLANT_TARGET}" <<'PLANT'
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    text = handle.read()

# W-2: the terminal rule's SQL form is the single source feeding BOTH
# the pending-scan exclusion and _count_terminal (see the header note).
old = '''    return or_(
        func.coalesce(MemoirEpisode.embedding_attempts, 0) >= int(attempt_cap),
        func.coalesce(MemoirEpisode.embedding_error, "").like(
            f"{MEMOIR_EMBEDDING_TERMINAL_MARKER}%"
        ),
    )'''
new = '''    return or_(  # MUTATION M6: the terminal predicate disabled — nothing matches
        func.coalesce(MemoirEpisode.embedding_attempts, 0) < 0,
        func.coalesce(MemoirEpisode.embedding_error, "").like(
            f"{MEMOIR_EMBEDDING_TERMINAL_MARKER}%MUTATION-NEVER"
        ),
    )'''
if text.count(old) != 1:
    sys.exit(
        "MUTATION PLANT FAILED: expected exactly one occurrence of the "
        f"terminal predicate (SQL form), found {text.count(old)}"
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

LOG="$LOG_DIR/M6-terminal-filter-disabled.log"
timeout "${TIMEOUT_S}" "${SCRUB[@]}" "${PY}" -m pytest \
  "${TEST_FILE}::test_the_runbook_requeue_procedure_revives_a_terminal_row" \
  -q --tb=line -p no:randomly --override-ini="addopts=" -rfE >"${LOG}" 2>&1
MUT_RC=$?
echo "--- leg 2 mutated exit=${MUT_RC} ---"
restore
trap - EXIT INT TERM

if [ ${MUT_RC} -eq 0 ]; then
  echo "RESULT: FAIL (the mutation did NOT turn the target RED — the"
  echo "        terminal-skip pin is not mutation-meaningful)"
  exit 1
fi
if [ ${MUT_RC} -eq 124 ]; then
  echo "RESULT: FAIL (the mutated run TIMED OUT instead of failing)"
  exit 1
fi
FAILED_IDS=$(grep -E "^FAILED " "${LOG}" | sed 's/^FAILED //' || true)
if [ -z "${FAILED_IDS}" ]; then
  echo "RESULT: FAIL (no FAILED node id captured — vacuous red)"
  exit 1
fi
echo "MUTATION LEG M6: RED as required; FAILED node id(s):"
echo "${FAILED_IDS}" | sed 's/^/    /'

# ── Leg 3: after the restore the suite is GREEN again ────────────────────
run_suite "leg 3 restored (full suite)" "${TEST_FILE}"
RESTORE_RC=$?
if [ ${RESTORE_RC} -ne 0 ]; then
  echo "RESULT: FAIL (the suite is not green after the restore: rc=${RESTORE_RC})"
  exit 1
fi

echo "PACK DURATION: $((SECONDS - PACK_START))s"
echo "RESULT: PASS"
exit 0
