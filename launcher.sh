#!/bin/bash
# ============================================================================
# launcher.sh — Ensemble production supervisor wrapper (Auto-Restart Phase 1)
# ============================================================================
# Runs UNDER launchd (macOS) / systemd (Linux). No TTY, no shell env — every
# path is absolute and derived from this script's own location.
#
# Size rationale (~1660 lines): this file is the whole supervisor in one
# sourced unit BY CONTRACT — launchd/systemd exec a single interpreter
# and the ADR-012 journal sweep + _pid_alive twin (byte-identical to
# lib.sh's body, 11d pin in tests/test_launcher.sh) must live where the
# restart loop lives. Growth here = state-file round-trip, backoff
# classes, env parsing, and the bounded-subprocess waits (W5) — all
# restart-loop-owned. Do NOT split: the twin-body pin and the source-
# guard contract (`sourcing must not run anything`) span the file.
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
HEARTBEAT_STALE_S=300          # mirror of lib.sh HEARTBEAT_STALE_S (lib.sh
                              # constant block) — journal in_flight.last_
                              # heartbeat older than this is stale. Used by
                              # _journal_sweep's comp3 liveness fast path
                              # (mirror-discipline: both sweep tables must
                              # agree, R-SR13 / lib.sh adopt_stale_txn
                              # comp3 block commentary).

# ── Phase 2 (promote-handback-halt): the serving-green stale-txn closer ──
# The `:=` form (env-overridable) — NOT the plain-assignment form of the
# mirrored constants above: the mirrored ones CANNOT be overridden (the
# sourced launcher overwrites them), so a pin that must drive them has no
# seam. The precedent for the `:=` form in this file is
# `: "${MALLOC_ARENA_MAX:=2}"`.
: "${SWEEP_DEFER_CEILING:=3}"    # the BOUNDED ABSENT-CEILING: the number of
                              # qualifying AMBIGUOUS observations after which
                              # the absent-class rollback becomes eligible.
                              # Mirrors the house "three strikes" flavour
                              # (SWEEP_ROLLBACK_CAP_24H=3 above) and gives
                              # the daemon-side closer (which acts at the
                              # first tick past the 600s staleness gate)
                              # ample room to win the race.
: "${SWEEP_SERVING_GREEN_ENABLED:=1}"  # the LAUNCHER-side kill-switch of
                              # this lane (REV-R-3 — ONE sentence, both
                              # sides): =0 restores today's UNCONDITIONAL
                              # auto-rollback disposition; =1 (the default)
                              # installs the new ladder. `=0` is an escape
                              # hatch for an unverified platform — NEVER a
                              # permanent state. The daemon-side twin is
                              # ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER (read
                              # per tick; default 1).
: "${SWEEP_SERVING_PROBE_BUDGET_S:=5}"  # the serving probe's TOTAL budget
                              # (seconds) — A3: bounded `read -t` only, no
                              # unbounded socket read (the sweep-hang class
                              # of 2026-10-01 must not be reintroduced on
                              # the boot path).
: "${SWEEP_JOURNAL_SIZE_GUARD_BYTES:=98304}"  # the LAUNCHER-side mirror of
                              # lib.sh's JOURNAL_SIZE_GUARD_BYTES (98304).
                              # The launcher has NO size guard today and
                              # the drafted history-count reader measured
                              # 13.5s / 35.7s at 27KB (≈285× at the
                              # incident size) ⇒ a breach here degrades to
                              # the CONSERVATIVE defer/halt path — NEVER a
                              # rollback (the daemon-side closer, which
                              # parses the journal in-process, is the only
                              # automatic rollback path in that regime).

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
#
# Portability (fix/portable-iso-parse-linux, 2026-09-28, r-f82e §3.9
# #1): mirror of lib.sh's _iso_to_epoch — uname dispatch between BSD
# ``date -ju -f …`` and GNU ``date -d …``. Same rationale, same
# precedent (atomic_flip 47630be0). The launcher's _journal_sweep is
# the ONE consumer that runs on every boot; an unparseable started_at
# here = the boot sweep fails closed on every startup, an unfixable
# orphan-txn wedge that nothing else recovers. Verified on macOS
# date(1) and GNU coreutils 9.x (Linux).
# Bounded 3s date parse (fix #4, v0.16.9): a wedged strftime / hung
# gettimeofday on a clock anomaly could pin the sweep forever — the
# 2026-10-01 attempt-1 sweep hang defense layer.
_js_iso_to_epoch() {
    local ts="$1" epoch
    case "$(uname -s)" in
        Darwin|*BSD*|*bsd*)
            epoch="$(_js_run_bounded 3 -- date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s)" || return 1
            ;;
        Linux|GNU*|*GNU*)
            epoch="$(_js_run_bounded 3 -- date -d "$ts" +%s)" || return 1
            ;;
        *)
            echo "_js_iso_to_epoch: unrecognized platform '$(uname -s)' — refusing (fail-closed; BSD or Linux required)" >&2
            return 1
            ;;
    esac
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
# Bounded 3s cat read (fix #4, v0.16.9): if the journal file is on a hung
# NFS / FIFO / kernel wedge, the previous unbounded `cat` could pin the
# sweep forever — the 2026-10-01 attempt-1 sweep hang. On timeout: sweep
# returns nonzero; the txn is left OPEN for the next start.
_js_journal_read() {
    local jp="$1" json
    [ -f "$jp" ] || return 1
    json="$(_js_run_bounded 3 -- cat "$jp")" || return 1
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

# _js_history_has_intent_flip <journal> <target> — returns 0 iff the journal
# history contains an `intent_flip` event whose `detail` names <target>
# (2.2c, upgrade-resilience 2026-10-07). The shell twin's BOOT-side
# evidence check for the boot_sweep_commit_and_continue branch:
# promote.sh emits intent_flip AFTER both integrity verifications
# (CURRENT drift + TARGET match) pass, BEFORE any mutation, so its
# presence in history proves the operator passed preflight + the
# intent+integrity contract. The walker is intentionally narrow:
# matches on the JSON `event` field being exactly "intent_flip" AND
# the detail carrying the `target $VERSION` substring
# (`preflight: intentional promote target $VERSION — …`) — the
# fingerprint is unambiguous because promote's emit uses the exact
# string shape. No fuzzy match — a missing target leaves the boot at
# the conservative halt gates.
#
# History entries are {ts,event,detail}; `_js_json_sub` extracts the
# array as raw text. We DO NOT parse JSON in shell (fragile); instead
# we use a `case` glob with the conjunction of the two substrings.
# Both substrings must appear in the SAME history array; false
# positives require a hand-edit that puts both strings in the array —
# the operator's intentionality is the sole producer (per the
# writer-side comment in promote.sh).
_js_history_has_intent_flip() {
    local jp="$1" target="$2" json hist pattern
    [ -n "$target" ] || return 1
    json="$(_js_journal_read "$jp")" || return 1
    hist="$(_js_json_sub "$json" "history")"
    case "$hist" in
        ''|null|\[\]) return 1 ;;
    esac
    # Build the conjunction pattern in a variable (case-glob expansion
    # of inline literal escapes is fragile; a variable keeps the `*` and
    # the `\"` stable). The pattern requires the array to carry BOTH
    # the event-kind literal AND the `target <VERSION>` substring —
    # promote's emit prefix is byte-identical (see promote.sh 2.2c).
    pattern='*"event":"intent_flip"*target '"$target"'*'
    case "$hist" in
        $pattern) return 0 ;;
    esac
    return 1
}

# ── Phase 2 (promote-handback-halt): the serving-green stale-txn closer ────
#
# The incident this exists for: run r-20261101-212542-8279 — a promote
# HALTED at hand-back (Phase 1 owns that verifier), leaving the txn OPEN
# with flipped=true; the target was SERVING ALL-GREEN; the next launcher
# boot AUTO-ROLLED BACK + QUARANTINED the healthy release. The launcher
# sweep is structurally BLIND to serving health (it runs PRE-daemon — "port
# down" is ambiguous: a broken release, or THIS boot starting the daemon).
#
# THE LADDER (phase2-plan.md §D.2 — the canonical four-rung form, restated
# ONCE here; every other site points here):
#   rung 0  a `rollback_requested` for this (target, txn) → RE-PROBE:
#             GREEN ∧ intent_flip ⇒ commit-close (the request is journaled
#               SUPERSEDED — serving-green = stay)
#             GREEN ∧ ¬intent_flip ⇒ DEFER + journal (the R3 hinge)
#             BROKEN / AMBIGUOUS ⇒ the EXISTING chain (the daemon's
#               post-grace verdict is the affirmative evidence)
#   rung 1  the serving probe:
#             GREEN ∧ intent_flip ∧ (owner-dead ∨ heartbeat-stale) ⇒
#               commit-close
#             GREEN ∧ ¬intent_flip ⇒ DEFER + journal
#             GREEN ∧ intent_flip ∧ owner-alive ⇒ DEFER
#             NOT GREEN ⇒ rung 3
#   rung 2  the OPERATOR ESCAPE — the existing boot_sweep_commit_and_continue
#           gate (prev manifest rollback_safe EXPLICITLY false + intent_flip)
#           ⇒ commit and continue (UNCHANGED)
#   rung 3  classify the non-green observation:
#             BROKEN ⇒ the EXISTING chain fires NOW
#             AMBIGUOUS ⇒ defer + count; the EXISTING chain fires when the
#               BOUNDED ABSENT-CEILING predicate holds
#
# THE THREE ROLLBACK-ELIGIBILITY PATHS ARE A CLOSED SET — there is no
# fourth: BROKEN ⇒ immediate · rollback_requested ⇒ immediate · AMBIGUOUS
# ⇒ bounded. EVERYTHING ELSE DEFERS (journaled `sweep_deferred`).

