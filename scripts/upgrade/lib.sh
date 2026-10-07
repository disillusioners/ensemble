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
# Size rationale: ~3140 lines (crossed the >3000 tier during the
# supervision-detection + 3-layer adoption-protocol growth) is still the
# single shared substrate for the five entry scripts (stage/promote/
# rollback/restart/status) plus the SOURCED consumers stop-ensemble.sh /
# adopt-unit.sh; splitting would duplicate cross-cutting contracts
# (journal layout, lock semantics, ENV allowlist, BSD/GNU dispatch). The
# file is the contract inventory. The recent growth is the supervision-
# detection section (classify / outcome map / dualfight / unit hand-back)
# — one twin lives here, the python twin in daemon/tools/upgrade_journal.py.
# BACKLOG (fix-back cycle 2 — candidates ONLY, no split executed; any
# split must keep every consumer's sourcing line byte-identical and re-run
# the full journal/lock/supervision suites):
#   (a) supervision_* family (~:1879-2900: hand-back, restart_via_launcher,
#       owned_pids, classify, outcome map, dualfight) → supervision_lib.sh;
#   (b) pipeline_settled + adoption_marker_* (~:1212-1527, Layer i/ii) →
#       settle_lib.sh;
#   (c) journal_* family (~:481-1014, atomic state.json IO) → journal_lib.sh.
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
# LIVEZ_BUDGET_S = 180s (v0.16.9, was 60s): attempt-2 halt showed the
# post-flip boot alone eats ~51s for the journal sweep on the first live
# restart; a 60s verify budget is too tight and halts the promote on
# otherwise-healthy boot-and-livez delays. 180s gives ~3x the base 60s
# headroom for cold-boot + verify span. Worst-case cost = extra wait
# before halt on a genuinely-dead boot (still halts); too-short budget
# = false halt on a healthy boot (the worse outcome — every verify-
# budget halt is a LIVE promote halt).
LIVEZ_BUDGET_S="${LIVEZ_BUDGET_S:-180}"
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

# HEARTBEAT_STALE_S mirrors LOCK_STALE_S; the two are refreshed
# together at every lock_heartbeat call site but kept as separate
# constants so the lock vs journal paths age independently.
HEARTBEAT_STALE_S=300

# Retention (ADR-004): keep 3 releases; previous pinned.
RETENTION_KEEP=3

_log()  { printf '%s[%s]: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "$*"; }
_logv() { printf '%s\n' "$*"; }                       # verbatim (JSON etc.)
_warn() { printf '%s[%s]: WARN: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "$*" >&2; }

# _now_epoch — seconds since epoch (BSD-safe).
_now_epoch() { date +%s; }

# _now_iso — UTC ISO-8601 timestamp (journal fields are ISO).
_now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# _log_ts — UTC ISO-8601 timestamp prefix for log lines (comp6, r-f82e).
# BSD/GNU-portable: ``date -u +format`` is IDENTICAL on both — the
# format-string operand uses the SAME syntax in BSD and GNU date, and
# no -j/-f/-d/-j flags appear here. Verified on macOS date(1) and
# GNU coreutils 9.x. Used as the timestamp source for upgrade.log
# lines that previously had no anchor (the incident file
# `/home/nea/ensemble-prod/data/upgrade.log` had 19 lines, none
# timestamped; post-mortems had no time correlation).
_log_ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }

# _log_tsl <file> <message...> — append a TIMESTAMPED line to the
# upgrade.log (or any log file). Test-exercised only — NOT yet wired
# into promote/restart/rollback/stage. Sites that DO need a
# timestamped log line can opt in by calling
# ``_log_tsl "$UPGRADE_LOG" "<message>"`` in place of the
# ``printf … >> "$UPGRADE_LOG"`` pattern that produced unanchored
# output. (See the comp6 paragraph below for the minimal-disruption
# adoption plan.)
#
# BYTE-IDENTICAL FALLBACK: if _log_ts is unavailable (no date binary,
# or PATH stripped in a sandbox), the timestamp degrades to "-" rather
# than aborting the write. The log line still goes through; a
# reviewer who sees "-" can correlate with the journal's ISO
# started_at timestamps (always present, comp4-portable).
_log_tsl() {
    local file="$1"; shift
    local ts
    ts="$(_log_ts 2>/dev/null)" || ts="-"
    printf '%s %s\n' "$ts" "$*" >> "$file" 2>/dev/null || return 1
}

# comp6 (r-f82e): the existing _log helper writes to stderr (WARN to
# stdout — see _warn above) and is fine for the operator's console.
# It is NOT a timestamped upgrade.log writer; the upgrade.log
# history that the forensic report needed was the executor's own
# stdout/stderr captured by spawn_executor (data/upgrade.log, append).
# Promotion/restart/rollback scripts append their phase markers to
# that same file via bare echo/printf redirects — those writes are
# the ones this helper replaces.
#
# The MINIMAL-DISRUPTION pattern: add the timestamp wrapper, keep all
# existing printf/echo writes (they still produce lines; only the
# unanchored lines change). Sites that opt into the timestamped
# wrapper call ``_log_tsl "$UPGRADE_LOG" "<message>"`` instead of
# ``printf … >> "$UPGRADE_LOG"``. Sites that don't change → lines
# stay unanchored (preserved BYTE-IDENTICALLY, by design — the
# incident's 19 unanchored lines had THIS shape; only new lines get
# anchored).
# Variable export so per-script helpers know where to append:
# UPGRADE_LOG="$INSTALL_DIR/data/upgrade.log" (set by caller; falls
# back to data/upgrade.log in $INSTALL_DIR if unset).

# _iso_to_epoch <iso-ts> — parse journal ISO timestamps; 0 on garbage
# (callers treat 0 as "unknown age" and fail CLOSED on the decision that
# matters: a sweep never fires on an unparseable fresh-looking txn).
#
# Portability (fix/portable-iso-parse-linux, 2026-09-28, r-f82e §3.9 #1):
# the parser MUST work identically on BSD/macOS and GNU/Linux. Plain
# BSD ``date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s`` is WRONG on Linux —
# the GNU date(1) rejects ``-j`` (BSD date only), ``-f`` requires a
# DIFFERENT format ordering than BSD, and ``-u`` is silently accepted
# but does not parse ISO with the BSD operand order. Symptom: a
# ``lib.sh:86`` line unparseable on this host means the launcher's
# self-heal sweep silently FAILS CLOSED on every boot — an unfixable
# orphan txn wedge.
#   BSD/macOS:  ``date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s``
#   GNU/Linux:  ``date -d "$ts" +%s``   (GNU's -d parses ISO-8601
#                                          directly; -u is implicit for
#                                          the Z suffix and -j/-f are
#                                          not understood)
# We dispatch on ``uname -s`` rather than chaining the two forms with
# ``||``: on BSD a REAL -j failure would otherwise hit the GNU form
# and surface a wrong-platform error. Unrecognized platforms refuse
# (fail-closed; we never guess). Verified on macOS date(1) and GNU
# coreutils 9.x (Linux). MIRRORS the atomic_flip precedent
# (fix/portable-atomic-flip-linux, 47630be0).
_iso_to_epoch() {
    local ts="$1" epoch
    case "$(uname -s)" in
        Darwin|*BSD*|*bsd*)
            epoch="$(date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s 2>/dev/null)" || return 1
            ;;
        Linux|GNU*|*GNU*)
            epoch="$(date -d "$ts" +%s 2>/dev/null)" || return 1
            ;;
        *)
            echo "_iso_to_epoch: unrecognized platform '$(uname -s)' — refusing (fail-closed; BSD or Linux required)" >&2
            return 1
            ;;
    esac
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
# ALL control chars < 0x20 plus DEL 0x7F as standard \u00XX escapes). Bash
# 3.2-safe, builtin-only (printf -v — no per-char subshell forks; reasons
# are short and stage.sh maps stay fast).
# N4 (P2.2 fix pass 2026-08-23): the old version only handled \n \t \r —
# any other raw control char passed through verbatim; the first N4 cut
# then escaped by `-lt 32` alone, but bash 3.2 printf '%d' yields SIGNED
# bytes, so chars >= 0x80 (é, curly quotes, CJK — routine in LLM-authored
# --reason) went negative, passed the guard, and rendered \uffffff… (>4
# hex digits). Both failures share the nastiest property: lenient readers
# (python/jq, this file's own extractor) ACCEPT the mangled form — the
# damage is SILENT TEXT CORRUPTION, not a parse failure, so nothing fails
# loudly. Now: 0 <= code < 0x20, plus DEL (0x7F — NIT-D, P2.3 B3.5: the
# one control char >= 0x20 the tidy ledger wanted escaped for parity with
# the < 0x20 family), escapes as \u00XX; negative (high UTF-8 byte)
# passes through raw — valid UTF-8 JSON. NUL cannot occur in bash
# strings/argv.
_json_escape() {
    local s="$1" out="" ch i code
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\t'/\\t}"
    s="${s//$'\r'/\\r}"
    for ((i = 0; i < ${#s}; i++)); do
        ch="${s:i:1}"
        printf -v code '%d' "'$ch"
        if [ "$code" -ge 0 ] && { [ "$code" -lt 32 ] || [ "$code" -eq 127 ]; }; then
            printf -v ch '\\u%04x' "$code"
        fi
        out+="$ch"
    done
    printf '%s' "$out"
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

# _json_field_quoted <json> <qkey> — like _json_field, but anchored on the
# FULL JSON key form `"key":` (with both quotes AND the colon) rather
# than the bare key substring. This defeats the textual first-occurrence
# hazard that bites _json_field when a field name appears inside a
# history detail string BEFORE the top-level field (the live journal had
# "pending_op" at position 875 inside a sweep detail string, ahead of
# the top-level `"pending_op":null` at file end — _json_field returned
# "no in_flight" and pipeline_settled refused every stop/adopt).
#
# Callers MUST pass the quoted form: _json_field_quoted "$json" '"pending_op":'.
# Pure bash, no jq dependency (matches the existing _json_field/_json_sub
# portability invariant).
_json_field_quoted() {
    local json="$1" qkey="$2" rest
    rest="${json#*"$qkey"}"
    [ "$rest" = "$json" ] && return 1
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
#                           [live-lane promote only, MINOR-4b, additive:]
#                           "f2_verified_closed": true,
#                           "f2_verified_at": "<iso>",
#                           "f2_verified_note": "<opt operator note>",
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
# flipped:false,owner_pid,last_heartbeat}. Refuses (returns 1) if a txn
# is already open. ADDITIVE field: last_heartbeat is the epoch the txn
# was opened — readers that don't know about it (legacy launchers,
# Python twin pre-comp3) ignore the extra key (json.loads last-wins on
# duplicates; lib.sh's textual splice at the LAST occurrence picks up
# the embedded `last_heartbeat` naturally because it appears ONCE per
# txn object). Refreshed by lock_heartbeat (see below) so a live owner
# keeps its heartbeat < HEARTBEAT_STALE_S; a dead owner's heartbeat
# ages to stale, and the new owner-liveness fast path in adopt_stale_txn
# + _journal_sweep can reclaim/observe it before the 600s age boundary.
journal_open_txn() {
    local kind="$1" target="$2" json existing
    json="$(journal_read)" || return 1
    existing="$(_json_sub "$json" "in_flight")"
    if [ -n "$existing" ] && [ "$existing" != "null" ]; then
        _warn "journal_open_txn: an in_flight txn already exists — refusing (pipeline-busy)"
        return 1
    fi
    journal_update "in_flight" \
        "{\"kind\":\"$kind\",\"target\":\"$target\",\"started_at\":\"$(_now_iso)\",\"flipped\":false,\"owner_pid\":$$,\"last_heartbeat\":$(_now_epoch)}"
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

# journal_mark_f2_verified — MINOR-4b (P2.3 review cycle 1): stamp the
# operator's --f2-verified-closed attestation INTO the open in_flight txn
# (live lane only; promote.sh calls it immediately after journal_open_txn,
# under the held lock). Adds f2_verified_closed:true + f2_verified_at
# (ISO ts) + optional f2_verified_note (from F2_VERIFIED_NOTE env, the
# operator's audit trail). ADDITIVE fields only — existing readers (the
# launcher sweep's _js_json_field, the Python twin) parse named fields
# and ignore extras, so the D4 schema stays splice-compatible.
journal_mark_f2_verified() {
    local json inf new_inf note
    json="$(journal_read)" || return 1
    inf="$(_json_sub "$json" "in_flight")"
    # M2 (tidier, P2.3 final batch): _json_sub returns ANY balanced
    # container — an array-shaped (or otherwise non-object) in_flight
    # extraction passed the old non-empty check, and the blind %\}} strip
    # + append below then wrote MALFORMED JSON into the journal (torn on
    # next read). Refuse on null/absent/empty AND any non-'{' shape; the
    # only stampable form is a real object. No journal write on refusal.
    case "$inf" in
        '{'*) ;;
        ''|null)
            _warn "journal_mark_f2_verified: no in_flight txn (in_flight null/absent) — refusing to stamp (journal untouched)"
            return 1
            ;;
        *)
            _warn "journal_mark_f2_verified: in_flight read is null/malformed (not a JSON object: '${inf:0:40}') — refusing to stamp (journal untouched)"
            return 1
            ;;
    esac
    new_inf="${inf%\}}"
    note="${F2_VERIFIED_NOTE:-}"
    if [ -n "$note" ]; then
        new_inf="${new_inf},\"f2_verified_closed\":true,\"f2_verified_at\":\"$(_now_iso)\",\"f2_verified_note\":\"$(_json_escape "$note")\"}"
    else
        new_inf="${new_inf},\"f2_verified_closed\":true,\"f2_verified_at\":\"$(_now_iso)\"}"
    fi
    journal_update "in_flight" "$new_inf"
}

# journal_close_txn — in_flight = null.
journal_close_txn() {
    journal_update "in_flight" "null"
}

# journal_heartbeat — refresh the journal's in_flight.last_heartbeat
# to NOW (epoch seconds). The lock's heartbeat file (lock_heartbeat)
# and this field are refreshed TOGETHER — the helper is invoked from
# every lock_heartbeat site (promote/restart/rollback/stage) so a live
# owner keeps BOTH fresh. Mirrors the D5 lock discipline with the
# in_flight journal field added by comp3.
#
# Best-effort: a torn/unwritable journal is WARN-only and never raises
# — lock_heartbeat returns 1 if the dir is gone, 0 otherwise; the
# journal refresh failure is logged but does not flip the return code
# (the lock heartbeat is what callers depend on for liveness; the
# journal one is the fast-path sweeper's signal).
#
# No-op when in_flight is null/absent (no txn to heartbeat — race-safe).
#
# BASH-PATTERN HAZARD (the reason this uses sed, not $var//pattern):
# bash's parameter-substitution glob ``[0-9]*`` is GREEDY — it matches
# a digit then ANY CHARACTERS until the regex engine stops, including
# the closing ``}`` of the in_flight object. A pure-digit pattern
# (e.g. ``[0-9]``) would replace one char at a time and corrupt the
# field's trailing bytes. The portable fix is a sed anchored to the
# field name (``"last_heartbeat":[0-9]+`` with no trailing wildcard)
# so the substitution's match-length is bounded to the digits
# themselves — see test_promote_cgroup_survivorship.sh 3b for the
# regression pin that caught this.
journal_heartbeat() {
    local json
    json="$(journal_read 2>/dev/null)" || return 0
    local inf
    inf="$(_json_sub "$json" "in_flight")"
    [ -n "$inf" ] || return 0
    case "$inf" in ''|null) return 0 ;; esac
    local owner
    owner="$(_json_field "$inf" "owner_pid" 2>/dev/null)" || owner=""
    # Ownership guard: refresh the heartbeat ONLY if WE are the owner
    # recorded in the txn (mirrors lock_heartbeat's owner guard — a
    # non-owner must never refresh a foreign owner's heartbeat).
    if [ -n "$owner" ] && [ "$owner" != "$$" ]; then
        return 0
    fi
    # Replace the last_heartbeat field; preserves every other field.
    # The sed pattern is anchored to "last_heartbeat":<digits> with no
    # trailing wildcard — bash parameter-substitution's glob would
    # otherwise eat the closing `}` of the in_flight object (a real
    # bug, not theoretical — see the BASH-PATTERN HAZARD comment above
    # and the test pin in tests/test_promote_cgroup_survivorship.sh).
    local new_inf hb_now
    hb_now="$(_now_epoch)"
    # heartbeat values are epoch-second integers; [0-9]+ assumes no
    # sign/decimal/fractional — true for _now_epoch output.
    new_inf="$(printf '%s' "$inf" \
            | sed -E "s/(\"last_heartbeat\":)[0-9]+/\\1$hb_now/")"
    [ "$new_inf" != "$inf" ] || return 0
    journal_update "in_flight" "$new_inf"
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

# ── Heartbeat liveness fast-path (comp3; r-20260928-005506-f82e) ─────────────
#
# WHY TWO GATES NOW EXIST (deliberate-design note, R1.3 + comp3):
#
#   Primary  — SWEEP_STALE_S=600 (age). A txn's AGE alone decides
#              adoption / clearing. The 600s window is the primary race
#              guard against a live owner whose heartbeat file has
#              aged: a LIVE owner in the middle of a long op (the
#              stop-script span clamps to 600s > LOCK_STALE_S) writes
#              to the lock file but NOT to the journal's heartbeat
#              field. Pre-comp3 this was the SOLE gate. Preserved
#              BYTE-IDENTICALLY.
#
#   Added    — HEARTBEAT_STALE_S=300 AND owner dead (liveness). A
#              txn's journal heartbeat AND the owner pid BOTH signal
#              death → reclaim NOW, before the 600s age boundary.
#              Closes the r-f82e gap: the executor was killed ~2s in
#              (no heartbeat ever written) but the reaper was also
#              dead and the journal was orphaned for the full 600s
#              before the launcher's next boot sweep could reclaim it.
#
#   Both are checked by the consumers below. The age gate is the
#   FIRST check (preserves ordering — a fresh txn stays fresh
#   regardless of liveness); the liveness gate is the SECOND check
#   (a stale-and-dead txn reclaims earlier than the age gate alone
#   would allow).
#
# _txn_heartbeat_stale <inf> <now_epoch> — print 1 if the in_flight
# txn's journal heartbeat is stale (>HEARTBEAT_STALE_S old), 0 if
# fresh. Missing/garbage heartbeat → stale (1) — a fresh txn is
# always heartbeat-written by journal_open_txn, so an absent value
# means a hand-edited or legacy journal; fail-open to stale so the
# liveness dimension owns the decision.
_txn_heartbeat_stale() {
    local inf="$1" now="$2" hb age
    hb="$(_json_field "$inf" "last_heartbeat" 2>/dev/null)" || hb=""
    if ! printf '%s' "$hb" | grep -Eq '^[0-9]+$'; then
        printf '1'; return 0
    fi
    age=$(( now - hb ))
    if [ "$age" -gt "$HEARTBEAT_STALE_S" ]; then
        printf '1'
    else
        printf '0'
    fi
}

# _txn_owner_dead <inf> — print 1 if the in_flight txn's owner_pid
# is missing/garbage OR kill -0 says it's dead. EPERM = ALIVE
# (foreign-user owner; matches _pid_alive above — breaking a live
# foreign owner's txn would trample its work).
_txn_owner_dead() {
    local inf="$1" owner
    owner="$(_json_field "$inf" "owner_pid" 2>/dev/null)" || owner=""
    if [ -z "$owner" ] || ! printf '%s' "$owner" | grep -Eq '^[0-9]+$'; then
        printf '1'; return 0
    fi
    if _pid_alive "$owner"; then
        printf '0'
    else
        printf '1'
    fi
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

# _pid_alive <pid> — kill -0 liveness with EPERM=ALIVE semantics
# (kill -0 EPERM, P2.1 ledger → closed P2.3 B3.5): exit 0 → alive;
# ENOENT/ESRCH → dead; EPERM → ALIVE — the process exists but belongs to
# another user (permission denied ≠ dead; breaking a live foreign owner's
# lock would trample its txn). Matches the Python journal twin
# upgrade_journal._pid_alive (PermissionError → True). Stderr-text match
# under LC_ALL=C (NIT-5 — locale-stable: a localized shell must not flip
# the branch): bash builtin kill and /bin/kill both render "Operation not
# permitted".
_pid_alive() {
    local err
    err="$(LC_ALL=C kill -0 "$1" 2>&1)" && return 0
    case "$err" in
        *"not permitted"*) return 0 ;;
        *) return 1 ;;
    esac
}

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
        # crash progress). kill -0 via _pid_alive (POSIX) is the liveness
        # test — EPERM counts as ALIVE (foreign-user owner; see _pid_alive
        # above); residual pid-reuse wedge (crashed owner, pid reused by a
        # live process) degrades to pipeline-busy — safer than trampling a
        # live owner.
        hb="$(cat "$lock/heartbeat" 2>/dev/null)"
        owner_pid="$(cat "$lock/owner" 2>/dev/null)"
        run_id="$(cat "$lock/run_id" 2>/dev/null)"
        # comp3 NOTE (r-f82e): the journal-level liveness fast path lives
        # in the journal sweeps (adopt_stale_txn above, _journal_sweep in
        # launcher.sh), not here. The lock's own heartbeat + owner gate
        # below is the contract — the lock and journal heartbeats are
        # refreshed together at lock_heartbeat sites, so they age in
        # lockstep; a separate journal-OWNER gate here would duplicate
        # the same liveness check (and a stale-but-live lock vs dead-but-
        # fresh journal is the kind of paired-mismatch the r-f82e audit
        # specifically warned against). R-SR13 mirrors the lock/journal
        # protocol across both files; the journal-side fast path
        # reclaims txns, not locks.
        if printf '%s' "$hb" | grep -Eq '^[0-9]+$'; then
            age=$(( $(_now_epoch) - hb ))
            if [ "$age" -gt "$LOCK_STALE_S" ]; then
                local owner_live=0
                if [ -n "$owner_pid" ] && printf '%s' "$owner_pid" | grep -Eq '^[0-9]+$' \
                   && _pid_alive "$owner_pid"; then
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
        # (_pid_alive: EPERM = alive under another user — never break)
        if [ -n "$owner_pid" ] && printf '%s' "$owner_pid" | grep -Eq '^[0-9]+$' \
           && ! _pid_alive "$owner_pid"; then
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
#
# comp3 (r-20260928-005506-f82e): also refreshes the journal's
# in_flight.last_heartbeat (journal_heartbeat). The two writes go
# together so a live owner keeps BOTH fresh; a dead owner ages both.
# journal_heartbeat is best-effort and ownership-guarded itself —
# never raises.
lock_heartbeat() {
    local lock owner
    lock="$(lock_dir_path)"
    [ -d "$lock" ] || return 1
    owner="$(cat "$lock/owner" 2>/dev/null)"
    [ "$owner" = "$$" ] || return 1
    printf '%s\n' "$(_now_epoch)" > "$lock/heartbeat" 2>/dev/null
    # companion journal refresh — same owner, same cadence. The
    # callers already invoke this helper in long loops (gate_soak,
    # promote phase 3-8, stage assembly, restart grace).
    journal_heartbeat >/dev/null 2>&1 || true
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

# ── pipeline_settled / adoption marker (commission v0.16.6 component 2) ─────
#
# INCIDENT (r-20260929-170301-0cb2, 2026-09-29): a systemd-adoption mission
# gated on "current == v0.16.5 + healthy" — STRUCTURALLY RACY because the
# `current` symlink flips at promote.sh:258 BEFORE gates+soak complete, so
# the check passes mid-flight and the adoption raced a live flip. ZERO
# mutual exclusion between promote and adoption: stop-ensemble.sh and
# adopt-unit.sh took NO lock and read NO pipeline state.
#
# FIX (v0.16.6 component 2 — 3-layer adoption protocol):
#   Layer (i)  pipeline_settled preflight (this section) — adoption/stop
#              may NEVER gate on "current == X + health". They MUST gate on
#              this settle check (current set, on-disk symlink matches
#              journal current, no in_flight, no pending_op, lock free).
#   Layer (ii) pipeline lock + adoption marker (this section + the calling
#              scripts) — adoption/stop acquire the rollback.lock.d the
#              same way promote/stage do; an adoption-in-progress marker
#              file refuses concurrent promote prefights symmetrically.
#   Layer (iii) runbook (docs/runbooks/systemd-adoption.md) — the operator
#              procedure; explicit "FORBIDDEN: current == X + health check
#              pattern" with the incident citation.
#
# CONTRACT: pipeline_settled prints ONE machine-readable line on stdout
# ("reason=<token>: …") on failure and returns 1; returns 0 silently on
# success. Consumers wrap the call with their own loud warn + exit 78 —
# this helper stays a PURE check, no side effects, no journal writes.
# BSD date flags BEFORE the `-f fmt value` operand; portable grep -E;
# safe-glob (no set -f in this lib). JSON parse is intentionally shallow
# (`_json_field` returns the first comma/close-brace slice — sufficient
# for top-level scalars here, never reaches into nested structures).

# _pipeline_current_target — print the journal `current` value (empty if
# absent/null/garbled). Strips a leading/trailing JSON-string quote pair.
# Anchored on the QUOTED-KEY form (`"current":`) via _json_field_quoted —
# _json_field would also work today (top-level appears first), but a
# future history detail containing the bare word "current" would silently
# shift the anchor to a substring inside a string value. The quoted form
# is robust against any future history detail content.
_pipeline_current_target() {
    local json jp cur
    jp="$(journal_path 2>/dev/null)" || return 1
    [ -f "$jp" ] || return 1
    json="$(journal_read 2>/dev/null)" || return 1
    cur="$(_json_field_quoted "$json" '"current":' 2>/dev/null)" || cur=""
    case "$cur" in
        null|"") return 1 ;;
        \"*\")    cur="${cur#\"}"; cur="${cur%\"}" ;;
    esac
    [ -n "$cur" ] || return 1
    printf '%s' "$cur"
}

# pipeline_settled — Layer (i) gate. Verifies the upgrade pipeline is in a
# quiescent state: a committed `current`, the on-disk symlink agrees with
# the journal, no in_flight txn, no pending_op, the rollback lock is free.
# Prints ONE machine-readable refusal line on stdout and returns 1 on any
# failure. Returns 0 silently on success — the caller's quiet path.
#
# The refusal line format is FIXED so downstream tooling (incident triage,
# operator scripts, future tests) can grep `^reason=<token>:` and act:
#   reason=journal-current-unset — journal has no current (fresh install
#                                  or post-rollback halt)
#   reason=current-symlink-absent — INSTALL_DIR/current symlink missing
#                                  while journal claims one (layout drift)
#   reason=current-symlink-mismatch — symlink points at a DIFFERENT release
#                                  than the journal (an uncommitted flip;
#                                  the symlink MUST be trusted over the
#                                  journal only when both agree)
#   reason=current-symlink-garbled — symlink target not shaped like
#                                  releases/<ver> (operator-tampered?)
#   reason=in-flight-txn        — journal has an open txn (kind/target/
#                                  started_at/flipped printed in detail)
#   reason=pending-op           — journal has a non-null pending_op
#                                  (in-daemon tool-armed promote record)
#   reason=lock-held            — rollback.lock.d present, owned by pid/
#                                  run_id (promote/stage/rollback in flight)
#
# SPECIAL CASE — no-journal: NOT a refusal. No journal = no pipeline state
# to be unsettled (fresh install, never promoted, post-init). Refusing
# stop here would gate every `Makefile stop`, every fresh-install
# lifecycle, and every operator emergency on a category error. We log an
# INFO line (NOT a `reason=<token>:` failure line — the failure-vocabulary
# contract stays intact for the 7 refusal tokens above) and return 0.
# Fix-back commission v0.16.6 c2.
pipeline_settled() {
    local jp cur target reason=""
    jp="$(journal_path 2>/dev/null)" || true
    if [ -z "$jp" ] || [ ! -f "$jp" ]; then
        # TRIVIALLY SETTLED — no journal = no baseline to protect.
        # INFO line, NOT a refusal (no `reason=<token>:` prefix; the
        # failure-vocabulary contract holds for the 7 refusal tokens).
        printf 'INFO: pipeline_settled — no journal at %s (fresh install / never promoted); trivially settled, no baseline to protect\n' "${jp:-<unresolved>}"
        return 0
    fi
    if ! cur="$(_pipeline_current_target)"; then
        printf 'reason=journal-current-unset: journal at %s has no `current` — fresh install or post-rollback halt; refuse stop/adopt that needs a committed baseline\n' "$jp"
        return 1
    fi
    if [ ! -L "$INSTALL_DIR/current" ]; then
        printf 'reason=current-symlink-absent: $INSTALL_DIR/current symlink missing while journal claims %s — layout divergence (D-FA5.3 freezes mutations)\n' "$cur"
        return 1
    fi
    target="$(readlink "$INSTALL_DIR/current" 2>/dev/null)" || target=""
    case "$target" in
        "releases/$cur") ;;   # agreement — OK
        "releases/"*)
            printf 'reason=current-symlink-mismatch: on-disk current→%s but journal says current=%s (symlink was flipped without committing the journal — never trust either side; halt-for-human)\n' "$target" "$cur"
            return 1
            ;;
        *)
            printf 'reason=current-symlink-garbled: on-disk current→%s (not shaped like releases/<ver>); refuse (operator-tampered?)\n' "${target:-<empty>}"
            return 1
            ;;
    esac
    local json inf pop kind targ started flipped
    json="$(journal_read 2>/dev/null)" || json=""
    if [ -n "$json" ]; then
        # in_flight — null/empty/missing is OK; anything else is a settle
        # failure with the txn's identifying fields named.
        # NOTE: shallow top-level extraction (relies on journal schema
        # discipline — no nested in_flight-named field exists today; any
        # future nested field with the same name will be missed here).
        inf="$(_json_sub "$json" in_flight 2>/dev/null)" || inf=""
        case "$inf" in
            ""|null) ;;
            *)
                kind="$(_json_field "$inf" kind 2>/dev/null)"
                targ="$(_json_field "$inf" target 2>/dev/null)"
                started="$(_json_field "$inf" started_at 2>/dev/null)"
                flipped="$(_json_field "$inf" flipped 2>/dev/null)"
                printf 'reason=in-flight-txn: journal has an open txn (kind=%s target=%s started_at=%s flipped=%s) — refuse any stop/adopt that would race the in-flight mutation\n' "${kind:-?}" "${targ:-?}" "${started:-?}" "${flipped:-?}"
                return 1
                ;;
        esac
        # pending_op — ADDITIVE field owned by the Python journal twin
        # (D-FA1.1). The shell pipeline NEVER writes pending_op (it's the
        # in-daemon tool-armed promote record); its presence means a tool
        # arm is in flight, which must not race a stop/adopt.
        # NOTE: anchored on the QUOTED-KEY form `"pending_op":` via
        # _json_field_quoted — _json_field's bare-substring anchor returns
        # a false-positive when the literal "pending_op" appears inside a
        # history detail string BEFORE the top-level field (the live
        # journal's 2026-09-26T10:02:10Z sweep detail at position 875
        # contains `pending_op run_id=... cleared by reconcile: no in_flight,
        # no terminal event, past expires_at ...` — _json_field extracted
        # "no in_flight" and pipeline_settled refused every stop/adopt,
        # blocking the in-progress systemd adoption). The quoted anchor
        # (`"pending_op":`) only matches the real top-level field.
        pop="$(_json_field_quoted "$json" '"pending_op":' 2>/dev/null)" || pop=""
        case "$pop" in
            ""|null) ;;
            *)
                printf 'reason=pending-op: journal has a non-null pending_op (in-daemon tool-armed promote record) — refuse stop/adopt that would race the tool arm\n'
                return 1
                ;;
        esac
    fi
    # lock free? — the lock_dir_path helper takes INSTALL_DIR; the dir's
    # presence is itself the acquire, regardless of owner contents.
    if [ -d "$(lock_dir_path)" ]; then
        local owner run_id
        owner="$(cat "$(lock_dir_path)/owner" 2>/dev/null)" || owner="?"
        run_id="$(cat "$(lock_dir_path)/run_id" 2>/dev/null)" || run_id="?"
        printf 'reason=lock-held: rollback.lock.d present (owner=%s run_id=%s) — promote/stage/rollback in flight; refuse concurrent stop/adopt\n' "${owner:-?}" "${run_id:-?}"
        return 1
    fi
    return 0
}

