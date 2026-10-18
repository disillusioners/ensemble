#!/usr/bin/env bash
# brute-drill common fence — live-view-passcode commission items 3+4.
# FENCING (critical-note convention): all mutable drill state lives under
# ONE mktemp root. DELETION IS ESCAPE-GUARDED (case-guard on the mktemp
# prefix). NEVER resolves live-shaped/HOME-derived install paths.
# DB: dev PG ensemble_dev (asserted — abort on ensemble_prod/unset).
# KEY: ONE fresh drill-only Fernet key minted per fence, REUSED across
# ALL boots/stages (rotation would invalidate grants/passcodes and fake
# a defect).
# PORT: 8083 (verified free). Never 8088.

set -uo pipefail

BRUTE_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # evidence .../brute/
WT="$(cd "$BRUTE_HERE/../../../../../.." && pwd)"             # worktree root
BASE_URL="http://127.0.0.1:8083"
PORT=8083
VIEW_ROOT_NAME="bruteroot1"
PUB="/views/public/${VIEW_ROOT_NAME}/target.md"
PUB5="/views/public/${VIEW_ROOT_NAME}/target5.md"
export BRUTE_HERE WT BASE_URL PORT VIEW_ROOT_NAME PUB PUB5

fence_init() {
  if [ -f /tmp/liveview-brute-fence/state.env ]; then
    echo "== fence already initialized at /tmp/liveview-brute-fence"
    return 0
  fi
  if [ -d /tmp/liveview-brute-fence ]; then
    echo "ABORT: /tmp/liveview-brute-fence already exists (stale fence) — run teardown first"; exit 98
  fi
  local FENCE
  FENCE="$(mktemp -d /tmp/liveview-brute.XXXXXX)"
  case "$FENCE" in /tmp/liveview-brute.*) ;; *) echo "FENCE FAIL: $FENCE"; exit 99;; esac
  mkdir -p "$FENCE/views/${VIEW_ROOT_NAME}" "$FENCE/data"
  printf '# Brute drill target\n\nSECRET-CONTENT-marker-target-md\n' > "$FENCE/views/${VIEW_ROOT_NAME}/target.md"
  printf '# Brute drill A5 view\n\nSECRET-CONTENT-marker-target5-md\n' > "$FENCE/views/${VIEW_ROOT_NAME}/target5.md"
  mv "$FENCE" /tmp/liveview-brute-fence
  case "/tmp/liveview-brute-fence" in /tmp/liveview-brute*) ;; *) echo "FENCE FAIL"; exit 99;; esac
  FENCE="/tmp/liveview-brute-fence"
  KEY="$("$WT/.venv/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  cat > "$FENCE/state.env" <<EOF
FENCE="$FENCE"
FERNET_KEY="$KEY"
VIEWS_ROOT="$FENCE/views/${VIEW_ROOT_NAME}"
DATA_DIR="$FENCE/data"
EOF
}

fence_load() {
  # shellcheck disable=SC1091
  source /tmp/liveview-brute-fence/state.env
  export FENCE FERNET_KEY VIEWS_ROOT DATA_DIR
  case "$FENCE" in /tmp/liveview-brute*) ;; *) echo "FENCE GUARD FAIL: $FENCE"; exit 99;; esac
}

fence_teardown() {
  fence_load
  echo "== fence teardown (escape-guarded): $FENCE"
  case "$FENCE" in /tmp/liveview-brute-fence) rm -rf "$FENCE";; *) echo "REFUSED (guard)"; exit 99;; esac
  rm -f /tmp/liveview-brute-fence/state.env 2>/dev/null
  rmdir /tmp/liveview-brute-fence 2>/dev/null || true
}

preboot_asserts() {
  fence_load
  # Worktree .env for POSTGRES_*
  set -a; source "$WT/.env"; set +a
  echo "== PREBOOT ASSERT: POSTGRES_DB='$POSTGRES_DB'"
  if [ "${POSTGRES_DB:-}" != "ensemble_dev" ]; then
    echo "ABORT: POSTGRES_DB is '$POSTGRES_DB' — must be ensemble_dev"; exit 98
  fi
  if [ -z "${POSTGRES_PASSWORD:-}" ]; then echo "ABORT: POSTGRES_PASSWORD unset"; exit 98; fi
  if ss -ltn "sport = :${PORT}" | grep -q LISTEN; then echo "ABORT: port $PORT not free"; exit 98; fi
  echo "== PREBOOT ASSERT OK: dev DB, port $PORT free, fence=$FENCE"
}

