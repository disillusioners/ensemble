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
#   8. EXPLICIT-ONLY INSTALL_DIR (MINOR-3/U6): absent or EMPTY argument →
#                  usage error, NONZERO exit, FATAL logged; NO HOME-based
#                  default is attempted anywhere (the former default
#                  resolved to the LIVE install — the U6 violation).
#   9. ZERO TUNABLES: explicit WATCHDOG_ABSENT_THRESHOLD_S=0 / PROBE_TIMEOUT_S=0
#                  fall back to 600 / 3 like garbage (curl max-time 0 = no
#                  timeout, so zero must never reach curl).
#  10. B4/ADR-025(b) BURST-ABORT: a real-shape .launcher-state abort marker
#                  (last_exit=1, crash_count > the BUDGET_MAX_CRASHES pin —
#                  NIT-6, coupled to launcher.sh) alone → one notify per
#                  abort occurrence (latch by count; deeper burst re-notifies;
#                  cleared marker re-arms); healthy /livez does not gate it.
#  11. B4/ADR-025(b) JOURNAL: real-shape releases/state.json halt /
#                  sweep_rollback events alone → count-latched notify citing
#                  the latest detail; plain sweep/commit stay quiet (R3.4);
#                  absent journal silently resets the latch.
#  12. BEST-EFFORT: JSON-invalid journals and empty/garbage state files are
#                  skipped silently (exit 0, no notify, no alert state) —
#                  partial-deploy survival.
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
# Hermeticity (F2 sweep): this suite must be green even when an ambient
# ENSEMBLE_LAUNCHER_STATE_FILE is exported by the caller (the Stage-2
# override). Unset it here so every cell starts from a clean slate;
# cells that TEST the inherited-env override set it inline for that one
# watcher invocation (13a, 13c, 14.e, 14.f, 14.h, ...).
unset ENSEMBLE_LAUNCHER_STATE_FILE

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

# ─── 8. MINOR-3 (U6): INSTALL_DIR explicit-only — absent/empty → usage
# refusal, NONZERO exit. The former contract here ("no-args unresolvable
# default → FATAL logged, exit 0") pinned the DEFAULT itself — the exact
# U6 violation: the fallback resolved to the LIVE install, so a watcher
# installed without configuration silently watched live. Now there is NO
# default anywhere: missing/empty arg → usage error; nothing HOME-based
# is resolved or named, and set -u never sees an unbound $1.
echo "== watcher: absent/empty INSTALL_DIR → usage refusal (nonzero) =="
env -i HOME="$TMP/no-such-home" PATH="/usr/bin:/bin" \
    bash "$WATCHER" >"$TMP/noargs.out" 2>&1
RC_NOARGS=$?
[ "$RC_NOARGS" -ne 0 ] && _pass || _fail "no-args: must exit NONZERO (explicit-only INSTALL_DIR usage error; got $RC_NOARGS)"
grep -q "FATAL: INSTALL_DIR argument required" "$TMP/noargs.out" \
    && _pass || _fail "no-args: must emit the FATAL usage line (got: $(cat "$TMP/noargs.out" 2>/dev/null))"
grep -q "Usage: bash scripts/watchdog-watcher.sh INSTALL_DIR" "$TMP/noargs.out" \
    && _pass || _fail "no-args: must print usage"
grep -Fq "agents-ensemble" "$TMP/noargs.out" \
    && _fail "no-args: must NOT name/attempt any HOME-based default dir (U6)" || _pass
grep -q "unbound variable" "$TMP/noargs.out" \
    && _fail "no-args: must not abort on unbound \$1 under set -u" || _pass
# empty-string INSTALL_DIR → the same refusal (an empty dir is no dir)
env -i HOME="$TMP/no-such-home" PATH="/usr/bin:/bin" \
    bash "$WATCHER" "" >"$TMP/emptyarg.out" 2>&1
