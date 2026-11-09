#!/usr/bin/env bash
# rk_stop.sh <boot-tag> — stop ONLY this lane's daemon, verified.
# PORT-VERIFY-BEFORE-KILL: the pid in $FENCE/boot-pid-<tag> must be alive,
# must be a python daemon process, and must be the pid LISTENING on 8082.
# Nothing else is ever signalled.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
TAG="${1:?boot tag required}"
PIDFILE="$FENCE/boot-pid-$TAG"
if [ ! -f "$PIDFILE" ]; then echo "no pidfile $PIDFILE — nothing to stop"; exit 0; fi
PID="$(cat "$PIDFILE")"

if [ -d "/proc/$PID" ]; then
  CMD="$(tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null || true)"
  CWD="$(readlink -f "/proc/$PID/cwd" 2>/dev/null || echo '')"
  LISTENERS="$(ss -ltnp "sport = :${PORT}" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | tr '\n' ' ')"
  echo "== pid=$PID cwd=$CWD"
  echo "== cmdline=$CMD"
  echo "== listeners on ${PORT}: ${LISTENERS:-none}"
  case "$CMD" in *"-m daemon"*) ;; *) echo "ABORT: cmdline is not the daemon"; exit 97;; esac
  case "$LISTENERS" in *"$PID"*) ;; *) echo "ABORT: pid $PID is not the ${PORT} listener"; exit 97;; esac
  kill -TERM "$PID" 2>/dev/null || true
  for i in $(seq 1 20); do
    [ -d "/proc/$PID" ] || break
    sleep 1
  done
  if [ -d "/proc/$PID" ]; then
    echo "== SIGTERM ignored — SIGKILL"
    kill -KILL "$PID" 2>/dev/null || true
    sleep 1
  fi
  echo "== stopped pid=$PID"
else
  echo "== pid=$PID already gone"
fi

# verify the port is free
sleep 1
if ss -ltn "sport = :${PORT}" 2>/dev/null | grep -q LISTEN; then
  echo "!! PORT ${PORT} STILL LISTENING — inspect manually"; ss -ltnp "sport = :${PORT}"
else
  echo "== port ${PORT} free"
fi
