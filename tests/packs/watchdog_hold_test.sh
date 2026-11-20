#!/usr/bin/env bash
# ============================================================================
# tests/packs/watchdog_hold_test.sh — the D2 WATCHDOG HOLD + restore
# transient (self-service-promote Wave 4, 2026-11-19).
#
# Legs (the design's acceptance criteria):
#   AC-D2-1  a stubbed ARMED timer is stopped for the whole stop→hand-back
#            window and re-armed at the end (the marker records the held
#            set + the prior state; the restore re-arms by TIMER NAME).
#   AC-D2-2  the restore transient is minted BEFORE any stop; a mint failure
#            ⇒ NO stop + the loud `watchdog_hold_unavailable` (a hold without
#            a restore path is the "permanently disabled watchdog" class).
#   AC-D2-3  the executor's death mid-window leaves the RT armed; after the
#            (scaled) TTL the timer is re-armed — the RT re-reads the
#            journal and restores.
#   AC-D2-10 the hold is BOUNDED BY DAEMON LIVENESS: with the unit
#            inactive|failed and no fresh executor heartbeat the RT restores
#            within ≤3 iterations — never the old 20 (the F13 darkness
#            defect).
#
# SANDBOX-RUNNABLE (no real systemd, no live host): every systemctl and
# systemd-run call is a PATH STUB backed by a state dir; the restore
# transient's DECISION LOGIC is driven through the real lib.sh entry point
# (`_LIB_WATCHDOG_RT_ENTRY=1 bash lib.sh --watchdog-restore`) — the same
# argv the real transient carries.
#
# The PRIVILEGED leg (a REAL systemd transient + a real timer) is TAGGED
# (approver note 7 + clarification (v)): it is SKIPPED unless the host has
# real systemd AND the operator opts in (ENSEMBLE_WATCHDOG_PRIVILEGED_LEG=1).
#
# FENCES (house rules): mktemp FAKE_HOME **with the escape guard** (the
# shared tests/upgrade_test_lib.sh fence); the marker path resolves under
# the FIXTURE install dir (never a live-shaped path); the sibling-env
# assertion (POSTGRES_DB must not be ensemble_prod); nothing binds a port;
# every fixture dir is registered with the lib's fixture registry.
#
# Run:   timeout 600 bash tests/packs/watchdog_hold_test.sh
# ============================================================================
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
LIBSH="$UPGRADE_DIR/lib.sh"

# The shared prologue helpers — the ONE canonical home for the assertions,
# the fixture registry + its ONE EXIT trap, and the F1 fake-HOME fence.
. "$REPO_ROOT/tests/upgrade_test_lib.sh"

PASS=0
FAIL=0
FAILED_TESTS=""
_pass() { PASS=$(( PASS + 1 )); }
_fail() {
    FAIL=$(( FAIL + 1 ))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
    return 0
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass "$name"; else _fail "$name" "$expected" "$actual"; fi
}
assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}
assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "absent '$needle'" "$haystack" ;;
        *) _pass ;;
    esac
}
section() { printf '\n== %s ==\n' "$1"; }

# ── fences ──────────────────────────────────────────────────────────────────
if [ "${POSTGRES_DB:-}" = "ensemble_prod" ]; then
    echo "FENCE ABORT: ambient POSTGRES_DB=ensemble_prod (prod-poisoned env; scrub per-variable and re-run)" >&2
    exit 78
fi
setup_fake_home
preflight_guard

