#!/bin/bash
# ============================================================================
# scripts/upgrade/lib.sh — shared library for the staged release/upgrade
# pipeline (Self-Restart/Self-Upgrade Phase 2, P2.1 — plan D1..D6, ADR-004/
# 005/009/012 as reconciled with the Phase-2 architect rulings D-FA4.x/D-FA5.x)
# ============================================================================
# SOURCED (never executed directly) by stage.sh / promote.sh / rollback.sh /
# status.sh. Provides:
#
#   resolve_env <target>      3-env topology table + fail-closed sandbox rules
#   require_live_guard        TARGET=live needs ENSEMBLE_UPGRADE_LIVE=1 (exit 78;
#                             mirrors deploy.sh:139-148 with the upgrade guard var)
#   journal_*                 atomic read/write of releases/state.json (D4:
#                             temp-file + mv; .torn rejects torn writes)
#   lock_acquire/_release     rollback.lock.d mkdir-lock (D5/D-FA5.1: mkdir IS
#                             the acquire; owner/run_id/heartbeat; stale-break
#                             >300s via mv to rollback.lock.stale.<pid>)
#   integrity_verify          per-file sha256 tree verification of a release
#                             against its manifest.json (D-FA4.4) + no-.env
#                             invariant + current-symlink resolution
#   _probe                    health-gate probe (2s sleep, curl max-time 5s —
#                             the same budget as deploy.sh phase 5)
#
# ENV DISCIPLINE (D-FA4.6 + test-strategy §5):
#   - the resolved triple (INSTALL_DIR / PORT / POSTGRES_DB) is asserted and
#     echoed by every action script before doing anything;
#   - NO literal live-port number exists anywhere under scripts/upgrade/ (the
#     D-FA4.6 rule; the live port is resolved from the live install dir's
#     staged .env — ADR-014: PORT is staged env state, not a script constant —
#     and resolve_env fails CLOSED when it is absent);
#   - sandbox has NO defaults that could resolve to live: INSTALL_DIR + PORT
#     are explicit requirements.
#
# NO NETWORK FETCH anywhere in this pipeline (ADR-009 D3): builds come from
# the LOCAL checkout; no VCS pull/fetch/clone is ever issued.
#
# Bash 3.2 / BSD tools only (macOS). No flock(1).

# NOTE on patterns: agent dir names contain glob metacharacters (e.g.
# tidier[v2]) — every ${var#pattern} / ${var%pattern} string op that
# interpolates such names MUST quote the interpolated part
# (${var#*"${key}"}) so it matches literally. Do NOT add `set -f` here:
# pathname globbing IS used (retention_evict release scan); the quoted-
# pattern form is the load-bearing fix.
# ============================================================================

# Upgrace namespace prefix for every log line (callers set LOG_TAG before
# sourcing if they want their own name; default "upgrade").
LOG_TAG="${LOG_TAG:-upgrade}"

# Health-gate budgets (seconds) — same numbers as deploy.sh phase 5.
LIVEZ_BUDGET_S="${LIVEZ_BUDGET_S:-60}"
READYZ_BUDGET_S="${READYZ_BUDGET_S:-120}"
# Post-flip soak (ADR-005 gate: 300s). Overridable for sandbox drills only
# (ENSEMBLE_PROMOTE_SOAK_S); production default stays 300.
SOAK_S_DEFAULT=300

# Rollback window / anti-flapping (ADR-005 D2, APPROVED).
ROLLBACK_CAP_24H=3            # max rollbacks per 24h window (entry-side)
COOLDOWN_S=600                # 10 min cooldown after an auto-rollback
SWEEP_STALE_S=600             # journal in_flight older than this = orphaned

# Lock protocol (D5/D-FA5.1).
LOCK_HEARTBEAT_S=30           # live owner rewrites heartbeat this often
LOCK_STALE_S=300              # heartbeat older than this = stale-breakable
LOCK_WAIT_S_DEFAULT=15        # bounded wait for a busy lock, never forever

# Retention (ADR-004): keep 3 releases; previous pinned.
RETENTION_KEEP=3

_log()  { printf '%s[%s]: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "$*"; }
_logv() { printf '%s\n' "$*"; }                       # verbatim (JSON etc.)
_warn() { printf '%s[%s]: WARN: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "$*" >&2; }

# _now_epoch — seconds since epoch (BSD-safe).
_now_epoch() { date +%s; }

# _now_iso — UTC ISO-8601 timestamp (journal fields are ISO).
_now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# _iso_to_epoch <iso-ts> — parse journal ISO timestamps; 0 on garbage
# (callers treat 0 as "unknown age" and fail CLOSED on the decision that
# matters: a sweep never fires on an unparseable fresh-looking txn).
_iso_to_epoch() {
    local ts="$1" epoch
    epoch="$(date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s 2>/dev/null)" || return 1
    [ -n "$epoch" ] || return 1
    printf '%s' "$epoch"
}

# _sha256 <file> — BSD shasum.
_sha256() { shasum -a 256 "$1" 2>/dev/null | awk '{print $1}'; }

# _canon_dir <dir> — PHYSICAL path of an existing dir (cd + pwd -P: resolves
# symlinks AND normalizes trailing slashes / dot components). Fallback: the
# raw string when the dir cannot be entered (an unresolvable dir cannot be
# an alias of a live dir that exists; the caller's existence check rejects
# it right after). Bash 3.2/BSD-safe (no readlink -f on stock macOS).
_canon_dir() { (cd "$1" 2>/dev/null && pwd -P) || printf '%s' "$1"; }

# _json_escape <string> — minimal JSON string escaper (quotes + backslash +
# control chars via printf %s safe subset). Bash 3.2-safe.
_json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\t'/\\t}"
    s="${s//$'\r'/\\r}"
    printf '%s' "$s"
}

# _json_field <json> <key> — crude top-level "key": "value" / number / bool
# extractor for FLAT json objects written by this pipeline. Handles nested
# objects only via _json_sub (below). Not a general parser: the journal is
# ours, and its shape is the D4 contract.
_json_field() {
    local json="$1" key="$2" rest
    rest="${json#*"${key}"}"
    [ "$rest" = "$json" ] && return 1
    rest="${rest#*:}"
    # trim leading whitespace, then dispatch on type
    rest="${rest#"${rest%%[![:space:]]*}"}"
    if [ "${rest:0:1}" = "\"" ]; then
        rest="${rest:1}"                 # opening quote
        rest="${rest%%\"*}"              # up to the closing quote
    else
        rest="${rest%%\,*}"              # number / bool / null
        rest="${rest%%\}*}"
    fi
    # trim whitespace
    rest="${rest#"${rest%%[![:space:]]*}"}"
    rest="${rest%"${rest##*[![:space:]]}"}"
    printf '%s' "$rest"
}

# _json_has <json> <key> — key present at top level?
_json_has() {
    case "$1" in
        *"\"$2\""*) return 0 ;;
        *) return 1 ;;
    esac
}