boot_daemon() { # $1 = boot label, $2 = log path; extra env via ENVS_EXTRA (eval'd)
  fence_load
  preboot_asserts
  local LABEL="$1" LOG="$2"
  set -a; source "$WT/.env"; set +a
  # fenced overrides
  export ENSEMBLE_CONFIG="$WT/config.yaml"   # worktree config (boot preflight)
  export DAEMON_HOST=127.0.0.1
  export DAEMON_PORT=$PORT
  export ENSEMBLE_DATA_DIR="$DATA_DIR"
  export SYSTEM_ENCRYPTION_KEY="$FERNET_KEY"
  export ENSEMBLE_LIVE_VIEWS_ROOTS="{\"${VIEW_ROOT_NAME}\": {\"type\": \"filesystem\", \"path\": \"$VIEWS_ROOT\"}}"
  # lockout threshold stays DEFAULT (never set)
  unset ENSEMBLE_LIVE_VIEWS_PASSCODE_LOCKOUT_THRESHOLD || true
  # optional documented overrides for phase B
  if [ -n "${ENVS_EXTRA:-}" ]; then eval "export $ENVS_EXTRA"; fi
  echo "== boot[$LABEL]: data_dir=$ENSEMBLE_DATA_DIR port=$PORT extra=[${ENVS_EXTRA:-}]"
  : > "$LOG"
  setsid "$WT/.venv/bin/python" -m daemon >> "$LOG" 2>&1 &
  local BOOT_PID=$!
  echo "$BOOT_PID" > "$FENCE/boot.pid"
  local UP=0 i
  for i in $(seq 1 90); do
    if curl -s -o /dev/null --max-time 2 "$BASE_URL/views/livez"; then UP=1; break; fi
    if ! kill -0 "$BOOT_PID" 2>/dev/null; then echo "== BOOT DIED after ${i}s"; tail -30 "$LOG"; exit 97; fi
    sleep 1
  done
  if [ "$UP" != "1" ]; then echo "== daemon not up in 90s"; tail -30 "$LOG"; stop_daemon; exit 97; fi
  echo "== daemon[$LABEL] UP (pid=$BOOT_PID, waited ${i}s)"
}

stop_daemon() {
  fence_load
  local BOOT_PID
  BOOT_PID="$(cat "$FENCE/boot.pid" 2>/dev/null || echo '')"
  [ -z "$BOOT_PID" ] && { echo "== no boot pid"; return 0; }
  # SAFETY: verify the pid we are about to kill is OUR daemon bound to OUR port
  if ss -ltnp "sport = :${PORT}" 2>/dev/null | grep -q "pid=${BOOT_PID},"; then
    echo "== killing pid=$BOOT_PID (verified bound to :$PORT)"
  else
    echo "== pid=$BOOT_PID not confirmed on :$PORT — checking alive anyway (started by us this fence)"
  fi
  kill -TERM -- -"$BOOT_PID" 2>/dev/null
  local t
  for t in $(seq 1 30); do kill -0 "$BOOT_PID" 2>/dev/null || break; sleep 1; done
  if kill -0 "$BOOT_PID" 2>/dev/null; then echo "== TERM timeout — KILL"; kill -KILL -- -"$BOOT_PID" 2>/dev/null; fi
  echo "== daemon stopped"
}

# service-level mint/refresh/rowpeek seam (same DB + SAME key as daemon).
# SEAM NOTE: view_link tool path requires a live agent instance context
# (Cardinal #2 actor); the proven state-b drill recipe uses the
# ViewPasscodeService directly with identical DB+key — seam recorded in
# the report. mint+refresh via svc.mint / svc.refresh; rowpeek is
# read-only via svc.get_row.
pgsvc() { # pgsvc <op> <root> <rel> [actor]
  fence_load
  # POSTGRES_* ALWAYS from the WORKTREE .env (ambient env may carry a
  # DIFFERENT POSTGRES_DB — observed ensemble_prod in the executor's
  # ambient env; the dev-DB assert below is the fail-closed backstop)
  set -a; source "$WT/.env"; set +a
  local OP="$1" ROOT="$2" REL="$3" ACTOR="${4:-brute-drill}"
  SYSTEM_ENCRYPTION_KEY="$FERNET_KEY" \
  POSTGRES_HOST="$POSTGRES_HOST" POSTGRES_PORT="$POSTGRES_PORT" \
  POSTGRES_DB="$POSTGRES_DB" POSTGRES_USER="$POSTGRES_USER" POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  POSTGRES_DB_ASSERT=ensemble_dev \
  "$WT/.venv/bin/python" - "$OP" "$ROOT" "$REL" "$ACTOR" <<'PYEOF'
import os, sys
assert os.environ["POSTGRES_DB"] == "ensemble_dev", "ABORT: not dev DB"
op, root, rel, actor = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
from sqlalchemy import create_engine
from daemon.services.view_passcode_service import ViewPasscodeCipher, ViewPasscodeService
url = f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
svc = ViewPasscodeService(engine=create_engine(url), cipher=ViewPasscodeCipher())
if op == "mint":
    try:
        r = svc.mint(root, rel, actor=actor)
    except Exception as rot:
        print(f"mint -> {type(rot).__name__}; recovering via refresh (documented)")
        r = svc.refresh(root, rel, actor=actor)
    print(f"OP=mint passcode={r.passcode} generation={r.generation} created={r.created} locked={r.locked_status}")
elif op == "refresh":
    r = svc.refresh(root, rel, actor=actor)
    print(f"OP=refresh passcode={r.passcode} generation={r.generation} created={r.created} locked={r.locked_status}")
elif op == "rowpeek":
    row = svc.get_row(root, rel)
    if row is None:
        print("OP=rowpeek row=None")
    else:
        print(f"OP=rowpeek failed_attempts={row.failed_attempts} locked={bool(row.locked)} generation={row.generation}")
else:
    print(f"unknown op {op}"); sys.exit(2)
PYEOF
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  # executed directly (stage scripts SOURCE this file — dispatcher skipped then)
  case "${1:-}" in
    fence-init) fence_init ;;
    fence-load) fence_load; echo "FENCE=$FENCE"; echo "PORT=$PORT" ;;
    stop) stop_daemon ;;
    teardown) stop_daemon; fence_teardown ;;
    *) echo "usage: brute-lib.sh {fence-init|fence-load|teardown}" >&2; exit 2 ;;
  esac
fi