RC_EMPTY=$?
[ "$RC_EMPTY" -ne 0 ] && _pass || _fail "empty INSTALL_DIR: must exit NONZERO (got $RC_EMPTY)"
grep -q "FATAL: INSTALL_DIR argument required" "$TMP/emptyarg.out" \
    && _pass || _fail "empty INSTALL_DIR: must emit the FATAL usage line"

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

# ─── 10. P2.3 B4 / T8b (ADR-025(b)): daemon-independent alert sources ───────
# Assumption-#3 closure: the extension detects a burst-abort scenario and a
# halt scenario from REAL-format fixture files ALONE — the exact
# .launcher-state shape launcher.sh write_state emits (abort action:
# last_exit=1, crash_count>5, last_backoff=0, then exit 1) and the exact
# compact releases/state.json shape lib.sh journal_write emits
# (journal_init schema + journal_history_append entries). No daemon, no
# HTTP for the DETECTION itself (a fake /livez is up in 10a purely to
# prove the alert sources fire while the daemon looks HEALTHY).
echo "== watcher: burst-abort from real .launcher-state alone =="
INST2="$TMP/inst2"
mkdir -p "$INST2/data"
printf 'PORT=%s\n' "$PORT" > "$INST2/.env"
start_fake_livez && {
    # 10a. real-shape abort marker (crash #6 > budget 5) → exactly one
    #      notify, latch abort_seen=6, healthy /livez → NO absent-episode
    #      state file (the source is daemon-independent).
    printf 'last_exit=1\ncrash_count=6\nwindow_start=%s\nlast_backoff=0\nnotified_75=0\nlast_uptime=3\n' \
        "$(( $(date +%s) - 60 ))" > "$INST2/.launcher-state"
    rm -f "$TMP/notify.log"
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$INST2" >"$TMP/abort1.out" 2>&1
    [ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "1" ] && _pass || _fail "burst-abort: exactly one notification"
    grep -q "WATCHDOG\[launcher-abort\]" "$TMP/abort1.out" && _pass || _fail "burst-abort: must log WATCHDOG[launcher-abort]"
    grep -q '^abort_seen=6$' "$INST2/data/.watchdog-alerts-state" 2>/dev/null && _pass || _fail "burst-abort: latch abort_seen=6 ($(cat "$INST2/data/.watchdog-alerts-state" 2>/dev/null || echo none))"
    [ ! -f "$INST2/data/.watchdog-state" ] && _pass || _fail "burst-abort: healthy /livez must not open an absent episode"
    # 10b. same marker, next 300s cycle → latched, NO second notify.
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$INST2" >"$TMP/abort2.out" 2>&1
    [ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "1" ] && _pass || _fail "burst-abort latch: no re-notify while marker unchanged"
    # 10c. deeper burst (crash #7) → a NEW occurrence notifies again.
    sed 's/^crash_count=6$/crash_count=7/' "$INST2/.launcher-state" > "$INST2/.launcher-state.new" && mv -f "$INST2/.launcher-state.new" "$INST2/.launcher-state"
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$INST2" >"$TMP/abort3.out" 2>&1
    [ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "2" ] && _pass || _fail "deeper burst (count 7): must notify the new occurrence"
    grep -q '^abort_seen=7$' "$INST2/data/.watchdog-alerts-state" 2>/dev/null && _pass || _fail "deeper burst: latch abort_seen=7"
    # 10d. marker cleared (clean exit, last_exit=0) → re-arm, silent.
    sed 's/^last_exit=1$/last_exit=0/' "$INST2/.launcher-state" > "$INST2/.launcher-state.new" && mv -f "$INST2/.launcher-state.new" "$INST2/.launcher-state"
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$INST2" >"$TMP/abort4.out" 2>&1
    [ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "2" ] && _pass || _fail "marker cleared: no notify on re-arm"
    grep -q '^abort_seen=0$' "$INST2/data/.watchdog-alerts-state" 2>/dev/null && _pass || _fail "marker cleared: latch re-armed to 0"
    grep -q "re-armed" "$TMP/abort4.out" && _pass || _fail "marker cleared: must log the re-arm"
    # 10e. re-armed → a fresh abort occurrence notifies again.
    printf 'last_exit=1\ncrash_count=6\nwindow_start=%s\nlast_backoff=0\nnotified_75=0\nlast_uptime=3\n' \
        "$(( $(date +%s) - 60 ))" > "$INST2/.launcher-state"
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$INST2" >"$TMP/abort5.out" 2>&1
    [ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "3" ] && _pass || _fail "re-armed latch: fresh abort must notify"
    # 10f. negative: garbage state values never match (crash_count non-numeric).
    printf 'last_exit=1\ncrash_count=garbage\nwindow_start=0\nlast_backoff=0\nnotified_75=0\nlast_uptime=3\n' \
        > "$INST2/.launcher-state"
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$INST2" >"$TMP/abort6.out" 2>&1
    [ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "3" ] && _pass || _fail "garbage crash_count must not notify (and must not crash)"
    stop_fake_livez
}