# _json_sub <json> <key> — extract a nested object/array value by bracket
# counting from `"key":`. Prints the raw {…} / […] text.
_json_sub() {
    local json="$1" key="$2" rest char depth start out=""
    rest="${json#*"${key}"}"
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

# ── Target resolution (D1 topology table) ────────────────────────────────────
#
# Sets: UP_TARGET, INSTALL_DIR, PORT, POSTGRES_DB, SELF_ENV_MARKER.
# Exits 78 on any unresolved element (fail-closed; D-FA4.6).
resolve_env() {
    local target="${1:-}"
    UP_TARGET="$target"
    SELF_ENV_MARKER="$target"
    case "$target" in
        demo)
            INSTALL_DIR="$HOME/agents-ensemble-demo"
            PORT=7979
            POSTGRES_DB=ensemble_demo
            ;;
        live)
            # Live topology: install dir + DB from the table; PORT from the
            # staged live .env (ADR-014 — no literal live port in this tree,
            # test-strategy §5.2). Execution is USER-GATED (require_live_guard).
            INSTALL_DIR="$HOME/agents-ensemble"
            POSTGRES_DB=ensemble_prod
            if [ -f "$INSTALL_DIR/.env" ]; then
                PORT="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}PORT[[:space:]]*=[[:space:]]*//p' "$INSTALL_DIR/.env" | head -1 | tr -d '\r')"
                PORT="${PORT%\"}"; PORT="${PORT#\"}"
                PORT="${PORT%\'}"; PORT="${PORT#\'}"
            else
                PORT=""
            fi
            if ! printf '%s' "$PORT" | grep -Eq '^[0-9]+$'; then
                _warn "live target: no resolvable PORT in $INSTALL_DIR/.env — refusing (fail-closed)"
                exit 78
            fi
            ;;
        sandbox)
            # Sandbox: EXPLICIT overrides only. No default that could ever
            # resolve to live (by construction there is no default at all
            # for dir/port). Ambient shells on this host may export the
            # LIVE daemon's env (PORT/POSTGRES_DB of the prod install) —
            # a sandbox NEVER inherits those values:
            #   - PORT must be explicitly numeric AND must not be the live
            #     install's staged port (resolved from ~/agents-ensemble/.env,
            #     no literal), the demo port, or the dev (repo) port;
            #   - POSTGRES_DB equal to another env's DB name is ignored
            #     (warned) and replaced by the sandbox default;
            #   - INSTALL_DIR must not be the demo/live install dir.
            INSTALL_DIR="${INSTALL_DIR:-}"
            PORT="${PORT:-}"
            POSTGRES_DB="${POSTGRES_DB:-}"
            # M1 (council rework Batch C): compare PHYSICAL paths, not raw
            # strings — a symlink alias or a trailing-slash spelling of the
            # live/demo dir passed the old string match. Both sides go
            # through _canon_dir (cd + pwd -P), so aliases normalize to the
            # real install dir and are refused; an unresolvable INSTALL_DIR
            # falls back to its raw string and is rejected by the existence
            # check below anyway.
            local canon_sbx canon_live canon_demo
            canon_sbx="$(_canon_dir "$INSTALL_DIR")"
            canon_live="$(_canon_dir "$HOME/agents-ensemble")"
            canon_demo="$(_canon_dir "$HOME/agents-ensemble-demo")"
            case "$canon_sbx" in
                "$canon_live"|"$canon_demo")
                    _warn "sandbox INSTALL_DIR must be a throwaway dir — refusing to operate on $INSTALL_DIR"
                    exit 78
                    ;;
            esac
            if [ -z "$INSTALL_DIR" ] || [ ! -d "$INSTALL_DIR" ]; then
                _warn "sandbox target requires an existing INSTALL_DIR (got '${INSTALL_DIR:-<empty>}') — e.g. TARGET=sandbox INSTALL_DIR=/tmp/ens-sbx PORT=8377"
                exit 78
            fi
            if ! printf '%s' "$PORT" | grep -Eq '^[0-9]+$'; then
                _warn "sandbox target requires an explicit numeric PORT (got '${PORT:-<empty>}')"
                exit 78
            fi
            case "$PORT" in
                7979)
                    _warn "sandbox PORT 7979 is the DEMO port — refusing (own-port discipline, test-strategy §5)"
                    exit 78
                    ;;
                8079)
                    # the repo dev daemon's port (dev.sh) — a sandbox must
                    # never collide with it either (review m5: the sandbox
                    # guard refuses demo + live-staged ports; dev completes
                    # the triple)
                    _warn "sandbox PORT 8079 is the DEV (repo) port — refusing (own-port discipline, test-strategy §5)"
                    exit 78
                    ;;
            esac
            # Live-port cross-check (own-port discipline, test-strategy §5).
            # FAIL CLOSED (M2): a live .env that EXISTS but cannot be read
            # must not silently disable the guard — that is exactly how a
            # sandbox ends up on the live port. Unreadable/unsourcedable →
            # refuse the sandbox outright. An ABSENT .env means no live
            # install is staged on this host — nothing to collide with.
            _live_env="$HOME/agents-ensemble/.env"
            if [ -e "$_live_env" ] || [ -L "$_live_env" ]; then
                if [ -d "$_live_env" ] || [ ! -r "$_live_env" ] \
                   || [ ! -f "$_live_env" ]; then
                    _warn "sandbox guard: live install .env at $_live_env exists but is NOT a readable file — cannot verify PORT collision — refusing (fail-closed)"
                    exit 78
                fi
                _live_port_resolve="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}PORT[[:space:]]*=[[:space:]]*//p' "$_live_env" 2>/dev/null | head -1 | tr -d '\r\"'"'"'')"
                if [ -n "$_live_port_resolve" ] && [ "$PORT" = "$_live_port_resolve" ]; then
                    _warn "sandbox PORT equals the LIVE install's staged port — refusing (live-isolation by construction)"
                    exit 78
                fi
            fi
            case "$POSTGRES_DB" in
                ensemble_prod|ensemble_demo|ensemble_dev|"")
                    [ -n "$POSTGRES_DB" ] && _warn "sandbox POSTGRES_DB '$POSTGRES_DB' belongs to another env (or ambient leak) — using the sandbox default"
                    POSTGRES_DB=ensemble_sandbox
                    ;;
            esac
            ;;
        *)
            _warn "unknown target '${target:-<empty>}' (demo|live|sandbox)"
            exit 78
            ;;
    esac
    export UP_TARGET INSTALL_DIR PORT POSTGRES_DB
}

# echo_env_triple — every action echoes the resolved triple (D-FA4.6).
echo_env_triple() {
    _log "resolved env: target=$UP_TARGET dir=$INSTALL_DIR port=$PORT db=$POSTGRES_DB"
}

# require_live_guard <target> — live needs ENSEMBLE_UPGRADE_LIVE=1 else exit
# 78 (deploy.sh:139-148 pattern with the upgrade-flavored guard variable).
require_live_guard() {
    if [ "$1" = "live" ] && [ "${ENSEMBLE_UPGRADE_LIVE:-0}" != "1" ]; then
        cat >&2 <<EOF
${LOG_TAG}: REFUSING to operate on live.
  The upgrade pipeline targets DEMO (\$HOME/agents-ensemble-demo, :7979,
  ensemble_demo) and SANDBOXES (own dir + port + throwaway DB). Live is
  the running orchestrator of Ari and all live agents — out of bounds
  for this initiative by user directive; execution belongs to the user
  after the P2.3 promotion ladder.
  To operate on live anyway:   ENSEMBLE_UPGRADE_LIVE=1 <script> live
EOF
        exit 78
    fi
}

# ── Journal (D4: releases/state.json — single durable state) ────────────────
#
# Schema (interface contract — the launcher sweep [T7, separate owner] and
# the P2.2 Python journal module implement against EXACTLY these fields;
# do not rename):
#   {
#     "current": "<ver>|null", "previous": "<ver>|null",
#     "in_flight": null | { "kind": "promote|rollback|sweep_rollback",
#                           "target": "<ver>", "started_at": "<iso>",
#                           "flipped": false, "owner_pid": <int> },
#     "rollback_window_count": { "24h": <int>, "window_start": "<iso>" },
#     "cooldown_until": "<iso>|null",
#     "quarantined": ["<ver>", ...],
#     "history": [ { "ts": "<iso>", "event": "commit|rollback|quarantine|sweep|halt", "detail": "..." } ]
#   }

journal_path() { printf '%s/releases/state.json' "$INSTALL_DIR"; }

# journal_init — create the empty journal if absent (idempotent).
journal_init() {
    local jp
    jp="$(journal_path)"
    [ -f "$jp" ] && return 0
    mkdir -p "$(dirname "$jp")"
    journal_write '{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}'
}

# journal_read — print journal JSON; exit 1 when absent/unreadable/torn.
# Torn-write detection is CONTENT-based (Batch C doc correction — earlier
# comments described a ".torn marker sibling" mechanism that never existed
# in code; there is NO .torn file, and none is needed): our writers never
# touch the target in place (temp-file + mv is atomic), so a torn state.json
# can only come from an OUT-of-discipline writer — and it is caught by (a)
# the empty-file check and (b) the brace/bracket-balance scan below, which
# rejects truncated JSON. Nothing under releases/ named *.torn is produced
# or consumed by this pipeline.
journal_read() {
    local jp
    jp="$(journal_path)"
    if [ ! -f "$jp" ]; then
        return 1
    fi
    local json
    json="$(cat "$jp" 2>/dev/null)" || return 1
    if [ -z "$json" ]; then
        _warn "journal at $jp is EMPTY (torn write?) — refusing to trust it"
        return 1
    fi
    # brace/bracket balance sanity — rejects truncated JSON.
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
        _warn "journal at $jp is MALFORMED (torn write: unbalanced json) — refusing to trust it"
        return 1
    fi
    printf '%s' "$json"
}

