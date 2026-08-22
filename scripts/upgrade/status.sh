#!/bin/bash
# ============================================================================
# scripts/upgrade/status.sh — read-only status of a staged install (P2.1 T1/T3)
# ============================================================================
# Prints the resolved env triple (D-FA4.6), the journal (releases/state.json)
# state, the release inventory, lock state, and (when the daemon answers) the
# running version vs the current manifest's binary_version.
#
# USAGE:
#   bash scripts/upgrade/status.sh [demo|live|sandbox] [--verify]
#   TARGET=sandbox INSTALL_DIR=/tmp/ens-sbx PORT=8377 bash scripts/upgrade/status.sh
#
# --verify: integrity mode — verify the CURRENTLY POINTED release against its
# manifest (per-file sha256, D-FA4.4), the current symlink resolution, the
# no-.env invariant, and (if the daemon is up) the /livez version smoke
# (D2/ADR-027). Exit 0 clean; exit 1 naming the offending file(s) on mismatch.
#
# NEVER mutates anything (read-only — no lock, no journal write). Live target
# requires ENSEMBLE_UPGRADE_LIVE=1 else exit 78 (observation-only would be
# safe, but one rule for the whole suite is simpler to audit).
#
# EXIT CODES: 0 ok · 1 verification mismatch · 78 config refuse (unknown
# target / unresolved sandbox / unconfirmed live).
# ============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_TAG="upgrade-status"

TARGET_ARG=""
VERIFY=0
for arg in "$@"; do
    case "$arg" in
        demo|live|sandbox) TARGET_ARG="$arg" ;;
        --verify) VERIFY=1 ;;
        -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "status: unknown flag '$arg' — usage: status.sh [demo|live|sandbox] [--verify]" >&2; exit 78 ;;
    esac
done

# shellcheck source=scripts/upgrade/lib.sh
. "$SCRIPT_DIR/lib.sh"

resolve_env "${TARGET_ARG:-${TARGET:-demo}}"
require_live_guard "$UP_TARGET"
echo_env_triple

# ── journal state ───────────────────────────────────────────────────────────
JOURNAL_PATH="$(journal_path)"
if [ -f "$JOURNAL_PATH" ]; then
    if J="$(journal_read)"; then
        _log "journal ($JOURNAL_PATH):"
        _logv "$J"
    else
        _warn "journal exists but is UNREADABLE/TORN — treat as halt-for-human (pipeline mutations will refuse)"
        VERIFY_RC_NOTE=1
    fi
else
    _log "journal: none at $JOURNAL_PATH (staged mode not initialized — run stage.sh)"
fi

# ── release inventory ───────────────────────────────────────────────────────
REL_DIR="$INSTALL_DIR/releases"
if [ -d "$REL_DIR" ]; then
    _log "releases:"
    for d in "$REL_DIR"/*/; do
        [ -d "$d" ] || continue
        name="${d%/}"; name="${name##*/}"
        rb="$(manifest_field "$name" rollback_safe 2>/dev/null)"
        q=""; journal_is_quarantined "$name" 2>/dev/null && q=" [QUARANTINED]"
        printf '    %-24s rollback_safe=%s%s\n' "$name" "${rb:-?}" "$q"
    done | sort
else
    _log "releases: none (install dir not in staged mode)"
fi

# ── current symlink ─────────────────────────────────────────────────────────
if [ -L "$REL_DIR/current" ]; then
    cur_target="$(readlink "$REL_DIR/current")"
    _log "current -> $cur_target"
    if [ ! -d "$REL_DIR/${cur_target##*/}" ]; then
        _warn "current symlink DANGLING (target release missing — layout divergence, mutations frozen per D-FA5.3)"
    fi
else
    _log "current symlink: none"
fi

# ── lock state ──────────────────────────────────────────────────────────────
LOCK="$(lock_dir_path)"
if [ -d "$LOCK" ]; then
    _log "pipeline lock: HELD (owner=$(cat "$LOCK/owner" 2>/dev/null) run_id=$(cat "$LOCK/run_id" 2>/dev/null) heartbeat=$(cat "$LOCK/heartbeat" 2>/dev/null))"
else
    _log "pipeline lock: free"
fi

# ── running version smoke (informational in plain mode) ────────────────────
cur_name=""
[ -L "$REL_DIR/current" ] && cur_name="$(readlink "$REL_DIR/current")" && cur_name="${cur_name##*/}"
LIVEZ="$(_probe_once "/livez" "$PORT")"
if [ -n "$LIVEZ" ]; then
    running_ver="$(_json_field "$LIVEZ" version)"
    [ -n "$running_ver" ] || running_ver="(no version field)"
    _log "daemon :$PORT /livez version=$running_ver"
    if [ -n "$cur_name" ] && [ -f "$(manifest_path "$cur_name")" ]; then
        want_ver="$(manifest_field "$cur_name" binary_version 2>/dev/null)"
        if [ "$running_ver" = "$want_ver" ]; then
            _log "version smoke: OK ($running_ver == manifest binary_version)"
        else
            _warn "version smoke MISMATCH: running=$running_ver manifest=$want_ver (D2/ADR-027)"
        fi
    fi
else
    _log "daemon :$PORT /livez: not answering (informational — daemon may be stopped)"
fi

# ── --verify: hard integrity mode ───────────────────────────────────────────
if [ "$VERIFY" = "1" ]; then
    _log "verify: integrity check of the currently-pointed release (D-FA4.4)"
    rc=0
    if [ -z "$cur_name" ] || [ ! -L "$REL_DIR/current" ]; then
        _warn "verify: no current symlink to verify against"
        rc=1
    elif verify_current_release; then
        _log "verify: integrity OK — release '$cur_name' matches its manifest (trio + launcher + config + trees)"
    else
        _warn "verify: integrity FAILED for release '$cur_name' (see MISMATCH lines above — the file is named there)"
        rc=1
    fi
    # version smoke hardens into a failure when the daemon IS answering
    if [ -n "$LIVEZ" ] && [ -n "$cur_name" ] && [ -f "$(manifest_path "$cur_name")" ]; then
        running_ver="$(_json_field "$LIVEZ" version)"
        want_ver="$(manifest_field "$cur_name" binary_version 2>/dev/null)"
        if [ -n "$running_ver" ] && [ "$running_ver" != "$want_ver" ]; then
            _warn "verify: version smoke MISMATCH is a verify failure (running=$running_ver manifest=$want_ver)"
            rc=1
        fi
    fi
    if [ "${VERIFY_RC_NOTE:-0}" = "1" ]; then rc=1; fi
    if [ "$rc" = "0" ]; then
        _log "verify: PASS"
        exit 0
    fi
    _warn "verify: FAIL"
    exit 1
fi

exit 0
