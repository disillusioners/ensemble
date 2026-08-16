#!/bin/bash
# ============================================================================
# stop-ensemble.sh — ownership-scoped stop for an Ensemble install
# ============================================================================
# INCIDENT FIX (2026-08-16): `make stop` / `make install` used to SIGTERM
# whatever listened on the prod port (lsof -ti:PORT | kill). On a dev+prod
# coexistence host that can kill a daemon owned by a DIFFERENT install (it
# did: a repo-side verification killed the real prod on 9797, and even a
# Chrome network-service client sharing that port was a potential victim).
#
# This script is structurally incapable of that: it NEVER selects processes
# by port. A process is stopped only when THIS install OWNS it:
#
#   Tier 1a — anchored executable path in the command line:
#             <INSTALL_DIR>/launcher.sh, <INSTALL_DIR>/ensemble-prod, or
#             <INSTALL_DIR>/current/ensemble-prod, each followed by a space
#             or end-of-line (so `tail -f <DIR>/launcher.sh.log`, editors
#             on other files, and mere directory mentions never match).
#   Tier 1b — ensemble-shaped process whose working directory IS the
#             install dir: catches the relative form `./ensemble-prod`
#             (launchd/manual starts rewrite the cmdline relative; the real
#             prod today runs exactly as `./ensemble-prod` with cwd =
#             ~/agents-ensemble — pgrep -f alone cannot identify it).
#
# Port lookup is REPORTING ONLY (echo which pids hold the port). The old
# behavior remains available behind an explicit opt-in:
#
#     STOP_BY_PORT=1 bash scripts/stop-ensemble.sh <dir> [port]
#
# with a loud warning that it can kill unrelated listeners on coexistence
# hosts.
#
# Signal hygiene (ADR-009): SIGTERM first, bounded wait (default 10s,
# WAIT_S override), SIGKILL last resort.
#
# Usage:
#   bash scripts/stop-ensemble.sh [INSTALL_DIR]     (default ~/agents-ensemble)
#   bash scripts/stop-ensemble.sh <dir> <port>      (port = reporting hint)
#   DRY_RUN=1 bash scripts/stop-ensemble.sh <dir>   (print the stop plan; never signal)
#
# Bash 3.2 / BSD tools compatible. Exit 0 when the install is stopped
# (or nothing was owned); exit 2 on usage errors.
# ============================================================================

set -u

WAIT_S="${WAIT_S:-10}"
DRY_RUN="${DRY_RUN:-0}"
SELF_PID=$$

_die() { echo "stop-ensemble: $*" >&2; exit 2; }

INSTALL_DIR="${1:-$HOME/agents-ensemble}"
REPORT_PORT="${2:-}"
[ -n "$INSTALL_DIR" ] || _die "empty INSTALL_DIR"

# Absolute, no trailing slash — cwd comparisons are exact.
INSTALL_DIR="$(cd "$INSTALL_DIR" 2>/dev/null && pwd)" || _die "cannot resolve INSTALL_DIR '$1'"
# Physical (symlink-free) form too: lsof reports PHYSICAL cwds (/tmp →
# /private/tmp on macOS), while launchd/cmdlines may carry the logical
# path. Ownership checks accept either form.
PHYS_DIR="$(cd -P "$INSTALL_DIR" 2>/dev/null && pwd)"
[ -n "$PHYS_DIR" ] || PHYS_DIR="$INSTALL_DIR"

# ── ERE-escape the install dir for pgrep patterns ──────────────────────────
_ere_escape() {
    printf '%s' "$1" | sed -e 's/[][\.*^$()+?{}|\\]/\\&/g'
}
ESC_DIR="$(_ere_escape "$INSTALL_DIR")"
ESC_PHYS="$(_ere_escape "$PHYS_DIR")"

_log() { printf 'stop-ensemble: %s\n' "$*"; }

# ── Candidate collection ────────────────────────────────────────────────────
# pids are printed one per line, deduped, SELF and our parent excluded.

_list_candidates() {
    # Tier 1a: anchored executable paths via pgrep -f (ERE, anchored token).
    pgrep -f "${ESC_DIR}/launcher\.sh( |$)" 2>/dev/null
    pgrep -f "${ESC_DIR}/ensemble-prod( |$)" 2>/dev/null
    pgrep -f "${ESC_DIR}/current/ensemble-prod( |$)" 2>/dev/null

    # Tier 1b: ps-based sweep for ensemble-shaped processes (catches the
    # relative `./ensemble-prod` form pgrep cannot anchor, and processes
    # pgrep refuses to see). Bracketed pattern so this sweep's own grep
    # line never matches itself. Postgres workers say "ensemble_prod"
    # (underscore) and autovacuum says "launcher" without ".sh" — both
    # are excluded by the literal shapes below.
    ps -axo pid=,comm=,args= 2>/dev/null | grep -E '[e]nsemble-prod|[l]auncher\.sh' \
        | awk '{pid=$1; comm=$2; $1=""; $2=""; args=$0; sub(/^  */, "", args)
                if (args ~ /(ensemble-prod)( |$)/ || comm ~ /ensemble-prod/ || \
                    args ~ /(^|\/)launcher\.sh( |$)/)
                    print pid}'
}

# cwd of a pid (empty string when unresolvable).
_cwd_of() {
    lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1
}

_alive() { kill -0 "$1" 2>/dev/null; }

# ── Ownership classification ────────────────────────────────────────────────
# A candidate is OWNED when its command line carries an anchored executable
# path under INSTALL_DIR, or it is ensemble-shaped AND its cwd IS the
# install dir. Everything else — foreign installs, dev daemons in the repo,
# editors, tails, Chrome — is NOT ours and is never signaled.