# journal_write <json> — ATOMIC: temp file in the same dir + mv -f (D4).
journal_write() {
    local json="$1" jp tmp
    jp="$(journal_path)"
    mkdir -p "$(dirname "$jp")"
    tmp="$jp.tmp.$$"
    if ! printf '%s\n' "$json" > "$tmp" 2>/dev/null; then
        rm -f "$tmp" 2>/dev/null
        _warn "journal write FAILED (cannot create temp in $(dirname "$jp"))"
        return 1
    fi
    if ! mv -f "$tmp" "$jp"; then
        rm -f "$tmp" 2>/dev/null
        _warn "journal write FAILED (mv into place)"
        return 1
    fi
    return 0
}

# journal_update <field> <raw-json-or-scalar> — read-modify-write ONE top-level
# field atomically. <raw> is spliced verbatim (objects/arrays/strings must
# arrive pre-quoted; numbers/bools/null raw). Simple + sufficient for the D4
# schema; avoids a jq dependency (none in the repo's shell toolchain).
journal_update() {
    local field="$1" raw="$2" json rest head tail out="" c depth
    json="$(journal_read)" || return 1
    if ! _json_has "$json" "$field"; then
        _warn "journal_update: field '$field' not found (schema drift?)"
        return 1
    fi
    # Walk the object; replace the value that follows "field":.
    rest="$json"
    while [ -n "$rest" ]; do
        case "$rest" in
            *"\"$field\""*)
                # head = prefix up to (and including) the last "field"
                # occurrence (% removes the shortest suffix containing it);
                # rest = what follows the occurrence. NOTE: escaped quotes in
                # the pattern — raw "..." here would be swallowed by the
                # outer double-quote context (verified).
                head="${rest%*\"${field}\"*}\"${field}\""
                rest="${rest#*\"${field}\"}"
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
                out="$head:$raw$rest"
                break
                ;;
            *)
                break
                ;;
        esac
    done
    [ -n "$out" ] || { _warn "journal_update: splice failed for '$field'"; return 1; }
    journal_write "$out"
}

# journal_in_flight — print the in_flight object verbatim, or nothing.
journal_in_flight() {
    local json
    json="$(journal_read)" || return 1
    _json_sub "$json" "in_flight"
}

# journal_history_append <event> <detail> — append to history (newest last).
journal_history_append() {
    local event="$1" detail="$2" json hist new
    json="$(journal_read)" || return 1
    hist="$(_json_sub "$json" "history")"
    if [ -z "$hist" ] || [ "$hist" = "[]" ]; then
        new="[{\"ts\":\"$(_now_iso)\",\"event\":\"$(_json_escape "$event")\",\"detail\":\"$(_json_escape "$detail")\"}]"
    else
        # hist is [ {...}, ... ] — strip the closing bracket, append entry
        new="${hist%]}, {\"ts\":\"$(_now_iso)\",\"event\":\"$(_json_escape "$event")\",\"detail\":\"$(_json_escape "$detail")\"}]"
    fi
    journal_update "history" "$new"
}

# journal_open_txn <kind> <target> — set in_flight {kind,target,started_at,
# flipped:false,owner_pid}. Refuses (returns 1) if a txn is already open.
journal_open_txn() {
    local kind="$1" target="$2" json existing
    json="$(journal_read)" || return 1
    existing="$(_json_sub "$json" "in_flight")"
    if [ -n "$existing" ] && [ "$existing" != "null" ]; then
        _warn "journal_open_txn: an in_flight txn already exists — refusing (pipeline-busy)"
        return 1
    fi
    journal_update "in_flight" \
        "{\"kind\":\"$kind\",\"target\":\"$target\",\"started_at\":\"$(_now_iso)\",\"flipped\":false,\"owner_pid\":$$}"
}

# journal_mark_flipped — in_flight.flipped = true (raw splice inside the
# in_flight object).
journal_mark_flipped() {
    local json inf new_inf
    json="$(journal_read)" || return 1
    inf="$(_json_sub "$json" "in_flight")"
    [ -n "$inf" ] || { _warn "journal_mark_flipped: no in_flight txn"; return 1; }
    new_inf="${inf//\"flipped\": false/\"flipped\": true}"
    [ "$new_inf" != "$inf" ] || new_inf="${inf//\"flipped\":false/\"flipped\":true}"
    [ "$new_inf" != "$inf" ] || { _warn "journal_mark_flipped: flipped flag not found"; return 1; }
    journal_update "in_flight" "$new_inf"
}

# journal_close_txn — in_flight = null.
journal_close_txn() {
    journal_update "in_flight" "null"
}

# journal_set_current <ver> — set current (string) + sanity echo.
journal_set_current() {
    journal_update "current" "\"$1\""
}

# journal_set_previous <ver> — set previous (string or null).
journal_set_previous() {
    if [ "$1" = "null" ]; then
        journal_update "previous" "null"
    else
        journal_update "previous" "\"$1\""
    fi
}

# journal_quarantine <ver> — append to quarantined[] (idempotent).
journal_quarantine() {
    local ver="$1" json q new
    json="$(journal_read)" || return 1
    q="$(_json_sub "$json" "quarantined")"
    case "$q" in
        *"\"$ver\""*) return 0 ;;
    esac
    if [ -z "$q" ] || [ "$q" = "[]" ]; then
        new="[\"$ver\"]"
    else
        new="${q%]}, \"$ver\"]"
    fi
    journal_update "quarantined" "$new"
}

# journal_quarantine_clear <ver> — remove from quarantined[]. Called by
# stage.sh when a version is RE-STAGED: the operator explicitly rebuilt the
# artifact, so the prior quarantine verdict no longer describes it.
journal_quarantine_clear() {
    local ver="$1" json q new
    json="$(journal_read)" || return 1
    q="$(_json_sub "$json" "quarantined")"
    case "$q" in
        *"\"$ver\""*) ;;
        *) return 0 ;;
    esac
    # rebuild the list without the entry
    new="$(printf '%s' "$q" | tr -d '[]' | tr ',' '\n' | sed 's/^ *"//;s/" *$//' | grep -v "^$ver\$" | sed 's/^/"/;s/$/"/' | paste -sd, - | sed 's/^/[/;s/$/]/')"
    [ "$new" = "[]" ] || [ "$new" = "[ ]" ] && new="[]"
    journal_update "quarantined" "$new"
}

# journal_is_quarantined <ver>.
journal_is_quarantined() {
    local json
    json="$(journal_read)" || return 1
    case "$(_json_sub "$json" "quarantined")" in
        *"\"$1\""*) return 0 ;;
        *) return 1 ;;
    esac
}

# journal_fail_loud <what> [exit_rc] — a post-flip journal mutation FAILED
# (M5, council rework Batch C). Pre-fix, promote's commit path only WARNED
# and exited 0 with the flipped symlink and the journal diverged — and a
# journal_set_current/close_txn failure left the txn OPEN + flipped:true,
# so the NEXT launcher start would sweep-ROLLBACK a HEALTHY promote.
# Here: best-effort close the txn (if ANY write still lands, closing kills
# the sweep-rollback risk), best-effort halt event, then exit NON-ZERO with
# an unmissable divergence message. Never exit 0 past a failed journal
# write. Operator remediation: repair the journal dir/file to agree with
# the `current` symlink before the next launcher start.
journal_fail_loud() {
    local what="$1" rc="${2:-1}"
    journal_close_txn 2>/dev/null || true
    journal_history_append halt "journal write FAILED ($what) — journal diverged from the flipped current symlink; best-effort txn close attempted; repair the journal before the next launcher start" 2>/dev/null || true
    printf '%s[%s]: JOURNAL DIVERGENCE: journal write FAILED at %s — best-effort txn close attempted. The env may be healthy but the journal does not agree with the current symlink; repair %s BEFORE the next launcher start (a surviving open flipped txn makes the sweep roll back a healthy promote)\n' \
        "$LOG_TAG" "${UP_TARGET:-lib}" "$what" "$(journal_path)" >&2
    exit "$rc"
}

