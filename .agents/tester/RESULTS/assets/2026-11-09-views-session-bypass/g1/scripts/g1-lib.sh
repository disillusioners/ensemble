#!/usr/bin/env bash
# G1 CLOSURE GATE fence + boot library — views-session-bypass (2026-11-09).
# Adapted from .agents/tester/RESULTS/assets/2026-10-18-liveview-passcode/
#   journey/scripts/{boot.sh,cleanup.sh} and brute/brute-lib.sh (PROVEN estate).
# Deltas: root name g1root1 (fresh rows, avoids persisted-key rotation),
#         TLS edge header-dump proxy, env scrubbed per-variable.
#
# SAFETY invariants (non-negotiable):
#   * POSTGRES_* scrubbed PER VARIABLE (env -u POSTGRES_* is a NO-OP), then
#     sourced from the worktree .env; hard assert POSTGRES_DB == ensemble_dev.
#   * ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 EXPORTED EXPLICITLY (daemon child
#     shell; the sweep kills LIVE processes when the var is absent).
#   * Ports 8081 (app) / 8443 (edge). 8088 is NEVER touched.
#   * Every kill is port-verified first.
#   * Fence root is mktemp'd + escape-guarded; never a live-shaped HOME path.

set -uo pipefail

WT=/home/nea/ensemble-src-wt-views-session-bypass
ASSET_DIR="$WT/.agents/tester/RESULTS/assets/2026-11-09-views-session-bypass/g1"
APP_PORT=8081
EDGE_PORT=8443
VIEW_ROOT_NAME=g1root2
VIEW_REL=gate.md
PUB_PATH="/views/public/${VIEW_ROOT_NAME}/${VIEW_REL}"

die() { echo "ABORT: $*" >&2; exit 90; }

fence_init() {
  [ -f /tmp/g1fence.ready ] && { echo "== fence already initialized"; return 0; }
  local F
  F="$(mktemp -d /tmp/g1lv.XXXXXX)"
  case "$F" in /tmp/g1lv.*) ;; *) die "fence path $F";; esac
  mkdir -p "$F/roots/$VIEW_ROOT_NAME" "$F/data" "$F/logs"
  cat > "$F/roots/$VIEW_ROOT_NAME/$VIEW_REL" <<'EOF'
# G1 Gate View

**G1-CONTENT-MARKER** — this markdown body proves the passcode gate opened.

- navigation reached the view
- no JSON error page was rendered
EOF
  KEY="$("$WT/.venv/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  cat > "$F/state.env" <<EOF
FENCE='$F'
FERNET_KEY='$KEY'
VIEWS_ROOT='$F/roots/$VIEW_ROOT_NAME'
DATA_DIR='$F/data'
APP_PORT=$APP_PORT
EDGE_PORT=$EDGE_PORT
VIEW_ROOT_NAME=$VIEW_ROOT_NAME
VIEW_REL=$VIEW_REL
PUB_PATH=$PUB_PATH
EOF
  echo "$F" > /tmp/g1fence.ready
  echo "== fence initialized: $F"
}

fence_load() {
  [ -f /tmp/g1fence.ready ] || die "no fence — run fence_init"
  local F; F="$(cat /tmp/g1fence.ready)"
  case "$F" in /tmp/g1lv.*) ;; *) die "fence guard: $F";; esac
  # shellcheck disable=SC1090
  source "$F/state.env"
  case "$FENCE" in /tmp/g1lv.*) ;; *) die "fence guard: $FENCE";; esac
  export FENCE FERNET_KEY VIEWS_ROOT DATA_DIR APP_PORT EDGE_PORT \
         VIEW_ROOT_NAME VIEW_REL PUB_PATH
}

# dev-DB env: scrub ambient per-variable, then take the worktree .env.
dev_env() {
  unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT \
        PGHOST PGPORT PGUSER PGPASSWORD \
        SSL_CERT_FILE SSL_CERT_DIR 2>/dev/null
  set -a; source "$WT/.env"; set +a
  [ "${POSTGRES_DB:-}" = "ensemble_dev" ] || die "POSTGRES_DB='${POSTGRES_DB:-}' != ensemble_dev"
  [ -n "${POSTGRES_PASSWORD:-}" ] || die "POSTGRES_PASSWORD unset"
  echo "== dev DB asserted: $POSTGRES_USER@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB"
}

