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

# ── Journal sweep (ADR-012 / P2.1 T7) ──────────────────────────────────────
# These mirror the protocol constants in scripts/upgrade/lib.sh — the PROTOCOL
# is the shared contract (D5: "the protocol — not shared code — is the
# contract"); the launcher implements it SELF-CONTAINED because launcher.sh
# is staged standalone into INSTALL_DIR and never sources lib.sh.
SWEEP_STALE_S=600             # in_flight older than this = orphaned txn.
                              # This is the PRIMARY race guard (R1.3): after
                              # 10 min the owner is presumed dead — the
                              # promote outer window is also 10 min (T4). A
                              # live owner refreshes nothing here; the
                              # 600s gate is what decides.
SWEEP_COOLDOWN_S=600          # cooldown armed after a sweep-rollback (ADR-005
                              # 10-min anti-flapping). ENTRY-side only
                              # (D-FA4.2): the sweep NEVER refuses on it.
SWEEP_ROLLBACK_CAP_24H=3      # ADR-005 cap. ENTRY-side only (D-FA4.2): the
                              # sweep itself always executes past the cap —
                              # refusing the recovery would strand the env on
                              # an orphaned flip; reaching 3 arms halt +
                              # cooldown for the NEXT entry.
SWEEP_LOCK_STALE_S=300        # rollback.lock.d heartbeat staleness (D5).
SWEEP_LOCK_WAIT_S=0            # the launcher NEVER delays boot on a busy
                              # pipeline lock — if another action holds it
                              # with a fresh heartbeat, the sweep defers to
                              # the next start (availability-first).

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

# ── Journal sweep (ADR-012 / M2 / P2.1 T7) ─────────────────────────────────
# Called from the start path BEFORE resolving the binary.
#
# CONTRACT (as implemented; see phase1-plan D4/D5, decisions.md ADR-024,
# architecture-recommendation D-FA4.2/D-FA4.3):
#   Read INSTALL_DIR/releases/state.json. If an upgrade transaction is
#   `in-flight` AND older than the 10-minute rollback window
#   (now - txn.started_at > SWEEP_STALE_S):
#     - flip already happened (marker flipped:true, OR current already
#       resolves to the txn target — the atomic_flip/mark_flipped kill
#       window is healed into this branch) → launcher executes the
#       rollback itself: previous must be rollback_safe:true (D-FA4.5
#       manifest gate — else halt-for-human, NO repoint), repoint
#       `current` to `previous`, notify, escalate (counts as an
#       auto-rollback for ADR-005 cooldown/counters — ADR-024);
#     - flip never happened (current NOT at the txn target) → clear the
#       pre-flip transaction and continue.
#   The sweep runs at a layer BELOW the daemon, so it recovers even when
#   the daemon is the thing that won't boot.
#   kind=restart is NEVER launcher-swept (D-FA4.3 / R-SR13: restarts are
#   self-completing; the daemon boot sweep owns them).
#   Cap/cooldown are ENTRY-side enforcement only (D-FA4.2): the sweep
#   increments counters and arms cooldown/halt but never refuses to run —
#   refusing the recovery would strand the env on an orphaned flip.
#   Stale-snapshot guard (B2a): after acquiring the lock the sweep
#   RE-READS the journal and revalidates the txn — a txn that changed or
#   disappeared between the first read and the acquire means the owner is
#   alive and acting; the sweep releases and does nothing.
#   The sweep is fail-open for BOOT: any internal failure logs and returns
#   0 so the start path always proceeds to binary resolution.
#
# The journal accessors below implement the D4/D5 protocol SELF-CONTAINED
# (never source scripts/upgrade/lib.sh — launcher.sh is staged standalone
# into INSTALL_DIR; the protocol, not the code, is the shared contract).
# Namespaced _js_* so tests can exercise them directly.

# _js_now_iso — UTC ISO-8601 timestamp (journal fields are ISO).
_js_now_iso() {
    date -u +%Y-%m-%dT%H:%M:%SZ
}

# _js_iso_to_epoch <iso-ts> — parse journal ISO timestamps; nonzero on
# garbage (callers fail CLOSED: the sweep never fires on an unparseable
# started_at — it might be a fresh txn we cannot age).
_js_iso_to_epoch() {
    local ts="$1" epoch
    epoch="$(date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s 2>/dev/null)" || return 1
    [ -n "$epoch" ] || return 1
    printf '%s' "$epoch"
}