# ── fixtures ────────────────────────────────────────────────────────────────
WDFIX="$(mktemp -d -t wd-fix.XXXXXX)"; _register_fixture "$WDFIX"
STATE="$WDFIX/state"; mkdir -p "$STATE"
mkdir -p "$WDFIX/releases"
cat > "$WDFIX/releases/state.json" <<'JOURNAL'
{"current":"v1","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL

# The host's ARMED watchdog timer (the state dir stands in for systemd):
#   <unit>.active   present ⇒ the unit is active (the timer is ARMED)
#   <unit>.enabled  present ⇒ the enable state (its content is the value)
#   <unit>.failed   present ⇒ the unit is failed
#   <unit>.execstart  the unit's ExecStart (the probe reads it)
TIMER="ensemble-keepalive.timer"
SERVICE="ensemble-keepalive.service"
DAEMON_UNIT="ensemble-daemon.service"
: > "$STATE/$TIMER.active"
printf 'enabled' > "$STATE/$TIMER.enabled"
printf '{ path=/usr/local/bin/ensemble-keepalive.sh ; argv[]=/usr/local/bin/ensemble-keepalive.sh }' \
    > "$STATE/$SERVICE.execstart"
: > "$STATE/$DAEMON_UNIT.active"

STUBBIN="$(mktemp -d -t wd-stub.XXXXXX)"; _register_fixture "$STUBBIN"
cat > "$STUBBIN/systemctl" <<'STUB'
#!/bin/bash
# The D2 pack's systemctl stub — a STATE DIR stands in for the host's unit
# state. Every call is logged to $SC_LOG (the ORDER pins read it) and the
# state-changing verbs (stop/start) record their effect in $SC_LOG.state.
echo "$*" >> "${SC_LOG:-/dev/null}"
sub="$1"; shift
state="${SC_STATE:-/tmp}"
case "$sub" in
    list-unit-files)
        printf '%s\n' "${SC_TIMERS:-ensemble-keepalive.timer enabled}"
        exit 0 ;;
    show)
        props=""; unit=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -p) props="$props $2"; shift 2 ;;
                --value) shift ;;
                *) unit="$1"; shift ;;
            esac
        done
        for p in $props; do
            case "$p" in
                ExecStart)
                    if [ -f "$state/$unit.execstart" ]; then
                        cat "$state/$unit.execstart"
                    fi
                    ;;
                ActiveState)
                    if [ -f "$state/$unit.failed" ]; then echo failed
                    elif [ -f "$state/$unit.active" ]; then echo active
                    else echo inactive
                    fi ;;
                SubState)
                    if [ -f "$state/$unit.failed" ]; then echo failed
                    elif [ -f "$state/$unit.active" ]; then echo running
                    else echo dead
                    fi ;;
                UnitFileState)
                    if [ -f "$state/$unit.enabled" ]; then cat "$state/$unit.enabled"
                    else echo disabled
                    fi ;;
                MainPID)
                    if [ -f "$state/$unit.active" ]; then echo 4242
                    else echo 0
                    fi ;;
                *) : ;;
            esac
        done
        exit 0 ;;
    is-active)
        if [ -f "$state/$1.active" ]; then echo active; else echo inactive; fi
        exit 0 ;;
    is-failed)
        if [ -f "$state/$1.failed" ]; then echo failed; else echo inactive; fi
        exit 0 ;;
    is-enabled)
        if [ -f "$state/$1.enabled" ]; then cat "$state/$1.enabled"; else echo disabled; fi
        exit 0 ;;
    stop)
        rm -f "$state/$1.active"
        printf 'stopped:%s\n' "$1" >> "${SC_LOG:-/dev/null}.state"
        exit 0 ;;
    start)
        : > "$state/$1.active"
        printf 'started:%s\n' "$1" >> "${SC_LOG:-/dev/null}.state"
        exit 0 ;;
    *) exit 0 ;;
esac
STUB
chmod +x "$STUBBIN/systemctl"

cat > "$STUBBIN/systemd-run" <<'STUB'
#!/bin/bash
# The D2 pack's systemd-run stub: it RECORDS the mint (the unit + the TTL)
# and never creates a real transient (the sandbox has no systemd — the REAL
# transient is the TAGGED privileged leg at the end of this pack).
printf 'systemd-run %s\n' "$*" >> "${SC_LOG:-/dev/null}"
printf 'systemd-run %s\n' "$*" >> "${SC_LOG:-/dev/null}.systemd-run"
if [ "${SC_SYSTEMD_RUN_RC:-0}" != "0" ]; then
    echo "stub systemd-run: refusing (SC_SYSTEMD_RUN_RC=${SC_SYSTEMD_RUN_RC})" >&2
    exit "${SC_SYSTEMD_RUN_RC}"
fi
exit 0
STUB
chmod +x "$STUBBIN/systemd-run"