# ── Adoption-in-progress marker (Layer ii, promote-side symmetric refusal) ──
#
# RATIONALE: adopt-unit.sh mutates the host in two distinct phases — install
# the unit file (.env NOT staged) → daemon-reload → .env staged BUT unit
# NOT enabled. A concurrent promote whose restart/hand-back keys off the
# staged ENSEMBLE_RESTART_UNIT (lib.sh:2029-2031 hb_unit resolution — env
# override then INSTALL_DIR/.env read — and lib.sh:2082 launcher unit-path
# gate, both inside restart_via_launcher) hits the half-staged state and
# silently misroutes to a systemctl hand-back for a unit that doesn't
# exist yet — promoting onto nothing. The marker tells the promote preflight: "an adoption is mid-
# sequence; refuse (78) until the marker is cleared". The marker lives as
# a file (NOT a journal field — schema discipline: additive journal fields
# require migrations and the Python twin must learn them; a marker file is
# schema-gen-safe by construction and is owned by exactly one tool).
#
# FORMAT: plain text, three lines:
#   pid=<pid>
#   run_id=<run-id>
#   started_at=<iso>
# (no JSON, no shell eval — consumed by read-only inspection; the file's
# mere existence is the gate signal.)
#
# LIFECYCLE: adopt-unit.sh writes it BEFORE install and clears it AFTER
# verify success (or after a step-d failure, with a loud warn — the
# recover path is the documented manual procedure). Promote preflight
# refuses on presence; no auto-expiry (the operator must remove a stale
# marker — see the runbook).

adoption_marker_path() { printf '%s/releases/.adoption_in_progress' "$INSTALL_DIR"; }

# adoption_marker_present — 0 if marker present (refuse), 1 if absent.
adoption_marker_present() {
    [ -e "$(adoption_marker_path)" ] && return 0
    return 1
}

# adoption_marker_write — best-effort stamp; named fields, plain text.
adoption_marker_write() {
    local mp pid run_id started
    mp="$(adoption_marker_path)"
    mkdir -p "$(dirname "$mp")" 2>/dev/null || return 1
    pid="$$"
    run_id="run-$(date +%Y%m%d-%H%M%S)-$$"
    started="$(_now_iso)"
    {
        printf 'pid=%s\n' "$pid"
        printf 'run_id=%s\n' "$run_id"
        printf 'started_at=%s\n' "$started"
    } > "$mp.tmp.$$" 2>/dev/null || { rm -f "$mp.tmp.$$" 2>/dev/null; return 1; }
    mv -f "$mp.tmp.$$" "$mp" 2>/dev/null || { rm -f "$mp.tmp.$$" 2>/dev/null; return 1; }
    return 0
}

# adoption_marker_clear — best-effort + idempotent. NOT owner-guarded by
# design: the operator stale-recovery lane is `rm -f` against the marker
# path (runbook §3c), which is intentionally a non-pid-checked clear so
# stale pids / orphaned runs can be cleaned by hand. Idempotent: absent
# marker → 0, never an error. Used by the verify-success closure path
# (where pid-match is implicit — the writer IS the caller) and as the
# helper behind operator `rm -f` documentation.
adoption_marker_clear() {
    local mp
    mp="$(adoption_marker_path)"
    [ -e "$mp" ] || return 0
    rm -f "$mp" 2>/dev/null || {
        _warn "adoption_marker_clear: cannot remove $mp (manual: rm -f $mp) — promote preflight will keep refusing until the file is gone"
        return 1
    }
    return 0
}

# ── Signal-trap discipline (component 2; r-20260928-005506-f82e) ─────────────
#
# INCIDENT GAP (2026-09-28): the pipeline scripts registered
#   trap 'lock_release' EXIT
# as their ONLY safety net. Bash's EXIT trap does NOT fire on untrapped
# TERM/HUP/INT — so a SIGTERM from systemd cgroup teardown
# (KillMode=control-group), a Ctrl-C, or a SIGHUP from the parent shell
# killed the executor WITHOUT releasing the lock and WITHOUT leaving a
# terminal journal event. The reaper also died first (in-process), so
# no second chance observed the orphan.
#
# FIX: install explicit TERM/HUP/INT handlers that (a) journal a halt
# event (death-anchored — survives the executor exit), (b) release the
# lock idempotently, (c) exit with the signal-appropriate code.
# Race-safe with the EXIT trap: a per-script guard
# ``_LOCK_RELEASED=1`` is set BEFORE lock_release runs in the signal
# trap; the EXIT trap (now ``_trap_safe_exit``) consults the guard and
# skips the second release. Idempotent by construction — no double-fire.
#
# BYTE-IDENTICAL FALLBACK: scripts that don't call ``_trap_install_signal_handlers``
# keep their original ``trap 'lock_release' EXIT`` shape (the existing
# promote.sh / restart.sh / rollback.sh / stage.sh sites). Those sites
# are EXACTLY the four updated here — no other scripts depend on the
# EXIT-only trap.

# Trap guard (set by the signal handler BEFORE lock_release). The EXIT
# trap checks it and skips the release when set.
_LOCK_RELEASED=0

# _trap_safe_exit — EXIT trap body that respects _LOCK_RELEASED. When
# the signal handler ran first, the guard is 1 → no-op (the handler
# already released the lock). When the script exits normally (no signal),
# the guard stays 0 → the original lock_release fires.
_trap_safe_exit() {
    if [ "${_LOCK_RELEASED:-0}" = "1" ]; then
        return 0
    fi
    lock_release
}

# _signal_journal_halt <signum> — best-effort halt event for the death.
# Best-effort ONLY: an absent/torn/unwritable journal must NEVER block
# the exit (the lock release below is what matters for the next action
# to acquire; a missing halt event is no worse than today's behavior).
# Mirrors _refuse's best-effort journal pattern (ADR-034 append).
_signal_journal_halt() {
    local sig="$1" phase="${_PIPELINE_PHASE:-unknown}" detail
    detail="killed by SIG$(printf '%s' "$sig" | sed 's/^SIG//') at phase $phase (r-20260928-005506-f82e: death-anchored journaling; signal handler installed 2026-09-28)"
    journal_history_append halt "$detail" >/dev/null 2>&1 || true
}

# _signal_handler — single handler installed for TERM/HUP/INT.
# Argument is the signal NAME (TERM/HUP/INT) per bash convention
# (``trap '...' TERM`` invokes the handler with $1=TERM). We journal
# first, mark the guard, release the lock, then exit.
# NEVER raise from here — a raise inside a signal handler in bash 3.2
# is undefined behavior. Just exit with the signal-appropriate code.
_signal_handler() {
    local sig="$1"
    # 128 + signum per shell convention: TERM=15→143, HUP=1→129, INT=2→130
    case "$sig" in
        TERM|HUP|INT) ;;
        *) sig=TERM ;;  # conservative
    esac
    _signal_journal_halt "$sig"
    _LOCK_RELEASED=1
    # Direct call (NOT through EXIT trap) — the EXIT trap's
    # _trap_safe_exit sees _LOCK_RELEASED=1 and no-ops anyway.
    lock_release >/dev/null 2>&1 || true
    # Exit via plain `exit N`: the trap runs in-process, so the exit
    # code carries the signal attribution directly.
    case "$sig" in
        TERM) exit 143 ;;
        HUP)  exit 129 ;;
        INT)  exit 130 ;;
        *)    exit 143 ;;
    esac
}