# _js_json_escape <string> — minimal JSON string escaper (Bash 3.2-safe).
_js_json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\t'/\\t}"
    s="${s//$'\r'/\\r}"
    printf '%s' "$s"
}

# _js_json_field <json> <key> — top-level "key": "value" / number / bool
# extractor for the FLAT journal objects (nested via _js_json_sub). Not a
# general parser: the journal is ours, its shape is the D4 contract.
_js_json_field() {
    local json="$1" key="$2" rest
    rest="${json#*\"$key\"}"
    [ "$rest" = "$json" ] && return 1
    rest="${rest#*:}"
    rest="${rest#"${rest%%[![:space:]]*}"}"
    if [ "${rest:0:1}" = "\"" ]; then
        rest="${rest:1}"                 # opening quote
        rest="${rest%%\"*}"              # up to the closing quote
    else
        rest="${rest%%\,*}"              # number / bool / null
        rest="${rest%%\}*}"
    fi
    rest="${rest#"${rest%%[![:space:]]*}"}"
    rest="${rest%"${rest##*[![:space:]]}"}"
    printf '%s' "$rest"
}

# _js_json_sub <json> <key> — extract a nested object/array value by
# bracket counting from `"key":`. Prints the raw {…} / […] text.
_js_json_sub() {
    local json="$1" key="$2" rest char depth out=""
    rest="${json#*\"$key\"}"
    [ "$rest" = "$json" ] && return 1
    rest="${rest#*:}"
    rest="${rest#"${rest%%[![:space:]]*}"}"
    case "$rest" in
        '{'*|'['*) ;;
        *) return 1 ;;
    esac
    depth=0
    while [ -n "$rest" ]; do
        char="${rest:0:1}"
        case "$char" in
            '{'|'[') depth=$((depth + 1)) ;;
            '}'|']')
                depth=$((depth - 1))
                out="$out$char"
                if [ "$depth" -eq 0 ]; then
                    printf '%s' "$out"
                    return 0
                fi
                rest="${rest:1}"
                continue
                ;;
        esac
        out="$out$char"
        rest="${rest:1}"
    done
    return 1
}

# _js_journal_read <path> — print journal JSON; nonzero on absent/unreadable/
# EMPTY or BRACE-UNBALANCED (torn write — refuse to trust, D4 discipline).
_js_journal_read() {
    local jp="$1" json
    [ -f "$jp" ] || return 1
    json="$(cat "$jp" 2>/dev/null)" || return 1
    [ -z "$json" ] && return 1
    local ob=0 cb=0 os=0 cs=0 i c in_str=0 esc=0
    for ((i = 0; i < ${#json}; i++)); do
        c="${json:i:1}"
        if [ "$esc" = "1" ]; then esc=0; continue; fi
        if [ "$c" = "\\" ]; then
            if [ "$in_str" = "1" ]; then esc=1; fi
            continue
        fi
        if [ "$c" = "\"" ]; then
            in_str=$((1 - in_str))
            continue
        fi
        [ "$in_str" = "1" ] && continue
        case "$c" in
            '{') ob=$((ob + 1)) ;;
            '}') cb=$((cb + 1)) ;;
            '[') os=$((os + 1)) ;;
            ']') cs=$((cs + 1)) ;;
        esac
    done
    if [ "$ob" != "$cb" ] || [ "$os" != "$cs" ] || [ "$in_str" = "1" ]; then
        return 1
    fi
    printf '%s' "$json"
}

# _js_journal_write <path> <json> — ATOMIC: temp file in the same dir +
# mv -f (D4 / .launcher-state discipline).
_js_journal_write() {
    local jp="$1" json="$2" tmp
    tmp="$jp.tmp.$$"
    if ! printf '%s\n' "$json" > "$tmp" 2>/dev/null; then
        rm -f "$tmp" 2>/dev/null
        return 1
    fi
    if ! mv -f "$tmp" "$jp" 2>/dev/null; then
        rm -f "$tmp" 2>/dev/null
        return 1
    fi
    return 0
}

