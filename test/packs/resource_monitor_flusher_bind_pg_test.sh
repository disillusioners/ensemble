#!/usr/bin/env bash
# Test Pack: resource_monitor_flusher_bind_pg_test — the resource
# monitor's FLUSHER BIND PATH against REAL PostgreSQL.
#
# Scope: tests/postgres/test_resource_monitor_flusher_bind_pg.py —
#   * the REAL sampler row travels through the REAL flusher into REAL
#     PostgreSQL (the chain that was DARK in production);
#   * EVERY column the flusher binds is type-audited against the LIVE
#     information_schema type AND against a HARD-CODED expected type
#     (B7a — the bound-column list is derived from the flusher SOURCE,
#     so a model mutation can no longer hide a drift);
#   * the bound-column SET is compared for EQUALITY against a
#     hardcoded expected set, so a REMOVED column fails too (m8);
#   * EVERY bound T2 column is SELECTed back and value-asserted (B7b),
#     including the written graph_cache_present=False, the
#     value-asserted bucket_ts and the estimated DEFAULT-TRUE path;
#   * the DEGRADED SENTINEL row (the B1 evidence path for a dropped
#     sample) lands in T1 with its marker and its NULL metrics, and
#     does NOT pollute the sample tiers;
#   * the sampler-side bucket_ts stamp (G3): a multi-tick window lands
#     MULTIPLE distinct rows, and each sentinel of a window carries
#     the drop count as of ITS OWN drop (G1/T-1).
#
# Marker flags (mandatory — the default addopts EXCLUDE the postgres
# marker, so a run without them collects 0 tests and reports a
# VACUOUS GREEN):
#   --override-ini="addopts="   → drop the default -m filter
#   -m postgres                 → select the postgres-marked tests
#
# SELF-CONTAINED SCRATCH DISCIPLINE (G4 — the pack must run on a host
# where the scratch database does NOT pre-exist, and must leave NO
# residue):
#   * the pack CREATES its own throwaway database and its own LOGIN
#     role, and DROPS both in the EXIT trap;
#   * the database name is the documented one
#     (ensemble_test_only_rmflusher) and the role owns NOTHING else —
#     the shared PG fence's OWNERSHIP rejection (a rejection-side
#     signal, never an acceptance rule) requires the scratch database
#     NOT to be co-owned with production;
#   * an AMBIENT PG_TEST_DB is NEVER used as the target — the pack
#     sets its own. The script asserts the allowlist membership of
#     its own target IN THE SCRIPT ITSELF (m11);
#   * psql / createdb / dropdb must exist — the pack checks and fails
#     LOUDLY otherwise (m11).
#
# Every PG_* / PG_TEST_* variable is set EXPLICITLY for the pytest
# process (an ambient POSTGRES_* is never trusted and never inherited
# as the target).
#
# Usage: bash test/packs/resource_monitor_flusher_bind_pg_test.sh
# Per-leg pytest rc: 0 ok / 1 test failure / 5 nothing collected
#   (collection error, or the -m postgres filter matched nothing) /
#   124 leg timeout (outer `timeout 300s`).
# Pack exit: 0 PASS / 1 FAIL / 124 TIMEOUT. FAIL covers ANY rc!=0
#   (incl. 5) AND a summary missing "N passed" with N>=1 (the explicit
#   collected-count check — GREEN-AT-ZERO IS A FAILURE).
set -euo pipefail

# The RESULT label is emitted by the EXIT trap (the repo's convention —
# see PACKS.md), so an UNEXPECTED abort still reports a FAIL. The
# default is a FAIL: the pack did not run to completion.
RESULT="FAIL (pack did not run)"
LOG=""
ADMIN_USER=""

on_exit() {
  echo "RESULT: ${RESULT}"
  if [ -n "${LOG}" ] && [ -f "${LOG}" ]; then
    rm -f "${LOG}"
  fi
  if declare -F cleanup >/dev/null 2>&1; then
    cleanup
  fi
}
trap on_exit EXIT

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: resource_monitor_flusher_bind_pg_test ==="

cd "$PROJECT_DIR"

# --- toolchain -----------------------------------------------------------
for bin in psql createdb dropdb; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    RESULT="FAIL (required binary '${bin}' is not on PATH — the pack cannot create or drop its own scratch database)"
    exit 1
  fi
done

