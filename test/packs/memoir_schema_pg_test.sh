#!/usr/bin/env bash
# Test Pack: memoir_schema_pg_test
#
# Memoir Store v1 — phase 1, the PostgreSQL lane (plan:
# .agents/shared/planning/ari-memoir-store/phase1-plan.md AC3).
#
# Scope:
#   * tests/postgres/test_memoir_schema_pg.py — the PG-only legs:
#     the PG-built schema (the four tables, the MODEL-bound CHECK
#     constraints, TEXT[] stars, JSONB embedding), the `tsv` generated
#     column created by the ENSURE, the AM-8 RED condition (the
#     one-argument to_tsvector is rejected by PostgreSQL), the GIN index
#     + EXPLAIN, the ensure's idempotence, and the ONE-TRANSACTION blast
#     radius (a failing memoir statement rolls back an inherited lane's
#     statement in the same transaction).
#
# The pack creates ITS OWN throwaway database, declares it through
# PG_TEST_ALLOWED_DBS (the house fence: exact-name membership, never a
# pattern), runs the lane through the env-scrub wrapper, and DROPS the
# database at teardown. It NEVER touches ensemble_prod / ensemble_dev.
#
# Dual-layer timeout: Layer 1 = the caller's outer timeout; Layer 2 =
# the internal watchdog below (280 s — under the outer cap so this
# pack's own RESULT verdict always lands first).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

PACK_NAME="memoir_schema_pg_test"
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

if [ ! -x "scripts/run_tests_scrubbed.sh" ]; then
  RESULT="FAIL (no scripts/run_tests_scrubbed.sh in ${REPO_ROOT})"
  exit 1
fi

TARGET=(
  tests/postgres/test_memoir_schema_pg.py
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

SCRATCH_DB="ensemble_memoir_pg_$$"
export PG_TEST_DB="${SCRATCH_DB}"
export PG_TEST_ALLOWED_DBS="${SCRATCH_DB}"

echo "Scratch DB: ${SCRATCH_DB}"

if ! PGPASSWORD="${PG_TEST_PASSWORD}" psql \
  -h "${PG_TEST_HOST}" -p "${PG_TEST_PORT}" -U "${PG_TEST_USER}" -d postgres \
  -c "CREATE DATABASE \"${SCRATCH_DB}\";" >/dev/null 2>&1; then
  RESULT="FAIL (could not create the throwaway database ${SCRATCH_DB})"
  exit 1
fi

LOG="$(mktemp -t memoir_schema_pg.XXXXXX)"

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
    "${MEMOIR_PACK_PYTHON:-python3}" -m pytest)
fi

timeout 280s "${RUNNER[@]}" \
  --override-ini="addopts=" -m postgres \
  "${TARGET[@]}" \
  -q --tb=short -rs >"${LOG}" 2>&1
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
if [ -z "${PASSED_N}" ] || [ "${PASSED_N}" -lt 1 ]; then
  RESULT="FAIL (rc=${RC}, collected-count check: no \"N passed\" N>=1 in summary — collection/selection failure or a fully-skipped module; GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "${RC}" -ne 0 ]; then
  RESULT="FAIL (rc=${RC}, ${PASSED_N} passed before failure)"
  exit 1
fi

RESULT="PASS (${PASSED_N} passed, rc=${RC})"
exit 0
