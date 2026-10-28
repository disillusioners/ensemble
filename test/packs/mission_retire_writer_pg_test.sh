#!/usr/bin/env bash
# Test Pack: mission_retire_writer_pg_test — Phase A verification of the
# RDRS lane-7 (mission-commission retirement) WRITER against REAL PostgreSQL.
#
# THREE separate pytest processes (the mode resolver is restart-read and
# caches once per process — a dry leg and an enforce leg can NEVER share
# one process):
#   1. tests/postgres/test_mission_reconciliation_sweep_pg.py  (ENFORCE)
#      — legs A1-A7 + O2/O3/O4 (satisfy/abandon/TTL/strand/backlog/e2e/
#        idempotence/mixed + the tree-liveness + mint-independence shapes)
#   2. tests/postgres/test_mission_reconciliation_mode_pg.py   (DRY)
#      — H-MODE: counters move, ZERO writes, the gate facade inert
#   3. tests/postgres/test_mission_reconciliation_mode_pg.py   (ENFORCE)
#      — H-MODE: the same code writes under enforce
#
# Scratch discipline (house PG conventions): a THROWAWAY LOGIN role +
#   createdb -O over TCP, dropped at cleanup. NEVER an ambient POSTGRES_*
#   that could reach ensemble_prod — every variable is set explicitly
#   per command (the `env -u POSTGRES_*` glob is a silent NO-OP; never
#   trust it).
#
# Usage: bash test/packs/mission_retire_writer_pg_test.sh
# Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Test Pack: mission_retire_writer_pg_test ==="

# --- scratch role + DB ------------------------------------------------------
SUFFIX="$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
PG_ADMIN_USER="${PG_ADMIN_USER:-ensemble}"
PG_ADMIN_PASSWORD="${PG_ADMIN_PASSWORD:-ensemble_dev}"
PG_HOST="${PG_HOST:-127.0.0.1}"
PG_PORT="${PG_PORT:-5432}"
SCRATCH_ROLE="ens_retire_pack_${SUFFIX}"
SCRATCH_DB="ensemble_retire_pack_${SUFFIX}"
SCRATCH_PW="pack_$(head -c12 /dev/urandom | od -An -tx1 | tr -d ' \n')"

PGPASSWORD="$PG_ADMIN_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_ADMIN_USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE ROLE \"${SCRATCH_ROLE}\" LOGIN PASSWORD '${SCRATCH_PW}';" > /dev/null
PGPASSWORD="$PG_ADMIN_PASSWORD" createdb -h "$PG_HOST" -p "$PG_PORT" -U "$PG_ADMIN_USER" -O "$SCRATCH_ROLE" "$SCRATCH_DB"
echo "[setup] scratch role=${SCRATCH_ROLE} db=${SCRATCH_DB}"

cleanup() {
  PGPASSWORD="$PG_ADMIN_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_ADMIN_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS \"${SCRATCH_DB}\";" > /dev/null 2>&1 || true
  PGPASSWORD="$PG_ADMIN_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_ADMIN_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP ROLE IF EXISTS \"${SCRATCH_ROLE}\";" > /dev/null 2>&1 || true
  echo "[cleanup] scratch dropped"
}
trap cleanup EXIT

FAKE_HOME="$(mktemp -d)"

run_leg() {
  local label="$1" mode_env="$2" target="$3"
  echo "--- leg: ${label} (ENSEMBLE_MISSION_RECORDS=${mode_env}) ---"
  timeout 280s env HOME="$FAKE_HOME" ENSEMBLE_SELF_ENV=dev \
    POSTGRES_HOST="$PG_HOST" POSTGRES_PORT="$PG_PORT" \
    POSTGRES_DB="$SCRATCH_DB" POSTGRES_USER="$SCRATCH_ROLE" POSTGRES_PASSWORD="$SCRATCH_PW" \
    PG_TEST_HOST="$PG_HOST" PG_TEST_PORT="$PG_PORT" \
    PG_TEST_DB="$SCRATCH_DB" PG_TEST_USER="$SCRATCH_ROLE" PG_TEST_PASSWORD="$SCRATCH_PW" \
    ENSEMBLE_MISSION_RECORDS="$mode_env" \
    "${PYTHON_BIN:-/home/nea/ensemble-src/.venv/bin/python}" -m pytest "$target" \
    --override-ini="addopts=" -m postgres -q --tb=short -p no:cacheprovider \
    --timeout=300 2>&1 | tail -5
  local rc="${PIPESTATUS[0]}"
  if [ "$rc" -eq 124 ]; then
    echo "RESULT: TIMEOUT (${label})"
    exit 124
  elif [ "$rc" -ne 0 ]; then
    echo "RESULT: FAIL (${label}, rc=${rc})"
    exit 1
  fi
}

run_leg "A1-A7+O2/O3/O4" "enforce" "tests/postgres/test_mission_reconciliation_sweep_pg.py"
run_leg "H-MODE dry"     "dry"     "tests/postgres/test_mission_reconciliation_mode_pg.py"
run_leg "H-MODE enforce" "enforce" "tests/postgres/test_mission_reconciliation_mode_pg.py"

echo "RESULT: PASS"
exit 0
