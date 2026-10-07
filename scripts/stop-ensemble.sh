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
# UNIT-AWARE STOP (P2, ownership-mode commission 2026-09-29): when the
# caller hands the P1 supervision classification over as env
# ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:<unit> (scripts/upgrade/lib.sh
# stop_via_stop_script does), the stop routes through `systemctl stop
# <unit>` + a UNIT-STATE poll instead of pid TERMs — see the P2 section
# in the main flow. Every other shape (no classification, SCRIPT_NOHUP,
# SCOPE_SURVIVOR, non-Linux) keeps the pid-scoped path below,
# byte-identical.
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
# SETTLE + LOCK PREFlight (commission v0.16.6 component 2, Layer i+ii,
# incident r-20260929-170301-0cb2): the upgrade pipeline's only mutex is
# the lib.sh mkdir-lock (rollback.lock.d). stop-ensemble.sh now takes
# that lock around its mutate window (the operator lane), with two
# escape paths:
#   PIPELINE_LOCK_HELD_BY_CALLER=1  — the caller (lib.sh stop_via_stop_script
#                                     inside promote/rollback) already
#                                     holds the lock; stop-ensemble.sh
#                                     MUST skip its own acquire (otherwise
#                                     every promote deadlocks 15s on the
#                                     busy-lock wait).
#                                     ⚠️  TRUST ASSUMPTION: ONLY lib.sh
#                                     stop_via_stop_script (the in-pipeline
#                                     caller) may set this. Any external
#                                     caller is equivalent to --force —
#                                     a LOUD stderr WARN fires at the
#                                     honor site. Prefer --force for any
#                                     operator emergency (auditable in
#                                     shell history; the env var is not).
#   --force                         — operator emergency: bypass both the
#                                     settle-check AND the lock-check with
#                                     a LOUD warning. Use case: the
#                                     daemon is wedged under a live
#                                     promote and the operator must stop
#                                     it anyway (adopt-unit.sh needs no
#                                     override — it already refuses on
#                                     live pids).
# The preflight ALSO refuses when the pipeline is unsettled (open txn,
# journal/symlink drift, pending_op, or another lock holder) — that
# kills the racy "current == X + health" anti-pattern; the runbook
# documents the FORBIDDEN pattern with the incident citation.
#
# Usage:
#   bash scripts/stop-ensemble.sh [INSTALL_DIR]           (default ~/agents-ensemble)
#   bash scripts/stop-ensemble.sh <dir> <port>            (port = reporting hint)
#   DRY_RUN=1 bash scripts/stop-ensemble.sh <dir>         (print the stop plan; never signal)
#   bash scripts/stop-ensemble.sh <dir> <port> --force    (operator emergency; bypass settle/lock)
#
# Bash 3.2 / BSD tools compatible. Exit 0 when the install is stopped
# (or nothing was owned); exit 2 on usage errors; exit 78 on settle/lock refusal.
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
STOP_FORCE="${STOP_FORCE:-0}"        # --force flag (set by CLI parser)
PIPELINE_LOCK_HELD="${PIPELINE_LOCK_HELD_BY_CALLER:-0}"   # lib.sh stop_via_stop_script sets this
STOP_LOCK_HELD_BY_ME=0               # set if this invocation acquired the lock itself
_die() { echo "stop-ensemble: $*" >&2; exit 2; }

INSTALL_DIR="${1:-$HOME/agents-ensemble}"
REPORT_PORT="${2:-}"
# Pre-parse --force in ANY position, REMOVING it from the positional list.
# Walks the full argv; each non-flag arg is copied into the remaining
# positional list, so INSTALL_DIR=$1 / REPORT_PORT=$2 below resolve from
# the dir/port only. Accepted orders (documented at usage below):
#   <dir> --force | <dir> <port> --force | --force <dir> | --force <dir> <port>
# The legacy <dir> <port> form (no flag) leaves STOP_FORCE=0 and both
# remaining slots intact — prior test 16a semantics preserved.
# STOP_FORCE is declared with the WAIT_S knobs at the top of the file;
# the argv walk below is the only site that sets it.
REMAINING=()
for arg in "$@"; do
    if [ "$arg" = "--force" ]; then
        STOP_FORCE=1
    else
        REMAINING+=("$arg")
    fi
done
INSTALL_DIR="${REMAINING[0]:-$HOME/agents-ensemble}"
REPORT_PORT="${REMAINING[1]:-}"

[ -n "$INSTALL_DIR" ] || _die "empty INSTALL_DIR"