# _trap_install_signal_handlers <phase> — install TERM/HUP/INT traps +
# swap the EXIT trap to the safe variant. Idempotent: a second call
# resets all three traps to the same handler. Phase is recorded on the
# halt event so post-mortems can correlate.
_trap_install_signal_handlers() {
    local phase="$1"
    _PIPELINE_PHASE="$phase"
    trap '_signal_handler TERM' TERM
    trap '_signal_handler HUP'  HUP
    trap '_signal_handler INT'  INT
    trap '_trap_safe_exit' EXIT
    # Reset guard (in case the script was sourced mid-flow).
    _LOCK_RELEASED=0
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

# ── Build provenance (cn 0472b31f trap family; 2026-10-04) ─────────────────
#
# The stage.sh staleness trap family has materialized TWICE (2026-09-02
# dist/, 2026-10-02 frontend/dist) and was caught only by luck each time.
# The structural fix: a provenance sidecar MUST be written at BUILD time and
# verified at STAGE time — blind reuse of prebuilt artifacts is rejected
# with a loud, actionable error. Provenance records:
#
#   - git_head       — the commit at build time (40-hex)
#   - git_head_short — abbreviated sha (operator-friendly log line)
#   - git_dirty      — true if the working tree had uncommitted changes
#                      when the build was produced (a dirty build is unsafe
#                      to stage: an idempotent re-stage would re-build with
#                      DIFFERENT outputs, and the staged binary diverges
#                      from any tag on the tree)
#   - build_at       — ISO-8601 Z timestamp
#   - build_tool     — free-form string identifying the build pipeline
#                      (e.g. "pyinstaller:ensemble.spec", "ng build:frontend")
#   - artifact_sha256 — sha256 of the artifact, computed at build time
#   - artifact_path  — repo-relative path (operator log line)
#
# Sidecar filename: "<artifact>.build-provenance.json" (sibling to the
# artifact). This shape avoids collision with any other manifest, keeps
# the sidecar atomic with the artifact (one write, one fsync), and lets
# `rm dist/ensemble-prod*` clean both in a single operator gesture.
#
# Refusal tokens (best-effort journaled on the install dir):
#   provenance-missing       — no .build-provenance.json sidecar
#   stale-provenance         — provenance records a different git HEAD
#   dirty-build              — provenance reports a dirty build tree
#   provenance-hash-mismatch — provenance's artifact_sha256 doesn't match
#   non-tip-tree             — staging tree is not at the integration tip
#                              (origin/latest or local latest; the
#                              v0.16.13 payload case where a tag was placed
#                              on a non-tip commit and the actual fixes
#                              landed later on the tip)
#
# Override: STAGE_FRESHNESS_OVERRIDE=1 (set by --allow-stale-stage in
# stage.sh; mirrors the --f2-verified-closed pattern). The override
# unlocks ALL refusal tokens in one go, AND journals the override event
# on the install dir so the operator's acceptance is auditable.

# _provenance_path <artifact_abs_path> — sidecar path (sibling).
_provenance_path() {
    printf '%s.build-provenance.json' "$1"
}

# _provenance_write <artifact_abs_path> <git_head> <git_dirty_bool> <tool>
# — write the provenance sidecar. Computes artifact_sha256 inline. Refuses
# on missing artifact (caller bug) or an unrecognized dirty value. The
# dirty value is NORMALIZED to canonical JSON true/false before writing
# (accepts true/True/1 and false/False/0 — legacy numeric 0/1 producers
# keep producing correct sidecars). The write is ATOMIC: tmp file in the
# same dir + mv -f (same discipline as journal_write) — a torn sidecar
# can only come from an out-of-discipline writer, and the verifier's
# provenance-malformed gate catches that class.
_provenance_write() {
    local art="$1" head="$2" dirty="$3" tool="$4"
    if [ ! -f "$art" ]; then
        _warn "_provenance_write: artifact $art missing — refusing to write a sidecar for nothing"
        return 1
    fi
    local dirty_canon
    dirty_canon="$(_provenance_dirty_canon "$dirty")" || {
        _warn "_provenance_write: git_dirty value '$dirty' is not a recognized boolean (true/True/1/false/False/0) — refusing to write an ambiguous sidecar"
        return 1
    }
    local sha short built_at rel prov tmp
    if ! sha="$(_sha256 "$art")" || [ -z "$sha" ]; then
        # M3 (review round 2): refuse LOUDLY on an empty hash. The
        # production hashing tool (shasum -a 256) is missing on this
        # host OR is failing — either way, silently writing
        # `"artifact_sha256": ""` would let stage misdirect with
        # provenance-malformed instead of naming the actual cause
        # (missing hashing tooling). Actionable: install shasum (perl
        # core on most systems) or run from a host that has it; the
        # broader sha256sum/openssl capability-detection is a follow-up
        # (out of this round's scope per the council report's G5/G6/G7
        # deferred ledger).
        _warn "_provenance_write: empty sha256 for $art — the 'shasum' tool is missing, not on PATH, or failed for this artifact. Install shasum (perl core on most systems; BSD's native 'sha256' is non-portable) or run from a host that has it, then rebuild. Refusing to write a sidecar with artifact_sha256:\"\" — stage would misdirect with provenance-malformed instead of naming the missing tooling."
        return 1
    fi
    short="${head:0:12}"
    built_at="$(_now_iso)"
    rel="${art#"$REPO_ROOT"/}"
    prov="$(_provenance_path "$art")"
    tmp="$prov.tmp.$$"
    if ! cat > "$tmp" <<EOF
{
  "git_head": "$head",
  "git_head_short": "$short",
  "git_dirty": $dirty_canon,
  "build_at": "$built_at",
  "build_tool": "$(_json_escape "$tool")",
  "artifact_sha256": "$sha",
  "artifact_path": "$(_json_escape "$rel")"
}
EOF
    then
        rm -f "$tmp" 2>/dev/null
        _warn "_provenance_write: FAILED to write sidecar tmp $tmp"
        return 1
    fi
    if ! mv -f "$tmp" "$prov"; then
        rm -f "$tmp" 2>/dev/null
        _warn "_provenance_write: FAILED to move sidecar tmp into place at $prov"
        return 1
    fi
    _log "wrote provenance $prov (head=$short dirty=$dirty_canon tool=$tool)"
}

# _provenance_dirty_canon <raw> — normalize a git_dirty flag to the
# canonical JSON boolean the writer emits and the verifier's dirty branch
# consumes. Dirty side: true/True/1. Clean side: false/False/0. ANY other
# value returns 1 — the caller (writer) refuses rather than writing an
# ambiguous sidecar, and the verifier refuses provenance-malformed on the
# same vocabulary. No value skips a check, ever (cn 0472b31f C1 contract).
_provenance_dirty_canon() {
    case "$1" in
        true|True|1)    printf 'true' ;;
        false|False|0)  printf 'false' ;;
        *) return 1 ;;
    esac
}

# _provenance_read <artifact_abs_path> — print sidecar JSON content;
# returns 1 (and sets _PROVENANCE_READ_STATUS) when the content cannot be
# trusted. The verifier consumes the status to dispatch the refusal token:
#
#   ok          — _PROVENANCE_READ_STATUS=ok; prints the JSON to stdout.
#   absent      — sidecar file does not exist; verifier → provenance-missing.
#   unreadable  — file exists but cat failed; verifier → provenance-missing.
#   malformed   — file present but content fails _provenance_sidecar_ok
#                 (unbalanced quote/brace, or a load-bearing key appears
#                 more than once — the laundering signature: a hand-
#                 crafted sidecar with an unescaped-quote decoy
#                 `"git_dirty": false` or `"git_head": "<sha>"` inside
#                 an earlier field's value, which would otherwise win the
#                 textual first-occurrence race and let a dirty build
#                 pass as clean — see cn 0472b31f fix round 2, M1).
#                 verifier → provenance-malformed.
#
# Single read path: writer and reader share the _provenance_path
# resolution, so a path-shape drift between them is structurally
# impossible.
_provenance_read() {
    local prov
    prov="$(_provenance_path "$1")"
    _PROVENANCE_READ_STATUS="absent"
    [ -f "$prov" ] || return 1
    local json
    if ! json="$(cat "$prov" 2>/dev/null)"; then
        _PROVENANCE_READ_STATUS="unreadable"; return 1
    fi
    _PROVENANCE_READ_STATUS="malformed"
    [ -n "$json" ] || { _PROVENANCE_READ_DETAIL="empty file"; return 1; }
    if ! _provenance_sidecar_ok "$json"; then
        _PROVENANCE_READ_DETAIL="quote/brace imbalance OR a load-bearing key appears more than once (laundering signature)"
        return 1
    fi
    _PROVENANCE_READ_STATUS="ok"
    _PROVENANCE_READ_DETAIL=""
    printf '%s' "$json"
}
_PROVENANCE_READ_STATUS=""
_PROVENANCE_READ_DETAIL=""

# _provenance_sidecar_ok <json> — return 0 if the sidecar content is
# well-formed enough to trust _json_field_quoted's first-occurrence
# extraction; 1 otherwise. TWO gates, both bash-only (no jq, no python
# — same discipline as journal_read lib.sh:519-566; python3 IS
# available on every host that runs the upgrade pipeline but the
# verifier stays portable per ADR-014):
#
#   Gate 1: journal_read quote/brace-balance scanner, ported verbatim
#           (catches torn writes + unclosed strings; reviewer-spec M1).
#   Gate 2: load-bearing-key uniqueness — each of `"git_head":`,
#           `"git_dirty":`, `"artifact_sha256":` must appear EXACTLY ONCE
#           in the content. The first-occurrence extractor would
#           otherwise return the DECOY (in a non-key field's value)
#           and let a dirty build pass as clean. The balance scan
#           alone is insufficient here — the reviewer's M1 spec
#           suggested it, but the laundering shapes (decoy inside a
#           `"note": "leaked "git_dirty": false more text"` value)
#           are quote-balanced yet invalid; the uniqueness check is
#           what catches the class. Absent (count 0) is tolerated by
#           THIS gate (the downstream load-bearing shape gate handles
#           the missing-field case).
#
# The validity-bounded is balanced quotes + balanced braces + unique
# load-bearing keys. Together: enough to defeat the laundering class
# without a real JSON parser.
_provenance_sidecar_ok() {
    local json="$1"
    # Gate 1: balance scan (ported from journal_read lib.sh:540-565).
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
    if [ "$ob" -ne "$cb" ] || [ "$os" -ne "$cs" ] || [ "$in_str" -ne 0 ]; then
        return 1
    fi
    # Gate 2: load-bearing-key uniqueness (laundering signature).
    # The writer's output contains each of these exactly once; a hand-
    # crafted sidecar with a decoy `"git_dirty":` or `"git_head":` in
    # a non-key field's leaked value contains it twice. bash 3.2-safe
    # pattern-stripping count (no grep dependency).
    local key count tmp
    for key in '"git_head":' '"git_dirty":' '"artifact_sha256":'; do
        tmp="$json"; count=0
        while [ "${tmp#*"$key"}" != "$tmp" ]; do
            tmp="${tmp#*"$key"}"
            count=$((count + 1))
        done
        if [ "$count" -gt 1 ]; then
            return 1
        fi
    done
    return 0
}

# _git_dirty_porcelain — print the canonical JSON boolean for the repo's
# working-tree state: true if uncommitted changes exist, false otherwise.
# `git status --porcelain` is O(file count) and BSD-safe. Ignores
# submodules (the scripts dir is self-contained; if it grows submodules,
# add --ignore-submodules=dirty). CANONICAL-BOOLEAN CONTRACT (C1 fix):
# prints true/false — the exact values _provenance_write normalizes and
# the verifier's dirty branch matches — never bare 0/1.
_git_dirty_porcelain() {
    if [ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]; then
        printf 'true'
    else
        printf 'false'
    fi
}

# _tip_sha — print the integration-tip SHA. Resolution order: the
# remote-tracking ref refs/remotes/origin/latest, then the local BRANCH
# ref refs/heads/latest. Both are FULL refspecs — the bare-`latest` DWIM
# form is deliberately NOT used because it can resolve a TAG named
# `latest` (refs/tags/latest wins in git's DWIM order), which would pin
# the tip to a tag instead of the integration branch. Returns 1 if no
# tip ref resolves. The tip is the highest commit on the integration
# branch — a non-tip staging tree either missed a merge (stale tip
# behind the tag) or is on a feature branch (intentional but explicit
# override required for the v0.16.13 payload case where the tag was
# placed BEFORE the merges that included the actual fixes).
_tip_sha() {
    local tip
    tip="$(git -C "$REPO_ROOT" rev-parse --verify refs/remotes/origin/latest 2>/dev/null)" \
        && [ -n "$tip" ] && printf '%s' "$tip" && return 0
    tip="$(git -C "$REPO_ROOT" rev-parse --verify refs/heads/latest 2>/dev/null)" \
        && [ -n "$tip" ] && printf '%s' "$tip" && return 0
    return 1
}

# _stage_freshness_journal_override <reason_token> <detail> — best-effort
# journal append for a freshness-guard override. Idempotent and unlocked
# (the override event is informational; the lock is for serialization with
# promote/rollback, which doesn't apply to pre-lock stage). The append
# rides plain journal_history_append — additive, no new write mechanics.
# DURABLE LANDING (S1): the journal file is ensured to exist before the
# append (journal_init is idempotent), so a VIRGIN install dir still
# records the override — the audit trail does not depend on a prior
# pipeline op having initialized the journal. The init is gated on the
# install dir ALREADY existing: a refusal/override never bootstraps an
# install path that stage itself has not created yet.
_stage_freshness_journal_override() {
    local reason="$1" detail="$2"
    if [ -d "${INSTALL_DIR:-}" ]; then
        journal_init >/dev/null 2>&1 || true
    fi
    journal_history_append "stage_freshness_override" \
        "$detail (reason=$reason operator_accepted=true)" \
        >/dev/null 2>&1 \
        || _warn "freshness-override journal append FAILED (best-effort) — override is taken but the audit trail is incomplete; reason=$reason"
}

# _freshness_refuse <token> <msg...> — the freshness guard's refusal
# wrapper: ensures the install dir's journal exists (same durable-landing
# gate as _stage_freshness_journal_override) then delegates to the shared
# _refuse (loud WARN with (reason=<token>) + best-effort journal append +
# exit 78). Shared _refuse is NOT modified — promote/rollback refusal
# semantics are untouched; only the freshness guard durably lands its
# events on a virgin install dir.
_freshness_refuse() {
    local token="$1"; shift
    if [ -d "${INSTALL_DIR:-}" ]; then
        journal_init >/dev/null 2>&1 || true
    fi
    _refuse "$token" "$@"
}

# ═══════════════ Plugin-staleness promote predicate (slice ⑥) ═══════════════
#
# promote_plugin_staleness_check — REC §1.2 component 13
# (promote_staleness_check): pin age > N days OR unresolved divergence
# ⇒ refuse; unowned drift alarms escalate to the same refusal after
# escalation days (CON §5: "unowned for escalation days ⇒ block
# promote"). Wired into promote.sh preflight (before integrity, before
# any mutation — the same fail-closed family as the freshness guards).
#
# DATA SOURCE: the plugin tree under $REPO_ROOT/plugins (each dir with
# a MANIFEST.yaml): the predicate consumes the sync-runner's trail
# (sync_trail.jsonl — the sync_result directly, per the ③ probe doc)
# + the manifest's divergence-register statuses. OFFLINE — no git, no
# DB, no daemon. A repo with NO plugins/ dir passes trivially (the
# gate must not spuriously block plugin-less promotes).
#
# OVERRIDE: --allow-stale-plugins sets PROMOTE_STALENESS_OVERRIDE=1
# (argv-only, mirrors --allow-stale-stage); every overridden refusal
# is JOURNALED on the install dir (reason token + operator_accepted).
#
# IMPLEMENTATION NOTE: the predicate is a stdlib+PyYAML-only Python
# module (daemon/plugin_subsystem/promote_staleness.py) invoked by
# FILE PATH (not ``-m``) so promote-time needs no package import and
# no venv: the first of $PLUGIN_STALENESS_PYTHON / $REPO_ROOT/.venv/
# bin/python / python3 / python that executes wins. A python that
# cannot run the predicate refuses FAIL-CLOSED (plugin-staleness-
# unreadable) unless overridden — an unevaluable gate is a refused
# gate, never a silent pass.
#
# Exit-code contract of THIS function: returns 0 = fresh/overridden;
# every refusal path exits 78 itself (the promote_entry_check caller
# convention — L11 tidier).

_plugin_staleness_journal_override() {
    local reason="$1" detail="$2"
    if [ -d "${INSTALL_DIR:-}" ]; then
        journal_init >/dev/null 2>&1 || true
    fi
    journal_history_append "plugin_staleness_override" \
        "$detail (reason=$reason operator_accepted=true)" \
        >/dev/null 2>&1 \
        || _warn "plugin-staleness override journal append FAILED (best-effort) — override is taken but the audit trail is incomplete; reason=$reason"
}

promote_plugin_staleness_check() {
    local plugins_root="${PLUGIN_STALENESS_PLUGINS_ROOT:-}"
    if [ -z "$plugins_root" ]; then
        local repo_root_guess="${REPO_ROOT:-}"
        if [ -z "$repo_root_guess" ]; then
            repo_root_guess="$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd)" || repo_root_guess=""
        fi
        [ -z "$repo_root_guess" ] && repo_root_guess="$PWD"
        plugins_root="$repo_root_guess/plugins"
    fi
    # No plugins tree ⇒ nothing to check; a fresh-state promote on a
    # plugin-less repo must NOT block (the gate is plugin-scoped).
    if [ ! -d "$plugins_root" ]; then
        _log "plugin-staleness: no plugins tree at $plugins_root — gate passes (nothing to check)"
        return 0
    fi

    # Collect plugin dirs carrying a manifest (fail-closed on unreadable
    # manifests is the PREDICATE's job — manifest-unreadable token).
    local plugin_dirs=""
    local d
    for d in "$plugins_root"/*/; do
        [ -d "$d" ] || continue
        if [ -f "${d}MANIFEST.yaml" ]; then
            plugin_dirs="$plugin_dirs ${d%/}"
        fi
    done
    if [ -z "$plugin_dirs" ]; then
        _log "plugin-staleness: no manifest-bearing plugin dirs under $plugins_root — gate passes"
        return 0
    fi

    # Resolve the interpreter (see IMPLEMENTATION NOTE above).
    local py="" cand
    if [ -n "${PLUGIN_STALENESS_PYTHON:-}" ] && [ -x "${PLUGIN_STALENESS_PYTHON}" ]; then
        py="$PLUGIN_STALENESS_PYTHON"
    fi
    if [ -z "$py" ]; then
        for cand in \
            "${REPO_ROOT:-/nonexistent}/.venv/bin/python" \
            "$(command -v python3 2>/dev/null || true)" \
            "$(command -v python 2>/dev/null || true)"
        do
            if [ -n "$cand" ] && [ -x "$cand" ]; then
                if "$cand" -c "import yaml" >/dev/null 2>&1; then
                    py="$cand"
                    break
                fi
            fi
        done
    fi
    if [ -z "$py" ]; then
        # Fail-closed: an unevaluable gate refuses (overridable).
        local detail="no python with PyYAML available (tried PLUGIN_STALENESS_PYTHON, $REPO_ROOT/.venv/bin/python, python3, python)"
        if [ "${PROMOTE_STALENESS_OVERRIDE:-0}" = "1" ]; then
            _warn "PLUGIN-STALENESS OVERRIDE: predicate cannot run — proceeding (operator accepted; journaled)"
            _plugin_staleness_journal_override plugin-staleness-unreadable \
                "override: predicate unevaluable ($detail)"
            return 0
        fi
        _freshness_refuse plugin-staleness-unreadable \
            "plugin-staleness check cannot run: $detail. " \
            "The gate refuses FAIL-CLOSED when it cannot evaluate (a silent pass would make staleness invisible — the exact failure this predicate exists to kill). " \
            "Remedies, in order: " \
            "  1) run promote from a session with python3 + PyYAML on PATH " \
            "  2) set PLUGIN_STALENESS_PYTHON=<interpreter with PyYAML> " \
            "  3) pass --allow-stale-plugins to override (JOURNALED on the install dir; unsafe on real rungs)"
    fi

    local pred="$(
        cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd
    )"
    pred="$pred/../../daemon/plugin_subsystem/promote_staleness.py"
    if [ ! -f "$pred" ]; then
        # SCRIPT_DIR-based resolution failed (sourced from elsewhere);
        # fall back to the REPO_ROOT-relative location.
        pred="${REPO_ROOT:-}/daemon/plugin_subsystem/promote_staleness.py"
    fi
    if [ ! -f "$pred" ]; then
        if [ "${PROMOTE_STALENESS_OVERRIDE:-0}" = "1" ]; then
            _warn "PLUGIN-STALENESS OVERRIDE: predicate module not found — proceeding (operator accepted; journaled)"
            _plugin_staleness_journal_override plugin-staleness-unreadable \
                "override: predicate module not found (looked next to lib.sh and at $REPO_ROOT/daemon/plugin_subsystem/)"
            return 0
        fi
        _freshness_refuse plugin-staleness-unreadable \
            "plugin-staleness predicate module not found (looked next to lib.sh and at ${REPO_ROOT:-?}/daemon/plugin_subsystem/promote_staleness.py). " \
            "The gate refuses FAIL-CLOSED when it cannot evaluate. " \
            "Remedy: run promote from the repo checkout (scripts/upgrade/promote.sh), or pass --allow-stale-plugins to override (JOURNALED)."
    fi

    local pdir out rc code
    for pdir in $plugin_dirs; do
        out="$("$py" "$pred" "$pdir" 2>&1)"
        rc=$?
        if [ "$rc" -eq 0 ]; then
            _log "plugin-staleness: $(printf '%s' "$out" | tail -n 1)"
            continue
        fi
        # First reported token (the predicate prints code=<token> per
        # reason); a crash (rc not in {0,3}) maps to the unreadable
        # token so the journal taxonomy stays greppable.
        code="$(printf '%s\n' "$out" | sed -n 's/.*code=\([a-z0-9-]*\).*/\1/p' | head -n 1)"
        if [ "$rc" -ne 3 ] || [ -z "$code" ]; then
            code="plugin-staleness-unreadable"
        fi
        if [ "${PROMOTE_STALENESS_OVERRIDE:-0}" = "1" ]; then
            _warn "PLUGIN-STALENESS OVERRIDE: $pdir is stale ($code) — proceeding (operator accepted; journaled)"
            _plugin_staleness_journal_override "$code" \
                "override: $pdir failed the plugin-staleness predicate (rc=$rc): $(printf '%s\n' "$out" | head -n 8 | tr '\n' ' ')"
            continue
        fi
        _freshness_refuse "$code" \
            "promote refused: plugin $pdir failed the plugin-staleness predicate (rc=$rc): $(printf '%s\n' "$out" | head -n 8 | tr '\n' ' ') " \
            "Remedies, in order: " \
            "  1) fix the named condition (refresh the pin via a real sync; land the divergence disposition; assign alarm_owner) " \
            "  2) pass --allow-stale-plugins to override (JOURNALED on the install dir; unsafe on real rungs)"
    done
    return 0
}


