#!/usr/bin/env bash
# E2E journey CLEANUP stage — kills ONLY processes this run started,
# each verified by its recorded pid AND its bound port (8081 daemon,
# 8443 edge) before signalling. Port 8088 is NEVER touched. Deletion
# of the fence root is escape-guarded (single case pattern).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EV="$(cd "$HERE/.." && pwd)"

[ -f "$EV/.drill-tmp" ] || { echo "no .drill-tmp — nothing to clean"; exit 0; }
DRILL_TMP="$(cat "$EV/.drill-tmp")"
case "$DRILL_TMP" in /tmp/lvjourney.*) ;; *) echo "FENCE FAIL: $DRILL_TMP — refusing"; exit 99;; esac

stop_by_pid_and_port() { # <pidfile> <port> <label>
  local pidfile="$1" port="$2" label="$3"
  [ -f "$pidfile" ] || { echo "== $label: no pidfile ($pidfile) — skip"; return 0; }
  local pid; pid="$(cat "$pidfile")"
  if ! kill -0 "$pid" 2>/dev/null; then echo "== $label: pid $pid already dead — skip"; return 0; fi
  # VERIFY the recorded pid actually holds <port> before killing.
  if ss -ltnp "sport = :${port}" 2>/dev/null | grep -q "pid=${pid},"; then
    echo "== $label: pid $pid verified bound to :${port} — TERM"
    kill -TERM -- -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
    for t in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    if kill -0 "$pid" 2>/dev/null; then
      echo "== $label: TERM timeout — KILL pid $pid"
      kill -KILL -- -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
    fi
    echo "== $label: stopped"
  else
    echo "== $label: pid $pid NOT bound to :${port} — NOT touching it (guard)"
  fi
}

# Edge proxy first (holds 8443), then the daemon (8081).
stop_by_pid_and_port "$DRILL_TMP/proxy-pid" 8443 "edge-proxy"
stop_by_pid_and_port "$DRILL_TMP/boot-pid"  8081 "daemon"

# Belt: confirm 8081/8443 are free of OUR pids; 8088 untouched by design.
sleep 1
for p in 8081 8443; do
  if ss -ltn "sport = :${p}" | grep -q LISTEN; then
    echo "WARN: :${p} still LISTENing (holder: $(ss -ltnp "sport = :${p}" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1))"
  else
    echo "== :${p} free"
  fi
done

# Escape-guarded fence removal.
case "$DRILL_TMP" in /tmp/lvjourney.*) rm -rf "$DRILL_TMP";; esac
rm -f "$EV/.drill-tmp"
echo "== fence removed; cleanup complete"