# _js_journal_update <path> <field> <raw-json-or-scalar> — read-modify-write
# ONE top-level field atomically; <raw> spliced verbatim (strings arrive
# pre-quoted, objects/numbers/bools/null raw).
_js_journal_update() {
    local jp="$1" field="$2" raw="$3" json rest head out="" c depth
    json="$(_js_journal_read "$jp")" || return 1
    case "$json" in
        *"\"$field\""*) ;;
        *) return 1 ;;
    esac
    # Walk the object; replace the value that follows "field":.
    rest="$json"
    while [ -n "$rest" ]; do
        case "$rest" in
            *"\"$field\""*)
                head="${rest%%\"$field\"*}\"$field\""
                rest="${rest#*\"$field\"}"
                rest="${rest#*:}"
                # drop leading whitespace of the old value
                rest="${rest#"${rest%%[![:space:]]*}"}"
                # consume the old value by bracket/quote scanning
                depth=0
                if [ "${rest:0:1}" = "\"" ]; then
                    # string value: skip to closing quote (escape-aware)
                    rest="${rest:1}"
                    while [ -n "$rest" ]; do
                        c="${rest:0:1}"
                        if [ "$c" = "\\" ]; then rest="${rest:2}"; continue; fi
                        [ "$c" = "\"" ] && { rest="${rest:1}"; break; }
                        rest="${rest:1}"
                    done
                else
                    while [ -n "$rest" ]; do
                        c="${rest:0:1}"
                        case "$c" in
                            '{'|'[') depth=$((depth + 1)) ;;
                            '}'|']')
                                if [ "$depth" -eq 0 ]; then break; fi
                                depth=$((depth - 1)) ;;
                            ',')
                                [ "$depth" -eq 0 ] && break ;;
                        esac
                        rest="${rest:1}"
                    done
                fi
                out="$head: $raw$rest"
                break
                ;;
            *)
                break
                ;;
        esac
    done
    [ -n "$out" ] || return 1
    _js_journal_write "$jp" "$out"
}

# _js_history_append <path> <event> <detail> — append {ts,event,detail} to
# history (newest last, D4).
_js_history_append() {
    local jp="$1" event="$2" detail="$3" json hist new
    json="$(_js_journal_read "$jp")" || return 1
    hist="$(_js_json_sub "$json" "history")"
    if [ -z "$hist" ] || [ "$hist" = "[]" ]; then
        new="[{\"ts\":\"$(_js_now_iso)\",\"event\":\"$(_js_json_escape "$event")\",\"detail\":\"$(_js_json_escape "$detail")\"}]"
    else
        new="${hist%]}, {\"ts\":\"$(_js_now_iso)\",\"event\":\"$(_js_json_escape "$event")\",\"detail\":\"$(_js_json_escape "$detail")\"}]"
    fi
    _js_journal_update "$jp" "history" "$new"
}

# _js_count_rollback <path> — increment rollback_window_count with 24h
# window rollover (a window_start >24h old resets the count; this rollback
# re-opens the window) and arm cooldown_until = now + SWEEP_COOLDOWN_S
# (ADR-024: sweep rollbacks count + cooldown). Prints the new count.
_js_count_rollback() {
    local jp="$1" json counts cnt wstart wstart_epoch new_cnt until
    json="$(_js_journal_read "$jp")" || return 1
    counts="$(_js_json_sub "$json" "rollback_window_count")"
    cnt="$(_js_json_field "$counts" "24h" 2>/dev/null)"
    wstart="$(_js_json_field "$counts" "window_start")"
    [ -n "$cnt" ] || cnt=0
    case "$wstart" in ""|null) wstart="" ;; esac
    if [ -n "$wstart" ] && wstart_epoch="$(_js_iso_to_epoch "$wstart")" 2>/dev/null \
       && [ "$(($(_now) - wstart_epoch))" -ge 86400 ]; then
        cnt=0   # window rolled over — this rollback re-opens it
    fi
    new_cnt=$((cnt + 1))
    _js_journal_update "$jp" "rollback_window_count" \
        "{\"24h\": $new_cnt, \"window_start\": \"$(_js_now_iso)\"}" || return 1
    until="$(date -jur "$(( $(_now) + SWEEP_COOLDOWN_S ))" +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" \
        || until="$(_js_now_iso)"
    _js_journal_update "$jp" "cooldown_until" "\"$until\"" || return 1
    printf '%s' "$new_cnt"
}

# _js_quarantine <path> <ver> — append to quarantined[] (idempotent).
_js_quarantine() {
    local jp="$1" ver="$2" json q new
    json="$(_js_journal_read "$jp")" || return 1
    q="$(_js_json_sub "$json" "quarantined")"
    case "$q" in
        *"\"$ver\""*) return 0 ;;
    esac
    if [ -z "$q" ] || [ "$q" = "[]" ]; then
        new="[\"$ver\"]"
    else
        new="${q%]}, \"$ver\"]"
    fi
    _js_journal_update "$jp" "quarantined" "$new"
}