echo "== watcher: halt/sweep events from real releases/state.json alone =="
INST3="$TMP/inst3"
mkdir -p "$INST3/data" "$INST3/releases"
printf 'PORT=%s\n' "$PORT" > "$INST3/.env"
start_fake_livez && {
    # 11a. real-shape journal (lib.sh journal_write output: compact, single
    #      line, trailing newline) with ONE halt event → one notify citing
    #      the halt detail, latch journal_seen=1.
    printf '%s\n' '{"current":"v1","previous":"v0","in_flight":null,"rollback_window_count":{"24h":3,"window_start":"2026-08-23T09:00:00Z"},"cooldown_until":null,"quarantined":[],"history":[{"ts":"2026-08-23T12:00:00Z","event":"commit","detail":"promote v1"},{"ts":"2026-08-23T12:05:00Z","event":"halt","detail":"rollback cap 3/24h reached (count=3) — halt-for-human; promotes refused until the window resets"}]}' \
        > "$INST3/releases/state.json"
    rm -f "$TMP/notify.log"
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$INST3" >"$TMP/jrn1.out" 2>&1
    [ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "1" ] && _pass || _fail "journal halt: exactly one notification"
    grep -q "WATCHDOG\[journal\]" "$TMP/jrn1.out" && _pass || _fail "journal halt: must log WATCHDOG[journal]"
    grep -q "halt_for_human" "$TMP/jrn1.out" && _pass || _fail "journal halt: log must cite the halt class"
    grep -q "rollback cap 3/24h reached" "$TMP/jrn1.out" && _pass || _fail "journal halt: log must cite the latest detail"
    grep -q '^journal_seen=1$' "$INST3/data/.watchdog-alerts-state" 2>/dev/null && _pass || _fail "journal halt: latch journal_seen=1"
    [ ! -f "$INST3/data/.watchdog-state" ] && _pass || _fail "journal halt: healthy /livez must not open an absent episode"
    # 11b. next cycle, unchanged journal → latched, no re-notify.
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$INST3" >"$TMP/jrn2.out" 2>&1
    [ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "1" ] && _pass || _fail "journal latch: no re-notify while count unchanged"
    # 11c. a SECOND halt appended (real writer keeps history additive) →
    #      the count grows → notify the new event.
    printf '%s\n' '{"current":"v1","previous":"v0","in_flight":null,"rollback_window_count":{"24h":3,"window_start":"2026-08-23T09:00:00Z"},"cooldown_until":null,"quarantined":[],"history":[{"ts":"2026-08-23T12:00:00Z","event":"commit","detail":"promote v1"},{"ts":"2026-08-23T12:05:00Z","event":"halt","detail":"rollback cap 3/24h reached (count=3) — halt-for-human; promotes refused until the window resets"},{"ts":"2026-08-23T12:30:00Z","event":"sweep_rollback","detail":"adopt: orphaned flipped promote txn rolled back to v0 at promote preflight"}]}' \
        > "$INST3/releases/state.json"
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$INST3" >"$TMP/jrn3.out" 2>&1
    [ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "2" ] && _pass || _fail "journal growth (halt+sweep_rollback=2): must notify the new event"
    grep -q '^journal_seen=2$' "$INST3/data/.watchdog-alerts-state" 2>/dev/null && _pass || _fail "journal growth: latch journal_seen=2"
    # 11d. journal REMOVED (fresh/partial deploy) → silent latch reset.
    rm "$INST3/releases/state.json"
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$INST3" >"$TMP/jrn4.out" 2>&1
    [ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "2" ] && _pass || _fail "journal absent: no notify on reset"
    grep -q '^journal_seen=0$' "$INST3/data/.watchdog-alerts-state" 2>/dev/null && _pass || _fail "journal absent: latch reset to 0"
    # 11e. plain sweep-clear and commit events NEVER alert (R3.4 mirror).
    printf '%s\n' '{"current":"v1","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[{"ts":"2026-08-23T13:00:00Z","event":"sweep","detail":"cleared stale pre-flip txn"},{"ts":"2026-08-23T13:01:00Z","event":"commit","detail":"promote v1"}]}' \
        > "$INST3/releases/state.json"
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
        bash "$WATCHER" "$INST3" >"$TMP/jrn5.out" 2>&1
    [ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "2" ] && _pass || _fail "plain sweep/commit must stay quiet (R3.4)"
    stop_fake_livez
}