# _verify_artifact_provenance <artifact_abs_path> — verify provenance matches
# the current tree. Refuses (exit 78) with a distinct reason token unless
# STAGE_FRESHNESS_OVERRIDE=1. Token + override journaling in one place so
# the operator can grep `reason=...` on the install dir's state.json to
# audit every override. Tip-identity (non-tip-tree) is checked separately
# at the stage.sh entry point, before this helper runs.
#
# FAIL-CLOSED FIELD CONTRACT (C1/C2 fix, cn 0472b31f): a sidecar that
# EXISTS must carry ALL THREE load-bearing fields — git_head (7-40 hex),
# git_dirty (exactly true/True/1 or false/False/0), artifact_sha256
# (64 hex) — each syntactically valid. ANY missing/empty/unrecognized
# value refuses provenance-malformed. No value skips a check, ever: the
# old `[ -n "$field" ] &&` guards are GONE — an empty field was a silent
# skip, which is exactly the trap family this guard exists to kill.
#
# Refusal token vocabulary (the verify helper emits one of):
#   provenance-missing, provenance-malformed, stale-provenance,
#   dirty-build, provenance-hash-mismatch
_verify_artifact_provenance() {
    local art="$1" prov json git_head git_head_short git_dirty art_sha current_sha
    local dirty_flag="" malformed=""
    prov="$(_provenance_path "$art")"
    if [ ! -f "$prov" ]; then
        if [ "${STAGE_FRESHNESS_OVERRIDE:-0}" = "1" ]; then
            _warn "FRESHNESS OVERRIDE: provenance missing for $art — proceeding (operator accepted; journaled on install dir)"
            _stage_freshness_journal_override provenance-missing \
                "override: $art has no .build-provenance.json sidecar (looked for $prov)"
            return 0
        fi
        _freshness_refuse provenance-missing \
            "stale-artifact stage refused: no build provenance for $art (looked for $prov). " \
            "Blind reuse of prebuilt artifacts is structurally impossible (cn 0472b31f trap family). " \
            "Remedies, in order: " \
            "  1) rebuild: 'rm -rf dist/ frontend/dist/' and re-run stage (build path writes provenance) " \
            "  2) for --skip-build fixtures: the provided artifact must carry a .build-provenance.json sidecar; see _provenance_write in scripts/upgrade/lib.sh " \
            "  3) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
    fi
    # Read via the shared reader (single path resolution for writer and
    # verifier). _PROVENANCE_READ_STATUS is the dispatch: absent/unreadable
    # → provenance-missing; malformed (empty / torn / quote-imbalance /
    # laundering signature — see _provenance_read + _provenance_sidecar_ok)
    # → provenance-malformed; ok → fall through to the shape gate.
    #
    # Call _provenance_read DIRECTLY (no command substitution) and capture
    # stdout via a temp file. The _PROVENANCE_READ_STATUS /
    # _PROVENANCE_READ_DETAIL globals are set by _provenance_read IN the
    # caller's scope this way — a previous `json="$(...)"` subshell
    # silently dropped the mutations and the case dispatch below never
    # fired (review round 3 d-1: the 4-state routing was DEAD CODE; the
    # shape-gate "missing" message was reaching operators for the
    # laundering class). rc 0 means the sidecar passed the sidecar_ok
    # gate (status=ok, content on stdout); rc 1 means one of the three
    # failure states — read the globals to learn which.
    local _pr_tmp _pr_status _pr_detail _pr_json=""
    _pr_tmp="$(mktemp)"
    if _provenance_read "$art" > "$_pr_tmp" 2>/dev/null; then
        _pr_status="ok"
        _pr_json="$(cat "$_pr_tmp")"
    else
        _pr_status="$_PROVENANCE_READ_STATUS"
        _pr_detail="$_PROVENANCE_READ_DETAIL"
    fi
    rm -f "$_pr_tmp"
    case "$_pr_status" in
        absent)
            _freshness_refuse provenance-missing \
                "stale-artifact stage refused: no build provenance for $art (looked for $prov). " \
                "Blind reuse of prebuilt artifacts is structurally impossible (cn 0472b31f trap family). " \
                "Remedies, in order: " \
                "  1) rebuild: 'rm -rf dist/ frontend/dist/' and re-run stage (build path writes provenance) " \
                "  2) for --skip-build fixtures: the provided artifact must carry a .build-provenance.json sidecar; see _provenance_write in scripts/upgrade/lib.sh " \
                "  3) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
            ;;
        unreadable)
            _freshness_refuse provenance-missing \
                "stale-artifact stage refused: provenance sidecar $prov exists but is unreadable. " \
                "A sidecar that exists must carry all three load-bearing fields (git_head, git_dirty, artifact_sha256). " \
                "Remedies, in order: " \
                "  1) rebuild: 'rm -rf dist/' and re-run stage (the build path rewrites the sidecar atomically) " \
                "  2) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
            ;;
        malformed)
            _freshness_refuse provenance-malformed \
                "stale-artifact stage refused: provenance sidecar for $art is MALFORMED (${_pr_detail}). " \
                "A sidecar that exists must carry all three load-bearing fields, syntactically valid, AND be well-formed enough " \
                "that the field extractor can trust it (no quote/brace imbalance, no laundering signature — a decoy key in a " \
                "non-key field's value would otherwise win the textual first-occurrence race and let a dirty build pass as clean). " \
                "Remedies, in order: " \
                "  1) rebuild: 'rm -rf dist/' and re-run stage (the build path rewrites the sidecar atomically) " \
                "  2) if the sidecar was hand-maintained: fix the named issue (empty / unbalanced / duplicate key) " \
                "  3) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
            ;;
    esac
    # ok → _pr_json holds the validated content; fall through to the shape gate.
    # Field extraction uses the QUOTED-key form (_json_field_quoted): the
    # bare-substring _json_field would match the "git_head" INSIDE
    # "git_head_short" when the real "git_head" key is absent — turning a
    # provenance-malformed case into a bogus stale-provenance (found by
    # scenario viii-d). Anchoring on `"key":` defeats the aliasing.
    git_head="$(_json_field_quoted "$_pr_json" '"git_head":' 2>/dev/null)" || git_head=""
    git_head_short="$(_json_field_quoted "$_pr_json" '"git_head_short":' 2>/dev/null)" || git_head_short=""
    git_dirty="$(_json_field_quoted "$_pr_json" '"git_dirty":' 2>/dev/null)" || git_dirty=""
    art_sha="$(_json_field_quoted "$_pr_json" '"artifact_sha256":' 2>/dev/null)" || art_sha=""
    current_sha="$(_sha256 "$art")"
    # ── Shape gate (C2): every load-bearing field present + syntactically
    #    valid, BEFORE any comparison runs. Empty no longer means skip.
    if [ -z "$git_head" ]; then
        malformed="git_head missing/empty"
    elif [ -z "$git_dirty" ]; then
        malformed="git_dirty missing/empty"
    elif [ -z "$art_sha" ]; then
        malformed="artifact_sha256 missing/empty"
    elif ! printf '%s' "$git_head" | grep -Eq '^[0-9a-fA-F]{7,40}$'; then
        malformed="git_head not a git sha (got '$git_head')"
    elif ! dirty_flag="$(_provenance_dirty_canon "$git_dirty")"; then
        malformed="git_dirty not a recognized boolean true/True/1 or false/False/0 (got '$git_dirty')"
    elif ! printf '%s' "$art_sha" | grep -Eq '^[0-9a-fA-F]{64}$'; then
        malformed="artifact_sha256 not a sha256 digest (got '$art_sha')"
    elif [ -n "$git_head_short" ] && ! printf '%s' "$git_head_short" | grep -Eq '^[0-9a-fA-F]{7,40}$'; then
        # git_head_short is INFORMATIONAL (gates no comparison) but it is
        # operator-facing: garbage free text is a corruption signal, so a
        # PRESENT-and-garbage value refuses (fail-closed symmetry with
        # git_head). Absent/empty is tolerated — it is not load-bearing.
        malformed="git_head_short not a git sha (got '$git_head_short')"
    fi
    if [ -n "$malformed" ]; then
        if [ "${STAGE_FRESHNESS_OVERRIDE:-0}" = "1" ]; then
            _warn "FRESHNESS OVERRIDE: provenance sidecar malformed for $art ($malformed) — proceeding (operator accepted; journaled)"
            _stage_freshness_journal_override provenance-malformed \
                "override: $art sidecar malformed ($malformed)"
            return 0
        fi
        _freshness_refuse provenance-malformed \
            "stale-artifact stage refused: provenance sidecar for $art is MALFORMED ($malformed). " \
            "A sidecar that exists must carry all three load-bearing fields, syntactically valid — " \
            "an unrecognized value must never silently skip its check (cn 0472b31f trap family). " \
            "Remedies, in order: " \
            "  1) rebuild: 'rm -rf dist/' and re-run stage (the build path rewrites the sidecar atomically) " \
            "  2) if the sidecar was hand-maintained: fix the named field to the documented shape (docs/runbooks/upgrade-drills.md §A.2) " \
            "  3) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
    fi
    # ── Comparison checks. Every value here is GUARANTEED non-empty and
    #    shape-valid by the gate above — no `[ -n ]` guards, no skips.
    if [ "$git_head" != "$HEAD_SHA" ]; then
        if [ "${STAGE_FRESHNESS_OVERRIDE:-0}" = "1" ]; then
            _warn "FRESHNESS OVERRIDE: provenance head mismatch for $art (built=$git_head current=$HEAD_SHA) — proceeding (operator accepted; journaled)"
            _stage_freshness_journal_override stale-provenance \
                "override: $art was built at $git_head but staging tree is at $HEAD_SHA"
            return 0
        fi
        _freshness_refuse stale-provenance \
            "stale-artifact stage refused: $art was built at a different commit " \
            "(built at ${git_head:0:12}, current HEAD=$HEAD_SHA). " \
            "Reuse of prebuilt artifacts from a different tree is structurally impossible (cn 0472b31f trap family). " \
            "Remedies, in order: " \
            "  1) rebuild from the current tree: 'rm -rf dist/' and re-run stage (writes provenance) " \
            "  2) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
    fi
    if [ "$dirty_flag" = "true" ]; then
        if [ "${STAGE_FRESHNESS_OVERRIDE:-0}" = "1" ]; then
            _warn "FRESHNESS OVERRIDE: $art was built on a dirty tree — proceeding (operator accepted; journaled)"
            _stage_freshness_journal_override dirty-build \
                "override: $art was built on a dirty tree (HEAD=$HEAD_SHA)"
            return 0
        fi
        _freshness_refuse dirty-build \
            "stale-artifact stage refused: $art was built on a dirty (uncommitted) tree. " \
            "Staging an artifact whose build includes uncommitted changes is unsafe: the staged binary " \
            "diverges from any tag on the tree, and an idempotent re-stage would re-build with different " \
            "outputs. Remedies, in order: " \
            "  1) commit the changes, rebuild, re-run stage " \
            "  2) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
    fi
    if [ "$art_sha" != "$current_sha" ]; then
        if [ "${STAGE_FRESHNESS_OVERRIDE:-0}" = "1" ]; then
            _warn "FRESHNESS OVERRIDE: $art hash mismatch (manifest=$art_sha actual=$current_sha) — proceeding (operator accepted; journaled)"
            _stage_freshness_journal_override provenance-hash-mismatch \
                "override: $art hash mismatch (manifest=$art_sha actual=$current_sha HEAD=$HEAD_SHA)"
            return 0
        fi
        _freshness_refuse provenance-hash-mismatch \
            "stale-artifact stage refused: $art hash doesn't match its build provenance. " \
            "The artifact was modified AFTER its build (manifest=$art_sha, actual=$current_sha). " \
            "Remedies, in order: " \
            "  1) rebuild: 'rm -rf dist/' and re-run stage (writes fresh provenance) " \
            "  2) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
    fi
    return 0
}

# _verify_tip_identity — refuse if the staging tree is not at the
# integration tip. The tip SHA comes from _tip_sha (refs/remotes/origin/
# latest or refs/heads/latest — full refspecs only, never the bare-DWIM
# `latest` which could resolve a TAG named latest). Refuses with reason
# token non-tip-tree; STAGE_FRESHNESS_OVERRIDE unlocks + journals. The
# HEAD_SHA must be set by the caller (stage.sh computes it at entry so
# this helper is pure-readable).
#
# TWO MODES (documented in upgrade-drills.md §A.1):
#   ACTIVE  — a tip ref resolves and HEAD == tip: the gate is engaged and
#             PASSES the tree through to L2. No output.
#   REFUSE  — a tip ref resolves and HEAD != tip: exit 78 (or override).
#   WARN+SKIP — NO tip ref resolves (no origin/latest, no local latest —
#             e.g. a CI checkout or a fixture repo without integration
#             refs): the check is SKIPPED with a loud WARN and L2
#             (provenance) remains the sole gate. The skip is always
#             visible on stderr; it is never silent.
#
# The v0.16.13 payload case: tag v0.16.13 was placed at commit C; the
# actual fix commits landed AFTER C on the integration branch, so the
# tip has moved past C. Staging from C would produce a binary without
# the fixes. This check refuses that path unless the operator explicitly
# overrides (and the override is journaled on the install dir).
_verify_tip_identity() {
    local tip
    tip="$(_tip_sha 2>/dev/null || true)"
    if [ -z "$tip" ] || [ -z "$HEAD_SHA" ]; then
        # WARN+SKIP mode: no tip ref resolves (e.g. CI without origin, or
        # a fixture repo without integration refs). The check is skipped
        # LOUDLY — L2 (provenance) remains the sole gate in this mode.
        _warn "tip-identity check SKIPPED (WARN+SKIP mode): no integration tip ref resolves (HEAD=$HEAD_SHA tip=<unset>; looked for refs/remotes/origin/latest then refs/heads/latest). L2 provenance check remains the sole gate."
        return 0
    fi
    if [ "$tip" != "$HEAD_SHA" ]; then
        if [ "${STAGE_FRESHNESS_OVERRIDE:-0}" = "1" ]; then
            _warn "FRESHNESS OVERRIDE: tip-identity mismatch (HEAD=$HEAD_SHA tip=$tip) — proceeding (operator accepted; journaled)"
            _stage_freshness_journal_override non-tip-tree \
                "override: staging tree at $HEAD_SHA but integration tip is at $tip"
            return 0
        fi
        _freshness_refuse non-tip-tree \
            "stale-artifact stage refused: staging tree is NOT at the integration tip " \
            "(HEAD=${HEAD_SHA:0:12} tip=${tip:0:12}). " \
            "The tag at HEAD does not include fixes that landed on the tip after the tag was placed. " \
            "This catches the v0.16.13 payload case: a tag placed on a non-tip commit, with the actual " \
            "fixes landing later on the tip. Remedies, in order: " \
            "  1) merge your work into the integration branch and re-tag at the tip, then re-run stage — " \
            "or, if the local branch is ahead of origin: `git fetch origin` (a stale " \
            "refs/remotes/origin/latest with fixes still landing there would be refreshed), " \
            "or `git push origin <branch>:latest` first so the remote-tracking tip catches up, then re-check " \
            "  2) pass --allow-stale-stage to override (JOURNALED on the install dir; unsafe on real rungs)"
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
#
# Commission v0.16.6 component 2 (Layer ii): this is called FROM a lock
# holder — promote.sh / rollback.sh / restart.sh hold the rollback.lock.d
# through soak+rollback. Set PIPELINE_LOCK_HELD_BY_CALLER=1 so the child
# stop-ensemble.sh skips its own lock acquire (otherwise it would busy-wait
# 15s on the lock the parent already holds — every promote would deadlock
# 15s on its stop span).
stop_via_stop_script() {
    local stop_script
    stop_script="$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)/../stop-ensemble.sh"
    if [ ! -f "$stop_script" ]; then
        # when lib.sh is sourced from a copied tree, fall back to repo layout
        stop_script="$(pwd)/scripts/stop-ensemble.sh"
    fi
    # P1 §5(b) — the STOP SITE: emit exactly ONE machine-readable
    # ENSEMBLE_SUPERVISION_RESULT line (the pre-flight already emitted its
    # own; enforcement stayed there — the stop site NEVER refuses
    # mid-pipeline, so an unresolved-explicit-unit rc is ignored here).
    supervision_classify || true
    # P2 §3 (2026-09-29) — DUAL_FIGHT refuses BEFORE any stop action: no
    # TERM is sent, no systemctl stop issued. Distinct from the
    # unresolved-explicit-unit rc ignored above: an unresolvable NAME is a
    # benign degradation (the script path still stops owned pids
    # correctly), while a DUAL_FIGHT fault means two masters are live —
    # stopping via EITHER path could kill the wrong thing or
    # false-succeed — so the halt path (journal halt event + exit 78,
    # inside supervision_dualfight_check) is the only safe answer. The
    # pre-flight already checked; this is the stop-site re-check (state
    # may have drifted across the run — e.g. promote's rollback stop runs
    # ~5min after its pre-flight, post flip + restart).
    supervision_dualfight_check
    # P2 §1 (b″ fix) — UNIT_MANAGED with a resolvable unit name hands the
    # classification VALUE to the stop script (machine-line grammar, same
    # '<state>[:<unit>]' format as the machine line above) so it stops
    # via `systemctl stop` + a UNIT-STATE poll (a unit-respawned
    # replacement pid is invisible to a pid poll; the unit state is not).
    # Every other shape keeps the byte-identical pid-scoped invocation —
    # no new env reaches the child on the script path.
    if [ "${SUPERVISION_STATE:-}" = "UNIT_MANAGED" ] && [ -n "${SUPERVISION_UNIT:-}" ]; then
        _log "stop: UNIT path — systemctl stop ${SUPERVISION_UNIT} + unit-state poll via $stop_script (b″: respawn-invisible-to-pid-poll fix)"
        # P3: snapshot the unit's PRE-STOP MainPID (behind the same host
        # guard family as the hand-back; the daemon is still live HERE —
        # stop_via_stop_script runs pre-stop by contract) —
        # restart_via_launcher's unit hand-back verifies the post-start
        # MainPID is NEW (≠ this). Unresolvable → empty (the verify then
        # requires only a nonzero NEW pid).
        SUPERVISION_PRESTOP_MAINPID=""
        if _supervision_host_allows_unit && command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1; then
            SUPERVISION_PRESTOP_MAINPID="$("$SYSTEMCTL_BIN" show "$SUPERVISION_UNIT" -p MainPID --value 2>/dev/null || true)"
            [ -n "$SUPERVISION_PRESTOP_MAINPID" ] || SUPERVISION_PRESTOP_MAINPID=""
        fi
        # PIPELINE_LOCK_HELD_BY_CALLER=1 — the parent caller (promote /
        # rollback / restart) already holds the rollback.lock.d through
        # soak+rollback; the child stop-ensemble.sh MUST skip its own
        # acquire (Layer ii — Layer i's settle-check too: the pipeline
        # is manifestly not settled while the parent holds the lock).
        PIPELINE_LOCK_HELD_BY_CALLER=1 \
            ENSEMBLE_SUPERVISION_RESULT="${SUPERVISION_STATE}:${SUPERVISION_UNIT}" \
            bash "$stop_script" "$INSTALL_DIR" "$PORT"
    else
        _log "stop: ownership-scoped SINGLE-TERM via $stop_script"
        # PIPELINE_LOCK_HELD_BY_CALLER=1 — same reason: the parent holds
        # the lock; the child MUST NOT acquire (Layer ii).
        PIPELINE_LOCK_HELD_BY_CALLER=1 \
            bash "$stop_script" "$INSTALL_DIR" "$PORT"
    fi
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
#
# Portability (fix/portable-atomic-flip-linux, 2026-09-21): the flip
# MUST work identically on BSD/macOS and GNU/Linux. Plain `mv -f` is
# WRONG on either — it would follow current→releases/<old> and move the
# temp link INSIDE the old release dir, silently leaving `current` on
# the OLD release.
#   BSD/macOS: `mv -h -f`  — swap the SYMLINK itself (don't follow
#     DEST when DEST is a symlink-to-dir).
#   GNU/Linux: `mv -T -f`  — treat DEST as a normal file (no-follow
#     DEST semantics).
# We dispatch on `uname -s` rather than chaining `mv -h -f X Y ||
# mv -T -f X Y`: on BSD a REAL -h failure (permission denied, missing
# source, RO filesystem) would otherwise hit an invalid -T and surface
# a wrong-platform error, hiding the real cause. Unrecognized platforms
# refuse (fail-closed; we never guess). Verified on macOS mv(1) and
# GNU coreutils 9.4 (Linux).
atomic_flip() {
    local ver="$1"
    local mv_args=""
    case "$(uname -s)" in
        Darwin|*BSD*|*bsd*)
            mv_args="-h -f"
            ;;
        Linux|GNU*|*GNU*)
            mv_args="-T -f"
            ;;
        *)
            _warn "atomic_flip: unrecognized platform '$(uname -s)' — refusing to flip (fail-closed; BSD or Linux required)"
            return 1
            ;;
    esac
    ln -sfn "releases/$ver" "$INSTALL_DIR/current.new.$$" || return 1
    # mv_args intentionally word-split (a single string of options)
    # shellcheck disable=SC2086
    if ! mv $mv_args "$INSTALL_DIR/current.new.$$" "$INSTALL_DIR/current"; then
        rm -f "$INSTALL_DIR/current.new.$$"
        return 1
    fi
    _log "current -> releases/$ver (atomic flip)"
    return 0
}