# journal_count_rollback — increment rollback_window_count with 24h window
# rollover; arms cooldown_until (now + COOLDOWN_S) when <arm_cooldown> = 1.
# Prints the new count (0-3 form: the count AFTER this rollback).
# WINDOW ANCHOR (Batch C doc): this is a SLIDING window anchored at the
# LAST rollback, not a tumbling 24h calendar window — window_start is
# re-stamped on EVERY counted rollback, so N rollbacks are refused only
# while all N fall inside the 24h span ending at the newest one. Sparse
# rollback pairs ≥24h apart never accumulate toward the cap; a burst is
# capped at 3 regardless of how the stamps drift. Callers needing a fixed
# anchor would require a schema change (out of scope; ADR-005 D2 wording
# "3/24h" is implemented as this sliding-anchor reading).
# Window rule: first rollback opens the window; a rollback whose window_start
# is >24h old resets count to 0 and re-opens the window.
journal_count_rollback() {
    local arm_cooldown="${1:-0}" json counts cnt wstart now_epoch wstart_epoch new_cnt
    json="$(journal_read)" || return 1
    counts="$(_json_sub "$json" "rollback_window_count")"
    cnt="$(_json_field "$counts" "24h")"
    wstart="$(_json_field "$counts" "window_start")"
    [ -n "$cnt" ] || cnt=0
    case "$wstart" in ""|null) wstart="" ;; esac
    now_epoch="$(_now_epoch)"
    if [ -n "$wstart" ] && wstart_epoch="$(_iso_to_epoch "$wstart")" 2>/dev/null \
       && [ "$((now_epoch - wstart_epoch))" -ge 86400 ]; then
        cnt=0   # window rolled over — this rollback re-opens it
    fi
    new_cnt=$((cnt + 1))
    # M5: a failed counter/cooldown write must propagate — callers (promote
    # 8b bookkeeping, rollback.sh) treat rc≠0 as a journal divergence and
    # fail loud instead of exiting 0 with the anti-flapping count lost.
    journal_update "rollback_window_count" \
        "{\"24h\": $new_cnt, \"window_start\": \"$(_now_iso)\"}" || return 1
    if [ "$arm_cooldown" = "1" ]; then
        local until
        # BSD date: -v adjustments must precede the [-f fmt date] operand
        until="$(date -ju -v+${COOLDOWN_S}S -f '%Y-%m-%dT%H:%M:%SZ' "$(_now_iso)" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" \
            || until="$(_now_iso)"
        journal_update "cooldown_until" "\"$until\"" || return 1
    fi
    printf '%s' "$new_cnt"
}

# journal_rollback_count_24h — current in-window count (0 when window stale:
# a caller checking ENTRY sees the post-rollover view).
journal_rollback_count_24h() {
    local json counts cnt wstart wstart_epoch
    json="$(journal_read)" || { printf '0'; return 0; }
    counts="$(_json_sub "$json" "rollback_window_count")"
    cnt="$(_json_field "$counts" "24h")"
    wstart="$(_json_field "$counts" "window_start")"
    [ -n "$cnt" ] || cnt=0
    case "$wstart" in ""|null) printf '0'; return 0 ;; esac
    if wstart_epoch="$(_iso_to_epoch "$wstart")" 2>/dev/null \
       && [ "$(($(_now_epoch) - wstart_epoch))" -ge 86400 ]; then
        printf '0'
        return 0
    fi
    printf '%s' "$cnt"
}

# journal_cooldown_active — 0 if within cooldown (refuse entry), 1 if clear.
# FAIL CLOSED (M3): a present-but-unparseable cooldown_until (garbage stamp,
# empty string, absent field) is treated as an ACTIVE cooldown — a corrupt
# stamp must never silently disable the ADR-005 anti-flapping window. Only
# an explicit null is "no cooldown". Remediation: fix the stamp or let the
# next journal_count_rollback overwrite it.
journal_cooldown_active() {
    local json cd_epoch until_epoch
    json="$(journal_read)" || return 1
    cd_epoch="$(_json_field "$json" "cooldown_until")"
    case "$cd_epoch" in null) return 1 ;; esac
    if [ -z "$cd_epoch" ] || ! until_epoch="$(_iso_to_epoch "$cd_epoch")"; then
        _warn "cooldown_until is unparseable ('${cd_epoch:-<absent>}') — treating cooldown as ACTIVE (fail-closed, ADR-005 anti-flapping)"
        return 0
    fi
    [ "$(_now_epoch)" -lt "$until_epoch" ]
}

# ── rollback.lock.d — mkdir-based lock (D5/D-FA5.1) ─────────────────────────
#
# The PROTOCOL is the contract (launcher [T7] and the P2.2 Python journal
# module implement the identical protocol independently):
#   - mkdir "$INSTALL_DIR/releases/rollback.lock.d" is the atomic acquire
#   - contents: owner (pid), run_id, heartbeat (epoch secs, refreshed
#     ~30s by the live owner)
#   - stale: heartbeat older than LOCK_STALE_S AND owner pid dead/
#     unverifiable (W1: a live owner's lock is never broken on heartbeat
#     age alone — the stop span is un-heartbeated up to 600s) → mv the
#     dir to rollback.lock.stale.<pid> (never rmdir — racy) → re-acquire
#   - second invocation: structured "pipeline-busy run_id=…" (exit 75
#     flavor at the caller), NOT a crash
lock_dir_path() { printf '%s/releases/rollback.lock.d' "$INSTALL_DIR"; }

lock_run_id=""

# lock_acquire [wait_s] — acquire or fail after bounded wait. Sets
# lock_run_id. Returns: 0 acquired (incl. stale-break); 1 busy after wait.
lock_acquire() {
    local wait_s="${1:-$LOCK_WAIT_S_DEFAULT}" lock owner_pid hb age run_id
    lock="$(lock_dir_path)"
    mkdir -p "$(dirname "$lock")"
    lock_run_id="run-$(date +%Y%m%d-%H%M%S)-$$"
    local waited=0
    while :; do
        if mkdir "$lock" 2>/dev/null; then
            printf '%s\n' "$$" > "$lock/owner" 2>/dev/null
            printf '%s\n' "$lock_run_id" > "$lock/run_id" 2>/dev/null
            printf '%s\n' "$(_now_epoch)" > "$lock/heartbeat" 2>/dev/null
            return 0
        fi
        # exists — stale? Break only when the heartbeat is > LOCK_STALE_S AND
        # the owner pid is dead/unverifiable (W1 — mirrors _js_lock_acquire
        # in launcher.sh; the PROTOCOL is the contract, D5). A stale
        # heartbeat ALONE must NOT break the lock: promote/rollback hold it
        # un-heartbeated across the stop-script span (stop-ensemble WAIT_S
        # clamps to 600s > the 300s staleness bound), and breaking under a
        # LIVE owner lets a concurrent action trample a txn whose owner is
        # still mutating it. A missing/garbage owner pid is UNVERIFIABLE:
        # nothing to protect, the heartbeat alone breaks it (preserves
        # crash progress). kill -0 (POSIX) is the liveness test; residual
        # pid-reuse wedge (crashed owner, pid reused by a live process)
        # degrades to pipeline-busy — safer than trampling a live owner.
        hb="$(cat "$lock/heartbeat" 2>/dev/null)"
        owner_pid="$(cat "$lock/owner" 2>/dev/null)"
        run_id="$(cat "$lock/run_id" 2>/dev/null)"
        if printf '%s' "$hb" | grep -Eq '^[0-9]+$'; then
            age=$(( $(_now_epoch) - hb ))
            if [ "$age" -gt "$LOCK_STALE_S" ]; then
                local owner_live=0
                if [ -n "$owner_pid" ] && printf '%s' "$owner_pid" | grep -Eq '^[0-9]+$' \
                   && kill -0 "$owner_pid" 2>/dev/null; then
                    owner_live=1
                fi
                if [ "$owner_live" -eq 0 ]; then
                    _log "pipeline lock stale (heartbeat ${age}s old, owner pid ${owner_pid:-?} dead/unverifiable, run ${run_id:-?}) — breaking"
                    mv "$lock" "${lock}.stale.$$" 2>/dev/null || continue
                    continue
                fi
                # LIVE owner mid un-heartbeated long op (the stop span) —
                # fall through to the bounded wait/busy below; NEVER break.
            fi
        fi
        # owner process dead? (crash left a fresh-heartbeat dir) — break too
        if [ -n "$owner_pid" ] && printf '%s' "$owner_pid" | grep -Eq '^[0-9]+$' \
           && ! kill -0 "$owner_pid" 2>/dev/null; then
            _log "pipeline lock owner pid $owner_pid is dead — breaking lock"
            mv "$lock" "${lock}.stale.$$" 2>/dev/null || continue
            continue
        fi
        if [ "$waited" -ge "$wait_s" ]; then
            _log "pipeline-busy run_id=${run_id:-?} owner=${owner_pid:-?} (live) — another pipeline action holds the lock"
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
}