echo "== watcher: best-effort survival (partial-deploy / invalid files) =="
INST4="$TMP/inst4"
mkdir -p "$INST4/data" "$INST4/releases"
printf 'PORT=%s\n' "$PORT" > "$INST4/.env"
# 12a. JSON-invalid journals (torn mid-write, leading garbage) are skipped
#     silently — exit 0, no notify, no crash, no latch churn.
printf '%s' '{"current":null,"previous":null,"in_flight":' > "$INST4/releases/state.json"
printf 'this is not json at all' > "$INST4/releases/state2.json"
WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
    bash "$WATCHER" "$INST4" >"$TMP/best1.out" 2>&1
RC12a=$?
[ "$RC12a" -eq 0 ] && _pass || _fail "invalid journal: watcher must still exit 0 (got $RC12a)"
[ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "2" ] && _pass || _fail "invalid journal: must not notify"
[ ! -f "$INST4/data/.watchdog-alerts-state" ] && _pass || _fail "invalid journal: must not create alert state"
# 12b. EMPTY .launcher-state / releases dir only — partially-deployed env:
#     silent, no state, exit 0.
rm -f "$INST4/releases/state.json" "$INST4/releases/state2.json"
: > "$INST4/.launcher-state"
WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
    bash "$WATCHER" "$INST4" >"$TMP/best2.out" 2>&1
RC12b=$?
[ "$RC12b" -eq 0 ] && _pass || _fail "empty launcher state: watcher must still exit 0 (got $RC12b)"
[ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "2" ] && _pass || _fail "empty launcher state: must not notify"
[ ! -f "$INST4/data/.watchdog-alerts-state" ] && _pass || _fail "empty launcher state: must not create alert state"

# ─── 13. Launcher-state override (design §v relocation) ────────────────────
# ENSEMBLE_LAUNCHER_STATE_FILE: inherited env → INSTALL_DIR/.env parse →
# classic default. The burst-abort marker (a) must be found at the
# RELOCATED location; the classic path stays byte-identical when the
# override is absent everywhere. Each case seeds the abort marker ONLY at
# the location the resolution is expected to pick, so the notify (or its
# absence) is the discrimination — a wrong resolution finds no marker.
ABORT_MARKER_BODY() {
    printf 'last_exit=1\ncrash_count=6\nwindow_start=%s\nlast_backoff=0\nnotified_75=0\nlast_uptime=3\n' \
        "$(( $(date +%s) - 60 ))"
}