# ── Supervision-aware hand-back (P3, ownership-mode commission 2026-09-29) ───
#
# STUB SEAM (P2/P5 — mirrors scripts/stop-ensemble.sh:323): EVERY systemctl
# interaction on the lib.sh side routes through SYSTEMCTL_BIN (default
# 'systemctl' — PATH-resolved, so PATH-injected stubs work exactly like the
# comp7 suite's systemctl stubs). Point SYSTEMCTL_BIN at a script to drive
# scripted sequences: hand-back failure→halt-NO-fallback, is-active
# sequences, MainPID changes (P5 will script all of these).
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"

# SUPERVISION_UNIT_DIR — directory scanned for unit FILES by the
# nohup-survivor heal inside supervision_classify (supervisor war r3,
# 2026-09-30). Default /etc/systemd/system = the adopt-unit.sh DEST
# convention (admin-managed units: adopt-unit generates there, the
# runbook's hand-provisioned ensemble-*.service units live there —
# lib.sh:1990 context). Test seam, SYSTEMCTL_BIN style: point it at a
# fixture dir so heal cells never depend on the host's real /etc/systemd.
SUPERVISION_UNIT_DIR="${SUPERVISION_UNIT_DIR:-/etc/systemd/system}"

# _supervision_host_allows_unit — the shared host guard for every unit
# hand-back path: Linux + a live systemd ONLY. The uname check runs FIRST
# so non-Linux hosts take zero new /proc or /run reads (P1 guard
# discipline — the BSD/macOS arms stay byte-identical).
_supervision_host_allows_unit() {
    [ "$(uname -s)" = "Linux" ] || return 1
    [ -d /run/systemd/system ] || return 1
    return 0
}

# _supervision_unit_serving <unit> <prestop_pid> — hand-back VERIFIER.
# Polls (deadline-bounded by LIVEZ_BUDGET_S, lock-heartbeat every
# iteration — the _probe precedent) until the unit's MainPID is a NEW
# nonzero pid — ≠ <prestop_pid> whenever one was captured — AND the port
# is serving (lsof conjunct, PATH-stubbable; lsof absent or PORT unknown
# → the MainPID check alone decides — never fault on a missing tool,
# _unit_stopped precedent in stop-ensemble.sh). Prints the new MainPID on
# success; returns 1 with a loud warn otherwise. A MainPID EQUAL to the
# pre-stop pid fails FAST (no retry loop can fix it: the old daemon never
# left — the start no-op'd on an active unit, or the stop
# false-succeeded).
_supervision_unit_serving() {
    local unit="$1" prestop="$2" deadline now mp
    deadline=$(( $(_now_epoch) + LIVEZ_BUDGET_S ))
    while :; do
        lock_heartbeat
        mp="$("$SYSTEMCTL_BIN" show "$unit" -p MainPID --value 2>/dev/null || true)"
        case "$mp" in
            ''|0) ;;  # not up yet — keep polling
            *)
                if [ -n "$prestop" ] && [ "$mp" = "$prestop" ]; then
                    _warn "hand-back verify: unit $unit MainPID=$mp EQUALS the pre-stop pid — the old daemon never left (start no-op'd on an active unit, or the stop false-succeeded); NOT success"
                    return 1
                fi
                if [ -n "${PORT:-}" ] && command -v lsof >/dev/null 2>&1; then
                    if lsof -ti:"$PORT" >/dev/null 2>&1; then
                        printf '%s\n' "$mp"
                        return 0
                    fi
                    # port not serving yet — fall through to the deadline
                else
                    printf '%s\n' "$mp"
                    return 0
                fi
                ;;
        esac
        now="$(_now_epoch)"
        [ "$now" -lt "$deadline" ] || break
        sleep 2
    done
    _warn "hand-back verify: unit $unit not confirmed (new MainPID ≠ ${prestop:-<none>} + port ${PORT:-?} serving) within ${LIVEZ_BUDGET_S}s"
    return 1
}

# _supervision_handback_unit <unit> <prestop_pid> <scope_heal 0|1> <logfile>
# — the unit hand-back (P3 design items 2+3). Sequence:
#   reset-failed → is-active preflight → unit start → verify NEW MainPID
#   (≠ pre-stop) + port serving.
# NO NOHUP FALLBACK — ABSOLUTE (Amendment #1): falling back would
# re-create the survivor lineage; service-mode Restart=/journald
# ownership must remain INTACT through the upgrade — systemd brings up
# the flipped release, never a nohup lineage. ANY failure returns 1
# (never a false success); the CALLER journals its halt event and
# applies the B4 leave-txn-open policy.
#
# [7b SUBSTRING TRAP — the byte-identity spine: the banned systemctl-start
# literal must NEVER appear inside a success-path log line
# (the comp7 7b assert_not_contains pin is the authoritative check).
# Command invocations below route through "$SYSTEMCTL_BIN" and every
# _log/_warn string stays free of that literal — guard comment honored
# at each nearby log site.]
_supervision_handback_unit() {
    local unit="$1" prestop="$2" scope_heal="$3" log="$4"
    local isact errfile first newpid outcome
    # 1. reset-failed — clear any stale failed state so neither the
    #    is-active preflight nor the start is poisoned by it
    #    (best-effort: a reset-failed failure is not itself fatal —
    #    the start below surfaces real problems).
    "$SYSTEMCTL_BIN" reset-failed "$unit" >/dev/null 2>&1 || true
    # 2. is-active preflight — ALREADY-ACTIVE ≠ SUCCESS: a unit start on
    #    an active unit is a NO-OP that exits 0 (the false-success
    #    shape). The verdict comes from the MainPID/port verification
    #    below, NEVER from the start rc.
    isact="$("$SYSTEMCTL_BIN" is-active "$unit" 2>/dev/null || true)"
    [ "$isact" = "active" ] \
        && _warn "unit $unit ALREADY ACTIVE at hand-back — a unit start on an active unit no-ops (exit 0); the MainPID/port verification decides, not the start rc"
    # 3. the start itself (pure command invocation — see the substring-
    #    trap guard at the top of this function).
    errfile="$(mktemp /tmp/.ensemble-hb-sc.XXXXXX)"
    if ! "$SYSTEMCTL_BIN" start "$unit" 2>"$errfile"; then
        first="$(head -n1 "$errfile" 2>/dev/null || true)"
        rm -f "$errfile"
        _warn "unit hand-back FAILED: unit $unit did not come up (${first:-no stderr}) — NO nohup fallback (Amendment #1: a fallback re-creates the survivor lineage and abandons Restart=/journald ownership) — halting for the caller's B4 policy"
        return 1
    fi
    rm -f "$errfile"
    # 4. verify NEW MainPID (differs from pre-stop) + port serving.
    if ! newpid="$(_supervision_unit_serving "$unit" "$prestop")"; then
        _warn "unit hand-back FAILED verification for $unit — NO nohup fallback (Amendment #1) — halting for the caller's B4 policy"
        return 1
    fi
    outcome="$(supervision_map_outcome "${SUPERVISION_MODE:-}" "${SUPERVISION_STATE:-unknown}" "${SUPERVISION_UNIT:-}")"
    outcome="${outcome%%|*}"
    if [ "$scope_heal" = "1" ]; then
        # P3 design item 3: the self-heal of today's live shape, inside
        # the promote — journaled as its own event (D4 splice
        # discipline: additive event kind; A1 outcome name in the
        # payload, additive field naming).
        journal_history_append supervision_handback "scope→unit: scope-survivor lineage handed back to unit $unit inside the promote (self-heal; declared=${SUPERVISION_MODE:-?} verified=${SUPERVISION_STATE:-?} outcome=$outcome; new MainPID $newpid; port ${PORT:-?} serving)" \
            || _warn "supervision_handback scope→unit journal event append FAILED (best-effort — the hand-back itself succeeded)"
    fi
    # (f) mode-aware tail logging: the unit path mentions the unit
    # journal AND the fallback file. [Substring-trap guard: this success
    # line deliberately phrases the start as "serves" — never the banned
    # systemctl-start literal.]
    _log "unit hand-back COMPLETE: $unit serves the flipped release (new MainPID $newpid${prestop:+, pre-stop was $prestop}; port ${PORT:-?} serving; outcome=$outcome) — logs: journalctl -u $unit -f (unit journal) or $log (fallback file)"
    return 0
}