# lock_heartbeat — refresh the heartbeat (call ~every 30s from long ops).
# OWNERSHIP-GUARDED (B2b): only the lock's owner (per the owner file) may
# refresh it. An unguarded write from a non-owner would keep ANOTHER
# process's lock alive — masking a crashed owner — or resurrect a lock a
# stale-breaker already moved aside. This guard is what lets _probe refresh
# the heartbeat unconditionally: probes also run from lockless display
# paths (status.sh-style) and must be a no-op there.
lock_heartbeat() {
    local lock owner
    lock="$(lock_dir_path)"
    [ -d "$lock" ] || return 1
    owner="$(cat "$lock/owner" 2>/dev/null)"
    [ "$owner" = "$$" ] || return 1
    printf '%s\n' "$(_now_epoch)" > "$lock/heartbeat" 2>/dev/null
}

# lock_release — remove the lock dir (only if we still own it).
lock_release() {
    local lock
    lock="$(lock_dir_path)"
    if [ -d "$lock" ]; then
        local owner
        owner="$(cat "$lock/owner" 2>/dev/null)"
        if [ "$owner" = "$$" ]; then
            rm -rf "$lock"
            return 0
        fi
        _warn "lock_release: lock owned by pid ${owner:-?}, not us — leaving it"
        return 1
    fi
    return 0
}

# ── Integrity (T3 / D-FA4.4) ─────────────────────────────────────────────────
#
# manifest.json (written by stage.sh) — field groups (ADR-004 M5 + D-FA4.4):
#   identity:     version, binary_version, staged_at, known_schema_gen,
#                 contains_contract_phase, rollback_safe
#   launcher:     launcher_sha256
#   checksums:    binary_sha256, config_sha256,
#                 agents_manifest {path:sha256,…}, agents_tree_sha256,
#                 frontend_manifest {path:sha256,…}, frontend_tree_sha256
# All sha256 values are hex, bare.

manifest_path() { printf '%s/releases/%s/manifest.json' "$INSTALL_DIR" "$1"; }

# manifest_field <ver> <key> — print a manifest top-level string/bool/number.
manifest_field() {
    local mp json
    mp="$(manifest_path "$1")"
    [ -f "$mp" ] || return 1
    json="$(cat "$mp")" || return 1
    _json_field "$json" "$2"
}

# _tree_manifest <dir> <relprefix> — print "sha256  relpath" lines, sorted by
# relpath (deterministic; D-FA4.4 per-file map + sorted-listing hash).
_tree_manifest() {
    local dir="$1" prefix="$2" f rel
    ( cd "$dir" 2>/dev/null || exit 1
      find . -type f | sed 's|^\./||' | sort | while IFS= read -r rel; do
          [ -n "$rel" ] || continue
          printf '%s  %s\n' "$(_sha256 "$rel")" "$prefix$rel"
      done )
}

# _tree_hash <dir> — hash over the sorted listing (the tree's identity).
_tree_hash() {
    _tree_manifest "$1" "" | shasum -a 256 | awk '{print $1}'
}

# _tree_hash_of_lines <manifest-lines> — aggregate hash of ALREADY-computed
# _tree_manifest output (avoids a second tree walk).
_tree_hash_of_lines() {
    printf '%s\n' "$1" | shasum -a 256 | awk '{print $1}'
}

# _manifest_map_lines <manifest-json> <tree> — the "<tree>_manifest" flat
# {path:sha,…} map as "path sha" lines. The map is FLAT by construction
# (stage.sh writes it: no nested braces, no commas inside paths), so a
# line-based sed/tr conversion replaces the generic char-walking JSON
# parser — the latter is O(n²) in bash and costs ~30s on the real agents
# tree. Paths containing commas/quotes would break this (none exist in the
# repo; a false mismatch fails CLOSED — flagged for operator inspection).
_manifest_map_lines() {
    printf '%s' "$1" | sed -n "s/.*\"$2_manifest\": *{//p" | sed 's/}[^}]*$//' \
        | tr ',' '\n' \
        | sed -e 's/^ *"//' -e 's/" *: *"*/ /' -e 's/" *$//' \
        | sort
}

# _no_env_in_release <release_dir> — ADR-014/m6 invariant: NO .env of any
# kind inside a release dir. Returns 0 = clean; 1 = violation (names file).
_no_env_in_release() {
    local hit
    hit="$(find "$1" -name '.env' -print -quit 2>/dev/null)"
    if [ -n "$hit" ]; then
        _warn "release dir contains a .env (ADR-014 m6 invariant): $hit"
        return 1
    fi
    return 0
}

# integrity_verify <ver> — verify a staged release against its manifest.
# Exit 0 clean; exit 1 with the offending file(s) named on mismatch.
# Checks: manifest exists + readable; no .env inside; every checksummed
# component matches; tree manifests match per-file AND in aggregate.
integrity_verify() {
    local ver="$1" rd mp json rc=0
    rd="$INSTALL_DIR/releases/$ver"
    mp="$rd/manifest.json"
    if [ ! -f "$mp" ]; then
        _warn "integrity: $mp MISSING"
        return 1
    fi
    json="$(cat "$mp" 2>/dev/null)" || { _warn "integrity: $mp unreadable"; return 1; }
    _no_env_in_release "$rd" || rc=1

    # binary
    local want got
    want="$(_json_field "$json" "binary_sha256")"
    if [ -n "$want" ]; then
        got="$(_sha256 "$rd/ensemble-prod")"
        if [ "$want" != "$got" ]; then
            _warn "integrity MISMATCH: $ver/ensemble-prod (want ${want:0:12}… got ${got:0:12}…)"
            rc=1
        fi
    else
        _warn "integrity: manifest missing binary_sha256"
        rc=1
    fi
    # launcher
    want="$(_json_field "$json" "launcher_sha256")"
    if [ -n "$want" ]; then
        got="$(_sha256 "$rd/launcher.sh")"
        if [ "$want" != "$got" ]; then
            _warn "integrity MISMATCH: $ver/launcher.sh (want ${want:0:12}… got ${got:0:12}…)"
            rc=1
        fi
    else
        _warn "integrity: manifest missing launcher_sha256"
        rc=1
    fi
    # config.yaml
    want="$(_json_field "$json" "config_sha256")"
    if [ -n "$want" ]; then
        got="$(_sha256 "$rd/config.yaml")"
        if [ "$want" != "$got" ]; then
            _warn "integrity MISMATCH: $ver/config.yaml (want ${want:0:12}… got ${got:0:12}…)"
            rc=1
        fi
    fi
    # agents/frontend trees — ONE walk per tree: actual manifest lines feed
    # both the aggregate hash AND the per-file pinpoint (diff against the
    # recorded map; names the tampered/missing/extra files). No per-file
    # forks beyond the single walk.
    local sub want_hash got_hash got_lines rec_lines actual_ps key wantf dpath dline
    local tree
    for tree in agents frontend; do
        got_lines="$(_tree_manifest "$rd/$tree" "")"
        want_hash="$(_json_field "$json" "${tree}_tree_sha256")"
        got_hash="$(_tree_hash_of_lines "$got_lines")"
        if [ -z "$want_hash" ] || [ "$want_hash" != "$got_hash" ]; then
            _warn "integrity MISMATCH: $ver/$tree tree (want ${want_hash:0:12}… got ${got_hash:0:12}…)"
            rc=1
        fi
        # per-file pinpoint: recorded map (flat — sed/tr conversion, no
        # per-char JSON walk) vs the actual walk ("sha  path" → "path sha");
        # diff pinpoints every delta (tampered, missing, unrecorded)
        actual_ps="$(printf '%s\n' "$got_lines" | awk -F '  ' '{print $2" "$1}')"
        while IFS= read -r dline; do
            [ -n "$dline" ] || continue
            case "$dline" in
                "< "*) dpath="${dline#< }"; dpath="${dpath%% *}"
                       _warn "integrity MISMATCH: $ver/$tree/$dpath (differs from manifest)"
                       rc=1 ;;
                "> "*) dpath="${dline#> }"; dpath="${dpath%% *}"
                       _warn "integrity MISMATCH: $ver/$tree/$dpath (on disk, not in manifest)"
                       rc=1 ;;
            esac
        done <<EOF