echo "== watcher: ENSEMBLE_LAUNCHER_STATE_FILE override (env / .env / default) =="

# 13a. INHERITED ENV override honored.
INST5="$TMP/inst5"
mkdir -p "$INST5/data"
printf 'PORT=%s\n' "$PORT" > "$INST5/.env"
mkdir -p "$TMP/reloc-env"
ABORT_MARKER_BODY > "$TMP/reloc-env/.launcher-state"
rm -f "$TMP/notify.log"
ENSEMBLE_LAUNCHER_STATE_FILE="$TMP/reloc-env/.launcher-state" \
WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
    bash "$WATCHER" "$INST5" >"$TMP/ovr1.out" 2>&1
[ $? -eq 0 ] && _pass || _fail "13a env override: watcher exits 0"
[ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "1" ] \
    && _pass || _fail "13a env override: abort marker found at the RELOCATED path (expected 1 notify)"
grep -q "WATCHDOG\[launcher-abort\]" "$TMP/ovr1.out" \
    && _pass || _fail "13a env override: must log WATCHDOG[launcher-abort]"
grep -q '^abort_seen=6$' "$INST5/data/.watchdog-alerts-state" 2>/dev/null \
    && _pass || _fail "13a env override: latch records the relocated marker's count"

# 13b. .ENV PARSE honored (double-quoted value — same tolerant shape as the
# PORT parser), with the inherited env scrubbed.
INST6="$TMP/inst6"
mkdir -p "$INST6/data" "$TMP/reloc-envfile"
printf 'PORT=%s\n' "$PORT" > "$INST6/.env"
printf 'ENSEMBLE_LAUNCHER_STATE_FILE="%s"\n' "$TMP/reloc-envfile/.launcher-state" >> "$INST6/.env"
ABORT_MARKER_BODY > "$TMP/reloc-envfile/.launcher-state"
rm -f "$TMP/notify.log"
env -u ENSEMBLE_LAUNCHER_STATE_FILE \
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
    bash "$WATCHER" "$INST6" >"$TMP/ovr2.out" 2>&1
[ $? -eq 0 ] && _pass || _fail "13b .env override: watcher exits 0"
[ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "1" ] \
    && _pass || _fail "13b .env override: quoted .env value parsed and honored (expected 1 notify)"

# 13c. PRECEDENCE (F-07): the .env value WINS over the inherited env —
# the launcher's effective order is .env (last occurrence wins) → inherited
# env → default, and the watcher must resolve the SAME file (the old order
# was INVERTED here, so the two sides could watch DIFFERENT files). The
# inherited target holds an EMPTY file (no marker) — a notify can only
# mean the .env path was read.
INST7="$TMP/inst7"
mkdir -p "$INST7/data" "$TMP/reloc-env2" "$TMP/reloc-envfile2"
printf 'PORT=%s\n' "$PORT" > "$INST7/.env"
printf 'ENSEMBLE_LAUNCHER_STATE_FILE="%s"\n' "$TMP/reloc-envfile2/.launcher-state" >> "$INST7/.env"
: > "$TMP/reloc-env2/.launcher-state"               # inherited target: NO marker
ABORT_MARKER_BODY > "$TMP/reloc-envfile2/.launcher-state"   # .env target: marker
rm -f "$TMP/notify.log"
ENSEMBLE_LAUNCHER_STATE_FILE="$TMP/reloc-env2/.launcher-state" \
WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
    bash "$WATCHER" "$INST7" >"$TMP/ovr3.out" 2>&1
[ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "1" ] \
    && _pass || _fail "13c precedence: the .env value beats the inherited env (expected 1 notify from the .env path)"