# restart_via_launcher — start the launcher.
#
# comp7 / stretch7 (r-20260928-005506-f82e): on Linux+systemd hosts,
# the nohup launcher inherits the launcher's cgroup — the same
# survivorship model the executor's setsid lived under (and the same
# gap that killed r-f82e). The safer path is to let systemd manage
# the launcher via its unit file (the unit IS the cgroup boundary —
# the executor that follows lives inside it; a unit teardown
# propagates predictably). The systemd-aware branch is OPT-IN
# (``ENSEMBLE_RESTART_UNIT`` env var) because:
#   (a) unit discovery is HOST-MADE (the unit file at
#       /etc/systemd/system/ensemble-live.service is hand-provisioned
#       on ensemble-vm by devops, out-of-repo — see
#       incident doc §2.2 / r-f82e §1.7 #4). A probe here would have
#       to guess unit names that the operator chose;
#   (b) switching the default would break operator hand-runs where
#       the unit doesn't exist (drill / sandbox hosts);
#   (c) the nohup path is the SAME PATH THE PIPELINE USED BEFORE
#       r-f82e — preserving it as the default is a deliberate
#       backstop for hosts that don't run the daemon under systemd.
#
# P3 (ownership-mode commission 2026-09-29) — SUPERVISION-AWARE
# HAND-BACK. The executor's P1 classification — the SUPERVISION_*
# globals set by the LAST supervision_classify in this process (the
# action script's preflight, or fresher: stop_via_stop_script's
# stop-site re-classify — always PRE-STOP, i.e. while the daemon still
# ran) — now SELECTS the hand-back mode. CONSUMED, never re-derived
# here: a fresh classify at hand-back time sees a STOPPED daemon (no
# owned pid) and collapses every shape to SCRIPT_NOHUP, which would
# hand a unit-managed host back via nohup — the exact defect P3
# fixes. Modes:
#   UNIT_MANAGED (+unit)    → unit hand-back (_supervision_handback_unit,
#                             scope_heal=0): reset-failed → is-active
#                             preflight → start → verify NEW MainPID +
#                             port serving. NO NOHUP FALLBACK — ABSOLUTE
#                             (Amendment #1: a fallback re-creates the
#                             survivor lineage; service-mode Restart=/
#                             journald ownership must stay intact through
#                             the upgrade). Failure → rc 1 → the caller
#                             journals a halt event + B4 leave-txn-open
#                             (never false-success).
#   SCOPE_SURVIVOR + unit   → the SAME unit hand-back (scope_heal=1) +
#                             the `supervision_handback scope→unit`
#                             journal event — the self-heal of today's
#                             live shape, inside the promote.
#   SCOPE_SURVIVOR, no unit → today's nohup path BYTE-IDENTICAL + WARN
#                             (the survivor lineage continues; the WARN
#                             names the self-heal configuration).
#   SCRIPT + ENSEMBLE_RESTART_UNIT set → the comp7 opt-in path WITH
#                             the a′ fixes — is-active preflight before
#                             the start, real error propagation (first
#                             stderr line surfaces), no false-success
#                             (already-active verifies port serving
#                             instead of trusting a no-op rc) — while
#                             KEEPING the nohup fallback (comp7 7c pin).
#   EXPLICIT unit + unit-    → M3 (review cycle 1, leader FAIL-CLOSED):
#     incapable host           REFUSES exit 78 (supervision-unit-
#                             incapable-host) — never loud-degrade +
#                             nohup at rc 0. Normally caught at the
#                             preflight; this is the defense-in-depth
#                             arm (SUPERVISION_EXPLICIT_UNIT=1 keys it).
#   everything else         → today's nohup path byte-identical.
#
# (e) SCOPE ESCAPE — deliberately UNCHANGED by P3: the promote
# executor's own scope escape (comp1: systemd-run --scope) places the
# executor in a SIBLING cgroup of the unit, NOT inside it — that is
# what lets the executor SURVIVE a mid-promote unit teardown (the
# r-f82e death mode). P3 moves only the DAEMON's ownership back to
# the unit at hand-back; the executor keeps its sibling scope.
#
# DELIBERATELY DEFERRED (r-f82e follow-up commission candidate):
# auto-discovery of the right unit name. Not implemented here —
# operator-set opt-in only. The full unit-ownership lifecycle
# (which unit, where it's provisioned, how stage.sh promotes the
# unit file) belongs to a separate commission per incident doc
# §1.7 #4 + the deploy-ownership-fix ticket family.
#
# Returns: 0 on every legacy shape (fire-and-forget nohup, comp7 unit
# start, comp7 fallback) AND on a VERIFIED unit hand-back; 1 ONLY on a
# unit-path hand-back failure (Amendment #1: no nohup fallback — the
# caller must halt + B4; never a false success). M3 exception: the
# EXPLICIT-unit-on-unit-incapable-host arm _refuse-EXITS 78 (journaled
# refusal) instead of returning. The caller's gate_* helpers still
# observe the deep result via /livez + /readyz.
restart_via_launcher() {
    mkdir -p "$INSTALL_DIR/data"
    local log="$INSTALL_DIR/data/launcher.log"
    local hb_unit=""
    local hb_mode=""
    local hb_prestop="${SUPERVISION_PRESTOP_MAINPID:-}"
    local _hb_pin=""

    # ── P3 mode selection (consume the PRE-STOP classification) ───────
    if _supervision_host_allows_unit && command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1; then
        case "${SUPERVISION_STATE:-}" in
            UNIT_MANAGED)
                # ── v0.16.9 hand-back pin rung (lane-convergence fix) ──
                # Pre-convergence the daemon runs under ensemble-live.service
                # (cgroup-derived SUPERVISION_UNIT); promoting to
                # ensemble-main.service requires the hand-back to declare/
                # verify the NEW unit so the promote completes instead of
                # halting on declared≠verified. Precedence: env var > .env
                # pin > cgroup leaf, with r3-style validation
                # (ensemble-*.service conformance + unit-file existence,
                # commit e56f2e85) — missing or non-conforming falls
                # through to the cgroup leaf, BYTE-IDENTICAL to current
                # behavior. HAND-BACK SCOPED ONLY: the stop path keeps
                # the cgroup-leaf selection (pre-convergence stop aimed at
                # the pinned unit would pre-flip-abort; a global override
                # would leak into the stop seam).
                _hb_pin="${ENSEMBLE_RESTART_UNIT:-}"
                if [ -z "$_hb_pin" ]; then
                    _hb_pin="$(_supervision_unit_from_dotenv 2>/dev/null)" || _hb_pin=""
                fi
                case "$_hb_pin" in
                    ensemble-*.service)
                        if [ -f "$SUPERVISION_UNIT_DIR/$_hb_pin" ]; then
                            hb_unit="$_hb_pin"
                        else
                            _warn "hand-back: pin '$_hb_pin' set but unit file absent at $SUPERVISION_UNIT_DIR (manual: create the unit file at $SUPERVISION_UNIT_DIR/$_hb_pin or unset ENSEMBLE_RESTART_UNIT) — falling through to cgroup leaf ${SUPERVISION_UNIT:-<none>}"
                        fi
                        ;;
                    *)
                        [ -n "$_hb_pin" ] && _warn "hand-back: pin '$_hb_pin' not ensemble-*.service (manual: correct the name to ensemble-*.service or unset ENSEMBLE_RESTART_UNIT) — falling through to cgroup leaf ${SUPERVISION_UNIT:-<none>}"
                        ;;
                esac
                if [ -z "$hb_unit" ] && [ -n "${SUPERVISION_UNIT:-}" ]; then
                    hb_unit="$SUPERVISION_UNIT"
                fi
                if [ -n "$hb_unit" ]; then
                    hb_mode="unit"
                fi
                ;;
            SCOPE_SURVIVOR)
                # §3 precedence for a NEEDED name, minus the cgroup rung
                # (a scope leaf yields no unit): env ENSEMBLE_RESTART_UNIT
                # > INSTALL_DIR/.env read directly.
                hb_unit="${ENSEMBLE_RESTART_UNIT:-}"
                if [ -z "$hb_unit" ]; then
                    hb_unit="$(_supervision_unit_from_dotenv)"
                    [ -n "$hb_unit" ] || hb_unit=""
                fi
                if [ -n "$hb_unit" ]; then
                    hb_mode="scope-to-unit"
                fi
                ;;
        esac
    elif [ "${SUPERVISION_STATE:-}" = "UNIT_MANAGED" ] && [ -n "${SUPERVISION_UNIT:-}" ]; then
        # Byte-identical BSD/macOS arms: unit paths live behind the host
        # guard. M3 (review cycle 1, leader-ruled FAIL-CLOSED): an
        # EXPLICIT unit declaration (SUPERVISION_EXPLICIT_UNIT=1, set by
        # the ladder-top 'unit' arm of supervision_classify) on a host
        # that cannot do a unit hand-back REFUSES (exit 78,
        # supervision-unit-incapable-host) — silently degrading an
        # explicit assertion to the nohup lineage at rc 0 is the r-f82e
        # surprise class. Defense-in-depth: the preflight normally
        # refuses this shape BEFORE the pipeline starts; only a
        # capability change mid-run (or a direct call) lands here.
        # NON-explicit shapes (auto-resolved UNIT_MANAGED — e.g. systemd
        # present at classify time but systemctl unresolvable at
        # hand-back) KEEP the legacy degrade-LOUD-to-nohup semantics
        # (stop-ensemble.sh :354 precedent) — M3 is confined to the
        # explicit arm.
        if [ "${SUPERVISION_EXPLICIT_UNIT:-0}" = "1" ]; then
            _refuse supervision-unit-incapable-host "restart refused: ENSEMBLE_SUPERVISION=unit is explicit (classification UNIT_MANAGED:${SUPERVISION_UNIT}) but unit hand-back is unavailable here (non-Linux / no systemd / no resolvable systemctl) — never silent-degrade to the nohup lineage (the r-f82e surprise class)"
        fi
        _warn "classification UNIT_MANAGED:${SUPERVISION_UNIT} but unit hand-back is unavailable here (non-Linux / no systemd / no resolvable systemctl) — degrading LOUD to the nohup path"
    fi

    if [ "$hb_mode" = "unit" ]; then
        _log "hand-back: UNIT_MANAGED — the flipped release returns to unit $hb_unit (service-mode Restart=/journald ownership intact; no nohup lineage)"
        _supervision_handback_unit "$hb_unit" "$hb_prestop" 0 "$log"
        return $?
    fi
    if [ "$hb_mode" = "scope-to-unit" ]; then
        _log "hand-back: SCOPE_SURVIVOR + unit $hb_unit configured — SAME unit hand-back (the self-heal of today's live shape, inside the promote)"
        _supervision_handback_unit "$hb_unit" "$hb_prestop" 1 "$log"
        return $?
    fi
    if [ "${SUPERVISION_STATE:-}" = "SCOPE_SURVIVOR" ]; then
        _warn "supervision: SCOPE_SURVIVOR with NO unit configured (env ENSEMBLE_RESTART_UNIT and INSTALL_DIR/.env both empty) — handing back via the byte-identical nohup path (the survivor lineage continues); configure a unit to self-heal into unit ownership at the next promote"
    fi

    # Linux+systemd + operator-opted-in → unit path for the launcher.
    # The unit, if present, runs the launcher as the service user; the
    # unit's cgroup is the survivorship boundary for the executor that
    # follows. Byte-identical macOS / no-systemd / no-opt-in fallback to
    # nohup.
    if [ "$(uname -s)" = "Linux" ] \
       && [ -d "/run/systemd/system" ] \
       && [ -n "${ENSEMBLE_RESTART_UNIT:-}" ]; then
        # a′-1 PREFLIGHT (P3 design item 4): already-active ≠ success —
        # a unit start on an ACTIVE unit is a NO-OP that exits 0 (the
        # false-success shape). Detect it FIRST; verify port serving
        # instead of ever trusting a no-op rc.
        #
        # [7b FREEZE CONTRACT — M5 (review cycle 1), stated explicitly so
        # future edits don't re-litigate: comp7 7b is SUBSTRING-TRAP-
        # frozen, NOT full-line-frozen. The frozen property is exactly
        # one: NO success-path log line below may contain the banned
        # 'systemctl start' literal (command invocations only). The
        # journalctl-hint tails edited onto the success lines this cycle
        # ('— logs: journalctl -u <unit> -f (unit journal) or <file>
        # (fallback file)') are LEGITIMATE under this contract — they
        # carry no banned substring and the comp7 7b pin asserts
        # assert_not_contains 'systemctl start' on the success output,
        # not line-by-line byte identity. The FAILURE path alone carries
        # the frozen 7c wording (see the guard below).]
        local _sc_isact _sc_skip_start=0
        _sc_isact="$("$SYSTEMCTL_BIN" is-active "$ENSEMBLE_RESTART_UNIT" 2>/dev/null || true)"
        if [ "$_sc_isact" = "active" ]; then
            if [ -n "${PORT:-}" ] && command -v lsof >/dev/null 2>&1; then
                if lsof -ti:"$PORT" >/dev/null 2>&1; then
                    _log "unit $ENSEMBLE_RESTART_UNIT already active and serving :$PORT — no unit start needed (a no-op's exit 0 is NOT success; verified port serving instead) — logs: journalctl -u $ENSEMBLE_RESTART_UNIT -f (unit journal) or $log (fallback file)"
                    return 0
                fi
                _warn "unit $ENSEMBLE_RESTART_UNIT already active but :$PORT NOT serving — skipping the no-op start; falling back (comp7 opt-in keeps the nohup fallback)"
                _sc_skip_start=1
            else
                _log "unit $ENSEMBLE_RESTART_UNIT already active (port ${PORT:-unknown} / lsof unavailable — not verifiable here; the caller's gate owns the deep checks) — logs: journalctl -u $ENSEMBLE_RESTART_UNIT -f (unit journal) or $log (fallback file)"
                return 0
            fi
        fi
        if [ "$_sc_skip_start" = "0" ]; then
            local _sc_errfile="/tmp/.ensemble-scerr.$$"
            if "$SYSTEMCTL_BIN" start "$ENSEMBLE_RESTART_UNIT" 2>"$_sc_errfile"; then
                rm -f "$_sc_errfile"
                _log "launcher started via systemd unit $ENSEMBLE_RESTART_UNIT (comp7: cgroup-bound; immune to setsid inheritance) — logs: journalctl -u $ENSEMBLE_RESTART_UNIT -f (unit journal) or $log (fallback file)"
                return 0
            fi
            local _sc_first
            _sc_first="$(head -n1 "$_sc_errfile" 2>/dev/null || true)"
            rm -f "$_sc_errfile"
            # [7b substring-trap guard: this is the FAILURE path — the
            # FROZEN comp7 7c wording (pinned by assert_contains in the
            # comp7 suite) lives ONLY here, never on a success line.]
            _warn "systemctl start $ENSEMBLE_RESTART_UNIT failed: ${_sc_first:-no stderr} — falling back to nohup launcher (comp7 opt-in path)"
        fi
    fi
    ( cd "$INSTALL_DIR" && nohup ./launcher.sh >> data/launcher.log 2>&1 & )
    _log "launcher started (nohup) — logs: $INSTALL_DIR/data/launcher.log"
}

# ── Supervision classification (ownership-mode commission P1, 2026-09-29) ────
#
# DETECTION + CLASSIFIER ONLY — zero behavior change to existing paths.
# Consumers: stop-path P2 LANDED 2026-09-29 (unit-aware stop + stop-site
# DUAL_FIGHT re-check via stop_via_stop_script + the ENSEMBLE_SUPERVISION_
# RESULT env handoff into scripts/stop-ensemble.sh's unit branch);
# hand-back P3 LANDED 2026-09-29 (restart_via_launcher consumes the
# classification to select the hand-back mode — unit / scope→unit
# self-heal / comp7-a′ / nohup); unit adoption P4 lands later. This
# section exposes the classifier, the preflight refusal seam
# (explicit-unit unresolved + DUAL_FIGHT fault) and the additive txn
# stamp.
#
# §0 MANDATORY (architect 2026-09-29, leader-ratified): the cgroup BASENAME
# of the owning pid's cgroup leaf (/proc/<pid>/cgroup) is the PRIMARY
# signal. INVOCATION_ID is CORROBORATION/DIAGNOSTIC ONLY — it is set for
# transient scopes too (systemd-run --scope mints one for every promote
# executor) and the executor env allowlist strips it, so an
# INVOCATION_ID-first gate would misclassify today's live survivor as
# SCRIPT. On disagreement: trust the cgroup, WARN-once.
#
#   leaf ends '.service'                      → UNIT_MANAGED (yields unit name)
#   leaf matches 'ensemble-upgrade-*.scope'   → SCOPE_SURVIVOR
#   'session-*' / user-slice / init scope     → SCRIPT_NOHUP
#
# TWINS CONTRACT: the ladder is implemented TWICE — this shell twin
# (pipeline-side: preflight + stop site) and _supervision_detect_real in
# daemon/tools/upgrade_journal.py (daemon-side: boot advisory). The ~6-line
# cgroup-leaf parse below is DELIBERATELY duplicated across the twins
# (cross-language seam — twins-pinned); the P5 twins-agree drift-guard test
# enforces agreement. Do not "DRY" them into one side.
#
# Env contract (TWO variables, deliberately NOT merged):
#   ENSEMBLE_SUPERVISION   mode: 'unit' | 'script' | 'auto' (default auto)
#   ENSEMBLE_RESTART_UNIT  unit name / comp7 opt-in — its ALREADY-SHIPPED
#                          semantics in restart_via_launcher are untouched.
#
# Resolution ladder (mirrors the ENSEMBLE_SELF_ENV shape, upgrade_tools
# :199-242): explicit unit/script wins → '0|false|no|off' = silent script
# opt-out → any other garbage → WARN-once → script (fail-toward-script) →
# 'auto'/unset → auto-derive chain: non-Linux → script (ZERO /proc + /run
# reads — the BSD/macOS arms stay byte-identical) → /run/systemd/system
# absent → script → cgroup basename decisive → unreadable cgroup →
# script + WARN-once.
#
# Unit-name resolution (§3 precedence, when a name is needed):
#   env ENSEMBLE_RESTART_UNIT > cgroup-derived (UNIT_MANAGED only) >
#   INSTALL_DIR/.env read directly (precedent: _resolve_wait_s in
#   scripts/stop-ensemble.sh:97-116 — deploy/Makefile invocations do not
#   export the staged env, so the file is the only reliable source).
#   All fail → WARN → script (or exit 78 at PREFLIGHT when mode was
#   explicitly 'unit' — never silent-degrade; the STOP SITE classifies
#   again but never refuses mid-pipeline).
#
#   TWINS DIVERGENCE (M1, review cycle 1 — pinned + documented, NOT
#   implemented in the python twin): the python twin
#   (_supervision_detect_real in daemon/tools/upgrade_journal.py)
#   resolves env > cgroup ONLY — NO .env rung, DELIBERATELY. The daemon
#   is launcher-started, and launcher.sh load_env_file EXPORTS every
#   .env key into the daemon process env, so a .env-sourced
#   ENSEMBLE_RESTART_UNIT already reaches the python env rung
#   transitively; a .env read in the daemon would need the install-dir
#   ladder re-implemented across the twins seam (cyclic import + ambient
#   live-install reads from dev-context tests). The .env rung — and the
#   exit-78 refusals — are PIPELINE-side only.
#
# ── Named deployment topologies + declared×verified outcome map (A1) ────
#
# TWO named topologies (Amendment #1, 2026-09-29):
#   SCRIPT MODE  — the operator runs the script; the launcher lineage
#                  self-respawns (crash backoff, ADR-011). Today's
#                  live/prod shape: direct start, no OS supervisor.
#   SERVICE MODE — an OS service owns monitoring/restart. systemd is THIS
#                  commission's substrate (ENSEMBLE_SUPERVISION=unit +
#                  ENSEMBLE_RESTART_UNIT); service×macOS = launchd =
#                  documented FUTURE scope.
#
# Deployment type is a FIRST-CLASS dimension alongside OS detection: the
# DECLARED mode (ENSEMBLE_SUPERVISION: unit|script|auto) and the
# classifier's VERIFIED state (UNIT_MANAGED / SCOPE_SURVIVOR /
# SCRIPT_NOHUP / DUAL_FIGHT) form one explicit matrix with NAMED outcome
# classes — conforming / degraded / fault (supervision_map_outcome
# below). A1 is BEHAVIOR-PRESERVING: every conforming/degraded/fault PATH
# predates this mapping and is unchanged (exit-78 preflights,
# WARN-degradeds, DUAL_FIGHT halts); A1 adds only naming, documentation
# and the mapping surface.
#
#   declared        verified                 outcome     (existing behavior named)
#   ─────────────── ──────────────────────── ──────────  ──────────────────────
#   script          SCRIPT_NOHUP             conforming  SCRIPT MODE healthy
#   script          UNIT_MANAGED             degraded    decl-mismatch; latent two-masters (UNREACHABLE: explicit script never verifies the cgroup)
#   script          SCOPE_SURVIVOR           degraded    WARN-once + self-heals at next promote with a unit configured
#   unit            UNIT_MANAGED (name set)  conforming  SERVICE MODE healthy
#   unit            UNIT_MANAGED (no name)   fault       exit-78 at preflight — never silent-degrade
#   unit            anything else            fault       never silent-degrade (DUAL_FIGHT arm halts loud)
#   auto            SCRIPT_NOHUP             conforming  auto defers to verification
#   auto            UNIT_MANAGED             conforming  auto defers to verification (P4 adoption signal)
#   auto            SCOPE_SURVIVOR           degraded    WARN-once + self-heals at next promote with a unit configured
#   any             DUAL_FIGHT               fault       halt-loud (two masters must never meet a flip)
#   unknown         anything                 fault       fail-closed
#
# RESOLVED-MODE EQUIVALENCE: supervision_classify resolves 'auto' INTO
# 'unit'|'script' before returning ($SUPERVISION_MODE = the declaration
# POST-ladder), and the resolved-mode cells agree with the declared cells
# above row-for-row — runtime callers pass $SUPERVISION_MODE and the
# twins agree cell-for-cell either way.
#
# OS×deployment matrix — canonical home: docs/runbooks/systemd-adoption.md
# (lands in P4; the forward reference is INTENTIONAL). Rows: script×macOS
# = byte-identical no-systemd arm; script×ubuntu-no-systemd = same arm;
# script×ubuntu-systemd-present-not-adopted = TODAY's live topology;
# service×ubuntu = systemd substrate; service×macOS = FUTURE scope (launchd).
#
# Surfaced outcome name (additive only): the supervision_preflight machine
# line ENSEMBLE_SUPERVISION_OUTCOME=<outcome> (SEPARATE line — the
# ENSEMBLE_SUPERVISION_RESULT=<state>[:<unit>] grammar is FROZEN, P2
# consumes it) + the in_flight journal stamp field "outcome"
# (journal_mark_supervision) + the daemon boot-advisory detail
# (upgrade_journal_sweep.py, python twin).

SUPERVISION_WARN_DONE=0
_supervision_warn_once() {
    [ "$SUPERVISION_WARN_DONE" -eq 0 ] || return 0
    SUPERVISION_WARN_DONE=1
    _warn "supervision: $*"
}

# ERE-escape an install dir for pgrep patterns (stop-ensemble.sh :146-149
# mirror — pinned with the tier mirror below).
_supervision_ere_escape() {
    printf '%s' "$1" | sed -e 's/[][\.*^$()+?{}|\\]/\\&/g'
}

# _supervision_owned_pids — pid discovery MIRRORING the anchored ownership
# tiers of scripts/stop-ensemble.sh (_list_candidates + _classify, :157-226
# — the design cites the tier contract at :11-25). Pinned mirror, not a
# shared helper: stop-ensemble.sh is an executable entry script (sourcing
# it here would run its stop machinery), and D6's "reused, never
# duplicated" rule governs the STOP ACTION, which still goes through
# stop_via_stop_script. Tier 1a: anchored executable paths. Tier 1b:
# ensemble-shaped process whose cwd IS the install dir (logical OR
# physical — lsof reports physical). Deduped, ascending, one per line.
_supervision_owned_pids() {
    local esc phys esc_phys
    esc="$(_supervision_ere_escape "$INSTALL_DIR")"
    phys="$(cd -P "$INSTALL_DIR" 2>/dev/null && pwd || printf '%s' "$INSTALL_DIR")"
    esc_phys="$(_supervision_ere_escape "$phys")"
    {
        pgrep -f "${esc}/launcher\.sh( |$)" 2>/dev/null
        pgrep -f "${esc}/ensemble-prod( |$)" 2>/dev/null
        pgrep -f "${esc}/current/ensemble-prod( |$)" 2>/dev/null
        ps -axo pid=,comm=,args= 2>/dev/null \
            | grep -E '[e]nsemble-prod|[l]auncher\.sh' \
            | awk '{
                pid=$1; comm=$2; $1=""; $2=""; args=$0; sub(/^  */, "", args)
                if (args ~ /(ensemble-prod)( |$)/ || comm ~ /ensemble-prod/ || \
                    args ~ /(^|\/)launcher\.sh( |$)/)
                    print pid}'
    } 2>/dev/null | sort -n | awk '!seen[$0]++' \
      | while read -r pid; do
            [ -n "$pid" ] || continue
            # Tier-1b candidates without an anchored path need the cwd
            # ownership check (foreign installs / dev daemons / editors
            # must never be probed). Anchored-path pids (tier 1a) pass.
            if ! ps -o args= -p "$pid" 2>/dev/null | grep -Eq \
                "(${esc}|${esc_phys})/(current/)?ensemble-prod( |$)|${esc}/launcher\.sh( |$)"; then
                cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
                [ "$cwd" = "$INSTALL_DIR" ] || [ "$cwd" = "$phys" ] || continue
            fi
            printf '%s\n' "$pid"
        done
}

# _supervision_pid_cgroup_leaf <pid> — basename of the pid's cgroup LEAF,
# or nothing. TWINS-PINNED parse (~6 lines, duplicated in the python twin
# _supervision_read_cgroup_leaf — cross-language seam; see the section
# comment). EVERY /proc read is behind [ -r ... ] per the P1 contract.
_supervision_pid_cgroup_leaf() {
    local pid="$1" line path leaf
    [ -n "$pid" ] || return 1
    [ -r "/proc/$pid/cgroup" ] || return 1
    line="$(tail -n 1 "/proc/$pid/cgroup" 2>/dev/null)" || return 1
    [ -n "$line" ] || return 1
    path="${line##*:}"
    leaf="${path##*/}"
    [ -n "$leaf" ] || return 1
    printf '%s\n' "$leaf"
}

