#!/bin/bash
# ============================================================================
# launcher.sh — Ensemble production supervisor wrapper (Auto-Restart Phase 1)
# ============================================================================
# Runs UNDER launchd (macOS) / systemd (Linux). No TTY, no shell env — every
# path is absolute and derived from this script's own location.
#
# Division of labor (plan §4.1, ADR-001/002):
#   OS supervisor (launchd): start at boot, SIGTERM stop, fixed 10s throttle,
#                           re-engage only if THIS launcher itself dies.
#   This launcher:           exponential backoff, burst budget (crash-loop
#                           protection), exit-code mapping, env loading.
#
# Exit-code contract (ADR-010/011 — the daemon side is live as of 582b4c27):
#   0   clean stop        → launcher exits 0, does NOT loop (launchd's
#                           KeepAlive semantics decide what happens next).
#   75  boot-time tempfail (PG unreachable at boot) → capped backoff
#                           (start 5s, double, cap 60s) and retry; does NOT
#                           decrement the burst budget; one-time notify.
#   78  refuse (fatal config / missing binary / schema refusal) → launcher
#                           exits 78 IMMEDIATELY, no restart loop.
#   1/other nonzero crash → exponential backoff (base 10s, factor 2,
#                           cap 300s) + burst budget: >5 crashes within
#                           10 min → abort, log halt, exit 1 (paced at OS
#                           level by launchd ThrottleInterval).
#
# Env precedence (ADR-014): INSTALL_DIR/.env (staged from repo .env.prod by
# `make install`) is exported HERE, before the binary runs. The frozen
# binary's own .env loader (run_app.py) only sets vars that are still unset —
# so launcher exports always win. Structural precedence, not convention.
#
# Pure-logic seam: classify_exit / next_backoff / budget_tick / load_env_file
# are small deterministic functions so tests can source this file WITHOUT
# running binaries (tests/test_launcher.sh). The run-loop is guarded by the
# source-guard below.
#
# NOTE: deliberately `set -u` WITHOUT `set -e` — the whole point of this
# script is surviving and classifying child failures, not dying on them.
# ============================================================================

set -u

# ── Tunables (ADR-002/011) ─────────────────────────────────────────────────
CRASH_BACKOFF_BASE_S=10       # crash backoff: first wait
CRASH_BACKOFF_FACTOR=2        # crash backoff: multiplier
CRASH_BACKOFF_CAP_S=300       # crash backoff: ceiling
TEMPFAIL_BACKOFF_START_S=5    # exit-75 track: first wait
TEMPFAIL_BACKOFF_CAP_S=60     # exit-75 track: ceiling (ADR-011: ≤60s)
BUDGET_MAX_CRASHES=5          # >5 crashes within window → abort
BUDGET_WINDOW_S=600           # 10-minute sliding burst window
BUDGET_UPTIME_RESET_S=600     # ≥600s continuous uptime → budget reset (ADR-011 #2)
CHILD_STOP_WAIT_S=70          # SIGTERM→child bound: daemon graceful default
                              # 60s (DaemonConfig.graceful_shutdown_timeout_
                              # seconds) + 10s margin

# ── Logging ─────────────────────────────────────────────────────────────────
# To stderr only. Under launchd, StandardErrorPath captures it
# (INSTALL_DIR/data/launcher.err.log). No TTY assumptions.
_log() {
    printf '%s launcher[%s]: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$$" "$*" >&2
}

