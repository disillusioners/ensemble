#!/usr/bin/env bash
# CELLS CLEANUP — port-verify-before-kill; 8083 must end FREE.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env

PORT="${CELLS_PORT:-8083}"
PID="$(cat "$FENCE/boot-pid" 2>/dev/null || true)"
if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
  if ss -ltnp "sport = :${PORT}" | grep -q "pid=${PID},"; then
    echo "== port ${PORT} verified held by OUR pid ${PID} — terminating"
    kill -TERM "$PID" 2>/dev/null || true
    for i in $(seq 1 15); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
    if kill -0 "$PID" 2>/dev/null; then
      echo "== SIGTERM grace exhausted — SIGKILL $PID"
      kill -KILL "$PID" 2>/dev/null || true
    fi
  else
    echo "!! REFUSING to kill pid ${PID}: port ${PORT} not bound by it:"
    ss -ltnp "sport = :${PORT}" || true
  fi
else
  echo "== recorded boot pid '${PID:-<none>}' not running"
fi

sleep 2
if ss -ltn "sport = :${PORT}" | grep -q LISTEN; then
  echo "!! PORT ${PORT} STILL BOUND after cleanup:"; ss -ltnp "sport = :${PORT}"; exit 1
fi
echo "== port ${PORT} FREE — cleanup complete"
