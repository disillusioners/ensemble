#!/bin/bash
# ============================================================================
# tests/test_watchdog_watcher.sh — safety + behavior for the watchdog-watcher
# ============================================================================
# Auto-Restart Phase 1 (m3): the watchdog-watcher launchd agent observes
# /livez and notifies when the daemon has been absent >600s. This test
# pins its contract against a SANDBOX install (never the real one):
#
#   1. SYNTAX:     script passes bash -n.
#   2. PRESENT:    /livez answering → quiet (no state file, no output).
#   3. FIRST MISS: no listener → state records first_miss_at, notified=0,
#                  NO notification yet.
#   4. AGED MISS:  pre-seeded first_miss_at >600s ago → notification fires
#                  EXACTLY ONCE (notified latch), state persists.
#   5. RECOVERY:   /livez answers again → state cleared, RECOVERED logged.
#   6. PORT RESOLUTION: PORT is read from INSTALL_DIR/.env (not hardwired).
#   7. TUNABLE GUARD: malformed WATCHDOG_ABSENT_THRESHOLD_S falls back to
#                  600 — never 0 (instant-notify hazard).
#   8. EXIT-0 CONTRACT: no-args + unresolvable default INSTALL_DIR → FATAL
#                  logged (naming the dir), exit 0 — no set -u abort.
#   9. ZERO TUNABLES: explicit WATCHDOG_ABSENT_THRESHOLD_S=0 / PROBE_TIMEOUT_S=0
#                  fall back to 600 / 3 like garbage (curl max-time 0 = no
#                  timeout, so zero must never reach curl).
#
# The notification step is exercised through the WATCHDOG_NOTIFY_CMD
# override (the script's documented test seam) — no osascript needed.
# Plain bash, no new dependencies. Self-asserting; nonzero exit on failure.
#
#   bash tests/test_watchdog_watcher.sh
# ============================================================================
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WATCHER="$REPO_ROOT/scripts/watchdog-watcher.sh"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
}

TMP="$(mktemp -d /tmp/ae-watchdogtest.XXXXXX)"
SERVER_PID=""
cleanup() {
    [ -n "$SERVER_PID" ] && kill -9 "$SERVER_PID" 2>/dev/null
    rm -rf "$TMP"
}
trap cleanup EXIT INT TERM

# High port, unlikely to collide; verified free below.
PORT=19139
if lsof -ti:"$PORT" >/dev/null 2>&1; then
    echo "port $PORT busy — pick another" >&2
    exit 2
fi

# ─── 1. gates ───────────────────────────────────────────────────────────────
if bash -n "$WATCHER" 2>/dev/null; then _pass; else _fail "watchdog-watcher.sh passes bash -n"; fi
command -v curl >/dev/null 2>&1 || { echo "curl required" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "python3 required" >&2; exit 2; }

start_fake_livez() {  # tiny always-200 server on $PORT
    python3 -c "
import http.server, threading, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', $PORT), H).serve_forever()
" &
    SERVER_PID=$!
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        curl -fsS --max-time 1 "http://127.0.0.1:$PORT/livez" >/dev/null 2>&1 && return 0
        sleep 0.3
    done
    _fail "fake /livez server never came up"
    return 1
}
stop_fake_livez() {
    [ -n "$SERVER_PID" ] && kill -9 "$SERVER_PID" 2>/dev/null
    SERVER_PID=""
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        lsof -ti:"$PORT" >/dev/null 2>&1 || return 0
        sleep 0.3
    done
    return 1
}

# ─── 2. present → quiet ─────────────────────────────────────────────────────
echo "== watcher: /livez present → quiet =="
mkdir -p "$TMP/inst/data"
printf 'PORT=%s\n' "$PORT" > "$TMP/inst/.env"
start_fake_livez && {
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$TMP/inst" >"$TMP/present.out" 2>&1
    [ ! -f "$TMP/inst/data/.watchdog-state" ] && _pass || _fail "present: must not write state (found one)"
    [ ! -f "$TMP/notify.log" ] && _pass || _fail "present: must not notify"
    [ -s "$TMP/present.out" ] && _fail "present: must be silent when healthy (got output)" || _pass
    stop_fake_livez
}

# ─── 3. first miss → state, no notify ───────────────────────────────────────
echo "== watcher: first miss → record episode, no notify =="
rm -f "$TMP/notify.log"
WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
    bash "$WATCHER" "$TMP/inst" >"$TMP/miss1.out" 2>&1