# _js_serving_version_strip <version> — strip ONE leading "v" (idempotent).
# The release dir/target is "v0.28.5"; the daemon reports "0.28.5". A naive
# compare is ALWAYS false ⇒ every healthy target reads unhealthy ⇒ the
# wrongful rollback returns (R-1). ONE helper per side (the daemon's twin
# lives in daemon/services/upgrade_journal_sweep.py).
_js_serving_version_strip() {
    local v="$1"
    case "$v" in
        v[0-9]*) printf '%s' "${v#v}" ;;
        *) printf '%s' "$v" ;;
    esac
}

# _js_serving_txn_identity <in_flight_json> <target> — the txn identity used
# by the write-once key, the request detail and the readers. The RESTART
# lane stamps `run_id` into in_flight; the PROMOTE lane does NOT (the
# promote run_id lives in the pipeline LOCK, gone by the time a stale txn
# is swept) ⇒ the identity falls back to "<target>@<started_at>" — stable
# across ticks AND launcher boots, distinct per txn. THE DAEMON MUST
# DERIVE THE SAME STRING (its twin is `_closer_txn_identity`).
_js_serving_txn_identity() {
    local inf="$1" target="$2" run_id started
    run_id="$(_js_json_field "$inf" "run_id" 2>/dev/null)" || run_id=""
    case "$run_id" in
        ''|null) run_id="" ;;
    esac
    if [ -n "$run_id" ]; then
        printf '%s' "$run_id"
        return 0
    fi
    started="$(_js_json_field "$inf" "started_at" 2>/dev/null)" || started=""
    case "$started" in
        ''|null) started="" ;;
    esac
    if [ -n "$started" ]; then
        printf '%s@%s' "$target" "$started"
    else
        printf '%s' "$target"
    fi
}

# _js_history_has_event_for_target <journal> <event> <target> [run_id]
# PER-ENTRY, DELIMITER-ANCHORED history reader (AM-P2-14 + the §D.3
# contract box). The event kind, the `target <VER>` substring and the
# optional `run_id <ID>` substring must ALL appear inside ONE history
# entry — the entry-object boundary is `{"ts":`.
#
# TWO HAZARDS are closed by that shape (both are live in the pre-existing
# whole-array reader `_js_history_has_intent_flip`, which is a NAMED
# FOLLOW-UP (R-15) and is NOT changed here):
#   (1) PREFIX COLLISION — `target v0.28.5` occurs inside `target
#       v0.28.55`; the delimiter anchor (a space, or the end of the
#       detail string) closes it.
#   (2) CROSS-ENTRY FALSE POSITIVE — a whole-array conjunction matches
#       "event kind in entry A" AND "target string in entry B"; per-entry
#       matching closes it. On the REQUEST reader a cross-entry false
#       positive would EXECUTE A ROLLBACK of a serving-green release.
#
# The version literal is UNQUOTED in the house detail convention (see
# promote.sh's intent_flip emit) — a quote-anchored match would NEVER
# fire.
_js_history_has_event_for_target() {
    local jp="$1" event="$2" target="$3" run_id="${4:-}"
    local json hist rest entry t_space t_end r_space r_end
    [ -n "$target" ] || return 1
    json="$(_js_journal_read "$jp")" || return 1
    hist="$(_js_json_sub "$json" "history")"
    case "$hist" in
        ''|null|\[\]) return 1 ;;
    esac
    t_space="target $target "
    t_end="target $target\""
    r_space="run_id $run_id "
    r_end="run_id $run_id\""
    rest="$hist"
    while :; do
        case "$rest" in
            *'{"ts":'*) rest="${rest#*'{"ts":'}" ;;
            *) break ;;
        esac
        case "$rest" in
            *'{"ts":'*) entry="${rest%%'{"ts":'*}" ;;
            *) entry="$rest" ;;
        esac
        # the EVENT KIND must be in THIS entry
        # the event-kind literal — the house writers emit the COMPACT form
        # ("event":"x"); the spaced form ("event": "x") is tolerated too
        # (a hand-edited journal must not silently change a verdict).
        case "$entry" in
            *"\"event\":\"$event\""*|*"\"event\": \"$event\""*) ;;
            *) continue ;;
        esac
        # the TARGET must be in THIS entry, delimiter-anchored
        case "$entry" in
            *"$t_space"*|*"$t_end"*) ;;
            *) continue ;;
        esac
        if [ -n "$run_id" ]; then
            case "$entry" in
                *"$r_space"*|*"$r_end"*) ;;
                *) continue ;;
            esac
        fi
        return 0
    done
    return 1
}

# _js_sanitise_run_id <run_id> — filename-safe form (AM-P2-20). The
# counter file is `<INSTALL_DIR>/.sweep-defer-<run_id>`; the run_id must
# be safe as a filename (no "/", no whitespace).
_js_sanitise_run_id() {
    local r="$1" out="" i c
    for ((i = 0; i < ${#r}; i++)); do
        c="${r:i:1}"
        case "$c" in
            [A-Za-z0-9._-]) out="$out$c" ;;
            *) out="${out}_" ;;
        esac
    done
    [ -n "$out" ] || out="unknown"
    printf '%s' "$out"
}

# _js_defer_counter_path <install_dir> <run_id> — the counter file's home
# (AM-P2-5). It is a LAUNCHER-LOCAL file (a sibling of .launcher-state),
# NOT a journal field: the launcher CANNOT create a journal field
# (`_js_journal_update` returns 1 on an absent literal) and it sweeps
# BEFORE the daemon exists, so no daemon-seeded field is reachable. The
# drafted journal-history-count alternative was REJECTED: cross-entry
# false positives, a measured O(N²) extraction cost (13.5s / 35.7s at
# 27KB ⇒ ≈285× at the incident size) with NO launcher-side size guard,
# and an INVERTED over-count doctrine (this counter gates the ROLLBACK
# itself ⇒ over-count = a wrongful rollback; the live count was 2/3).
_js_defer_counter_path() {
    local install_dir="$1" run_id="$2"
    printf '%s/.sweep-defer-%s' "$install_dir" "$(_js_sanitise_run_id "$run_id")"
}

# _js_defer_counter_read <path> <key> — one counter value (0 when absent).
_js_defer_counter_read() {
    local path="$1" key="$2" line
    if [ ! -f "$path" ]; then
        printf '0'
        return 0
    fi
    while IFS= read -r line; do
        case "$line" in
            "$key="*)
                printf '%s' "${line#*=}"
                return 0
                ;;
        esac
    done < "$path"
    printf '0'
}

# _js_defer_counter_write <path> <absent> <mismatch> <unready> <broken>
# <green> — atomic write (temp + mv), the file format pinned by
# AM-P2-10.2.
_js_defer_counter_write() {
    local path="$1" absent="$2" mismatch="$3" unready="$4" broken="$5" green="$6" tmp
    tmp="$path.tmp.$$"
    {
        printf 'absent=%s\n' "$absent"
        printf 'mismatch=%s\n' "$mismatch"
        printf 'unready=%s\n' "$unready"
        printf 'broken=%s\n' "$broken"
        printf 'green=%s\n' "$green"
    } > "$tmp" 2>/dev/null || { rm -f "$tmp" 2>/dev/null; return 1; }
    mv -f "$tmp" "$path" 2>/dev/null || { rm -f "$tmp" 2>/dev/null; return 1; }
    return 0
}