# Absolute, no trailing slash — cwd comparisons are exact.
INSTALL_DIR="$(cd "$INSTALL_DIR" 2>/dev/null && pwd)" || _die "cannot resolve INSTALL_DIR '$1'"
# Physical (symlink-free) form too: lsof reports PHYSICAL cwds (/tmp →
# /private/tmp on macOS), while launchd/cmdlines may carry the logical
# path. Ownership checks accept either form.
PHYS_DIR="$(cd -P "$INSTALL_DIR" 2>/dev/null && pwd)"
[ -n "$PHYS_DIR" ] || PHYS_DIR="$INSTALL_DIR"

# ── Source lib.sh (commission v0.16.6 component 2) ──────────────────────────
# Sourced ONLY for pipeline_settled + lock_acquire/lock_release — no other
# pipeline semantics are reached by the stop path. Sourced AFTER INSTALL_DIR
# resolution because pipeline_settled reads INSTALL_DIR/current + the
# journal path. lib.sh has no sourcing side effects beyond defining helpers
# (no journal_init / lock_acquire auto-fire on source). log tag pinned to
# stop-ensemble so _log/_warn don't print "[lib]" tags.
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_TAG="stop-ensemble"
UP_TARGET="${UP_TARGET:-stop}"
# shellcheck source=scripts/upgrade/lib.sh
. "$SELF_DIR/upgrade/lib.sh" || _die "cannot source scripts/upgrade/lib.sh"

# ── EXIT trap: release the lock on every exit path (Layer ii) ────────────────
# Mirrors lib.sh's _trap_safe_exit discipline; idempotent (lock_release is
# ownership-guarded — a non-owner's release is a no-op). When the caller
# already holds the lock (PIPELINE_LOCK_HELD_BY_CALLER=1) we skip our own
# acquire below AND the exit trap must NOT touch the lock (the caller
# owns the release; double-release would race the caller's cleanup).
# NOTE (fix-back cycle 2): when the acquire below SUCCEEDS (operator
# lane), _trap_install_signal_handlers REPLACES this EXIT trap with
# _trap_safe_exit + installs TERM/HUP/INT handlers — see the lock-acquire
# block. This trap remains the active net only for exits BEFORE the
# acquire (where it no-ops: STOP_LOCK_HELD_BY_ME=0) and for the
# caller-held lane (where it also no-ops).
_stop_ensemble_exit_trap() {
    if [ "$STOP_LOCK_HELD_BY_ME" = "1" ]; then
        lock_release || true
    fi
}
trap '_stop_ensemble_exit_trap' EXIT

# ── Layer (i) preflight — pipeline_settled (commission v0.16.6 component 2) ─
# Refuses 78 when the upgrade pipeline is not in a quiescent state. Bypassed
# by --force (operator emergency — loud warning below). PIPELINE_LOCK_HELD_BY_CALLER=1
# callers (lib.sh stop_via_stop_script inside promote/rollback) SKIP this
# check too: they were called BY the lock holder, so the pipeline is
# manifestly not settled (the lock holder IS the mutation in flight). The
# settle-check is for the OPERATOR lane where the script is a top-level
# invocation.
#
# PIPELINE_LOCK_HELD_BY_CALLER=1 is --force-equivalent in safety: any caller
# can set the env var and bypass BOTH Layer (i) settle-check AND Layer (ii)
# lock acquire. Emit a loud WARN (commission v0.16.6 c2 fix-back) so a
# misconfigured / foreign caller cannot silently sidestep the gates.
# Trusted set: ONLY lib.sh stop_via_stop_script (the only legitimate
# lock-holding caller; everything else is an operator emergency and should
# pass --force explicitly).
if [ "$PIPELINE_LOCK_HELD" = "1" ]; then
    echo "stop-ensemble: ⚠️  PIPELINE_LOCK_HELD_BY_CALLER=1 — BOTH Layer (i) settle-check AND Layer (ii) lock-acquire are being SKIPPED (equivalent to --force). This flag is ONLY for lib.sh stop_via_stop_script (in-pipeline promote/rollback/restart); any external caller can race a live flip." >&2
    echo "stop-ensemble: ⚠️  if you intended an operator emergency, prefer passing --force explicitly — the flag is auditable in shell history; the env var is not." >&2