grep -q '^first_miss_at=[0-9]*$' "$TMP/inst/data/.watchdog-state" && _pass || _fail "first miss: state must record first_miss_at ($(cat "$TMP/inst/data/.watchdog-state" 2>/dev/null || echo no-state))"
grep -q '^notified=0$' "$TMP/inst/data/.watchdog-state" && _pass || _fail "first miss: notified must be 0"
[ ! -f "$TMP/notify.log" ] && _pass || _fail "first miss: must NOT notify yet"
grep -q "watching (threshold" "$TMP/miss1.out" && _pass || _fail "first miss: must log the watching line"

# ─── 4. aged miss → notify exactly once ─────────────────────────────────────
echo "== watcher: aged miss (>600s) → one-time notify =="
OLD_TS=$(( $(date +%s) - 601 ))
printf 'first_miss_at=%s\nnotified=0\n' "$OLD_TS" > "$TMP/inst/data/.watchdog-state"
WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
    bash "$WATCHER" "$TMP/inst" >"$TMP/aged.out" 2>&1
grep -q '^notified=1$' "$TMP/inst/data/.watchdog-state" && _pass || _fail "aged miss: notified must latch to 1"
[ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "1" ] && _pass || _fail "aged miss: exactly one notification expected"
grep -q "WATCHDOG\[absent\]" "$TMP/aged.out" && _pass || _fail "aged miss: must log WATCHDOG[absent]"
# still absent, still aged → latched, NO second notification
WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
    bash "$WATCHER" "$TMP/inst" >"$TMP/aged2.out" 2>&1
[ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "1" ] && _pass || _fail "latch: a second aged-miss run must not notify again"
grep -q '^notified=1$' "$TMP/inst/data/.watchdog-state" && _pass || _fail "latch: state must stay notified=1"

# ─── 5. recovery → state cleared ────────────────────────────────────────────
echo "== watcher: recovery → clear episode =="
start_fake_livez && {
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$TMP/inst" >"$TMP/recover.out" 2>&1
    [ ! -f "$TMP/inst/data/.watchdog-state" ] && _pass || _fail "recovery: state file must be cleared"
    grep -q "RECOVERED" "$TMP/recover.out" && _pass || _fail "recovery: must log RECOVERED"
    [ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "1" ] && _pass || _fail "recovery: must not notify"
    stop_fake_livez
}

# ─── 6. port resolution from .env ───────────────────────────────────────────
echo "== watcher: port comes from INSTALL_DIR/.env =="
PORT2=$(( PORT + 1 ))
if lsof -ti:"$PORT2" >/dev/null 2>&1; then
    _fail "port $PORT2 busy — skipping port-resolution case"
else
    printf 'export PORT="%s"\n' "$PORT2" > "$TMP/inst/.env"
    PORT="$PORT2" start_fake_livez && {
        # $PORT global drives the fake server; watcher must find it via .env
        WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
            bash "$WATCHER" "$TMP/inst" >"$TMP/portres.out" 2>&1
        [ ! -f "$TMP/inst/data/.watchdog-state" ] && _pass || _fail "port-resolution: .env PORT (export+quoted) must be used ($(cat "$TMP/inst/data/.watchdog-state" 2>/dev/null || echo state-created))"
        stop_fake_livez
    }
fi

# ─── 7. malformed threshold falls back to 600 ───────────────────────────────
echo "== watcher: malformed tunable guard =="
rm -f "$TMP/inst/data/.watchdog-state" "$TMP/notify.log"
WATCHDOG_ABSENT_THRESHOLD_S="abc" WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
    bash "$WATCHER" "$TMP/inst" >"$TMP/tunable.out" 2>&1
grep -q "threshold 600s" "$TMP/tunable.out" && _pass || _fail "malformed threshold must fall back to 600s (got: $(grep threshold "$TMP/tunable.out" || echo none))"
[ ! -f "$TMP/notify.log" ] && _pass || _fail "malformed threshold: must not notify on a fresh miss"

# ─── 8. no-args + unresolvable default → FATAL log + exit 0 ─────────────────
# Contract (header line ~44 + commit 94388762): "Exits 0 always". Under
# set -u the no-args FATAL path used to reference $1 → bash abort exit 1.
echo "== watcher: no-args unresolvable default → FATAL logged, exit 0 =="
env -i HOME="$TMP/no-such-home" PATH="/usr/bin:/bin" \
    bash "$WATCHER" >"$TMP/noargs.out" 2>&1