# 13d. DEFAULT UNCHANGED: no override anywhere → the CLASSIC
# $INSTALL_DIR/.launcher-state is still the marker source (the VM/legacy
# shape — byte-identical behavior; section 10 exercises the same path).
INST8="$TMP/inst8"
mkdir -p "$INST8/data"
printf 'PORT=%s\n' "$PORT" > "$INST8/.env"
ABORT_MARKER_BODY > "$INST8/.launcher-state"
rm -f "$TMP/notify.log"
env -u ENSEMBLE_LAUNCHER_STATE_FILE \
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
    bash "$WATCHER" "$INST8" >"$TMP/ovr4.out" 2>&1
[ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "1" ] \
    && _pass || _fail "13d default: classic .launcher-state still honored (expected 1 notify)"

# 13e. SOURCE PINS: the FUNCTIONAL default lives at exactly ONE site (the
# resolver's printf fallback — comments may document the path), the
# resolver reads the override var, and the consumer uses the resolved
# LAUNCHER_STATE_FILE (no re-hardcode).
if [ "$(grep -c 'printf .%s. "\$INSTALL_DIR/\.launcher-state"' "$WATCHER")" = "1" ]; then
    _pass
else
    _fail "13e single functional classic-path default (the resolver fallback; found $(grep -c 'printf .%s. "\$INSTALL_DIR/\.launcher-state"' "$WATCHER"))"
fi
grep -q '_resolve_launcher_state_file' "$WATCHER" \
    && _pass || _fail "13e resolver function present"
grep -q 'local f="\$LAUNCHER_STATE_FILE"' "$WATCHER" \
    && _pass || _fail "13e check_burst_abort consumes the resolved path (no re-hardcode)"

# ─── 14. F-06 / F-07: value-resolution + launcher-parity rows ──────────────
# The watcher's resolution must EXACTLY match the launcher's:
#   .env value (LAST occurrence wins) → inherited env → the classic default.
# The launcher side is proved by a REAL sandboxed launcher boot (the stub
# child dumps its inherited environment — the launcher EXPORTS the resolved
# value to every child), so BOTH sides are compared against the SAME .env
# fixture. Nothing here touches the live install, the live daemon or the
# real ~/.nvm: every fixture lives under $TMP and every boot is sandboxed.
LAUNCHER="$REPO_ROOT/launcher.sh"

# launcher_resolved_state_file <install-dir> <env-file> [inherited] — the
# launcher's EFFECTIVE ENSEMBLE_LAUNCHER_STATE_FILE (real sandboxed boot).
launcher_resolved_state_file() {
    local inst="$1" envf="$2" inh="${3-}" dump="$TMP/launcher-boot-env.txt"
    rm -f "$dump"
    mkdir -p "$inst/data"
    cat > "$inst/ensemble-prod" <<EOF
#!/bin/sh
env > "$dump"
exit 0
EOF
    chmod +x "$inst/ensemble-prod"
    cp "$envf" "$inst/.env"
    if [ -n "$inh" ]; then
        ENSEMBLE_LAUNCHER_STATE_FILE="$inh" \
        ENSEMBLE_INSTALL_DIR="$inst" \
            bash "$LAUNCHER" >/dev/null 2>"$TMP/launcher-boot.err"
    else
        env -u ENSEMBLE_LAUNCHER_STATE_FILE \
        ENSEMBLE_INSTALL_DIR="$inst" \
            bash "$LAUNCHER" >/dev/null 2>"$TMP/launcher-boot.err"
    fi
    sed -n 's/^ENSEMBLE_LAUNCHER_STATE_FILE=//p' "$dump" 2>/dev/null | head -1
}

# watchdog_resolved_state_file <install-dir> <env-file> [inherited] — the
# watcher's resolved path, read from its startup resolution log line.
watchdog_resolved_state_file() {
    local inst="$1" envf="$2" inh="${3-}"
    mkdir -p "$inst/data"
    cp "$envf" "$inst/.env"
    if [ -n "$inh" ]; then
        ENSEMBLE_LAUNCHER_STATE_FILE="$inh" \
            bash "$WATCHER" "$inst" >"$TMP/wd-res.out" 2>&1
    else
        env -u ENSEMBLE_LAUNCHER_STATE_FILE \
            bash "$WATCHER" "$inst" >"$TMP/wd-res.out" 2>&1
    fi
    sed -n 's/^.*launcher-state: .* resolved=//p' "$TMP/wd-res.out" | head -1
}

