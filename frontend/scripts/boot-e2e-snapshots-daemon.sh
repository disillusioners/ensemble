#!/usr/bin/env bash
# Disposable-PG dev daemon for the Snapshots Page e2e suite.
#
# Mirrors frontend/scripts/boot-e2e-maintenance-daemon.sh (the
# in-repo precedent for second-daemon disposable-PG bootstrap).
# Only the ports + data dir + disposable DB name + log dir +
# DATA_DIR_E2E + canary endpoint differ; all other mechanics
# (initdb -A trust, pg_ctl -o "-p $PG_PORT -k /tmp" start, 127.0.0.1
# host NOT /tmp, POSTGRES_* env scrub + rebuild, OPENAI_API_KEY
# early-exit guard, dual TERM/INT+EXIT trap wired into the same
# cleanup() function, 60-iter canary wait, ENSEMBLE_E2E_KEEP=1 skip
# for the rm) are copied verbatim from the maintenance script.
#
# Usage:
#   ENSEMBLE_E2E_KEEP=1 ./scripts/boot-e2e-snapshots-daemon.sh start
#   ./scripts/boot-e2e-snapshots-daemon.sh stop
#
# Env vars (all optional):
#   PG_PORT       — local PG port (default 15532 — distinct from
#                   maintenance's 15432 AND dev :5432)
#   DAEMON_PORT   — daemon port (default 18279 — distinct from
#                   maintenance's 8099 AND dev :8079)
#   DATA_DIR      — PG cluster data dir (default /tmp/pg_e2e_snap_$$)
#   DISPOSABLE_DB — disposable DB name (default ensemble_e2e_snap_$$)
#   OPENAI_API_KEY — must be set; the daemon's lifespan requires it
#
# This script is paired with `playwright.snapshots.config.ts`. The
# Playwright webServer invokes `start`, waits for the canary response,
# and runs the spec. On test teardown, Playwright kills the daemon
# (the `wait`-blocked child), the script's traps fire cleanup(), and
# the globalTeardown backstop (`e2e/global-teardown-snapshots.ts`)
# covers the SIGKILL race. Three layers; all idempotent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# PG server binaries (initdb, pg_ctl) often live outside the default
# non-interactive PATH on Linux PG installs (typically
# /usr/lib/postgresql/<ver>/bin). Prepend every installed version's
# bin so the script works regardless of the operator's shell PATH.
# The glob is safe under `set -u` — an unmatched pattern fails the
# -d test and skips silently; multiple installed versions stack with
# the highest version taking precedence.
for _pgbin in /usr/lib/postgresql/*/bin; do
  [ -d "$_pgbin" ] && export PATH="$_pgbin:$PATH"
done

PG_PORT="${PG_PORT:-15532}"
DAEMON_PORT="${DAEMON_PORT:-18279}"
DATA_DIR="${DATA_DIR:-/tmp/pg_e2e_snap_$$}"
DISPOSABLE_DB="${DISPOSABLE_DB:-ensemble_e2e_snap_$$}"
LOG_DIR="${LOG_DIR:-/tmp/e2e_snapshots_logs}"
DATA_DIR_E2E="$REPO_ROOT/data_e2e_snapshots"

mkdir -p "$LOG_DIR"

action="${1:-start}"

# ── Factored teardown (mirrors maintenance cleanup() verbatim; only ─────
# DATA_DIR + DATA_DIR_E2E + LOG_DIR differ — resolved above).
# Symmetric idempotent teardown — used by the TERM/INT trap, the EXIT
# trap, AND by `action_stop`. In-use port detection refuses to silently
# adopt a stale cluster (kill-or-error semantics). When a foreign PG is
# already listening on $PG_PORT, refuse to stop it (we did not start
# it) and stop the daemon only — this prevents clobbering an
# operator's local PG that happens to be on :15532.
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
    # Refuse to stop a foreign cluster: only stop when the data dir
    # we initialised exists (same guard as the maintenance mirror).
    if [ -d "$DATA_DIR" ]; then
      pg_ctl -D "$DATA_DIR" stop >> "$LOG_DIR/boot.log" 2>&1 || true
    else
      echo "[cleanup] WARN: $PG_PORT is up but $DATA_DIR is absent — refusing to stop a foreign cluster" | tee -a "$LOG_DIR/boot.log"
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
  #    MUST clear them before invoking uvicorn (mirrors maintenance
  #    verbatim, including the ENSEMBLE_DB_DSN scrub — the DSN is
  #    maintenance-spec-specific but scrubbing it here is free and
  #    keeps the daemon on the POSTGRES_* path only).
  unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL
  unset ENSEMBLE_DB_DSN

  # Refuse to silently adopt a stale cluster. If the port is up and
  # the data dir is empty/missing, the cluster is foreign; bail with a
  # clear error rather than racing against it (mirrors maintenance
  # :102-107 verbatim).
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
  # try the default :5432 Unix socket and mis-parse :15532/DB as the
  # db name. (Mirrors maintenance:131-135 verbatim.)
  export POSTGRES_HOST="127.0.0.1"
  export POSTGRES_PORT="$PG_PORT"
  export POSTGRES_DB="$DISPOSABLE_DB"
  export POSTGRES_USER="$(whoami)"
  unset POSTGRES_PASSWORD

  # 3. Force the daemon's lifespan port to 18279 (the dedicated e2e
  #    port — mirrors maintenance:138-142 verbatim; the canonical
  #    port-set. The old `dev.sh:128 export PORT=8079` is the dead
  #    knob this script replaces for the e2e lane).
  export DAEMON_PORT="$DAEMON_PORT"
  export PORT="$DAEMON_PORT"
  export DATA_DIR_E2E="$REPO_ROOT/data_e2e_snapshots"
  export ENSEMBLE_DATA_DIR="$DATA_DIR_E2E"
  mkdir -p "$DATA_DIR_E2E"

  # 4. Daemon requires an OPENAI_API_KEY to boot its lifespan. The e2e
  #    spec does NOT exercise LLM calls, so any well-formed key works
  #    (the daemon's LLM client is initialized but never invoked).
  if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "[boot] ERROR: OPENAI_API_KEY must be set for the daemon lifespan." >&2
    exit 1
  fi

  # NOTE (deliberate divergence from the maintenance mirror): there is
  # NO snapshots equivalent of `MAINTENANCE_ENDPOINTS_ENABLED` — the
  # snapshots router is unconditional on this branch (the R15 toggle
  # is UI-only and routes /api/settings/snapshot-create, not
  # /api/snapshots). Nothing to export here.

  echo "[boot] Starting daemon on port $DAEMON_PORT..." | tee -a "$LOG_DIR/boot.log"
  cd "$REPO_ROOT"
  # TERM/INT trap wires into the factored cleanup() (mirrors
  # maintenance:160).
  trap 'cleanup' TERM INT
  # EXIT trap backstop (mirrors maintenance:168). Playwright's
  # webServer teardown can race SIGKILL against SIGTERM (esp. when
  # `pg_ctl stop` takes >5s on a saturated cluster). The EXIT trap
  # fires on ANY script exit — the boot script's own success path
  # calls cleanup() explicitly after `wait`, AND the globalTeardown
  # in playwright.snapshots.config.ts is a deterministic backstop
  # for the SIGKILL race. Three layers; all idempotent.
  trap 'cleanup' EXIT
  uv run python -m uvicorn daemon.api:app \
    --host 127.0.0.1 --port "$DAEMON_PORT" \
    --log-level info --timeout-graceful-shutdown 10 \
    >> "$LOG_DIR/daemon.log" 2>&1 &
  DAEMON_PID=$!
  echo "$DAEMON_PID" > "$LOG_DIR/daemon.pid"
  echo "[boot] daemon pid=$DAEMON_PID, log=$LOG_DIR/daemon.log" | tee -a "$LOG_DIR/boot.log"

  # 5. Wait for /readyz to respond — pass 6 amendment #1. Mirrors the
  #    maintenance 60-iter canary in SHAPE (60 × 1s curl poll), NOT in
  #    endpoint: we poll the REAL GET /readyz route and assert the
  #    JSON body `status == "ready"` (ReadyzResponse: `status` is
  #    'ready' when all components pass, 'degraded' otherwise → 503).
  #    The handler is an O(1) cached-composite read (ADR-003 — zero
  #    DB access per request), so polling it at 1 Hz is safe. The
  #    maintenance mirror polls its own maintenance-specific
  #    /api/maintenance/checkpoint-cleanup/availability endpoint
  #    (shape `state`) — that is deliberate on its lane and stays.
  echo "[boot] Waiting for daemon canary (/readyz → status:'ready')..." | tee -a "$LOG_DIR/boot.log"
  for i in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$DAEMON_PORT/readyz" > "$LOG_DIR/canary.json" 2>/dev/null; then
      state=$(python3 -c "import json,sys; d=json.load(open('$LOG_DIR/canary.json')); print(d.get('status','?'))" 2>/dev/null || echo "?")
      echo "[boot] Canary response: status='$state'" | tee -a "$LOG_DIR/boot.log"
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
  echo "[boot] ERROR: daemon did not reach status=ready within 60s" >&2
  tail -20 "$LOG_DIR/daemon.log" >&2 || true
  cleanup
  exit 1
}

# ── stop ───────────────────────────────────────────────────────────────────
# `action_stop` is a thin wrapper around cleanup(). Manual `stop` and the
# TERM/INT trap run the SAME teardown, so behaviour is symmetric with the
# maintenance mirror.
action_stop() {
  cleanup
}

case "$action" in
  start) action_start ;;
  stop)  action_stop  ;;
  *)     echo "Usage: $0 {start|stop}" >&2; exit 2 ;;
esac
