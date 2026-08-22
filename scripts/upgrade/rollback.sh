#!/bin/bash
# ============================================================================
# scripts/upgrade/rollback.sh — manual rollback to a staged release (P2.1 T6)
# ============================================================================
# Manual, explicit rollback: lock → manifest gate on the target → stop →
# launcher swap from the target release → repoint current → restart →
# short re-gate → journal 'rollback' event (COUNTS toward the 3/24h cap,
# ADR-005) → lock release.
#
# Target selection: the journal's `previous` by default; an explicit version
# via --to <ver> (or VERSION=<ver>). A QUARANTINED target requires --force
# and prints a warning (the quarantine exists because that version already
# failed a gate).
#
# Differences from the AUTO-rollback (T5) by design:
#   - no cooldown is armed (cooldown is the auto-path anti-flapping response;
#     the cap count still applies — 3 rollbacks/24h of ANY kind arm halt)
#   - manual rollback is NOT subject to cooldown/cap entry refusal itself
#     (D-FA4.2 entry-side ruling: the recovery never refuses on cap/cooldown)
#
# USAGE:
#   bash scripts/upgrade/rollback.sh demo                    # → previous
#   bash scripts/upgrade/rollback.sh sandbox --to v3         # explicit
#   bash scripts/upgrade/rollback.sh sandbox --to v3 --force # quarantined
#   (sandbox needs INSTALL_DIR=<dir> PORT=<port>)
#
# EXIT CODES: 0 rolled back · 1 failure (rollback did not complete) ·
# 78 refusal (no target / manifest gate / quarantined without --force /
# unknown target / unconfirmed live / busy).
# ============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_TAG="upgrade-rollback"

TO_VERSION="${VERSION:-}"
FORCE=0
TARGET_ARG=""
args=("$@")
i=0
while [ $i -lt ${#args[@]} ]; do
    arg="${args[$i]}"
    case "$arg" in
        demo|live|sandbox) TARGET_ARG="$arg" ;;
        --to)
            i=$((i + 1))
            TO_VERSION="${args[$i]:-}"
            ;;
        --force) FORCE=1 ;;
        -h|--help) sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "rollback: unknown flag '$arg' — see --help" >&2; exit 78 ;;
    esac
    i=$((i + 1))
done

# shellcheck source=scripts/upgrade/lib.sh
. "$SCRIPT_DIR/lib.sh"

resolve_env "${TARGET_ARG:-${TARGET:-demo}}"
require_live_guard "$UP_TARGET"
echo_env_triple

REL="$INSTALL_DIR/releases"

# ── Target resolution ───────────────────────────────────────────────────────
journal_init || exit 1
J="$(journal_read)" || { _warn "journal unreadable/TORN — halt-for-human"; exit 78; }

if [ -z "$TO_VERSION" ]; then
    TO_VERSION="$(_json_field "$J" previous)"
    [ "$TO_VERSION" = "null" ] && TO_VERSION=""
    if [ -z "$TO_VERSION" ]; then
        _warn "no --to version given and the journal has no previous — nothing to roll back to"
        exit 78
    fi
    _log "target: journal previous = $TO_VERSION"
else
    _log "target: explicit $TO_VERSION"
fi

if [ ! -d "$REL/$TO_VERSION" ]; then
    _warn "target release $REL/$TO_VERSION does not exist"
    exit 78
fi

# ── Lock (D5) ───────────────────────────────────────────────────────────────
if ! lock_acquire; then
    exit 78   # pipeline-busy (structured, logged)
fi
trap 'lock_release' EXIT
lock_heartbeat

# Re-read the journal under the lock (state may have moved since).
J="$(journal_read)" || exit 78

# ── Manifest gate (ADR-005 M5 / D-FA4.5) ────────────────────────────────────
SAFE="$(manifest_field "$TO_VERSION" rollback_safe 2>/dev/null)"
if [ "$SAFE" != "true" ]; then
    _warn "manifest gate: target $TO_VERSION has rollback_safe=${SAFE:-<missing>} — refusing (halt-for-human; rolling back into schema drift is worse than staying)"
    journal_history_append halt "manual rollback to $TO_VERSION refused: rollback_safe=${SAFE:-missing}"
    exit 78
