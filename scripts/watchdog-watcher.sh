#!/bin/bash
# ============================================================================
# watchdog-watcher.sh — who watches the watchdog? (Auto-Restart Phase 1, m3)
# ============================================================================
# A LOW-FREQUENCY launchd AGENT (StartInterval=300, see
# scripts/ensemble-watchdog-watcher.plist) that observes the daemon's
# /livez endpoint and NOTIFIES when the daemon has been absent for more
# than 10 minutes. This covers supervisor *misconfiguration* — the one
# failure launchd itself cannot report (plan §6 row "Watchdog dies":
# an unloaded plist, a typo'd path → the daemon is simply absent and
# nothing says so).
#
# OBSERVATION ONLY — hard safety rules:
#   * NEVER restarts, signals, or otherwise touches any process.
#   * /livez ONLY (ADR-002/003: liveness is the restart-policy input;
#     readiness NEVER triggers action — and this watcher takes none
#     anyway, it notifies).
#   * Read-only against the install except its own state file under
#     INSTALL_DIR/data/.
#
# Port resolution mirrors the launcher/stop convention (ADR-014):
# PORT from INSTALL_DIR/.env (staged from repo .env.prod), default 9797.
# An explicit second argument overrides (used by tests).
#
# State (INSTALL_DIR/data/.watchdog-state, atomic tmp+mv write):
#   first_miss_at=<epoch>   — when /livez was first seen absent
#   notified=<0|1>          — whether the >10-min notification fired
# Absent/corrupt file → fresh episode. Recovery (/livez answers again)
# clears the state and logs RECOVERED.
#
# Notification: macOS Notification Center via osascript, falling back to
# log-only when osascript is unavailable (headless host). WATCHDOG_NOTIFY_CMD
# overrides the whole notify step (tests / alternative sinks). PHASE-6 SEAM:
# when the LLM observer lands (ADR-008), the enqueue-retry for postmortems
# attaches HERE — same site, replace the notify body only.
#
# Tunables (env overrides, positive integers only):
#   WATCHDOG_ABSENT_THRESHOLD_S  (default 600 — plan: "absent >10 min")
#   WATCHDOG_PROBE_TIMEOUT_S     (default 3)
#
# Usage (normally via launchd; manual for verification):
#   bash scripts/watchdog-watcher.sh [INSTALL_DIR] [PORT]
#
# Exits 0 always (a launchd agent must not spawn retry loops of its own).
# Bash 3.2 / BSD tools compatible.
# ============================================================================

set -u

WATCHDOG_ABSENT_THRESHOLD_S="${WATCHDOG_ABSENT_THRESHOLD_S:-600}"
WATCHDOG_PROBE_TIMEOUT_S="${WATCHDOG_PROBE_TIMEOUT_S:-3}"
INSTALL_DIR="${1:-$HOME/agents-ensemble}"
PORT_OVERRIDE="${2:-}"

_log() {
    printf '%s watchdog-watcher[%s]: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$$" "$*" >&2
}

# Positive-integer guard for the tunables — malformed values must never
# turn the threshold into 0 (instant notify) or garbage arithmetic.
_posint() {
    case "$1" in
        ''|*[!0-9]*) printf '%s' "$2" ;;
        *)           printf '%s' "$1" ;;
    esac
}
WATCHDOG_ABSENT_THRESHOLD_S="$(_posint "$WATCHDOG_ABSENT_THRESHOLD_S" 600)"
WATCHDOG_PROBE_TIMEOUT_S="$(_posint "$WATCHDOG_PROBE_TIMEOUT_S" 3)"

# Absolute install dir (cwd-independent state paths).
INSTALL_DIR="$(cd "$INSTALL_DIR" 2>/dev/null && pwd)" || {
    _log "FATAL: cannot resolve INSTALL_DIR '$1'"
    exit 0
}
DATA_DIR="$INSTALL_DIR/data"
STATE_FILE="$DATA_DIR/.watchdog-state"
mkdir -p "$DATA_DIR" 2>/dev/null || {
    _log "WARN: cannot create data dir $DATA_DIR — running stateless this cycle"
    STATE_FILE=""
}