$(_manifest_map_lines "$json" "$tree" | diff - <(printf '%s\n' "$actual_ps" | sort) 2>/dev/null | grep -E '^[<>]' || true)
EOF
    done
    return "$rc"
}

# verify_current_release — resolve the `current` symlink + integrity-verify
# the release it points at. Exit 0 clean; 1 mismatch/unresolvable.
verify_current_release() {
    local cur_link="$INSTALL_DIR/current" target
    if [ ! -L "$cur_link" ]; then
        _warn "current symlink missing at $cur_link (no staged release promoted yet, or layout divergence)"
        return 1
    fi
    target="$(readlink "$cur_link")"
    target="${target##*/}"
    if [ ! -d "$INSTALL_DIR/releases/$target" ]; then
        _warn "current symlink points at missing release: $target"
        return 1
    fi
    integrity_verify "$target"
}

# ── Health gate probe (deploy.sh phase-5 budget) ─────────────────────────────
# _probe <path> <budget_s> <port> — body on stdout, 0 on success; nonzero
# when the budget expires without an HTTP response. 2s sleep between tries,
# curl --max-time 5 (same as deploy.sh).
#
# B2b hardening — both halves:
#   - WALL-CLOCK budget: deadline = start-epoch + budget. The old
#     iteration counter added only the 2s sleeps, never the curl time
#     (up to 5s/try), so a "60s" /livez gate could span ~210s wall and a
#     full gate run ~640s — past LOCK_STALE_S (300) and most of
#     SWEEP_STALE_S (600). The deadline bounds the gate to its documented
#     wall seconds (+ at most one in-flight curl of 5s).
#   - LOCK HEARTBEAT every iteration: gate loops used to be the one long
#     lock-held span with NO heartbeat, so a second launcher start could
#     stale-break a LIVE owner's lock mid-gate and double-rollback
#     underneath it. lock_heartbeat is ownership-guarded — this is a no-op
#     when the caller does not hold the pipeline lock.
_probe() {
    local path="$1" budget="$2" port="$3" deadline now body=""
    deadline=$(( $(_now_epoch) + budget ))
    lock_heartbeat
    while :; do
        body="$(curl -fsS --max-time 5 "http://localhost:$port$path" 2>/dev/null)" \
            && { printf '%s\n' "$body"; return 0; }
        now="$(_now_epoch)"
        [ "$now" -lt "$deadline" ] || break
        lock_heartbeat
        sleep 2
    done
    return 1
}

# _probe_once <path> <port> — single fast fetch (status.sh display; no budget).
_probe_once() {
    curl -fsS --max-time 5 "http://localhost:$2$1" 2>/dev/null
}

# ── Promote/rollback shared mechanics (D6 + D-FA4.1 amendment) ──────────────

# stop_via_stop_script — SIGTERM-bounded, ownership-scoped stop. ALWAYS via
# scripts/stop-ensemble.sh (D6: reused, never duplicated; NEVER a raw kill).
stop_via_stop_script() {
    local stop_script
    stop_script="$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)/../stop-ensemble.sh"
    if [ ! -f "$stop_script" ]; then
        # when lib.sh is sourced from a copied tree, fall back to repo layout
        stop_script="$(pwd)/scripts/stop-ensemble.sh"
    fi
    _log "stop: ownership-scoped SINGLE-TERM via $stop_script"
    bash "$stop_script" "$INSTALL_DIR" "$PORT"
}

# launcher_swap <ver> — swap INSTALL_DIR/launcher.sh from a release's staged
# copy. MUST run in the STOPPED window (launcher + daemon both exited post
# SINGLE-TERM) — D-FA4.1 amendment: the launcher is part of the release
# surface; carrying it in-payload makes launcher/binary skew self-healing.
launcher_swap() {
    local ver="$1"
    local src="$INSTALL_DIR/releases/$ver/launcher.sh"
    if [ ! -f "$src" ]; then
        _warn "launcher_swap: no staged launcher at $src — keeping existing launcher"
        return 1
    fi
    cp "$src" "$INSTALL_DIR/launcher.sh" || { _warn "launcher_swap: copy failed"; return 1; }
    chmod +x "$INSTALL_DIR/launcher.sh"
    _log "launcher swapped from release $ver (stopped window)"
    return 0
}

# atomic_flip <ver> — rename(2)-semantics symlink flip: build current.new.$$
# then mv -f over `current` (the mv is the atomic point). The link lives at
# the INSTALL-DIR ROOT ($INSTALL_DIR/current) — the launcher's shipped
# resolver looks there FIRST (launcher.sh resolve_binary:
# $INSTALL_DIR/current/ensemble-prod, verified foundation) — and its target
# is "releases/<ver>", relative to the install dir.
atomic_flip() {
    local ver="$1"
    ln -sfn "releases/$ver" "$INSTALL_DIR/current.new.$$" || return 1
    # mv -h: swap the SYMLINK ITSELF (BSD). Plain mv would follow the
    # existing current→releases/<old> link and move the new link INTO the
    # old release dir, silently leaving `current` pointing at the OLD
    # release — verified on macOS mv(1).
    if ! mv -h -f "$INSTALL_DIR/current.new.$$" "$INSTALL_DIR/current"; then
        rm -f "$INSTALL_DIR/current.new.$$"
        return 1
    fi
    _log "current -> releases/$ver (atomic flip)"
    return 0
}

# restart_via_launcher — nohup launcher (deploy.sh phase-4 pattern; the
# launcher runs the journal sweep BEFORE binary resolution — T7).
restart_via_launcher() {
    mkdir -p "$INSTALL_DIR/data"
    ( cd "$INSTALL_DIR" && nohup ./launcher.sh >> data/launcher.log 2>&1 & )
    _log "launcher started (nohup) — logs: $INSTALL_DIR/data/launcher.log"
}

# gate_livez / gate_readyz — budgeted probes (D2). Print body; nonzero on fail.
gate_livez()  { _probe "/livez"  "$LIVEZ_BUDGET_S"  "$PORT"; }
gate_readyz() { _probe "/readyz" "$READYZ_BUDGET_S" "$PORT"; }

# gate_version <expected> — one-shot /livez version check (D2/ADR-027: the
# RUNNING daemon must self-report the manifest's binary_version).
gate_version() {
    local expected="$1" body running
    body="$(_probe_once "/livez" "$PORT")"
    [ -n "$body" ] || { _warn "version verify: /livez not answering on :$PORT"; return 1; }
    running="$(_json_field "$body" version)"
    if [ "$running" != "$expected" ]; then
        _warn "version verify MISMATCH: running=$running expected=$expected (manifest binary_version)"
        return 1
    fi
    _log "version verify OK: $running"
    return 0
}