# _js_http_request <path> <deadline_epoch> <status_var> <body_var>
# ONE bounded HTTP/1.1 GET over the ALREADY-OPEN fd 3. Sets the status
# var (the HTTP code) and the body var (the raw body). Returns nonzero on
# any failure (no answer / unparseable status / deadline).
#
# BOUNDS (A3 + AM-P2-9): every read is `read -t` with the REMAINING
# wall-clock budget (a per-read timeout alone is defeatable by a
# slow-dribble responder — each read would reset the timer). The
# `<&3` redirection form is used (bash 3.2-safe — NOT `read -u fd`,
# which bash 3.2 lacks).
_js_http_request() {
    local path="$1" deadline="$2" status_var="$3" body_var="$4"
    # The locals are NAMED DISTINCTIVELY on purpose: the caller's
    # variables are assigned through the eval below, and a local with the
    # same name as the caller's target would shadow it (the eval runs in
    # THIS function's scope) — the caller would read an empty value.
    local remaining now status_line _js_http_status line content_length=0 _js_http_body body_len=0
    now="$(_now)"
    remaining=$(( deadline - now ))
    [ "$remaining" -lt 1 ] && remaining=1
    # The write runs in a SUBSHELL: when the peer already closed the
    # connection (the HTTP/1.0 shape) the write raises SIGPIPE, and a
    # SIGPIPE in the launcher itself would KILL it. In the subshell only
    # the writer dies; the exit status (141) is caught by the `||`.
    ( printf 'GET %s HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: keep-alive\r\n\r\n' \
        "$path" >&3 ) 2>/dev/null || return 1
    if ! IFS= read -r -t "$remaining" status_line <&3 2>/dev/null; then
        return 1
    fi
    # CRLF: bash's `read` strips the newline but NOT the carriage return —
    # the header-terminating blank line would arrive as "\r" (never empty)
    # and the header loop would run into the body. Strip it.
    status_line="${status_line%$'\r'}"
    _js_http_status="${status_line#* }"
    _js_http_status="${_js_http_status%% *}"
    case "$_js_http_status" in
        [0-9][0-9][0-9]) ;;
        *) return 1 ;;
    esac
    # headers (bounded; the blank line ends them)
    while :; do
        now="$(_now)"
        remaining=$(( deadline - now ))
        [ "$remaining" -lt 1 ] && remaining=1
        if ! IFS= read -r -t "$remaining" line <&3 2>/dev/null; then
            return 1
        fi
        line="${line%$'\r'}"
        case "$line" in
            '') break ;;
            Content-Length:*|content-length:*)
                content_length="${line#*:}"
                content_length="${content_length// /}"
                ;;
        esac
    done
    # body — read until the advertised length is covered (a truncated body
    # is NOT-GREEN, never green).
    _js_http_body=""
    if [ "${content_length:-0}" -gt 0 ] 2>/dev/null; then
        while [ "$body_len" -lt "$content_length" ]; do
            now="$(_now)"
            remaining=$(( deadline - now ))
            [ "$remaining" -lt 1 ] && remaining=1
            if ! IFS= read -r -t "$remaining" line <&3 2>/dev/null; then
                line="${line%$'\r'}"
                [ -n "$line" ] && { _js_http_body="$_js_http_body$line"; body_len=$(( body_len + ${#line} )); }
                break
            fi
            line="${line%$'\r'}"
            _js_http_body="$_js_http_body$line"
            body_len=$(( body_len + ${#line} + 1 ))
        done
    fi
    eval "$status_var=\"\$_js_http_status\""
    eval "$body_var=\"\$_js_http_body\""
    return 0
}

# P2 / R-08 (b) BLAST RADIUS — protocol boundary (D-P2.8 / C-4):
# launcher.sh is SELF-CONTAINED — it does NOT source lib.sh,
# has ZERO python3 dependency, and uses its OWN in-shell
# _js_json_field / _js_journal_update / _js_history_append
# writers for the boot-sweep commit path above. The promote
# lane's splice-based journal writes are scoped to promote.sh
# + lib.sh only — those calls land on
# `scripts/upgrade/journal_splice.py` (P2) and would NEVER
# reach this file. The boot_sweep_commit_and_continue path
# (this branch) is the ONLY path that may update `previous`
# from boot-sweep land; promote.sh owns `previous` writes
# from the live lane via `journal_set_previous ${OLD_CUR}`
# at promote.sh:438 (and will move to the splice helper
# `set-previous` op in P2 PR-2 — see
# scripts/upgrade/journal_splice.py). DO NOT add a python3
# dep here — launcher purity is a hard constraint (R-08).
#
# PHASE 2 (promote-handback-halt) — the serving-green probe obeys the SAME
# constraint: the probe is a BASH BUILTIN feature (`/dev/tcp` — the
# redirection form, NOT an external binary), so NO new dependency surface
# is introduced. There is NO curl / wget / nc / lsof / python3 anywhere in
# this file's executable text (the comment mentions above are the only
# occurrences of the literal tokens, and a comment is not executable —
# the static purity pin strips comments before matching, P-31 /
# AM-P2-21).
#
# _js_serving_probe <port> <target_version> — the PURITY-SAFE (R-08)
# serving probe. Prints "<class> <detail>" and returns 0 only for GREEN.
#
# The CLASS is the AM-P2-10.1 taxonomy:
#   green      — /livez 200 ∧ version == the v-stripped target ∧ /readyz
#                200 whose body reports ready
#   broken     — /livez ANSWERED with a non-200 status (affirmative
#                unhealthy: the zero-DB liveness endpoint is broken)
#   absent     — no answer at all (no listener / no response / the version
#                field absent or unparseable — "cannot tell")
#   mismatch   — /livez 200 but the version ≠ the target (the OLD daemon is
#                still bound, or the flip's binary never took effect)
#   unready    — /livez 200 + the right version but /readyz is not 200 /
#                not "ready" (the boot window: MCP warmup + auto-continue)
#
# NOT-POSITIVELY-GREEN ⇒ DEFER, NEVER rollback (A6): the launcher's port
# down is ambiguous by construction (this very boot may be starting the
# daemon).
#
# PURITY (R-08): bash builtins only — `/dev/tcp` is a bash feature, NOT an
# external binary. There is NO curl/wget/nc/lsof/python3 here (the
# in-file purity guard at the end of this file is the backstop).
_js_serving_probe() {
    local port="$1" target="$2"
    local budget="${SWEEP_SERVING_PROBE_BUDGET_S:-5}"
    local v_target deadline status body version rstatus
    v_target="$(_js_serving_version_strip "$target")"
    if [ -z "$port" ] || [ -z "$v_target" ]; then
        printf 'absent no-probe-target'
        return 1
    fi
    deadline=$(( $(_now) + budget ))
    # The group form (NOT `exec 3<>... 2>/dev/null`): the redirections are
    # applied left-to-right, so the failing 3<> would print bash's
    # "connect: Connection refused" to the launcher's stderr BEFORE the
    # 2> redirect took effect. The group redirects the WHOLE block's
    # stderr (the fd still lands in the current shell — a group is not a
    # subshell).
    if ! { exec 3<>"/dev/tcp/127.0.0.1/$port"; } 2>/dev/null; then
        printf 'absent no-listener'
        return 1
    fi
    status=""; body=""
    if ! _js_http_request "/livez" "$deadline" status body; then
        exec 3>&-
        printf 'absent no-answer'
        return 1
    fi
    if [ "$status" != "200" ]; then
        exec 3>&-
        printf 'broken livez-status-%s' "$status"
        return 1
    fi
    version="$(_js_json_field "$body" "version" 2>/dev/null)" || version=""
    if [ -z "$version" ]; then
        # absent / unparseable version ⇒ NOT-GREEN ⇒ defer (AM-P2-9 — the
        # wrongful-COMMIT mirror of tonight; a probe artefact must never
        # become an immediate rollback).
        exec 3>&-
        printf 'absent livez-version-absent'
        return 1
    fi
    if [ "$version" != "$v_target" ]; then
        exec 3>&-
        printf 'mismatch version-%s' "$version"
        return 1
    fi
    # /readyz — a SECOND GET over the SAME fd (no second connect, no
    # second port race). If the server closed the connection after the
    # first response (the HTTP/1.0 shape), the fd is dead: reopen ONCE
    # (documented fallback — the readyz check still happens) and continue.
    status=""; body=""
    if ! _js_http_request "/readyz" "$deadline" status body; then
        exec 3>&-
        if ! { exec 3<>"/dev/tcp/127.0.0.1/$port"; } 2>/dev/null; then
            printf 'unready readyz-no-listener'
            return 1
        fi
        status=""; body=""
        if ! _js_http_request "/readyz" "$deadline" status body; then
            exec 3>&-
            printf 'unready readyz-no-answer'
            return 1
        fi
    fi
    exec 3>&-
    if [ "$status" != "200" ]; then
        printf 'unready readyz-status-%s' "$status"
        return 1
    fi
    rstatus="$(_js_json_field "$body" "status" 2>/dev/null)" || rstatus=""
    if [ "$rstatus" != "ready" ]; then
        printf 'unready readyz-status-%s' "${rstatus:-absent}"
        return 1
    fi
    printf 'green livez-200 version-%s readyz-200' "$version"
    return 0
}

# _js_quarantine_remove <path> <ver> — remove from quarantined[] (the
# array-rewrite half of the serving-green clearance). Idempotent; a
# missing field is a no-op, not an error.
_js_quarantine_remove() {
    local jp="$1" ver="$2" json q inner item out="" first=1
    json="$(_js_journal_read "$jp")" || return 1
    q="$(_js_json_sub "$json" "quarantined")"
    case "$q" in
        *"\"$ver\""*) ;;
        *) return 0 ;;
    esac
    inner="${q#\[}"
    inner="${inner%\]}"
    local old_ifs="$IFS"
    IFS=','
    for item in $inner; do
        item="${item//\"/}"
        item="${item// /}"
        [ -n "$item" ] || continue
        [ "$item" = "$ver" ] && continue
        if [ "$first" = "1" ]; then
            out="\"$item\""
            first=0
        else
            out="$out, \"$item\""
        fi
    done
    IFS="$old_ifs"
    _js_journal_update "$jp" "quarantined" "[$out]"
}

# ── rollback.lock.d — mkdir-lock, D5 protocol (self-contained) ─────────────
# mkdir IS the atomic acquire (portable; no flock on stock macOS). Contents:
# owner (pid), heartbeat (epoch). Stale: heartbeat older than
# SWEEP_LOCK_STALE_S AND owner pid dead/unverifiable (W1 — a live owner
# mid un-heartbeated long op, e.g. the ≤600s stop span, is never broken)
# → mv to rollback.lock.d.stale.<pid>
# (never rmdir — racy) → re-acquire. Bounded wait; the launcher passes
# wait=0 (never delay boot — defer to the next start instead).

_js_lock_dir() {
    printf '%s/releases/rollback.lock.d' "$1"
}

# _js_run_bounded <timeout_s> [--] <cmd...> — run a subprocess with a
# SIGKILL deadline; on wedge, log LOUD and return nonzero. Launcher's
# sweep-hang timeouts (fix #4, v0.16.9): previously any blocking
# subprocess (kill -0 on a foreign-D-state pid; cat on a hung FIFO/NFS;
# date on a wedged strftime) could pin the boot sweep FOREVER
# (2026-10-01 attempt-1 halt — recovery runbook commit 3dd8dc1
# §Journal Sweep Hang Recovery). On timeout: subprocess is SIGKILLed,
# helper returns 124 (the conventional GNU `timeout` "timed out" code,
# used here for consistency with the rest of the project), JOURNAL
# STATE UNTOUCHED — the sweep is a recovery backstop; never write fake
# terminal states, the next start will retry.
# PORTABILITY (fix/portable-atomic-flip-linux 47630be0 idiom): no GNU
# `timeout` (BSD/macOS stock shells lack it); uname-agnostic; bash
# 3.2-safe (no chained local declarations). Implementation: background
# the command, race a `sleep` watchdog that SIGKILLs it on win. The
# `sleep + kill` arm fires only if `wait` has not returned by the
# deadline — never destroys a fast-finishing child.
_js_run_bounded() {
    local timeout_s="$1"; shift
    if [ "${1:-}" = "--" ]; then shift; fi
    local pid watcher rc=0
    "$@" &
    pid=$!
    # Detach the watcher subshell from the command-substitution pipe so an
    # orphan `sleep` (reparented to init when SIGKILLed on child-wins) cannot
    # hold the pipe write-end and stall `$()` until sleep expires. The
    # redirected fd is /dev/null — the watched child's fds (set up earlier)
    # are unaffected. Without this, every instant bounded call stalls ~N s
    # even though the wrapped command completed instantly (commit 961dfe59
    # regression — see LESSONS/2026-10-01-js-run-bounded-orphaned-sleep-stall).
    (
        sleep "$timeout_s" 2>/dev/null
        kill -KILL "$pid" 2>/dev/null
    ) >/dev/null 2>&1 &
    watcher=$!
    if wait "$pid" 2>/dev/null; then
        rc=0
    else
        rc=$?
    fi
    # SIGKILL = 137 (128+9), SIGTERM = 143 (128+15). When the child is
    # killed by a signal we did NOT send (i.e. our watchdog fired first),
    # bash wedges with 128+SIGKILL = 137 → wedge → return 124 (conventional
    # GNU `timeout` "timed out" code, used here for cross-tool consistency).
    # If the child itself exits with rc 137 (no kill involved), we cannot
    # distinguish, but in this codebase no sweep child does that — `kill -0`,
    # `cat`, `date` all exit 0 or 1, never 137. The watcher is always
    # SIGKILLed at the end; it has its own race window but bash builtin kill
    # is a single syscall (no FS / IPC dependency), safe to ignore.
    case "$rc" in
        137|143) rc=124 ;;
    esac
    kill -KILL "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    return "$rc"
}

# _pid_alive <pid> — kill -0 liveness with EPERM=ALIVE semantics (B3.5
# lib.sh _pid_alive twin, mirrored locally: the launcher is the BOOT path
# and must stay self-contained — it never sources scripts/upgrade/lib.sh).
# exit 0 → alive; ENOENT/ESRCH → dead; EPERM → ALIVE — the process exists
# but belongs to another user (permission denied ≠ dead; breaking a live
# foreign owner's lock would trample its txn). Matches the Python journal
# twin upgrade_journal._pid_alive (PermissionError → True). Stderr-text
# match under LC_ALL=C (NIT-5 — locale-stable: a localized shell must
# not flip the branch): bash builtin kill and /bin/kill both render
# "Operation not permitted".
_pid_alive() {
    local err
    err="$(LC_ALL=C kill -0 "$1" 2>&1)" && return 0
    case "$err" in
        *"not permitted"*) return 0 ;;
        *) return 1 ;;
    esac
}

# _js_pid_alive_bounded <pid> — sweep-only bounded liveness test (fix #4,
# v0.16.9). Wraps kill -0 in a 3-second SIGKILL deadline so a foreign
# D-state pid (or wedged kernel) cannot pin the boot sweep forever
# (2026-10-01 attempt-1 halt — recovery runbook commit 3dd8dc1
# §Journal Sweep Hang Recovery). Preserves the B3.5 EPERM=ALIVE contract
# (kill -0 on a foreign-owned pid returns "not permitted" → alive).
# Conservative on wedge: returns ALIVE (rc 0) — the lock is NEVER broken
# on a wedged liveness check (we don't know the owner is dead, only
# that we couldn't prove it alive within 3s; the operator removes a
# stale lock manually if needed). sweep-scoped ONLY: callers outside
# the sweep continue to use _pid_alive (the timeout would change
# semantics elsewhere).
_js_pid_alive_bounded() {
    local out rc
    out="$(_js_run_bounded 3 -- sh -c 'LC_ALL=C kill -0 "$1" 2>&1' -- "$1")"
    rc=$?
    # The "rc=N" suffix is the inner sh's exit code; kill -0 → 0 (alive),
    # 1 (dead or EPERM); _js_run_bounded → 0, or 124 on wedge (rc of the
    # inner sh is whatever it was when SIGKILL'd; we cannot trust it
    # after a wedge, default to conservative-alive below).
    case "$rc" in
        0)
            # inner rc=0 → permitted alive
            return 0 ;;
        124)
            # _js_run_bounded timed out — wedge → conservative alive
            return 0 ;;
        *)
            # inner rc≠0 — check stderr for the EPERM=alive contract
            case "$out" in
                *"not permitted"*) return 0 ;;
                *) return 1 ;;
            esac ;;
    esac
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
        # exists — stale? Break only when the heartbeat is > SWEEP_LOCK_STALE_S
        # AND the owner pid is dead/unverifiable (W1). A stale heartbeat
        # ALONE must NOT break the lock: promote/rollback hold it un-
        # heartbeated across the stop-script span (stop-ensemble WAIT_S
        # clamps to 600s > the 300s staleness bound), and breaking under a
        # LIVE owner lets a concurrent launcher start clear/roll back a txn
        # whose owner is still mutating it — the B3 ungated-flip class. A
        # missing/garbage owner pid is UNVERIFIABLE: nothing to protect, so
        # the heartbeat alone breaks it (preserves crash progress). kill -0
        # (POSIX) is the liveness test; residual pid-reuse wedge (crashed
        # owner, pid reused by a live process) degrades to DEFER — safer
        # than trampling a live owner; operator removes the stale lock.
        hb="$(cat "$lock/heartbeat" 2>/dev/null)"
        owner_pid="$(cat "$lock/owner" 2>/dev/null)"
        run_id="$(cat "$lock/run_id" 2>/dev/null)"
        if printf '%s' "$hb" | grep -Eq '^[0-9]+$'; then
            age=$(( $(_now) - hb ))
            if [ "$age" -gt "$SWEEP_LOCK_STALE_S" ]; then
                local owner_live=0
                if [ -n "$owner_pid" ] && printf '%s' "$owner_pid" | grep -Eq '^[0-9]+$' \
                   && _js_pid_alive_bounded "$owner_pid"; then
                    owner_live=1
                fi
                if [ "$owner_live" -eq 0 ]; then
                    _log "journal sweep: pipeline lock stale (heartbeat ${age}s old, owner pid ${owner_pid:-?} dead/unverifiable, run ${run_id:-?}) — breaking"
                    mv "$lock" "${lock}.stale.$$" 2>/dev/null || continue
                    continue
                fi
                # LIVE owner mid un-heartbeated long op (the stop span) —
                # fall through to the bounded wait/defer below; NEVER break.
            fi
        fi
        # owner process dead? (crash left a fresh-heartbeat dir) — break too
        if [ -n "$owner_pid" ] && printf '%s' "$owner_pid" | grep -Eq '^[0-9]+$' \
           && ! _js_pid_alive_bounded "$owner_pid"; then
            _log "journal sweep: pipeline lock owner pid $owner_pid is dead — breaking lock"
            mv "$lock" "${lock}.stale.$$" 2>/dev/null || continue
            continue
        fi
        if [ "$waited" -ge "$wait_s" ]; then
            _log "journal sweep: pipeline lock held by a LIVE owner (pid ${owner_pid:-?}, run ${run_id:-?}) — deferring sweep to next start"
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
# build current.new.$$ then mv -f over `current` (the mv is the atomic
# point). Target is RELATIVE ("releases/<ver>") so the layout stays
# relocatable; the symlink lives at the INSTALL ROOT — exactly what
# resolve_binary expects ($INSTALL_DIR/current/ensemble-prod, ADR-004).
# NOTE: plain `mv -f` is WRONG here on either platform — it follows
# current→releases/<old> and moves the temp link INSIDE the old
# release, silently leaving `current` on the OLD release.
#   BSD/macOS: `mv -h -f`  — swap the SYMLINK itself (don't follow DEST).
#   GNU/Linux: `mv -T -f`  — no-follow DEST (treat DEST as a normal file).
# Unrecognized platforms refuse (fail-closed). Verified on macOS mv(1)
# and GNU coreutils 9.4 (Linux). Mirrors scripts/upgrade/lib.sh's
# atomic_flip; launcher.sh is staged standalone and cannot source lib.sh
# (see comment at top-of-file).
_js_flip_current() {
    local install_dir="$1"
    local ver="$2"
    local mv_args=""
    case "$(uname -s)" in
        Darwin|*BSD*|*bsd*)
            mv_args="-h -f"
            ;;
        Linux|GNU*|*GNU*)
            mv_args="-T -f"
            ;;
        *)
            _log "WARN: _js_flip_current: unrecognized platform '$(uname -s)' — refusing to repoint (fail-closed; BSD or Linux required)"
            return 1
            ;;
    esac
    ln -sfn "releases/$ver" "$install_dir/current.new.$$" 2>/dev/null || return 1
    # mv_args intentionally word-split (a single string of options)
    # shellcheck disable=SC2086
    if ! mv $mv_args "$install_dir/current.new.$$" "$install_dir/current" 2>/dev/null; then
        rm -f "$install_dir/current.new.$$" 2>/dev/null
        return 1
    fi
    return 0
}

