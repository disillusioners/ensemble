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
# ── The awk seam (the launcher's JSON validator/extractor) ─────────────────
# _JS_AWK_TIMEOUT_S — the wall-clock bound on ONE awk pass (the balance
# validator in _js_journal_read and the extractor in _js_json_sub). 60 s is
# ORDERS OF MAGNITUDE above any realistic journal (the ~250 KB perf fixture
# validates in ~50 ms; the launcher-side journal is size-capped at 96 KiB),
# so the bound can ONLY fire on a genuinely wedged interpreter. A bound a
# realistic journal COULD trip would be a self-inflicted boot outage. A
# TIMEOUT maps to the "validator unavailable" rc (2) — NEVER to "unbalanced".
_JS_AWK_TIMEOUT_S=60

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
#   KEY=value   # trailing comment   (the comment suffix is stripped — F-06)
# Malformed lines (no '=', invalid key charset) are logged and skipped.
# A missing file is NOT fatal: log loudly and continue with the inherited
# environment (prod may rely on plist-passed env).
#
# F-06: the VALUE-RESOLUTION RULE lives in _env_strip_value below and is
# duplicated BY DESIGN (byte-identical semantics) in
# scripts/watchdog-watcher.sh — the watchdog is a standalone script and must
# not depend on launcher.sh. The parity rows in
# tests/test_watchdog_watcher.sh pin the two implementations together.
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
        # PATH is SUPERVISOR-OWNED (design §iv Layer 1 + Layer 2): the
        # launchd anchor and the _path_ensure synthesis decide the PATH.
        # A PATH= line in .env would silently override BOTH — the rule used
        # to be comment-only; now it is ENFORCED (loud WARN + the key is
        # NOT exported).
        if [ "$key" = "PATH" ]; then
            _log "WARNING: PATH key in env file IGNORED ($env_file) — PATH is supervisor-owned (launchd anchor / _path_ensure synthesis); a PATH= line in .env would silently override it"
            continue
        fi
        # one layer of matching surrounding quotes + the unquoted inline
        # comment suffix (F-06) — ONE rule, shared with the watchdog.
        value="$(_env_strip_value "$value")"
        export "$key=$value"
    done < "$env_file"
    return 0
}