# ── Port resolution: PORT from INSTALL_DIR/.env, else 9797 (ADR-014/D1) ────
# Same tolerant parsing as stop-ensemble.sh's env reader: optional
# `export ` prefix, optional surrounding quotes, digits-only validation.
_resolve_port() {
    if [ -n "$PORT_OVERRIDE" ]; then
        printf '%s' "$PORT_OVERRIDE"
        return 0
    fi
    local env_file="$INSTALL_DIR/.env" raw=""
    raw="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}PORT[[:space:]]*=[[:space:]]*//p' "$env_file" 2>/dev/null | head -1)"
    raw="${raw%$'\r'}"
    case "$raw" in
        \"*\") raw="${raw#\"}"; raw="${raw%\"}" ;;
        \'*\') raw="${raw#\'}"; raw="${raw%\'}" ;;
    esac
    printf '%s' "$raw" | grep -Eq '^[0-9]+$' || raw=""
    if [ -z "$raw" ]; then
        printf '%s' "9797"
    else
        printf '%s' "$raw"
    fi
}
PORT="$(_resolve_port)"

# ── State I/O ───────────────────────────────────────────────────────────────
read_state() {
    FIRST_MISS_AT=0
    NOTIFIED=0
    [ -n "$STATE_FILE" ] || return 0
    [ -f "$STATE_FILE" ] || return 0
    local line key value
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            *=*) ;;
            *) continue ;;   # corrupt/garbage line → keep defaults
        esac
        key="${line%%=*}"
        value="${line#*=}"
        case "$key" in
            first_miss_at) [[ "$value" =~ ^[0-9]+$ ]] && FIRST_MISS_AT="$value" ;;
            notified)      [[ "$value" =~ ^[01]$ ]] && NOTIFIED="$value" ;;
        esac
    done < "$STATE_FILE"
    return 0
}

write_state() {
    [ -n "$STATE_FILE" ] || return 0
    local tmp="${STATE_FILE}.tmp.$$"
    printf 'first_miss_at=%s\nnotified=%s\n' "$FIRST_MISS_AT" "$NOTIFIED" \
        > "$tmp" 2>/dev/null \
        && mv -f "$tmp" "$STATE_FILE" 2>/dev/null \
        || { _log "WARN: could not persist watchdog state to $STATE_FILE"; rm -f "$tmp" 2>/dev/null; }
    return 0
}

clear_state() {
    [ -n "$STATE_FILE" ] || return 0
    rm -f "$STATE_FILE" 2>/dev/null \
        || _log "WARN: could not clear watchdog state $STATE_FILE"
    return 0
}

# ── Notify (macOS Notification Center; override seam; Phase-6 attach point) ─
_notify() {
    # $1 = subject, $2 = message
    _log "WATCHDOG[absent]: $1 — $2"
    if [ -n "${WATCHDOG_NOTIFY_CMD:-}" ]; then
        # Test/operator override: entire notify step. Never fatal.
        /bin/bash -c "$WATCHDOG_NOTIFY_CMD" >/dev/null 2>&1 </dev/null \
            || _log "WARN: WATCHDOG_NOTIFY_CMD exited nonzero"
        return 0
    fi
    if command -v osascript >/dev/null 2>&1; then
        osascript -e "display notification \"$2\" with title \"$1\"" \
            >/dev/null 2>&1 || _log "WARN: osascript notification failed (headless host?)"
    else
        _log "WARN: osascript unavailable — log-only notification"
    fi
    return 0
}

_now() { date +%s; }

# ── Probe: /livez only, short timeout, loopback ─────────────────────────────
probe_livez() {
    curl -fsS --max-time "$WATCHDOG_PROBE_TIMEOUT_S" \
        "http://127.0.0.1:${PORT}/livez" >/dev/null 2>&1
}

# ── Main ────────────────────────────────────────────────────────────────────
read_state

if probe_livez; then
    if [ "$FIRST_MISS_AT" -gt 0 ]; then
        _log "RECOVERED: /livez answering again on :$PORT (was absent since epoch $FIRST_MISS_AT)"
        clear_state
    fi
    # Healthy and no episode in flight → stay quiet (launchd log noise
    # discipline: a 300s agent that logs every run would drown the log).
    exit 0
fi

# /livez absent
NOW="$(_now)"
if [ "$FIRST_MISS_AT" -le 0 ]; then
    FIRST_MISS_AT="$NOW"
    NOTIFIED=0
    write_state
    _log "miss: /livez absent on :$PORT — watching (threshold ${WATCHDOG_ABSENT_THRESHOLD_S}s)"
    exit 0
fi

ABSENT_S=$(( NOW - FIRST_MISS_AT ))
if [ "$ABSENT_S" -gt "$WATCHDOG_ABSENT_THRESHOLD_S" ] && [ "$NOTIFIED" -eq 0 ]; then
    NOTIFIED=1
    write_state
    _notify "Ensemble daemon absent ${ABSENT_S}s (>${WATCHDOG_ABSENT_THRESHOLD_S}s)" \
        "Daemon not answering on port $PORT — check launchd (e.g. launchctl print gui/$(id -u)/com.ensemble.prod) and INSTALL_DIR: $INSTALL_DIR"
    exit 0
fi

# Still inside the threshold, or already notified — persist episode only.
write_state
exit 0