# _js_manifest_field <install_dir> <ver> <key> — top-level release manifest
# field. Same semantics as lib.sh manifest_field (the protocol, not the
# code, is the shared contract): nonzero on missing/unreadable manifest or
# absent key. Callers fail closed on EVERY unreadable shape (D-FA4.5).
# Bounded 3s cat read (fix #4, v0.16.9): same sweep-hang defense as
# _js_journal_read.
_js_manifest_field() {
    local mp json
    mp="$1/releases/$2/manifest.json"
    [ -f "$mp" ] || return 1
    json="$(_js_run_bounded 3 -- cat "$mp")" || return 1
    _js_json_field "$json" "$3"
}

# ── The sweep itself ────────────────────────────────────────────────────────
#
# AXIS NOTE (cause ①, upgrade-resilience 2026-10-07): this sweep
# operates on a DIFFERENT axis than the daemon's reconcile
# (``reconcile_pending_op`` at ``daemon/tools/upgrade_journal.py``).
# The shell sweep reads the journal's ``in_flight`` (and its
# ``flipped`` marker) — the txn-level state owned by the pipeline
# scripts (``promote.sh`` / ``rollback.sh`` / ``adopt_stale_txn``).
# The daemon's reconcile reads the journal's ``pending_op`` and
# walks ``history`` for terminal-class events. The two are layered
# (the daemon's tool lane is ABOVE the shell pipeline, the shell
# sweep is the BOOT-time backstop for orphaned in_flight records).
#
# Consequence: this sweep does NOT see — and does NOT need to
# mirror — the daemon-side terminal events ``refusal`` and
# ``executor_exit`` that were added to ``_TERMINAL_EVENTS`` by the
# cause ① fix. Those events fire ONLY from the daemon's tool lane
# (``upgrade_tools._refusal`` → ``_journal_refusal_event`` at
# ``upgrade_tools.py:1268``) and the daemon's reaper
# (``_journal_executor_exit`` at
# ``upgrade_journal_sweep.py:1776``); they are observability-only
# (no flip, no in_flight) so they never reach this sweep's input
# axis. A clean pre-mutation exit-78 (the live 2026-10-07 06:45:43Z
# + 06:46:34Z evidence) is correctly closed by the daemon's
# reconcile via the terminal-evidence branch; the shell sweep has
# no work to do because no in_flight was ever opened. Mirroring
# here would be a false symmetry (the two sweeps solve different
# failure modes; forcing them onto the same vocabulary would
# conflate pipeline-flips with observability-only terminality).
#
# The shell-side sweep's terminal vocabulary is documented
# separately: ``adopt_stale_txn`` (``lib.sh:3936``) and the journal
# sweep-rolled-back path (``sweep_rollback`` event) — both produce
# in_flight shape that this sweep reclaims. Do not extend this
# comment to cover daemon-side events.
# _js_sweep_release <install_dir> <lock_held> — release the D5 lock ONLY if
# this call owns it (lock_held=1 ⇒ the caller — _journal_sweep — took it;
# lock_held=0 ⇒ this helper took it itself, the T15 run-loop path).
_js_sweep_release() {
    local install_dir="$1" lock_held="${2:-1}"
    if [ "$lock_held" = "1" ]; then
        _js_lock_release "$install_dir"
    fi
    return 0
}