# ── rollback.lock.d — mkdir-lock, D5 protocol (self-contained) ─────────────
# mkdir IS the atomic acquire (portable; no flock on stock macOS). Contents:
# owner (pid), heartbeat (epoch). Stale: heartbeat older than
# SWEEP_LOCK_STALE_S or dead owner pid → mv to rollback.lock.d.stale.<pid>
# (never rmdir — racy) → re-acquire. Bounded wait; the launcher passes
# wait=0 (never delay boot — defer to the next start instead).

_js_lock_dir() {
    printf '%s/releases/rollback.lock.d' "$1"
}

# _js_lock_acquire <install_dir> [wait_s] — 0 acquired, 1 busy.
_js_lock_acquire() {
    local install_dir="$1" wait_s="${2:-$SWEEP_LOCK_WAIT_S}"
    local lock hb owner_pid run_id age waited=0
    lock="$(_js_lock_dir "$install_dir")"
    mkdir -p "$(dirname "$lock")" 2>/dev/null
    while :; do
        if mkdir "$lock" 2>/dev/null; then
            printf '%s\n' "$$" > "$lock/owner" 2>/dev/null
            # run_id: the D5 protocol file lib.sh writes on acquire —
            # without it a launcher-held lock degrades every lib.sh
            # stale-break/pipeline-busy diagnostic to run_id=? and
            # status.sh shows an empty run (review m1).
            printf '%s\n' "run-$(date +%Y%m%d-%H%M%S)-$$" > "$lock/run_id" 2>/dev/null
            printf '%s\n' "$(_now)" > "$lock/heartbeat" 2>/dev/null
            return 0
        fi
        # exists — stale? heartbeat > SWEEP_LOCK_STALE_S → break it (mv)
        hb="$(cat "$lock/heartbeat" 2>/dev/null)"
        owner_pid="$(cat "$lock/owner" 2>/dev/null)"
        run_id="$(cat "$lock/run_id" 2>/dev/null)"
        if printf '%s' "$hb" | grep -Eq '^[0-9]+$'; then
            age=$(( $(_now) - hb ))
            if [ "$age" -gt "$SWEEP_LOCK_STALE_S" ]; then
                _log "journal sweep: pipeline lock stale (heartbeat ${age}s old, owner pid ${owner_pid:-?}, run ${run_id:-?}) — breaking"
                mv "$lock" "${lock}.stale.$$" 2>/dev/null || continue
                continue
            fi
        fi
        # owner process dead? (crash left a fresh-heartbeat dir) — break too
        if [ -n "$owner_pid" ] && printf '%s' "$owner_pid" | grep -Eq '^[0-9]+$' \
           && ! kill -0 "$owner_pid" 2>/dev/null; then
            _log "journal sweep: pipeline lock owner pid $owner_pid is dead — breaking lock"
            mv "$lock" "${lock}.stale.$$" 2>/dev/null || continue
            continue
        fi
        if [ "$waited" -ge "$wait_s" ]; then
            _log "journal sweep: pipeline lock held (owner pid ${owner_pid:-?}, heartbeat fresh) — deferring sweep to next start"
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
}

# _js_lock_release <install_dir> — remove the lock dir only if we own it.
_js_lock_release() {
    local lock owner
    lock="$(_js_lock_dir "$1")"
    if [ -d "$lock" ]; then
        owner="$(cat "$lock/owner" 2>/dev/null)"
        if [ "$owner" = "$$" ]; then
            rm -rf "$lock"
        fi
    fi
    return 0
}

# _js_flip_current <install_dir> <ver> — rename(2)-semantics symlink flip:
# build current.new.$$ then mv -hf over `current` (the mv is the atomic
# point). Target is RELATIVE ("releases/<ver>") so the layout stays
# relocatable; the symlink lives at the INSTALL ROOT — exactly what
# resolve_binary expects ($INSTALL_DIR/current/ensemble-prod, ADR-004).
# NOTE: plain `mv -f` is WRONG here — BSD mv follows a symlink-to-directory
# DEST and would move the temp link INSIDE the target release; `-h` ("do
# not follow it... rename the file source to the destination path") is
# what makes this a true atomic replace (mv -T is GNU-only).
_js_flip_current() {
    local install_dir="$1" ver="$2"
    ln -sfn "releases/$ver" "$install_dir/current.new.$$" 2>/dev/null || return 1
    if ! mv -hf "$install_dir/current.new.$$" "$install_dir/current" 2>/dev/null; then
        rm -f "$install_dir/current.new.$$" 2>/dev/null
        return 1
    fi
    return 0
}

