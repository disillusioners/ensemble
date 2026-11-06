#!/usr/bin/env bash
# Test Pack: memoir_pg_corpus_test
#
# Memoir Store v1 — gate G2 (gate worker 2), the AUTHORITATIVE real-PG
# corpus legs not already covered by the two registered PG packs
# (plan: .agents/shared/planning/ari-memoir-store/phase7-plan.md D7.6).
#
# Scope: tests/postgres/test_memoir_pg_corpus.py — the PG-only behavior
# legs:
#   * keepsake pin ADMISSION against the 4,000-token zone (D2.12);
#   * the DR-1 render-time zone arithmetic on real PG rows (the
#     24,000-char ceiling rung, the 48,000-char withhold rung, the
#     nominal three-zone block, keepsakes never double-rendered);
#   * TWO-AXIS TIME (DD-7): the window filters event_at, the recency
#     prior runs on the write axis;
#   * the persona SEED path + the HUMAN-LANE GUARD on real PG rows;
#   * RRF fusion of the tsvector ∥ cosine lanes + the recency prior +
#     the keepsake exemption;
#   * ACCESS STATS on EXPLICIT recall only (both directions).
#
# THE FENCE: the pack creates ITS OWN throwaway database on the cluster
# the PG_TEST_* variables name, ASSERTS the resolved target
# (current_database()) before anything runs, and DROPS the database at
# teardown. It NEVER touches ensemble_prod / ensemble_dev. The ambient
# POSTGRES_* environment is scrubbed PER VARIABLE (never the
# ``env -u POSTGRES_*`` glob form — a silent NO-OP).
#
# Dual-layer timeout: Layer 1 = the caller's outer timeout; Layer 2 =
# the internal watchdog below (280 s — under the outer cap so this
# pack's own RESULT verdict always lands first).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
set -uo pipefail

PACK_NAME="memoir_pg_corpus_test"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "${REPO_ROOT}" || exit 1

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo: ${REPO_ROOT}"

# ── the interpreter (fail loud — never a bare python3 fallback) ──────────
PY=""
if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  PY="${REPO_ROOT}/.venv/bin/python"
elif [ -n "${MEMOIR_PACK_PYTHON:-}" ] && [ -x "${MEMOIR_PACK_PYTHON}" ]; then
  PY="${MEMOIR_PACK_PYTHON}"
fi
if [ -z "${PY}" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — neither ${REPO_ROOT}/.venv/bin/python nor MEMOIR_PACK_PYTHON is executable)"
  exit 1
fi
echo "Interpreter: ${PY}"

# ── the connection parameters (explicit; never inherited) ─────────────────
PG_HOST="${PG_TEST_HOST:-127.0.0.1}"
PG_PORT="${PG_TEST_PORT:-5432}"
PG_USER="${PG_TEST_USER:-ensemble}"
PG_PASSWORD="${PG_TEST_PASSWORD:-ensemble_dev}"

SCRATCH_DB="ensemble_memoir_pg_corpus_$$"

cleanup() {
  if [ -n "${SCRATCH_DB}" ]; then
    PGPASSWORD="${PG_PASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" \
      -U "${PG_USER}" -d postgres \
      -c "DROP DATABASE IF EXISTS \"${SCRATCH_DB}\";" >/dev/null 2>&1 || true
    echo "Scratch DB dropped: ${SCRATCH_DB}"
  fi
}
trap cleanup EXIT

if ! command -v psql >/dev/null 2>&1; then
  echo "RESULT: FAIL (psql is not on PATH — the PG pack cannot create its scratch database)"
  exit 1
fi

if ! PGPASSWORD="${PG_PASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" \
  -U "${PG_USER}" -d postgres \
  -c "CREATE DATABASE \"${SCRATCH_DB}\";" >/dev/null 2>&1; then
  echo "RESULT: FAIL (could not create the throwaway database ${SCRATCH_DB})"
  exit 1