# _js_defer_counter_delete <install_dir> <run_id> — the counter file's
# lifecycle (AM-P2-20): it is DELETED when the txn commits or rolls back
# (the tally is meaningless once the txn is closed).
_js_defer_counter_delete() {
    local install_dir="$1" run_id="$2" path
    path="$(_js_defer_counter_path "$install_dir" "$run_id")"
    rm -f "$path" 2>/dev/null || true
    return 0
}

# _js_serving_defer <journal> <target> <run_id> <age> <reason> <detail>
# The DEFER disposition: a journaled `sweep_deferred` row (a HOLD — it
# closes nothing; it is NOT a terminal event), the txn left OPEN, and NO
# quarantine / counter / cooldown / rollback. The row carries an explicit,
# GREPPABLE reason marker (which conjunct was missing) — matrix row 6.
_js_serving_defer() {
    local journal="$1" target="$2" run_id="$3" age="$4" reason="$5" detail="$6"
    _js_history_append "$journal" "sweep_deferred" \
        "sweep: stale flipped txn (target=$target, run_id=$run_id, age=${age}s) — DEFERRED ($reason) — txn left OPEN; no quarantine, no counter, no cooldown, no rollback; $detail" \
        || { _log "WARN: journal sweep: sweep_deferred history append FAILED"; return 1; }
    _log "journal sweep: stale flipped txn (target=$target, run_id=$run_id, age=${age}s) — DEFERRED ($reason) — txn left OPEN"
    return 0
}

# _js_serving_commit_close <install_dir> <journal> <target> <prev>
# <displaced> <run_id> <age> <owner> <probe_detail> <reason>
# The serving-green COMMIT-CLOSE — AM-P2-1's HISTORY-BEFORE-CLOSE order
# (NOT the old close-before-history order at the old launcher.sh:1108-1127
# — mirroring that would defeat the journal check the crash-prefix pin
# relies on):
#   1. current  → target
#   2. previous → in_flight.displaced_version (absent/null ⇒ HALT-FOR-HUMAN:
#      the txn is old-format/ambiguous — no silent commit)
#   3. history append `serving_green_commit` — and FIRST, when the target
#      is in quarantined[], append `quarantine_cleared_serving_green` (the
#      EVENT is the amended shell gate's acceptance signal ⇒ event-before-
#      close makes the clearance crash-proof)
#   4. in_flight → null   ← the txn closes ONLY here
#   5. quarantined[] rewrite (opportunistic — the field must already exist)
# NOT done: quarantine marking, the rollback counter increment, cooldown
# arming — the cap/cooldown are anti-flapping responses to BAD outcomes
# and a successful-and-declared flip is not one.
_js_serving_commit_close() {
    local install_dir="$1" journal="$2" target="$3" prev="$4" displaced="$5"
    local run_id="$6" age="$7" owner="$8" probe="$9" reason="${10}"
    local quarantined
    if [ -z "$displaced" ] || [ "$displaced" = "null" ]; then
        _notify_once "sweep-halt-old-format" \
            "HALT-FOR-HUMAN: stale flipped txn (target=$target) has no in_flight.displaced_version field (old-format txn) — no silent commit; boot proceeds on current; see $install_dir/releases/state.json"
        _js_history_append "$journal" "halt" \
            "sweep: stale flipped txn (target=$target, run_id=$run_id) has no in_flight.displaced_version (old-format txn) — halt-for-human, txn left in place" \
            || true
        _log "HALT: journal sweep: serving-green commit refused — flipped txn (target=$target) has no in_flight.displaced_version (old-format) — halt-for-human"
        return 1
    fi
    _js_journal_update "$journal" "current" "\"$target\"" \
        || { _log "WARN: journal sweep: serving-green commit current update FAILED — boot proceeds on existing current"; return 1; }
    _js_journal_update "$journal" "previous" "\"$displaced\"" \
        || { _log "WARN: journal sweep: serving-green commit previous update FAILED"; return 1; }
    quarantined="$(_js_json_sub "$(_js_journal_read "$journal")" "quarantined" 2>/dev/null)" || quarantined=""
    case "$quarantined" in
        *"\"$target\""*)
            _js_history_append "$journal" "quarantine_cleared_serving_green" \
                "target $target run_id $run_id serving_green_commit prev $displaced" \
                || { _log "WARN: journal sweep: quarantine_cleared_serving_green append FAILED"; return 1; }
            ;;
    esac
    _js_history_append "$journal" "serving_green_commit" \
        "target $target run_id $run_id prev $displaced owner pid ${owner:-?} age ${age}s — serving green ($probe); committed and continued — NOT rolled back, NOT quarantined, counter NOT incremented, no cooldown ($reason)" \
        || { _log "WARN: journal sweep: serving_green_commit append FAILED"; return 1; }
    _js_journal_update "$journal" "in_flight" "null" \
        || { _log "WARN: journal sweep: serving-green commit in_flight clear FAILED"; return 1; }
    case "$quarantined" in
        *"\"$target\""*)
            _js_quarantine_remove "$journal" "$target" \
                || _log "WARN: journal sweep: quarantined[] rewrite FAILED (the clearance EVENT is the gate's acceptance signal — the array is hygiene)"
            ;;
    esac
    _js_defer_counter_delete "$install_dir" "$run_id"
    _log "BOOT-SWEEP SERVING-GREEN COMMIT: stale flipped txn (age ${age}s, target=$target, prev=$prev, displaced=$displaced) — serving green ($probe) + intent_flip evidence; committed at target (NOT sweep-rollback; txn closed, counter NOT incremented, no cooldown)"
    return 0
}