fi
if [ "$PIPELINE_LOCK_HELD" != "1" ]; then
    if ! SETTLE_OUT="$(pipeline_settled)"; then
        if [ "$STOP_FORCE" = "1" ]; then
            echo "stop-ensemble: ⚠️  --force OVERRIDE: bypassing settle refusal ($SETTLE_OUT)" >&2
            echo "stop-ensemble: ⚠️  --force can break a running promote (the pipeline is by definition in flight). Use only for emergency recovery when the operator intends to proceed despite a half-completed promote." >&2
        else
            # Structured refusal (fix-back cycle 2, mirrors adopt-unit.sh
            # _step_pre_settle): Layer-tagged prefix + best-effort refusal
            # journal append (_refuse's ADR-034 pattern — a torn/absent
            # journal must never block the refusal itself).
            _warn "REFUSED (Layer i): pipeline is not settled ($SETTLE_OUT) — the install cannot be safely stopped while the upgrade pipeline is in flight; wait for it to settle or pass --force for operator emergencies"
            journal_history_append refusal "stop refused: pipeline not settled ($SETTLE_OUT) (reason=layer-i-pipeline-unsettled)" >/dev/null 2>&1 || true
            exit 78
        fi
    fi
fi

# ── Layer (ii) — acquire the pipeline lock around the mutate window ────────
# Skipped when PIPELINE_LOCK_HELD_BY_CALLER=1 (lib.sh stop_via_stop_script
# already holds the lock). Otherwise: acquire the same mkdir-lock the
# promote/rollback/stage scripts use (D5/D-FA5.1). Bounded wait (15s) —
# a longer hold by a concurrent action is the correct refusal. --force
# bypasses with a LOUD warning. STOP_LOCK_HELD_BY_ME arms the EXIT trap.
if [ "$PIPELINE_LOCK_HELD" != "1" ]; then
    if ! lock_acquire; then
        if [ "$STOP_FORCE" = "1" ]; then
            echo "stop-ensemble: ⚠️  --force OVERRIDE: bypassing lock-busy refusal (the lock is held by another pipeline action)" >&2
            echo "stop-ensemble: ⚠️  --force can race a live promote; expect journal divergence / sweep recovery / restart thrash" >&2
        else
            # Structured refusal (fix-back cycle 2, mirrors adopt-unit.sh
            # _step_lock): Layer-tagged prefix + best-effort refusal journal
            # append (the concurrent holder owns the mutation window — the
            # append is atomic temp+mv and best-effort by contract).
            _warn "REFUSED (Layer ii): pipeline lock busy (another promote/stage/rollback in flight) — stop-ensemble serializes on the same rollback.lock.d; wait for it to settle or pass --force for operator emergencies"
            journal_history_append refusal "stop refused: pipeline lock busy (reason=layer-ii-lock-busy)" >/dev/null 2>&1 || true
            exit 78
        fi
    else
        STOP_LOCK_HELD_BY_ME=1
        # Signal trap discipline (component 2 of r-20260928-005506-f82e,
        # fix-back cycle 2): the EXIT-only trap above does NOT fire on
        # untrapped TERM/HUP/INT — a systemd cgroup teardown of THIS
        # stop invocation would orphan the rollback.lock.d it just
        # acquired (up to LOCK_STALE_S) with no terminal journal event.
        # Install the lib.sh handlers: TERM/HUP/INT → halt journal event +
        # idempotent lock release + signal exit. Installed ONLY when this
        # invocation owns the lock: in the PIPELINE_LOCK_HELD_BY_CALLER=1
        # lane the lock belongs to the parent promote/rollback/restart,
        # whose OWN handlers already cover it — a child-side release is a
        # non-owner no-op (lock_release ownership guard) and a child-side
        # halt event would double-count against the rollback cap while
        # the parent continues live.
        _trap_install_signal_handlers "stop:lock"
        lock_heartbeat
    fi
fi

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

# _run_bounded <budget_s> [--] <cmd> [args...] — same algorithm as
# scripts/upgrade/lib.sh:2573 (2.2b, upgrade-resilience 2026-10-07):
# background the command, race a TERM→KILL watchdog, collapse 137/143
# to 124 (GNU `timeout(1)` convention). Stop-ensemble.sh is a LEAF
# script and intentionally does NOT source lib.sh (no journal access,
# no `set -e` script-wide discipline, no system-defaults), so the
# helper is duplicated locally rather than refactoring the script to
# source lib.sh. Identical contract: NEVER aborts the script on a 124;
# caller-side MUST capture with `|| rc=$?` so a bounded timeout falls
# THROUGH to the next recovery step.
#
# Cite: §9 manual-push reproduction + incident ④ — the unbounded
# `systemctl stop` at line ~530 was the ④ culprit (pre-flip freeze).
_run_bounded() {
    local timeout_s="$1"; shift
    [ "${1:-}" = "--" ] && shift
    local pid watcher rc=0 TERM_GRACE_S=5
    "$@" &
    pid=$!
    (
        sleep "$timeout_s" 2>/dev/null
        kill -TERM "$pid" 2>/dev/null
        sleep "$TERM_GRACE_S" 2>/dev/null
        kill -KILL "$pid" 2>/dev/null
    ) >/dev/null 2>&1 &
    watcher=$!
    if wait "$pid" 2>/dev/null; then
        rc=0
    else
        rc=$?
    fi
    case "$rc" in
        137|143) rc=124 ;;
    esac
    kill -KILL "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    return "$rc"
}

