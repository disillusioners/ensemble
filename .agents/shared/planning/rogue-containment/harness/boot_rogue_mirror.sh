#!/usr/bin/env bash
# Fenced mirror harness for rogue-containment RED legs (E-ii / E-iii / DB-refusal).
# Modeled on boot_v2.sh + tests/support/wedge_smoke.py.
# NEVER touches live 9797/9798/ensemble_prod. Scratch only.
set -euo pipefail
umask 077

# --- FAKE_HOME escape guard (tests/upgrade_test_lib.sh:69-102) ---
FAKE_HOME="${TMPDIR:-/tmp}/rogue-mirror-home-$$"
mkdir -p "$FAKE_HOME"
export HOME="$FAKE_HOME"
case "$HOME" in /tmp/*) ;; *) echo "FATAL: HOME not under /tmp"; exit 99 ;; esac
case "$HOME" in /home/nea*) echo "FATAL: HOME under /home/nea"; exit 99 ;; esac

# --- per-variable scrub (NEVER `env -u POSTGRES_*` glob) ---
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
unset DATABASE_URL PGHOST PGPORT PGUSER PGPASSWORD
# pin sweep guard explicitly (never env -u on it)
export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0

# rogue-containment iter-1 D5: refuse to run without a scratch postgres
# (silent SQLite fallback is the bug this guard prevents).
if [ -z "${PG_TEST_HOST:-}" ]; then
  echo "FATAL: PG_TEST_HOST is required (scratch postgres). Refusing to fall back to SQLite." >&2
  exit 99
fi

# --- scratch port (re-roll off 9797/9798/7979/8079) ---
PORT="${ROGUE_MIRROR_PORT:-18790}"
export PORT HOST=0.0.0.0
export DAEMON_PORT="$PORT"

# --- scratch PG (PG_TEST_* -> POSTGRES_*) ---
export PG_TEST_DB_NAME="${PG_TEST_DB_NAME:-ens_e2e_scrub_probe}"
# Caller must provide PG_TEST_* vars; we translate:
if [ -n "${PG_TEST_HOST:-}" ]; then export POSTGRES_HOST="$PG_TEST_HOST"; fi
if [ -n "${PG_TEST_PORT:-}" ]; then export POSTGRES_PORT="$PG_TEST_PORT"; fi
if [ -n "${PG_TEST_USER:-}" ]; then export POSTGRES_USER="$PG_TEST_USER"; fi
if [ -n "${PG_TEST_PASSWORD:-}" ]; then export POSTGRES_PASSWORD="$PG_TEST_PASSWORD"; fi
export POSTGRES_DB="$PG_TEST_DB_NAME"

# --- scratch config (literal port, never ${PORT:-}) ---
SCRATCH_DIR="${TMPDIR:-/tmp}/rogue-mirror-$$"
mkdir -p "$SCRATCH_DIR/data"
export ENSEMBLE_DATA_DIR="$SCRATCH_DIR/data"
export ENSEMBLE_SELF_ENV=sandbox

# --- run the daemon; capture redacted output ---
echo "[mirror] PORT=$PORT DATA_DIR=$ENSEMBLE_DATA_DIR HOME=$HOME SWEEP=$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"
echo "[mirror] scratch DB=$POSTGRES_DB (must not be a forbidden name)"
echo "[mirror] backend=postgres://${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB} (password redacted)"
case "$POSTGRES_DB" in ensemble_prod|ensemble_dev|ensemble_prod_test) echo "FATAL: forbidden DB name"; exit 99 ;; esac

# Caller supplies the daemon invocation via ROGUE_MIRROR_CMD (default: python -m daemon)
ROGUE_MIRROR_CMD="${ROGUE_MIRROR_CMD:-/home/nea/ensemble-src/.venv/bin/python -m daemon}"
echo "[mirror] CMD=$ROGUE_MIRROR_CMD"

# Leg selector
LEG="${ROGUE_MIRROR_LEG:-happy}"
case "$LEG" in
  refuse-foreign)
    # Plant a foreign claim before boot.
    mkdir -p "$ENSEMBLE_DATA_DIR/port-claims"
    /home/nea/ensemble-src/.venv/bin/python - 2>/dev/null || /home/nea/ensemble-src/.venv/bin/python - "$ENSEMBLE_DATA_DIR" "$PORT" <<'PY'
import json,sys,os
dd, port = sys.argv[1], int(sys.argv[2])
p=os.path.join(dd,"port-claims",f"0.0.0.0-{port}.json")
os.makedirs(os.path.dirname(p),exist_ok=True)
st=__import__("daemon.services.port_ownership",fromlist=["_read_starttime"])._read_starttime(os.getpid())
json.dump({"install_dir":"foreign-install-root","pid":os.getpid(),"starttime":st,"port":port,"host":"0.0.0.0","claimed_at":"now"},open(p,"w"))
PY
    export ENSEMBLE_DB_AMBIENT_ALLOW=1
    ;;
  db-poison)
    # Ensure no ensemble.json (tier 2) and ambient POSTGRES_* present (already).
    rm -f "$ENSEMBLE_DATA_DIR/ensemble.json"
    unset ENSEMBLE_DB_AMBIENT_ALLOW
    ;;
  happy)
    rm -f "$ENSEMBLE_DATA_DIR/ensemble.json"
    export ENSEMBLE_DB_AMBIENT_ALLOW=1
    ;;
esac

# Run with timeout; capture exit code
set +e
eval "$ROGUE_MIRROR_CMD" > "$SCRATCH_DIR/run.log" 2>&1 &
DPID=$!
# wait briefly for boot
sleep 8
# probe readyz
curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/api/health/readyz" 2>/dev/null || echo "no-readyz"
# teardown: SIGTERM only
kill -TERM "$DPID" 2>/dev/null
wait "$DPID" 2>/dev/null
RC=$?
echo "[mirror] exit_code=$RC"
echo "[mirror] --- tail of run.log ---"
tail -30 "$SCRATCH_DIR/run.log"
# redacted capture: sha256 of log only
echo "[mirror] log_sha256=$(sha256sum "$SCRATCH_DIR/run.log" | awk '{print $1}')"

# cleanup
rm -rf "$SCRATCH_DIR" "$FAKE_HOME"
exit 0
