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
# Per-leg pytest rc: 0 ok / 1 test failure / 5 nothing collected
#   (collection error, or the -m postgres filter matched nothing) /
#   124 leg timeout (outer `timeout 280s`).
# Pack exit: 0 PASS / 1 FAIL / 124 TIMEOUT. FAIL covers ANY leg rc!=0
#   (incl. 5) AND a leg summary missing "N passed" with N>=1 (the
#   explicit collected-count check). The RESULT labels always print —
#   a red leg never kills the pack silently at the pytest line.
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
  if [ -n "${FAKE_HOME:-}" ]; then
    rm -rf "$FAKE_HOME"
  fi
  echo "[cleanup] scratch dropped"
}
trap cleanup EXIT

FAKE_HOME="$(mktemp -d)"

run_leg() {
  local label="$1" mode_env="$2" target="$3"
  local log
  log="$(mktemp "$FAKE_HOME/leg.XXXXXX")"
  echo "--- leg: ${label} (ENSEMBLE_MISSION_RECORDS=${mode_env}) ---"
  # Run the leg NON-FATALLY and capture its rc: under set -euo pipefail a
  # failing leg would kill the pack at the pytest line itself, leaving the
  # RESULT labels below unreachable. A red leg still fails the pack at the
  # checks below (fail-closed preserved).
  set +e
  timeout 280s env HOME="$FAKE_HOME" ENSEMBLE_SELF_ENV=dev \
    POSTGRES_HOST="$PG_HOST" POSTGRES_PORT="$PG_PORT" \
    POSTGRES_DB="$SCRATCH_DB" POSTGRES_USER="$SCRATCH_ROLE" POSTGRES_PASSWORD="$SCRATCH_PW" \
    PG_TEST_HOST="$PG_HOST" PG_TEST_PORT="$PG_PORT" \
    PG_TEST_DB="$SCRATCH_DB" PG_TEST_USER="$SCRATCH_ROLE" PG_TEST_PASSWORD="$SCRATCH_PW" \
    PG_TEST_ALLOWED_DBS="$SCRATCH_DB" \
    ENSEMBLE_MISSION_RECORDS="$mode_env" \
    "${PYTHON_BIN:-/home/nea/ensemble-src/.venv/bin/python}" -m pytest "$target" \
    --override-ini="addopts=" -m postgres -q --tb=short -p no:cacheprovider \
    --timeout=300 >"$log" 2>&1
  local rc="$?"
  set -e
  tail -5 "$log"
  if [ "$rc" -eq 124 ]; then
    echo "RESULT: TIMEOUT (${label})"
    exit 124
  fi
  # Explicit ran-count assertion: the leg summary must show "N passed" with
  # N>=1. "no tests ran" (pytest rc=5 no-collect) and collection errors land
  # here with a loud label instead of relying on pytest's rc semantics alone.
  local summary passed_n=""
  summary="$(grep -Eo '[0-9]+ passed' "$log" | tail -1 || true)"
  case "$summary" in [0-9]*) passed_n="${summary%% *}";; esac
  if [ -z "$passed_n" ] || [ "$passed_n" -lt 1 ]; then
    echo "RESULT: FAIL (${label}, rc=${rc}, collected-count check: no \"N passed\" N>=1 in summary — collection/selection failure)"
    exit 1
  fi
  if [ "$rc" -ne 0 ]; then
    echo "RESULT: FAIL (${label}, rc=${rc}, ${passed_n} passed before failure)"
    exit 1
  fi
  echo "[leg ok] ${label}: ${passed_n} passed (rc=${rc})"
}

run_leg "A1-A7+O2/O3/O4" "enforce" "tests/postgres/test_mission_reconciliation_sweep_pg.py"
run_leg "H-MODE dry"     "dry"     "tests/postgres/test_mission_reconciliation_mode_pg.py"
run_leg "H-MODE enforce" "enforce" "tests/postgres/test_mission_reconciliation_mode_pg.py"

echo "RESULT: PASS"
exit 0