# _js_sweep_rollback_chain <install_dir> <journal> <json> <inf> <target>
# <prev> <age> <owner> <kind> <disposition> <lock_held>
# The EXISTING sweep-rollback disposition (ADR-012 / ADR-024 / D-FA4.2),
# EXTRACTED verbatim so the new ladder has exactly ONE implementation of
# it (the ladder reaches it from three places: the BROKEN class, the
# daemon's rollback REQUEST, and the bounded absent-ceiling). The body is
# the pre-Phase-2 chain; the only changes are (a) the history detail
# carries the disposition marker and (b) the lock release is conditional
# on the caller.
_js_sweep_rollback_chain() {
    local install_dir="$1" journal="$2" json="$3" inf="$4" target="$5"
    local prev="$6" age="$7" owner="$8" kind="$9" disposition="${10}"
    local lock_held="${11:-1}"
    local rc=0 prev_q prev_safe new_cnt=""
    case "$prev" in ''|null)
        _notify_once "sweep-halt" \
            "HALT-FOR-HUMAN: stale flipped $kind txn (target=${target:-?}) but journal has no previous release — cannot sweep-rollback; boot proceeds on current; see $install_dir/releases/state.json"
        _js_history_append "$journal" "halt" \
            "sweep: stale flipped $kind txn target=${target:-?} has no previous to roll back to — halt-for-human, txn left in place" \
            || rc=1
        _log "HALT: journal sweep: flipped txn (age ${age}s) with no previous — halt-for-human, boot proceeds on current"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
        ;;
    esac
    # M4 quarantine gate: a QUARANTINED previous is a known-bad release
    # (it failed a gate before) — the sweep must never flip onto it.
    prev_q="$(_js_json_sub "$json" "quarantined" 2>/dev/null)" || prev_q=""
    case "$prev_q" in
        *"\"$prev\""*)
            _notify_once "sweep-halt" \
                "HALT-FOR-HUMAN: stale flipped $kind txn (target=${target:-?}) but previous release $prev is QUARANTINED (known-bad — M4) — refusing to sweep-rollback onto it; boot proceeds on current; see $install_dir/releases/state.json"
            _js_history_append "$journal" "halt" \
                "sweep: previous release $prev is QUARANTINED (known-bad) — halt-for-human, NO repoint, txn left in place (M4)" \
                || rc=1
            _log "HALT: journal sweep: previous $prev is quarantined (known-bad) — halt-for-human, boot proceeds on current (M4)"
            _js_sweep_release "$install_dir" "$lock_held"
            return 0
            ;;
    esac
    if [ ! -d "$install_dir/releases/$prev" ]; then
        _notify_once "sweep-halt" \
            "HALT-FOR-HUMAN: stale flipped $kind txn (target=${target:-?}) but previous release dir releases/$prev is missing (manual deletion?) — cannot sweep-rollback; boot proceeds on current; see $install_dir/releases/state.json"
        _js_history_append "$journal" "halt" \
            "sweep: previous release $prev missing (manual deletion?) — halt-for-human, txn left in place" \
            || rc=1
        _log "HALT: journal sweep: previous release $prev is missing — halt-for-human, boot proceeds on current"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    # Manifest gate (ADR-005 M5 / D-FA4.5): previous must be
    # rollback_safe:true.
    prev_safe="$(_js_manifest_field "$install_dir" "$prev" rollback_safe 2>/dev/null)" \
        || prev_safe=""
    if [ "$prev_safe" != "true" ]; then
        _notify_once "sweep-halt" \
            "HALT-FOR-HUMAN: stale flipped $kind txn (target=${target:-?}) but previous release $prev is NOT rollback_safe (${prev_safe:-missing manifest}) — refusing to sweep-rollback into schema drift (D-FA4.5); boot proceeds on current; see $install_dir/releases/state.json"
        _js_history_append "$journal" "halt" \
            "sweep: previous release $prev has rollback_safe=${prev_safe:-missing} (D-FA4.5 schema-drift guard) — halt-for-human, txn left in place" \
            || rc=1
        _log "HALT: journal sweep: previous $prev not rollback_safe (${prev_safe:-missing}) — halt-for-human, boot proceeds on current"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    # NOTE (D-FA4.1, Batch C): this sweep-rollback mutates current + the
    # journal but does NOT restore the previous release's launcher.sh — the
    # sweep deliberately cannot (it runs INSIDE launcher.sh).
    if ! _js_flip_current "$install_dir" "$prev"; then
        _log "WARN: journal sweep: could not repoint current to $prev — txn left in place for the next sweep, boot proceeds"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    _log "journal sweep: STALE flipped $kind txn (age ${age}s, target=${target:-?}, owner pid ${owner:-?}) — rolled back: current -> releases/$prev"
    _js_quarantine "$journal" "$target" \
        || { _log "WARN: journal sweep: quarantine append failed"; rc=1; }
    _js_journal_update "$journal" "current" "\"$prev\"" \
        || { _log "WARN: journal sweep: journal current update failed"; rc=1; }
    new_cnt="$(_js_count_rollback "$journal")" \
        || { _log "WARN: journal sweep: rollback counter/cooldown update failed"; rc=1; }
    _js_history_append "$journal" "sweep_rollback" \
        "sweep: orphaned flipped $kind txn (target=${target:-?}, owner pid ${owner:-?}, age ${age}s) rolled back to $prev; counted as auto-rollback (ADR-024) disposition=${disposition}" \
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
        "sweep-rollback executed: orphaned flipped txn (age ${age}s) rolled current back to $prev (from target ${target:-?}); counted toward the 3/24h cap + cooldown armed (ADR-024, disposition=${disposition})"
    # AM-P2-20 — the counter file's lifecycle: the txn is CLOSED ⇒ the
    # tally is meaningless ⇒ the file is deleted (on ANY rollback path).
    _js_defer_counter_delete "$install_dir" "$(_js_serving_txn_identity "$inf" "$target")"
    _js_sweep_release "$install_dir" "$lock_held"
    return 0
}