# _js_manifest_field <install_dir> <ver> <key> — top-level release manifest
# field. Same semantics as lib.sh manifest_field (the protocol, not the
# code, is the shared contract): nonzero on missing/unreadable manifest or
# absent key. Callers fail closed on EVERY unreadable shape (D-FA4.5).
_js_manifest_field() {
    local mp json
    mp="$1/releases/$2/manifest.json"
    [ -f "$mp" ] || return 1
    json="$(cat "$mp" 2>/dev/null)" || return 1
    _js_json_field "$json" "$3"
}

# ── The sweep itself ────────────────────────────────────────────────────────
_journal_sweep() {
    local install_dir="${1:-${INSTALL_DIR:-}}"
    [ -n "$install_dir" ] || return 0
    local journal="$install_dir/releases/state.json"
    [ -f "$journal" ] || return 0

    local json
    json="$(_js_journal_read "$journal")" || {
        _log "WARN: journal sweep: $journal unreadable or torn (unbalanced) — refusing to trust it, boot proceeds"
        return 0
    }

    local inf
    inf="$(_js_json_sub "$json" "in_flight")"
    # absent (null / not an object) → nothing in flight, no-op
    [ -n "$inf" ] || return 0
    case "$inf" in ''|null) return 0 ;; esac

    # A txn object must carry a kind (D4); without it the record is not one
    # of ours — fail closed, leave it alone.
    local kind target started flipped owner
    kind="$(_js_json_field "$inf" "kind" 2>/dev/null)" || kind=""
    [ -n "$kind" ] || {
        _log "WARN: journal sweep: in_flight record without kind — leaving untouched, boot proceeds"
        return 0
    }

    # D-FA4.3 / R-SR13: restart-kind pending-ops are NEVER launcher-swept
    # (self-completing; the daemon boot sweep owns them).
    if [ "$kind" = "restart" ]; then
        _log "journal sweep: in_flight kind=restart — daemon boot sweep owns restarts (D-FA4.3), leaving untouched"
        return 0
    fi

    target="$(_js_json_field "$inf" "target" 2>/dev/null)" || target=""
    started="$(_js_json_field "$inf" "started_at" 2>/dev/null)" || started=""
    flipped="$(_js_json_field "$inf" "flipped" 2>/dev/null)" || flipped=""
    owner="$(_js_json_field "$inf" "owner_pid" 2>/dev/null)" || owner=""

    # Age the txn. Unparseable started_at → FAIL CLOSED (never fire on a
    # txn we cannot age — it may be fresh).
    local started_epoch age
    if ! started_epoch="$(_js_iso_to_epoch "$started")" 2>/dev/null; then
        _log "WARN: journal sweep: in_flight started_at unparseable ('$started') — leaving untouched, boot proceeds"
        return 0
    fi
    age=$(( $(_now) - started_epoch ))

    # Fresh txn (≤ SWEEP_STALE_S) → the owner may still be alive. Leave it.
    if [ "$age" -le "$SWEEP_STALE_S" ]; then
        _log "journal sweep: in_flight $kind txn (target=${target:-?}) is fresh (${age}s ≤ ${SWEEP_STALE_S}s) — leaving alone"
        return 0
    fi

    # Stale (> SWEEP_STALE_S) → owner presumed dead (R1.3: the 600s gate is
    # the primary race guard). All mutations below serialize on the D5 lock;
    # a busy-but-live pipeline (fresh heartbeat / live owner) makes the
    # sweep DEFER (return 0) rather than block boot.
    if ! _js_lock_acquire "$install_dir"; then
        return 0
    fi

    # Re-read the journal UNDER the lock and revalidate the txn (B2a —
    # mirrors rollback.sh's re-read-under-lock). The snapshot above was
    # taken BEFORE the acquire; a promote/rollback that completed, cleared,
    # or replaced its txn in that window must not be undone by mutations
    # below (flip-back of a good current, phantom quarantine, phantom
    # counter/cooldown). The txn OBJECT is the identity: any field change
    # (kind/target/started_at/flipped/owner_pid) or disappearance → the
    # owner is alive and acting — do nothing, release, boot proceeds.
    local json2 inf2
    if ! json2="$(_js_journal_read "$journal")"; then
        _log "WARN: journal sweep: journal became unreadable under the lock — refusing to act on the stale snapshot, boot proceeds"
        _js_lock_release "$install_dir"
        return 0
    fi
    inf2="$(_js_json_sub "$json2" "in_flight")"
    if [ "$inf2" != "$inf" ]; then
        _log "journal sweep: in_flight changed under the lock (owner still alive — txn completed or replaced) — NOT acting on the stale snapshot"
        _js_lock_release "$install_dir"
        return 0
    fi
    json="$json2"   # mutations below read other fields (previous, …) fresh

    # Kill-window heal (promote.sh atomic_flip ↔ journal_mark_flipped): if
    # promote dies between the flip and the marker, the journal says
    # flipped:false while `current` ALREADY resolves to releases/$target.
    # Trust the symlink over the marker: treating that txn as pre-flip
    # would clear it and boot the ungated, never-verified release. If
    # current already points at the txn target, run the rollback branch
    # instead (this also heals the handled mark_flipped-failure path).
    local cur_link
    cur_link="$(readlink "$install_dir/current" 2>/dev/null)" || cur_link=""
    if [ "$flipped" != "true" ] && [ -n "$target" ] \
       && [ "$cur_link" = "releases/$target" ]; then
        _log "journal sweep: flipped marker is false but current already -> releases/$target (owner died in the atomic_flip/mark_flipped window) — treating as flipped"
        flipped="true"
    fi

    local rc=0
    if [ "$flipped" = "true" ]; then
        # ── Sweep-rollback (ADR-012 / ADR-024 / D-FA4.2) ────────────────────
        # Flip-first ordering: if we die mid-sequence the next start re-runs
        # the sweep on the same stale txn; every step is idempotent except
        # the counter increment (which can only over-count — conservative,
        # anti-flapping direction). Journal-first would strand the env on
        # the orphaned flip — exactly what D-FA4.2 forbids.
        local prev
        prev="$(_js_json_field "$json" "previous" 2>/dev/null)" || prev=""
        case "$prev" in ''|null)
            _notify_once "sweep-halt" \
                "HALT-FOR-HUMAN: stale flipped $kind txn (target=${target:-?}) but journal has no previous release — cannot sweep-rollback; boot proceeds on current; see $install_dir/releases/state.json"
            _js_history_append "$journal" "halt" \
                "sweep: stale flipped $kind txn target=${target:-?} has no previous to roll back to — halt-for-human, txn left in place" \
                || rc=1
            _log "HALT: journal sweep: flipped txn (age ${age}s) with no previous — halt-for-human, boot proceeds on current"
            _js_lock_release "$install_dir"
            return 0
            ;;
        esac
        # M4 quarantine gate: a QUARANTINED previous is a known-bad release
        # (it failed a gate before) — the sweep must never flip onto it.
        # Same halt shape as the other gates (notify + halt event, NO
        # repoint, txn left in place, boot proceeds on current). Guards a
        # stranded/hand-edited journal: promote's rollback bookkeeping no
        # longer writes previous==quarantined, but consumption fails closed
        # here regardless.
        local prev_q
        prev_q="$(_js_json_sub "$json" "quarantined" 2>/dev/null)" || prev_q=""
        case "$prev_q" in
            *"\"$prev\""*)
                _notify_once "sweep-halt" \
                    "HALT-FOR-HUMAN: stale flipped $kind txn (target=${target:-?}) but previous release $prev is QUARANTINED (known-bad — M4) — refusing to sweep-rollback onto it; boot proceeds on current; see $install_dir/releases/state.json"
                _js_history_append "$journal" "halt" \
                    "sweep: previous release $prev is QUARANTINED (known-bad) — halt-for-human, NO repoint, txn left in place (M4)" \
                    || rc=1
                _log "HALT: journal sweep: previous $prev is quarantined (known-bad) — halt-for-human, boot proceeds on current (M4)"
                _js_lock_release "$install_dir"
                return 0
                ;;
        esac
        if [ ! -d "$install_dir/releases/$prev" ]; then
            _notify_once "sweep-halt" \
                "HALT-FOR-HUMAN: stale flipped $kind txn (target=${target:-?}) but previous release dir releases/$prev is missing (manual deletion?) — cannot sweep-rollback; boot proceeds on current; see $install_dir/releases/state.json"
            _js_history_append "$journal" "halt" \
                "sweep: previous release $prev missing (manual deletion?) — halt-for-human, txn left in place" \
                || rc=1
            _log "HALT: journal sweep: previous release $prev missing — halt-for-human, boot proceeds on current"
            _js_lock_release "$install_dir"
            return 0
        fi

        # Manifest gate (ADR-005 M5 / D-FA4.5): previous must be
        # rollback_safe:true — the SAME gate promote auto-rollback, manual
        # rollback.sh, and adopt_stale_txn enforce. Missing/unreadable
        # manifest, absent field, or any value ≠ true → halt-for-human:
        # NO repoint (never boot the old binary against a possibly
        # migrated DB — risk R1.2), journal halt event, txn left in place.
        local prev_safe
        prev_safe="$(_js_manifest_field "$install_dir" "$prev" rollback_safe 2>/dev/null)" \
            || prev_safe=""
        if [ "$prev_safe" != "true" ]; then
            _notify_once "sweep-halt" \
                "HALT-FOR-HUMAN: stale flipped $kind txn (target=${target:-?}) but previous release $prev is NOT rollback_safe (${prev_safe:-missing manifest}) — refusing to sweep-rollback into schema drift (D-FA4.5); boot proceeds on current; see $install_dir/releases/state.json"
            _js_history_append "$journal" "halt" \
                "sweep: previous release $prev has rollback_safe=${prev_safe:-missing} (D-FA4.5 schema-drift guard) — halt-for-human, txn left in place" \
                || rc=1
            _log "HALT: journal sweep: previous $prev not rollback_safe (${prev_safe:-missing}) — halt-for-human, boot proceeds on current"
            _js_lock_release "$install_dir"
            return 0
        fi

        # NOTE (D-FA4.1, Batch C): this sweep-rollback mutates current + the
        # journal but does NOT restore the previous release's launcher.sh —
        # promote's auto-rollback and manual rollback.sh both launcher_swap
        # in the stopped window, the sweep deliberately cannot: it runs
        # INSIDE launcher.sh, and overwriting the script under a running
        # bash corrupts its lazy reads mid-execution. The residual skew
        # (new launcher booting the rolled-back old release) is benign per
        # D-FA4.1: the running launcher has already proven it boots this
        # install, and the next successful promote re-syncs launcher↔binary
        # in its stopped window. The swap-on-rollback policy difference is
        # documented at each site (promote.sh 8b / rollback.sh / here).
        if ! _js_flip_current "$install_dir" "$prev"; then
            _log "WARN: journal sweep: could not repoint current to $prev — txn left in place for the next sweep, boot proceeds"
            _js_lock_release "$install_dir"
            return 0
        fi
        _log "journal sweep: STALE flipped $kind txn (age ${age}s, target=${target:-?}, owner pid ${owner:-?}) — rolled back: current -> releases/$prev"

        # Journal mutations (atomic temp+mv each, D4). Failures degrade to
        # WARNs — the flip already happened; the env must still boot.
        _js_quarantine "$journal" "$target" \
            || { _log "WARN: journal sweep: quarantine append failed"; rc=1; }
        _js_journal_update "$journal" "current" "\"$prev\"" \
            || { _log "WARN: journal sweep: journal current update failed"; rc=1; }
        local new_cnt=""
        new_cnt="$(_js_count_rollback "$journal")" \
            || { _log "WARN: journal sweep: rollback counter/cooldown update failed"; rc=1; }
        _js_history_append "$journal" "sweep_rollback" \
            "sweep: orphaned flipped $kind txn (target=${target:-?}, owner pid ${owner:-?}, age ${age}s) rolled back to $prev; counted as auto-rollback (ADR-024)" \
            || { _log "WARN: journal sweep: history append failed"; rc=1; }
        _js_journal_update "$journal" "in_flight" "null" \
            || { _log "WARN: journal sweep: in_flight clear failed"; rc=1; }

        # Cap is ENTRY-side (D-FA4.2): never refused above, but reaching it
        # arms halt + cooldown for the NEXT promote.
        if [ -n "$new_cnt" ] && [ "$new_cnt" -ge "$SWEEP_ROLLBACK_CAP_24H" ]; then
            _js_history_append "$journal" "halt" \
                "sweep-rollback reached cap $SWEEP_ROLLBACK_CAP_24H/24h (count=$new_cnt) — promotes refused until the window resets or an operator intervenes" \
                || rc=1
            _notify_once "sweep-halt" \
                "HALT-FOR-HUMAN: rollback cap $SWEEP_ROLLBACK_CAP_24H/24h reached by sweep-rollback (count=$new_cnt) — promotes refused until the 24h window resets; see $install_dir/releases/state.json"
            _log "HALT: sweep-rollback reached rollback cap ($new_cnt/$SWEEP_ROLLBACK_CAP_24H in 24h) — next promotes refused (entry-side)"
        fi
        _notify_once "sweep-rollback" \
            "sweep-rollback executed: orphaned flipped txn (age ${age}s) rolled current back to $prev (from target ${target:-?}); counted toward the 3/24h cap + cooldown armed (ADR-024)"
    else
        # ── Stale pre-flip txn → clear, boot proceeds ────────────────────────
        _log "journal sweep: stale pre-flip $kind txn (age ${age}s, target=${target:-?}, owner pid ${owner:-?}) — clearing, boot proceeds"
        _js_history_append "$journal" "sweep" \
            "sweep: orphaned pre-flip $kind txn (target=${target:-?}, owner pid ${owner:-?}, age ${age}s) cleared — never flipped" \
            || { _log "WARN: journal sweep: history append failed"; rc=1; }
        _js_journal_update "$journal" "in_flight" "null" \
            || { _log "WARN: journal sweep: in_flight clear failed"; rc=1; }
    fi

    _js_lock_release "$install_dir"
    if [ "$rc" -ne 0 ]; then
        _log "WARN: journal sweep completed with journal-mutation warnings — inspect $journal"
    fi
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
# prev ≤ 0 restarts at the family base. Both tracks share one shape so the
# mapping is fully determined by (prev, code).
#
# prev is FAMILY-SCOPED: callers must pass the persisted backoff only when
# the previous cycle's verdict family matches the current one — see
# effective_prev_backoff() below (review m3). Raw cross-family doubling
# would turn a 75-track prev of 60 into a 120s first crash backoff (12× the
# 10s spec base).
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

