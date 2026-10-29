#!/usr/bin/env bash
# Test Pack: metrics_census_pg_test — the metrics PostgreSQL census
# against REAL PostgreSQL.
#
# Scope: tests/postgres/test_metrics_census_pg.py — the census's
#   * DIRECT, NON-POOLED connection (never the manager pool, never the
#     checkpoint pool);
#   * connection identity: application_name='ensemble-metrics-census'
#     + statement_timeout=5 s (+ connect_timeout=3 s);
#   * the live pg_stat_activity read;
#   * the self-exclusion rule (ONLY the census's own application_name
#     is excluded — every other backend is counted);
#   * the server-side fragment->site map (a live idle-in-transaction
#     backend is classified; the sum-ties invariant holds);
#   * the SCRATCH-DB FENCE ordering (a disallowed DB name errors
#     BEFORE any connect attempt; an allowed name reaches the probe).
#
# Marker flags (mandatory — the default addopts EXCLUDE the postgres
# marker, so a run without them collects 0 tests and reports a
# VACUOUS GREEN):
#   --override-ini="addopts="   → drop the default -m filter
#   -m postgres                 → select the postgres-marked tests
#
# SELF-CONTAINED SCRATCH DISCIPLINE:
#   * the pack CREATES its own throwaway database and its own LOGIN
#     role, and DROPS both in the EXIT trap;
#   * the database name is one of the module's EXACT-membership
#     allowlist names (tests/postgres/test_metrics_census_pg.py:
#     ALLOWED_SCRATCH_DBS) — the script asserts its own target's
#     membership IN THE SCRIPT ITSELF, so a rename fails loudly HERE
#     instead of deep inside pytest;
#   * an AMBIENT PG_TEST_DB is NEVER used as the target — the pack sets
#     its own. Every PG_* / PG_TEST_* variable is set EXPLICITLY for
#     the pytest process.
#
# Usage: bash test/packs/metrics_census_pg_test.sh
# Per-leg pytest rc: 0 ok / 1 test failure / 5 nothing collected
#   (collection error, or the -m postgres filter matched nothing) /
#   124 leg timeout (outer `timeout 300s`).
# Pack exit: 0 PASS / 1 FAIL / 124 TIMEOUT. FAIL covers ANY rc!=0
#   (incl. 5) AND a summary missing "N passed" with N>=1 (the explicit
#   collected-count check — GREEN-AT-ZERO IS A FAILURE).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: metrics_census_pg_test ==="

cd "$PROJECT_DIR"

# --- toolchain -----------------------------------------------------------
for bin in psql createdb dropdb; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "RESULT: FAIL (required binary '${bin}' is not on PATH — the pack cannot create or drop its own scratch database)"
    exit 1
  fi
done

if [ ! -x ".venv/bin/pytest" ]; then
  echo "RESULT: FAIL (no .venv/bin/pytest in ${PROJECT_DIR})"
  exit 1
fi

# --- the pack's OWN throwaway database + role ----------------------------
PG_ADMIN_USER="${PG_ADMIN_USER:-ensemble}"
PG_ADMIN_PASSWORD="${PG_ADMIN_PASSWORD:-ensemble_dev}"
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"

#: The ONE documented throwaway database name of this pack. It is an
#: EXACT member of the module's allowlist
#: (tests/postgres/test_metrics_census_pg.py:ALLOWED_SCRATCH_DBS).
SCRATCH_DB="ensemble_dev_test_only"
#: The pack's dedicated LOGIN role — it owns the scratch database and
#: NOTHING else.
SCRATCH_ROLE="ensemble_metrics_census_pack"
SCRATCH_PW="pack_$(head -c12 /dev/urandom | od -An -tx1 | tr -d ' \n')"

#: The shared fence's allowlist — DUPLICATED HERE ON PURPOSE: the
#: script asserts its own target's membership BEFORE it creates
#: anything, so a rename of the throwaway database fails loudly HERE
#: instead of deep inside pytest.
ALLOWED_DBS="ensemble_dev_test_only ensemble_test_only"

case " ${ALLOWED_DBS} " in
  *" ${SCRATCH_DB} "*)
    echo "[fence] scratch database '${SCRATCH_DB}' is in the pack's allowlist"
    ;;
  *)
    echo "RESULT: FAIL (the pack's scratch database '${SCRATCH_DB}' is not in the allowlist [${ALLOWED_DBS}] — the module's fence would refuse it)"
    exit 1
    ;;
esac