# gate_soak <seconds> <expected_version> — ADR-005 soak: keep probing both
# endpoints through the window; any red → fail. Heartbeats the pipeline lock.
# Wall-clock deadline (B2b, same as _probe): three probes × curl max-time 5
# per iteration made the old iteration counter overcount sleeps and
# undercount curl — a "300s" soak could stretch to ~450s wall and push the
# promote outer window past SWEEP_STALE_S.
gate_soak() {
    local soak_s="$1" expected="$2" waited=0 now deadline rc
    if [ "$soak_s" -le 0 ]; then
        _log "soak skipped (0s — drill knob)"
        return 0
    fi
    _log "soak ${soak_s}s (re-probe /livez + /readyz every 30s)"
    deadline=$(( $(_now_epoch) + soak_s ))
    while :; do
        lock_heartbeat
        if ! _probe_once "/livez" "$PORT" > /dev/null; then
            _warn "soak FAILURE: /livez went red at ${waited}s"
            return 1
        fi
        if ! _probe_once "/readyz" "$PORT" > /dev/null; then
            _warn "soak FAILURE: /readyz went red at ${waited}s"
            return 1
        fi
        gate_version "$expected" > /dev/null || { _warn "soak FAILURE: version drifted at ${waited}s"; return 1; }
        now="$(_now_epoch)"
        [ "$now" -lt "$deadline" ] || break
        sleep 30
        waited=$((waited + 30))
    done
    _log "soak complete (${soak_s}s green)"
    return 0
}

# retention_evict — T8: keep RETENTION_KEEP newest (by manifest staged_at,
# dir-mtime fallback); NEVER evict current or journal previous; previous
# pinned. Explicit check: previous recorded but dir missing → loud WARN
# (the auto-rollback halt path already refuses to roll back onto it).
retention_evict() {
    local rel="$INSTALL_DIR/releases" json cur prev d name entries sorted n
    json="$(journal_read)" || return 1
    cur="$(_json_field "$json" current)"
    prev="$(_json_field "$json" previous)"
    [ "$cur" = "null" ] && cur=""
    [ "$prev" = "null" ] && prev=""
    if [ -n "$prev" ] && [ ! -d "$rel/$prev" ]; then
        _warn "retention: journal previous '$prev' has NO release dir (manual deletion?) — rollback target missing; auto-rollback would halt-for-human"
    fi
    # Count ALL releases; evict oldest non-pinned until RETENTION_KEEP
    # remain IN TOTAL (ADR-004: "retention 3 releases" — current + previous
    # are pinned members of the three, not additions to it).
    local total=0 name st
    local entries=""
    for d in "$rel"/*/; do
        [ -d "$d" ] || continue
        name="${d%/}"; name="${name##*/}"
        [ "$name" = "current" ] && continue   # the flip symlink, not a release
        # Protocol/working artifacts under releases/ are NOT releases: the
        # D5 lock dir, its stale-break leftovers, and stage temp assemblies.
        # (Latent defect exposed by the epoch-key normalization below: with
        # the old mixed ISO/epoch sort the lock dir — epoch mtime — sorted
        # lexicographically FIRST and was silently rm -rf'd as the
        # "oldest release", defeating the lock protocol mid-operation.)
        case "$name" in
            rollback.lock.d|rollback.lock.d.stale.*|.staging.*) continue ;;
        esac
        total=$((total + 1))
        [ "$name" = "$cur" ] && continue
        [ "$name" = "$prev" ] && continue
        # normalize the sort key to EPOCH: a manifest staged_at (ISO) is
        # converted; a missing manifest falls back to the dir mtime (already
        # epoch). Sorting ISO strings against epoch digits lexicographically
        # is meaningless (digits vs 'T'/'-'/'Z' compare by codepoint, not
        # time) — normalizing keeps the oldest-first eviction order correct
        # across mixed staged/unstaged release sets (review i3).
        st="$(manifest_field "$name" staged_at 2>/dev/null)"
        if [ -n "$st" ]; then
            st="$(_iso_to_epoch "$st" 2>/dev/null)"
        fi
        [ -n "$st" ] || st="$(stat -f '%m' "$d" 2>/dev/null)"
        [ -n "$st" ] || st=0
        entries="$entries$st $name\n"
    done
    if [ "$total" -le "$RETENTION_KEEP" ]; then
        _log "retention: $total releases ≤ keep=$RETENTION_KEEP — nothing to evict"
        return 0
    fi
    local evict_count=$((total - RETENTION_KEEP))
    _log "retention: $total releases > keep=$RETENTION_KEEP — evicting $evict_count oldest (current=$cur previous=$prev pinned)"
    printf '%b' "$entries" | sort | head -"$evict_count" | while read -r st name; do
        [ -n "$name" ] || continue
        _log "retention: evicting $name (staged $st) — neither current ($cur) nor previous ($prev)"
        rm -rf "$rel/$name"
    done
    return 0
}

# ── Entry-side refusal checks (D-FA4.2: ENTRY only — never the recovery) ────
# promote_entry_check <target_ver> — refuses (exit 78) on: halthead (cap
# exhausted in-window), cooldown window, quarantined target, fresh in_flight.
# The AUTO-ROLLBACK path and the launcher sweep NEVER call this.
promote_entry_check() {
    local target_ver="$1" json cnt
    # cap / halt state
    cnt="$(journal_rollback_count_24h)"
    if [ "$cnt" -ge "$ROLLBACK_CAP_24H" ]; then
        _warn "HALT-FOR-HUMAN: rollback cap $ROLLBACK_CAP_24H/24h reached (count=$cnt) — promotes refused until the 24h window resets or an operator intervenes (journal halt events carry the record)"
        exit 78
    fi
    # cooldown
    if journal_cooldown_active; then
        local until
        until="$(_json_field "$(journal_read)" cooldown_until)"
        _warn "promote refused: rollback cooldown active until $until (ADR-005: 10-min anti-flapping)"
        exit 78
    fi
    # quarantined target
    if journal_is_quarantined "$target_ver"; then
        _warn "promote refused: version '$target_ver' is QUARANTINED (prior gate failure) — quarantine is cleared only by re-staging the version"
        exit 78
    fi
    return 0
}

