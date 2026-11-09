#!/usr/bin/env bash
# Test Pack: memoir_v2_reflection_pg_test
#
# Memoir Store v2 — phase 2, the REFLECTION lane on REAL PostgreSQL
# (plan: .agents/shared/planning/ari-memoir-store/v2-phase2-plan.md AC3;
# module tests/postgres/test_memoir_v2_reflection_pg.py).
#
# Scope:
#   * tests/postgres/test_memoir_v2_reflection_pg.py — the PG-only legs:
#     the DERIVED TRIGGER query with its dialect-neutral window binding
#     (ND-10), the COALESCE(poignancy, importance) effective-importance
#     sum, the CLAIM compare-and-set with the 15-minute stale takeover
#     (V2-35 / SF5), the atomic commit_pass (the marks + the nightly
#     date in ONE transaction), the C2 catch-up scan without the window,
#     the synthesis dedup key, the atomic supersession with its
#     concurrent double-claim guard, and the record_line pass_id guard.
#
# The pack creates ITS OWN throwaway database on the NATIVE local
# PostgreSQL server (never Docker), declares it through
# PG_TEST_ALLOWED_DBS (the house fence: exact-name membership, never a
# pattern), runs the lane through the env-scrub wrapper, and DROPS the
# database at teardown. It NEVER touches ensemble_prod / ensemble_dev /
# ensemble_prod_test — the names are refused by name AND the pack
# verifies `SELECT current_database()` before the run.
#
# Dual-layer timeout: Layer 1 = the caller's outer timeout; Layer 2 =
# the internal watchdog below (280 s — under the outer cap so this
# pack's own RESULT verdict always lands first).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

PACK_NAME="memoir_v2_reflection_pg_test"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RESULT="FAIL (pack did not run)"
LOG=""
SCRATCH_DB=""
cleanup() {
  if [ -n "${SCRATCH_DB}" ]; then
    PGPASSWORD="${PG_TEST_PASSWORD:-ensemble_dev}" \
      psql -h "${PG_TEST_HOST:-localhost}" -p "${PG_TEST_PORT:-5432}" \
      -U "${PG_TEST_USER:-ensemble}" -d postgres \
      -c "DROP DATABASE IF EXISTS \"${SCRATCH_DB}\";" >/dev/null 2>&1 || true
  fi
  echo "RESULT: ${RESULT}"
  if [ -n "${LOG}" ] && [ -f "${LOG}" ]; then
    rm -f "${LOG}"
  fi
}
trap cleanup EXIT

cd "${REPO_ROOT}" || {
  RESULT="FAIL (cannot cd to ${REPO_ROOT})"
  exit 1
}

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    ${REPO_ROOT}"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

TARGET=(
  tests/postgres/test_memoir_v2_reflection_pg.py
)

MISSING=0
for t in "${TARGET[@]}"; do
  if [ ! -f "$t" ]; then
    echo "FAIL: missing test module '$t'"
    MISSING=1
  fi
done
if [ "${MISSING}" -ne 0 ]; then
  RESULT="FAIL (a declared test module is missing — the leg would be vacuous)"
  exit 1
fi

# ── the throwaway database ────────────────────────────────────────────────
export PG_TEST_HOST="${PG_TEST_HOST:-localhost}"
export PG_TEST_PORT="${PG_TEST_PORT:-5432}"
export PG_TEST_USER="${PG_TEST_USER:-ensemble}"
export PG_TEST_PASSWORD="${PG_TEST_PASSWORD:-ensemble_dev}"

SCRATCH_DB="ensemble_memoir_v2_reflect_pg_$$"
export PG_TEST_DB="${SCRATCH_DB}"
export PG_TEST_ALLOWED_DBS="${SCRATCH_DB}"

# THE LIVE-DATABASE REFUSAL (by EXACT name — never a pattern).
for live_db in ensemble_prod ensemble_dev ensemble_prod_test; do
  if [ "${SCRATCH_DB}" = "${live_db}" ]; then
    RESULT="FAIL (the scratch database name is a LIVE database name)"
    exit 1
  fi
done

echo "Scratch DB: ${SCRATCH_DB}"

if ! PGPASSWORD="${PG_TEST_PASSWORD}" psql \
  -h "${PG_TEST_HOST}" -p "${PG_TEST_PORT}" -U "${PG_TEST_USER}" -d postgres \
  -c "CREATE DATABASE \"${SCRATCH_DB}\";" >/dev/null 2>&1; then
  RESULT="FAIL (could not create the throwaway database ${SCRATCH_DB})"
  exit 1
