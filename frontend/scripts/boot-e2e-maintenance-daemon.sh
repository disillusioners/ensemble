#!/usr/bin/env bash
# Disposable-PG dev daemon for the Maintenance Console e2e suite.
#
# Bootstraps a PostgreSQL cluster on port 15432 (a port other daemons do
# NOT use), creates a per-run disposable database, and starts the
# Ensemble daemon on port 8099 against it. NEVER touches ensemble_prod.
#
# Usage:
#   ENSEMBLE_E2E_KEEP=1 ./scripts/boot-e2e-maintenance-daemon.sh start
#   ./scripts/boot-e2e-maintenance-daemon.sh stop
#
# Env vars (all optional):
#   PG_PORT       — local PG port (default 15432 — avoids dev :5432 + 8079)
#   DAEMON_PORT   — daemon port (default 8099 — distinct from dev :8079)
#   DATA_DIR      — PG cluster data dir (default /tmp/pg_e2e_maint_$$)
#   DISPOSABLE_DB — disposable DB name (default ensemble_e2e_maint_$$)
#   OPENAI_API_KEY — must be set; the daemon's lifespan requires it
#
# This script is paired with `playwright.maintenance.config.ts`. The
# Playwright webServer invokes `start`, waits for the canary response,
# and runs the spec. On test teardown, Playwright invokes `stop`, which
# drops the disposable DB and stops the daemon + PG cluster.
#
# Item 3 (v4 fix pass) — factored cleanup() wired into BOTH the
# TERM/INT trap AND `action_stop`. The trap previously ONLY killed
# the daemon — orphan PG clusters + leaked `data_e2e_maintenance/`
# dirs were the operational cost. The cleanup() now kills daemon,
# stops PG (conditional on pg_isready), and conditional-rmrfs both
# dirs. `action_stop` calls the same cleanup() so manual invocation
# is symmetric with the trap.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PG_PORT="${PG_PORT:-15432}"
DAEMON_PORT="${DAEMON_PORT:-8099}"
DATA_DIR="${DATA_DIR:-/tmp/pg_e2e_maint_$$}"
DISPOSABLE_DB="${DISPOSABLE_DB:-ensemble_e2e_maint_$$}"
LOG_DIR="${LOG_DIR:-/tmp/e2e_maintenance_logs}"
DATA_DIR_E2E="$REPO_ROOT/data_e2e_maintenance"

mkdir -p "$LOG_DIR"

action="${1:-start}"

# ── Item 3: factored teardown ────────────────────────────────────────────
# Symmetric idempotent teardown — used by the TERM/INT trap AND by
# `action_stop`. In-use port detection refuses to silently adopt a
# stale cluster (kill-or-error semantics). When a foreign PG is
# already listening on $PG_PORT, refuse to stop it (we did not start
# it) and stop the daemon only — this prevents clobbering an
# operator's local PG that happens to be on :15432.
cleanup() {
  echo "[cleanup] teardown start (DAEMON pid file=$LOG_DIR/daemon.pid, PG_PORT=$PG_PORT, DATA_DIR=$DATA_DIR)" | tee -a "$LOG_DIR/boot.log"
  # 1. Kill daemon.
  if [ -f "$LOG_DIR/daemon.pid" ]; then
    local pid
    pid="$(cat "$LOG_DIR/daemon.pid")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
    fi
    rm -f "$LOG_DIR/daemon.pid"
  fi
  # 2. Stop PG — guard against clobbering a foreign cluster.
  if pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then
    if pg_isready -h 127.0.0.1 -p "$PG_PORT" -t 1 >/dev/null 2>&1; then
      # Refuse to stop a foreign cluster by checking the data dir
      # matches what we initialised (PG reports `Data directory`
      # on `pg_ctl status`; cheaper: only stop if $DATA_DIR exists
      # AND our pid is recorded in $LOG_DIR/daemon.pid (daemon was
      # ours). We additionally assert the cluster speaks the
      # expected DISPOSABLE_DB exists.
      if [ -d "$DATA_DIR" ]; then
        pg_ctl -D "$DATA_DIR" stop >> "$LOG_DIR/boot.log" 2>&1 || true
      else
        echo "[cleanup] WARN: $PG_PORT is up but $DATA_DIR is absent — refusing to stop a foreign cluster" | tee -a "$LOG_DIR/boot.log"
      fi
    fi
  fi
  # 3. Remove cluster + e2e data dir (unless ENSEMBLE_E2E_KEEP=1).
  if [ -z "${ENSEMBLE_E2E_KEEP:-}" ]; then
    rm -rf "$DATA_DIR" "$DATA_DIR_E2E" 2>/dev/null || true
  else
    echo "[cleanup] ENSEMBLE_E2E_KEEP=1 — keeping $DATA_DIR and $DATA_DIR_E2E" | tee -a "$LOG_DIR/boot.log"
  fi
  echo "[cleanup] teardown complete" | tee -a "$LOG_DIR/boot.log"
}

