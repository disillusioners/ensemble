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
# P2.3 B4 / T8b (ADR-025(b)) — daemon-independent alert sources: besides
# /livez, the watcher file-watches two daemon-DOWN channels, readable
# without the daemon process:
#   (a) INSTALL_DIR/.launcher-state — the launcher's burst-abort marker
#       (last_exit=1 + crash_count > budget 5, launcher.sh budget_tick/
#       write_state) — one notify per abort occurrence;
#   (b) INSTALL_DIR/releases/state.json — journal halt (halt_for_human)
#       and sweep_rollback history events (the same terminal classes the
#       in-daemon SSE emits; plain sweep-clears stay quiet, R3.4).
# All file reads are BEST-EFFORT: absent/invalid files are skipped
# silently — the watcher must survive partially-deployed envs where
# releases/ or .launcher-state do not exist yet (P2.1 pre-T4 installs).
# Watch-set paths derive from the SAME INSTALL_DIR/env as Phase 1 — no
# new defaults, no port literals; a LIVE watcher install stays USER-GATED
# (promotion ladder U6: duplicate the plist per env only on explicit
# operator action). Dedup latches live in their OWN state file
# (.watchdog-alerts-state) so /livez recovery clears the absent-episode
# state WITHOUT re-arming these livez-independent sources.
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

# Positive-integer guard for the tunables — malformed values (including
# explicit zero: 0/00/any all-zero string) must never turn the threshold
# into 0 (instant notify) or garbage arithmetic; like garbage, they fall
# back to the default.
_posint() {
    case "$1" in
        ''|*[!0-9]*) printf '%s' "$2" ;;   # empty / non-numeric → default
        *[1-9]*)     printf '%s' "$1" ;;   # has a nonzero digit → positive int
        *)           printf '%s' "$2" ;;   # all zeros (0, 00, …) → default
    esac
}
WATCHDOG_ABSENT_THRESHOLD_S="$(_posint "$WATCHDOG_ABSENT_THRESHOLD_S" 600)"
WATCHDOG_PROBE_TIMEOUT_S="$(_posint "$WATCHDOG_PROBE_TIMEOUT_S" 3)"

# Absolute install dir (cwd-independent state paths). Resolve via a
# scratch var: a failed assignment would clobber $INSTALL_DIR with the
# empty substitution, and $1 is unbound under set -u on the no-args path —
# so log pre-assignment $INSTALL_DIR, which still names the bad input.
# Either way: log the FATAL and still exit 0 (launchd contract).
_RESOLVED_INSTALL_DIR="$(cd "$INSTALL_DIR" 2>/dev/null && pwd)" || {
    _log "FATAL: cannot resolve INSTALL_DIR '$INSTALL_DIR'"
    exit 0
}
INSTALL_DIR="$_RESOLVED_INSTALL_DIR"
DATA_DIR="$INSTALL_DIR/data"
STATE_FILE="$DATA_DIR/.watchdog-state"
# B4 daemon-independent latches (ADR-025(b)) — SEPARATE from STATE_FILE:
# absent-episode recovery clears .watchdog-state, but these sources are
# livez-independent and must NOT re-arm across a /livez recovery.
ALERT_STATE_FILE="$DATA_DIR/.watchdog-alerts-state"
mkdir -p "$DATA_DIR" 2>/dev/null || {
    _log "WARN: cannot create data dir $DATA_DIR — running stateless this cycle"
    STATE_FILE=""
    ALERT_STATE_FILE=""
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
# $1 = subject, $2 = message, $3 = optional log tag (default: absent — the
# /livez absent-episode; B4 sources pass their own: launcher-abort/journal).
_notify() {
    local tag="${3:-absent}"
    _log "WATCHDOG[$tag]: $1 — $2"
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

# ── Daemon-independent alert sources (P2.3 B4 / T8b — ADR-025(b)) ──────────
# Both checks are BEST-EFFORT file reads: absent/garbage/invalid files are
# skipped SILENTLY (partial-deploy survival — see header). They never touch
# a process, never bind a port, and dedup via .watchdog-alerts-state latches
# that are written ONLY when a latch value changes (log-noise discipline).

# _file_kv <file> <key> — first `key=<value>` line's value; empty when the
# file is absent/unreadable or the key is missing.
_file_kv() {
    [ -f "$1" ] || return 0
    sed -n "s/^$2=//p" "$1" 2>/dev/null | head -1
}

read_alert_state() {
    ABORT_SEEN=0
    JOURNAL_SEEN=0
    [ -f "$ALERT_STATE_FILE" ] || return 0
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            abort_seen=*)   [[ "${line#abort_seen=}" =~ ^[0-9]+$ ]] && ABORT_SEEN="${line#abort_seen=}" ;;
            journal_seen=*) [[ "${line#journal_seen=}" =~ ^[0-9]+$ ]] && JOURNAL_SEEN="${line#journal_seen=}" ;;
        esac
    done < "$ALERT_STATE_FILE"
    return 0
}

