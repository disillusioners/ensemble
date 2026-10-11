#!/usr/bin/env bash
# Disposable-PG dev daemon for the OAuth2 auth e2e suite.
#
# Mirrors frontend/scripts/boot-e2e-snapshots-daemon.sh and
# frontend/scripts/boot-e2e-maintenance-daemon.sh. Only the ports +
# data dir + disposable DB name + log dir + canary + auth env-var
# block differ.
#
# The auth-ON posture is exercised through the §6.3 enablement env set
# (ENSEMBLE_AUTH_{ENABLED,CLIENT_ID,CLIENT_SECRET,ISSUER,
# ALLOWED_ORIGINS,EXTERNAL_REDIRECT_BASE}, cookie dev flags per the LAN
# opt-in). The fake OIDC provider (T5.1) is mounted on
# http://127.0.0.1:$IDP_PORT by a tiny uvicorn side process — the
# --fake-idp mode keeps the whole boot Docker-less (NFR-10).
#
# Usage:
#   ENSEMBLE_E2E_KEEP=1 ./scripts/boot-e2e-auth-daemon.sh start
#   ./scripts/boot-e2e-auth-daemon.sh stop
#   ENSEMBLE_E2E_KEEP=1 ./scripts/boot-e2e-auth-daemon.sh start --fake-idp
#
# Env vars (all optional):
#   PG_PORT        — local PG port (default 15533 — distinct from the
#                    snapshots :15532 AND maintenance :15432 AND dev :5432)
#   DAEMON_PORT    — daemon port (default 18280 — distinct from the
#                    snapshots :18279 AND maintenance :8099 AND dev :8079)
#   IDP_PORT       — fake OIDC provider port (default 18281)
#   DATA_DIR       — PG cluster data dir (default /tmp/pg_e2e_auth_$$)
#   DISPOSABLE_DB  — disposable DB name (default ensemble_e2e_auth_$$)
#   OPENAI_API_KEY — must be set; the daemon's lifespan requires it
#   USE_FAKE_IDP   — set to "1" (or pass --fake-idp) to spawn the
#                    FakeOidcProvider as a side process
#
# This script is paired with the Phase 5 T5.5 e2e suite
# (tests/e2e/test_auth_lifecycle.py). The canary now asserts the 5
# public probes are reachable POST-restart, BEFORE any session
# revalidation (AMEND v1.2 / A7).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# PG server binaries (initdb, pg_ctl) often live outside the default
# non-interactive PATH on Linux PG installs (typically
# /usr/lib/postgresql/<ver>/bin). Prepend every installed version's
# bin so the script works regardless of the operator's shell PATH.
for _pgbin in /usr/lib/postgresql/*/bin; do
  [ -d "$_pgbin" ] && export PATH="$_pgbin:$PATH"
done

PG_PORT="${PG_PORT:-15533}"
DAEMON_PORT="${DAEMON_PORT:-18280}"
IDP_PORT="${IDP_PORT:-18281}"
DATA_DIR="${DATA_DIR:-/tmp/pg_e2e_auth_$$}"
DISPOSABLE_DB="${DISPOSABLE_DB:-ensemble_e2e_auth_$$}"
LOG_DIR="${LOG_DIR:-/tmp/e2e_auth_logs}"
DATA_DIR_E2E="$REPO_ROOT/data_e2e_auth"

mkdir -p "$LOG_DIR"

action="${1:-start}"
shift || true
fake_idp=0
for arg in "$@"; do
  case "$arg" in
    --fake-idp) fake_idp=1 ;;
  esac
done
if [ "${USE_FAKE_IDP:-0}" = "1" ]; then
  fake_idp=1
fi

# ── Factored teardown (mirrors snapshots/maintenance verbatim; only ─────
# DATA_DIR + DATA_DIR_E2E + LOG_DIR + the fake-idp pid differ).
cleanup() {
  echo "[cleanup] teardown start (DAEMON pid file=$LOG_DIR/daemon.pid, PG_PORT=$PG_PORT, DATA_DIR=$DATA_DIR)" | tee -a "$LOG_DIR/boot.log"
  # 1. Kill the fake IdP if we spawned it.
  if [ -f "$LOG_DIR/fakeidp.pid" ]; then
    local pid
    pid="$(cat "$LOG_DIR/fakeidp.pid")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
    fi
    rm -f "$LOG_DIR/fakeidp.pid"
  fi
  # 2. Kill daemon.
  if [ -f "$LOG_DIR/daemon.pid" ]; then
    local pid
    pid="$(cat "$LOG_DIR/daemon.pid")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
    fi
    rm -f "$LOG_DIR/daemon.pid"
  fi
  # 3. Stop PG — guard against clobbering a foreign cluster.
  if pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then
    if [ -d "$DATA_DIR" ]; then
      pg_ctl -D "$DATA_DIR" stop >> "$LOG_DIR/boot.log" 2>&1 || true
    else
      echo "[cleanup] WARN: $PG_PORT is up but $DATA_DIR is absent — refusing to stop a foreign cluster" | tee -a "$LOG_DIR/boot.log"
    fi
  fi
  # 4. Remove cluster + e2e data dir (unless ENSEMBLE_E2E_KEEP=1).
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
  #    cannot bleed through (mirrors snapshots verbatim).
  unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL
  unset ENSEMBLE_DB_DSN

  # Refuse to silently adopt a stale cluster.
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
  if ! pg_isready -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1; then
    pg_ctl -D "$DATA_DIR" -l "$LOG_DIR/pg.log" -o "-p $PG_PORT -k /tmp" start >> "$LOG_DIR/boot.log" 2>&1
    sleep 1
  fi

  echo "[boot] Creating disposable database: $DISPOSABLE_DB" | tee -a "$LOG_DIR/boot.log"
  if ! psql -h 127.0.0.1 -p "$PG_PORT" -d postgres -c "SELECT 1 FROM pg_database WHERE datname='$DISPOSABLE_DB'" -tA 2>/dev/null | grep -q 1; then
    createdb -h 127.0.0.1 -p "$PG_PORT" "$DISPOSABLE_DB" >> "$LOG_DIR/boot.log" 2>&1
  fi
  # NOTE on F1 (PG-only DROP CONSTRAINT migration 20260714_000001):
  # the migration runner is deliberately NO-OP on PG (blueprint invariant —
  # runner is SQLite-only-by-design), so PG boots are unaffected by the
  # F1 trap. SQLite legs would need the ledger pre-mark; PG does not.
  # See .agents/tester/RESULTS/2026-10-11-oauth2-p4-verification.md F1.

  export POSTGRES_HOST="127.0.0.1"
  export POSTGRES_PORT="$PG_PORT"
  export POSTGRES_DB="$DISPOSABLE_DB"
  export POSTGRES_USER="$(whoami)"
  unset POSTGRES_PASSWORD

  export DAEMON_PORT="$DAEMON_PORT"
  export PORT="$DAEMON_PORT"
  export DATA_DIR_E2E="$REPO_ROOT/data_e2e_auth"
  export ENSEMBLE_DATA_DIR="$DATA_DIR_E2E"
  mkdir -p "$DATA_DIR_E2E"

  if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "[boot] ERROR: OPENAI_API_KEY must be set for the daemon lifespan." >&2
    exit 1
  fi

  # 2. Auth-ON enablement env block (per §6.3 enablement matrix).
  #    The cookie dev flags (allow_insecure_cookie / cookie_secure=false)
  #    are the LAN opt-in (D8) — production uses HTTPS + Secure cookies.
  export ENSEMBLE_AUTH_ENABLED="true"
  export ENSEMBLE_AUTH_CLIENT_ID="${ENSEMBLE_AUTH_CLIENT_ID:-e2e-auth-client}"
  export ENSEMBLE_AUTH_CLIENT_SECRET="${ENSEMBLE_AUTH_CLIENT_SECRET:-e2e-auth-secret}"
  # Issuer URL is the fake IdP's URL when --fake-idp is set; otherwise
  # the operator's real IdP URL is read from the verifier-supplied env.
  if [ "$fake_idp" = "1" ]; then
    export ENSEMBLE_AUTH_ISSUER="http://127.0.0.1:${IDP_PORT}"
  else
    export ENSEMBLE_AUTH_ISSUER="${ENSEMBLE_AUTH_ISSUER:-https://idp.example.com}"
  fi
  export ENSEMBLE_AUTH_ALLOWED_ORIGINS="${ENSEMBLE_AUTH_ALLOWED_ORIGINS:-http://127.0.0.1:${DAEMON_PORT}}"
  # Fail-closed (auth-ON boot exit-78 contract): an empty ALLOWED_ORIGINS
  # aborts the boot explicitly — not implied by the :- idiom above.
  [ -z "$ENSEMBLE_AUTH_ALLOWED_ORIGINS" ] && { echo "FATAL: ENSEMBLE_AUTH_ALLOWED_ORIGINS empty" >&2; exit 78; }
  export ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE="${ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE:-http://127.0.0.1:${DAEMON_PORT}}"
  export ENSEMBLE_AUTH_ALLOW_INSECURE_COOKIE="${ENSEMBLE_AUTH_ALLOW_INSECURE_COOKIE:-true}"
  export ENSEMBLE_AUTH_COOKIE_SECURE="${ENSEMBLE_AUTH_COOKIE_SECURE:-false}"
  export ENSEMBLE_AUTH_VSCODE_PROTECTED="${ENSEMBLE_AUTH_VSCODE_PROTECTED:-false}"
  export ENSEMBLE_AUTH_DOCS_ENABLED="${ENSEMBLE_AUTH_DOCS_ENABLED:-true}"

  # 3. Optionally spawn the fake IdP as a tiny uvicorn side process
  #    (Docker-less — NFR-10). The fake lives in tests/fakes and is
  #    reachable at the issuer URL the daemon's AuthConfig points at.
  if [ "$fake_idp" = "1" ]; then
    echo "[boot] Starting fake OIDC provider on :$IDP_PORT..." | tee -a "$LOG_DIR/boot.log"
    cd "$REPO_ROOT"
    uv run python -m uvicorn tests.fakes.fake_oidc_provider:app \
      --factory --host 127.0.0.1 --port "$IDP_PORT" \
      --log-level warning --timeout-graceful-shutdown 5 \
      >> "$LOG_DIR/fakeidp.log" 2>&1 &
    FAKE_IDP_PID=$!
    echo "$FAKE_IDP_PID" > "$LOG_DIR/fakeidp.pid"
    # Wait briefly for the fake IdP to come up.
    sleep 1
  fi

  echo "[boot] Starting daemon on port $DAEMON_PORT..." | tee -a "$LOG_DIR/boot.log"
  cd "$REPO_ROOT"
  trap 'cleanup' TERM INT
  trap 'cleanup' EXIT
  uv run python -m uvicorn daemon.api:app \
    --host 127.0.0.1 --port "$DAEMON_PORT" \
    --log-level info --timeout-graceful-shutdown 10 \
    >> "$LOG_DIR/daemon.log" 2>&1 &
  DAEMON_PID=$!
  echo "$DAEMON_PID" > "$LOG_DIR/daemon.pid"
  echo "[boot] daemon pid=$DAEMON_PID, log=$LOG_DIR/daemon.log" | tee -a "$LOG_DIR/boot.log"

  # 4. Wait for /readyz to respond (AMEND v1.2 / A7 — public probes
  #    reachable POST-restart, BEFORE any session revalidation).
  echo "[boot] Waiting for daemon canary (/readyz → status:'ready')..." | tee -a "$LOG_DIR/boot.log"
  for i in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$DAEMON_PORT/readyz" > "$LOG_DIR/canary.json" 2>/dev/null; then
      state=$(python3 -c "import json,sys; d=json.load(open('$LOG_DIR/canary.json')); print(d.get('status','?'))" 2>/dev/null || echo "?")
      echo "[boot] Canary response: status='$state'" | tee -a "$LOG_DIR/boot.log"
      if [ "$state" = "ready" ]; then
        echo "[boot] OK — daemon is READY on :$DAEMON_PORT (DB $DISPOSABLE_DB)" | tee -a "$LOG_DIR/boot.log"
        # Run the auth-health-probes canary (T5.6) inline — surfaces the
        # 5/5 NOT-401 contract at boot time.
        if [ -x "$REPO_ROOT/tests/e2e/auth_health_probes.sh" ]; then
          BASE_URL="http://127.0.0.1:$DAEMON_PORT" \
            "$REPO_ROOT/tests/e2e/auth_health_probes.sh" || \
            echo "[boot] WARN: auth_health_probes canary failed (non-fatal)" >&2
        fi
        # BLOCK on the daemon process.
        wait "$DAEMON_PID"
        cleanup
        exit 0
      fi
    fi
    sleep 1
  done
  echo "[boot] ERROR: daemon did not become READY within 60 seconds" >&2
  echo "[boot] Last canary response: $(cat "$LOG_DIR/canary.json" 2>/dev/null || echo '(none)')" >&2
  exit 1
}

# ── stop ───────────────────────────────────────────────────────────────────
action_stop() {
  cleanup
}

case "$action" in
  start) action_start ;;
  stop) action_stop ;;
  *)
    echo "Usage: $0 {start|stop} [--fake-idp]" >&2
    exit 2
    ;;
esac