boot_daemon() {
  fence_load; dev_env
  local LOG="$FENCE/logs/daemon.log"
  ss -ltn "sport = :$APP_PORT" | grep -q LISTEN && die "port $APP_PORT busy"
  export ENSEMBLE_CONFIG="$WT/config.yaml"
  export PORT=$APP_PORT DAEMON_PORT=$APP_PORT HOST=127.0.0.1
  export ENSEMBLE_DATA_DIR="$DATA_DIR"
  export SYSTEM_ENCRYPTION_KEY="$FERNET_KEY"
  export ENSEMBLE_SELF_ENV=dev
  # CRITICAL: explicit, never `env -u`
  export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
  # auth ON (proven State-B shape)
  # Ambient daemon-child env carries LIVE auth vars (discovery URL pointing at
  # the live front). Clear them per-variable so the drill's placeholder issuer
  # stands alone; discovery is then deferred (WARN-not-fatal).
  unset ENSEMBLE_AUTH_DISCOVERY_URL ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE 2>/dev/null
  export ENSEMBLE_AUTH_ENABLED=true
  export ENSEMBLE_AUTH_CLIENT_ID=g1-drill-client
  export ENSEMBLE_AUTH_CLIENT_SECRET=g1-drill-secret
  export ENSEMBLE_AUTH_ISSUER=https://g1.drill.invalid
  export ENSEMBLE_AUTH_ALLOWED_ORIGINS="http://127.0.0.1:${APP_PORT} https://127.0.0.1:${EDGE_PORT}"
  export ENSEMBLE_LIVE_VIEWS_ROOTS="{\"${VIEW_ROOT_NAME}\": {\"type\": \"filesystem\", \"path\": \"$VIEWS_ROOT\"}}"
  unset ENSEMBLE_LIVE_VIEWS_PASSCODE_LOCKOUT_THRESHOLD 2>/dev/null
  : > "$LOG"
  setsid "$WT/.venv/bin/python" -m daemon >> "$LOG" 2>&1 &
  local PID=$!
  echo "$PID" > "$FENCE/daemon.pid"
  local i UP=0
  for i in $(seq 1 120); do
    if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$APP_PORT/views/livez"; then UP=1; break; fi
    kill -0 "$PID" 2>/dev/null || { echo "== BOOT DIED after ${i}s"; tail -40 "$LOG"; return 97; }
    sleep 1
  done
  [ "$UP" = 1 ] || { echo "== not up in 120s"; tail -40 "$LOG"; return 97; }
  echo "== daemon UP pid=$PID (${i}s) log=$LOG"
  curl -s "http://127.0.0.1:$APP_PORT/views/livez"; echo
  grep -m1 "derived public_path_set" "$LOG" || echo "WARN: public_path_set line missing"
  grep -m1 "passcode substrate ready" "$LOG" || echo "WARN: substrate line missing"
  return 0
}

mint_passcode() {
  fence_load; dev_env
  SYSTEM_ENCRYPTION_KEY="$FERNET_KEY" \
  POSTGRES_HOST="$POSTGRES_HOST" POSTGRES_PORT="$POSTGRES_PORT" \
  POSTGRES_DB="$POSTGRES_DB" POSTGRES_USER="$POSTGRES_USER" POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  "$WT/.venv/bin/python" - "$VIEW_ROOT_NAME" "$VIEW_REL" "$FENCE" <<'PY'
import os, sys
assert os.environ["POSTGRES_DB"] == "ensemble_dev", "ABORT: not dev DB"
root, rel, fence = sys.argv[1], sys.argv[2], sys.argv[3]
from sqlalchemy import create_engine
from daemon.services.view_passcode_service import ViewPasscodeCipher, ViewPasscodeService
url = (f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
       f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}")
svc = ViewPasscodeService(engine=create_engine(url), cipher=ViewPasscodeCipher())
try:
    r = svc.mint(root, rel, actor="g1-gate-tester")
    op = "mint"
except Exception as exc:
    print(f"mint -> {type(exc).__name__}; recovering via refresh")
    r = svc.refresh(root, rel, actor="g1-gate-tester")
    op = "refresh"
open(os.path.join(fence, "passcode"), "w").write(r.passcode)
print(f"OP={op} passcode={r.passcode} generation={r.generation} created={r.created} locked={r.locked_status}")
PY
}

stop_all() {
  fence_load
  local lbl pid port
  for pair in "edge:$FENCE/edge.pid:$EDGE_PORT" "daemon:$FENCE/daemon.pid:$APP_PORT"; do
    lbl="${pair%%:*}"; rest="${pair#*:}"; pidfile="${rest%%:*}"; port="${rest##*:}"
    [ -f "$pidfile" ] || { echo "== $lbl: no pidfile — skip"; continue; }
    pid="$(cat "$pidfile")"
    kill -0 "$pid" 2>/dev/null || { echo "== $lbl: pid $pid already dead"; continue; }
    if ss -ltnp "sport = :$port" 2>/dev/null | grep -q "pid=${pid},"; then
      echo "== $lbl: pid $pid verified on :$port — TERM"
      kill -TERM -- -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
      for t in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      kill -0 "$pid" 2>/dev/null && { echo "== $lbl: KILL"; kill -KILL -- -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null; }
      echo "== $lbl stopped"
    else
      echo "== $lbl: pid $pid NOT bound to :$port — NOT touching (guard)"
    fi
  done
  sleep 1
  for p in "$APP_PORT" "$EDGE_PORT"; do
    ss -ltn "sport = :$p" | grep -q LISTEN && echo "WARN: :$p still LISTEN" || echo "== :$p free"
  done
}

case "${1:-}" in
  init)    fence_init ;;
  load)    fence_load; echo "FENCE=$FENCE" ;;
  devenv)  fence_load; dev_env ;;
  boot)    boot_daemon ;;
  mint)    mint_passcode ;;
  stop)    stop_all ;;
  *) echo "usage: $0 {init|load|devenv|boot|mint|stop}" >&2; exit 2 ;;
esac