if [ ! -x ".venv/bin/pytest" ]; then
  RESULT="FAIL (no .venv/bin/pytest in ${PROJECT_DIR})"
  exit 1
fi

# --- the pack's OWN throwaway database + role ----------------------------
PG_ADMIN_USER="${PG_ADMIN_USER:-ensemble}"
PG_ADMIN_PASSWORD="${PG_ADMIN_PASSWORD:-ensemble_dev}"
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"

#: The ONE documented throwaway database name of this pack. It is a
#: member of the shared fence's EXACT allowlist
#: (tests/postgres/conftest.py:ALLOWED_SCRATCH_DBS).
SCRATCH_DB="ensemble_test_only_rmflusher"
#: The pack's dedicated LOGIN role — it owns the scratch database and
#: NOTHING else (the ownership rejection requires the scratch DB not to
#: be co-owned with production).
SCRATCH_ROLE="ensemble_rmflusher_pack"
SCRATCH_PW="pack_$(head -c12 /dev/urandom | od -An -tx1 | tr -d ' \n')"

#: The shared fence's allowlist — DUPLICATED HERE ON PURPOSE (m11): the
#: script asserts its own target's membership BEFORE it creates
#: anything, so a rename of the throwaway database fails loudly HERE
#: instead of deep inside pytest.
ALLOWED_DBS="ensemble_test ensemble_dev_test_only ensemble_test_only ensemble_test_only_rmflusher"

case " ${ALLOWED_DBS} " in
  *" ${SCRATCH_DB} "*)
    echo "[fence] scratch database '${SCRATCH_DB}' is in the pack's allowlist"
    ;;
  *)
    RESULT="FAIL (the pack's scratch database '${SCRATCH_DB}' is not in the allowlist [${ALLOWED_DBS}] — the shared PG fence would refuse it)"
    exit 1
    ;;
esac

cleanup() {
  # Leave NO residue: the database and the role go away with the pack.
  if [ -z "$ADMIN_USER" ]; then
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

# The admin role needs CREATEROLE (to create the pack's dedicated LOGIN)
# and CREATEDB (to create the database). The candidates are tried in
# order; the first one that connects AND holds those privileges wins.
ADMIN_CANDIDATES=()
if [ -n "${PG_ADMIN_USER:-}" ]; then
  ADMIN_CANDIDATES+=("$PG_ADMIN_USER")
fi
ADMIN_CANDIDATES+=(postgres "$(whoami)" ensemble)

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
  RESULT="FAIL (no admin role with CREATEROLE+CREATEDB could connect on ${PG_HOST}:${PG_PORT} — set PG_ADMIN_USER / PG_ADMIN_PASSWORD)"
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
  RESULT="FAIL (could not create the scratch role '${SCRATCH_ROLE}' on ${PG_HOST}:${PG_PORT} as '${ADMIN_USER}')"
  exit 1
fi

if ! PGPASSWORD="$PG_ADMIN_PASSWORD" createdb \
      -h "$PG_HOST" -p "$PG_PORT" -U "$ADMIN_USER" \
      -O "$SCRATCH_ROLE" "$SCRATCH_DB"; then
  RESULT="FAIL (could not create the scratch database '${SCRATCH_DB}' on ${PG_HOST}:${PG_PORT} as '${ADMIN_USER}')"
  exit 1
fi
echo "[setup] scratch role=${SCRATCH_ROLE} db=${SCRATCH_DB} (created by this pack; dropped in the EXIT trap)"

LOG="$(mktemp -t rm_flusher_bind_pg.XXXXXX)"

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
    tests/postgres/test_resource_monitor_flusher_bind_pg.py \
    --override-ini="addopts=" -m postgres -q --tb=short -p no:randomly \
    >"$LOG" 2>&1
RC="$?"
set -e

cat "$LOG"

if [ "$RC" -eq 124 ]; then
  RESULT="TIMEOUT"
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
  RESULT="FAIL (rc=${RC}, collected-count check: no \"N passed\" N>=1 in summary — collection/selection failure or a fully-skipped module; GREEN-AT-ZERO IS A FAILURE)"
  exit 1
fi
if [ "$RC" -ne 0 ]; then
  RESULT="FAIL (rc=${RC}, ${PASSED_N} passed before failure)"
  exit 1
fi

echo "[leg ok] ${PASSED_N} passed (rc=${RC})"
RESULT="PASS"
exit 0