write_alert_state() {
    [ -n "$ALERT_STATE_FILE" ] || return 0
    local tmp="${ALERT_STATE_FILE}.tmp.$$"
    printf 'abort_seen=%s\njournal_seen=%s\n' "$ABORT_SEEN" "$JOURNAL_SEEN" \
        > "$tmp" 2>/dev/null \
        && mv -f "$tmp" "$ALERT_STATE_FILE" 2>/dev/null \
        || { _log "WARN: could not persist watchdog alert state to $ALERT_STATE_FILE"; rm -f "$tmp" 2>/dev/null; }
    return 0
}

# (a) .launcher-state burst-abort marker (launcher.sh abort action persists
# last_exit=1 + crash_count > BUDGET_MAX_CRASHES(5) + last_backoff=0, then
# exits 1 staying down). One notify per abort OCCURRENCE: the latch stores
# the alerted crash_count — a deeper burst (count grows) re-notifies; a
# clean exit / fresh burst (marker predicate gone) re-arms silently.
check_burst_abort() {
    local f="$INSTALL_DIR/.launcher-state" last_exit count
    last_exit="$(_file_kv "$f" last_exit)"
    count="$(_file_kv "$f" crash_count)"
    if [ "$last_exit" = "1" ] \
       && printf '%s' "$count" | grep -Eq '^[0-9]+$' \
       && [ "$count" -gt 5 ]; then
        if [ "$count" -ne "$ABORT_SEEN" ]; then
            ABORT_SEEN="$count"
            write_alert_state
            _notify "Ensemble launcher burst-abort (staying down)" \
                "crash #${count} inside the 10-min window (budget 5) — launcher exited 1 and stays down; see $INSTALL_DIR/data/launcher.err.log" \
                "launcher-abort"
        fi
        return 0
    fi
    # marker gone → re-arm (log only when a latch actually resets)
    if [ "$ABORT_SEEN" -ne 0 ]; then
        ABORT_SEEN=0
        write_alert_state
        _log "launcher-abort marker cleared (last_exit=${last_exit:-none}, crash_count=${count:-none}) — re-armed"
    fi
    return 0
}

# (b) releases/state.json terminal events: count `halt` (halt_for_human)
# and `sweep_rollback` history entries — the watcher-side mirror of the
# in-daemon terminal classes (plain `sweep` clears and commits stay
# quiet, R3.4). Count-based latch: notify when the count GROWS (citing the
# latest matching detail); a smaller count (fresh/recreated journal) or an
# absent journal silently resets the latch. Light shape gate: JSON-invalid
# files are skipped (never parsed, never crashed on).
check_journal_events() {
    local f="$INSTALL_DIR/releases/state.json" n detail
    if [ ! -f "$f" ]; then
        if [ "$JOURNAL_SEEN" -ne 0 ]; then
            JOURNAL_SEEN=0
            write_alert_state
        fi
        return 0
    fi
    # best-effort shape gate: non-empty braced document (torn/truncated or
    # non-JSON files skip silently). Writers end the file with "}\n"
    # (journal_write printf '%s\n') — the tail check strips whitespace
    # first so the trailing newline never rejects a REAL journal.
    [ "$(head -c 1 "$f" 2>/dev/null)" = "{" ] || return 0
    case "$(tail -c 8 "$f" 2>/dev/null | tr -d '[:space:]')" in
        *'}') ;;
        *) return 0 ;;
    esac
    n="$(grep -oE '"event": *"(halt|sweep_rollback)"' "$f" 2>/dev/null | wc -l | tr -d ' ')"
    case "$n" in ''|*[!0-9]*) return 0 ;; esac
    if [ "$n" -gt "$JOURNAL_SEEN" ]; then
        detail="$(grep -oE '"event": *"halt", *"detail": *"[^"]*' "$f" 2>/dev/null | tail -1 | sed 's/.*"detail": *"//')"
        [ -n "$detail" ] || detail="$(grep -oE '"event": *"sweep_rollback", *"detail": *"[^"]*' "$f" 2>/dev/null | tail -1 | sed 's/.*"detail": *"//')"
        JOURNAL_SEEN="$n"
        write_alert_state
        _notify "Ensemble upgrade journal: ${n} halt/sweep event(s)" \
            "releases/state.json carries operator-attention events (halt_for_human / sweep_rollback)${detail:+ — latest: $detail}" \
            "journal"
    elif [ "$n" -lt "$JOURNAL_SEEN" ]; then
        JOURNAL_SEEN="$n"
        write_alert_state
    fi
    return 0
}

# ── Main ────────────────────────────────────────────────────────────────────
read_state
read_alert_state

# Daemon-independent sources run BEFORE the /livez probe and regardless of
# its outcome — they are the daemon-DOWN alert channel (ADR-025(b)); with
# no marker files present both checks are silent and write nothing.
check_burst_abort
check_journal_events

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