# ── start ──────────────────────────────────────────────────────────────────
action_start() {
  # 1. Scrub inherited POSTGRES_* env so an operator's prod settings
  #    cannot bleed through. The daemon reads POSTGRES_* at boot, so we
  #    MUST clear them before invoking uvicorn.
  unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL
  unset ENSEMBLE_DB_DSN

  # Item 3 — refuse to silently adopt a stale cluster. If the port
  # is up and the data dir is empty/missing, the cluster is foreign;
  # bail with a clear error rather than racing against it.
  if pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then
    if [ ! -d "$DATA_DIR" ]; then
      echo "[boot] ERROR: port $PG_PORT is in use by a foreign PG cluster (no $DATA_DIR)." >&2
      echo "[boot] Refusing to silently adopt a stale cluster. Set PG_PORT to a free port." >&2
      exit 1
    fi
  fi

  echo "[boot] Initializing PostgreSQL cluster at $DATA_DIR on port $PG_PORT..." | tee -a "$LOG_DIR/boot.log"
  if [ ! -d "$DATA_DIR" ]; then
    initdb -A trust -D "$DATA_DIR" --no-locale -E UTF8 >> "$LOG_DIR/boot.log" 2>&1
  fi
  # Start PG cluster (skip if already running).
  if ! pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then
    pg_ctl -D "$DATA_DIR" -l "$LOG_DIR/pg.log" -o "-p $PG_PORT -k /tmp" start >> "$LOG_DIR/boot.log" 2>&1
    sleep 1
  fi

  echo "[boot] Creating disposable database: $DISPOSABLE_DB" | tee -a "$LOG_DIR/boot.log"
  # Use `createdb` via psql -h 127.0.0.1 -p PG_PORT — no password needed (trust auth).
  if ! psql -h 127.0.0.1 -p "$PG_PORT" -d postgres -c "SELECT 1 FROM pg_database WHERE datname='$DISPOSABLE_DB'" -tA 2>/dev/null | grep -q 1; then
    createdb -h 127.0.0.1 -p "$PG_PORT" "$DISPOSABLE_DB" >> "$LOG_DIR/boot.log" 2>&1
  fi

  # 2. Build the POSTGRES_* env block the daemon reads at boot.
  # NOTE: use `127.0.0.1` (not `/tmp`) — Unix-socket path is host-only
  # and PG_PORT must apply via the network. `/tmp` causes psycopg to
  # try the default :5432 Unix socket and mis-parse :15432/DB as the
  # db name.
  export POSTGRES_HOST="127.0.0.1"
  export POSTGRES_PORT="$PG_PORT"
  export POSTGRES_DB="$DISPOSABLE_DB"
  export POSTGRES_USER="$(whoami)"
  unset POSTGRES_PASSWORD

  # 3. Force the daemon's lifespan port to 8099 (the dedicated e2e port).
  export DAEMON_PORT="$DAEMON_PORT"
  export PORT="$DAEMON_PORT"
  export DATA_DIR_E2E="$REPO_ROOT/data_e2e_maintenance"
  export ENSEMBLE_DATA_DIR="$DATA_DIR_E2E"
  mkdir -p "$DATA_DIR_E2E"

  # 4. Daemon requires an OPENAI_API_KEY to boot its lifespan. The e2e
  #    spec does NOT exercise LLM calls, so any well-formed key works
  #    (the daemon's LLM client is initialized but never invoked).
  if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "[boot] ERROR: OPENAI_API_KEY must be set for the daemon lifespan." >&2
    exit 1
  fi

  # 5. Enable the maintenance endpoints (default ON; explicit for clarity).
  export MAINTENANCE_ENDPOINTS_ENABLED="${MAINTENANCE_ENDPOINTS_ENABLED:-1}"

  echo "[boot] Starting daemon on port $DAEMON_PORT..." | tee -a "$LOG_DIR/boot.log"
  cd "$REPO_ROOT"
  # Item 3 — TERM/INT trap wires into the factored cleanup() (was:
  # killed daemon only — leaked PG clusters + leaked
  # `data_e2e_maintenance/` were the operational cost).
  trap 'cleanup' TERM INT
  # Item 3 v4 fix pass — EXIT trap backstop. Playwright's webServer
  # teardown can race SIGKILL against SIGTERM (esp. when
  # `pg_ctl stop` takes >5s on a saturated cluster). The EXIT trap
  # fires on ANY script exit — the boot script's own success path
  # calls cleanup() explicitly after `wait`, AND the globalTeardown
  # in playwright.maintenance.config.ts is a deterministic backstop
  # for the SIGKILL race. Three layers; all idempotent.
  trap 'cleanup' EXIT
  uv run python -m uvicorn daemon.api:app \
    --host 127.0.0.1 --port "$DAEMON_PORT" \
    --log-level info --timeout-graceful-shutdown 10 \
    >> "$LOG_DIR/daemon.log" 2>&1 &
  DAEMON_PID=$!
  echo "$DAEMON_PID" > "$LOG_DIR/daemon.pid"
  echo "[boot] daemon pid=$DAEMON_PID, log=$LOG_DIR/daemon.log" | tee -a "$LOG_DIR/boot.log"

  # 6. Wait for /availability to respond.
  echo "[boot] Waiting for daemon canary (/availability → state:'ready')..." | tee -a "$LOG_DIR/boot.log"
  for i in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$DAEMON_PORT/api/maintenance/checkpoint-cleanup/availability" > "$LOG_DIR/canary.json" 2>/dev/null; then
      state=$(python3 -c "import json,sys; d=json.load(open('$LOG_DIR/canary.json')); print(d.get('state','?'))" 2>/dev/null || echo "?")
      echo "[boot] Canary response: state='$state'" | tee -a "$LOG_DIR/boot.log"
      if [ "$state" = "ready" ]; then
        echo "[boot] OK — daemon is READY on :$DAEMON_PORT (DB $DISPOSABLE_DB) — blocking on daemon pid=$DAEMON_PID" | tee -a "$LOG_DIR/boot.log"
        # BLOCK on the daemon process so Playwright's webServer stays
        # up for the duration of the e2e run. Playwright's webServer
        # teardown kills the daemon (the child), `wait` returns, and
        # the script reaches the normal-exit path below. The TERM/INT
        # trap also fires `cleanup()` if Playwright SIGTERMs the shell
        # instead of the daemon (covers both webServer styles).
        wait "$DAEMON_PID"
        # Normal-exit path — Playwright killed the daemon (or it died
        # on its own). Run cleanup() here so PG + dirs don't leak. The
        # TERM/INT trap may have already fired; cleanup() is
        # idempotent (each step guards against missing state).
        cleanup
        exit 0
      fi
    fi
    sleep 1
  done
  echo "[boot] ERROR: daemon did not reach state=ready within 60s" >&2
  tail -20 "$LOG_DIR/daemon.log" >&2 || true
  cleanup
  exit 1
}

# ── stop ───────────────────────────────────────────────────────────────────
# Item 3 — `action_stop` is now a thin wrapper around `cleanup()`. Manual
# `stop` and the TERM/INT trap run the SAME teardown, so behaviour is
# symmetric and the lying teardown comment ("stops the daemon + PG
# cluster") is no longer a lie.
action_stop() {
  cleanup
}

case "$action" in
  start) action_start ;;
  stop)  action_stop  ;;
  *)     echo "Usage: $0 {start|stop}" >&2; exit 2 ;;
esac