# effective_prev_backoff <prev_exit> <current_code> <persisted_backoff>
#   → echoes the persisted backoff if the previous cycle's verdict family
#     (classify_exit of prev_exit) matches the current one, else 0.
# Track-switch reset (review m3): STATE_LAST_BACKOFF persists across verdict
# families, but the two backoff ladders are independent — a 75-track prev of
# 60 must NOT seed a crash restart at 120s, and a crash prev of 300 must not
# start the 75 track at its 60s cap (the crash→75 direction is masked by the
# cap but equally wrong). Families come from the same classify_exit() that
# drives the run-loop verdicts, so a previous clean (0) or refuse (78) exit
# also resets: those verdicts exit the loop, making any persisted backoff
# stale by definition.
effective_prev_backoff() {
    local prev_exit="$1" code="$2" persisted="$3"
    if [ "$(classify_exit "$prev_exit")" = "$(classify_exit "$code")" ]; then
        printf '%s\n' "$persisted"
    else
        printf '%s\n' 0
    fi
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
        # P5b dedupe: only warn when this is the sole non-executable report
        # (via_current absent — otherwise its WARN above already fired).
        [ ! -e "$via_current" ] && _log "WARN: $flat exists but is not executable"
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
    local new_count=0 new_window=0 backoff=0 reaped=0 prev=0 prev_exit=""

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

        # Capture the PREVIOUS cycle's exit BEFORE overwriting it — the
        # family-scoped backoff reset below needs it (review m3).
        prev_exit="${STATE_LAST_EXIT:-}"
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
                # Family-scoped prev (review m3): a crash-track backoff must
                # not seed the 75 ladder (and vice versa).
                prev="$(effective_prev_backoff "$prev_exit" 75 "${STATE_LAST_BACKOFF:-0}")"
                backoff="$(next_backoff "$prev" 75)"
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

                # Family-scoped prev (review m3): a 75-track backoff must not
                # seed the crash ladder (and vice versa). Uses prev_exit —
                # captured BEFORE STATE_LAST_EXIT was overwritten — NOT
                # STATE_LAST_EXIT, which already holds THIS cycle's exit and
                # would make the family comparison a tautology.
                prev="$(effective_prev_backoff "$prev_exit" "$child_exit" "${STATE_LAST_BACKOFF:-0}")"
                backoff="$(next_backoff "$prev" "$child_exit")"
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
