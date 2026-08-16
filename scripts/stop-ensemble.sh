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
# Signal hygiene (ADR-009): SIGTERM first, bounded wait, SIGKILL last resort.
#
# SINGLE-TERM CONTRACT (review M2, 2026-08-16): when a launcher owns the
# daemon, ONLY the launcher is TERMed. The launcher trap forwards SIGTERM
# to its child exactly once, waits bounded, and exits with the child's
# exit code — so uvicorn receives exactly ONE SIGTERM and runs its full
# graceful lifespan teardown. A second TERM to the daemon pid would trip
# uvicorn's force_exit and skip manager.shutdown() entirely (crash-
# equivalent). Direct-TERM of daemon pids happens ONLY in the no-launcher
# pass (plain installs, or a launcher that died without reaping).
#
# WAIT budget (review M3): the daemon's graceful drain is ~60s by default
# (DaemonConfig.graceful_shutdown_timeout_seconds, env
# DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS). WAIT_S defaults to
# graceful+10 (70) and prefers the value parsed from the TARGET's staged
# INSTALL_DIR/.env so the budget stays single-source; explicit WAIT_S
# always wins. Clamp 10..600 — malformed env can never produce garbage
# sleeps or 0s kills.
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

# WAIT_S resolution (M3), in precedence order:
#   1. explicit WAIT_S (CLI `WAIT_S=... bash ...` or exported env) — wins
#   2. DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS from INSTALL_DIR/.env + 10
#      (parsed below once INSTALL_DIR is resolved; clamped)
#   3. default 70 (60s graceful + 10s margin)
DEFAULT_WAIT_S=70
WAIT_S_FLOOR=10
WAIT_S_CAP=600
WAIT_S_EXPLICIT="${WAIT_S:-}"
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

# ── WAIT_S resolution (review M3) ────────────────────────────────────────────
# Single-source-of-truth: the SAME staged INSTALL_DIR/.env the launcher
# exports (ADR-014) also carries DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS.
# We read it directly from the file (not the environment): deploy.sh and
# the Makefile invoke this script WITHOUT exporting the daemon's env
# first (verified), so the staged file is the only reliable source.
# Budget = graceful timeout + 10s margin (launcher's CHILD_STOP_WAIT_S
# uses the same formula). Digits-only validation; clamp to floor/cap.
_resolve_wait_s() {
    # $1 = env file path (may not exist)
    local env_file="$1" raw="" val=""
    raw="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS[[:space:]]*=[[:space:]]*//p' "$env_file" 2>/dev/null | head -1)"
    raw="${raw%$'\r'}"
    # strip optional surrounding quotes
    case "$raw" in
        \"*\") raw="${raw#\"}"; raw="${raw%\"}" ;;
        \'*\') raw="${raw#\'}"; raw="${raw%\'}" ;;
    esac
    printf '%s' "$raw" | grep -Eq '^[0-9]+$' || raw=""
    if [ -z "$raw" ]; then
        printf '%s\n' "$DEFAULT_WAIT_S"
        return 0
    fi
    val=$((raw + 10))
    if [ "$val" -lt "$WAIT_S_FLOOR" ]; then val="$WAIT_S_FLOOR"; fi
    if [ "$val" -gt "$WAIT_S_CAP" ]; then val="$WAIT_S_CAP"; fi
    printf '%s\n' "$val"
}

WAIT_SOURCE="default (70 = 60s graceful + 10s margin)"
if [ -n "$WAIT_S_EXPLICIT" ]; then
    if printf '%s' "$WAIT_S_EXPLICIT" | grep -Eq '^[0-9]+$'; then
        WAIT_S="$WAIT_S_EXPLICIT"
        WAIT_SOURCE="explicit WAIT_S=$WAIT_S_EXPLICIT (override wins)"
    else
        WAIT_S="$DEFAULT_WAIT_S"
        WAIT_SOURCE="malformed WAIT_S='$WAIT_S_EXPLICIT' — fell back to $DEFAULT_WAIT_S"
    fi
else
    WAIT_S="$(_resolve_wait_s "$INSTALL_DIR/.env")"
    if [ "$WAIT_S" = "$DEFAULT_WAIT_S" ]; then
        WAIT_SOURCE="default (70 = 60s graceful + 10s margin)"
    else
        WAIT_SOURCE="derived from $INSTALL_DIR/.env (graceful + 10s, clamped ${WAIT_S_FLOOR}..${WAIT_S_CAP})"
    fi
fi

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
    # physical form — lsof reports physical paths). Launcher-shaped
    # processes get their own kind: deploy starts the launcher as
    # `./launcher.sh` (relative argv — Tier 1a's absolute anchor cannot
    # match it), and the launcher must still stop LAUNCHER-FIRST so the
    # daemon gets its single TERM via the trap forward (review M2).
    if printf '%s\n' "$args" | grep -Eq '(ensemble-prod)( |$)' \
        || printf '%s\n' "$comm" | grep -Eq 'ensemble-prod' \
        || printf '%s\n' "$args" | grep -Eq '(^| )([^ ]*/)?launcher\.sh( |$)'; then
        cwd="$(_cwd_of "$pid")"
        if [ "$cwd" = "$INSTALL_DIR" ] || [ "$cwd" = "$PHYS_DIR" ]; then
            if printf '%s\n' "$args" | grep -Eq '(^| )([^ ]*/)?launcher\.sh( |$)'; then
                echo "cwd-launcher"
            else
                echo "cwd"
            fi
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
    case "$kind" in
        launcher-path|cwd-launcher) owned_launchers="$owned_launchers $pid" ;;
        *) owned_rest="$owned_rest $pid" ;;
    esac