# parity_row <name> <env-file> [inherited] <expected-path> — the watcher
# AND the launcher must resolve the SAME path.
parity_row() {
    local name="$1" envf="$2" inh="${3-}" want="$4" got
    got="$(watchdog_resolved_state_file "$TMP/parity-wd" "$envf" "$inh")"
    [ "$got" = "$want" ] \
        && _pass || _fail "$name: watcher resolves $want" "$want" "$got"
    got="$(launcher_resolved_state_file "$TMP/parity-boot" "$envf" "$inh")"
    [ "$got" = "$want" ] \
        && _pass || _fail "$name: launcher resolves $want" "$want" "$got"
}

echo "== watcher: F-06 value resolution + launcher parity =="

# 14.a TRAILING WHITESPACE: both sides resolve the SAME trimmed path (the
# old watcher kept the trailing blanks → a non-existent path → the
# burst-abort alert was LOST silently).
PA="$TMP/parity-a"
mkdir -p "$PA"
printf 'PORT=%s\nENSEMBLE_LAUNCHER_STATE_FILE=%s   \n' "$PORT" "$PA/state" > "$PA/env"
parity_row "14.a trailing whitespace" "$PA/env" "" "$PA/state"

# 14.b an UNQUOTED inline comment suffix is stripped by BOTH sides.
PB="$TMP/parity-b"
mkdir -p "$PB"
printf 'PORT=%s\nENSEMBLE_LAUNCHER_STATE_FILE=%s   # the relocated state\n' \
    "$PORT" "$PB/state" > "$PB/env"
parity_row "14.b inline comment" "$PB/env" "" "$PB/state"

# 14.c a '#' NOT preceded by whitespace is PART of the value (both sides).
PC="$TMP/parity-c"
mkdir -p "$PC"
printf 'PORT=%s\nENSEMBLE_LAUNCHER_STATE_FILE=%s\n' "$PORT" "$PC/pa#ss" > "$PC/env"
parity_row "14.c '#' without whitespace kept" "$PC/env" "" "$PC/pa#ss"

# 14.d a '#' INSIDE a quoted value is NOT a comment (both sides).
PD="$TMP/parity-d"
mkdir -p "$PD/a # b"
printf 'PORT=%s\nENSEMBLE_LAUNCHER_STATE_FILE="%s"\n' "$PORT" "$PD/a # b" > "$PD/env"
parity_row "14.d '#' inside quotes kept" "$PD/env" "" "$PD/a # b"

# 14.e PRECEDENCE (F-07): the .env value beats the inherited env on BOTH
# sides (the old watcher order was INVERTED → the two sides could watch
# DIFFERENT files).
PE="$TMP/parity-e"
mkdir -p "$PE"
printf 'PORT=%s\nENSEMBLE_LAUNCHER_STATE_FILE=%s\n' "$PORT" "$PE/from-dotenv" > "$PE/env"
parity_row "14.e .env beats the inherited env" "$PE/env" "$PE/from-env" "$PE/from-dotenv"

# 14.f DUPLICATE KEY (F-07 / residual e): the LAST occurrence wins on BOTH
# sides (the old watcher took the FIRST match via `head -1`).
PF="$TMP/parity-f"
mkdir -p "$PF"
printf 'PORT=%s\nENSEMBLE_LAUNCHER_STATE_FILE=%s\n' "$PORT" "$PF/first" > "$PF/env"
printf 'ENSEMBLE_LAUNCHER_STATE_FILE=%s\n' "$PF/second" >> "$PF/env"
parity_row "14.f duplicate key: LAST wins" "$PF/env" "$PF/from-env" "$PF/second"