RC_NOARGS=$?
[ "$RC_NOARGS" -eq 0 ] && _pass || _fail "no-args unresolvable default: must exit 0 — 'Exits 0 always' (got $RC_NOARGS)"
grep -q "FATAL: cannot resolve INSTALL_DIR" "$TMP/noargs.out" \
    && _pass || _fail "no-args unresolvable default: must emit FATAL log (got: $(cat "$TMP/noargs.out" 2>/dev/null))"
grep -Fq "cannot resolve INSTALL_DIR '$TMP/no-such-home/agents-ensemble'" "$TMP/noargs.out" \
    && _pass || _fail "no-args FATAL must name the attempted default dir (got: $(grep FATAL "$TMP/noargs.out" 2>/dev/null || echo none))"
grep -q "unbound variable" "$TMP/noargs.out" \
    && _fail "no-args: must not abort on unbound \$1 under set -u" || _pass

# ─── 9. explicit-zero tunables fall back to defaults (600 / 3) ──────────────
# "0"/"00" are all-zero digit strings — the guard must reject them like
# garbage (curl --max-time 0 means NO timeout, so a zero probe timeout
# must never reach curl).
echo "== watcher: explicit zero tunables → defaults (600 / 3) =="
rm -f "$TMP/inst/data/.watchdog-state" "$TMP/notify.log"
# 9a. WATCHDOG_ABSENT_THRESHOLD_S=0 → first-miss log must say 600s, not 0s
WATCHDOG_ABSENT_THRESHOLD_S=0 WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
    bash "$WATCHER" "$TMP/inst" >"$TMP/zerothresh.out" 2>&1
grep -q "watching (threshold 600s)" "$TMP/zerothresh.out" \
    && _pass || _fail "WATCHDOG_ABSENT_THRESHOLD_S=0 must fall back to 600s (got: $(grep threshold "$TMP/zerothresh.out" 2>/dev/null || echo none))"
# 9b. WATCHDOG_PROBE_TIMEOUT_S=0 against a STALLING server (accepts TCP,
#     never answers): fixed → probe gives up at the 3s default and records
#     the miss; buggy (0 reaches curl = no timeout) → run hangs forever.
STALL_PORT=$(( PORT + 1 ))   # .env still points here from test 6
if lsof -ti:"$STALL_PORT" >/dev/null 2>&1; then
    _fail "port $STALL_PORT busy — skipping zero-probe-timeout case"
else
    python3 -c "
import socket
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', $STALL_PORT))
s.listen(8)
conns = []
while True:
    c, _ = s.accept()
    conns.append(c)   # accept, never respond
" &
    STALL_PID=$!
    sleep 0.5   # let the staller bind
    rm -f "$TMP/inst/data/.watchdog-state"
    T0=$(date +%s)
    WATCHDOG_PROBE_TIMEOUT_S=0 WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$TMP/inst" >"$TMP/zerotimeout.out" 2>&1 &
    WPID=$!
    FINISHED=0
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        kill -0 "$WPID" 2>/dev/null || { FINISHED=1; break; }
        sleep 0.5
    done
    DUR=$(( $(date +%s) - T0 ))
    if [ "$FINISHED" -ne 1 ]; then
        kill -9 "$WPID" 2>/dev/null
        _fail "WATCHDOG_PROBE_TIMEOUT_S=0: run must not hang (0 reaching curl = no timeout)"
    else
        _pass
        [ "$DUR" -ge 2 ] \
            && _pass || _fail "WATCHDOG_PROBE_TIMEOUT_S=0: probe must use the 3s default, not instant/zero (duration ${DUR}s)"
    fi
    grep -q '^first_miss_at=[0-9]*$' "$TMP/inst/data/.watchdog-state" 2>/dev/null \
        && _pass || _fail "WATCHDOG_PROBE_TIMEOUT_S=0: giving-up-at-default must record the miss episode"
    kill -9 "$STALL_PID" 2>/dev/null
    wait "$STALL_PID" 2>/dev/null
    wait "$WPID" 2>/dev/null
fi

# ─── summary ────────────────────────────────────────────────────────────────
echo
echo "========================================"
if [ "$FAIL" -eq 0 ]; then
    echo "watchdog-watcher tests: $PASS passed, 0 failed — ALL PASS"
    exit 0
else
    echo "watchdog-watcher tests: $PASS passed, $FAIL failed"
    echo "$FAILED_TESTS"
    exit 1
fi
