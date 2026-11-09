#!/usr/bin/env bash
# CELL (iii) BOOT — UNCONFIGURED loopback carve shape: auth-OFF, NO declared
# base (ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL scrubbed to unset), no
# audience (auth OFF -> no AuthConfig). Port 8083, bind 127.0.0.1.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
ensure_fence_state
set -a; source "$FENCE/state.env"; set +a
export ENSEMBLE_LIVE_VIEWS_ROOTS="$ROOTS_JSON"
ensure_fixtures

# ── cell-(iii) boot shape (lib scrub already unset the declared base) ────
export ENSEMBLE_AUTH_ENABLED=false
export ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL=""   # NO declared base (empty = unset for the accessor)
export HOST=127.0.0.1 DAEMON_HOST=127.0.0.1

{
  echo "# cell-(iii) boot shape — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "ENSEMBLE_AUTH_ENABLED=$ENSEMBLE_AUTH_ENABLED"
  echo "ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL=<unset/empty — NO declared base>"
  echo "ENSEMBLE_AUTH_DISCOVERY_URL=${ENSEMBLE_AUTH_DISCOVERY_URL:-<unset>}"
  echo "ENSEMBLE_AUTH_ISSUER=${ENSEMBLE_AUTH_ISSUER:-<unset>}"
  echo "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE=${ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE:-<unset>}"
  echo "HOST=$HOST DAEMON_HOST=$DAEMON_HOST PORT=$PORT DAEMON_PORT=$DAEMON_PORT"
  echo "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"
  echo "SYSTEM_ENCRYPTION_KEY=<set redacted>"
  echo "ENSEMBLE_LIVE_VIEWS_ROOTS=$ENSEMBLE_LIVE_VIEWS_ROOTS"
} | tee "$EV/boot-cell3.env.txt"
cp "$EV/boot-cell3.env.txt" "$MIRROR/" 2>/dev/null || true

if ss -ltn "sport = :${PORT}" | grep -q LISTEN; then
  echo "PORT ${PORT} NOT FREE — aborting"; exit 98
fi
echo "== port ${PORT} free"

BOOT_LOG="$EV/boot-cell3.log"
: > "$BOOT_LOG"
setsid "$WT/.venv/bin/python" -m daemon >> "$BOOT_LOG" 2>&1 &
BOOT_PID=$!
echo "$BOOT_PID" > "$FENCE/boot-pid"
echo "== booting daemon pid=$BOOT_PID port=$PORT host=$HOST (cell iii)"

UP=0
for i in $(seq 1 90); do
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/views/livez"; then UP=1; break; fi
  sleep 1
done
if [ "$UP" != "1" ]; then
  echo "BOOT FAILED — last 40 log lines:"; tail -40 "$BOOT_LOG"; exit 96
fi
echo "== daemon UP: $(curl -s http://127.0.0.1:${PORT}/views/livez | head -c 200)"

ss -ltnp "sport = :${PORT}" | tee "$EV/bind-cell3.txt"
cp "$EV/bind-cell3.txt" "$MIRROR/" 2>/dev/null || true