# STOP_SCRIPT_BUDGET_S — bound for the `systemctl stop` invocation
# below (2.2b). stop-ensemble.sh has no journal access (it is a leaf
# script with no daemon side-channel); on timeout we log loudly to
# stderr + continue. Default 120s is generous; a healthy unit stop
# completes in <2s. Env-overridable for sandbox drills + tests.
STOP_SCRIPT_BUDGET_S="${STOP_SCRIPT_BUDGET_S:-120}"

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

# ── P2 unit-aware stop (ownership-mode commission, 2026-09-29) ───────────────
# b″ DEFECT FIXED HERE: the pid poll in _stop_pids watches TODAY'S pids; a
# unit-respawned replacement pid is INVISIBLE to that poll, so the old stop
# path false-succeeds while a daemon is still alive under the unit. When
# the P1 seam classifies this install UNIT_MANAGED with a resolvable unit,
# the stop goes through the UNIT instead: `systemctl stop` (an INTENTIONAL
# stop — systemd suppresses Restart= for it, which is also why this path
# does not race the port) and verification polls UNIT STATE (is-active not
# running AND MainPID=0 AND port free), never pids.
#
# SEAM CONTRACT (P1 reuse — classification is CONSUMED, never re-derived
# here): scripts/upgrade/lib.sh stop_via_stop_script (the P1 stop site)
# hands the machine-line VALUE over as env ENSEMBLE_SUPERVISION_RESULT,
# exact machine-line grammar '<state>[:<unit>]'. ABSENT (every direct
# invocation: deploy.sh / Makefile / start.sh / operator / the ownership
# tests) → today's pid-scoped path below, byte-identical — and ZERO new
# /proc or /run/systemd reads on ANY platform: the guard short-circuits
# on the env var BEFORE the uname check (P1 guard discipline).
#
# STUB SEAM (P5): every systemctl interaction routes through SYSTEMCTL_BIN
# (default 'systemctl' — PATH-resolved, so PATH-injected stubs work exactly
# like the comp7 suite's systemctl stubs); point SYSTEMCTL_BIN at a script
# to drive scripted is-active / MainPID sequences (incl. the b″ respawn
# simulation: old pids dead, unit still active). lsof (port-free conjunct)
# is PATH-stubbable the same way; the poll budget is WAIT_S (explicit
# WAIT_S=2 keeps stub runs fast) and UNIT_KILL_GRACE_S bounds the
# post-escalation re-verify.
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
UNIT_KILL_GRACE_S="${UNIT_KILL_GRACE_S:-10}"

_unit_stopped() {
    # $1 = unit, $2 = poll port ('' = unknown). True when the unit is NOT
    # running AND MainPID is 0 AND (port unknown OR port free).
    # 'inactive' AND 'failed' both count as not-running — we verify
    # GONE-ness, not health; 'activating'/'reloading' keep waiting (a
    # unit mid-activate can hold the port). Empty is-active/MainPID
    # (D-Bus hiccup) counts as NOT stopped — fail-closed, keep polling.
    local unit="$1" port="$2" isact mp
    isact="$("$SYSTEMCTL_BIN" is-active "$unit" 2>/dev/null || true)"
    case "$isact" in
        active|activating|reloading) return 1 ;;
    esac
    mp="$("$SYSTEMCTL_BIN" show "$unit" -p MainPID --value 2>/dev/null || true)"
    [ "$mp" = "0" ] || return 1
    if [ -n "$port" ] && command -v lsof >/dev/null 2>&1; then
        lsof -ti:"$port" >/dev/null 2>&1 && return 1
    fi
    return 0
}

SU_UNIT=""
case "${ENSEMBLE_SUPERVISION_RESULT:-}" in
    UNIT_MANAGED:?*) SU_UNIT="${ENSEMBLE_SUPERVISION_RESULT#UNIT_MANAGED:}" ;;