fi

# ── Quarantine gate ─────────────────────────────────────────────────────────
if journal_is_quarantined "$TO_VERSION"; then
    if [ "$FORCE" = "1" ]; then
        _warn "FORCING rollback onto QUARANTINED $TO_VERSION (--force) — it failed a gate before; you own what happens next"
        journal_history_append rollback "manual rollback to quarantined $TO_VERSION FORCED (--force)"
    else
        _warn "target $TO_VERSION is QUARANTINED — rollback refused. Re-run with --force if you really mean it."
        exit 78
    fi
fi

TO_BIN_VERSION="$(manifest_field "$TO_VERSION" binary_version 2>/dev/null)"

# ── Transaction (D4) ────────────────────────────────────────────────────────
if ! journal_open_txn "rollback" "$TO_VERSION"; then
    _warn "an in_flight txn is open — pipeline-busy (resolve it or wait for the sweep)"
    exit 78
fi

# ── Stop → launcher swap → repoint → restart (D6 + amendment) ───────────────
lock_heartbeat
if ! stop_via_stop_script; then
    _warn "stop FAILED — aborting rollback before any flip"
    journal_close_txn
    exit 1
fi
launcher_swap "$TO_VERSION" || { _warn "launcher swap failed — keeping current launcher"; }
if ! atomic_flip "$TO_VERSION"; then
    _warn "repoint FAILED — current untouched; halting"
    journal_history_append halt "manual rollback repoint to $TO_VERSION failed (current untouched)"
    exit 1
fi
journal_mark_flipped
lock_heartbeat
restart_via_launcher

# ── Short re-gate (livez + readyz + version; no soak) ───────────────────────
REGATE_FAIL=""
if RG_LIVEZ="$(gate_livez)"; then
    _log "livez OK"; _logv "$RG_LIVEZ"
else
    REGATE_FAIL="/livez unreachable"
fi
if [ -z "$REGATE_FAIL" ] && RG_READY="$(gate_readyz)"; then
    _log "readyz OK"; _logv "$RG_READY"
elif [ -z "$REGATE_FAIL" ]; then
    REGATE_FAIL="/readyz unreachable"
fi
if [ -z "$REGATE_FAIL" ] && [ -n "$TO_BIN_VERSION" ]; then
    gate_version "$TO_BIN_VERSION" > /dev/null || REGATE_FAIL="version mismatch"
fi

# ── Journal bookkeeping: counts toward the cap (ADR-005); NO cooldown arm ───
OLD_CUR="$(_json_field "$J" current)"
[ "$OLD_CUR" = "null" ] && OLD_CUR=""
journal_set_current "$TO_VERSION"
journal_set_previous "${OLD_CUR:-null}"
NEW_COUNT="$(journal_count_rollback 0)"
journal_close_txn

if [ -n "$REGATE_FAIL" ]; then
    journal_history_append halt "manual rollback to $TO_VERSION landed but re-gate FAILED ($REGATE_FAIL) — halt-for-human (ADR-028)"
    _warn "HALT-FOR-HUMAN: rolled back to $TO_VERSION but re-gate failed ($REGATE_FAIL) — human picks the next step"
    lock_release
    exit 78
fi

journal_history_append rollback "manual rollback → $TO_VERSION (re-gate green; window count $NEW_COUNT/$ROLLBACK_CAP_24H)"
if [ "$NEW_COUNT" -ge "$ROLLBACK_CAP_24H" ]; then
    journal_history_append halt "rollback cap $ROLLBACK_CAP_24H/24h reached via manual rollback (count=$NEW_COUNT) — promotes refused until the window resets"
    _warn "rollback complete (serving $TO_BIN_VERSION on :$PORT) BUT cap reached — subsequent promotes will refuse (halt-for-human)"
    lock_release
    exit 0
fi

retention_evict
lock_release
_log "rollback complete: current=$TO_VERSION serving ${TO_BIN_VERSION:-?}; window count $NEW_COUNT/$ROLLBACK_CAP_24H"
exit 0