done

_log "WAIT_S resolved to ${WAIT_S}s — ${WAIT_SOURCE}"

if [ -z "$(printf '%s' "$owned_launchers$owned_rest" | tr -d ' ')" ]; then
    _log "no processes owned by $INSTALL_DIR — nothing to stop"
    exit 0
fi

# SINGLE-TERM STOP (review M2): when launchers are present, TERM ONLY the
# launcher(s). The launcher trap (launcher.sh run_loop) forwards SIGTERM
# to its daemon child exactly once, waits bounded (CHILD_STOP_WAIT_S),
# and exits with the child's real exit code — uvicorn gets exactly ONE
# TERM and runs its full graceful lifespan teardown (manager.shutdown()).
# TERMs in this same pass as well would be the SECOND TERM from uvicorn's
# point of view → force_exit → teardown skipped → every stop of a healthy
# daemon becomes crash-equivalent. The daemon pids are therefore deferred
# to the straggler pass below, which runs only for what is STILL alive
# after the launcher(s) finished (launcher died without reaping, or plain
# no-launcher installs where owned_rest IS the daemon set).
if [ -n "$(printf '%s' "$owned_launchers" | tr -d ' ')" ]; then
    _log "launcher-owned stop: TERMinG launcher(s) ONLY (single TERM, forwarded to daemon):$owned_launchers"
    _stop_pids $owned_launchers
fi

# Straggler / no-launcher pass — direct daemon TERM. Descendants of a
# launcher we just stopped were TERMed via the trap's forward (the
# launcher's bounded reap waits for its direct child; PyInstaller's
# bootloader waits for ITS child in turn, so the tree drains through
# the launcher). They get a short grace re-check here instead of an
# immediate second TERM; only what is STILL alive after that (launcher
# died without reaping → orphan) or was never under a launcher (plain
# install) gets the direct TERM→wait→KILL.
_descendants_of() {
    # $@ = root pids → prints all live descendants (recursive), one/line
    # Single ps snapshot (pid+ppid from the SAME line) so the two columns
    # can never misalign across separate ps calls.
    local roots="$*" out=" $* " changed=1 line pid ppid
    while [ "$changed" = "1" ]; do
        changed=0
        while read -r line; do
            pid="${line%% *}"; ppid="${line##* }"
            case " $out " in *" $ppid "*) ;; *) continue ;; esac
            case " $out " in *" $pid "*) continue ;; esac
            out="$out $pid "
            changed=1
        done < <(ps -axo pid=,ppid= 2>/dev/null | sed 's/  */ /g; s/^ //')
    done
    for pid in $out; do
        [ "$pid" = "$SELF_PID" ] && continue
        _alive "$pid" && printf '%s\n' "$pid"
    done
    return 0
}

if [ -n "$(printf '%s' "$owned_rest" | tr -d ' ')" ]; then
    LAUNCHER_DESC=""
    if [ -n "$(printf '%s' "$owned_launchers" | tr -d ' ')" ]; then
        LAUNCHER_DESC="$(_descendants_of $owned_launchers | tr '\n' ' ')"
    fi
    STRAGGLERS=""
    for pid in $owned_rest; do
        _alive "$pid" || continue
        case " $LAUNCHER_DESC " in
            *" $pid "*)
                if [ "$DRY_RUN" = "1" ]; then
                    _log "DRY_RUN: $pid (launcher descendant) would be grace-checked post-launcher — no second TERM while draining"
                    continue
                fi
                # was TERMed via the launcher's forward — brief grace
                # re-check before any second TERM (never double-TERM a
                # daemon still inside its graceful teardown)
                DRAINED=0
                for _ in 1 2 3; do
                    _alive "$pid" || { DRAINED=1; break; }
                    sleep 1
                done
                if [ "$DRAINED" = "1" ]; then
                    _log "$pid drained via launcher forward — no second TERM"
                    continue
                fi
                _log "$pid still alive after launcher stopped + grace (orphan) — direct TERM"
                ;;
        esac
        STRAGGLERS="$STRAGGLERS $pid"
    done
    if [ -n "$(printf '%s' "$STRAGGLERS" | tr -d ' ')" ]; then
        _log "stopping owned daemon process(es) directly (no live launcher):$STRAGGLERS"
        _stop_pids $STRAGGLERS
    fi
fi

_log "done — $INSTALL_DIR is stopped"
exit 0
