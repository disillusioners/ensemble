#!/usr/bin/env bash
# install-mermaid-cli.lib.sh — pure bash + jq library sourced by
# charter/workflow.md Step 5 (render path). Single source of truth
# for the 4-signal READINESS_PROBE (arch-rec §3 amendment #17).
#
# The lib is INTERPRETED — it is sourced on every render so it must
# stay cheap (no compiled deps, no daemon code, fits Phase A's
# agent-prompt-only fence). Companion to install-mermaid-cli.md
# (the install SKILL loaded on cold-detect).
#
# FENCES (per install-opendesign pattern, job f9946b1d):
#   - Pure bash + jq — no python, no compiled deps
#   - Idempotent: sourcing on every render is a cheap jq-read + stat-walk
#   - BSD portability: degrades gracefully (no hard flock/sed -i GNU-isms)
#
# Export contract (single source of truth — both the skill body and
# workflow.md Step 5 read these):
#   charter_readiness_probe  — returns 0 (warm), 1 (cold), 2 (install-in-progress-other)
#   MMDC_BIN                  — absolute path to mmdc (set on success/rc=0)
#   PUPPETEER_EXECUTABLE_PATH — absolute path to chromium (set on success/rc=0)
#   charter_verify_toolchain  — evidence-gate for the install skill: renders a
#                               minimal diagram under the exact render contract
#                               (sanitizer + ulimit -v + timeout 60 + security
#                               pin + sandboxed-first launch); returns 0 only on
#                               real non-empty SVG+PNG evidence
#
# Author: Phase A implementation, chart-image-delivery commission
# Companion: install-mermaid-cli.md (same dir)

# --- Config file location (per arch-rec §3 amendment #17, signal 1) ---
CHARTER_PUPPETEER_CONFIG="${CHARTER_PUPPETEER_CONFIG:-$HOME/.config/charter-mermaid-puppeteer.json}"

# --- charter_readiness_probe ---------------------------------------------
# 4-signal contract (arch-rec §3 amendment #17):
#   1. config file exists + is valid JSON
#   2. mmdcPath (from config) is absolute + executable
#   3. puppeteerConfig.executablePath (from config) is absolute + executable
#      — re-probed at probe time, NOT trusted from config-write time
#   4. pinned mermaidCli major version matches (config.mermaidCli == 12)
#
# Returns 0 on warm (all 4 signals pass), 1 on cold (any signal fails),
# 2 on install-in-progress-other (lock held by another process).
# On rc=0, MMDC_BIN and PUPPETEER_EXECUTABLE_PATH are exported.
charter_readiness_probe() {
    # Lock detection first (signal 5 — install-in-progress-other).
    # flock is GNU; BSD fallback uses mkdir-based lock (never hard-fail).
    if _charter_lock_held; then
        return 2
    fi

    # Signal 1 — config file exists + is valid JSON
    if [ ! -f "$CHARTER_PUPPETEER_CONFIG" ]; then
        return 1
    fi
    if ! jq -e . "$CHARTER_PUPPETEER_CONFIG" >/dev/null 2>&1; then
        return 1
    fi

    # Signal 2 — mmdcPath absolute + executable
    local mmdc_path
    mmdc_path=$(jq -r '.mmdcPath // ""' "$CHARTER_PUPPETEER_CONFIG" 2>/dev/null)
    if [ -z "$mmdc_path" ] || [ "${mmdc_path:0:1}" != "/" ] || [ ! -x "$mmdc_path" ]; then
        return 1
    fi

    # Signal 3 — puppeteerConfig.executablePath absolute + executable
    # RE-PROBED at probe time (arch-rec §3 amendment #17) — chromium may
    # have moved between config-write and probe (cache evict, manual edit,
    # upgrade). Probing on every render survives moves.
    local puppeteer_path
    puppeteer_path=$(jq -r '.puppeteerConfig.executablePath // ""' "$CHARTER_PUPPETEER_CONFIG" 2>/dev/null)
    if [ -z "$puppeteer_path" ] || [ "${puppeteer_path:0:1}" != "/" ] || [ ! -x "$puppeteer_path" ]; then
        return 1
    fi

    # Signal 4 — pinned mermaidCli major version matches (== 12)
    local pinned_major
    pinned_major=$(jq -r '.mermaidCli // ""' "$CHARTER_PUPPETEER_CONFIG" 2>/dev/null)
    if [ "$pinned_major" != "12" ]; then
        return 1
    fi

    # Warm — export the env vars workflow.md reads.
    MMDC_BIN="$mmdc_path"
    PUPPETEER_EXECUTABLE_PATH="$puppeteer_path"
    export MMDC_BIN
    export PUPPETEER_EXECUTABLE_PATH
    return 0
}