# 14.g BEHAVIOURAL (F-06): the trailing-whitespace .env value still finds
# the burst-abort marker (before the fix the watcher resolved a
# non-existent path → the alert was lost with NO operator misconfiguration).
PG="$TMP/parity-g"
mkdir -p "$PG/inst/data" "$PG/state-dir"
printf 'PORT=%s\nENSEMBLE_LAUNCHER_STATE_FILE=%s   \n' \
    "$PORT" "$PG/state-dir/.launcher-state" > "$PG/inst/.env"
ABORT_MARKER_BODY > "$PG/state-dir/.launcher-state"
rm -f "$TMP/notify.log"
env -u ENSEMBLE_LAUNCHER_STATE_FILE \
    WATCHDOG_NOTIFY_CMD="echo notified >> $TMP/notify.log" \
    bash "$WATCHER" "$PG/inst" >"$TMP/ovr-g.out" 2>&1
[ "$(wc -l < "$TMP/notify.log" 2>/dev/null | tr -d ' ')" = "1" ] \
    && _pass || _fail "14.g behavioural: the trimmed .env value still finds the abort marker (expected 1 notify)"

# 14.h the startup log names the inherited-env candidate, the .env candidate
# AND the resolved path (drift visibility, F-07).
PH="$TMP/parity-h"
mkdir -p "$PH/inst/data"
printf 'PORT=%s\nENSEMBLE_LAUNCHER_STATE_FILE=%s\n' "$PORT" "$PH/from-dotenv" \
    > "$PH/inst/.env"
ENSEMBLE_LAUNCHER_STATE_FILE="$PH/from-env" \
    bash "$WATCHER" "$PH/inst" >"$TMP/ovr-h.out" 2>&1
grep -q "launcher-state: inherited=$PH/from-env " "$TMP/ovr-h.out" \
    && _pass || _fail "14.h startup log names the inherited-env candidate" \
    "inherited=$PH/from-env" "$(cat "$TMP/ovr-h.out" 2>/dev/null | head -3)"
grep -q "dotenv=$PH/from-dotenv " "$TMP/ovr-h.out" \
    && _pass || _fail "14.h startup log names the .env candidate" \
    "dotenv=$PH/from-dotenv" "$(cat "$TMP/ovr-h.out" 2>/dev/null | head -3)"
grep -q "resolved=$PH/from-dotenv" "$TMP/ovr-h.out" \
    && _pass || _fail "14.h startup log names the resolved path" \
    "resolved=$PH/from-dotenv" "$(cat "$TMP/ovr-h.out" 2>/dev/null | head -3)"

# 14.j the VALUE-level trim (whitespace right after '=') — both sides.
PJ="$TMP/parity-j"
mkdir -p "$PJ"
printf 'PORT=%s\nENSEMBLE_LAUNCHER_STATE_FILE=   %s   \n' "$PORT" "$PJ/state" > "$PJ/env"
parity_row "14.j whitespace around the value trimmed" "$PJ/env" "" "$PJ/state"

# 14.i the NO-override case stays SILENT (the "healthy → silent" contract):
# with no candidate anywhere the resolution IS the default and there is
# nothing to disclose.
PI="$TMP/parity-i"
mkdir -p "$PI/inst/data"
printf 'PORT=%s\n' "$PORT" > "$PI/inst/.env"
env -u ENSEMBLE_LAUNCHER_STATE_FILE \
    bash "$WATCHER" "$PI/inst" >"$TMP/ovr-i.out" 2>&1
if grep -q 'launcher-state:' "$TMP/ovr-i.out"; then
    _fail "14.i no override anywhere → the resolution stays unlogged" \
        "no launcher-state line" "$(cat "$TMP/ovr-i.out" 2>/dev/null | head -3)"
else
    _pass
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