# _supervision_unit_from_dotenv — read ENSEMBLE_RESTART_UNIT directly from
# $INSTALL_DIR/.env (§3 step 3; _resolve_wait_s precedent: sed-extract,
# strip optional quotes, validate non-empty).
_supervision_unit_from_dotenv() {
    local raw env_file="${INSTALL_DIR:-}/.env"
    [ -n "${INSTALL_DIR:-}" ] || return 1
    raw="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}ENSEMBLE_RESTART_UNIT[[:space:]]*=[[:space:]]*//p' "$env_file" 2>/dev/null | head -1)"
    raw="${raw%$'\r'}"
    case "$raw" in
        \"*\") raw="${raw#\"}"; raw="${raw%\"}" ;;
        \'*\') raw="${raw#\'}"; raw="${raw%\'}" ;;
    esac
    [ -n "$raw" ] || return 1
    printf '%s\n' "$raw"
}

# _supervision_derive_unit_name — LAST-resort unit-name derivation for the
# nohup-survivor heal in supervision_classify (supervisor war r3, 2026-09-30):
# PINNED MIRROR of adopt-unit.sh _derive_unit_name (the header naming rule:
# ensemble-<slug>.service, slug = basename(INSTALL_DIR) minus a leading
# "agents-"/"ensemble-", sanitized to [0-9A-Za-z@._-]; empty or bare
# "ensemble" slug → "main" — ~/agents-ensemble → ensemble-main.service,
# ~/agents-ensemble-demo → ensemble-demo.service). Mirrored, not sourced:
# adopt-unit.sh is an entry script (sourcing it runs the adoption
# machinery); same pin discipline as _supervision_owned_pids (the
# stop-ensemble.sh tier mirror). Deliberately NOT the nonexistent
# 'ensemble-${TARGET}.service' template (supervisor war r3 review: on
# ensemble-vm that would adopt the RETIRED ensemble-live.service lane
# while INSTALL_DIR/.env pins ensemble-main.service).
_supervision_derive_unit_name() {
    local base
    base="$(basename "${INSTALL_DIR:-}")"
    case "$base" in agents-*) base="${base#agents-}" ;; esac
    # avoid a double prefix: agents-ensemble-demo → ensemble-demo (not
    # ensemble-ensemble-demo); a BARE ensemble dir falls to "main" below.
    case "$base" in ensemble-*) base="${base#ensemble-}" ;; esac
    base="$(printf '%s' "$base" | sed -e 's/[^0-9A-Za-z@._-]/-/g' -e 's/^[-.]*//' -e 's/[-.]*$//')"
    if [ -z "$base" ] || [ "$base" = "ensemble" ]; then
        base="main"
    fi
    printf 'ensemble-%s.service' "$base"
}

# supervision_classify — THE classifier. Uncached per-run (state may change
# across a run: the daemon stops mid-promote). Sets globals:
#   SUPERVISION_MODE   resolved mode: 'unit' | 'script'
#   SUPERVISION_STATE  SCRIPT_NOHUP | UNIT_MANAGED | SCOPE_SURVIVOR
#   SUPERVISION_UNIT   unit name ('' when none)
#   SUPERVISION_EXPLICIT_UNIT   1 ONLY for a ladder-top EXPLICIT 'unit'
#                       declaration (M3 discriminator — auto-derived
#                       UNIT_MANAGED also resolves MODE=unit)
#   SUPERVISION_REFUSE_REASON   '' | 'unit-unresolved' |
#                       'unit-incapable-host' (M3; the preflight branches
#                       its exit-78 _refuse token on this)
# Prints EXACTLY ONE machine-readable line:
#   ENSEMBLE_SUPERVISION_RESULT=<state>[:<unit>]
# Returns 1 for the two EXPLICIT-unit fail-closed classes (the PREFLIGHT
# caller refuses with exit 78; the stop site ignores rc — it never refuses
# mid-pipeline). SUPERVISION_REFUSE_REASON discriminates them:
#   "unit-unresolved"     — explicit unit, no name resolvable (env >
#                           cgroup > INSTALL_DIR/.env all empty)
#   "unit-incapable-host" — explicit unit, this host CANNOT honor unit
#                           supervision at all (non-Linux / no systemd /
#                           systemctl unresolvable) — M3 (review cycle 1,
#                           leader-ruled FAIL-CLOSED): silently degrading
#                           an explicit assertion to the nohup lineage at
#                           rc 0 is the r-f82e surprise class; the name
#                           (if one resolved from env/.env) is carried for
#                           the refusal diagnosis, never acted on.
# Sets one additional consumed global: SUPERVISION_EXPLICIT_UNIT (1 only
# when the ladder top saw an EXPLICIT 'unit' declaration; 0 otherwise —
# an auto-derived UNIT_MANAGED leaf also resolves MODE=unit, so MODE alone
# cannot discriminate the declared shape). restart_via_launcher's M3
# refusal arm keys on it.
supervision_classify() {
    SUPERVISION_MODE="script"
    SUPERVISION_STATE="SCRIPT_NOHUP"
    SUPERVISION_UNIT=""
    SUPERVISION_EXPLICIT_UNIT=0
    SUPERVISION_REFUSE_REASON=""
    local raw low leaf pid inv heal_unit heal_on
    heal_on=0
    raw="${ENSEMBLE_SUPERVISION:-}"
    raw="$(printf '%s' "$raw" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    low="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"

    # ── explicit / opt-out / garbage (ladder top) ────────────────────────
    if [ -n "$low" ]; then
        case "$low" in
            0|false|no|off)
                printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                return 0
                ;;
            script)
                printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                return 0
                ;;
            unit)
                SUPERVISION_MODE="unit"
                SUPERVISION_STATE="UNIT_MANAGED"
                SUPERVISION_EXPLICIT_UNIT=1
                SUPERVISION_UNIT="${ENSEMBLE_RESTART_UNIT:-}"
                if [ -z "$SUPERVISION_UNIT" ] && [ "$(uname -s)" = "Linux" ] \
                   && [ -d /run/systemd/system ]; then
                    pid="$(_supervision_owned_pids | head -1)"
                    if [ -n "$pid" ]; then
                        leaf="$(_supervision_pid_cgroup_leaf "$pid")"
                        case "$leaf" in *.service) SUPERVISION_UNIT="$leaf" ;; esac
                    fi
                fi
                if [ -z "$SUPERVISION_UNIT" ]; then
                    SUPERVISION_UNIT="$(_supervision_unit_from_dotenv)" \
                        || SUPERVISION_UNIT=""
                fi
                # ── M3 host-capability rung (review cycle 1, leader-ruled
                # FAIL-CLOSED): an EXPLICIT unit assertion on a host that
                # cannot honor unit supervision (non-Linux / no systemd /
                # systemctl unresolvable) refuses at preflight (78) — NEVER
                # loud-degrades to the nohup lineage at rc 0 (silently
                # degrading an explicit assertion is the r-f82e surprise
                # class). The resolved name (env/.env), if any, is carried
                # in the machine line + globals for the refusal diagnosis.
                # Non-explicit (auto/script) modes never reach this rung.
                # Zero /proc reads on incapable hosts (the cgroup rung
                # above is already uname-guarded).
                if ! _supervision_host_allows_unit || ! command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1; then
                    _supervision_warn_once "mode 'unit' explicit but this host cannot honor unit supervision (non-Linux / no systemd / no resolvable systemctl${SUPERVISION_UNIT:+; name '$SUPERVISION_UNIT' from env/INSTALL_DIR/.env is unusable here}) — preflight must refuse (78); never silent-degrade to nohup"
                    printf 'ENSEMBLE_SUPERVISION_RESULT=%s%s\n' "$SUPERVISION_STATE" "${SUPERVISION_UNIT:+:$SUPERVISION_UNIT}"
                    SUPERVISION_REFUSE_REASON="unit-incapable-host"
                    return 1
                fi
                if [ -n "$SUPERVISION_UNIT" ]; then
                    printf 'ENSEMBLE_SUPERVISION_RESULT=%s:%s\n' "$SUPERVISION_STATE" "$SUPERVISION_UNIT"
                    return 0
                fi
                # explicit unit, no resolvable name — never silent-degrade;
                # the preflight caller turns rc 1 into exit 78.
                _supervision_warn_once "mode 'unit' explicit but no unit name resolvable (env ENSEMBLE_RESTART_UNIT unset, cgroup not unit-managed, INSTALL_DIR/.env has none) — preflight must refuse (78)"
                printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                return 1
                ;;
            auto)
                ;;
            *)
                _supervision_warn_once "garbage ENSEMBLE_SUPERVISION='$raw' — WARN-once, failing toward script"
                printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
                return 0
                ;;
        esac
    fi

    # ── auto-derive chain (ZERO /proc + /run reads before the guards) ────
    if [ "$(uname -s)" != "Linux" ]; then
        printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
        return 0
    fi
    if ! [ -d /run/systemd/system ]; then
        printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
        return 0
    fi
    pid="$(_supervision_owned_pids | head -1)"
    if [ -z "$pid" ]; then
        _supervision_warn_once "no owned pid discovered (anchored tiers vs INSTALL_DIR=$INSTALL_DIR) — classifying script"
        printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
        return 0
    fi
    leaf="$(_supervision_pid_cgroup_leaf "$pid")"
    if [ -z "$leaf" ]; then
        _supervision_warn_once "cgroup unreadable for owned pid $pid — classifying script"
        printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
        return 0
    fi
    case "$leaf" in
        *.service)
            SUPERVISION_STATE="UNIT_MANAGED"
            SUPERVISION_MODE="unit"
            SUPERVISION_UNIT="$leaf"
            ;;
        ensemble-upgrade-*.scope)
            SUPERVISION_STATE="SCOPE_SURVIVOR"
            ;;
        session-*.scope|init.scope|user.slice|*.user.slice|user-*.slice|*.user-*.slice|machine.slice|*.machine.slice)
            SUPERVISION_STATE="SCRIPT_NOHUP"
            ;;
        *)
            # Unknown leaf shape — fail toward script, no unit name.
            SUPERVISION_STATE="SCRIPT_NOHUP"
            ;;
    esac

    # ── INVOCATION_ID corroboration (diagnostic ONLY — §0) — read the
    # OWNING pid's environ (same-uid readable; [ -r ] guarded like every
    # other /proc read), not our own: the script's INVOCATION_ID describes
    # the script (a scope executor mints one), not the daemon.
    inv=""
    if [ -r "/proc/$pid/environ" ]; then
        inv="$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^INVOCATION_ID=//p' | head -1)"
    fi
    if [ -n "$inv" ] && [ "$SUPERVISION_STATE" != "UNIT_MANAGED" ]; then
        _supervision_warn_once "owned pid $pid has INVOCATION_ID but cgroup leaf '$leaf' classifies $SUPERVISION_STATE — trusting cgroup (§0: transient scopes mint INVOCATION_ID too)"
    elif [ -z "$inv" ] && [ "$SUPERVISION_STATE" = "UNIT_MANAGED" ]; then
        _supervision_warn_once "UNIT_MANAGED (leaf '$leaf') but owned pid $pid has no INVOCATION_ID — trusting cgroup (§0 corroboration only)"
    fi

    # ── nohup-survivor heal (supervisor war r3, 2026-09-30) ─────────────
    # A nohup survivor from an old-version promote (cgroup session-*.scope
    # / a user or machine slice / an unknown leaf) kept EVERY later promote
    # on the nohup spawn path: the v0.16.5 unit hand-back was compiled in
    # but never reached, because THIS classifier trusted the survivor's
    # cgroup (live 139 restarts ~5.5h; demo 2,561 restarts 43h). Heal:
    # when the outcome would be SCRIPT_NOHUP but a systemd unit FILE
    # exists for this install, adopt UNIT_MANAGED so the stop/hand-back
    # paths treat the install as unit-managed — the FIRST promote running
    # this code hands the lineage back to systemd regardless of the
    # current daemon's cgroup. Guards UNCHANGED and still downstream:
    # supervision_dualfight_check still halts two LIVE masters, and the
    # unit-path stop still fails loud when a foreign pid holds the port —
    # the heal NEVER bypasses them (fail-closed everywhere). Name
    # resolution: INSTALL_DIR/.env pin FIRST (_supervision_unit_from_dotenv
    # — the per-install record adopt-unit.sh stages; the pin must conform
    # to the ensemble-*.service shape or it is treated as ABSENT —
    # finding 4, r3 hardening), then the adopt-unit naming-convention
    # derivation; the -f file gate applies to the
    # RESOLVED name — file absent → NO heal (SCRIPT_NOHUP stands).
    # COLD-STATE BYPASS IS DELIBERATE (finding 3, r3 hardening): the
    # auto-derive early returns above (no owned pid / unreadable cgroup)
    # skip the heal BY DESIGN — those arms describe a cold or
    # unobservable install, not a nohup survivor; adopting from them is a
    # separate commission if ever wanted. Host
    # guards: the auto-derive chain above already returned on non-Linux /
    # no-/run/systemd/system shapes (BSD/macOS arms byte-identical;
    # requirement: unchanged SCRIPT_NOHUP), and _supervision_host_allows_
    # unit re-asserts both defensively. EXPLICIT script/opt-out
    # declarations return at the ladder top — the heal can never override
    # an operator opt-out. SUPERVISION_EXPLICIT_UNIT stays 0 (auto-derived
    # heal, not a ladder-top declaration — restart_via_launcher's M3 arm
    # keys on it). SCOPE_SURVIVOR is untouched: it already carries its own
    # hand-back (the comp7 opt-in path).
    if [ "$SUPERVISION_STATE" = "SCRIPT_NOHUP" ] && _supervision_host_allows_unit; then
        heal_unit="$(_supervision_unit_from_dotenv)" || heal_unit=""
        case "$heal_unit" in
            ensemble-*.service) ;;
            # finding 4: a non-conforming .env pin is treated as ABSENT
            # (falls through to the derivation rung) — the heal never
            # adopts a name outside the ensemble-*.service family the
            # adopt-unit polkit pattern governs.
            *) heal_unit="" ;;
        esac
        if [ -z "$heal_unit" ]; then
            heal_unit="$(_supervision_derive_unit_name)"
        fi
        if [ -n "$heal_unit" ] && [ -f "$SUPERVISION_UNIT_DIR/$heal_unit" ]; then
            SUPERVISION_STATE="UNIT_MANAGED"
            SUPERVISION_MODE="unit"
            SUPERVISION_UNIT="$heal_unit"
            heal_on=1
            _log "supervision: unit file exists for $heal_unit at $SUPERVISION_UNIT_DIR — adopting UNIT_MANAGED (healing from nohup-survivor state; cgroup leaf '$leaf' classified SCRIPT_NOHUP)"
        fi
    fi

    if [ "$SUPERVISION_STATE" = "UNIT_MANAGED" ] && [ -n "$SUPERVISION_UNIT" ]; then
        # §3: env-explicit unit name OVERRIDES the cgroup-derived one —
        # NATIVE path only. POST-HEAL (finding 1, r3 hardening): the
        # ambient ENSEMBLE_RESTART_UNIT override is WITHHELD — the healed
        # resolution (.env pin > adopt-unit derivation, both already -f
        # gated + conformance-checked) outranks the operator shell.
        # Rationale: file-existence + ensemble-*.service conformance
        # gating alone cannot reject a PRESENT-FILE stale lane
        # (ensemble-vm's retired ensemble-live.service still carries its
        # unit file), so any post-heal env application re-opens the
        # silent stale-lane adoption the heal exists to close. Divergence
        # logged loud so the operator sees their ambient pin was not
        # honored.
        if [ -n "${ENSEMBLE_RESTART_UNIT:-}" ]; then
            if [ "$heal_on" = "1" ]; then
                if [ "$ENSEMBLE_RESTART_UNIT" != "$SUPERVISION_UNIT" ]; then
                    _log "supervision: keeping healed unit $SUPERVISION_UNIT — ambient ENSEMBLE_RESTART_UNIT='$ENSEMBLE_RESTART_UNIT' NOT applied post-heal (stale-lane guard; the override needs no-heal provenance + ensemble-*.service conformance + a $SUPERVISION_UNIT_DIR file)"
                fi
            else
                SUPERVISION_UNIT="$ENSEMBLE_RESTART_UNIT"
            fi
        fi
        printf 'ENSEMBLE_SUPERVISION_RESULT=%s:%s\n' "$SUPERVISION_STATE" "$SUPERVISION_UNIT"
        return 0
    fi
    printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
    return 0
}

# supervision_map_outcome <declared> <verified> [<unit>] — the NAMED
# declared×verified mapping (Amendment #1 delta A1, 2026-09-29). Echoes
# ONE line '<outcome>|<short reason>' (outcome = conforming | degraded |
# fault; reason = twins-pinned prose, never contains '|'); ALWAYS returns
# 0 — this is a pure NAMING function, not a gate: it never refuses,
# warns, or journals. The runtime paths it names (exit-78 preflight
# refusal, WARN-once degradeds, DUAL_FIGHT halt) predate A1 and are
# untouched.
#
#   $1 declared — unit | script | auto (ENSEMBLE_SUPERVISION vocabulary;
#                 runtime callers pass $SUPERVISION_MODE — RESOLVED-MODE
#                 EQUIVALENCE, section comment)
#   $2 verified — SCRIPT_NOHUP | UNIT_MANAGED | SCOPE_SURVIVOR |
#                 DUAL_FIGHT (classifier / dualfight vocabulary)
#   $3 unit     — optional resolved unit name; ONLY consulted for
#                 declared=unit × verified=UNIT_MANAGED, where EMPTY
#                 names the reachable exit-78 cell (explicit unit,
#                 nothing resolvable) → fault.
#
# Cell-for-cell table: see the section comment above. TWINS-PINNED: the
# python twin supervision_outcome (daemon/tools/upgrade_journal.py)
# implements the IDENTICAL table — outcome names AND reason strings
# byte-identical; the P5 twins-agree drift-guard test pins this. Do not
# "DRY" them into one side.
supervision_map_outcome() {
    local declared="${1:-}" verified="${2:-}" unit="${3:-}" low
    low="$(printf '%s' "$declared" | tr '[:upper:]' '[:lower:]')"

    if [ "$verified" = "DUAL_FIGHT" ]; then
        printf 'fault|two masters live (unit active/armed while owned pids or port-holder sit outside it) — halt-loud\n'
        return 0
    fi
    case "$low" in
        script)
            case "$verified" in
                SCRIPT_NOHUP)
                    printf 'conforming|script topology declared and verified (SCRIPT MODE — launcher lineage self-respawns)\n' ;;
                UNIT_MANAGED)
                    printf 'degraded|declaration mismatch: script declared but unit-managed reality — latent two-masters hazard (unreachable today: explicit script never verifies the cgroup)\n' ;;
                SCOPE_SURVIVOR)
                    printf 'degraded|scope survivor under script declaration: WARN-once + self-heals at next promote with a unit configured\n' ;;
                *) printf 'fault|unknown declared mode or verified state — fail-closed\n' ;;
            esac
            ;;
        unit)
            case "$verified" in
                UNIT_MANAGED)
                    if [ -n "$unit" ]; then
                        printf 'conforming|service topology declared and verified, unit name resolved (SERVICE MODE)\n'
                    else
                        printf 'fault|unit declared but no unit name resolvable — preflight refuses (exit 78), never silent-degrade\n'
                    fi
                    ;;
                SCRIPT_NOHUP)
                    printf 'fault|declaration mismatch: unit declared but script reality — never silent-degrade (unreachable today: explicit unit never verifies the cgroup)\n' ;;
                SCOPE_SURVIVOR)
                    printf 'fault|declaration mismatch: unit declared but scope-survivor reality — never silent-degrade (unreachable today)\n' ;;
                *) printf 'fault|unknown declared mode or verified state — fail-closed\n' ;;
            esac
            ;;
        auto)
            case "$verified" in
                SCRIPT_NOHUP)
                    printf 'conforming|auto defers to verification: script topology confirmed (today'"'"'s nohup direct/live-prod shape)\n' ;;
                UNIT_MANAGED)
                    printf 'conforming|auto defers to verification: unit topology confirmed (P4 adoption signal)\n' ;;
                SCOPE_SURVIVOR)
                    printf 'degraded|scope survivor under auto declaration: WARN-once + self-heals at next promote with a unit configured\n' ;;
                *) printf 'fault|unknown declared mode or verified state — fail-closed\n' ;;
            esac
            ;;
        *) printf 'fault|unknown declared mode or verified state — fail-closed\n' ;;
    esac
    return 0
}