# --- _charter_lock_held --------------------------------------------------
# Internal helper: returns 0 if install lock is held by another process,
# 1 if not held. BSD-portable (mkdir-based lock on systems without
# flock, since `flock -n file` creates the file if missing — wrong
# semantics for a probe). Never a hard failure — this is advisory.
#
# Lock file: $HOME/.cache/charter/mermaid-install.lock
#   - if absent → no lock held
#   - if present AND fresh (younger than the staleness window) → another
#     process holds it (we don't try to read or own the lock from the
#     probe; we just report it's there)
#   - if present AND STALE → a crashed install left it behind. Treat as
#     not held and remove it. Without this check a mid-install crash
#     wedges every future probe into rc=2 — a permanent warm-host
#     degrade (text-only forever, self-heal never re-fires).
#
# Staleness window: CHARTER_LOCK_STALE_SECS, default 1800s (30 min) —
# it MUST comfortably exceed the worst-case cold-install duration
# (nvm + Node + ~150 MB chromium) so a LIVE install is never stolen;
# lower it only with that in mind. `find -mmin` is GNU+BSD portable.
#
# The acquire function (charter_acquire_install_lock) is the place that
# actually claims the lock. The probe is just a "is someone else in
# the install path right now?" check.
CHARTER_LOCK_STALE_SECS="${CHARTER_LOCK_STALE_SECS:-1800}"
_charter_lock_held() {
    local lockfile="$HOME/.cache/charter/mermaid-install.lock"
    if [ ! -e "$lockfile" ]; then
        return 1
    fi
    local stale_min=$(( (CHARTER_LOCK_STALE_SECS + 59) / 60 ))
    if [ -n "$(find "$lockfile" -mmin +"$stale_min" 2>/dev/null)" ]; then
        echo "WARN: stale install lock (older than ${CHARTER_LOCK_STALE_SECS}s) — treating as abandoned"
        rm -f "$lockfile" 2>/dev/null || true
        return 1
    fi
    return 0
}

# --- charter_acquire_install_lock ---------------------------------------
# Advisory 10s lock acquisition used by workflow.md's flock-guarded
# self-heal path. If contended, the caller should log
# `install_in_progress_other`, write $HOME/.cache/charter/mermaid-pending-install,
# and degrade this turn.
# Returns 0 on acquired, 1 on contended.
#
# Implementation: create the lock file atomically. If we created it,
# we own the lock. If `set -C` (noclobber) blocks us, someone else
# holds it. This is POSIX-portable — no GNU flock required.
charter_acquire_install_lock() {
    local lockfile="$HOME/.cache/charter/mermaid-install.lock"
    mkdir -p "$(dirname "$lockfile")"
    # Atomic create-only: set -C / noclobber. If the file already
    # exists, redirect fails and we report contended.
    ( set -C; : > "$lockfile" ) 2>/dev/null
}

# --- charter_release_install_lock ---------------------------------------
charter_release_install_lock() {
    local lockfile="$HOME/.cache/charter/mermaid-install.lock"
    rm -f "$lockfile" 2>/dev/null || true
}

# --- charter_write_pending_install_marker -------------------------------
# Writes the async queue marker $HOME/.cache/charter/mermaid-pending-install
# so the next turn's probe re-evaluates. The marker is removed by
# charter_clear_pending_install_marker once a warm probe succeeds.
charter_write_pending_install_marker() {
    : > "$HOME/.cache/charter/mermaid-pending-install"
}

charter_clear_pending_install_marker() {
    rm -f "$HOME/.cache/charter/mermaid-pending-install" 2>/dev/null || true
}

# --- charter_read_install_session_state ---------------------------------
# Reads $HOME/.cache/charter/mermaid-session-state.json (cold_misses_in_session
# counter — the amendment #18 self-heal attempt cap). The counter is bumped
# on every cold-detect self-heal and cleared on a successful install; once
# it passes the cap, the cold path stops attempting installs for the
# session (write async marker + degrade) instead of flailing on a host
# where the install can never succeed.
# Returns the int cold_misses count, or 0 on miss.
charter_read_install_session_state() {
    local statefile="$HOME/.cache/charter/mermaid-session-state.json"
    if [ -f "$statefile" ]; then
        jq -r '.cold_misses_in_session // 0' "$statefile" 2>/dev/null
        return
    fi
    echo "0"
}

# --- charter_bump_install_session_state ---------------------------------
# Increments cold_misses_in_session (persists across turns; cleared on
# warm probe).
charter_bump_install_session_state() {
    local statefile="$HOME/.cache/charter/mermaid-session-state.json"
    local current
    current=$(charter_read_install_session_state)
    mkdir -p "$(dirname "$statefile")"
    jq -n --argjson n "$current" '{cold_misses_in_session: ($n + 1)}' > "$statefile" 2>/dev/null || true
}

charter_clear_install_session_state() {
    rm -f "$HOME/.cache/charter/mermaid-session-state.json" 2>/dev/null || true
}

