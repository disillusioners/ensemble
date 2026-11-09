#!/usr/bin/env bash
# CELL (iv) BOOT — UNCONFIGURED NON-LOOPBACK peer: auth-OFF, NO declared
# base, daemon bound to THIS host's LAN interface 192.168.1.151 ONLY
# (never 0.0.0.0). Port 8083. TCP peer for the probes = the LAN IP
# (non-loopback) — curl connects to http://192.168.1.151:8083.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
ensure_fence_state
set -a; source "$FENCE/state.env"; set +a
export ENSEMBLE_LIVE_VIEWS_ROOTS="$ROOTS_JSON"
ensure_fixtures

LAN_IP="$(hostname -I | awk '{print $1}')"
case "$LAN_IP" in
  127.*|10.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*|192.168.*) : ;;  # private/LAN ok
  *) echo "ABORT: '$LAN_IP' not a private/LAN address"; exit 97;;
esac
[ "$LAN_IP" = "127.0.0.1" ] && { echo "ABORT: loopback picked"; exit 97; }
export ENSEMBLE_AUTH_ENABLED=false
export ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL=""   # NO declared base
export HOST="$LAN_IP" DAEMON_HOST="$LAN_IP"

{
  echo "# cell-(iv) boot shape — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "ENSEMBLE_AUTH_ENABLED=$ENSEMBLE_AUTH_ENABLED"
  echo "ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL=<unset/empty — NO declared base>"
  echo "ENSEMBLE_AUTH_DISCOVERY_URL=${ENSEMBLE_AUTH_DISCOVERY_URL:-<unset>}"
  echo "ENSEMBLE_AUTH_ISSUER=${ENSEMBLE_AUTH_ISSUER:-<unset>}"
  echo "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE=${ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE:-<unset>}"
  echo "BIND: HOST=$HOST DAEMON_HOST=$DAEMON_HOST PORT=$PORT DAEMON_PORT=$DAEMON_PORT (interface bind ONLY — verified via ss below)"
  echo "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"
  echo "SYSTEM_ENCRYPTION_KEY=<set redacted>"
  echo "ENSEMBLE_LIVE_VIEWS_ROOTS=$ENSEMBLE_LIVE_VIEWS_ROOTS"
} | tee "$EV/boot-cell4.env.txt"
cp "$EV/boot-cell4.env.txt" "$MIRROR/" 2>/dev/null || true

if ss -ltn "sport = :${PORT}" | grep -q LISTEN; then
  echo "PORT ${PORT} NOT FREE — aborting"; exit 98
fi
echo "== port ${PORT} free"

BOOT_LOG="$EV/boot-cell4.log"
: > "$BOOT_LOG"
setsid "$WT/.venv/bin/python" -m daemon >> "$BOOT_LOG" 2>&1 &
BOOT_PID=$!
echo "$BOOT_PID" > "$FENCE/boot-pid"
echo "== booting daemon pid=$BOOT_PID port=$PORT host=$HOST (cell iv)"

UP=0
for i in $(seq 1 90); do
  if curl -s -o /dev/null --max-time 2 "http://${LAN_IP}:${PORT}/views/livez"; then UP=1; break; fi
  sleep 1
done
if [ "$UP" != "1" ]; then
  echo "BOOT FAILED — last 40 log lines:"; tail -40 "$BOOT_LOG"; exit 96
fi
echo "== daemon UP: $(curl -s http://${LAN_IP}:${PORT}/views/livez | head -c 200)"

# ── bind disclosure + BIND ASSERT: interface bind, never wildcard ────────
ss -ltnp "sport = :${PORT}" | tee "$EV/bind-cell4.txt"
if ss -ltn "sport = :${PORT}" | grep -q '0\.0\.0\.0:'"$PORT"; then
  echo "BIND ASSERT FAIL: wildcard 0.0.0.0 bind detected — aborting cell iv"; exit 95
fi
ss -ltn "sport = :${PORT}" | grep -q "$LAN_IP:$PORT" || { echo "BIND ASSERT FAIL: $LAN_IP:$PORT not bound"; exit 95; }
echo "== bind assert OK: $LAN_IP:$PORT (no wildcard)"
cp "$EV/bind-cell4.txt" "$MIRROR/" 2>/dev/null || true
echo "$LAN_IP" > "$EV/.cell4-ip"