fi
echo "Scratch DB: ${SCRATCH_DB}"

# ── THE TARGET ASSERTION ──────────────────────────────────────────────────
RESOLVED="$(PGPASSWORD="${PG_PASSWORD}" psql \
  "host=${PG_HOST} port=${PG_PORT} dbname=${SCRATCH_DB} user=${PG_USER}" \
  -X -A -t -c "SELECT current_database();" 2>/dev/null | tr -d '[:space:]')"
echo "Target assertion: current_database() = '${RESOLVED}' (expected '${SCRATCH_DB}')"
if [ "${RESOLVED}" != "${SCRATCH_DB}" ]; then
  echo "RESULT: FAIL (TARGET ASSERTION FAILED — the resolved database is '${RESOLVED}', not the scratch '${SCRATCH_DB}')"
  exit 1
fi
case "${RESOLVED}" in
  ensemble_prod|ensemble_dev|ensemble_prod_test)
    echo "RESULT: FAIL (TARGET ASSERTION FAILED — the resolved database is a LIVE database)"
    exit 1
    ;;
esac

export PG_TEST_HOST="${PG_HOST}"
export PG_TEST_PORT="${PG_PORT}"
export PG_TEST_USER="${PG_USER}"
export PG_TEST_PASSWORD="${PG_PASSWORD}"
export PG_TEST_DB="${SCRATCH_DB}"
export PG_TEST_ALLOWED_DBS="${SCRATCH_DB}"

TARGET=(tests/postgres/test_memoir_pg_corpus.py)

MISSING=0
for t in "${TARGET[@]}"; do
  if [ ! -f "$t" ]; then
    echo "FAIL: missing test module '$t'"
    MISSING=1
  fi
done
if [ "${MISSING}" -ne 0 ]; then
  echo "RESULT: FAIL (a declared test module is missing — the leg would be vacuous)"
  exit 1
fi

#: The MEASURED node count at the pin (2026-11-06, this worktree, G2).
EXPECTED_PASSED_N=13

LOG="$(mktemp -t memoir_pg_corpus.XXXXXX)"

# The per-variable env scrub (never the glob form) — the ambient
# POSTGRES_* points at PRODUCTION and must not reach the test process.
timeout 280 env \
  -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_HOST -u POSTGRES_PORT \
  -u POSTGRES_PASSWORD -u DATABASE_URL -u DATABASE_URL_POSTGRES \
  -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD \
  -u PGPASSFILE -u PGSSLMODE -u PGSERVICE -u PGSERVICEFILE \
  -u PORT -u DAEMON_PORT \
  "${PY}" -m pytest "${TARGET[@]}" \
  --override-ini="addopts=" -m postgres \
  -q --tb=short -rfE -p no:randomly >"${LOG}" 2>&1
RC=$?
cat "${LOG}"

if [ "${RC}" -eq 124 ]; then
  echo "RESULT: TIMEOUT (280s cap exceeded)"
  exit 124
fi

SUMMARY="$(grep -Eo '[0-9]+ passed' "${LOG}" | tail -1 || true)"
case "${SUMMARY}" in
  [0-9]*) PASSED_N="${SUMMARY%% *}" ;;
  *)      PASSED_N="" ;;
esac
if [ -z "${PASSED_N}" ]; then
  echo "RESULT: FAIL (rc=${RC}, collected-count check: no \"N passed\" in summary — GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "${PASSED_N}" -ne "${EXPECTED_PASSED_N}" ]; then
  echo "RESULT: FAIL (rc=${RC}, passed=${PASSED_N} != EXPECTED_PASSED_N=${EXPECTED_PASSED_N} — the node count moved without the pin moving)"
  exit 1
fi
if [ "${RC}" -ne 0 ]; then
  echo "RESULT: FAIL (rc=${RC}, ${PASSED_N} passed before failure)"
  exit 1
fi

echo "RESULT: PASS (${PASSED_N} passed, rc=${RC})"
exit 0