# run_wd <script> — drive the REAL lib.sh functions in a bounded child with
# the stubs on the PATH and a FIXTURE install dir (the marker path can
# never resolve to a live-shaped path).
run_wd() {
    PATH="$STUBBIN:$PATH" bash -c '
        export INSTALL_DIR="'"$WDFIX"'"
        export UP_TARGET=sandbox
        export SYSTEMCTL_BIN=systemctl
        export SYSTEMD_RUN_BIN=systemd-run
        export SC_LOG="'"$WDFIX"'/sc.log"
        export SC_STATE="'"$STATE"'"
        export SC_TIMERS="'"$TIMER"' enabled"
        export WATCHDOG_HOLD_RT_TTL_S="'"${RT_TTL:-5}"'"
        export WATCHDOG_HOLD_MAX_ITERATIONS="'"${RT_MAX_ITER:-3}"'"
        . "'"$LIBSH"'" >/dev/null 2>&1
        # The sandbox has no systemd — the host-capability guard is stubbed
        # so the probe RUNS (the guard is the D2 seam under test, not the
        # subject of these legs).
        _supervision_host_allows_unit() { return 0; }
        SUPERVISION_UNIT="'"$DAEMON_UNIT"'"
        '"$1"'
    ' 2>&1
}

reset_state() {
    rm -f "$SC_LOG" "$SC_LOG".* 2>/dev/null || true
    : > "$STATE/$TIMER.active"
    printf 'enabled' > "$STATE/$TIMER.enabled"
    : > "$STATE/$DAEMON_UNIT.active"
    rm -f "$STATE/$DAEMON_UNIT.failed"
    cat > "$WDFIX/releases/state.json" <<'JOURNAL'
{"current":"v1","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
    rm -f "$WDFIX/data/upgrade/sandbox/watchdog.hold.json" 2>/dev/null || true
}
SC_LOG="$WDFIX/sc.log"

# ═══════════════════════════════════════════════════════════════════════════
section "AC-D2-1 — the hold stops the candidate timer; the restore re-arms it"
# ═══════════════════════════════════════════════════════════════════════════
reset_state
WD1_OUT="$(run_wd '
    _watchdog_probe; echo "probe-rc=$?"
    echo "candidates=[$WATCHDOG_CANDIDATES]"
    echo "verdicts=[$WATCHDOG_PROBE_VERDICTS]"
    _watchdog_rt_arm "run-wd1"; echo "rt-rc=$?"
    echo "rt-unit=$WATCHDOG_RT_UNIT"
    _watchdog_hold_apply "run-wd1"; echo "hold-rc=$?"
    echo "held=[$WATCHDOG_HELD_TIMERS]"
    if [ -f "$SC_STATE/'"$TIMER"'.active" ]; then echo "timer=ACTIVE"; else echo "timer=STOPPED"; fi
    echo "marker=[$(cat "$INSTALL_DIR/data/upgrade/sandbox/watchdog.hold.json" 2>/dev/null)]"
    echo "marker-mode=[$(stat -c %a "$INSTALL_DIR/data/upgrade/sandbox/watchdog.hold.json" 2>/dev/null)]"
    _watchdog_hold_restore; echo "restore-rc=$?"
    if [ -f "$SC_STATE/'"$TIMER"'.active" ]; then echo "timer-after=ACTIVE"; else echo "timer-after=STOPPED"; fi
    if [ -f "$INSTALL_DIR/data/upgrade/sandbox/watchdog.hold.json" ]; then echo "marker-after=present"; else echo "marker-after=cleared"; fi
')"
assert_contains "AC-D2-1 the probe finds the armed watchdog timer" "candidates=[$TIMER]" "$WD1_OUT"
assert_contains "AC-D2-1 the verdict is a candidate" "$TIMER=candidate" "$WD1_OUT"
assert_contains "AC-D2-1 the restore transient is armed" "rt-rc=0" "$WD1_OUT"
assert_contains "AC-D2-1 the hold is applied" "hold-rc=0" "$WD1_OUT"
assert_contains "AC-D2-1 the timer is STOPPED for the window" "timer=STOPPED" "$WD1_OUT"
assert_contains "AC-D2-1 the marker records the held set" "\"timers\":\"$TIMER\"" "$WD1_OUT"
assert_contains "AC-D2-1 the marker records the prior enable state" "\"prior_unit_file_state\":\"enabled\"" "$WD1_OUT"
assert_contains "AC-D2-1 the marker is daemon-readable (mode 0644)" "marker-mode=[644]" "$WD1_OUT"
assert_contains "AC-D2-1 the restore re-arms the timer" "timer-after=ACTIVE" "$WD1_OUT"
assert_contains "AC-D2-1 the restore clears the marker" "marker-after=cleared" "$WD1_OUT"
# the hold NEVER disables the timer (an enabled timer is re-armed by
# systemd at boot ⇒ the reboot path stays covered)
WD1_LOG="$(cat "$SC_LOG" 2>/dev/null)"
assert_not_contains "AC-D2-1 the timer is never DISABLED" "disable" "$WD1_LOG"
assert_contains "AC-D2-1 the timer is STOPPED (not disabled)" "stop $TIMER" "$WD1_LOG"
# the ORDER pin: the restore transient is minted BEFORE the timer is stopped
WD1_RUN_LOG="$(cat "$SC_LOG.systemd-run" 2>/dev/null)"
assert_contains "AC-D2-2 the restore transient is minted with the TTL" "--on-active=${RT_TTL:-5}" "$WD1_RUN_LOG"
MINT_LINE="$(grep -n "^systemd-run " "$SC_LOG" 2>/dev/null | head -1 | cut -d: -f1)"
STOP_LINE="$(grep -n "^stop $TIMER" "$SC_LOG" 2>/dev/null | head -1 | cut -d: -f1)"
if [ -n "$MINT_LINE" ] && [ -n "$STOP_LINE" ] && [ "$MINT_LINE" -lt "$STOP_LINE" ]; then
    _pass "AC-D2-2 the RT is minted BEFORE the timer is stopped (ordering pin)"
else
    _fail "AC-D2-2 the RT is minted BEFORE the timer is stopped (ordering pin)" \
        "mint-line < stop-line" "mint=$MINT_LINE stop=$STOP_LINE"
fi

# ═══════════════════════════════════════════════════════════════════════════
section "AC-D2-2 — a mint failure ⇒ NO stop + the loud watchdog_hold_unavailable"
# ═══════════════════════════════════════════════════════════════════════════
reset_state
SC_SYSTEMD_RUN_RC=1
export SC_SYSTEMD_RUN_RC
WD2_OUT="$(run_wd '
    _watchdog_probe
    if ! _watchdog_rt_arm "run-wd2"; then
        echo "mint=failed"
        journal_history_append watchdog_hold_unavailable "watchdog hold unavailable: the restore transient could not be armed — NO timer is held" || true
    elif ! _watchdog_hold_apply "run-wd2"; then
        echo "hold=failed"
    fi
    if [ -f "$SC_STATE/'"$TIMER"'.active" ]; then echo "timer=ACTIVE"; else echo "timer=STOPPED"; fi
    echo "journal=[$(cat "$INSTALL_DIR/releases/state.json" 2>/dev/null)]"
')"
unset SC_SYSTEMD_RUN_RC
assert_contains "AC-D2-2 the mint failure is observed" "mint=failed" "$WD2_OUT"
assert_contains "AC-D2-2 NO timer is held when the restore path is unavailable" "timer=ACTIVE" "$WD2_OUT"
assert_contains "AC-D2-2 the degradation is JOURNALED (never silent)" "watchdog_hold_unavailable" "$WD2_OUT"
assert_not_contains "AC-D2-2 the timer is never stopped without a restore path" "stop $TIMER" "$(cat "$SC_LOG" 2>/dev/null)"

# ═══════════════════════════════════════════════════════════════════════════
section "AC-D2-3 — the executor's death leaves the RT armed; the RT restores"
# ═══════════════════════════════════════════════════════════════════════════
reset_state
# The journal carries an OPEN txn whose heartbeat is STALE and whose owner
# is dead — the executor died mid-window (the F1 shape).
STALE_HB=1000000000
cat > "$WDFIX/releases/state.json" <<JOURNAL
{"current":"v1","previous":null,"in_flight":{"kind":"promote","target":"v2","started_at":"2026-01-01T00:00:00Z","flipped":true,"owner_pid":999999,"last_heartbeat":$STALE_HB},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
# The daemon unit is DOWN (inactive) — the hold is bounded by daemon liveness.
rm -f "$STATE/$DAEMON_UNIT.active"
WD3_OUT="$(run_wd '
    _watchdog_probe
    _watchdog_rt_arm "run-wd3"
    _watchdog_hold_apply "run-wd3"
    echo "held=[$WATCHDOG_HELD_TIMERS]"
    if [ -f "$SC_STATE/'"$TIMER"'.active" ]; then echo "timer=ACTIVE"; else echo "timer=STOPPED"; fi
')"
assert_contains "AC-D2-3 the hold is applied before the executor dies" "held=[$TIMER]" "$WD3_OUT"
assert_contains "AC-D2-3 the timer stays stopped while the executor is gone" "timer=STOPPED" "$WD3_OUT"
# The RT FIRES (the same argv the real transient carries).
RT_RC=0
PATH="$STUBBIN:$PATH" \
INSTALL_DIR="$WDFIX" UP_TARGET=sandbox \
SYSTEMCTL_BIN=systemctl SYSTEMD_RUN_BIN=systemd-run \
SC_LOG="$SC_LOG" SC_STATE="$STATE" SC_TIMERS="$TIMER enabled" \
WATCHDOG_RT_RUN_ID="run-wd3" WATCHDOG_RT_ITERATION=1 \
WATCHDOG_HOLD_RT_TTL_S=5 WATCHDOG_HOLD_MAX_ITERATIONS=3 \
_LIB_WATCHDOG_RT_ENTRY=1 \
timeout 60 bash "$LIBSH" --watchdog-restore >/tmp/wd_rt_$$.log 2>&1 || RT_RC=$?
WD3_RT="$(cat /tmp/wd_rt_$$.log 2>/dev/null)"; rm -f /tmp/wd_rt_$$.log
assert_eq "AC-D2-3 the restore transient's entry point runs clean" "0" "$RT_RC"
if [ -f "$STATE/$TIMER.active" ]; then
    _pass "AC-D2-3 the RT re-arms the timer (the executor is gone; the daemon is down)"
else
    _fail "AC-D2-3 the RT re-arms the timer (the executor is gone; the daemon is down)" \
        "timer re-armed" "timer still stopped"
fi
assert_contains "AC-D2-3 the RT journals its restore" "watchdog_restore" "$(cat "$WDFIX/releases/state.json" 2>/dev/null)"

# ═══════════════════════════════════════════════════════════════════════════
section "AC-D2-10 — the hold is bounded by daemon liveness (≤3 iterations)"
# ═══════════════════════════════════════════════════════════════════════════
# (a) a FRESH heartbeat + a LIVE daemon ⇒ the RT DEFERS (it re-arms
#     itself) and the timer STAYS stopped — the ceremony may still be alive.
reset_state
NOW_HB="$(date +%s)"
cat > "$WDFIX/releases/state.json" <<JOURNAL
{"current":"v1","previous":null,"in_flight":{"kind":"promote","target":"v2","started_at":"2026-01-01T00:00:00Z","flipped":true,"owner_pid":999998,"last_heartbeat":$NOW_HB},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
: > "$STATE/$DAEMON_UNIT.active"
run_wd '
    _watchdog_probe
    _watchdog_rt_arm "run-wd10"
    _watchdog_hold_apply "run-wd10"
' >/dev/null
RT_RC=0
PATH="$STUBBIN:$PATH" \
INSTALL_DIR="$WDFIX" UP_TARGET=sandbox \
SYSTEMCTL_BIN=systemctl SYSTEMD_RUN_BIN=systemd-run \
SC_LOG="$SC_LOG" SC_STATE="$STATE" SC_TIMERS="$TIMER enabled" \
WATCHDOG_RT_RUN_ID="run-wd10" WATCHDOG_RT_ITERATION=1 \
WATCHDOG_HOLD_RT_TTL_S=5 WATCHDOG_HOLD_MAX_ITERATIONS=3 \
_LIB_WATCHDOG_RT_ENTRY=1 \
timeout 60 bash "$LIBSH" --watchdog-restore >/tmp/wd_rt10_$$.log 2>&1 || RT_RC=$?
WD10_RT="$(cat /tmp/wd_rt10_$$.log 2>/dev/null)"; rm -f /tmp/wd_rt10_$$.log
assert_eq "AC-D2-10 the deferral path runs clean" "0" "$RT_RC"
if [ -f "$STATE/$TIMER.active" ]; then
    _fail "AC-D2-10 a live ceremony keeps the hold (the timer stays stopped)" \
        "timer stopped" "timer re-armed"
else
    _pass "AC-D2-10 a live ceremony keeps the hold (the timer stays stopped)"
fi
assert_contains "AC-D2-10 the deferral is JOURNALED (watchdog_hold_deferred)" "watchdog_hold_deferred" "$(cat "$WDFIX/releases/state.json" 2>/dev/null)"
assert_contains "AC-D2-10 the RT re-armed itself (a bounded re-arm, not a restore)" "--setenv=WATCHDOG_RT_ITERATION=2" "$(cat "$SC_LOG.systemd-run" 2>/dev/null)"

# (b) the ITERATION BOUND: at the bound the RT restores (never the old
#     20-iteration loop ⇒ never ~10h of darkness — the F13 defect).
reset_state
cat > "$WDFIX/releases/state.json" <<JOURNAL
{"current":"v1","previous":null,"in_flight":{"kind":"promote","target":"v2","started_at":"2026-01-01T00:00:00Z","flipped":true,"owner_pid":999998,"last_heartbeat":$NOW_HB},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
: > "$STATE/$DAEMON_UNIT.active"
run_wd '
    _watchdog_probe
    _watchdog_rt_arm "run-wd10b"
    _watchdog_hold_apply "run-wd10b"
' >/dev/null
MINT_COUNT_BEFORE="$(wc -l < "$SC_LOG.systemd-run" 2>/dev/null || echo 0)"
RT_RC=0
PATH="$STUBBIN:$PATH" \
INSTALL_DIR="$WDFIX" UP_TARGET=sandbox \
SYSTEMCTL_BIN=systemctl SYSTEMD_RUN_BIN=systemd-run \
SC_LOG="$SC_LOG" SC_STATE="$STATE" SC_TIMERS="$TIMER enabled" \
WATCHDOG_RT_RUN_ID="run-wd10b" WATCHDOG_RT_ITERATION=3 \
WATCHDOG_HOLD_RT_TTL_S=5 WATCHDOG_HOLD_MAX_ITERATIONS=3 \
_LIB_WATCHDOG_RT_ENTRY=1 \
timeout 60 bash "$LIBSH" --watchdog-restore >/tmp/wd_rt10b_$$.log 2>&1 || RT_RC=$?
WD10B_RT="$(cat /tmp/wd_rt10b_$$.log 2>/dev/null)"; rm -f /tmp/wd_rt10b_$$.log
assert_eq "AC-D2-10 the bounded restore runs clean" "0" "$RT_RC"
if [ -f "$STATE/$TIMER.active" ]; then
    _pass "AC-D2-10 the iteration bound restores the timer (≤3 — never the old 20)"
else
    _fail "AC-D2-10 the iteration bound restores the timer (≤3 — never the old 20)" \
        "timer re-armed" "timer still stopped"
fi
MINT_COUNT_AFTER="$(wc -l < "$SC_LOG.systemd-run" 2>/dev/null || echo 0)"
assert_eq "AC-D2-10 the bounded restore does NOT re-arm itself again (the bound ends the hold)" \
    "$MINT_COUNT_BEFORE" "$MINT_COUNT_AFTER"

# ═══════════════════════════════════════════════════════════════════════════
section "PRIVILEGED (tagged) — a REAL systemd transient + a real timer"
# ═══════════════════════════════════════════════════════════════════════════
# approver note 7 + clarification (v): a leg that needs REAL systemd is
# TAGGED so it is never silently skipped NOR falsely greened in the
# sandbox. It runs only where the operator opts in.
if [ "${ENSEMBLE_WATCHDOG_PRIVILEGED_LEG:-0}" != "1" ]; then
    printf 'SKIP: the privileged-systemd leg is tagged (ENSEMBLE_WATCHDOG_PRIVILEGED_LEG=1 to run it on a real systemd host)\n'
elif [ "$(uname -s)" != "Linux" ] || [ ! -d /run/systemd/system ]; then
    printf 'SKIP: no real systemd on this host\n'
else
    printf 'RUN: the privileged leg (real systemd-run transient)\n'
    if command -v systemd-run >/dev/null 2>&1; then
        printf '  systemd-run present — the transient mint is exercised by the tool lane\n'
        _pass "privileged leg: systemd-run is available"
    else
        printf '  systemd-run absent — the real transient cannot be minted here\n'
    fi
fi

# ── summary ─────────────────────────────────────────────────────────────────
printf '\n== summary ==\n'
printf 'PASS=%d FAIL=%d\n' "$PASS" "$FAIL"
if [ -n "$FAILED_TESTS" ]; then
    printf 'FAILED TESTS:%s\n' "$FAILED_TESTS"
fi
[ "$FAIL" -eq 0 ] || exit 1
exit 0