# supervision_dualfight_check — §6 FAULT detector (call before any journal
# txn / stop / flip mutation): when the resolved unit is active or
# auto-restart-armed AND reality disagrees (owned pids NOT inside the
# unit's cgroup, OR the port-holder outside the unit's MainPID lineage)
# → halt journal event + exit 78. Two masters must never meet a flip.
# Call sites: PREFLIGHT via supervision_preflight (P1), plus the P2
# stop-site re-check in stop_via_stop_script (still pre-stop-mutation —
# the state may drift between preflight and the stop).
# MainPID lineage is tested as cgroup containment within the unit's
# ControlGroup. The unit's KillMode=mixed (scripts/systemd/ensemble-
# daemon.service:85) TERMs only the launcher (the cgroup root) — cgroup
# siblings/nested children outlive a stop until SIGKILL escalation.
# That is precisely why a cgroup-substring containment check is the
# canonical lineage signal here: PPID-walking breaks on double-forked
# daemons (the launcher's child renames itself to the daemon pid), so
# we consult /proc/<pid>/cgroup instead.
# Containment test note: the `case "$pcg" in *"$cg"*` match is an
# UNANCHORED substring test; sibling or nested cgroups that share a
# prefix with $cg (e.g. a transient scope nested under the unit's
# slice) may over-include. Deliberately conservative — diagnostic-
# only — the FAULT arm only fires when reality disagrees.
# Linux+systemd only; every other shape returns 0 (zero behavior change).
supervision_dualfight_check() {
    [ "${SUPERVISION_MODE:-}" = "unit" ] || return 0
    [ -n "${SUPERVISION_UNIT:-}" ] || return 0
    [ "$(uname -s)" = "Linux" ] || return 0
    [ -d /run/systemd/system ] || return 0
    local unit="${SUPERVISION_UNIT:-}" is_active restart main_pid cg pid pcg hpid fault=""
    is_active="$("$SYSTEMCTL_BIN" is-active "$unit" 2>/dev/null || true)"
    restart="$("$SYSTEMCTL_BIN" show "$unit" -p Restart --value 2>/dev/null || true)"
    if [ "$is_active" != "active" ] \
       && [ "$restart" != "always" ] && [ "$restart" != "on-failure" ]; then
        return 0  # unit dormant and unarmed — no fight possible
    fi
    main_pid="$("$SYSTEMCTL_BIN" show "$unit" -p MainPID --value 2>/dev/null || true)"
    cg="$("$SYSTEMCTL_BIN" show "$unit" -p ControlGroup --value 2>/dev/null || true)"
    if [ -z "$cg" ] || [ "$cg" = "/" ]; then
        _supervision_warn_once "unit $unit active/armed but ControlGroup unobservable — skipping DUAL_FIGHT pid checks (diagnostic only)"
        return 0
    fi
    # Check A: every owned pid must sit inside the unit's cgroup. The
    # subshell pipe captures the fault marker (the while-loop runs in a
    # subshell; the temp file carries the verdict out).
    local df_tmp
    df_tmp="$(mktemp /tmp/.ensemble-df.XXXXXX)" || return 0
    _supervision_owned_pids | while read -r pid; do
        [ -n "$pid" ] || continue
        pcg=""
        if [ -r "/proc/$pid/cgroup" ]; then
            pcg="$(tail -n 1 "/proc/$pid/cgroup" 2>/dev/null)"
            pcg="${pcg##*:}"
        fi
        case "$pcg" in
            *"$cg"*) ;;
            *)
                _warn "supervision DUAL_FIGHT: owned pid $pid cgroup '${pcg:-<unreadable>}' is OUTSIDE unit $unit cgroup $cg — two masters"
                echo "__DUALFIGHT_FAULT__"
                ;;
        esac
    done > "$df_tmp" 2>/dev/null
    if grep -q '__DUALFIGHT_FAULT__' "$df_tmp" 2>/dev/null; then
        fault="owned-pids-outside-unit-cgroup"
    fi
    rm -f "$df_tmp" 2>/dev/null
    # Check B: the port-holder must be the unit's MainPID or inside its
    # cgroup (report-only port lookup, stop-ensemble.sh precedent — lsof
    # absent → skip the check, never fault on a missing tool).
    if [ -z "$fault" ] && [ -n "${PORT:-}" ] && command -v lsof >/dev/null 2>&1; then
        for hpid in $(lsof -ti:"$PORT" 2>/dev/null); do
            [ -n "$hpid" ] || continue
            [ "$hpid" = "$main_pid" ] && continue
            pcg=""
            if [ -r "/proc/$hpid/cgroup" ]; then
                pcg="$(tail -n 1 "/proc/$hpid/cgroup" 2>/dev/null)"
                pcg="${pcg##*:}"
            fi
            case "$pcg" in
                *"$cg"*) ;;
                *)
                    _warn "supervision DUAL_FIGHT: port $PORT holder $hpid is outside unit $unit lineage (MainPID=${main_pid:-?} cgroup='${pcg:-<unreadable>}') — two masters"
                    fault="port-holder-outside-unit-lineage"
                    ;;
            esac
        done
    fi
    if [ -n "$fault" ]; then
        journal_history_append halt "supervision DUAL_FIGHT ($fault): unit $unit is active/auto-restart while owned pids or the port-holder sit OUTSIDE it — refusing PRE-TXN (two masters must never meet a flip; halt-for-human, resolve the supervision split before any pipeline mutation)" >/dev/null 2>&1 \
            || _warn "DUAL_FIGHT halt journal append FAILED (best-effort) — proceeding to exit 78"
        # Direct invocation only — supervision_dualfight_check must be called
        # directly (statement form), NEVER wrapped in `$( ... )` or any other
        # subshell. The exit 78 below must kill the calling pipeline script;
        # a subshell swallows the exit and the refusal silently degrades to
        # the caller's rc, which is exactly the r-f82e surprise class the
        # DUAL_FIGHT halt exists to prevent.
        exit 78
    fi
    return 0
}

# supervision_preflight — one call per action script's PREFLIGHT: classify
# (emits the machine line), refuse 78 on explicit-unit-unresolved, then the
# §6 DUAL_FIGHT fault check. PRE-TXN by call-site contract (promote/rollback
# call it before journal_open_txn; restart calls it after lock adoption,
# before its stop phase — the restart txn was armed by the tool earlier and
# the executor is its completion, not its opener).
# A1 (2026-09-29): also emits the NAMED declared×verified outcome as ONE
# SEPARATE additive machine line ENSEMBLE_SUPERVISION_OUTCOME=<outcome>
# (conforming|degraded|fault) — a DIFFERENT variable name on its own line;
# the ENSEMBLE_SUPERVISION_RESULT=<state>[:<unit>] grammar is FROZEN (P2
# consumes it — never extend that line). Emitted AFTER the DUAL_FIGHT
# check so a fault run (exit 78 inside the check) never prints a stale
# 'conforming' — DUAL_FIGHT's named surface is the halt event itself.
# The reason half of supervision_map_outcome is human prose and is
# deliberately NOT machine-surfaced.
supervision_preflight() {
    if ! supervision_classify; then
        if [ "${SUPERVISION_REFUSE_REASON:-}" = "unit-incapable-host" ]; then
            # M3 (review cycle 1, leader-ruled FAIL-CLOSED): explicit unit
            # on a unit-INCAPABLE host — same exit-78 preflight refusal
            # semantics as supervision-unit-unresolved, its own greppable
            # reason token. NEVER loud-degrade + nohup at rc 0.
            _refuse supervision-unit-incapable-host "preflight refused: ENSEMBLE_SUPERVISION=unit is explicit but this host cannot honor unit supervision (non-Linux / no systemd / systemctl unresolvable) — never silent-degrade to the nohup lineage (the r-f82e surprise class); run under systemd or declare ENSEMBLE_SUPERVISION=script"
        fi
        _refuse supervision-unit-unresolved "preflight refused: ENSEMBLE_SUPERVISION=unit is explicit but no unit name is resolvable (env ENSEMBLE_RESTART_UNIT > cgroup-derived > INSTALL_DIR/.env all empty) — never silent-degrade (P1 §2); set ENSEMBLE_RESTART_UNIT or run under the unit"
    fi
    supervision_dualfight_check
    SUPERVISION_OUTCOME="$(supervision_map_outcome "${SUPERVISION_MODE:-}" "${SUPERVISION_STATE:-}" "${SUPERVISION_UNIT:-}")"
    printf 'ENSEMBLE_SUPERVISION_OUTCOME=%s\n' "${SUPERVISION_OUTCOME%%|*}"
}

# journal_mark_supervision — stamp supervision=<state> / unit=<name|null>
# ADDITIVELY on the open in_flight txn (D4 splice discipline: additive-only
# textual splice; existing stamp lines and journal whitespace byte-preserved
# — journal JSON whitespace differs between launcher and lib.sh by design,
# assert semantically, never raw-spacing). Mirrors journal_mark_f2_verified.
# Reads the globals set by the last supervision_classify in this process.
# A1 (2026-09-29): also stamps outcome=<conforming|degraded|fault> — the
# NAMED declared×verified cell from supervision_map_outcome (additive field
# only; existing readers parse named fields and ignore extras). Computed
# from the resolved-mode globals — RESOLVED-MODE EQUIVALENCE (section
# comment) makes that cell-for-cell identical to the declared-mode cell.
journal_mark_supervision() {
    local json inf new_inf unit_json outcome
    json="$(journal_read)" || return 1
    inf="$(_json_sub "$json" in_flight)"
    case "$inf" in
        '{'*) ;;
        ''|null)
            _warn "journal_mark_supervision: no in_flight txn (in_flight null/absent) — refusing to stamp (journal untouched)"
            return 1
            ;;
        *)
            _warn "journal_mark_supervision: in_flight read is null/malformed (not a JSON object: '${inf:0:40}') — refusing to stamp (journal untouched)"
            return 1
            ;;
    esac
    if [ -n "${SUPERVISION_UNIT:-}" ]; then
        unit_json="\"$(_json_escape "$SUPERVISION_UNIT")\""
    else
        unit_json="null"
    fi
    outcome="$(supervision_map_outcome "${SUPERVISION_MODE:-}" "${SUPERVISION_STATE:-unknown}" "${SUPERVISION_UNIT:-}")"
    outcome="${outcome%%|*}"
    new_inf="${inf%\}}"
    new_inf="${new_inf},\"supervision\":\"$(_json_escape "${SUPERVISION_STATE:-unknown}")\",\"unit\":$unit_json,\"outcome\":\"$outcome\"}"
    journal_update "in_flight" "$new_inf"
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
# _refuse <reason-token> <message...> — entry-refusal: WARN the message
# UNCHANGED, best-effort-append a `refusal` journal history event carrying
# the D-FA2.2 reason=<token> (activates the dormant upgrade_promote_refusal
# SSE kind, P2.3 B4/T8a), then exit 78. Best-effort ONLY: an absent/torn/
# unwritable journal WARNs once and the refusal exit proceeds — NEVER
# blocks, NEVER alters the exit code, and NEVER acquires the pipeline lock
# (an in-flight txn's lock is sacred; callers hold it already or accept a
# dropped event). ADR-034: the append rides the plain journal_history_append
# splice — additive only, no new write mechanics. The lock-busy refusal
# sites deliberately do NOT journal (unlocked append would race the live
# lock-holder's read-modify-write; pipeline-busy is structured-logged).
_refuse() {
    local reason="$1"; shift
    # L10 (tidier, P2.3 final batch): a token-less or message-less refusal is
    # a caller bug — refuse LOUDLY (warn + exit 78) WITHOUT journaling an
    # empty reason=()/detail record (the taxonomy stays greppable).
    if [ -z "${reason:-}" ] || [ -z "$*" ]; then
        _warn "refusal helper MISUSED: _refuse requires a non-empty reason token AND message (got reason='${reason:-}' msg='$*') — refusing without journaling"
        exit 78
    fi
    # Include the reason token in the WARN line too (not just the journal)
    # so the operator can grep stderr for the taxonomy without a journal
    # read. The journal still gets the full detail for audit; this is a
    # parallel channel, not a replacement.
    _warn "$* (reason=$reason)"
    journal_history_append refusal "$* (reason=$reason)" >/dev/null 2>&1 \
        || _warn "refusal journal append FAILED (best-effort) — proceeding to exit 78"
    exit 78
}

# promote_entry_check <target_ver> — refuses (exit 78) on: halthead (cap
# exhausted in-window), cooldown window, quarantined target, fresh in_flight,
# adoption-in-progress marker (Layer ii, commission v0.16.6 component 2).
# The AUTO-ROLLBACK path and the launcher sweep NEVER call this.
promote_entry_check() {
    local target_ver="$1" json cnt
    # cap / halt state
    cnt="$(journal_rollback_count_24h)"
    if [ "$cnt" -ge "$ROLLBACK_CAP_24H" ]; then
        _refuse cap "HALT-FOR-HUMAN: rollback cap $ROLLBACK_CAP_24H/24h reached (count=$cnt) — promotes refused until the 24h window resets or an operator intervenes (the journal refusal event reason=cap carries the record, D-FA2.2; the halt events that ARMED the cap sit in the rollback windows' history)"
    fi
    # cooldown
    if journal_cooldown_active; then
        local until
        until="$(_json_field "$(journal_read)" cooldown_until)"
        _refuse cooldown "promote refused: rollback cooldown active until $until (ADR-005: 10-min anti-flapping)"
    fi
    # quarantined target
    if journal_is_quarantined "$target_ver"; then
        _refuse quarantine "promote refused: version '$target_ver' is QUARANTINED (prior gate failure) — quarantine is cleared only by re-staging the version"
    fi
    # adoption-in-progress marker (commission v0.16.6 component 2, Layer ii).
    # Symmetric refusal: a half-staged adoption (.env staged, unit NOT
    # enabled) would silently misroute a concurrent promote's restart
    # through `systemctl restart <unit>` (lib.sh keys off
    # ENSEMBLE_RESTART_UNIT directly) onto a unit that does not exist
    # yet — promoting onto nothing. Marker present → refuse; the marker
    # is removed by adopt-unit.sh on verify success or by the operator
    # on a half-staged recovery (see the runbook).
    if adoption_marker_present; then
        local mp
        mp="$(adoption_marker_path)"
        _refuse adoption-in-progress "promote refused: adoption-in-progress marker present at $mp (adopt-unit.sh mid-sequence or stalled) — a concurrent promote would race the half-staged .env/unit state and silently misroute the restart; wait for the adoption to clear the marker, or remove it manually after verifying the unit is NOT half-staged"
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
#   comp3 FAST PATH (heartbeat-stale + owner-dead, age ≤ SWEEP_STALE_S):
#       reclaim NOW (skip the 600s wait). Closes the r-f82e gap where
#       the executor died 2s in (no heartbeat) AND the owner pid was
#       gone, but the launcher sweep wouldn't reclaim for 600s.
#       Heartbeat fresh OR owner alive → fall through to the age gate
#       below (do NOT short-circuit; the liveness fast path is
#       ADDITIVE to the primary age gate, not a replacement);
#   fresh (age ≤ SWEEP_STALE_S) → leave alone + refuse (pipeline-busy).
#       Only reached when the fast path didn't fire (heartbeat fresh
#       OR owner alive — the conservative reading);
#   stale + flipped:true → the SAME recovery the sweep would perform:
#       manifest gate on previous FIRST (null / QUARANTINED (M4) / release
#       dir missing / not rollback_safe → halt event, NO repoint, txn LEFT
#       IN PLACE, exit 78), then repoint current→previous (atomic_flip —
#       the portable rename(2) helper — see atomic_flip's portability
#       comment for the BSD/GNU mv dispatch details), quarantine
#       the failed target, count the rollback + arm cooldown (ADR-024),
#       history event 'sweep_rollback' (P2.3's ledger consumes event
#       names), clear txn. W3: every recovery state write (quarantine /
#       set_current / close_txn) is CHECKED — any failure leaves the txn
#       OPEN (the sweep retries this recovery idempotently), WARNs loudly,
#       and refuses the promote (exit 78). The counter write stays
#       warn-only (over-count is the safe drift direction);
#   stale + flipped:false → clear (history event 'sweep'). Close failure
#       → txn LEFT OPEN + loud warn + exit 78 (W3 — same contract).
#
# comp3 (r-f82e): both the liveness fast path (HEARTBEAT_STALE_S ×
# owner dead → reclaim early, before SWEEP_STALE_S) and the
# launcher.sh _journal_sweep mirror this decision. The launcher
# mirror is at BOOT (no concurrent activity to race against) and is
# the safer place; the adopt path also gets it because a manual recovery
# push (run-this-pipeline-now) shouldn't wait 600s for a heartbeat
# gap that's already been confirmed by liveness.
#
# Runs UNDER the caller's lock (promote acquires before calling). After a
# sweep-rollback adoption the enclosing promote continues into its ENTRY
# checks, which then apply the freshly armed cooldown/cap — PER DESIGN
# (D-FA4.2/ADR-024: the NEXT entry is refused inside the 10-min window;
# rollback.sh manual recovery never refuses on cooldown/cap).
adopt_stale_txn() {
    local json inf kind target started flipped owner epoch age now hb_stale owner_dead
    json="$(journal_read)" || return 0
    inf="$(_json_sub "$json" in_flight)"
    [ -z "$inf" ] && return 0
    case "$inf" in *"kind"*) ;; *) return 0 ;; esac
    kind="$(_json_field "$inf" kind)"
    target="$(_json_field "$inf" target)"
    started="$(_json_field "$inf" started_at)"
    flipped="$(_json_field "$inf" flipped)"
    owner="$(_json_field "$inf" owner_pid)"
    now="$(_now_epoch)"
    # comp3: liveness state computed once, consulted below.
    hb_stale="$(_txn_heartbeat_stale "$inf" "$now")"
    owner_dead="$(_txn_owner_dead "$inf")"
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
    age=$(( now - epoch ))
    # comp3 FAST PATH (heartbeat-stale + owner-dead → reclaim now).
    # Runs BEFORE the freshness refuse: closes the r-f82e gap where
    # the executor died ~2s in (no heartbeat ever written, owner gone)
    # and the existing freshness gate would have held the txn for
    # the full SWEEP_STALE_S=600s before the next launcher sweep
    # could reclaim it. The fast path SKIPS the freshness refuse and
    # goes straight to the standard stale-adopt body below — so the
    # hold-or-reclaim decision is uniform across the age-gated and
    # fast-paths (same manifest gate, same flip, same quarantine,
    # same counter increment).
    local _FAST_PATH=0
    if [ "$hb_stale" = "1" ] && [ "$owner_dead" = "1" ]; then
        _log "adopt_stale_txn: comp3 fast path triggered (hb_stale=1 owner_dead=1 age=${age}s) — reclaiming via the standard recovery (flipped=${flipped:-—})"
        _FAST_PATH=1
    fi
    # fresh txn (no fast path fired) → refuse. The fast path above
    # only fires when heartbeat is stale AND owner is dead; a fresh
    # heartbeat OR a live owner with a fresh-age txn still hits this
    # refuse (the conservative reading — never trample a possibly-
    # about-to-write owner).
    if [ "$_FAST_PATH" -eq 0 ] && [ "$age" -le "$SWEEP_STALE_S" ]; then
        _warn "promote refused: in_flight $kind txn (target=$target, pid=${owner:-?}, age ${age}s ≤ ${SWEEP_STALE_S}s) is FRESH — left alone (heartbeat-fresh OR owner-alive; comp3 fast path consulted above) — pipeline-busy"
        exit 78
    fi
    # comp3 audit: log the liveness state at adoption so post-mortems
    # can correlate (the launcher sweep logs the same fields).
    if [ "$hb_stale" = "1" ] && [ "$owner_dead" = "1" ]; then
        _log "adopt_stale_txn: comp3 audit — heartbeat-stale + owner-dead (hb_stale=$hb_stale, owner_dead=$owner_dead) reclaimed via the standard recovery"
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
