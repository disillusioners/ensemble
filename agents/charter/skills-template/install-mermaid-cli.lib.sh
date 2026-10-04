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
#   - if present → another process holds it (we don't try to read or
#     own the lock from the probe; we just report it's there)
#
# The acquire function (charter_acquire_install_lock) is the place that
# actually claims the lock. The probe is just a "is someone else in
# the install path right now?" check.
_charter_lock_held() {
    local lockfile="$HOME/.cache/charter/mermaid-install.lock"
    if [ -e "$lockfile" ]; then
        return 0
    fi
    return 1
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
# counter used by inline-install gating — arch-rec §3 amendment #18).
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