cleanup() {
  # Leave NO residue: the database and the role go away with the pack.
  if [ -z "${ADMIN_USER:-}" ]; then
    return 0
  fi
  PGPASSWORD="$PG_ADMIN_PASSWORD" dropdb \
    -h "$PG_HOST" -p "$PG_PORT" -U "$ADMIN_USER" \
    --if-exists "$SCRATCH_DB" >/dev/null 2>&1 || true
  PGPASSWORD="$PG_ADMIN_PASSWORD" psql \
    -h "$PG_HOST" -p "$PG_PORT" -U "$ADMIN_USER" -d postgres \
    -v ON_ERROR_STOP=1 \
    -c "DROP ROLE IF EXISTS \"${SCRATCH_ROLE}\";" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# The admin role needs CREATEROLE (to create the pack's dedicated LOGIN)
# and CREATEDB (to create the database). The candidates are tried in
# order; the first one that connects AND holds those privileges wins.
ADMIN_CANDIDATES=()
if [ -n "${PG_ADMIN_USER:-}" ]; then
  ADMIN_CANDIDATES+=("$PG_ADMIN_USER")
fi
ADMIN_CANDIDATES+=(postgres "$(whoami)" ensemble)

ADMIN_USER=""
for cand in "${ADMIN_CANDIDATES[@]}"; do
  if PGPASSWORD="${PG_ADMIN_PASSWORD:-}" psql \
        -h "$PG_HOST" -p "$PG_PORT" -U "$cand" -d postgres -tAc \
        "SELECT (rolsuper OR rolcreaterole) AND rolcreatedb
         FROM pg_roles WHERE rolname = current_user" 2>/dev/null \
        | grep -q t; then
    ADMIN_USER="$cand"
    break
  fi
done
if [ -z "$ADMIN_USER" ]; then
  echo "RESULT: FAIL (no admin role with CREATEROLE+CREATEDB could connect on ${PG_HOST}:${PG_PORT} — set PG_ADMIN_USER / PG_ADMIN_PASSWORD)"
  exit 1
fi
echo "[setup] admin role=${ADMIN_USER}"

# Residue from a previous (killed) run of THIS pack is removed first —
# the names are owned by this pack alone.
cleanup

if ! PGPASSWORD="$PG_ADMIN_PASSWORD" psql \
      -h "$PG_HOST" -p "$PG_PORT" -U "$ADMIN_USER" -d postgres \
      -v ON_ERROR_STOP=1 \
      -c "CREATE ROLE \"${SCRATCH_ROLE}\" LOGIN PASSWORD '${SCRATCH_PW}';" \
      >/dev/null; then
  echo "RESULT: FAIL (could not create the scratch role '${SCRATCH_ROLE}' on ${PG_HOST}:${PG_PORT} as '${ADMIN_USER}')"
  exit 1
fi

if ! PGPASSWORD="$PG_ADMIN_PASSWORD" createdb \
      -h "$PG_HOST" -p "$PG_PORT" -U "$ADMIN_USER" \
      -O "$SCRATCH_ROLE" "$SCRATCH_DB"; then
  echo "RESULT: FAIL (could not create the scratch database '${SCRATCH_DB}' on ${PG_HOST}:${PG_PORT} as '${ADMIN_USER}')"
  exit 1
fi
echo "[setup] scratch role=${SCRATCH_ROLE} db=${SCRATCH_DB} (created by this pack; dropped in the EXIT trap)"

LOG="$(mktemp -t metrics_census_pg.XXXXXX)"
trap 'rm -f "$LOG"; cleanup' EXIT

# Run the leg NON-FATALLY and capture its rc: under `set -euo pipefail`
# a failing leg would kill the pack at the pytest line itself, leaving
# the RESULT labels below unreachable. A red leg still fails the pack
# at the checks below (fail-closed preserved).
set +e
timeout 300s env \
  PG_TEST_HOST="$PG_HOST" \
  PG_TEST_PORT="$PG_PORT" \
  PG_TEST_DB="$SCRATCH_DB" \
  PG_TEST_USER="$SCRATCH_ROLE" \
  PG_TEST_PASSWORD="$SCRATCH_PW" \
  .venv/bin/pytest \
    tests/postgres/test_metrics_census_pg.py \
    --override-ini="addopts=" -m postgres -q --tb=short -p no:randomly \
    >"$LOG" 2>&1
RC="$?"
set -e

cat "$LOG"

if [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
fi

# Explicit ran-count assertion: the summary must show "N passed" with
# N>=1. "no tests ran" (pytest rc=5 no-collect), a collection error and
# a fully-skipped module all land here with a loud label instead of
# relying on pytest's rc semantics alone.
SUMMARY="$(grep -Eo '[0-9]+ passed' "$LOG" | tail -1 || true)"
case "$SUMMARY" in
  [0-9]*) PASSED_N="${SUMMARY%% *}" ;;
  *)      PASSED_N="" ;;
esac
if [ -z "$PASSED_N" ] || [ "$PASSED_N" -lt 1 ]; then
  echo "RESULT: FAIL (rc=${RC}, collected-count check: no \"N passed\" N>=1 in summary — collection/selection failure or a fully-skipped module; GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "$RC" -ne 0 ]; then
  echo "RESULT: FAIL (rc=${RC}, ${PASSED_N} passed before failure)"
  exit 1
fi

echo "[leg ok] ${PASSED_N} passed (rc=${RC})"
echo "RESULT: PASS"
exit 0