# _js_sweep_flipped_disposition <install_dir> [lock_held]
# THE disposition for a stale, already-FLIPPED promote txn — the Phase-2
# split closer's launcher half. Called by BOTH `_journal_sweep` (the
# launcher-boot path, lock already held) and the T15 run-loop hook (which
# takes the lock itself). The stale PRE-FLIP clear is explicitly OUT of
# this helper's scope (AM-P2-17 — the hook must never clear a live
# pre-flip promote's txn).
_js_sweep_flipped_disposition() {
    local install_dir="$1" lock_held="${2:-1}"
    local journal="$install_dir/releases/state.json"
    local json inf kind target started flipped owner prev age
    local started_epoch now hb hb_stale owner_dead cur_link jcur run_id
    local probe_out probe_class probe_detail intent_flip=0 request_present=0
    local counter_path absent mismatch unready broken green total
    local jsize displaced_v
    [ -f "$journal" ] || return 0
    json="$(_js_journal_read "$journal")" || return 0
    inf="$(_js_json_sub "$json" "in_flight")"
    case "$inf" in ''|null) return 0 ;; esac
    kind="$(_js_json_field "$inf" "kind" 2>/dev/null)" || kind=""
    [ -n "$kind" ] || return 0
    if [ "$kind" = "restart" ]; then
        return 0
    fi
    target="$(_js_json_field "$inf" "target" 2>/dev/null)" || target=""
    [ -n "$target" ] || return 0
    started="$(_js_json_field "$inf" "started_at" 2>/dev/null)" || started=""
    flipped="$(_js_json_field "$inf" "flipped" 2>/dev/null)" || flipped=""
    owner="$(_js_json_field "$inf" "owner_pid" 2>/dev/null)" || owner=""
    started_epoch="$(_js_iso_to_epoch "$started" 2>/dev/null)" || return 0
    now="$(_now)"
    age=$(( now - started_epoch ))
    # comp3 liveness (the same contract as _journal_sweep's block)
    hb="$(_js_json_field "$inf" "last_heartbeat" 2>/dev/null)" || hb=""
    hb_stale=1
    if printf '%s' "$hb" | grep -Eq '^[0-9]+$' \
       && [ $(( now - hb )) -le "$HEARTBEAT_STALE_S" ]; then
        hb_stale=0
    fi
    owner_dead=1
    if [ -n "$owner" ] && printf '%s' "$owner" | grep -Eq '^[0-9]+$' \
       && _js_pid_alive_bounded "$owner" 2>/dev/null; then
        owner_dead=0
    fi
    # AM-P2-18 — the owner-liveness conjunct is a precondition of EVERY
    # stale-path branch: a live owner (fresh heartbeat) owns its txn — the
    # 900s ceremony cap exceeds the 600s staleness gate, so a slow-but-live
    # promote is reachable and must not be disposed of.
    if [ "$owner_dead" = "0" ] && [ "$hb_stale" = "0" ]; then
        _log "journal sweep: stale flipped $kind txn (target=$target, age ${age}s) — owner ALIVE (heartbeat fresh) — leaving the txn OPEN (AM-P2-18 owner-liveness conjunct)"
        return 0
    fi
    # the freshness gate (the comp3 fast path above skipped it in
    # _journal_sweep; mirror it here so the T15 hook path agrees)
    if [ "$age" -le "$SWEEP_STALE_S" ] && ! { [ "$hb_stale" = "1" ] && [ "$owner_dead" = "1" ]; }; then
        return 0
    fi
    # ── the lock ──
    if [ "$lock_held" != "1" ]; then
        if ! _js_lock_acquire "$install_dir"; then
            return 0
        fi
    fi
    # re-read + revalidate UNDER the lock (the launcher's discipline — the
    # txn OBJECT is the identity: any change ⇒ the owner acted ⇒ no-op)
    local json2 inf2
    if ! json2="$(_js_journal_read "$journal")"; then
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    inf2="$(_js_json_sub "$json2" "in_flight")"
    if [ "$inf2" != "$inf" ]; then
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    json="$json2"
    # the kill-window heal (the symlink is the authoritative flip evidence)
    cur_link="$(readlink "$install_dir/current" 2>/dev/null)" || cur_link=""
    jcur="$(_js_json_field "$json" "current" 2>/dev/null)" || jcur=""
    if [ "$flipped" != "true" ] && [ -n "$target" ] \
       && [ "$cur_link" = "releases/$target" ] && [ "$jcur" != "$target" ]; then
        flipped="true"
    fi
    if [ "$flipped" != "true" ]; then
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    prev="$(_js_json_field "$json" "previous" 2>/dev/null)" || prev=""
    run_id="$(_js_serving_txn_identity "$inf" "$target")"
    # ── the launcher-side size guard (AM-P2-5) ──
    jsize="${#json}"
    if [ "$jsize" -ge "$SWEEP_JOURNAL_SIZE_GUARD_BYTES" ] 2>/dev/null; then
        _log "WARN: journal sweep: journal is ${jsize}B ≥ ${SWEEP_JOURNAL_SIZE_GUARD_BYTES}B (the launcher-side size guard) — CONSERVATIVE DEFER (never a rollback in this regime; the daemon-side closer parses in-process and is the only automatic rollback path)"
        _js_serving_defer "$journal" "$target" "$run_id" "$age" \
            "journal-size-guard" \
            "the journal exceeds the launcher-side size guard — the launcher never rolls back in this regime"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    # ── the KILL-SWITCH (REV-R-3): =0 restores today's unconditional
    # auto-rollback disposition (an escape hatch for an unverified
    # platform — NEVER a permanent state).
    if [ "${SWEEP_SERVING_GREEN_ENABLED:-1}" = "0" ]; then
        _log "journal sweep: SWEEP_SERVING_GREEN_ENABLED=0 — the serving-green ladder is DISABLED; the legacy disposition applies"
        _js_sweep_rollback_chain "$install_dir" "$journal" "$json" "$inf" "$target" \
            "$prev" "$age" "$owner" "$kind" "serving_green_disabled" "$lock_held"
        return 0
    fi
    # ── the authorization evidence (P9) — per-entry + delimiter-anchored ──
    if _js_history_has_event_for_target "$journal" "intent_flip" "$target" ""; then
        intent_flip=1
    fi
    # ── the daemon's rollback REQUEST (rung 0) ──
    if _js_history_has_event_for_target "$journal" "rollback_requested" "$target" "$run_id"; then
        request_present=1
    fi
    # ── the serving probe (rung 1) ──
    probe_out="$(_js_serving_probe "${PORT:-}" "$target")" || true
    probe_class="${probe_out%% *}"
    probe_detail="${probe_out#* }"
    if [ "$probe_class" = "$probe_out" ]; then
        probe_detail=""
    fi
    # ── rung 0: the REQUEST path (T14 / AM-P2-15) ──
    if [ "$request_present" = "1" ]; then
        case "$probe_class" in
            green)
                if [ "$intent_flip" = "1" ]; then
                    _js_serving_commit_close "$install_dir" "$journal" "$target" "$prev" \
                        "$(_js_json_field "$inf" "displaced_version" 2>/dev/null)" "$run_id" "$age" \
                        "$owner" "$probe_detail" "serving-green (rollback request SUPERSEDED)"
                else
                    _js_serving_defer "$journal" "$target" "$run_id" "$age" \
                        "request-green-without-intent-flip" \
                        "a rollback_requested was seen AND the probe is green, but there is NO intent_flip evidence — the R3 hinge: never commit-close an unauthorized flip, never roll back a serving-green release"
                    _notify_once "sweep-defer-unauthorized" \
                        "DEFERRED: stale flipped txn (target=$target) is SERVING GREEN and carries a rollback_requested, but the journal carries NO intent_flip evidence for it — the flip is unauthorized: not committed (never commit-close an unauthorized flip) and not rolled back (serving-green = stay). The txn stays OPEN for operator resolution — see $install_dir/releases/state.json"
                fi
                ;;
            *)
                # BROKEN / AMBIGUOUS — the daemon's post-grace verdict is the
                # affirmative evidence ⇒ the EXISTING chain executes.
                _js_sweep_rollback_chain "$install_dir" "$journal" "$json" "$inf" "$target" \
                    "$prev" "$age" "$owner" "$kind" "rollback_requested" "$lock_held"
                ;;
        esac
        return 0
    fi
    # ── rung 1: the serving probe ──
    if [ "$probe_class" = "green" ]; then
        if [ "$intent_flip" = "1" ] && { [ "$owner_dead" = "1" ] || [ "$hb_stale" = "1" ]; }; then
            _js_serving_commit_close "$install_dir" "$journal" "$target" "$prev" \
                "$(_js_json_field "$inf" "displaced_version" 2>/dev/null)" "$run_id" "$age" \
                "$owner" "$probe_detail" "serving-green"
            return 0
        fi
        if [ "$intent_flip" != "1" ]; then
            _js_serving_defer "$journal" "$target" "$run_id" "$age" \
                "green-without-intent-flip" \
                "the target is serving green but there is NO intent_flip evidence — the flip is unauthorized: never commit-close an unauthorized flip, never roll back a serving-green release"
            _notify_once "sweep-defer-unauthorized" \
                "DEFERRED: stale flipped txn (target=$target) is SERVING GREEN but the journal carries NO intent_flip evidence for it — the flip is unauthorized: not committed and not rolled back. The txn stays OPEN for operator resolution — see $install_dir/releases/state.json"
            return 0
        fi
        _js_serving_defer "$journal" "$target" "$run_id" "$age" \
            "green-owner-alive" \
            "the target is serving green but its owner is still alive — the txn belongs to a live owner; not the sweep's to dispose of"
        return 0
    fi
    # ── rung 2: the OPERATOR ESCAPE (the existing boot_sweep_commit_and_continue
    # gate — UNCHANGED): the operator declared the previous release NOT
    # rollback-safe ⇒ rolling back would be strictly worse ⇒ commit and
    # continue at the target.
    if [ -n "$prev" ] && [ "$prev" != "null" ] \
       && _js_history_has_intent_flip "$journal" "$target" \
       && _js_manifest_field "$install_dir" "$prev" "rollback_safe" 2>/dev/null \
         | grep -q '^false$'; then
        displaced_v="$(_js_json_field "$inf" "displaced_version" 2>/dev/null)" || displaced_v=""
        if [ "$displaced_v" = "null" ]; then
            displaced_v=""
        fi
        if [ -z "$displaced_v" ]; then
            _notify_once "sweep-halt-old-format" \
                "HALT-FOR-HUMAN: stale flipped $kind txn (target=$target) has no in_flight.displaced_version field (old-format txn from pre-C-4 release); boot proceeds on current; see $install_dir/releases/state.json"
            _js_history_append "$journal" "halt" \
                "sweep: stale flipped $kind txn target=$target has no in_flight.displaced_version (old-format txn) — halt-for-human, txn left in place" \
                || true
            _log "HALT: journal sweep: flipped txn (age ${age}s) with no in_flight.displaced_version (old-format) — halt-for-human, boot proceeds on current"
            _js_sweep_release "$install_dir" "$lock_held"
            return 0
        fi
        _notify_once "sweep-commit" \
            "sweep-commit-and-continue: stale flipped $kind txn (target=$target, prev=$prev, displaced_version=$displaced_v) — operator declared previous rollback_safe:false AND preflight emitted intent_flip evidence; commit+continue at target (NOT sweep-rollback)"
        _js_journal_update "$journal" "current" "\"$target\"" \
            || { _log "WARN: journal sweep: commit-and-continue journal current update FAILED — boot proceeds on existing current"; _js_sweep_release "$install_dir" "$lock_held"; return 0; }
        _js_journal_update "$journal" "previous" "\"$displaced_v\"" \
            || { _log "WARN: journal sweep: commit-and-continue previous=$displaced_v update FAILED — boot proceeds on existing previous"; }
        _js_journal_update "$journal" "in_flight" "null" \
            || { _log "WARN: journal sweep: commit-and-continue in_flight clear FAILED — boot proceeds"; }
        _js_history_append "$journal" "boot_sweep_commit_and_continue" \
            "sweep: flipped $kind txn (target=$target, prev=$prev, displaced_version=$displaced_v, owner pid ${owner:-?}, age ${age}s) committed and continued — intent_flip evidence + prev rollback_safe:false (operator-declared incompatibility); boot proceeds at target" \
            || { _log "WARN: journal sweep: commit-and-continue history append FAILED — boot proceeds"; }
        _js_defer_counter_delete "$install_dir" "$run_id"
        _log "BOOT-SWEEP COMMIT-AND-CONTINUE: stale flipped $kind txn (age ${age}s, target=$target, prev=$prev, displaced_version=$displaced_v) — operator declared prev rollback_safe:false + preflight emitted intent_flip evidence; boot proceeds at target (NOT sweep-rollback; txn closed, counter NOT incremented, no cooldown)"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    # ── rung 3: classify the non-green observation (AM-P2-10.1) ──
    case "$probe_class" in
        broken)
            # BROKEN ⇒ the EXISTING chain fires NOW (the evidence is
            # unambiguous: the daemon answered and its zero-DB liveness
            # endpoint is broken). The count is waived.
            _js_sweep_rollback_chain "$install_dir" "$journal" "$json" "$inf" "$target" \
                "$prev" "$age" "$owner" "$kind" "broken" "$lock_held"
            return 0
            ;;
    esac
    # AMBIGUOUS — the bounded absent-ceiling (AM-P2-10.2).
    counter_path="$(_js_defer_counter_path "$install_dir" "$run_id")"
    absent="$(_js_defer_counter_read "$counter_path" absent)"
    mismatch="$(_js_defer_counter_read "$counter_path" mismatch)"
    unready="$(_js_defer_counter_read "$counter_path" unready)"
    broken="$(_js_defer_counter_read "$counter_path" broken)"
    green="$(_js_defer_counter_read "$counter_path" green)"
    # never-green-observed (AM-P2-10.3): the sticky flag AND the journal
    # check (the AM-P2-1 crash-prefix shape — a serving_green_commit row
    # with the txn still open). EITHER ⇒ the ceiling is PERMANENTLY
    # ineligible for this txn ⇒ defer (the operator backstop).
    if [ "$green" = "1" ] \
       || _js_history_has_event_for_target "$journal" "serving_green_commit" "$target" "" \
       || _js_history_has_event_for_target "$journal" "quarantine_cleared_serving_green" "$target" ""; then
        _js_serving_defer "$journal" "$target" "$run_id" "$age" \
            "never-green-observed-violated" \
            "a GREEN observation was recorded for this txn — the absent-ceiling is permanently ineligible (the owner ruling: a serving-green release is never rolled back)"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    # the qualifying-observation rule (AM-P2-10.2): owner-dead ∧ intent_flip
    # ∧ never-green-observed — all hold here (the owner-liveness conjunct
    # above guarantees owner-dead ∨ heartbeat-stale; the ceiling needs
    # owner-DEAD specifically).
    if [ "$owner_dead" != "1" ] || [ "$intent_flip" != "1" ]; then
        _js_serving_defer "$journal" "$target" "$run_id" "$age" \
            "observation-not-qualifying" \
            "the observation is not ceiling-eligible (owner-dead ∧ intent_flip ∧ never-green-observed must ALL hold) — no count, no rollback"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    # the T15 hook's own observation (the child just exited ⇒ the port is
    # absent BY CONSTRUCTION) is never counted when the child exit was a
    # tempfail-class (75) exit — a persistent tempfail is normally "PG
    # unreachable?", an ENVIRONMENTAL failure, and converting a PG outage
    # into a release rollback would repeat tonight's punishment class.
    if [ "${_JS_SWEEP_HOOK_EXIT:-0}" = "75" ]; then
        _js_serving_defer "$journal" "$target" "$run_id" "$age" \
            "tempfail-exit-not-counted" \
            "the child exited 75 (tempfail — normally an environmental failure); the observation is NOT counted toward the absent-ceiling"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    case "$probe_class" in
        absent) absent=$(( absent + 1 )) ;;
        mismatch) mismatch=$(( mismatch + 1 )) ;;
        unready) unready=$(( unready + 1 )) ;;
    esac
    total=$(( absent + mismatch + unready ))
    _js_defer_counter_write "$counter_path" "$absent" "$mismatch" "$unready" "$broken" "$green" \
        || _log "WARN: journal sweep: defer counter write FAILED ($counter_path) — the tally restarts (bounded, not unbounded)"
    if [ "$total" -ge "$SWEEP_DEFER_CEILING" ]; then
        _log "journal sweep: the BOUNDED ABSENT-CEILING reached (qualifying observations=$total ≥ ceiling=$SWEEP_DEFER_CEILING; owner-dead ∧ intent_flip ∧ never-green-observed) — the existing auto-rollback chain fires"
        _js_sweep_rollback_chain "$install_dir" "$journal" "$json" "$inf" "$target" \
            "$prev" "$age" "$owner" "$kind" "absent-ceiling" "$lock_held"
        return 0
    fi
    _js_serving_defer "$journal" "$target" "$run_id" "$age" \
        "ambiguous-$probe_class" \
        "qualifying observations=$total of ceiling=$SWEEP_DEFER_CEILING — deferred; the rollback fires only when the ceiling predicate holds"
    _js_sweep_release "$install_dir" "$lock_held"
    return 0
}

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
    local started_epoch age now hb hb_stale owner_dead
    if ! started_epoch="$(_js_iso_to_epoch "$started")" 2>/dev/null; then
        _log "WARN: journal sweep: in_flight started_at unparseable ('$started') — leaving untouched, boot proceeds"
        return 0
    fi
    now="$(_now)"
    age=$(( now - started_epoch ))

    # comp3 (r-f82e): liveness fast-path helpers (mirror of lib.sh's
    # _txn_heartbeat_stale + _txn_owner_dead). Same contract:
    #   hb_stale=1 → in_flight.last_heartbeat > HEARTBEAT_STALE_S old
    #                (or missing/garbage → stale);
    #   owner_dead=1 → kill -0 says owner_pid is dead (EPERM=alive,
    #                   matches the lock_file _pid_alive above);
    # The fast path runs BEFORE the freshness gate: hb_stale +
    # owner_dead → reclaim NOW (skip the SWEEP_STALE_S wait). Closes
    # the r-f82e gap where the executor died ~2s in, never wrote a
    # heartbeat, the reaper also died, and the sweep would have held
    # the txn for the full 600s. Heartbeat fresh OR owner alive →
    # fall through to the freshness gate (the conservative reading).
    hb="$(_js_json_field "$inf" "last_heartbeat" 2>/dev/null)" || hb=""
    hb_stale=1
    if printf '%s' "$hb" | grep -Eq '^[0-9]+$' \
       && [ $(( now - hb )) -le "$HEARTBEAT_STALE_S" ]; then
        hb_stale=0
    fi
    owner_dead=1
    if [ -n "$owner" ] && printf '%s' "$owner" | grep -Eq '^[0-9]+$' \
       && _js_pid_alive_bounded "$owner" 2>/dev/null; then
        owner_dead=0
    fi

    # Fresh txn (≤ SWEEP_STALE_S) → the owner may still be alive. Leave it
    # UNLESS the liveness fast path fires (hb_stale + owner_dead →
    # reclaim now). The freshness check below is the conservative reading
    # when the fast path doesn't apply.
    if [ "$age" -le "$SWEEP_STALE_S" ]; then
        if [ "$hb_stale" = "1" ] && [ "$owner_dead" = "1" ]; then
            _log "journal sweep: comp3 fast path triggered (hb_stale=1 owner_dead=1 age=${age}s ≤ SWEEP_STALE_S=${SWEEP_STALE_S}s) — reclaiming (flipped=${flipped:-—})"
        else
            _log "journal sweep: in_flight $kind txn (target=${target:-?}) is fresh (${age}s ≤ ${SWEEP_STALE_S}s, hb_stale=$hb_stale owner_dead=$owner_dead) — leaving alone"
            return 0
        fi
    fi

    # Stale (> SWEEP_STALE_S) — OR fast path above — → owner presumed
    # dead (R1.3: the 600s gate is the primary race guard). All
    # mutations below serialize on the D5 lock; a busy-but-live pipeline
    # (fresh heartbeat / live owner) makes the sweep DEFER (return 0)
    # rather than block boot.
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
    # W2 guard (B4×B3): heal ONLY when the journal's own `current` differs
    # from the txn target — the flip moves the symlink OFF the journaled
    # baseline, so journal current ≠ target is what proves a flip happened.
    # A same-version re-promote opens its txn while journal current already
    # == target (the symlink never moves), so an aged PRE-flip abort of it
    # also satisfies cur_link == target; healing there would sweep-rollback
    # + quarantine + count the HEALTHY serving version (B4's current-at-LKG
    # invariant broken for the same-version case). With journal current ==
    # target the txn is genuinely pre-flip → the clear branch below owns it.
    # A missing `current` field (degenerate/hand-edited journal) leaves
    # jcur="" ≠ target → the symlink evidence still heals, as before.
    local cur_link jcur
    cur_link="$(readlink "$install_dir/current" 2>/dev/null)" || cur_link=""
    jcur="$(_js_json_field "$json" "current" 2>/dev/null)" || jcur=""
    if [ "$flipped" != "true" ] && [ -n "$target" ] \
       && [ "$cur_link" = "releases/$target" ] \
       && [ "$jcur" != "$target" ]; then
        _log "journal sweep: flipped marker is false but current already -> releases/$target while journal current=${jcur:-<none>} (owner died in the atomic_flip/mark_flipped window) — treating as flipped"
        flipped="true"
    fi

    local rc=0
    if [ "$flipped" = "true" ]; then
        # ── Phase 2 (promote-handback-halt): the SPLIT closer ──────────────
        # The disposition of a stale, already-FLIPPED promote txn is now
        # made on OBSERVED SERVING HEALTH (the four-rung ladder in
        # `_js_sweep_flipped_disposition`): serving-green ⇒ commit-close
        # (stay; no quarantine / counter / cooldown), genuinely unhealthy
        # ⇒ the EXISTING auto-rollback chain (BROKEN ⇒ immediate;
        # rollback_requested ⇒ immediate; AMBIGUOUS ⇒ the bounded
        # absent-ceiling), everything else ⇒ a journaled DEFER.
        #
        # The lock is ALREADY held here (this function took it); the
        # helper re-reads and re-validates the txn UNDER it (the
        # launcher's revalidate-under-lock discipline) and then disposes of
        # the txn. ONE implementation — the T15 run-loop hook calls the
        # same helper (it takes the lock itself).
        _js_sweep_flipped_disposition "$install_dir" 1
        _js_lock_release "$install_dir"
        return 0
    fi
    # ── Stale pre-flip txn → clear, boot proceeds ────────────────────────
    # AM-P2-18 — the owner-liveness conjunct is a precondition of EVERY
    # stale-path branch: a stale PRE-FLIP txn whose owner is ALIVE (fresh
    # heartbeat) is LEFT OPEN. The 900s ceremony cap exceeds the 600s
    # staleness gate, so a slow-but-live promote is reachable — clearing
    # its txn would destroy a live promote's bookkeeping (the flip would
    # later find no open txn). Disclosed consequence: such a txn survives
    # to the next sweep (the safe direction; the operator/runbook owns the
    # "txn left open" shape).
    if [ "$owner_dead" = "0" ] && [ "$hb_stale" = "0" ]; then
        _log "journal sweep: stale pre-flip $kind txn (age ${age}s, target=${target:-?}) — owner ALIVE (heartbeat fresh) — leaving the txn OPEN (AM-P2-18 owner-liveness conjunct)"
        _js_lock_release "$install_dir"
        return 0
    fi
    _log "journal sweep: stale pre-flip $kind txn (age ${age}s, target=${target:-?}, owner pid ${owner:-?}) — clearing, boot proceeds"
    _js_history_append "$journal" "sweep" \
        "sweep: orphaned pre-flip $kind txn (target=${target:-?}, owner pid ${owner:-?}, age ${age}s) cleared — never flipped" \
        || { _log "WARN: journal sweep: history append failed"; rc=1; }
    _js_journal_update "$journal" "in_flight" "null" \
        || { _log "WARN: journal sweep: in_flight clear failed"; rc=1; }

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

        # ── Phase 2 (promote-handback-halt): the T15 run-loop re-sweep hook ──
        # AM-P2-7 / AM-P2-17: after each child exit the launcher re-runs the
        # NARROW flipped-branch disposition helper — NOT the whole
        # `_journal_sweep` (the stale PRE-FLIP clear is explicitly OUT of
        # scope: the hook runs mid-run-loop, exactly when a promote may be
        # in flight, and clearing a pre-flip txn would destroy a live
        # promote's bookkeeping).
        #
        # Its value: a `rollback_requested` written by the daemon during an
        # up-window is EXECUTED at the next child cycle instead of waiting
        # for a launcher boot that may never come (the tempfail-75 and
        # clean-exit-0 shapes produce no further launcher boot).
        #
        # The hook's OWN observation (the child just exited ⇒ the port is
        # absent BY CONSTRUCTION) is never used to infer rollback
        # eligibility beyond the ruled predicate, and a TEMPFAIL-class
        # (exit 75) child exit is NEVER counted toward the absent-ceiling
        # (a persistent tempfail is normally "PG unreachable?" — an
        # ENVIRONMENTAL failure; converting a PG outage into a release
        # rollback would repeat tonight's punishment class).
        #
        # The hook is NARROW and IDEMPOTENT: after a commit-close or a
        # rollback the txn is closed ⇒ the helper finds no stale flipped
        # txn ⇒ no action.
        _JS_SWEEP_HOOK_EXIT="$child_exit"
        _js_sweep_flipped_disposition "$INSTALL_DIR" 0
        _JS_SWEEP_HOOK_EXIT=0

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

    # Allocator cap (resource-cost-control O1, 2026-10-18): default glibc
    # to 2 malloc arenas BEFORE the daemon child starts. MALLOC_ARENA_MAX
    # is a libc tunable parsed at libc init — before the child's main() —
    # so it must arrive via the environment, not an in-process setdefault.
    # Default-only (setdefault semantics): a MALLOC_ARENA_MAX staged in
    # .env (already exported above) wins over this default. Exported here
    # in main(), so every `run_loop` cycle's child inherits it.
    : "${MALLOC_ARENA_MAX:=2}"
    export MALLOC_ARENA_MAX

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
