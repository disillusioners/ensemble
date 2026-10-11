#!/usr/bin/env bash
# ============================================================================
# Pack wrapper: storedwatches_pg_integration_test
# ============================================================================
# PG-lane verification pack for the stored_watches quarantine table
# (fix e8a61da3 — usage-limit retry-supersede double-silence, Option A).
#
# DUAL-LAYER TIMEOUT: this wrapper runs pytest under a self-timer of
# 280s; the CALLER wraps this script in `timeout 300`. Exit codes:
#   0   -> RESULT: PASS
#   1   -> RESULT: FAIL
#   124 -> RESULT: TIMEOUT (self-timer hit; caller's 300 never reached)
#
# ENV: mandatory scrub of live-DB coordinates (POSTGRES_*/DATABASE_URL/
# ENSEMBLE_DB_DSN/SSL_CERT_*) + explicit PG_TEST_* export. Cluster
# lifecycle (initdb/pg_ctl/createdb) is the CALLER'S job (STEP 1 of the
# commission) — this wrapper probes reachability and refuses fail-closed
# otherwise. Foreign-cluster refusal both directions: host must be
# 127.0.0.1, port must be within 15000-19999 (5432/5433 hard-refused).
# Serial execution only (no pytest-xdist).
# ============================================================================
set -u
PACK_START=$(date +%s)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PG_BIN="/usr/lib/postgresql/16/bin"
PYBIN="$REPO_ROOT/.venv/bin/python"
TEST_PATH="tests/vfy_pg/test_stored_watches_pg_lane.py"
SELF_TIMER=280

# Disposable-cluster coordinates (overridable via PG_TEST_* from caller)
V_HOST="${PG_TEST_HOST:-127.0.0.1}"
V_PORT="${PG_TEST_PORT:-15533}"
V_DB="${PG_TEST_DB:-ensemble_vfy_sw}"
V_USER="${PG_TEST_USER:-$(id -un)}"

finish() { # $1=rc $2=label
  local dur=$(( $(date +%s) - PACK_START ))
  echo "pack_runtime=${dur}s"
  echo "RESULT: $2"
  exit "$1"
}

echo "=== Test Pack: storedwatches_pg_integration_test ==="
echo "repo_root=$REPO_ROOT"
echo "pg_target=$V_HOST:$V_PORT/$V_DB user=$V_USER"
echo "test_path=$TEST_PATH self_timer=${SELF_TIMER}s"

# --- foreign-cluster refusal (fail closed) --------------------------------
if [ "$V_HOST" != "127.0.0.1" ]; then
  echo "REFUSED: PG_TEST_HOST must be 127.0.0.1 (disposable-cluster contract), got '$V_HOST'"
  finish 1 FAIL
fi
case "$V_PORT" in
  5432|5433) echo "REFUSED: protected port $V_PORT is not disposable"; finish 1 FAIL ;;
esac
if [ "$V_PORT" -lt 15000 ] || [ "$V_PORT" -gt 19999 ] 2>/dev/null; then
  echo "REFUSED: PG_TEST_PORT must be within 15000-19999, got '$V_PORT'"
  finish 1 FAIL
fi

# --- cluster reachability probe (never starts/stops anything) -------------
if ! "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$V_PORT" -t 5 -q; then
  echo "PG cluster not reachable on 127.0.0.1:$V_PORT — start the disposable cluster first (STEP 1)."
  finish 1 FAIL
fi
"$PG_BIN/psql" -h 127.0.0.1 -p "$V_PORT" -U "$V_USER" -d "$V_DB" -tAc "SELECT version();" \
  || { echo "psql version probe failed against $V_DB"; finish 1 FAIL; }

cd "$REPO_ROOT" || finish 1 FAIL
[ -x "$PYBIN" ] || { echo "venv python missing: $PYBIN"; finish 1 FAIL; }

# --- env scrub + export + self-timer pytest run (serial) -------------------
env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN \
    -u SSL_CERT_FILE -u SSL_CERT_DIR -u PYTEST_XDIST_WORKER \
    PG_TEST_HOST="$V_HOST" PG_TEST_PORT="$V_PORT" PG_TEST_DB="$V_DB" \
    PG_TEST_USER="$V_USER" \
  timeout --kill-after=10 "$SELF_TIMER" "$PYBIN" -m pytest "$TEST_PATH" \
    -v --tb=long \
    --override-ini="addopts=" \
    --override-ini="timeout=240" \
    -m postgres \
    -p no:cacheprovider
rc=$?

case "$rc" in
  0)        finish 0 PASS ;;
  124|137)  echo "pytest self-timer (${SELF_TIMER}s) hit"; finish 124 TIMEOUT ;;
  *)        finish 1 FAIL ;;
esac