# adopt_stale_txn — preflight handling of an unresolved in_flight. MIRRORS
# the launcher sweep decision table (launcher.sh _journal_sweep — D-FA4.3)
# so a promote never tramples an unresolved transaction and never strands
# the env on an orphaned flip:
#   unparseable started_at → FAIL CLOSED (refuse; never adopt a txn we
#       cannot age — the sweep does the same);
#   kind=restart → NEVER adopted (D-FA4.3: the daemon boot sweep owns
#       restart txns; the launcher sweep skips them too) — refuse, leave
#       untouched;
#   fresh (age ≤ SWEEP_STALE_S) → leave alone + refuse (pipeline-busy).
#       DEAD OWNER MAKES NO DIFFERENCE: the sweep leaves any fresh txn
#       alone regardless of owner liveness (the 600s gate is the primary
#       race guard, R1.3) — adoption does too;
#   stale + flipped:true → the SAME recovery the sweep would perform:
#       manifest gate on previous FIRST (null / QUARANTINED (M4) / release
#       dir missing / not rollback_safe → halt event, NO repoint, txn LEFT
#       IN PLACE, exit 78), then repoint current→previous (atomic_flip —
#       mv -h, the same rename(2) semantics the sweep uses), quarantine
#       the failed target, count the rollback + arm cooldown (ADR-024),
#       history event 'sweep_rollback' (P2.3's ledger consumes event
#       names), clear txn. W3: every recovery state write (quarantine /
#       set_current / close_txn) is CHECKED — any failure leaves the txn
#       OPEN (the sweep retries this recovery idempotently), WARNs loudly,
#       and refuses the promote (exit 78). The counter write stays
#       warn-only (over-count is the safe drift direction);
#   stale + flipped:false → clear (history event 'sweep'). Close failure
#       → txn LEFT OPEN + loud warn + exit 78 (W3 — same contract).
# Runs UNDER the caller's lock (promote acquires before calling). After a
# sweep-rollback adoption the enclosing promote continues into its ENTRY
# checks, which then apply the freshly armed cooldown/cap — PER DESIGN
# (D-FA4.2/ADR-024: the NEXT entry is refused inside the 10-min window;
# rollback.sh manual recovery never refuses on cooldown/cap).
adopt_stale_txn() {
    local json inf kind target started flipped owner epoch age
    json="$(journal_read)" || return 0
    inf="$(_json_sub "$json" in_flight)"
    [ -z "$inf" ] && return 0
    case "$inf" in *"kind"*) ;; *) return 0 ;; esac
    kind="$(_json_field "$inf" kind)"
    target="$(_json_field "$inf" target)"
    started="$(_json_field "$inf" started_at)"
    flipped="$(_json_field "$inf" flipped)"
    owner="$(_json_field "$inf" owner_pid)"
    # D-FA4.3 / R-SR13: restart-kind pending-ops are NEVER adopted (the
    # launcher sweep skips them too) — restarts are self-completing and the
    # daemon boot sweep owns them (P2.2). Leave untouched; pipeline-busy.
    if [ "$kind" = "restart" ]; then
        _warn "promote refused: in_flight kind=restart (target=${target:-?}) — D-FA4.3: restart txns are never adopted/swept (daemon boot sweep owns them); leaving untouched, pipeline-busy"
        exit 78
    fi
    # unparseable started_at → fail closed: the sweep leaves such a txn
    # untouched (launcher.sh:570-573); adoption must not fire on a txn it
    # cannot prove stale.
    if ! epoch="$(_iso_to_epoch "$started")"; then
        _warn "promote refused: in_flight $kind txn (target=$target) has unparseable started_at ('$started') — leaving untouched, pipeline-busy (the sweep fails closed on it too)"
        exit 78
    fi
    age=$(( $(_now_epoch) - epoch ))
    # fresh txn → leave alone + refuse, REGARDLESS of owner liveness (the
    # sweep's freshness gate ignores liveness; a fresh txn with a dead
    # owner is resolved by the sweep once it ages, or by the owner).
    if [ "$age" -le "$SWEEP_STALE_S" ]; then
        _warn "promote refused: in_flight $kind txn (target=$target, pid=${owner:-?}, age ${age}s ≤ ${SWEEP_STALE_S}s) is FRESH — left alone (owner may be alive; the sweep ages it) — pipeline-busy"
        exit 78
    fi
    if [ "$flipped" = "true" ]; then
        # ── stale flipped → adopt via the sweep-rollback recovery ──────────
        # Manifest gate on previous FIRST (halt-for-human, NO repoint, txn
        # left in place — mirrors the sweep's no-previous / missing-dir /
        # rollback_safe halts; D-FA4.5 schema-drift guard, enforced by
        # every rollback path in this pipeline INCLUDING the launcher
        # sweep).
        local prev prev_safe newcnt
        prev="$(_json_field "$json" previous)"
        case "$prev" in ''|null)
            journal_history_append halt "adopt: stale flipped $kind txn (target=$target) but journal has no previous release — halt-for-human, NO repoint, txn left in place"
            _warn "HALT-FOR-HUMAN: stale flipped txn (target=$target) with NO previous release — cannot adopt-rollback; txn left for the sweep/human"
            exit 78
            ;;
        esac
        # M4: a QUARANTINED previous is a known-bad release (it failed a
        # gate before) — never adopt-rollback onto it. Same halt shape as
        # promote's auto-rollback gate and the launcher sweep's: halt event,
        # NO repoint, txn left in place. Guards against a stranded/hand-
        # edited journal (promote's bookkeeping no longer strands
        # previous==quarantined, but drift must fail closed at consumption
        # too). Gate order matches promote 8b and the sweep: null →
        # QUARANTINED → missing dir → rollback_safe.
        if journal_is_quarantined "$prev"; then
            journal_history_append halt "adopt: previous release $prev is QUARANTINED (known-bad) — halt-for-human, NO repoint, txn left in place (M4)"
            _warn "HALT-FOR-HUMAN: previous release $prev is QUARANTINED — refusing to adopt-rollback onto a known-bad release; txn left for the sweep/human (M4)"
            exit 78
        fi
        if [ ! -d "$INSTALL_DIR/releases/$prev" ]; then
            journal_history_append halt "adopt: previous release $prev missing (evicted/manually deleted?) — halt-for-human, NO repoint, txn left in place"
            _warn "HALT-FOR-HUMAN: previous release $prev is MISSING — cannot adopt-rollback; txn left for the sweep/human"
            exit 78
        fi
        prev_safe="$(manifest_field "$prev" rollback_safe 2>/dev/null)"
        if [ "$prev_safe" != "true" ]; then
            journal_history_append halt "adopt: previous release $prev has rollback_safe=${prev_safe:-missing} (D-FA4.5 schema-drift guard) — halt-for-human, NO repoint, txn left in place"
            _warn "HALT-FOR-HUMAN: previous release $prev is NOT rollback_safe (${prev_safe:-missing}) — refusing to adopt-rollback into schema drift; txn left in place"
            exit 78
        fi
        # Repoint FIRST — the same flip-first ordering as the sweep: if we
        # die mid-sequence the next launcher start re-runs the sweep on
        # this same stale txn; every journal step below is idempotent
        # except the counter increment (over-counts only — conservative,
        # anti-flapping direction). Without the repoint the promote would
        # exit stranded: journal.current ≠ symlink target, env left on the
        # ungated orphaned release, cleared txn blocking future sweeps.
        _warn "adopting STALE flipped txn (age ${age}s, target=$target): sweep-rollback recovery — repoint current -> $prev, quarantine $target, count + cooldown (ADR-024)"
        if ! atomic_flip "$prev"; then
            _warn "adopt: repoint current -> $prev FAILED — txn left in place (the sweep retries at the next launcher start); promote refusing"
            exit 78
        fi
        # W3 (M5 completed): every recovery STATE write below is checked —
        # a failed write must leave the txn OPEN (the sweep retries this
        # recovery idempotently at the next launcher start) and refuse the
        # promote (exit 78) with a loud warn. NEVER close over a failed
        # state write: a set_current failure followed by a successful close
        # is permanent silent divergence, and a quarantine failure silently
        # leaves the gate-failed version promotable. The counter write
        # stays warn-only by design (9be59635 — over-count is the only
        # safe drift direction); history appends are advisory.
        journal_quarantine "$target" || {
            _warn "adopt: quarantine write FAILED for $target after the repoint — txn LEFT OPEN (the sweep retries this recovery idempotently at the next launcher start); promote refusing (W3)"
            exit 78
        }
        journal_set_current "$prev" || {
            _warn "adopt: journal_set_current $prev FAILED after the repoint — txn LEFT OPEN, never closed over a failed state write (the sweep retries idempotently); promote refusing (W3)"
            exit 78
        }
        newcnt="$(journal_count_rollback 1)" || newcnt=""
        if [ -z "$newcnt" ]; then
            # M5: the recovery's repoint/quarantine already landed — do not
            # undo them — but the counter/cooldown write FAILED: the
            # anti-flapping count is lost until the journal is repaired.
            # Loud, not silent (the sweep-rollback over-count direction is
            # the only safe drift here).
            _warn "adopt: rollback counter/cooldown write FAILED after the repoint — journal diverged; anti-flapping count may be lost (repair the journal)"
        fi
        journal_history_append sweep_rollback "adopt: orphaned flipped $kind txn (target=$target, owner pid ${owner:-?}, age ${age}s) rolled back to $prev at promote preflight; counted as auto-rollback (ADR-024)"
        journal_close_txn || {
            _warn "adopt: txn close FAILED after the sweep-rollback recovery — txn LEFT OPEN for the sweep to retry idempotently at the next launcher start; promote refusing (W3)"
            exit 78
        }
        if [ -n "$newcnt" ] && [ "$newcnt" -ge "$ROLLBACK_CAP_24H" ]; then
            journal_history_append halt "adopt sweep-rollback reached cap $ROLLBACK_CAP_24H/24h (count=$newcnt) — promotes refused until the window resets or an operator intervenes"
            _warn "HALT-FOR-HUMAN: cap reached while adopting stale txn — this promote is refused; next entry attempts will refuse too"
            exit 78
        fi
    else
        _warn "adopting STALE pre-flip txn (age ${age}s, target=$target): clearing (never flipped)"
        journal_history_append sweep "orphaned pre-flip $kind txn target=$target cleared at promote preflight"
        # W3: same checked-write contract — a failed close leaves the txn
        # OPEN for the sweep to retry; never report the clear as done.
        journal_close_txn || {
            _warn "adopt: txn close FAILED while clearing the stale pre-flip txn — txn LEFT OPEN (the sweep retries idempotently); promote refusing (W3)"
            exit 78
        }
    fi
    return 0
}