# ── INSTALL_DIR resolution ──────────────────────────────────────────────────
# Resolve symlinks without `readlink -f` (not on macOS). Loop over readlink
# output; relative link targets are anchored at the link's directory.
_resolve_symlink() {
    local target="$1" link
    while [ -L "$target" ]; do
        link="$(readlink "$target" 2>/dev/null)" || break
        case "$link" in
            /*) target="$link" ;;
            *) target="$(dirname "$target")/$link" ;;
        esac
    done
    printf '%s\n' "$target"
}

# Derive INSTALL_DIR from this script's own (symlink-resolved, physical)
# location. ENSEMBLE_INSTALL_DIR overrides for tests / exotic layouts.
resolve_install_dir() {
    local self="$1" dir
    if [ -n "${ENSEMBLE_INSTALL_DIR:-}" ]; then
        printf '%s\n' "$ENSEMBLE_INSTALL_DIR"
        return 0
    fi
    self="$(_resolve_symlink "$self")"
    dir="$(cd "$(dirname "$self")" 2>/dev/null && pwd -P)" || dir=""
    if [ -z "$dir" ]; then
        _log "FATAL: cannot resolve install directory from $1"
        return 1
    fi
    printf '%s\n' "$dir"
}

# ── .env loader (ADR-014) ───────────────────────────────────────────────────
# Parse KEY=VALUE lines and export them. Pure bash, NO eval — values may
# contain spaces and quotes. Tolerated forms:
#   KEY=value
#   export KEY=value          (optional `export ` prefix)
#   KEY="quoted value"        (matching surrounding quotes are stripped)
#   KEY='single quoted'
#   # comment / blank lines   (skipped)
# Malformed lines (no '=', invalid key charset) are logged and skipped.
# A missing file is NOT fatal: log loudly and continue with the inherited
# environment (prod may rely on plist-passed env).
load_env_file() {
    local env_file="$1" line key value
    if [ ! -f "$env_file" ]; then
        _log "WARN: env file not found: $env_file — continuing with inherited environment"
        return 0
    fi
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        # trim leading/trailing whitespace
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [ -z "$line" ] && continue
        case "$line" in
            \#*) continue ;;
        esac
        # strip optional `export ` prefix
        case "$line" in
            export\ *) line="${line#export }" ;;
        esac
        case "$line" in
            *=*) ;;
            *) _log "WARN: env file line without '=' skipped: $line"; continue ;;
        esac
        key="${line%%=*}"
        value="${line#*=}"
        # trim whitespace around key and value
        key="${key%"${key##*[![:space:]]}"}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        if ! [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            _log "WARN: env file line with invalid key skipped: $line"
            continue
        fi
        # strip one layer of matching surrounding quotes
        if [ "${#value}" -ge 2 ]; then
            case "$value" in
                \"*\") value="${value#\"}"; value="${value%\"}" ;;
                \'*\') value="${value#\'}"; value="${value%\'}" ;;
            esac
        fi
        export "$key=$value"
    done < "$env_file"
    return 0
}

# ── Journal sweep hook (ADR-012 / M2) — Phase 1: STRUCTURE ONLY ────────────
# Called from the start path BEFORE resolving the binary.
#
# PHASE-3 CONTRACT (do not lose this when implementing):
#   Read INSTALL_DIR/releases/state.json. If an upgrade transaction is
#   `in-flight` AND older than the 10-minute rollback window
#   (now - txn.started_at > 600s):
#     - flip already happened → launcher executes the rollback itself:
#       repoint `current` to `previous`, notify, escalate (counts as an
#       auto-rollback for ADR-005 cooldown/counters);
#     - flip never happened → clear the pre-flip transaction and continue.
#   The sweep runs at a layer BELOW the daemon, so it recovers even when
#   the daemon is the thing that won't boot. Until Phase 2/3 land the
#   releases/ layout + journal, no promote/journal exists — today this
#   only observes and logs.
_journal_sweep() {
    local install_dir="${1:-${INSTALL_DIR:-}}"
    [ -n "$install_dir" ] || return 0
    local journal="$install_dir/releases/state.json"
    [ -f "$journal" ] || return 0
    # Phase 1 stub: journal exists (unexpected pre-Phase-2) — log it.
    _log "journal sweep: $journal present — no promote/journal machinery until Phase 2/3 (ADR-012 stub, logging only)"
    return 0
}

# ── One-time notify (Phase 1: log-only stub) ────────────────────────────────
# ADR-008 (Phase 6) wires the real observer: post-hoc LLM postmortem on the
# system_background_queue, triggered only AFTER terminal events and only once
# /readyz is green. This stub exists so the run-loop's notify sites are final;
# Phase 6 replaces the body only. `kind` examples: tempfail-75, burst-abort.
_notify_once() {
    local kind="$1"
    shift
    _log "NOTIFY[$kind]: $*"
}

# ── Restart-state persistence (belt-and-braces to ADR-010) ─────────────────
# INSTALL_DIR/.launcher-state — tiny key=value file, atomic write (tmp+mv).
# Keys: last_exit, crash_count, window_start, last_backoff, notified_75.
# Absent or corrupt file → reset to defaults (never fatal).

STATE_FILE=""

# Sets globals: STATE_LAST_EXIT STATE_CRASH_COUNT STATE_WINDOW_START
#              STATE_LAST_BACKOFF STATE_NOTIFIED_75 STATE_LAST_UPTIME
read_state() {
    STATE_LAST_EXIT=""
    STATE_CRASH_COUNT=0
    STATE_WINDOW_START=0
    STATE_LAST_BACKOFF=0
    STATE_NOTIFIED_75=0
    STATE_LAST_UPTIME=0
    [ -n "$STATE_FILE" ] || return 0
    [ -f "$STATE_FILE" ] || return 0
    local line key value
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            *=*) ;;
            *) continue ;;   # corrupt/garbage line → ignore, keep defaults
        esac
        key="${line%%=*}"
        value="${line#*=}"
        case "$key" in
            last_exit)
                [[ "$value" =~ ^-?[0-9]+$ ]] && STATE_LAST_EXIT="$value"
                ;;
            crash_count)
                [[ "$value" =~ ^[0-9]+$ ]] && STATE_CRASH_COUNT="$value"
                ;;
            window_start)
                [[ "$value" =~ ^[0-9]+$ ]] && STATE_WINDOW_START="$value"
                ;;
            last_backoff)
                [[ "$value" =~ ^[0-9]+$ ]] && STATE_LAST_BACKOFF="$value"
                ;;
            notified_75)
                [[ "$value" =~ ^[01]$ ]] && STATE_NOTIFIED_75="$value"
                ;;
            last_uptime)
                [[ "$value" =~ ^[0-9]+$ ]] && STATE_LAST_UPTIME="$value"
                ;;
        esac
    done < "$STATE_FILE"
    return 0
}

write_state() {
    [ -n "$STATE_FILE" ] || return 0
    local tmp="${STATE_FILE}.tmp.$$"
    printf 'last_exit=%s\ncrash_count=%s\nwindow_start=%s\nlast_backoff=%s\nnotified_75=%s\nlast_uptime=%s\n' \
        "${STATE_LAST_EXIT:-}" "${STATE_CRASH_COUNT:-0}" \
        "${STATE_WINDOW_START:-0}" "${STATE_LAST_BACKOFF:-0}" \
        "${STATE_NOTIFIED_75:-0}" "${STATE_LAST_UPTIME:-0}" > "$tmp" 2>/dev/null \
        && mv -f "$tmp" "$STATE_FILE" 2>/dev/null \
        || { _log "WARN: could not persist launcher state to $STATE_FILE"; rm -f "$tmp" 2>/dev/null; }
    return 0
}

_now() {
    date +%s
}

# ── Pure decision functions (unit-testable seam) ────────────────────────────

# classify_exit <code> → echoes: clean | tempfail | refuse | crash
classify_exit() {
    case "$1" in
        0)  echo "clean" ;;
        75) echo "tempfail" ;;
        78) echo "refuse" ;;
        *)  echo "crash" ;;
    esac
}

# next_backoff <prev_backoff_s> <code> → echoes next backoff (seconds).
# Crash track (exit 1/other):  base 10s, ×2, cap 300s  → 10→20→40→…→300.
# Tempfail track (exit 75):    start 5s, ×2, cap 60s   → 5→10→20→40→60 (ADR-011).
# prev ≤ 0 (or track switch) restarts at the family base. Both tracks share
# one shape so the mapping is fully determined by (prev, code).
next_backoff() {
    local prev="$1" code="$2" base cap next
    if [ "$code" = "75" ]; then
        base=$TEMPFAIL_BACKOFF_START_S
        cap=$TEMPFAIL_BACKOFF_CAP_S
    else
        base=$CRASH_BACKOFF_BASE_S
        cap=$CRASH_BACKOFF_CAP_S
    fi
    if [ "$prev" -le 0 ] 2>/dev/null; then
        next=$base
    else
        next=$((prev * CRASH_BACKOFF_FACTOR))
    fi
    if [ "$next" -gt "$cap" ]; then
        next=$cap
    fi
    echo "$next"
}

# budget_tick <crash_count> <window_start> <uptime_s> <code> [now_epoch]
#   → echoes "<new_crash_count> <new_window_start> <action>"
#   action:
#     exempt — exit 75: budget untouched (ADR-011 #1), no decrement
#     reset  — fresh burst (uptime ≥600s continuous → counter+backoff reset,
#              ADR-011 #2; also covers an aged-out 10-min window)
#     count  — crash counted inside the current window
#     abort  — >5 crashes within the 10-min window → halt
# Callers reset their backoff to base on action=reset.
budget_tick() {
    local count="$1" window_start="$2" uptime_s="$3" code="$4"
    local now="${5:-$(_now)}"
    if [ "$code" -eq 75 ]; then
        echo "$count $window_start exempt"
        return 0
    fi
    # Crash (exit 78 never reaches here — the run-loop exits before ticking).
    if [ "$uptime_s" -ge "$BUDGET_UPTIME_RESET_S" ]; then
        echo "1 $now reset"
        return 0
    fi
    if [ "$window_start" -le 0 ] || [ $((now - window_start)) -gt "$BUDGET_WINDOW_S" ]; then
        echo "1 $now reset"
        return 0
    fi
    count=$((count + 1))
    if [ "$count" -gt "$BUDGET_MAX_CRASHES" ]; then
        echo "$count $window_start abort"
    else
        echo "$count $window_start count"
    fi
}

# ── Binary resolution (Phase-1 form) ────────────────────────────────────────
# Prefer INSTALL_DIR/current/ensemble-prod (Phase 2 introduces the releases/
# layout behind the `current` symlink); fall back to today's flat
# INSTALL_DIR/ensemble-prod. Neither → exit 78 (missing binary is fatal
# config, non-looping — the supervisor must NOT restart-loop on it).
resolve_binary() {
    local via_current="$INSTALL_DIR/current/ensemble-prod"
    local flat="$INSTALL_DIR/ensemble-prod"
    if [ -e "$via_current" ]; then
        if [ -x "$via_current" ]; then
            printf '%s\n' "$via_current"
            return 0
        fi
        _log "WARN: $via_current exists but is not executable — trying flat layout"
    fi
    if [ -e "$flat" ]; then
        if [ -x "$flat" ]; then
            printf '%s\n' "$flat"
            return 0
        fi
        _log "WARN: $flat exists but is not executable"
    fi
    return 1
}

# ── Signal handling ─────────────────────────────────────────────────────────
# Trap is installed BEFORE the loop (task requirement). The handler only
# sets a flag and forwards the signal to the child; the loop observes the
# flag after `wait` returns and exits directly — the trap can never re-enter
# or resume the loop.
SHUTDOWN_REQUESTED=0
CHILD_PID=0

_handle_signal() {
    local sig="$1"
    SHUTDOWN_REQUESTED=1
    _log "received SIG$sig — forwarding to child (pid ${CHILD_PID:-none})"
    if [ "${CHILD_PID:-0}" -gt 0 ]; then
        kill -"$sig" "$CHILD_PID" 2>/dev/null
    fi
}

# Wait (bounded) for a child we already signaled; SIGKILL as last resort.
# Sets REAPED_EXIT to the child's real exit code (143=TERM, 137=KILL, …).
_reap_child_bounded() {
    local pid="$1" timeout="$2" waited=0
    REAPED_EXIT=0
    while kill -0 "$pid" 2>/dev/null; do
        if [ "$waited" -ge "$timeout" ]; then
            _log "child pid $pid still alive after ${timeout}s — SIGKILL last resort"
            kill -9 "$pid" 2>/dev/null
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done
    wait "$pid"
    REAPED_EXIT=$?
    return 0
}

# Interruptible sleep: `sleep N &` + wait so a trapped signal wakes us at
# once instead of after the full backoff.
_sleep_interruptible() {
    local secs="$1"
    sleep "$secs" &
    local sleeper=$!
    wait "$sleeper" 2>/dev/null
    return 0
}

# ── Run loop (ADR-002/011) ──────────────────────────────────────────────────
run_loop() {
    local bin="$1"
    local child_exit=0 started_at=0 uptime=0 verdict="" tick_out="" action=""
    local new_count=0 new_window=0 backoff=0 reaped=0

    trap '_handle_signal TERM' TERM
    trap '_handle_signal INT' INT

    read_state

    while :; do
        if [ "$SHUTDOWN_REQUESTED" -eq 1 ]; then
            _log "shutdown requested before start — exiting 0"
            exit 0
        fi

        started_at=$(_now)
        _log "starting: $bin"
        "$bin" &
        CHILD_PID=$!
        wait "$CHILD_PID"
        child_exit=$?
        uptime=$(( $(_now) - started_at ))
        STATE_LAST_UPTIME="$uptime"

        verdict="$(classify_exit "$child_exit")"

        if [ "$SHUTDOWN_REQUESTED" -eq 1 ]; then
            # wait was interrupted by our trap (bash: wait returns >128 when
            # a trapped signal fires; the trap runs, then wait returns). The
            # child HAS received the forwarded signal and is inside its
            # graceful shutdown — the pid is still reapable via jobs -p.
            # Bound the wait, then exit with the child's real exit code —
            # the loop does NOT continue (clean supervisor semantics for
            # `make stop`-style kills).
            reaped=$(jobs -p | head -1)
            if [ -n "$reaped" ] && kill -0 "$reaped" 2>/dev/null; then
                CHILD_PID="$reaped"
                _reap_child_bounded "$reaped" "$CHILD_STOP_WAIT_S"
                child_exit="$REAPED_EXIT"
            fi
            CHILD_PID=0
            _log "shutdown complete — exiting $child_exit (child's exit code)"
            exit "$child_exit"
        fi
        CHILD_PID=0

        STATE_LAST_EXIT="$child_exit"
        write_state

        case "$verdict" in
            clean)
                _log "child exited 0 (clean stop) — launcher exiting 0, not looping"
                exit 0
                ;;
            refuse)
                _log "child exited 78 (refuse: fatal config/schema) — launcher exiting 78, NOT restarting (ADR-010)"
                exit 78
                ;;
            tempfail)
                # ADR-011: capped backoff, NO burst-budget decrement.
                if [ "${STATE_NOTIFIED_75:-0}" -ne 1 ]; then
                    _notify_once "tempfail-75" \
                        "boot-time temporary failure (PG unreachable?) — retrying with capped backoff (cap ${TEMPFAIL_BACKOFF_CAP_S}s), burst budget untouched (ADR-011)"
                    STATE_NOTIFIED_75=1
                fi
                backoff="$(next_backoff "${STATE_LAST_BACKOFF:-0}" 75)"
                STATE_LAST_BACKOFF="$backoff"
                write_state
                _log "child exited 75 (boot tempfail, uptime ${uptime}s) — retrying in ${backoff}s (75-track, cap ${TEMPFAIL_BACKOFF_CAP_S}s)"
                _sleep_interruptible "$backoff"
                continue
                ;;
            crash)
                tick_out="$(budget_tick "${STATE_CRASH_COUNT:-0}" "${STATE_WINDOW_START:-0}" "$uptime" "$child_exit")"
                new_count="${tick_out%% *}"
                tick_out="${tick_out#* }"
                new_window="${tick_out%% *}"
                action="${tick_out#* }"

                STATE_CRASH_COUNT="$new_count"
                STATE_WINDOW_START="$new_window"

                if [ "$action" = "abort" ]; then
                    STATE_LAST_BACKOFF=0
                    write_state
                    _notify_once "burst-abort" \
                        "HALT: ${new_count} crashes within ${BUDGET_WINDOW_S}s (budget ${BUDGET_MAX_CRASHES}) — launcher staying down (exit 1); OS-level pacing via launchd ThrottleInterval; see $INSTALL_DIR/data/launcher.err.log"
                    _log "HALT: burst budget exceeded (${new_count} crashes in window) — exiting 1, staying down"
                    exit 1
                fi

                if [ "$action" = "reset" ]; then
                    # Fresh burst (uptime ≥600s or aged window): counter AND
                    # backoff reset to base (ADR-011 #2). Also re-arm the
                    # one-time 75 notify for the new epoch.
                    STATE_LAST_BACKOFF=0
                    STATE_NOTIFIED_75=0
                fi

                backoff="$(next_backoff "${STATE_LAST_BACKOFF:-0}" "$child_exit")"
                STATE_LAST_BACKOFF="$backoff"
                write_state
                _log "child exited $child_exit (crash #${new_count}, uptime ${uptime}s, action=$action) — restarting in ${backoff}s"
                _sleep_interruptible "$backoff"
                continue
                ;;
        esac
    done
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    INSTALL_DIR="$(resolve_install_dir "${BASH_SOURCE[0]}")" || exit 78
    if [ -z "$INSTALL_DIR" ]; then
        _log "FATAL: could not resolve INSTALL_DIR"
        exit 78
    fi

    if ! cd "$INSTALL_DIR" 2>/dev/null; then
        _log "FATAL: cannot cd to INSTALL_DIR: $INSTALL_DIR"
        exit 78
    fi

    STATE_FILE="$INSTALL_DIR/.launcher-state"

    # Env first (ADR-014): INSTALL_DIR/.env staged from repo .env.prod.
    # Exports win over the binary's own .env load (run_app.py only sets
    # unset vars) — precedence is structural.
    _log "ensemble launcher starting (INSTALL_DIR=$INSTALL_DIR)"
    load_env_file "$INSTALL_DIR/.env"

    # Journal sweep BEFORE resolving the binary (ADR-012).
    _journal_sweep

    local bin
    if ! bin="$(resolve_binary)"; then
        # Operator-facing, non-looping fatal (missing binary = config error).
        _log "FATAL: no ensemble-prod binary found — tried:"
        _log "  $INSTALL_DIR/current/ensemble-prod  (Phase 2 releases/ layout)"
        _log "  $INSTALL_DIR/ensemble-prod          (flat layout)"
        _log "Run 'make install' from the agents-ensemble repo, then restart."
        _log "Exiting 78 (refuse — supervisor must NOT loop on this)."
        exit 78
    fi

    run_loop "$bin"
}

# Source guard: `source launcher.sh` defines the pure functions without
# running anything (tests/test_launcher.sh relies on this).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