# --- _charter_mmdc_render ------------------------------------------------
# Single home of the VERIFY render contract (mirrors workflow.md Step 5.5
# byte-for-byte — when one changes, the other must change with it):
#   - sanitized .mmd input (the caller sanitizes; see workflow.md Step 5.3)
#   - ( ulimit -v 2097152; timeout 60 ... ) memory + wall-clock bounds
#   - -c security pin strict + htmlLabels:false
#   - sandboxed launch is the DEFAULT; --no-sandbox is applied ONLY as a
#     logged single fallback when the sandboxed launch fails with the
#     chromium sandbox signature (arch-rec §3 amendment #15)
#
# usage: _charter_mmdc_render <mmdc_bin> <cfg_file> <in.mmd> <out_file>
# Prints the render log; returns the render rc.
_charter_mmdc_render() {
    local mmdc_bin="$1" cfg="$2" in_mmd="$3" out_file="$4"
    local log rc cfg_fb
    log=$( ( ulimit -v 2097152 2>/dev/null; timeout 60 "$mmdc_bin" \
        -i "$in_mmd" \
        -o "$out_file" \
        -t default -b white -w 1200 -s 2 \
        -c '{"securityLevel":"strict","htmlLabels":false}' \
        --puppeteerConfigFile "$cfg" \
        --quiet \
    ) 2>&1 )
    rc=$?
    printf '%s\n' "$log"
    [ "$rc" -eq 0 ] && return 0

    # Sandbox launch-failure fallback — ONE retry with --no-sandbox,
    # logged. This is the ONLY sanctioned render-side retry (launch
    # failure ≠ syntax/render failure; the Never rule is untouched).
    if printf '%s' "$log" | grep -qi \
        "no usable sandbox\|sandbox was unable\|running as root without --no-sandbox"; then
        echo "WARN: sandboxed launch failed — retrying once WITH --no-sandbox (logged fallback, amendment #15)"
        cfg_fb=$(mktemp "${TMPDIR:-/tmp}/charter-verify-cfg.XXXXXX") || return "$rc"
        jq '.puppeteerConfig.args = ["--no-sandbox"]' "$cfg" > "$cfg_fb" 2>/dev/null
        ( ulimit -v 2097152 2>/dev/null; timeout 60 "$mmdc_bin" \
            -i "$in_mmd" \
            -o "$out_file" \
            -t default -b white -w 1200 -s 2 \
            -c '{"securityLevel":"strict","htmlLabels":false}' \
            --puppeteerConfigFile "$cfg_fb" \
            --quiet \
        ) 2>&1
        rc=$?
        rm -f "$cfg_fb" 2>/dev/null || true
    fi
    return "$rc"
}

# --- charter_verify_toolchain --------------------------------------------
# Evidence, not claims: render a minimal flowchart to BOTH out.svg and
# out.png under the exact render contract, then require non-empty
# artifacts and a real PNG (file(1) magic check). Returns 0 only on
# real evidence.
#
# usage: charter_verify_toolchain <mmdc_bin> <cfg_file>
#   <cfg_file> carries puppeteerConfig.executablePath (chromium path).
#   On a sandbox launch failure the function flips that config's args to
#   ["--no-sandbox"] for the single logged fallback attempt (amendment #15).
#
# The install skill gates config promotion on this function: a broken or
# partial install NEVER reaches the probe-visible config file, so the
# 4-signal probe stays cold and the next render re-fires the install.
charter_verify_toolchain() {
    local mmdc_bin="$1" cfg="$2"
    [ -x "$mmdc_bin" ] || return 1
    [ -f "$cfg" ] || return 1
    local chrome_bin
    chrome_bin=$(jq -r '.puppeteerConfig.executablePath // ""' "$cfg" 2>/dev/null)
    [ -n "$chrome_bin" ] && [ -x "$chrome_bin" ] || return 1

    local vdir
    vdir=$(mktemp -d) || return 1
    printf 'flowchart TD\n    A-->B\n' > "$vdir/test.mmd"

    # Sanitize — same pass as the render path (workflow.md Step 5.3).
    sed -E \
        -e '/^%%\{init\}%%$/,/^%%\{init\}%%$/d' \
        -e '/^[[:space:]]*securityLevel:[[:space:]]*/d' \
        "$vdir/test.mmd" > "$vdir/test.san.mmd" \
        && mv "$vdir/test.san.mmd" "$vdir/test.mmd"

    _charter_mmdc_render "$mmdc_bin" "$cfg" "$vdir/test.mmd" "$vdir/out.svg" \
        || { rm -rf "$vdir"; return 1; }
    _charter_mmdc_render "$mmdc_bin" "$cfg" "$vdir/test.mmd" "$vdir/out.png" \
        || { rm -rf "$vdir"; return 1; }

    if [ ! -s "$vdir/out.svg" ] || [ ! -s "$vdir/out.png" ]; then
        rm -rf "$vdir"
        return 1
    fi
    if command -v file >/dev/null 2>&1; then
        file "$vdir/out.png" 2>/dev/null | grep -q "PNG image data" \
            || { rm -rf "$vdir"; return 1; }
    fi
    rm -rf "$vdir"
    return 0
}