esac
if [ -n "$SU_UNIT" ] \
   && { [ "$(uname -s)" != "Linux" ] || ! command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1; }; then
    # Non-Linux or no systemctl: today's TERM shapes stay authoritative
    # (design item 2 — BSD/macOS arms byte-identical). Degrade LOUD.
    _log "unit classification UNIT_MANAGED:$SU_UNIT present but unit path unavailable on this host — falling back to pid-scoped stop (degraded)"
    SU_UNIT=""
fi
if [ -n "$SU_UNIT" ]; then
    UNIT_POLL_PORT="$REPORT_PORT"
    [ -n "$UNIT_POLL_PORT" ] || UNIT_POLL_PORT="${PROD_PORT_HINT:-}"
    if [ "$DRY_RUN" = "1" ]; then
        _log "DRY_RUN: would systemctl stop $SU_UNIT, then poll unit state (is-active not running + MainPID=0 + port ${UNIT_POLL_PORT:-unknown} free) bounded by ${WAIT_S}s, escalating to systemctl kill"
        exit 0
    fi
    _log "unit-owned stop: systemctl stop $SU_UNIT (intentional stop — Restart= respawn suppressed; verifying UNIT STATE, not pids — b″)"
    SC_ERR="$(mktemp /tmp/.ensemble-stop-sc.XXXXXX)"
    # 2.2b (upgrade-resilience 2026-10-07) — bound the `systemctl stop`
    # (incident ④ culprit — the pre-flip freeze on this site is what
    # first surfaced the unbounded-wait family). Bounded at
    # STOP_SCRIPT_BUDGET_S (default 120s); on timeout: loud stderr +
    # fall through to the EXISTING bounded re-verify loop (:583-590)
    # and the SIGKILL escalation (:591-603) — that machinery already
    # handles a still-running unit. The daemon must never sit stopped
    # forever: if everything fails, the existing exit 1 at :606-607
    # beats an infinite stall. DRY_RUN (:571-574) stays untouched.
    if _run_bounded "$STOP_SCRIPT_BUDGET_S" -- "$SYSTEMCTL_BIN" stop "$SU_UNIT" 2>"$SC_ERR"; then
        :  # success — fall through to the bounded re-verify loop below
    else
        ssbrc=$?
        SC_FIRST="$(head -n1 "$SC_ERR" 2>/dev/null)"
        rm -f "$SC_ERR"
        if [ "$ssbrc" = "124" ]; then
            printf 'stop-ensemble: WARN: systemctl stop %s TIMED OUT (budget=%ss); child SIGTERM->SIGKILL sent — falling through to the bounded re-verify loop (an undying unit will then escalate to kill + exit 1)\n' \
                "$SU_UNIT" "$STOP_SCRIPT_BUDGET_S" >&2
        else
            _log "systemctl stop $SU_UNIT FAILED (${SC_FIRST:-no stderr}) — NOT falling back to pid TERMs under a live unit (a respawn would false-succeed the stop; b″) — failing loud"
            exit 1
        fi
    fi
    rm -f "$SC_ERR"
    WAITED=0
    while [ "$WAITED" -lt "$WAIT_S" ]; do
        if _unit_stopped "$SU_UNIT" "$UNIT_POLL_PORT"; then
            _log "unit $SU_UNIT stopped (unit not running + MainPID=0${UNIT_POLL_PORT:+ + port $UNIT_POLL_PORT free})"
            _log "done — $INSTALL_DIR is stopped (unit path)"
            exit 0
        fi
        sleep 1
        WAITED=$((WAITED + 1))
    done
    _log "unit $SU_UNIT not confirmed stopped after ${WAIT_S}s — escalating: systemctl kill (SIGKILL to the unit's cgroup)"
    "$SYSTEMCTL_BIN" kill "$SU_UNIT" 2>/dev/null || true
    # re-verify after escalation: short bounded grace (cgroup teardown
    # post-SIGKILL is fast) — deliberately NOT another WAIT_S, so the
    # total window stays ~WAIT_S + UNIT_KILL_GRACE_S.
    WAITED=0
    while [ "$WAITED" -lt "$UNIT_KILL_GRACE_S" ]; do
        if _unit_stopped "$SU_UNIT" "$UNIT_POLL_PORT"; then
            _log "unit $SU_UNIT stopped after SIGKILL escalation"
            _log "done — $INSTALL_DIR is stopped (unit path)"
            exit 0
        fi
        sleep 1
        WAITED=$((WAITED + 1))
    done
    _log "unit $SU_UNIT STILL not confirmed stopped after systemctl kill — stop NOT confirmed; failing loud (caller aborts; its txn policy owns recovery)"
    exit 1
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