fi

# THE WRITE-BEARING SAFETY CHECK — the run must be against the
# throwaway database, never a live one.
CURRENT_DB="$(PGPASSWORD="${PG_TEST_PASSWORD}" psql \
  -h "${PG_TEST_HOST}" -p "${PG_TEST_PORT}" -U "${PG_TEST_USER}" \
  -d "${SCRATCH_DB}" -tAc "SELECT current_database();" 2>/dev/null | tr -d '[:space:]')"
if [ "${CURRENT_DB}" != "${SCRATCH_DB}" ]; then
  RESULT="FAIL (current_database() is '${CURRENT_DB}', expected the throwaway '${SCRATCH_DB}')"
  exit 1
fi
echo "current_database(): ${CURRENT_DB}"

#: The MEASURED node count at the pin (2026-11-09, this worktree):
#: `-lt 1` let a silently-shortened selection pass; an exact count
#: turns "a test disappeared" into a RED instead of a quieter green
#: (tidier M12). Re-measure and move this constant deliberately.
EXPECTED_PASSED_N=17

LOG="$(mktemp -t memoir_v2_reflection_pg.XXXXXX)"

# FAIL LOUD (the false-green class): a bare `python3` fallback can
# import `daemon` from the MAIN checkout, so a "pass" would describe a
# tree that is not this worktree. The interpreter is resolved FIRST.
PG_PY=""
if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  PG_PY="${REPO_ROOT}/.venv/bin/python"
elif [ -n "${MEMOIR_PACK_PYTHON:-}" ] && [ -x "${MEMOIR_PACK_PYTHON}" ]; then
  PG_PY="${MEMOIR_PACK_PYTHON}"
fi
if [ -z "${PG_PY}" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
echo "Interpreter: ${PG_PY}"

if [ -x ".venv/bin/pytest" ]; then
  # The house path: the env-scrub wrapper (it hardcodes
  # ``exec .venv/bin/pytest`` and fails loud on a leaked connection var).
  RUNNER=(./scripts/run_tests_scrubbed.sh)
else
  # The worktree path: a sibling worktree has no .venv of its own, so the
  # wrapper cannot exec. The SAME scrub is performed here, per variable
  # (never the ``env -u POSTGRES_*`` glob form, which is a silent NO-OP).
  echo "NOTE: .venv/bin/pytest is absent — using the per-variable scrub fallback"
  RUNNER=(env
    -u POSTGRES_HOST -u POSTGRES_DB -u POSTGRES_USER
    -u POSTGRES_PASSWORD -u POSTGRES_PORT -u POSTGRES_URL
    -u DATABASE_URL_POSTGRES
    -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD
    -u PGPASSFILE -u PGSSLMODE -u PGSERVICE -u PGSERVICEFILE
    -u PLANE_WORKSPACE_SLUG -u PLANE_BASE_URL
    "${PG_PY}" -m pytest)
fi

timeout 280s "${RUNNER[@]}" \
  --override-ini="addopts=" -m postgres \
  "${TARGET[@]}" \
  -q --tb=short -rs --timeout=120 >"${LOG}" 2>&1
RC="$?"

cat "${LOG}"

if [ "${RC}" -eq 124 ]; then
  RESULT="TIMEOUT (internal 280s cap exceeded)"
  exit 124
fi

# Explicit collected-count guard — GREEN-AT-ZERO IS A FAILURE.
SUMMARY="$(grep -Eo '[0-9]+ passed' "${LOG}" | tail -1 || true)"
case "${SUMMARY}" in
  [0-9]*) PASSED_N="${SUMMARY%% *}" ;;
  *)      PASSED_N="" ;;
esac
if [ -z "${PASSED_N}" ]; then
  RESULT="FAIL (rc=${RC}, collected-count check: no \"N passed\" in summary — collection/selection failure or a fully-skipped module; GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "${PASSED_N}" -ne "${EXPECTED_PASSED_N}" ]; then
  RESULT="FAIL (rc=${RC}, passed=${PASSED_N} != EXPECTED_PASSED_N=${EXPECTED_PASSED_N} — the node count moved without the pin moving)"
  exit 1
fi
if [ "${RC}" -ne 0 ]; then
  RESULT="FAIL (rc=${RC}, ${PASSED_N} passed before failure)"
  exit 1
fi

RESULT="PASS (${PASSED_N} passed, rc=${RC})"
exit 0