# _env_strip_value <value> — the ONE .env value-resolution rule (F-06).
# The value is expected to be TRIMMED already (both callers trim first).
#   1. a value WRAPPED IN MATCHING QUOTES is quoted → any '#' inside it is
#      part of the value ("a # b" survives) and ONE quote layer is stripped;
#   2. an UNQUOTED value loses everything from the first '#' THAT IS
#      PRECEDED BY WHITESPACE ("/p # c" → "/p"); a '#' NOT preceded by
#      whitespace ("pa#ss") is part of the value;
#   3. one layer of matching surrounding quotes is stripped afterwards
#      (e.g. the '"a"' left by cutting a trailing comment).
# Prints the resolved value. Pure — no side effects, no eval.
_env_strip_value() {
    local v="$1" i c prev=""
    if [ "${#v}" -ge 2 ]; then
        case "$v" in
            \"*\") printf '%s' "${v:1:${#v}-2}"; return 0 ;;
            \'*\') printf '%s' "${v:1:${#v}-2}"; return 0 ;;
        esac
    fi
    i=0
    while [ "$i" -lt "${#v}" ]; do
        c="${v:$i:1}"
        if [ "$c" = "#" ]; then
            case "$prev" in
                [[:space:]])
                    v="${v:0:$i}"
                    v="${v%"${v##*[![:space:]]}"}"
                    break
                    ;;
            esac
        fi
        prev="$c"
        i=$((i + 1))
    done
    if [ "${#v}" -ge 2 ]; then
        case "$v" in
            \"*\") v="${v:1:${#v}-2}" ;;
            \'*\') v="${v:1:${#v}-2}" ;;
        esac
    fi
    printf '%s' "$v"
}

# ── Supervisor-env PATH strategy (design §iv, Layer 2 — launcher surface) ──
# Two complementary layers, both required (architecture-recommendation.md §iv;
# rehearsal-a A/B proof: minimal launchd PATH ⇒ context7 stdio MCP
# FileNotFoundError, healthy:False; anchored PATH ⇒ healthy:True):
#   Layer 1 (macOS supervisor-env): scripts/ensemble-prod.plist carries a
#           STABLE-ANCHOR EnvironmentVariables.PATH (no user dirs, no nvm
#           version dirs — version dirs rot when nvm updates).
#   Layer 2 (THIS block, cross-platform shared launcher): probe the
#           inherited PATH; no-op when the needed tool class already
#           resolves; otherwise synthesize from stable anchors + the DYNAMIC
#           nvm default ($HOME/.nvm/alias/default read at boot — NEVER a
#           baked version dir). Required-tools provenance: npx
#           (context7.py:56), uvx (webfetch.py:36), node (npx runtime); the
#           daemon's spawn seams pass commands verbatim PATH-resolved, so
#           the fix is supervisor/PATH-layer ONLY — no daemon code changes.
# VM no-op contract: under systemd's default PATH npx/uvx/node already
# resolve ⇒ the probe logs one line and changes NOTHING (burst budget,
# env loading, every other behavior byte-identical).
#
# Pure functions (unit-tested by tests/test_launcher.sh §13 — source guard:
# sourcing must not run anything; _path_ensure is called from main() only).

# _path_has <dir> — exact colon-component membership test against the
# CURRENT PATH. No word-splitting games, no external tools.
_path_has() {
    case ":${PATH:-}:" in
        *":$1:"*) return 0 ;;
    esac
    return 1
}

# _path_ver_str_gt <verA> <verB> — numeric dot-component compare of two
# VERSION strings ("22.21.1" vs "22.9.1"; a leading "v" is tolerated).
# Missing components read as 0; any non-numeric component ⇒ not-greater
# (stable fallback). Pure bash — `sort -V` is GNU-only and this must run on
# the macOS/BSD supervisor too.
_path_ver_str_gt() {
    local a="${1#v}" b="${2#v}"
    local -a A B
    local IFS=. i ai bi
    read -ra A <<< "$a" || return 1
    read -ra B <<< "$b" || return 1
    for i in 0 1 2 3; do
        ai="${A[i]:-}"; bi="${B[i]:-}"
        [ -n "$ai" ] || ai=0
        [ -n "$bi" ] || bi=0
        case "${ai}${bi}" in *[!0-9]*) return 1 ;; esac
        if [ "$ai" -gt "$bi" ]; then return 0; fi
        if [ "$ai" -lt "$bi" ]; then return 1; fi
    done
    return 1
}

# _path_version_gt <dirA> <dirB> — numeric dot-component compare of the
# version SUFFIX of two nvm version dirs (…/node/vX.Y.Z). Delegates to the
# string comparator above (same semantics — shared with the alias resolver,
# which compares bare version strings).
_path_version_gt() {
    _path_ver_str_gt "${1##*/v}" "${2##*/v}"
}

# _nvm_alias_version <token> <depth> — resolve an nvm ALIAS token to a
# CONCRETE version string (F-10). nvm's alias/default may hold a CODENAME
# ("lts/*", "lts/krypton") rather than a version — the old one-hop glob
# ("…/node/v<token>*") is dead for those, so on macOS (the only source of
# npx) the synthesis yielded NOTHING and npx stayed unresolved — exactly
# the rehearsal-a failure mode this block exists to prevent.
# Resolution (BOUNDED to 3 hops so a self-referential alias cannot loop):
#   token without "/"        → already version-ish ("22", "22.21.1") → as-is;
#   alias/<token> is a FILE  → its content is the next token (recurse);
#   alias/<token> is a DIR   → every entry file's content is a version →
#                              the HIGHEST version wins;
#   token contains a glob    → expand alias/<token>, HIGHEST version wins.
# Prints the version string (leading "v" stripped); prints NOTHING when the
# token is unresolvable (the caller then warns — boot is never wedged).
_nvm_alias_version() {
    local token="$1" depth="$2" f content best="" v alias_dir=""
    local max_hops=3
    [ -n "$token" ] || return 0
    [ "$depth" -lt "$max_hops" ] || return 0
    case "$token" in
        */*) ;;
        *) printf '%s' "${token#v}"; return 0 ;;
    esac
    f="${HOME:-}/.nvm/alias/$token"
    if [ -f "$f" ]; then
        content="$(head -n 1 "$f" 2>/dev/null)"
        content="${content%%$'\r'}"
        content="${content#"${content%%[![:space:]]*}"}"
        content="${content%"${content##*[![:space:]]}"}"
        content="${content%%[[:space:]]*}"
        _nvm_alias_version "$content" "$((depth + 1))"
        return 0
    fi
    if [ -d "$f" ]; then
        for v in "$f"*; do
            [ -f "$v" ] || continue
            content="$(head -n 1 "$v" 2>/dev/null)"
            content="${content%%$'\r'}"
            content="${content#"${content%%[![:space:]]*}"}"
            content="${content%"${content##*[![:space:]]}"}"
            content="${content%%[[:space:]]*}"
            [ -n "$content" ] || continue
            if [ -z "$best" ] || _path_ver_str_gt "$content" "$best"; then
                best="$content"
            fi
        done
        [ -n "$best" ] && printf '%s' "${best#v}"
        return 0
    fi
    case "$token" in
        *'*'*|*'?'*)
            # the token may CARRY a glob ("lts/*") → expand it. The alias
            # dir stays quoted; the token is unquoted so the shell performs
            # the pathname expansion (it is the first whitespace-delimited
            # word of the alias file, so it carries no spaces).
            alias_dir="${HOME:-}/.nvm/alias"
            for v in "$alias_dir"/$token; do
                [ -f "$v" ] || continue
                content="$(head -n 1 "$v" 2>/dev/null)"
                content="${content%%$'\r'}"
                content="${content#"${content%%[![:space:]]*}"}"
                content="${content%"${content##*[![:space:]]}"}"
                content="${content%%[[:space:]]*}"
                [ -n "$content" ] || continue
                if [ -z "$best" ] || _path_ver_str_gt "$content" "$best"; then
                    best="$content"
                fi
            done
            [ -n "$best" ] && printf '%s' "${best#v}"
            ;;
    esac
    return 0
}

# _path_nvm_bin — the nvm DEFAULT version's bin dir, resolved DYNAMICALLY
# (design §iv: "read $HOME/.nvm/alias/default, glob $HOME/.nvm/versions/
# node/v<alias>*, take latest, verify bin/node executable — NEVER hardcode
# a version dir"). The alias token is first RESOLVED to a concrete version
# (F-10: "lts/*" / "lts/krypton" are codenames, not versions). Highest
# VERSION wins (numeric compare — lexicographic glob order would pick
# v22.9.3 over v22.21.1). Prints the bin dir, or nothing when nvm is
# absent/unresolvable.
_path_nvm_bin() {
    local alias_file="${HOME:-}/.nvm/alias/default" alias="" d best="" ver=""
    [ -n "${HOME:-}" ] || return 0
    [ -f "$alias_file" ] || return 0
    alias="$(head -n 1 "$alias_file" 2>/dev/null)"
    alias="${alias%%$'\r'}"
    alias="${alias%%[[:space:]]*}"   # first token ("22" / "22.21.1" / "lts/*")
    [ -n "$alias" ] || return 0
    ver="$(_nvm_alias_version "$alias" 0)"
    [ -n "$ver" ] || return 0
    for d in "$HOME/.nvm/versions/node/v$ver"*; do
        [ -d "$d" ] || continue
        [ -x "$d/bin/node" ] || continue
        if [ -z "$best" ] || _path_version_gt "$d" "$best"; then
            best="$d"
        fi
    done
    [ -n "$best" ] && printf '%s\n' "$best/bin"
    return 0
}

# _path_synth_dirs — the §iv candidate set in order: stable anchors, then
# the dynamic nvm default. Prints EXISTING dirs only (newline-separated);
# the caller filters dirs already present in the CURRENT PATH.
_path_synth_dirs() {
    local d
    for d in /opt/homebrew/bin /usr/local/bin "${HOME:-}/.local/bin"; do
        [ -n "$d" ] && [ -d "$d" ] && printf '%s\n' "$d"
    done
    _path_nvm_bin
    return 0
}

# _path_ensure — the Layer-2 entry point (called from main, AFTER
# load_env_file so an .env-staged ENSEMBLE_LAUNCHER_PATH participates in
# the same precedence, and BEFORE _journal_sweep/binary resolve so every
# launcher child and the daemon inherit the resolved PATH):
#   1. ENSEMBLE_LAUNCHER_PATH set → export VERBATIM (top precedence —
#      full replacement, not a prepend; design §iv step 1).
#   2. Probe: npx/uvx/node ALL resolve → one log line, change NOTHING
#      (the VM no-op case).
#   3. Synthesize (any missing): prepend existing anchor/nvm dirs not
#      already in PATH; inherited PATH retained as suffix. Nothing
#      addable → loud WARNING, proceed — optional MCP tooling must never
#      wedge daemon boot (behavior degrades to the pre-block status quo).
#
# F-11 (observability only — WHEN synthesis fires and WHAT it prepends are
# UNCHANGED): EVERY branch now logs the three facts a VM drill can assert:
#   source=      which branch produced the final PATH
#                (ensemble_launcher_path_override | probe-satisfied |
#                 synthesized | none-addable);
#   delta=       exactly what was prepended (and whether the inherited PATH
#                was retained as the suffix — the override branch is a FULL
#                replacement);
#   resolved_path=the final PATH value (a PATH is not a secret; the drill
#                needs the real value to assert the delta).
# The "VM no-op / changes NOTHING" claim is therefore CHECKABLE from the
# log: source=probe-satisfied + delta=none ⇒ the PATH was not touched.
_path_ensure() {
    local t missing="" synth="" added="" d still=""
    if [ -n "${ENSEMBLE_LAUNCHER_PATH:-}" ]; then
        export PATH="$ENSEMBLE_LAUNCHER_PATH"
        _log "PATH: ENSEMBLE_LAUNCHER_PATH override exported verbatim (§iv Layer 2, top precedence)"
        _log "PATH: source=ensemble_launcher_path_override delta=full-replacement (the inherited PATH is NOT retained — the override replaces it) resolved_path=$PATH"
        return 0
    fi
    for t in npx uvx node; do
        command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
    done
    if [ -z "$missing" ]; then
        _log "PATH: npx/uvx/node all resolve on the inherited PATH — probe no-op, PATH unchanged"
        _log "PATH: source=probe-satisfied delta=none (the inherited PATH is retained verbatim) resolved_path=${PATH:-}"
        return 0
    fi
    while IFS= read -r d; do
        [ -n "$d" ] || continue
        _path_has "$d" && continue
        synth="${synth:+$synth:}$d"
        added="$added $d"
    done < <(_path_synth_dirs)
    if [ -z "$synth" ]; then
        _log "WARNING: PATH probe missing:${missing} — no existing stable anchors and nvm default unresolvable; proceeding on the inherited PATH (optional MCP tooling may be unavailable)"
        _log "PATH: source=none-addable delta=none (the inherited PATH is retained verbatim) resolved_path=${PATH:-}"
        return 0
    fi
    # "${PATH:+:$PATH}" — set -u safe (an UNSET PATH must not abort the
    # launcher) and EMPTY-PATH safe (a trailing ":" is an empty PATH
    # component, which POSIX resolves as the CWD on the command search
    # path). Inert on any host with a non-empty PATH.
    export PATH="$synth${PATH:+:$PATH}"
    _log "PATH: probe missing:${missing} — synthesized PATH extended with:${added} (stable anchors + dynamic nvm default; inherited PATH retained as suffix)"
    _log "PATH: source=synthesized delta=prepended:${synth} (the inherited PATH is retained as the suffix) resolved_path=$PATH"
    for t in npx uvx node; do
        command -v "$t" >/dev/null 2>&1 || still="$still $t"
    done
    if [ -n "$still" ]; then
        _log "WARNING: PATH synthesis complete but still unresolved:${still} — proceeding (optional MCP tooling degraded, never blocking boot)"
    fi
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

# ── the awk seam (ONE place owns the rc VOCABULARY) ───────────────────────
#
# Both JSON readers in this file are POSIX-awk passes over the journal:
# `_js_journal_read`'s balance validator and `_js_json_sub`'s extractor.
# The awk PROGRAM BODIES are the council-signed equivalence artifact (they
# reproduce the replaced per-character bash loops BYTE-FOR-BYTE) — they are
# NOT touched by the rc work. What lives here is the EXECUTION seam: the
# wall-clock bound and the mapping of the awk's exit status onto the
# launcher's rc vocabulary.
#
# RC VOCABULARY (produced by _js_awk_bounded, below):
#   0 — the awk ran and returned SUCCESS (journal valid / value extracted)
#   1 — the awk ran and returned a CONTENT verdict: bracket-unbalanced,
#       unterminated string, key absent, value not an object → the content
#       is NOT TRUSTWORTHY
#   2 — the VALIDATOR/EXTRACTOR was UNAVAILABLE: awk missing (127), awk
#       runtime error (2), or the watchdog fired (124). The content verdict
#       is UNKNOWN — NEVER report it as "unbalanced" (a false "unbalanced"
#       silently DISABLES the sweep) and NEVER as "absent" (a false
#       "absent" silently RESETS the 24h rollback counter).
#
# _JS_LAST_AWK_RC — the RAW exit status of the last awk pass (0/1/127/2/124).
# Read it in the message that names the failure class, so the operator sees
# WHICH failure it was ("awk rc=127" = the interpreter is gone).
# VISIBILITY ACROSS THE $(...) BOUNDARY: `_js_journal_read`/`_js_json_sub`
# are invoked via command substitution (itself a subshell), so even with
# the here-string delivery the in-frame assignment would die with that
# subshell. When a caller holds FD 9 OPEN FOR WRITING around the producer
# call (`_js_awk_rc_begin` / `_js_awk_rc_end`), `_js_awk_bounded` writes
# the raw status through it and `_js_awk_rc_end` folds it back into the
# caller's frame. FD 9 — NOT 3 (`_js_awk_pass` runs awk on fd 3).
_JS_LAST_AWK_RC=0
_JS_AWK_RC_TMP=""

# _js_awk_pass <program> [key] — run ONE awk pass over the caller's input.
#
# WHY fd 3: bash assigns /dev/null to an ASYNCHRONOUS command's stdin when
# job control is off (POSIX), so a naive `printf ... | awk ...` would read
# EOF the moment the awk is backgrounded for the watchdog. The caller
# dup'ed the pipe onto fd 3 (3<&0) and this helper points awk's stdin at
# it (<&3).
#
# WHY exec: the helper shell REPLACES itself with awk, so the watchdog's
# SIGKILL lands on awk ITSELF — a wrapper would leave awk orphaned (and the
# pipe held open) after the kill.
#
# The key (when given) travels via the ENVIRONMENT (never `awk -v`), so no
# escape sequence inside a key can be mangled.
_js_awk_pass() {
    local prog="$1" key="${2-}"
    if [ -n "$key" ]; then
        _JS_JSON_SUB_KEY="$key" LC_ALL=C exec awk "$prog" <&3
    fi
    LC_ALL=C exec awk "$prog" <&3
}

# _js_awk_bounded <program> [key] — run ONE awk pass under the wall-clock
# bound and MAP its exit status onto the rc vocabulary above.
#
# The mapping lives HERE (in the WRAPPER), not in the awk program: the
# program still exits 0/1 and its body stays byte-identical to the
# council-signed artifact.
#   raw 0   → 0 (success)
#   raw 1   → 1 (content verdict — the content is not trustworthy)
#   raw 127 → 2 (awk not found)
#   raw 2   → 2 (awk runtime error — a broken program or environment)
#   raw 124 → 2 (the watchdog fired — the pass wedged; _js_run_bounded
#              maps the SIGKILL to the conventional `timeout` code 124)
#   any other raw status → 2 (an unknown failure is NOT a content verdict)
#
# The input arrives on the caller's stdin (delivered by a here-string at
# the call sites); the awk program is unchanged. Callers invoke this
# wrapper DIRECTLY — never as a pipeline segment: a pipeline's last
# segment runs in a SUBSHELL, so the `_JS_LAST_AWK_RC` assignment below
# would never reach the caller's frame and every unavailable-WARN would
# quote a stale `awk rc=0` (D1, probe-proven; fixed round 3).
_js_awk_bounded() {
    local prog="$1" key="${2-}" rc=0
    if _js_run_bounded "$_JS_AWK_TIMEOUT_S" -- _js_awk_pass "$prog" "$key" 3<&0; then
        rc=0
    else
        rc=$?
    fi
    _JS_LAST_AWK_RC="$rc"
    # raw-rc side-channel (see the rc-vocabulary block above): when the
    # caller holds FD 9 open for writing around the producer call, the
    # raw status crosses the $(...) command-substitution boundary with
    # it. Closed FD 9 → the write fails harmlessly and the value simply
    # stays frame-local. NB: the silencing MUST be scoped to the group —
    # redirection-setup errors fire BEFORE a command-scoped 2>/dev/null
    # is applied, so `printf ... >&9 2>/dev/null` still leaks
    # "9: Bad file descriptor" to stderr on every unbracketed pass.
    { printf '%s' "$rc" >&9; } 2>/dev/null || true
    case "$rc" in
        0|1) return "$rc" ;;
        *)   return 2 ;;
    esac
}

# _js_awk_rc_begin / _js_awk_rc_end — bracket ONE producer call
# (`_js_journal_read` / `_js_json_sub`) so its RAW awk status reaches the
# CALLER's frame. `$(...)` is itself a subshell: the in-frame assignment
# inside the producer would be discarded with it. begin opens FD 9 to a
# per-process temp file (inherited by the cmdsub subshell); the wrapper
# writes the raw status through it; end folds it back into
# `_JS_LAST_AWK_RC` and removes the file. Every WARN that quotes
# `awk rc=$_JS_LAST_AWK_RC` sits behind a bracketed producer call —
# brackets only ever span ONE producer call, so "the last pass" and
# "the pass that failed" are the same pass.
_js_awk_rc_begin() {
    _JS_AWK_RC_TMP="${TMPDIR:-/tmp}/launcher-awkrc.$$"
    # NB: the silencing MUST be scoped to the group — a bare
    # `exec 9>f 2>/dev/null` persists BOTH redirections and would send
    # every later WARN to /dev/null (probe-caught in round 3).
    { exec 9>"$_JS_AWK_RC_TMP"; } 2>/dev/null || true
}
_js_awk_rc_end() {
    { exec 9<&-; } 2>/dev/null || true
    if [ -n "$_JS_AWK_RC_TMP" ] && [ -f "$_JS_AWK_RC_TMP" ]; then
        _JS_LAST_AWK_RC="$(cat "$_JS_AWK_RC_TMP" 2>/dev/null)"
        [ -n "$_JS_LAST_AWK_RC" ] || _JS_LAST_AWK_RC=0
        rm -f "$_JS_AWK_RC_TMP" 2>/dev/null || true
    fi
    _JS_AWK_RC_TMP=""
}

# _js_json_sub <json> <key> — extract a nested object/array value by
# bracket counting from `"key":`. Prints the raw {…} / […] text.
#
# RC CONTRACT:
#   0 — the value was extracted and printed
#   1 — the key is ABSENT (or its value is not an object/array) — a CONTENT
#       verdict, not an error
#   2 — the EXTRACTOR was UNAVAILABLE (awk missing / broken / timed out) —
#       the verdict is UNKNOWN. Callers MUST NOT read this as "the key is
#       absent": an empty extraction is indistinguishable from "the field is
#       gone", and the writers that splice an empty value back into the
#       journal would then OVERWRITE real history.
_js_json_sub() {
    local json="$1" key="$2" rc=0
    [ -n "$json" ] || return 1
    # O(n) extraction (launcher-journal-sweep-fix, R2): the replaced loop
    # built the value with `out="$out$char"` AND stripped the consumed
    # prefix per character (`rest="${rest:1}"`) — both O(n) per step,
    # O(n²) overall, on every sweep read. ONE POSIX awk pass owns the
    # scan now (vectorized: the post-value tail is split into
    # per-bracket segments once, C-speed; the depth walk then visits
    # brackets only, by computed position — ~tens of ms @ 455 KB). The
    # contract is preserved BYTE-FOR-BYTE:
    #   - the FIRST `"key"` occurrence anchors (≡ bash shortest-`#` strip);
    #   - skip to the first `:` after it (index(), C-speed), trim
    #     leading [[:space:]];
    #   - the value must START with { or [ (else return 1);
    #   - bracket depth counts EVERY { } [ ] — string context is NOT
    #     tracked, exactly like the replaced loop (a brace inside a
    #     string value still terminates the extraction — quirk preserved
    #     deliberately, pinned by the tests; changing it would CHANGE
    #     verdicts on existing journals);
    #   - EOF before depth closes → return 1.
    # PORTABILITY: POSIX awk — the pass runs UNCHANGED on macOS's onetrue
    # awk, Debian/Ubuntu's mawk and gawk (the POSIX-only features it uses:
    # ENVIRON, split() with a regex separator, substr/index/length).
    # LC_ALL=C pins byte-oriented processing; the key travels via the
    # environment (not awk -v) so no escape sequences can be mangled.
    # DELIVERY (round-3 rc-visibility fix): the json rides stdin via a
    # bash here-string and the wrapper is invoked DIRECTLY — no longer as
    # a pipeline segment. A pipeline's last segment runs in a SUBSHELL,
    # so `_JS_LAST_AWK_RC` (set inside `_js_awk_bounded`) never reached
    # this frame and every unavailable-WARN printed a stale `awk rc=0`
    # even after a raw 127 (D1, probe-proven). The RAW RC CROSSES THE
    # OUTER $(...) LAYER TOO via the FD 9 side-channel: callers that
    # quote the raw status bracket the producer call with
    # `_js_awk_rc_begin` / `_js_awk_rc_end` (see the rc-vocabulary
    # block). The here-string delivers the SAME bytes as the old printf
    # (one O(n) write) plus ONE trailing newline; awk drops a final
    # record separator, so buf is byte-exact unchanged (§13g/h/i pins
    # hold).
    # The awk program re-assembles stdin VERBATIM: RS="\n" consumes the
    # record separator and the join puts it back, so buf is the input
    # bytes MINUS a trailing newline (awk's record split drops the final
    # separator — the here-string's newline IS that final separator; the
    # callers' json is itself newline-free: `_js_journal_read` prints
    # with printf '%s' and every `$(...)` capture strips trailing
    # newlines). The §13g/h/i fixtures pin a byte-exact round-trip,
    # so this detail is load-bearing.
    _js_awk_bounded '
        BEGIN { buf = ""; first = 1 }
        {
            if (first == 1) { buf = $0; first = 0 }
            else buf = buf "\n" $0
        }
        END {
            n = length(buf)
            kq = "\"" ENVIRON["_JS_JSON_SUB_KEY"] "\""
            p = index(buf, kq)
            if (p == 0) exit 1
            i = p + length(kq)
            # skip to the first ":" after the key (≡ bash ${rest#*:})
            t = substr(buf, i)
            r = index(t, ":")
            if (r == 0) exit 1
            i = i + r
            # trim leading whitespace (≡ bash [![:space:]] trim)
            n = length(buf)
            while (i <= n) {
                c = substr(buf, i, 1)
                if (c == " " || c == "\t" || c == "\n" || c == "\r" \
                    || c == "\f" || c == "\v") { i++; continue }
                break
            }
            if (i > n) exit 1
            c = substr(buf, i, 1)
            if (c != "{" && c != "[") exit 1
            # vectorized depth walk. `tail` is the value text starting at
            # the FIRST bracket (the value was just checked to start with
            # { or [), so split() yields an EMPTY seg[1] — the
            # initialization `pos = length(seg[1])` is therefore ALWAYS 0.
            # seg[j] (j>=2) is the text BETWEEN bracket j-1 and bracket j.
            # At the top of iteration j, pos is the OFFSET (within tail)
            # of bracket j MINUS 1; `pos++` makes it the exact offset of
            # bracket j, and `pos += length(seg[j+1])` at the END of
            # iteration j advances to just before bracket j+1. The emitted
            # substr(buf, i, pos) uses pos as a LENGTH RELATIVE TO i — it
            # is the offset of the closing bracket within tail, so the
            # slice is the value text INCLUDING its closing bracket.
            # THE LOOP VISITS EXACTLY THE tn-1 REAL BRACKETS: split() yields
            # one field per segment PLUS a trailing field (the text AFTER
            # the last bracket), so the bound is `j < tn` — visiting the
            # trailing field would read a position PAST the last bracket
            # (the empty string, or trailing text) and count it as a
            # CLOSING bracket. That phantom decrement let a value which is
            # missing its LAST closing bracket reach depth 0 and return a
            # TRUNCATED value with rc 0 — a divergence from the replaced
            # bash loop (which returns 1 at EOF with the depth still open)
            # and a violation of the documented contract of this program
            # ("EOF before depth closes → return 1"). With the bound at
            # `j < tn` an unbalanced value exits 1 with NO output.
            tail = substr(buf, i)
            tn = split(tail, seg, "[]{}[]")
            pos = length(seg[1])
            depth = 0
            for (j = 1; j < tn; j++) {
                pos++
                c = substr(tail, pos, 1)
                if (c == "{" || c == "[") depth++
                else {
                    depth--
                    if (depth == 0) {
                        printf "%s", substr(buf, i, pos)
                        exit 0
                    }
                }
                pos += length(seg[j + 1])
            }
            exit 1
        }
    ' "$key" <<<"$json" || rc=$?
    return "$rc"
}

# _js_journal_read <path> — print journal JSON.
#
# RC CONTRACT:
#   0 — the journal was read and PASSED the balance check; the JSON is
#       printed on stdout
#   1 — the journal is ABSENT / UNREADABLE / EMPTY, or its CONTENT is not
#       trustworthy: BRACKET-UNBALANCED ({ } [ ]) or UNTERMINATED STRING
#       (a torn write) — the D4 refuse-to-trust class
#   2 — the VALIDATOR was UNAVAILABLE (awk missing / broken / timed out) —
#       the verdict is UNKNOWN, because the check never ran. NEVER report
#       this as "unbalanced": a false "unbalanced" silently DISABLES the
#       sweep and hides a broken interpreter.
#
# WHAT THE CHECK IS: a BRACKET/STRING BALANCE CHECK — NOT a JSON parse.
# (The daemon's side of the journal requires PARSEABLE JSON — JournalTorn —
# so the two sides do not agree on what "a valid journal" means. That
# asymmetry is a known, filed gap; the LAUNCHER half is this function, and
# every message that reports a refusal names the check that actually ran.)
#
# Bounded 3s cat read (fix #4, v0.16.9): if the journal file is on a hung
# NFS / FIFO / kernel wedge, the previous unbounded `cat` could pin the
# sweep forever — the 2026-10-01 attempt-1 sweep hang. On timeout: sweep
# returns nonzero; the txn is left OPEN for the next start.
_js_journal_read() {
    local jp="$1" json rc=0
    [ -f "$jp" ] || return 1
    json="$(_js_run_bounded 3 -- cat "$jp")" || return 1
    [ -z "$json" ] && return 1
    # O(n) balance validation (launcher-journal-sweep-fix, R1): the old
    # per-character bash loop (`${json:i:1}` indexing) was O(n²) — 25.2 s
    # measured @ 37 KB, ~31 min extrapolated @ 215 KB — and ran on EVERY
    # boot via _journal_sweep. ONE POSIX awk pass owns the scan now
    # (vectorized: split each line on quotes, count brackets per
    # outside-string segment with gsub — all C-speed; ~tens of ms @
    # 455 KB). LC_ALL=C pins byte-oriented processing, so the verdict is
    # locale-invariant BY CONSTRUCTION (multibyte UTF-8 bytes can never
    # collide with the ASCII structural characters).
    # PORTABILITY: POSIX awk — the pass runs UNCHANGED on macOS's onetrue
    # awk, Debian/Ubuntu's mawk and gawk (the POSIX-only features it uses:
    # split() with a regex separator, gsub()'s count-returning form,
    # match()/RLENGTH).
    # Semantics are UNCHANGED (D4 discipline — callers and pins stay put):
    #   - { } [ ] counted ONLY outside strings;
    #   - strings are escape-aware (an escaped quote toggles nothing, an
    #     escaped backslash consumes the next char, so \\\" can never
    #     close the string) — the vectorized form decides each boundary
    #     quote by the PARITY of the backslash run immediately before it
    #     (odd = escaped), which is provably the same state machine;
    #   - reject on ob≠cb ∨ os≠cs ∨ unterminated string at EOF;
    #   - NOT a full JSON parser — the same balance-count contract the
    #     replaced loop implemented (torn/truncated tail → nonzero →
    #     _journal_sweep skips safely; e.g. a stray "}{ is still
    #     "balanced" exactly as before).
    # The pass is BOUNDED (60 s — see _JS_AWK_TIMEOUT_S) and its exit
    # status is MAPPED by _js_awk_bounded: 1 = a content verdict (not
    # trustworthy), 2 = the validator was unavailable (verdict UNKNOWN).
    # DELIVERY (round-3 rc-visibility fix): the wrapper is invoked via a
    # here-string in THIS shell — a pipeline segment would run it in a
    # subshell and `_JS_LAST_AWK_RC` (the raw awk status the WARN sites
    # quote) would never land in this frame. The raw status ALSO crosses
    # this function's own `$(...)` capture at the callers via the FD 9
    # side-channel (`_js_awk_rc_begin`/`_js_awk_rc_end` brackets — see
    # the rc-vocabulary block). The here-string adds ONE trailing
    # newline, which awk drops as the final record separator — the pass
    # sees the same bytes (see _js_json_sub's delivery note).
    _js_awk_bounded '
        BEGIN { ob = 0; cb = 0; os = 0; cs = 0; in_str = 0 }
        {
            n = split($0, parts, "\"")
            cur = in_str
            for (p = 1; p <= n; p++) {
                if (cur == 0) {
                    t = parts[p]
                    ob += gsub(/\{/, "", t)
                    cb += gsub(/\}/, "", t)
                    os += gsub(/\[/, "", t)
                    cs += gsub(/\]/, "", t)
                }
                if (p < n) {
                    if (cur == 1) {
                        # boundary quote while inside a string: escaped
                        # iff the backslash run before it has odd length
                        t = parts[p]
                        if (match(t, /\\+$/) > 0 && RLENGTH % 2 == 1) {
                            ;  # escaped quote — the string continues
                        } else {
                            cur = 0
                        }
                    } else {
                        cur = 1
                    }
                }
            }
            in_str = cur
        }
        END {
            if (ob != cb || os != cs || in_str == 1) exit 1
            exit 0
        }
    ' <<<"$json" || rc=$?
    if [ "$rc" -ne 0 ]; then
        return "$rc"
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
#
# O(n) (launcher-journal-sweep-fix, R3): the replaced implementation
# consumed the OLD VALUE ONE CHARACTER AT A TIME (`rest="${rest:1}"` in the
# two value-scan loops) — O(n) per step under LC_ALL=C and O(n) per
# CHARACTER under a UTF-8 locale ⇒ O(n²) overall. That scan was 100% of
# `_js_history_append`'s cost (the append re-writes the WHOLE history
# array): measured 16.9 s @ 32 KB and 106.2 s @ 32 KB under the daemon's
# UTF-8 env (6.3×/locale), >300 s @ 220 KB — the boot wedge this lane
# exists to close. ONE POSIX awk pass owns the scan now.
#
# BYTE-EQUIVALENCE (the contract this rewrite MUST hold — the output is
# byte-identical to the replaced implementation for the same input):
#   - the ANCHOR is unchanged: the FIRST occurrence of the field token
#     wins (`head`/`rest` still use the same `"$field"` globs, and the
#     quoted-token presence gate is still checked BEFORE the splice — a
#     bare field NAME that never appears as `"field"` still refuses);
#   - the WHITESPACE TRIM is still BASH's own `[![:space:]]` trim — it is
#     ASCII-only in EVERY locale (probed on macOS: a U+00A0 is NOT
#     stripped under either en_US.UTF-8 or C), so the trim is
#     locale-invariant and stays in bash;
#   - the VALUE SCAN is byte-equivalent to the replaced character loop:
#     the same structural characters (`{ } [ ] ,`) are compared and the
#     same escape-aware quote rule applies, and only ASCII characters are
#     ever compared — a multibyte UTF-8 byte can never be mistaken for a
#     structural one, so a character-wise scan and a byte-wise scan stop
#     at the SAME place and yield the SAME tail;
#   - the SPLICE is unchanged: `head + ": " + raw + tail`.
# The awk pass runs under the launcher's single awk seam, which pins
# LC_ALL=C (see `_js_awk_pass`) — byte-oriented, so the scan is
# locale-invariant BY CONSTRUCTION.
_js_journal_update() {
    local jp="$1" field="$2" raw="$3" json rest head out="" tail rc=0 arc=0
    _js_awk_rc_begin
    json="$(_js_journal_read "$jp")" || rc=$?
    _js_awk_rc_end
    if [ "$rc" -eq 2 ]; then
        # The validator was UNAVAILABLE — the journal's trustworthiness is
        # UNKNOWN, so the write is REFUSED (an empty read would splice a
        # field's worth of nothing over real content).
        _log "WARN: journal update refused — $jp: the journal validator is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC); the journal is left untouched"
        return 1
    fi
    [ "$rc" -eq 0 ] || return 1
    case "$json" in
        *"$field"*) ;;
        *) return 1 ;;
    esac
    # Walk the object; replace the value that follows "field".
    rest="$json"
    case "$rest" in
        *"\"$field\""*) ;;
        # The field NAME occurs in the document but never as a quoted
        # token (field=history against {"xhistory":…}) — the replaced
        # loop matched nothing and left `out` empty ⇒ refuse (the same
        # verdict, decided before the splice instead of after it).
        *) return 1 ;;
    esac
    head="${rest%%\"$field\"*}\"$field\""
    rest="${rest#*\"$field\"}"
    rest="${rest#*:}"
    # drop leading whitespace of the old value
    rest="${rest#"${rest%%[![:space:]]*}"}"
    # ONE awk pass returns the tail that follows the old value: the
    # string case (a leading quote) skips to the escape-aware closing
    # quote; the composite case walks the bracket depth to the first
    # top-level `,` / `}` / `]`. Both are the replaced loops' rules,
    # byte-for-byte.
    _js_awk_rc_begin
    tail="$(_js_awk_bounded '
        BEGIN { buf = ""; first = 1 }
        {
            if (first == 1) { buf = $0; first = 0 }
            else buf = buf "\n" $0
        }
        END {
            n = length(buf)
            if (n >= 1 && substr(buf, 1, 1) == "\"") {
                # STRING VALUE — VECTORIZED (the validator parity idiom,
                # proven in the balance validator):: split ONCE on the quote boundaries and
                # visit only the SEGMENTS; each boundary quote is escaped
                # iff the backslash run before it has odd length, so the
                # first unescaped quote closes the string. Only ASCII
                # structural characters are compared — byte-equivalent to
                # the replaced per-character loop.
                s = substr(buf, 2)
                m = split(s, q, "\"")
                pos = 0
                found = 0
                for (j = 1; j < m; j++) {
                    pos += length(q[j]) + 1   # the segment bytes + the quote byte
                    t = q[j]
                    if (match(t, /\\+$/) > 0 && RLENGTH % 2 == 1) continue
                    found = 1
                    break
                }
                if (found) start = pos + 2    # the closing quote is consumed
                else start = n + 1            # unterminated: everything consumed
            } else {
                # COMPOSITE VALUE — VECTORIZED (the _js_json_sub idiom):
                # split the value ONCE on the structural characters and
                # visit only those (~3 per history entry, never a
                # per-byte walk). The terminator is NOT consumed (the
                # replaced loop broke on it); EOF ⇒ the tail is empty.
                tn = split(buf, seg, "[],{}[]")
                pos = length(seg[1])
                depth = 0
                start = n + 1
                for (j = 1; j < tn; j++) {
                    pos++
                    c = substr(buf, pos, 1)
                    if (c == "{" || c == "[") depth++
                    else if (c == "}" || c == "]") {
                        if (depth == 0) { start = pos; break }
                        depth--
                    } else if (c == ",") {
                        if (depth == 0) { start = pos; break }
                    }
                    pos += length(seg[j + 1])
                }
            }
            printf "%s", substr(buf, start)
        }
    ' <<<"$rest")" || arc=$?
    _js_awk_rc_end
    if [ "$arc" -ne 0 ]; then
        # The SCANNER was UNAVAILABLE (awk missing / wedged): the extent
        # of the old value is UNKNOWN, so the splice is REFUSED — a wrong
        # extent would truncate or corrupt the journal.
        _log "WARN: journal update refused — $jp: the value scanner is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC); the journal is left untouched"
        return 1
    fi
    out="$head: $raw$tail"
    [ -n "$out" ] || return 1
    _js_journal_write "$jp" "$out"
}

# _js_history_append <path> <event> <detail> — append {ts,event,detail} to
# history (newest last, D4).
_js_history_append() {
    local jp="$1" event="$2" detail="$3" json hist new rc=0
    _js_awk_rc_begin
    json="$(_js_journal_read "$jp")" || rc=$?
    _js_awk_rc_end
    if [ "$rc" -eq 2 ]; then
        # The journal READ was UNAVAILABLE (the balance validator never
        # ran) — the verdict is UNKNOWN, so the append is REFUSED. Same
        # safe outcome as the extractor refusal below, but LOUD: this arm
        # used to fall into `|| return 1` with NO WARN (silent-refuse
        # class — D2, round-3 sweep).
        _log "WARN: journal history append REFUSED — $jp: the journal validator is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC); the history array is left untouched"
        return 1
    fi
    [ "$rc" -eq 0 ] || return 1
    _js_awk_rc_begin
    hist="$(_js_json_sub "$json" "history")" || rc=$?
    _js_awk_rc_end
    if [ "$rc" -eq 2 ]; then
        # The EXTRACTOR was UNAVAILABLE — the history array could not be
        # read. An empty extraction is indistinguishable from "the history
        # is gone", and the append below would then REPLACE THE WHOLE
        # HISTORY ARRAY with this one row. Refuse: the history is left
        # untouched.
        _log "WARN: journal history append REFUSED — $jp: the extractor is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC); the history array is left untouched (an empty read would have replaced it)"
        return 1
    fi
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
#
# COOLDOWN ARM (promote-handback-halt Phase 3 — the RIDER). The stamp is
# epoch → ISO, and `date(1)`'s BSD dialect (`date -jur <epoch>`) is
# BSD-ONLY: GNU coreutils REJECTS `-j` outright, so the old single-site
# form fell through to its `|| until="$(_js_now_iso)"` fallback and armed
# a BORN-EXPIRED cooldown — the ADR-005 10-minute anti-flapping window was
# silently DISARMED on every GNU host. Live-corroborated 2026-11-01: the
# `cooldown_until` stamp PREDATED the sweep row that armed it (a
# zero-length cooldown). The arm is now a TWO-ARM `uname` dispatch in this
# file's own idiom (_js_iso_to_epoch / _js_flip_current): ONE `_now()`
# call feeds both arms, so the target instant is computed exactly once.
#
# UNRECOGNIZED PLATFORM (OQ-C-17, ARCHITECT AM-P3-1 — (iii)-scoped): the
# arm writes an explicitly UNPARSEABLE marker — NOT a timestamp and NOT
# "now". The ceremony-side consumer (lib.sh `journal_cooldown_active`)
# reads an unparseable cooldown as ACTIVE (fail-closed, ADR-005) ⇒ the
# anti-flapping window stays ARMED instead of being silently disabled.
# Loud stderr + a journal history row make the refusal visible. (Option
# (i) — writing "now" — is exactly the corrupt state that made the
# incident hard to read; option (ii) — writing nothing — leaves the field
# ABSENT, which the same consumer also reads as ACTIVE but WITHOUT the
# loudness the ruling asks for.)
_js_count_rollback() {
    local jp="$1" json counts cnt wstart wstart_epoch new_cnt until until_epoch rc=0
    _js_awk_rc_begin
    json="$(_js_journal_read "$jp")" || rc=$?
    _js_awk_rc_end
    if [ "$rc" -eq 2 ]; then
        # The journal READ was UNAVAILABLE — the verdict is UNKNOWN; the
        # counter is NOT reset and NO cooldown is armed. Same safe outcome
        # as the extractor refusal below, but LOUD: this arm used to fall
        # into `|| return 1` with NO WARN (silent-refuse class — D2,
        # round-3 sweep).
        _log "WARN: journal rollback counter NOT advanced — $jp: the journal validator is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC); the 24h counter is left untouched"
        return 1
    fi
    [ "$rc" -eq 0 ] || return 1
    _js_awk_rc_begin
    counts="$(_js_json_sub "$json" "rollback_window_count")" || rc=$?
    _js_awk_rc_end
    if [ "$rc" -eq 2 ]; then
        # The EXTRACTOR was UNAVAILABLE — the 24h counter could not be read.
        # An empty extraction is indistinguishable from "the counter is
        # gone", and the `cnt=0` default below would then RESTART THE
        # WINDOW FROM ZERO — which is exactly the shape the ADR-005
        # incident documents ("the cooldown stamp was silently DISARMED").
        # Refuse: the counter is NOT reset and NO cooldown is armed.
        _log "WARN: journal rollback counter NOT advanced — $jp: the extractor is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC); the 24h counter is left untouched (a reset would silently disarm the ADR-005 cooldown)"
        return 1
    fi
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
    # ── the cooldown arm (Phase 3 rider) — see the block comment above ──
    until_epoch="$(( $(_now) + SWEEP_COOLDOWN_S ))"
    case "$(uname -s)" in
        Darwin|*BSD*|*bsd*)
            until="$(date -jur "$until_epoch" +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" \
                || until="$(_js_now_iso)" ;;
        Linux|GNU*|*GNU*)
            until="$(date -u -d "@$until_epoch" +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" \
                || until="$(_js_now_iso)" ;;
        *)
            # (iii)-scoped fail-closed: an UNPARSEABLE marker ⇒ the
            # ceremony-side consumer reads ACTIVE (the anti-flapping window
            # stays armed). NEVER "now" (that DISARMS the window — tonight's
            # defect) and never a fabricated timestamp.
            until="unparseable-unknown-platform"
            echo "_js_count_rollback: unrecognized platform '$(uname -s)' — cooldown_until armed as an UNPARSEABLE marker (fail-closed: the ceremony-side consumer treats it as ACTIVE, so the ADR-005 anti-flapping window stays ARMED)" >&2
            _js_history_append "$jp" "cooldown_unavailable" "unrecognized platform '$(uname -s)' — cooldown_until armed unparseable (fail-closed ACTIVE; the anti-flapping window stays armed)" || true
            ;;
    esac
    _js_journal_update "$jp" "cooldown_until" "\"$until\"" || return 1
    printf '%s' "$new_cnt"
}

# _js_quarantine <path> <ver> — append to quarantined[] (idempotent).
_js_quarantine() {
    local jp="$1" ver="$2" json q new rc=0
    _js_awk_rc_begin
    json="$(_js_journal_read "$jp")" || rc=$?
    _js_awk_rc_end
    if [ "$rc" -eq 2 ]; then
        # The journal READ was UNAVAILABLE — the verdict is UNKNOWN, so
        # the append is REFUSED. Same safe outcome as the extractor
        # refusal below, but LOUD (silent-refuse class — D2, round-3
        # sweep found this arm alongside the two the verifier probed).
        _log "WARN: journal quarantine append REFUSED — $jp: the journal validator is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC); the quarantined[] array is left untouched"
        return 1
    fi
    [ "$rc" -eq 0 ] || return 1
    _js_awk_rc_begin
    q="$(_js_json_sub "$json" "quarantined")" || rc=$?
    _js_awk_rc_end
    if [ "$rc" -eq 2 ]; then
        # The EXTRACTOR was UNAVAILABLE — the quarantine array could not be
        # read. An empty extraction is indistinguishable from "the array is
        # gone", and the append below would then REPLACE THE WHOLE
        # QUARANTINE ARRAY with this one version. Refuse: the array is left
        # untouched.
        _log "WARN: journal quarantine append REFUSED — $jp: the extractor is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC); the quarantined[] array is left untouched (an empty read would have replaced it)"
        return 1
    fi
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
# array as raw text. We DO NOT parse JSON in shell (fragile) — the
# matching itself is delegated (see the body).
#
# F8 (code-council A1, LEADER DECISION: fold in this lane) — THIS READER
# NOW DELEGATES to the per-entry, delimiter-anchored reader. The
# whole-array conjunction described above was the SOURCE of two named
# hazards and it was STILL LIVE on the rung-2 operator-escape path
# (`launcher.sh:1723` pre-fix):
#   (1) PREFIX COLLISION — `target v0.28.5` occurs inside `target
#       v0.28.55`;
#   (2) CROSS-ENTRY FALSE POSITIVE — the event kind in entry A, the
#       target string in entry B.
# On the escape path a cross-entry false positive COMMIT-AND-CONTINUES a
# flip whose authorization evidence names a DIFFERENT target (and a
# prefix collision does the same for a near-miss version). One reader,
# one contract — `_js_history_has_event_for_target`, the same per-entry
# contract the daemon (`history_entry_matches`) and `lib.sh` implement.
_js_history_has_intent_flip() {
    local jp="$1" target="$2"
    # F8 (code-council A1, LEADER DECISION: fold in this lane) — DELEGATES
    # to the per-entry, delimiter-anchored reader
    # `_js_history_has_event_for_target`; the rationale (BOTH named hazards
    # and the rung-2 live path) is in the block comment above this
    # function — ONE reader, ONE contract, the same one the daemon
    # (`history_entry_matches`) and lib.sh implement.
    _js_history_has_event_for_target "$jp" "intent_flip" "$target" ""
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

# _js_sweep_probe_port — resolve the port the SERVING PROBE targets.
#
# F9 (code-council A2) — the probe and the daemon must agree on the port.
# The daemon's EFFECTIVE port is the config.yaml-interpolated value
# (`daemon.port: ${PORT:-8088}`, config.yaml:113): pydantic-settings gives
# the INIT (yaml) source priority over the env source, so `PORT` IS the
# source of truth and `DAEMON_PORT` (DaemonConfig's env_prefix) only takes
# effect when the yaml carries no `daemon.port` key. (Verified empirically
# at the fix cycle: `D(port=8088).port == 8088` with DAEMON_PORT=9999 set.)
# The launcher exports every .env key, so both are visible here.
#
# Prints the port and returns 0 when PORT is usable; returns 1 and prints
# NOTHING when it is unresolvable (the caller's probe then reports the
# `unprobeable` class — F3). A PORT/DAEMON_PORT divergence is logged
# LOUDLY: it silently redirects the daemon's binding to the yaml default
# when the config has no `daemon.port` key. The DAEMON_PORT-UNSET shape is
# logged too (delta re-review A-2, final fold): PORT set + DAEMON_PORT
# unset leaves a custom config.yaml WITHOUT a daemon.port key binding the
# pydantic default (8079) silently while the probe targets PORT — visible,
# not refused (refusing would change the reviewed disposition semantics).
_js_sweep_probe_port() {
    local p="${PORT:-}" d="${DAEMON_PORT:-}"
    # numeric-only: the /dev/tcp path needs a port literal
    case "$p" in ''|*[!0-9]*) p="" ;; esac
    case "$d" in ''|*[!0-9]*) d="" ;; esac
    if [ -n "$p" ] && [ -n "$d" ] && [ "$p" != "$d" ]; then
        _log "WARN: journal sweep: PORT=$p but DAEMON_PORT=$d — the daemon binds the config.yaml-interpolated PORT (the pydantic init source wins over the DAEMON_ env prefix); the probe targets $p. If config.yaml carries no daemon.port key the daemon binds $d instead — reconcile the .env (a wrong probe target makes the serving-green arm silently inert)"
    fi
    if [ -n "$p" ] && [ -z "$d" ]; then
        _log "WARN: journal sweep: PORT=$p with DAEMON_PORT unset — the daemon binds the config.yaml-interpolated PORT only while config.yaml carries a daemon.port key; a custom config.yaml WITHOUT one makes the daemon silently bind the pydantic default (8079) while this probe targets $p (the serving-green arm would be silently inert). Ensure config.yaml carries daemon.port or set DAEMON_PORT=$p in the .env"
    fi
    if [ -n "$p" ]; then
        printf '%s' "$p"
        return 0
    fi
    if [ -n "$d" ]; then
        _log "WARN: journal sweep: PORT is unset while DAEMON_PORT=$d — the daemon's effective port is the config.yaml default, NOT DAEMON_PORT; the serving probe is UNPROBEABLE (never counted, never rollback-eligible). Set PORT in the .env to enable the launcher-side serving-green arm"
    fi
    return 1
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
# TWO HAZARDS are closed by that shape (both were live in the pre-existing
# whole-array reader `_js_history_has_intent_flip`; F8 — the code-council's
# A1, LEADER-DECIDED fold — RETIRED that reader's own matcher: it now
# delegates here, so the rung-2 operator-escape path inherits this
# contract):
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
#
# RC CONTRACT: 0 = the matching entry was found · 1 = not found (a CONTENT
# verdict) · 2 = the reader could not read the history at all (the extractor
# was UNAVAILABLE) — the caller MUST NOT read rc 2 as "not found".
_js_history_has_event_for_target() {
    local jp="$1" event="$2" target="$3" run_id="${4:-}"
    local json hist rest entry t_space t_end r_space r_end rc=0
    [ -n "$target" ] || return 1
    json="$(_js_journal_read "$jp")" || rc=$?
    [ "$rc" -eq 0 ] || return "$rc"
    hist="$(_js_json_sub "$json" "history")" || rc=$?
    if [ "$rc" -eq 2 ]; then
        # The EXTRACTOR was UNAVAILABLE — the history could not be read.
        # Answering "not found" here would be a lie in the UNSAFE
        # direction: this reader carries the operator-authorization
        # evidence, and a false "absent" would let the sweep roll a
        # serving release back. Refuse to answer instead (rc 2).
        return 2
    fi
    case "$hist" in
        ''|null|\[\]) return 1 ;;
    esac
    # O(n) SCAN (launcher-journal-sweep-fix, R4): the replaced loop walked
    # the history ONE ENTRY AT A TIME and each step re-sliced the WHOLE
    # remainder (`rest="${rest#*'{"ts":'}"` + `entry="${rest%%'{"ts":'*}"`)
    # — O(n) per ENTRY ⇒ O(n·entries) = quadratic in BYTES (measured
    # ×3.8–4.1 per doubling; 125 s @ 220 KB under the daemon's UTF-8 env).
    # ONE POSIX awk pass answers the question for ALL entries: the document
    # is split ONCE on the entry marker and each entry is matched with
    # LITERAL substring searches (index()).
    #
    # SEMANTICS UNCHANGED (every quirk preserved):
    #   - the entry boundary is still the LITERAL `{"ts":` marker matched
    #     ANYWHERE (even inside a string value — a detail carrying that
    #     byte sequence still splits an entry, exactly as before);
    #   - the text BEFORE the first marker is still discarded (no marker
    #     at all ⇒ no entries ⇒ "not found", rc 1);
    #   - the event-kind literals keep BOTH forms (compact + spaced);
    #   - the target/run_id checks keep BOTH delimiter anchors (the
    #     space form and the closing-quote form);
    #   - run_id is still skipped when empty;
    #   - "first entry that matches all conjuncts wins" — same order.
    # The literal index() searches are byte-exact equivalents of the
    # replaced `case` globs: every needle in those globs was a quoted
    # expansion (literal already — no glob metacharacters were active).
    # The parameters travel via the ENVIRONMENT (never `awk -v` — no
    # escape sequence can be mangled), under the launcher's single awk
    # seam, which pins LC_ALL=C (see `_js_awk_pass`).
    local _JS_HIST_EVENT="$event" _JS_HIST_TARGET="$target" _JS_HIST_RUN_ID="$run_id"
    export _JS_HIST_EVENT _JS_HIST_TARGET _JS_HIST_RUN_ID
    rc=0
    _js_awk_bounded '
        BEGIN { buf = ""; first = 1 }
        {
            if (first == 1) { buf = $0; first = 0 }
            else buf = buf "\n" $0
        }
        END {
            ev = ENVIRON["_JS_HIST_EVENT"]
            tg = ENVIRON["_JS_HIST_TARGET"]
            rid = ENVIRON["_JS_HIST_RUN_ID"]
            n = split(buf, seg, /\{"ts":/)
            for (i = 2; i <= n; i++) {
                e = seg[i]
                if (index(e, "\"event\":\"" ev "\"") == 0 && \
                    index(e, "\"event\": \"" ev "\"") == 0) continue
                if (index(e, "target " tg " ") == 0 && \
                    index(e, "target " tg "\"") == 0) continue
                if (rid != "") {
                    if (index(e, "run_id " rid " ") == 0 && \
                        index(e, "run_id " rid "\"") == 0) continue
                }
                exit 0
            }
            exit 1
        }
    ' <<<"$hist" || rc=$?
    return "$rc"
}

# _js_history_has_defer_reason <journal> <target> <run_id> <reason>
# Per-(txn, reason) DEFER-row reader (F6): true iff the history already
# carries a `sweep_deferred` row for THIS txn with THIS reason marker.
# Same per-entry, delimiter-anchored contract as the other readers. NOTE
# the anchor shape: the DEFER writer's detail is
#   "sweep: stale flipped txn (target=$target, run_id=$run_id, age=…) —
#    DEFERRED ($reason) — …"
# so the txn anchors are the `=`-form ("target=<ver>,") with the COMMA as
# the delimiter (the comma closes the v0.28.5-inside-v0.28.55 prefix
# collision exactly the way the space does for the space-form writers),
# and the reason marker `DEFERRED (<reason>)` is stable across ticks
# (unlike the row's age field).
#
# RC CONTRACT: 0 = a matching DEFER row exists · 1 = none (a CONTENT
# verdict) · 2 = the reader could not read the history at all (the
# extractor was UNAVAILABLE) — the caller MUST NOT read rc 2 as "none".
_js_history_has_defer_reason() {
    local jp="$1" target="$2" run_id="$3" reason="$4"
    local json hist rest entry t_eq r_eq marker rc=0
    [ -n "$target" ] || return 1
    [ -n "$reason" ] || return 1
    json="$(_js_journal_read "$jp")" || rc=$?
    if [ "$rc" -ne 0 ]; then
        # The journal could not be READ at all (rc 1 = the balance check
        # says the document is NOT TRUSTWORTHY; rc 2 = the validator was
        # UNAVAILABLE). That is NOT "no defer row": the write-once check
        # is UNKNOWN. The rc contract above says the caller MUST NOT read
        # rc 2 as "none" — so a READ failure is reported as rc 2 too (the
        # conservative class), never as the rc-1 content verdict. Before
        # this, a torn journal answered "no defer row yet" and the caller
        # read that as a licence to write (Q1=C).
        return 2
    fi
    hist="$(_js_json_sub "$json" "history")" || rc=$?
    if [ "$rc" -eq 2 ]; then
        # The EXTRACTOR was UNAVAILABLE — the history could not be read, so
        # the write-once check is UNKNOWN. Refuse to answer (rc 2) rather
        # than report "not journaled yet" and write a duplicate row.
        return 2
    fi
    case "$hist" in
        ''|null|'[]') return 1 ;;
    esac
    # O(n) SCAN (launcher-journal-sweep-fix, R4): the same single-pass
    # mechanism as `_js_history_has_event_for_target` (see its block
    # comment) — split ONCE on the `{"ts":` marker, match each entry with
    # literal index() searches. SEMANTICS UNCHANGED: the entry boundary,
    # the discarded pre-first-marker text, the compact+spaced event
    # forms, the `,`-anchored `target=<ver>,` / `run_id=<id>,` txn
    # anchors, the `DEFERRED (<reason>)` marker (stable across ticks) and
    # the run_id-skipped-when-empty rule are all the replaced loop's.
    # LC_ALL=C is pinned at the `_js_awk_pass` seam; the parameters travel
    # via the ENVIRONMENT (never `awk -v`).
    local _JS_HIST_REASON="$reason" _JS_HIST_TARGET="$target" _JS_HIST_RUN_ID="$run_id"
    export _JS_HIST_REASON _JS_HIST_TARGET _JS_HIST_RUN_ID
    rc=0
    _js_awk_bounded '
        BEGIN { buf = ""; first = 1 }
        {
            if (first == 1) { buf = $0; first = 0 }
            else buf = buf "\n" $0
        }
        END {
            rs = ENVIRON["_JS_HIST_REASON"]
            tg = ENVIRON["_JS_HIST_TARGET"]
            rid = ENVIRON["_JS_HIST_RUN_ID"]
            marker = "DEFERRED (" rs ")"
            t_eq = "target=" tg ","
            r_eq = "run_id=" rid ","
            n = split(buf, seg, /\{"ts":/)
            for (i = 2; i <= n; i++) {
                e = seg[i]
                if (index(e, "\"event\":\"sweep_deferred\"") == 0 && \
                    index(e, "\"event\": \"sweep_deferred\"") == 0) continue
                if (index(e, marker) == 0) continue
                if (rid != "" && index(e, r_eq) == 0) continue
                if (index(e, t_eq) == 0) continue
                exit 0
            }
            exit 1
        }
    ' <<<"$hist" || rc=$?
    return "$rc"
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
#
# Design §v relocation: the counter relocates WITH the state file — its
# directory is dirname($STATE_FILE) whenever STATE_FILE is set (runtime
# always sets it, classic or overridden), else the classic $install_dir
# (the sourced/unit-test shape and the byte-identical pre-relocation
# behavior when the override is unset). Callers keep passing install_dir —
# the derivation, not the signature, moved.
_js_defer_counter_path() {
    local install_dir="$1" run_id="$2" dir
    if [ -n "$STATE_FILE" ]; then
        dir="$(dirname "$STATE_FILE")"
    else
        dir="$install_dir"
    fi
    printf '%s/.sweep-defer-%s' "$dir" "$(_js_sanitise_run_id "$run_id")"
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
# — atomic write (temp + mv), the file format pinned by AM-P2-10.2.
#
# F7 (code-council W6, leader-adopted AMEND — the sticky `green=` line is
# DROPPED): the flag was plan-mandated (AM-P2-10.3) but NEVER SET — the
# ladder's increment case covers only absent/mismatch/unready, so `green=`
# was dead weight in the format. The declared mechanism is
# `intent_flip` + the JOURNAL history check below (`serving_green_commit`
# / `quarantine_cleared_serving_green` rows): the ceiling requires
# intent_flip ∧ owner-dead ∧ never-green-observed, so a GREEN-but-
# UNAUTHORIZED txn can never reach the ceiling either (the intent_flip
# conjunct fails) and a GREEN-and-authorized txn is COMMITTED (the
# journal row exists from then on). Nothing safety-relevant is lost.
_js_defer_counter_write() {
    # broken= is carried for format stability (AM-P2-10.2) and never incremented — BROKEN observations fire the chain immediately, bypassing the tally.
    local path="$1" absent="$2" mismatch="$3" unready="$4" broken="$5" tmp
    tmp="$path.tmp.$$"
    {
        printf 'absent=%s\n' "$absent"
        printf 'mismatch=%s\n' "$mismatch"
        printf 'unready=%s\n' "$unready"
        printf 'broken=%s\n' "$broken"
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
# at promote.sh:765 (and will move to the splice helper
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
#   green       — /livez 200 ∧ version == the v-stripped target ∧ /readyz
#                 200 whose body reports ready
#   broken      — /livez ANSWERED with a non-200 status (affirmative
#                 unhealthy: the zero-DB liveness endpoint is broken)
#   absent      — no answer at all (no listener / no response / the version
#                 field absent or unparseable — "cannot tell")
#   mismatch    — /livez 200 but the version ≠ the target (the OLD daemon is
#                 still bound, or the flip's binary never took effect)
#   unready     — /livez 200 + the right version but /readyz is not 200 /
#                 not "ready" (the boot window: MCP warmup + auto-continue)
#   unprobeable — FIX-CYCLE F3 (code-council W1): THE PROBE ITSELF cannot
#                 run (no PORT / a non-numeric or out-of-range PORT). This
#                 is a PROBE-CONFIGURATION failure, NOT a serving
#                 observation: the caller DEFERS, never counts it toward
#                 the absent-ceiling and never treats it as
#                 rollback-eligible. Pre-fix this collapsed into `absent`
#                 (the `no-probe-target` detail) and WAS counted ⇒ three
#                 launcher boots on a host whose .env lacks PORT rolled
#                 back a healthy release. (The daemon-side GREEN predicate
#                 is in-process and needs no port, so the only mitigation
#                 such an install has is the daemon closer.)
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
    local budget="${SWEEP_SERVING_PROBE_BUDGET_S}"
    # H5 (cycle-2 tidier, COUNCIL-CORRECTED shape) — validate the knob
    # BEFORE the arithmetic below (`deadline=$(( $(_now) + budget ))`):
    # a non-numeric value ("0.5", "5x", a stray empty string) is a FATAL
    # arithmetic-syntax error inside $(( )) — it kills the parent shell
    # (the systemd ExecStart, pre-daemon) or, inside the F11 subshell
    # under `set -u`, yields an EMPTY probe class ⇒ rung-0's `*` case
    # executes the rollback chain WITHOUT any serving observation — the
    # F3 punishment class. House idiom: the numeric-only case guard of
    # _js_sweep_probe_port above. Invalid ⇒ loud WARN + the documented
    # default (5s). Valid-but-out-of-range ⇒ clamped into [1, 300] (a
    # huge integer is a ~31-year probe budget — same envelope).
    case "$budget" in
        ''|*[!0-9]*)
            _log "WARN: journal sweep: SWEEP_SERVING_PROBE_BUDGET_S='${SWEEP_SERVING_PROBE_BUDGET_S}' is not a plain integer — using the default (5s); fix the .env"
            budget=5
            ;;
    esac
    if [ "$budget" -lt 1 ]; then
        _log "WARN: journal sweep: SWEEP_SERVING_PROBE_BUDGET_S=${SWEEP_SERVING_PROBE_BUDGET_S} is below the floor — clamping to 1s; fix the .env"
        budget=1
    elif [ "$budget" -gt 300 ]; then
        _log "WARN: journal sweep: SWEEP_SERVING_PROBE_BUDGET_S=${SWEEP_SERVING_PROBE_BUDGET_S} exceeds the 300s cap — clamping to 300s; fix the .env"
        budget=300
    fi
    local v_target deadline status body version rstatus
    v_target="$(_js_serving_version_strip "$target")"
    if [ -z "$v_target" ]; then
        printf 'absent no-probe-target'
        return 1
    fi
    # F3: the probe-configuration checks come FIRST — an unusable port is
    # not an observation of the target.
    case "$port" in
        ''|*[!0-9]*)
            printf 'unprobeable no-port'
            return 1
            ;;
    esac
    if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        printf 'unprobeable port-out-of-range'
        return 1
    fi
    deadline=$(( $(_now) + budget ))
    # F11 (code-council doubt register #2) — THE FD-OPEN AND THE WHOLE
    # PROBE RUN IN A SUBSHELL. Under bash 3.2 with `execfail` ON (macOS /
    # launchd is a supported substrate — scripts/ensemble-prod.plist,
    # Makefile:293) a failing `exec 3<>` CAN KILL THE SHELL: the failure
    # would be LOUD at exactly the "port down" moment the ladder exists
    # for. Isolated in a subshell the `exec` failure kills only the
    # subshell, which then prints the class and exits; the launcher is
    # untouched. The fd-3 protocol (`_js_http_request` reads/writes fd 3)
    # is entirely contained: every consumer runs INSIDE this subshell.
    (
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
    )
}

# _js_quarantine_remove <path> <ver> — remove from quarantined[] (the
# array-rewrite half of the serving-green clearance). Idempotent; a
# missing field is a no-op, not an error.
_js_quarantine_remove() {
    local jp="$1" ver="$2" json q inner item out="" first=1 rc=0
    _js_awk_rc_begin
    json="$(_js_journal_read "$jp")" || rc=$?
    _js_awk_rc_end
    if [ "$rc" -eq 2 ]; then
        # The journal READ was UNAVAILABLE — the verdict is UNKNOWN, so
        # the removal is REFUSED loudly (this arm used to fall into
        # `[ "$rc" -eq 0 ] || return 1` with NO WARN — silent-refuse
        # class, round-3 sweep).
        _log "WARN: journal quarantine removal REFUSED — $jp: the journal validator is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC); the quarantined[] array is left untouched"
        return 1
    fi
    [ "$rc" -eq 0 ] || return 1
    _js_awk_rc_begin
    q="$(_js_json_sub "$json" "quarantined")" || rc=$?
    _js_awk_rc_end
    if [ "$rc" -eq 2 ]; then
        # The extractor was UNAVAILABLE — the array could not be read, so
        # the removal is REFUSED (an empty read would rewrite the array).
        _log "WARN: journal quarantine removal REFUSED — $jp: the extractor is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC); the quarantined[] array is left untouched"
        return 1
    fi
    case "$q" in
        *"\"$ver\""*) ;;
        *) return 0 ;;
    esac
    inner="${q#\[}"
    inner="${inner%\]}"
    local old_ifs="$IFS"
    IFS=','
    # M7 (cycle-2 tidier) — noglob around this word-split: the unquoted
    # $inner relies on the IFS=',' split, and an unquoted expansion ALSO
    # glob-expands — a quarantined[] entry containing glob characters
    # (*, ?, [) would be rewritten against the working directory's file
    # names, silently corrupting the array. `set -f` disables pathname
    # expansion for exactly this loop (quoting $inner would ALSO disable
    # the comma-split the loop exists for).
    set -f
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
    # M7 — restore pathname expansion (paired with the set -f above).
    set +f
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
    #
    # KNOWN ISSUE (recorded here; NO behaviour change): the watchdog's
    # `sleep` child is NOT reaped together with the watcher — the watcher
    # subshell dies, its `sleep` child does not ⇒ every bounded call leaves
    # ONE orphaned `sleep` behind (POK `awk-01-watchdog-process-leak`: a
    # burst of probes exhausts the process table and yields bogus verdict
    # flips). The O(n) journal rewrite (commit 5f8528869) added THREE more
    # bounded awk calls to the boot path (the journal update + the two
    # per-entry history scanners), so the leak RATE per boot-path sweep is
    # marginally higher than before that change. This is a pre-existing,
    # SEPARATE defect (it is not what the size guard bounds); this note
    # records the rate change only.
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
#
# F1 (code-council CRITICAL, fix cycle): the helper's OWN acquisition is
# tracked by `_JS_SWEEP_LOCK_OWNED` — SET at the one self-acquire site in
# `_js_sweep_flipped_disposition` and CLEARED here on release. A blanket
# "owner == $$" test is NOT sufficient: the launcher-boot path holds the
# SAME lock (owner == $$ too — the launcher IS the process) across a
# broader scope, so the helper must release EXACTLY what IT acquired.
#
# Why this is load-bearing: without it the T15 run-loop path leaked the
# lock with owner=$$ (the LAUNCHER pid — alive; the launcher IS the
# systemd ExecStart) ⇒ BOTH stale-break paths need a DEAD owner ⇒ they
# never fire ⇒ every later promote is refused pipeline-busy ⇒ the
# daemon-side closer is refused `lock_busy` on EVERY tick ⇒ with the
# closer inert the launcher ladder accumulates AMBIGUOUS observations ⇒
# after CEILING the auto-rollback chain rolls back + QUARANTINES a
# SERVING-GREEN release — tonight's class, re-armed by its own fix.
#
# The global (not a function-local) is deliberate: the T15 call site in
# `run_loop` carries a belt-and-braces release that is a NO-OP unless the
# helper left the flag set (a future missed exit path degrades to "the
# call site cleans up" instead of "the lock leaks forever").
_JS_SWEEP_LOCK_OWNED=0
_js_sweep_release() {
    local install_dir="$1" lock_held="${2:-1}"
    if [ "$lock_held" = "1" ]; then
        _js_lock_release "$install_dir"
        return 0
    fi
    if [ "${_JS_SWEEP_LOCK_OWNED:-0}" = "1" ]; then
        _js_lock_release "$install_dir"
        _JS_SWEEP_LOCK_OWNED=0
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

# _js_size_guard_defer_decision <journal> <target> <run_id> <reason>
# The BOUNDED defer DECISION for the launcher-side size guard (Q1=C).
#
# RC CONTRACT — the SAME vocabulary as the reader it wraps
# (`_js_history_has_defer_reason`), so the verdict can be handed to
# `_js_serving_defer` unchanged (that helper consumes the reader's rc
# directly):
#   0 — the decision RAN and says "a defer row IS ALREADY journaled for
#       this (txn, reason)" ⇒ the write-once rule applies (nothing to
#       write);
#   1 — the decision RAN and says "NO defer row is journaled yet" ⇒ the
#       caller MAY write one;
#   2 — the decision is UNKNOWN: the extractor was UNAVAILABLE (awk gone /
#       broken), the watchdog fired (the pass wedged past its bound), or
#       the WHOLE decision exceeded its HARD budget. The caller MUST
#       REFUSE TO ACT (write NOTHING). A missing decision must NEVER read
#       as "no defer row" (rc 1) — that is the silent-skip defect this
#       helper exists to close.
#
# WHY A SEPARATE HELPER (and not a bare call at the call site): the
# decision is a CHEAP DECIDE — ONE O(n) per-entry scan of the history (the
# same reader the write-once check uses) — and it is BOUNDED twice:
#   (1) the scan itself runs under the bounded awk seam (`_js_awk_bounded`
#       → `_js_run_bounded $_JS_AWK_TIMEOUT_S`), so a wedged awk can never
#       pin the boot path;
#   (2) the WHOLE decision (journal read + history extraction + scan) is
#       bounded by the SAME hard budget (`$_JS_AWK_TIMEOUT_S` — one knob
#       for the operator and for the tests; it is read at CALL time, so a
#       test that shortens it after sourcing shortens BOTH bounds).
# The elapsed check below is what makes (2) a budget over the WHOLE
# decision: the awk seam bounds each pass individually, but three passes
# could add up to 3× the budget.
#
# The budget is reported in the caller's WARN (the operator must see the
# size AND the budget that was exceeded).
_js_size_guard_defer_decision() {
    local jp="$1" target="$2" run_id="$3" reason="$4"
    local budget="${_JS_AWK_TIMEOUT_S}" t0 t1 rc=0
    t0="$(date +%s 2>/dev/null)" || t0=""
    [ -n "$t0" ] || t0=0
    _js_history_has_defer_reason "$jp" "$target" "$run_id" "$reason" || rc=$?
    t1="$(date +%s 2>/dev/null)" || t1=""
    [ -n "$t1" ] || t1=0
    # rc 2 = the decision could not be made (UNAVAILABLE / wedged / the
    # journal could not be read at all) ⇒ the verdict is UNKNOWN.
    [ "$rc" -le 1 ] || return 2
    # the HARD budget over the whole decision — an over-budget verdict is
    # NOT trusted (the journal may have been rewritten under the scan).
    if [ $(( t1 - t0 )) -ge "$budget" ]; then
        return 2
    fi
    return "$rc"
}

# _js_serving_defer <journal> <target> <run_id> <age> <reason> <detail> [decision]
# The DEFER disposition: a journaled `sweep_deferred` row (a HOLD — it
# closes nothing; it is NOT a terminal event), the txn left OPEN, and NO
# quarantine / counter / cooldown / rollback. The row carries an explicit,
# GREPPABLE reason marker (which conjunct was missing) — matrix row 6.
#
# The OPTIONAL 7th argument carries a PRE-COMPUTED write-once verdict in
# the READER's vocabulary ("0" = a defer row IS already journaled for this
# (txn, reason) · "1" = no defer row is journaled yet). A caller that
# already paid for the bounded decision (the launcher-side size guard —
# Q1=C) passes it here so the O(n) history scan is not paid for twice.
# When the argument is absent the decision is computed here (the
# historical path, unchanged).
_js_serving_defer() {
    local journal="$1" target="$2" run_id="$3" age="$4" reason="$5" detail="$6" decision="${7:-}"
    local drc=0
    # F6 (code-council W5) — WRITE-ONCE per (txn, reason). The defer row is
    # ~150-200B and the T15 hook fires per CHILD EXIT; in a crash loop the
    # journal crossed the launcher-side 98,304B size guard in ~1-2 days,
    # and past that guard this branch DEFERS FOREVER ("the daemon-side
    # closer is the only automatic rollback path") — but in the crash-loop
    # shape the daemon is not alive long enough to run it (it needs
    # stale > 600s PLUS its 300s boot-grace) ⇒ the never-green chain was
    # SILENTLY DISARMED. One row per (txn, reason) bounds the growth by
    # the distinct-reason count; the log line still fires every time so
    # the observation stays visible.
    if [ -n "$decision" ]; then
        drc="$decision"
    else
        _js_history_has_defer_reason "$journal" "$target" "$run_id" "$reason" || drc=$?
    fi
    if [ "$drc" -eq 2 ]; then
        # The history could not be read (extractor UNAVAILABLE) — the
        # write-once check is UNKNOWN. Take the SAFE branch (the txn is
        # left OPEN) but write NO row: the row write would fail the same
        # way, and a duplicate row breaks the write-once bound.
        _log "WARN: journal sweep: the history could not be read (extractor UNAVAILABLE) — the write-once check is UNKNOWN; the txn is left OPEN and NO defer row is written"
        return 0
    fi
    if [ "$drc" -eq 0 ]; then
        _log "journal sweep: stale flipped txn (target=$target, run_id=$run_id, age=${age}s) — DEFERRED ($reason — already journaled for this txn, write-once per (txn, reason)) — txn left OPEN"
        return 0
    fi
    _js_history_append "$journal" "sweep_deferred" \
        "sweep: stale flipped txn (target=$target, run_id=$run_id, age=${age}s) — DEFERRED ($reason) — txn left OPEN; no quarantine, no counter, no cooldown, no rollback; $detail" \
        || { _log "WARN: journal sweep: sweep_deferred history append FAILED"; return 1; }
    _log "journal sweep: stale flipped txn (target=$target, run_id=$run_id, age=${age}s) — DEFERRED ($reason) — txn left OPEN"
    return 0
}

# _js_serving_commit_close <install_dir> <journal> <target> <prev>
# <displaced> <run_id> <age> <owner> <probe_detail> <reason>
# The serving-green COMMIT-CLOSE — AM-P2-1's ordering guarantee: the
# history rows land BEFORE the txn closes (step 3 precedes step 4 — the
# old launcher.sh:1108-1127 order closed the txn first and appended the
# history row after, so a crash in that gap left a CLOSED txn with NO
# history row: an undiscoverable commit). These writes are NOT
# transactional — five independent atomic file rewrites — and a
# mid-sequence crash leaves, by crash point:
#   - after step 1 or 2 — current/previous updated, NO history row, txn
#     OPEN: the next sweep re-evaluates and re-runs this handler; steps
#     1-2 are idempotent rewrites of the same values (the P-19 prefix A
#     shape — converges to the commit, never a rollback);
#   - after step 3 — history row landed, txn OPEN (the P-19 prefix B
#     shape): the next sweep converges the same way; the row is exactly
#     the evidence the crash-prefix pin asserts on;
#   - after step 4 — txn CLOSED, quarantined[] may still name the
#     target: hygiene only — the clearance EVENT already landed, and the
#     amended shell gate accepts the event, not the array.
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
    # M6 (cycle-2 tidier, A6 parity, COUNCIL-CORRECTED scope) — confirm
    # the fields this handler's FIRST TWO writes mutate EXIST in the
    # journal BEFORE the first write (the daemon twin's
    # journal_field_missing arm checks current/previous ONLY; the
    # quarantined[] rewrite below is opportunistic and guarded — the
    # daemon completes a commit without requiring the field, and the
    # launcher twin must not refuse a commit the daemon would complete).
    # `_js_journal_update` REFUSES an absent field (returns 1), so a
    # journal missing `previous` would take the `current` write and then
    # fail mid-sequence — a half-applied commit on a malformed journal.
    # The pre-check refuses the whole commit, loudly, with ZERO writes;
    # the txn stays open and the next sweep retries. The probe mirrors
    # `_js_journal_update`'s OWN existence gate (the same substring
    # test), so it can only refuse what the writer itself would refuse.
    local pre_json pre_missing="" mfield prc=0
    _js_awk_rc_begin
    pre_json="$(_js_journal_read "$journal")" || prc=$?
    _js_awk_rc_end
    if [ "$prc" -eq 2 ]; then
        # The validator was UNAVAILABLE — the journal's trustworthiness is
        # UNKNOWN, so the whole commit is refused (ZERO writes, the txn
        # stays open, the next sweep retries).
        _log "WARN: journal sweep: serving-green commit refused — the journal validator is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC); the journal is left untouched, boot proceeds on current"
        return 1
    fi
    [ "$prc" -eq 0 ] || return 1
    for mfield in current previous; do
        case "$pre_json" in
            *"\"$mfield\""*) ;;
            *) pre_missing="$pre_missing $mfield" ;;
        esac
    done
    if [ -n "$pre_missing" ]; then
        _log "WARN: journal sweep: serving-green commit refused — the journal is missing required field(s):$pre_missing (schema drift — a half-applied commit is worse than a defer); the txn is left untouched, boot proceeds on current"
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

# _js_sweep_operator_escape <install_dir> <journal> <json> <inf> <target>
# <prev> <age> <owner> <kind> <lock_held> <run_id>
#
# RUNG 2 — the PRE-EXISTING boot_sweep_commit_and_continue ESCAPE
# (ADR / upgrade-resilience 2026-10-07), extracted verbatim so the ladder
# and the LEGACY kill-switch arm share ONE implementation (F5: the legacy
# arm must reproduce the BASE order — escape BEFORE chain).
#
# Predicate (ALL must hold): a previous release exists · the journal
# carries per-entry intent_flip evidence for THIS target (F8: the
# per-entry reader, never the whole-array conjunction) · the previous
# manifest declares rollback_safe:false (the operator's deliberate
# incompatibility declaration).
#
# Returns 0 iff it FIRED (the txn is committed-and-continued, or the
# old-format halt ran — the lock is released and the caller must stop);
# returns 1 when the predicate does not hold (the caller continues).
# It ALSO returns 0 when the authorization evidence could not be READ at
# all (the extractor was UNAVAILABLE): an unknown verdict must never fall
# through to the rollback chain, so the caller stops there too.
_js_sweep_operator_escape() {
    local install_dir="$1" journal="$2" json="$3" inf="$4" target="$5"
    local prev="$6" age="$7" owner="$8" kind="$9" lock_held="${10:-1}"
    local run_id="${11:-}"
    local displaced_v
    local ifrc=0
    _js_history_has_intent_flip "$journal" "$target" || ifrc=$?
    if [ "$ifrc" -eq 2 ]; then
        # The authorization evidence could not be read — UNKNOWN, not
        # "absent". Refuse to act: the txn is left OPEN (no
        # commit-and-continue, and — the important half — NO rollback).
        _log "WARN: journal sweep: the authorization evidence could not be read (extractor UNAVAILABLE) — the verdict is UNKNOWN; the txn is left OPEN (no commit-and-continue, no rollback)"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    if [ -n "$prev" ] && [ "$prev" != "null" ] \
       && [ "$ifrc" -eq 0 ] \
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
    return 1
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
    # F1: the helper-owned-lock flag starts CLEAR on every call (see
    # `_js_sweep_release`). The belt-and-braces release at the T15 call
    # site depends on this reset.
    _JS_SWEEP_LOCK_OWNED=0
    local probe_out probe_class probe_detail intent_flip=0 request_present=0
    local counter_path absent mismatch unready broken total
    local jsize
    [ -f "$journal" ] || return 0
    local frc=0
    _js_awk_rc_begin
    json="$(_js_journal_read "$journal")" || frc=$?
    _js_awk_rc_end
    if [ "$frc" -eq 2 ]; then
        _log "WARN: journal sweep: the journal validator is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC) — the balance check did NOT run, so the txn state is UNKNOWN; the txn is left OPEN, boot proceeds"
        return 0
    fi
    [ "$frc" -eq 0 ] || return 0
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
    # _journal_sweep; mirror it here so the T15 hook path agrees). D2a
    # SIGKILL mitigation: a FRESH heartbeat no longer protects a txn whose
    # owner is provably DEAD (the promote refreshes the heartbeat ⇒ it is
    # fresh at a SIGKILL) — owner_dead ALONE is reclaimable. A LIVE owner
    # still defers (a fresh txn belongs to a running promote).
    if [ "$age" -le "$SWEEP_STALE_S" ] && [ "$owner_dead" = "0" ]; then
        return 0
    fi
    # ── the lock ──
    if [ "$lock_held" != "1" ]; then
        if ! _js_lock_acquire "$install_dir"; then
            return 0
        fi
        # F1: record LOCAL ownership — `_js_sweep_release` releases it on
        # EVERY exit path of this helper (and only for lock_held=0).
        _JS_SWEEP_LOCK_OWNED=1
    fi
    # re-read + revalidate UNDER the lock (the launcher's discipline — the
    # txn OBJECT is the identity: any change ⇒ the owner acted ⇒ no-op)
    local json2 inf2 j2rc=0
    _js_awk_rc_begin
    json2="$(_js_journal_read "$journal")" || j2rc=$?
    _js_awk_rc_end
    if [ "$j2rc" -ne 0 ]; then
        if [ "$j2rc" -eq 2 ]; then
            _log "WARN: journal sweep: the journal validator is UNAVAILABLE under the lock (awk rc=$_JS_LAST_AWK_RC) — the re-read could not be validated; refusing to act on the stale snapshot, boot proceeds"
        fi
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
    # M1 (cycle-2 tidier, COUNCIL-CORRECTED shape) — validate the knob
    # LOUDLY before the test, and on invalid SUBSTITUTE THE DOCUMENTED
    # DEFAULT (98304), never skip: the pre-fix `[ ... ] 2>/dev/null`
    # silenced the whole "integer expression expected" error class, and a
    # skip-on-invalid shape would DISARM the guard (fail-open — the
    # never-green protection must stay armed under a poisoned knob; the
    # ratified composition requirement). With the value substituted the
    # test below can no longer error, so the silencer is gone too.
    local jguard="${SWEEP_JOURNAL_SIZE_GUARD_BYTES}"
    case "$jguard" in
        ''|*[!0-9]*)
            _log "WARN: journal sweep: SWEEP_JOURNAL_SIZE_GUARD_BYTES='${SWEEP_JOURNAL_SIZE_GUARD_BYTES}' is not a plain integer — substituting the documented default (98304); fix the .env"
            jguard=98304
            ;;
    esac
    if [ "$jsize" -ge "$jguard" ]; then
        # Q1=C — the over-threshold defer ACTION is BOUNDED. The DECISION
        # (the write-once check: "is a defer row already journaled for
        # this txn?") is a cheap decide — one O(n) history scan under the
        # bounded awk seam — with a HARD budget over the whole decision.
        #
        # If the decision cannot be made (the extractor was UNAVAILABLE,
        # the watchdog fired, or the budget was exceeded) the launcher
        # REFUSES TO ACT: it writes NOTHING (the journal stays byte-
        # identical), it leaves the txn OPEN, and it WARNs with the
        # JOURNAL SIZE and the BUDGET. It NEVER treats a missing decision
        # as "there is no defer row" (rc 1) and never skips silently.
        local ddec=0
        _js_size_guard_defer_decision "$journal" "$target" "$run_id" "journal-size-guard" || ddec=$?
        if [ "$ddec" -eq 2 ]; then
            _log "WARN: journal sweep: journal size=${jsize}B ≥ guard=${jguard}B — defer decision budget (${_JS_AWK_TIMEOUT_S}s) exceeded/unavailable (awk rc=${_JS_LAST_AWK_RC}): REFUSING to act (no journal write; the txn is left OPEN)"
            _js_sweep_release "$install_dir" "$lock_held"
            return 0
        fi
        _log "WARN: journal sweep: journal is ${jsize}B ≥ ${jguard}B (the launcher-side size guard) — CONSERVATIVE DEFER (never a rollback in this regime; the daemon-side closer parses in-process and is the only automatic rollback path)"
        _js_serving_defer "$journal" "$target" "$run_id" "$age" \
            "journal-size-guard" \
            "the journal exceeds the launcher-side size guard — the launcher never rolls back in this regime" \
            "$ddec"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    # ── the KILL-SWITCH (REV-R-3): =0 restores the BASE (pre-Phase-2)
    # disposition (an escape hatch for an unverified platform — NEVER a
    # permanent state).
    #
    # F5 (code-council W4) — the switch used to short-circuit STRAIGHT to
    # the rollback chain, which is NOT "today's disposition": at base the
    # OPERATOR ESCAPE sat BEFORE the chain (base 941cb6321:
    # `launcher.sh:984-1122` escape vs `:1129` chain) — the pre-Phase-2
    # disposition was never unconditional. Consequence pre-fix: with the
    # switch at 0 a stale flipped txn whose prev is rollback_safe:false
    # HALTED FOR HUMAN (the chain's manifest gate) instead of
    # commit-and-continuing — i.e. the switch silently changed the
    # behaviour it documents as restoring. ⇒ the legacy arm is now
    # rung 2 THEN the chain, exactly as at base.
    if [ "${SWEEP_SERVING_GREEN_ENABLED}" = "0" ]; then
        _log "journal sweep: SWEEP_SERVING_GREEN_ENABLED=0 — the serving-green ladder is DISABLED; the LEGACY (base) disposition applies: rung 2 (the operator escape) FIRST, then the auto-rollback chain"
        if _js_sweep_operator_escape "$install_dir" "$journal" "$json" "$inf" "$target" \
             "$prev" "$age" "$owner" "$kind" "$lock_held" "$run_id"; then
            return 0
        fi
        _js_sweep_rollback_chain "$install_dir" "$journal" "$json" "$inf" "$target" \
            "$prev" "$age" "$owner" "$kind" "serving_green_disabled" "$lock_held"
        return 0
    fi
    # ── the authorization evidence (P9) — per-entry + delimiter-anchored ──
    # rc 2 = the history could not be READ (extractor UNAVAILABLE) — the
    # evidence is UNKNOWN, and an unknown verdict must never reach the
    # rollback chain: the txn is left OPEN.
    local evrc=0
    _js_history_has_event_for_target "$journal" "intent_flip" "$target" "" || evrc=$?
    if [ "$evrc" -eq 2 ]; then
        _log "WARN: journal sweep: the authorization evidence could not be read (extractor UNAVAILABLE) — the verdict is UNKNOWN; the txn is left OPEN (no rollback)"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    if [ "$evrc" -eq 0 ]; then
        intent_flip=1
    fi
    # ── the daemon's rollback REQUEST (rung 0) ──
    local rqrc=0
    _js_history_has_event_for_target "$journal" "rollback_requested" "$target" "$run_id" || rqrc=$?
    if [ "$rqrc" -eq 2 ]; then
        _log "WARN: journal sweep: the rollback-request evidence could not be read (extractor UNAVAILABLE) — the verdict is UNKNOWN; the txn is left OPEN (no rollback)"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    if [ "$rqrc" -eq 0 ]; then
        request_present=1
    fi
    # ── the serving probe (rung 1) ──
    # F9: the probe targets the DAEMON's effective port (PORT — the
    # config.yaml interpolation), not a bare `${PORT:-}` that silently
    # degrades into an "absent" observation when the .env lacks it.
    local probe_port
    probe_port="$(_js_sweep_probe_port)" || probe_port=""
    probe_out="$(_js_serving_probe "$probe_port" "$target")" || true
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
            unprobeable)
                # F3: a PROBE-CONFIGURATION failure is NOT evidence of
                # anything about the target — never execute a rollback on
                # it (an UNVERIFIED execution of the daemon's request is
                # exactly tonight's punishment class).
                _log "WARN: journal sweep: the serving probe is UNPROBEABLE ($probe_detail) — a probe-CONFIGURATION failure, not a serving observation; the daemon's rollback_requested is NOT executed this tick (no count, no rollback)"
                _js_serving_defer "$journal" "$target" "$run_id" "$age" \
                    "request-probe-unprobeable-$probe_detail" \
                    "a rollback_requested was seen but the serving probe cannot run ($probe_detail — a probe-configuration failure): the request is NOT executed unverified; no count, no rollback"
                ;;
            *)
                # BROKEN / AMBIGUOUS — the daemon's post-grace verdict is the
                # affirmative evidence ⇒ the EXISTING chain executes.
                _js_sweep_rollback_chain "$install_dir" "$journal" "$json" "$inf" "$target" \
                    "$prev" "$age" "$owner" "$kind" "rollback_requested" "$lock_held"
                ;;
        esac
        # F1: rung 0's GREEN arms commit-close / defer — BOTH must release
        # the lock the helper took (a second release is a no-op).
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    # ── rung 1: the serving probe ──
    if [ "$probe_class" = "green" ]; then
        if [ "$intent_flip" = "1" ] && { [ "$owner_dead" = "1" ] || [ "$hb_stale" = "1" ]; }; then
            _js_serving_commit_close "$install_dir" "$journal" "$target" "$prev" \
                "$(_js_json_field "$inf" "displaced_version" 2>/dev/null)" "$run_id" "$age" \
                "$owner" "$probe_detail" "serving-green"
            # F1: EVERY exit path of the helper releases the lock IT took.
            _js_sweep_release "$install_dir" "$lock_held"
            return 0
        fi
        if [ "$intent_flip" != "1" ]; then
            _js_serving_defer "$journal" "$target" "$run_id" "$age" \
                "green-without-intent-flip" \
                "the target is serving green but there is NO intent_flip evidence — the flip is unauthorized: never commit-close an unauthorized flip, never roll back a serving-green release"
            _notify_once "sweep-defer-unauthorized" \
                "DEFERRED: stale flipped txn (target=$target) is SERVING GREEN but the journal carries NO intent_flip evidence for it — the flip is unauthorized: not committed and not rolled back. The txn stays OPEN for operator resolution — see $install_dir/releases/state.json"
            _js_sweep_release "$install_dir" "$lock_held"
            return 0
        fi
        # UNREACHABLE by construction — AM-P2-18 (:1853) returns on owner-alive ∧ hb-fresh; retained defensively.
        _js_serving_defer "$journal" "$target" "$run_id" "$age" \
            "green-owner-alive" \
            "the target is serving green but its owner is still alive — the txn belongs to a live owner; not the sweep's to dispose of"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    # ── rung 2: the OPERATOR ESCAPE — the extracted helper (the legacy
    # kill-switch arm calls the SAME function; F5).
    if _js_sweep_operator_escape "$install_dir" "$journal" "$json" "$inf" "$target" \
         "$prev" "$age" "$owner" "$kind" "$lock_held" "$run_id"; then
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
    # never-green-observed (AM-P2-10.3, F7-amended): the JOURNAL check — a
    # `serving_green_commit` or `quarantine_cleared_serving_green` row for
    # this target (the AM-P2-1 crash-prefix shape: the event landed with
    # the txn still open). The plan-mandated sticky `green=` counter flag
    # is DROPPED (F7/W6): it was never implemented and `intent_flip` is
    # the declared mechanism (see _js_defer_counter_write).
    local grc=0
    _js_history_has_event_for_target "$journal" "serving_green_commit" "$target" "" || grc=$?
    if [ "$grc" -eq 1 ]; then
        grc=0
        _js_history_has_event_for_target "$journal" "quarantine_cleared_serving_green" "$target" "" || grc=$?
    fi
    if [ "$grc" -eq 2 ]; then
        # The GREEN evidence could not be read — UNKNOWN, not "absent". A
        # green release is never rolled back on an unknown verdict: the
        # txn is left OPEN.
        _log "WARN: journal sweep: the serving-green evidence could not be read (extractor UNAVAILABLE) — the verdict is UNKNOWN; the txn is left OPEN (a green release is never rolled back on an unknown verdict)"
        _js_sweep_release "$install_dir" "$lock_held"
        return 0
    fi
    if [ "$grc" -eq 0 ]; then
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
    # F3 (code-council W1) — a PROBE-CONFIGURATION failure is NOT an
    # observation: no count, no ceiling progress, never rollback-eligible.
    # Placed beside the tempfail arm for the same reason: the two classes
    # are both "the observation tells us nothing about the release".
    case "$probe_class" in
        unprobeable)
            _log "WARN: journal sweep: the serving probe is UNPROBEABLE ($probe_detail) — a probe-CONFIGURATION failure (PORT unset/invalid), NOT a serving observation: not counted toward the absent-ceiling, never rollback-eligible"
            _js_serving_defer "$journal" "$target" "$run_id" "$age" \
                "ambiguous-probe-unprobeable-$probe_detail" \
                "the serving probe cannot run ($probe_detail — a probe-configuration failure: PORT unset or invalid); the observation is NOT counted toward the absent-ceiling and is never rollback-eligible"
            _js_sweep_release "$install_dir" "$lock_held"
            return 0
            ;;
    esac
    case "$probe_class" in
        absent) absent=$(( absent + 1 )) ;;
        mismatch) mismatch=$(( mismatch + 1 )) ;;
        unready) unready=$(( unready + 1 )) ;;
    esac
    total=$(( absent + mismatch + unready ))
    _js_defer_counter_write "$counter_path" "$absent" "$mismatch" "$unready" "$broken" \
        || _log "WARN: journal sweep: defer counter write FAILED ($counter_path) — the tally restarts (bounded, not unbounded)"
    # M1 (cycle-2 tidier, COUNCIL-CORRECTED shape) — validate the ceiling
    # knob LOUDLY before the test, and on invalid SUBSTITUTE THE
    # DOCUMENTED DEFAULT (3), never skip: a non-numeric
    # SWEEP_DEFER_CEILING made bare `[ -ge ]` print a raw "integer
    # expression expected" shell error every sweep AND silently never
    # fire the ceiling (unbounded defers — the W5 shape); a skip-on-
    # invalid shape would leave the never-green bound DISARMED (fail-open
    # — violates the ratified composition requirement).
    local ceiling="${SWEEP_DEFER_CEILING}"
    case "$ceiling" in
        ''|*[!0-9]*)
            _log "WARN: journal sweep: SWEEP_DEFER_CEILING='${SWEEP_DEFER_CEILING}' is not a plain integer — substituting the documented default (3); fix the .env"
            ceiling=3
            ;;
    esac
    if [ "$total" -ge "$ceiling" ]; then
        _log "journal sweep: the BOUNDED ABSENT-CEILING reached (qualifying observations=$total ≥ ceiling=$ceiling; owner-dead ∧ intent_flip ∧ never-green-observed) — the existing auto-rollback chain fires"
        _js_sweep_rollback_chain "$install_dir" "$journal" "$json" "$inf" "$target" \
            "$prev" "$age" "$owner" "$kind" "absent-ceiling" "$lock_held"
        return 0
    fi
    _js_serving_defer "$journal" "$target" "$run_id" "$age" \
        "ambiguous-$probe_class" \
        "qualifying observations=$total of ceiling=$ceiling — deferred; the rollback fires only when the ceiling predicate holds"
    _js_sweep_release "$install_dir" "$lock_held"
    return 0
}

_journal_sweep() {
    local install_dir="${1:-${INSTALL_DIR:-}}"
    [ -n "$install_dir" ] || return 0
    local journal="$install_dir/releases/state.json"
    [ -f "$journal" ] || return 0

    local json jrc=0
    _js_awk_rc_begin
    json="$(_js_journal_read "$journal")" || jrc=$?
    _js_awk_rc_end
    if [ "$jrc" -eq 2 ]; then
        # The VALIDATOR was UNAVAILABLE — the balance check NEVER RAN, so
        # the journal's trustworthiness is UNKNOWN. NEVER report this as
        # "unbalanced": the check did not fail, it did not run.
        _log "WARN: journal sweep: $journal — the journal validator is UNAVAILABLE (awk rc=$_JS_LAST_AWK_RC): the balance check did NOT run, so the journal is UNKNOWN (this is NOT 'unbalanced'); refusing to trust it, boot proceeds"
        return 0
    fi
    if [ "$jrc" -ne 0 ]; then
        # The check that ran is a BRACKET/STRING BALANCE CHECK — not a JSON
        # parse (the daemon's side requires parseable JSON). The message
        # names the check that actually ran, so an operator reading it does
        # not mistake a balance failure for a parse failure.
        _log "WARN: journal sweep: $journal unreadable or torn (balance-check only — not a JSON parse) — refusing to trust it, boot proceeds"
        return 0
    fi

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
    # UNLESS a liveness fast path fires. TWO fast paths:
    #   comp3 (original): hb_stale ∧ owner_dead → reclaim now.
    #   comp3-owner-dead (D2a SIGKILL mitigation, 2026-11-11): the promote
    #     refreshes the heartbeat every ~30s ⇒ at a SIGKILL inside the window
    #     the heartbeat is < 300s old ⇒ hb_stale=0 ⇒ the ORIGINAL comp3 does
    #     NOT fire ⇒ the sweep "leaves alone" ⇒ the launcher boots the FLIPPED
    #     (possibly-broken) current SILENTLY. But the owner pid is DIRECTLY
    #     checkable and provably DEAD ⇒ treat owner_dead ALONE as reclaimable,
    #     after a short BOUNDED confirmation wait (re-check liveness so a
    #     transient /proc hiccup cannot false-reclaim). The heartbeat-age bound
    #     at kill is exactly "< 300 s" (HEARTBEAT_STALE_S=300); typical
    #     heartbeat age at SIGKILL is ≤30s (the promote heartbeat cadence), but
    #     a stalled promote can reach just-under 300s — the point is that
    #     hb_stale stays 0 either way, so owner-dead must carry the decision.
    if [ "$age" -le "$SWEEP_STALE_S" ]; then
        if [ "$hb_stale" = "1" ] && [ "$owner_dead" = "1" ]; then
            _log "journal sweep: comp3 fast path triggered (hb_stale=1 owner_dead=1 age=${age}s ≤ SWEEP_STALE_S=${SWEEP_STALE_S}s) — reclaiming (flipped=${flipped:-—})"
        elif [ "$owner_dead" = "1" ]; then
            # Bounded confirmation: owner_dead is only trustworthy if it STAYS
            # dead across a short re-check (a fork/exec hiccup could hide it
            # briefly). ~3s; a live owner aborts the reclaim (DEFER).
            _confirm=1
            for _cw in 1 2 3; do
                sleep 1
                if [ -n "$owner" ] && printf '%s' "$owner" | grep -Eq '^[0-9]+$' \
                   && _js_pid_alive_bounded "$owner" 2>/dev/null; then
                    _confirm=0; break
                fi
            done
            if [ "$_confirm" = "1" ]; then
                _log "journal sweep: comp3-owner-dead fast path triggered (owner_dead=1 hb_stale=$hb_stale — heartbeat age ${age}s < HEARTBEAT_STALE_S=${HEARTBEAT_STALE_S}s, owner pid ${owner:-<none>} stayed dead across the bounded re-check) — reclaiming (SIGKILL-in-window mitigation)"
            else
                _log "journal sweep: in_flight $kind txn (target=${target:-?}) owner ${owner} re-appeared during the bounded re-check — leaving alone (DEFER)"
                return 0
            fi
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
    local json2 inf2 j2rc=0
    _js_awk_rc_begin
    json2="$(_js_journal_read "$journal")" || j2rc=$?
    _js_awk_rc_end
    if [ "$j2rc" -ne 0 ]; then
        if [ "$j2rc" -eq 2 ]; then
            _log "WARN: journal sweep: the journal validator is UNAVAILABLE under the lock (awk rc=$_JS_LAST_AWK_RC) — the re-read could not be validated; refusing to act on the stale snapshot, boot proceeds"
        else
            _log "WARN: journal sweep: journal became unreadable under the lock — refusing to act on the stale snapshot, boot proceeds"
        fi
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
    # ── the launcher-side size guard, applied to the PRE-FLIP branch (Q2) ──
    # The FLIPPED branch has carried this guard since AM-P2-5 (a journal at
    # or above the guard is a CONSERVATIVE DEFER: the launcher never rolls
    # back in that regime — the daemon-side closer parses the journal
    # in-process and is the only automatic rollback path). The PRE-FLIP
    # branch had NO guard: it still appended a history row and rewrote
    # `in_flight` on a journal of ANY size — two full-journal rewrites on
    # exactly the shape (a big journal) the guard exists to keep the
    # launcher's shell-side writers away from. The launcher-side writers
    # are the O(n)-per-byte cost; the daemon-side closer is in-process.
    #
    # DISCLOSURE (release notes): at or above the launcher-side size guard
    # (98304B by default) the launcher DECLINES TO ACT on a stale promote
    # (flip) transaction — no launcher rollback, no commit-and-continue and
    # no txn clear — so a stale flip transaction in such a journal is left
    # to the daemon-side closer. The transaction stays OPEN; the next boot
    # (or the daemon-side closer) disposes of it.
    #
    # Below the guard the behaviour is BYTE-IDENTICAL to the pre-fix
    # behaviour (the clear still happens, with the same history row).
    local pjsize="${#json}" pjguard="${SWEEP_JOURNAL_SIZE_GUARD_BYTES}"
    case "$pjguard" in
        ''|*[!0-9]*)
            _log "WARN: journal sweep: SWEEP_JOURNAL_SIZE_GUARD_BYTES='${SWEEP_JOURNAL_SIZE_GUARD_BYTES}' is not a plain integer — substituting the documented default (98304); fix the .env"
            pjguard=98304
            ;;
    esac
    if [ "$pjsize" -ge "$pjguard" ]; then
        _log "WARN: journal sweep: journal is ${pjsize}B ≥ ${pjguard}B (the launcher-side size guard) — DECLINING TO ACT on the stale pre-flip $kind txn (target=${target:-?}, age ${age}s): no launcher rollback and no commit-and-continue — a stale flip transaction in a ≥${pjguard}B journal is left to the daemon-side closer; the txn is left OPEN"
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
# $STATE_FILE — tiny key=value file, atomic write (tmp+mv). Defaults to
# INSTALL_DIR/.launcher-state; relocatable via ENSEMBLE_LAUNCHER_STATE_FILE
# (design §v — assigned in main() AFTER load_env_file so the override can
# come from the install .env; default byte-identical when unset).
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
        # F1 belt-and-braces (code-council CRITICAL): the helper releases
        # the lock IT acquired on every exit path; this second call is a
        # NO-OP unless the helper left the ownership flag set (a future
        # missed exit path degrades to "the call site cleans up" instead
        # of "the lock leaks with owner=$$ — alive — forever").
        _js_sweep_release "$INSTALL_DIR" 0
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

    # Env first (ADR-014): INSTALL_DIR/.env staged from repo .env.prod.
    # Exports win over the binary's own .env load (run_app.py only sets
    # unset vars) — precedence is structural.
    _log "ensemble launcher starting (INSTALL_DIR=$INSTALL_DIR)"
    load_env_file "$INSTALL_DIR/.env"

    # STATE_FILE (design §v relocation): default UNCHANGED —
    # "$INSTALL_DIR/.launcher-state" (the VM's crash-loop burst budget
    # depends on this path staying exactly where it is when the override
    # is unset). Assigned AFTER load_env_file — NOT before it — because
    # the override must be readable from the install .env: the Stage-2
    # wiring writes ENSEMBLE_LAUNCHER_STATE_FILE into INSTALL_DIR/.env
    # (relocating state into the writable set of a root-owned top dir).
    # Precedence (F-07): .env value → inherited env → default (the := fires
    # only when neither source set it; load_env_file's export already beat
    # the inherited value for keys present in .env — ADR-014 discipline).
    # The .env side is LAST-wins (the loader exports sequentially, so the
    # last export wins) and an EMPTY .env value clears the inherited value
    # → the := default fires. scripts/watchdog-watcher.sh resolves the SAME
    # file with the SAME rules (and the SAME value-resolution rule for
    # trailing whitespace / inline comments — F-06); the parity rows in
    # tests/test_watchdog_watcher.sh pin the agreement.
    # Pre-relocation this line sat BEFORE load_env_file; nothing between
    # the cd and the env load consumed STATE_FILE, so the move is
    # behavior-neutral for the unset case. Exported so every run_loop
    # child inherits it (same pattern as MALLOC_ARENA_MAX below).
    : "${ENSEMBLE_LAUNCHER_STATE_FILE:=$INSTALL_DIR/.launcher-state}"
    export ENSEMBLE_LAUNCHER_STATE_FILE
    STATE_FILE="$ENSEMBLE_LAUNCHER_STATE_FILE"

    # Allocator cap (resource-cost-control O1, 2026-10-18): default glibc
    # to 2 malloc arenas BEFORE the daemon child starts. MALLOC_ARENA_MAX
    # is a libc tunable parsed at libc init — before the child's main() —
    # so it must arrive via the environment, not an in-process setdefault.
    # Default-only (setdefault semantics): a MALLOC_ARENA_MAX staged in
    # .env (already exported above) wins over this default. Exported here
    # in main(), so every `run_loop` cycle's child inherits it.
    : "${MALLOC_ARENA_MAX:=2}"
    export MALLOC_ARENA_MAX

    # Supervisor-env PATH (design §iv Layer 2 — see the _path_* family):
    # probe the inherited PATH, no-op when npx/uvx/node already resolve
    # (the VM case — systemd's default PATH carries them), otherwise
    # synthesize from stable anchors + the dynamic nvm default. Must run
    # AFTER load_env_file (an .env-staged ENSEMBLE_LAUNCHER_PATH takes the
    # same precedence) and BEFORE _journal_sweep/binary resolve so the
    # daemon child inherits the resolved PATH.
    _path_ensure

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
