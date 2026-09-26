#!/usr/bin/env bash
# test/packs/ab/snapshot_pg_smoke_integration_test.sh
#   Agent-snapshot-v1 Gate 3 — PG dialect smoke (filter_by_tags @> arm).
#   Provisions its own throwaway PG cluster on port 15432, exercises
#   SnapshotRepository.filter_by_tags on both PG and SQLite, and
#   asserts id-list agreement. No shared state, no LIVE daemon,
#   no port 5432 touched.
#
#   House convention: dual-layer timeout — internal 240s subprocess
#   guard, `timeout 300` outer guard. Provisions + tears down its
#   own cluster. PG_BIN=/usr/lib/postgresql/16/bin (Debian/Ubuntu).
#
#   The python exercise below contains the FULL evidence; the
#   bash harness only handles cluster lifecycle + reporting.

set -u
set -o pipefail

# ── Layer 1 (outer): command-level timeout — 5 min hard cap ──
# Self-invoke under `timeout 300` if not already wrapped.
SELF_NAME="${BASH_SOURCE[0]##*/}"
if [[ -z "${__PG_SMOKE_WRAPPED:-}" ]]; then
    exec env __PG_SMOKE_WRAPPED=1 timeout 300 bash "$0" "$@"
fi

# ── Constants / pinned config ──
PG_BIN="${PG_BIN:-/usr/lib/postgresql/16/bin}"
PG_PORT="${PG_SMOKE_PORT:-15432}"
PG_CLUSTER="/tmp/snapab/pg-smoke"
PG_LOG="/tmp/snapab/pg-smoke.log"
PG_SOCK="/tmp/snapab"
PG_USER="snap_smoke"
PG_DB="snap_smoke"

# Repo / venv resolution
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# test/packs/ab/<file> → repo root is 3 levels up
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
EXERCISE="$HERE/_snapshot_pg_smoke_exercise.py"

# ── Helpers ──
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; }

# ── Layer 2 (inner): script-internal timeout via subprocess ──
# Runs the python exercise under its own watchdog. The wrapper
# around `python ...` is the per-script timeout interrupt path.
INNER_TIMEOUT="${INNER_TIMEOUT:-240}"

# ── Teardown trap — runs unconditionally ──
cleanup() {
    local rc=$?
    log "teardown: stopping cluster (rc=$rc)"
    if [[ -x "$PG_BIN/pg_ctl" && -d "$PG_CLUSTER" ]]; then
        "$PG_BIN/pg_ctl" -D "$PG_CLUSTER" stop -m fast 2>&1 | sed 's/^/  /' || true
    fi
    log "teardown: removing data dir + log"
    rm -rf "$PG_CLUSTER" "$PG_LOG" 2>&1 || true
    log "teardown: port $PG_PORT liveness check"
    if command -v ss >/dev/null 2>&1; then
        if ss -ltn 2>/dev/null | grep -q ":$PG_PORT "; then
            fail "port $PG_PORT still listening — teardown incomplete"
            ss -ltn | grep ":$PG_PORT " || true
            return 1
        fi
    fi
    log "teardown: port $PG_PORT free ✓"
    return $rc
}
trap cleanup EXIT
trap 'log "interrupted"; exit 130' INT TERM

# ── Pre-flight — refuse if LIVE PG (5432) would collide on host ──
log "pre-flight: verify port $PG_PORT is free, not 5432"
if ss -ltn 2>/dev/null | grep -q ":$PG_PORT "; then
    fail "port $PG_PORT already in use — refusing to start"; exit 1
fi
if ss -ltn 2>/dev/null | grep -q ":5432 "; then
    log "note: port 5432 (LIVE PG) is active on host — OFF-LIMITS, our cluster uses $PG_PORT"
fi

# ── Ensure venv / repo ──
if [[ ! -x "$PYTHON_BIN" ]]; then
    fail "venv not found at $PYTHON_BIN"; exit 2
fi
if [[ ! -f "$EXERCISE" ]]; then
    fail "exercise not found at $EXERCISE"; exit 2
fi
log "repo: $REPO_ROOT"
log "venv: $($PYTHON_BIN --version)"

# ── Provision cluster ──
log "provision: initdb -A trust -U $PG_USER -D $PG_CLUSTER"
rm -rf "$PG_CLUSTER" "$PG_LOG" 2>/dev/null || true
mkdir -p "$PG_SOCK"
if ! "$PG_BIN/initdb" -A trust -U "$PG_USER" -D "$PG_CLUSTER" >/tmp/snapab-initdb.log 2>&1; then
    cat /tmp/snapab-initdb.log >&2
    fail "initdb failed"; exit 1
fi

log "provision: pg_ctl start on port $PG_PORT (socket dir $PG_SOCK)"
if ! "$PG_BIN/pg_ctl" -D "$PG_CLUSTER" \
        -o "-p $PG_PORT -k $PG_SOCK" \
        -l "$PG_LOG" \
        start >/tmp/snapab-start.log 2>&1; then
    cat /tmp/snapab-start.log >&2
    fail "pg_ctl start failed"; exit 1
fi

log "provision: createdb $PG_DB on port $PG_PORT"
if ! "$PG_BIN/createdb" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" "$PG_DB" >/tmp/snapab-createdb.log 2>&1; then
    cat /tmp/snapab-createdb.log >&2
    fail "createdb failed"; exit 1
fi

# ── Runtime env (deliberate throwaway DSN, never ambient) ──
unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=$PG_PORT
export POSTGRES_DB=$PG_DB
export POSTGRES_USER=$PG_USER
# (trust auth, no password)

log "DSN: postgresql+psycopg://$PG_USER@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB"

# ── Layer 2: inner subprocess timeout — interrupt hung python ──
log "exercise: launching python (inner timeout ${INNER_TIMEOUT}s)"
START_TS=$(date +%s)
EXERCISE_LOG=/tmp/snapab-exercise.log

# `timeout` is the actual interrupter — SIGTERM after INNER_TIMEOUT
set +e
timeout "$INNER_TIMEOUT" "$PYTHON_BIN" "$EXERCISE" >"$EXERCISE_LOG" 2>&1
EX_RC=$?
set -e

END_TS=$(date +%s)
ELAPSED=$((END_TS - START_TS))

# Pass-through output for the report
cat "$EXERCISE_LOG"
echo "--- end exercise output ---"

# ── Map exit codes ──
# python exit 0 → PASS  ;  python exit 124 → TIMEOUT  ;  python exit 1 → FAIL  ;  other → FAIL
case "$EX_RC" in
    0)  VERDICT="PASS" ;;
    124) VERDICT="TIMEOUT" ;;
    *)  VERDICT="FAIL" ;;
esac

echo "=== Test Pack: snapshot_pg_smoke_integration_test ==="
printf 'RESULT: %s (python_exit=%d, elapsed=%ds, inner_timeout=%ds)\n' "$VERDICT" "$EX_RC" "$ELAPSED" "$INNER_TIMEOUT"
echo "=== pack_end ==="

# Exit code mirrors the python exit code (124 for TIMEOUT)
exit "$EX_RC"