owned_pids=""
_classify() {
    local pid="$1" args="" comm="" cwd=""
    args="$(ps -o args= -p "$pid" 2>/dev/null)" || return 1
    comm="$(ps -o comm= -p "$pid" 2>/dev/null)"
    [ -n "$args" ] || return 1

    # Tier 1a: anchored executable path token in the command line.
    if printf '%s\n' "$args" | grep -Eq "${ESC_DIR}/launcher\.sh( |$)"; then
        echo "launcher-path"
        return 0
    fi
    if printf '%s\n' "$args" | grep -Eq "(${ESC_DIR}|${ESC_PHYS})/(current/)?ensemble-prod( |$)"; then
        echo "binary-path"
        return 0
    fi

    # Tier 1b: ensemble-shaped + cwd is the install dir (logical OR
    # physical form — lsof reports physical paths).
    if printf '%s\n' "$args" | grep -Eq '(ensemble-prod)( |$)' \
        || printf '%s\n' "$comm" | grep -Eq 'ensemble-prod' \
        || printf '%s\n' "$args" | grep -Eq '(^| )([^ ]*/)?launcher\.sh( |$)'; then
        cwd="$(_cwd_of "$pid")"
        if [ "$cwd" = "$INSTALL_DIR" ] || [ "$cwd" = "$PHYS_DIR" ]; then
            echo "cwd"
            return 0
        fi
    fi
    return 1
}

# ── Port reporting (NEVER a kill selector) ──────────────────────────────────
_report_port() {
    local port="$1" pids=""
    [ -n "$port" ] || return 0
    pids="$(lsof -ti:"$port" 2>/dev/null | tr '\n' ' ')"
    if [ -n "$pids" ]; then
        _log "port $port is held by: $pids (REPORT ONLY — ports are not a kill selector)"
    else
        _log "port $port is free"
    fi
}

# ── Bounded stop: TERM, wait, KILL ──────────────────────────────────────────
_stop_pids() {
    # $@ = pids
    if [ "$DRY_RUN" = "1" ]; then
        local p
        for p in "$@"; do
            _log "DRY_RUN: would signal $p ($(ps -o comm= -p "$p" 2>/dev/null))"
        done
        return 0
    fi
    local pid
    for pid in "$@"; do
        if _alive "$pid"; then
            _log "SIGTERM $pid ($(ps -o comm= -p "$pid" 2>/dev/null))"
            kill "$pid" 2>/dev/null || true
        fi
    done
    local waited=0
    while [ "$waited" -lt "$WAIT_S" ]; do
        local still=""
        for pid in "$@"; do
            _alive "$pid" && still="$still $pid"
        done
        [ -z "$still" ] && return 0
        sleep 1
        waited=$((waited + 1))
    done
    for pid in $still; do
        _log "SIGKILL $pid (still alive after ${WAIT_S}s)"
        kill -9 "$pid" 2>/dev/null || true
    done
}

# ── Main ────────────────────────────────────────────────────────────────────
_log "scoping to INSTALL_DIR=$INSTALL_DIR"

# Reporting hints only.
if [ -n "${PROD_PORT_HINT:-}" ]; then
    _report_port "$PROD_PORT_HINT"
fi
_report_port "$REPORT_PORT"

# Optional legacy escape hatch — explicit opt-in, loud warning.
if [ "${STOP_BY_PORT:-0}" = "1" ] && [ -n "$REPORT_PORT" ]; then
    echo "stop-ensemble: ⚠️  STOP_BY_PORT=1 — KILLING BY PORT can terminate unrelated" >&2
    echo "stop-ensemble: ⚠️  listeners on dev+prod coexistence hosts. You opted in." >&2
    pids="$(lsof -ti:"$REPORT_PORT" 2>/dev/null)"
    if [ -n "$pids" ]; then
        _stop_pids $pids
        _log "port-based stop done (opt-in)"
    else
        _log "nothing on port $REPORT_PORT"
    fi
    exit 0
fi

# Collect + classify.
raw="$(_list_candidates)"
owned_launchers=""
owned_rest=""
seen=""
for pid in $raw; do
    case " $seen " in *" $pid "*) continue ;; esac
    seen="$seen $pid"
    [ "$pid" = "$SELF_PID" ] && continue
    [ "$pid" = "$PPID" ] && continue
    kind="$(_classify "$pid")" || continue
    if [ "$kind" = "launcher-path" ]; then
        owned_launchers="$owned_launchers $pid"
    else
        owned_rest="$owned_rest $pid"
    fi
done

if [ -z "$(printf '%s' "$owned_launchers$owned_rest" | tr -d ' ')" ]; then
    _log "no processes owned by $INSTALL_DIR — nothing to stop"
    exit 0
fi

# Stop launchers FIRST: a running launcher forwards SIGTERM to its daemon
# child, waits (bounded), and exits with the child's real exit code — the
# stop is never classified as a crash and nothing bounces back up.
if [ -n "$(printf '%s' "$owned_launchers" | tr -d ' ')" ]; then
    _log "stopping owned launcher(s):$owned_launchers"
    _stop_pids $owned_launchers
fi
if [ -n "$(printf '%s' "$owned_rest" | tr -d ' ')" ]; then
    _log "stopping owned daemon process(es):$owned_rest"
    _stop_pids $owned_rest
fi

_log "done — $INSTALL_DIR is stopped"
exit 0
