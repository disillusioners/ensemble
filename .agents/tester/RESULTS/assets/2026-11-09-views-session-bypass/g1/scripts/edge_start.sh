#!/usr/bin/env bash
# Start the TLS edge (8443 -> 8081) with request-header tracing on.
set -uo pipefail
FENCE="$(cat /tmp/g1fence.ready)"
case "$FENCE" in /tmp/g1lv.*) ;; *) echo "FENCE FAIL"; exit 99;; esac
# shellcheck disable=SC1090
source "$FENCE/state.env"
WT=/home/nea/ensemble-src-wt-views-session-bypass
LOGDIR="$FENCE/logs"
ss -ltn "sport = :8443" | grep -q LISTEN && { echo "port 8443 busy"; exit 98; }
EDGE_PORT=8443 UP_PORT=8081 EDGE_DEBUG=1 \
  setsid "$WT/.venv/bin/python" "$WT/.agents/tester/RESULTS/assets/2026-11-09-views-session-bypass/g1/scripts/edge_proxy.py" \
    "$FENCE/edge-cert.pem" "$FENCE/edge-key.pem" \
    >> "$LOGDIR/edge.log" 2>&1 &
PID=$!
echo "$PID" > "$FENCE/edge.pid"
for i in $(seq 1 30); do
  if curl -sk -o /dev/null --max-time 2 "https://127.0.0.1:8443/views/livez"; then
    echo "== edge UP pid=$PID (${i}s)"; exit 0
  fi
  kill -0 "$PID" 2>/dev/null || { echo "== EDGE DIED"; tail -20 "$LOGDIR/edge.log"; exit 97; }
  sleep 1
done
echo "== edge not up in 30s"; tail -20 "$LOGDIR/edge.log"; exit 97
