#!/bin/bash
# ============================================================================
# scripts/upgrade/promote.sh — atomic flip + health gate + commit/rollback
# (P2.1 T4/T5/T8 — ADR-005/009, architect rulings D-FA4.x)
# ============================================================================
# SEQUENCE (phase1-plan T4, exact):
#   1 preflight   lock acquire (D5) · integrity of CURRENT + TARGET releases
#                (D-FA4.4) · adopt-or-refuse stale in_flight · journal open
#                in_flight{kind:promote} · ENTRY-side cooldown/cap/quarantine
#                checks (D-FA4.2: entry only — the rollback itself never
#                refuses on cap/cooldown)
#   2 stop        scripts/stop-ensemble.sh (D6: SIGTERM-bounded, NEVER raw
#                kill)
#   3 launcher    swap INSTALL_DIR/launcher.sh from the release payload in the
#                stopped window (D-FA4.1 amendment — launcher travels with
#                the release)
#   4 flip        ln -sfn releases/<ver> current.new.$$ ; mv -f (rename(2))
#   5 journal     flipped:true
#   6 restart     launcher.sh (journal sweep runs before binary resolution —
#                T7, separate owner)
#   7 gate        /livez ≤60s → /readyz ≤120s → version verify (/livez
#                version == manifest binary_version, D2/ADR-027) → 300s soak
#                (re-probed every 30s) — all inside the 10-min outer window
#   8a commit     journal current/previous update · history 'commit' ·
#                retention (keep 3, previous pinned — T8) · lock release
#   8b rollback   T5 auto-rollback: manifest gate on previous FIRST (else
#                halt-for-human + journal halt, NO repoint) → repoint to
#                previous → restart → short re-gate → notify → quarantine →
#                cooldown 10min → count++ → cap 3/24h arms halt-for-human
#
# SOAK KNOB: ENSEMBLE_PROMOTE_SOAK_S (default 300) exists for sandbox drills
# only; production keeps the ADR-005 300s soak.
#
# USAGE:
#   VERSION=v0.10.6 bash scripts/upgrade/promote.sh demo
#   bash scripts/upgrade/promote.sh sandbox --version v1
#   (sandbox needs INSTALL_DIR=<dir> PORT=<port>)
#
# EXIT CODES: 0 committed · 1 rolled back (env recovered, promote failed) ·
# 78 refusal (preflight/halt/cooldown/cap/quarantine/integrity/busy/live) ·
# 75 gate-unreachable class is handled internally by rollback.
#
# ABORT-LANE POLICY (B4): every post-stop abort (stop/swap/flip failure)
# leaves the journal txn OPEN — never closed — so the ADR-012 sweep self-
# recovers: `current` is untouched at the last-known-good release, the next
# launcher start boots it, and the sweep clears the stale txn then.
# ============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_TAG="upgrade-promote"

VERSION="${VERSION:-}"
TARGET_ARG=""
args=("$@")
i=0
while [ $i -lt ${#args[@]} ]; do
    arg="${args[$i]}"
    case "$arg" in
        demo|live|sandbox) TARGET_ARG="$arg" ;;
        --version)
            # documented usage form (promote.sh sandbox --version v1) —
            # parsed like stage.sh; VERSION env still works (review m4)
            i=$((i + 1))
            VERSION="${args[$i]:-}"
            ;;
        -h|--help) sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "promote: unknown flag '$arg' — set VERSION=<ver> env or use --help" >&2; exit 78 ;;
    esac
    i=$((i + 1))
done

# shellcheck source=scripts/upgrade/lib.sh
. "$SCRIPT_DIR/lib.sh"

resolve_env "${TARGET_ARG:-${TARGET:-demo}}"
require_live_guard "$UP_TARGET"
echo_env_triple

if [ -z "$VERSION" ]; then
    _warn "explicit VERSION required — e.g. VERSION=v0.10.6 bash scripts/upgrade/promote.sh demo"
    exit 78
fi

SOAK_S="${ENSEMBLE_PROMOTE_SOAK_S:-$SOAK_S_DEFAULT}"
case "$SOAK_S" in
    ''|*[!0-9]*) _warn "invalid ENSEMBLE_PROMOTE_SOAK_S='$SOAK_S' (digits only)"; exit 78 ;;
esac
[ "$SOAK_S" -gt 900 ] && SOAK_S=900

REL="$INSTALL_DIR/releases"
TARGET_REL="$REL/$VERSION"

# ═══════════════════════════ 1. PREFLIGHT ══════════════════════════════════
_log "preflight: lock · integrity · journal txn · entry checks"

# 1a. lock (D5/D-FA5.1) — second invocation = structured pipeline-busy
if ! lock_acquire; then
    exit 78
fi
trap 'lock_release' EXIT

# lock held from here on — heartbeat before every long wait
lock_heartbeat

# 1b. staged mode sanity
if [ ! -d "$TARGET_REL" ]; then
    _warn "target release $TARGET_REL does not exist — stage it first (stage.sh $UP_TARGET --version $VERSION)"
    exit 78
fi
journal_init || { _warn "cannot initialize journal"; exit 1; }
J="$(journal_read)" || {
    _warn "journal unreadable/TORN at $(journal_path) — halt-for-human (repair the journal before any pipeline mutation)"
    exit 78
}

# 1c. adopt-or-refuse an existing in_flight (sweep-mirroring, D-FA4.3)
adopt_stale_txn || exit 78

# 1d. ENTRY-side checks (D-FA4.2): cap/halt · cooldown · quarantine
promote_entry_check "$VERSION" || exit 78

# 1e. integrity (D-FA4.4): CURRENT (drift detection) + TARGET + manifest
# fields + no-.env invariant. Same-version re-promote verifies once.
CUR_JSON="$(journal_read)"
CUR="$(_json_field "$CUR_JSON" current)"
[ "$CUR" = "null" ] && CUR=""
if [ -n "$CUR" ] && [ "$CUR" != "$VERSION" ]; then
    if [ ! -L "$INSTALL_DIR/current" ]; then
        _warn "journal current='$CUR' but the current SYMLINK is missing (layout divergence — D-FA5.3 freezes mutations)"
        journal_history_append halt "promote refused: layout divergence (journal current $CUR, no symlink)"
        exit 78
    fi
    _log "integrity: verifying CURRENT release $CUR (drift detection)"
    if ! integrity_verify "$CUR"; then
        _warn "preflight ABORT (exit 78): CURRENT release $CUR failed integrity — the running baseline is corrupted/tampered; promote refuses to build on it"
        exit 78
    fi
fi
_log "integrity: verifying TARGET release $VERSION"
if ! integrity_verify "$VERSION"; then
    _warn "preflight ABORT (exit 78): TARGET release $VERSION failed integrity"
    exit 78
fi
BIN_VERSION="$(manifest_field "$VERSION" binary_version)"
if [ -z "$BIN_VERSION" ]; then
    _warn "target manifest missing binary_version — refusing"
    exit 78
fi

# 1f. open the transaction (D4)
if ! journal_open_txn "promote" "$VERSION"; then
    _warn "cannot open journal txn (an in_flight survived adoption?) — pipeline-busy"
    exit 78
fi
PROMOTE_START="$(_now_epoch)"
_log "txn open: promote target=$VERSION pid=$$ (outer window $((SWEEP_STALE_S))s from txn start)"

# ═══════════════════════════ 2. STOP (D6) ══════════════════════════════════
lock_heartbeat
if ! stop_via_stop_script; then
    # B4 policy (leave-txn-open, applied at all four abort sites): the txn
    # stays OPEN so the sweep self-recovers. `current` still points at the
    # last-known-good release, so ANY next launcher start (watchdog or
    # operator) boots LKG, and the sweep clears the stale pre-flip txn at
    # that same start. Closing the txn here would tell the sweep "nothing
    # happened" while the env may be dark. We do NOT restart via launcher
    # either: a FAILED stop leaves daemon liveness unknown — a blind boot
    # could double-boot onto a still-running daemon.
    _warn "stop-ensemble.sh FAILED — daemon state UNKNOWN (may be down); aborting promote BEFORE any flip (current untouched at last-known-good; txn left open for sweep recovery)"
    journal_history_append halt "promote aborted pre-flip: stop failed for $VERSION — txn left open for sweep recovery (current untouched at LKG)"
    exit 1
fi

# ═══════════════════ 3. LAUNCHER SWAP (stopped window) ═════════════════════
launcher_swap "$VERSION" || {
    # B4 leave-txn-open: env is DARK here (stop succeeded) and current is
    # untouched at LKG — the next launcher start boots LKG; the sweep then
    # clears this stale pre-flip txn. Never close the txn on a post-stop
    # abort: in_flight:null makes the sweep a no-op → env stays dark until
    # a human intervenes.
    _warn "launcher swap failed — aborting promote BEFORE any flip (current untouched at last-known-good; txn left open for sweep recovery)"
    journal_history_append halt "promote aborted pre-flip: launcher swap failed for $VERSION — txn left open for sweep recovery"
    exit 1
}

# ═══════════════════════════ 4. ATOMIC FLIP ════════════════════════════════
if ! atomic_flip "$VERSION"; then
    # B4 leave-txn-open (same policy): current untouched at LKG; the next
    # launcher start boots it and the sweep clears this stale pre-flip txn.
    _warn "ATOMIC FLIP FAILED — current untouched; aborting (txn left open — the sweep clears it at the next launcher start, which boots the untouched current = LKG)"
    journal_history_append halt "promote aborted: flip failed for $VERSION (current untouched) — txn left open for sweep recovery"
    exit 1
fi

# ═══════════════════════════ 5. FLIPPED MARKER ═════════════════════════════
journal_mark_flipped || { _warn "cannot mark flipped — halting for human (sweep will find the txn)"; exit 1; }
lock_heartbeat

# ═══════════════════════════ 6. RESTART (launcher) ═════════════════════════
restart_via_launcher

# ═══════════════════════════ 7. HEALTH GATE (D2) ═══════════════════════════
gate_fail_reason=""

if LIVEZ_JSON="$(gate_livez)"; then
    _log "livez OK:"; _logv "$LIVEZ_JSON"
else
    gate_fail_reason="/livez unreachable >${LIVEZ_BUDGET_S}s"
fi

if [ -z "$gate_fail_reason" ] && READYZ_JSON="$(gate_readyz)"; then
    _log "readyz OK:"; _logv "$READYZ_JSON"
elif [ -z "$gate_fail_reason" ]; then
    gate_fail_reason="/readyz unreachable >${READYZ_BUDGET_S}s"
fi

if [ -z "$gate_fail_reason" ]; then
    gate_version "$BIN_VERSION" || gate_fail_reason="version verify mismatch"
fi

if [ -z "$gate_fail_reason" ]; then
    lock_heartbeat
    gate_soak "$SOAK_S" "$BIN_VERSION" || gate_fail_reason="soak failure"
fi

# ═════════════════ 8a. COMMIT — or 8b. AUTO-ROLLBACK (T5) ══════════════════
if [ -z "$gate_fail_reason" ]; then
    # commit
    OLD_CUR="$CUR"
    journal_set_current "$VERSION"
    journal_set_previous "${OLD_CUR:-null}"
    journal_close_txn
    journal_history_append commit "promote $VERSION committed (gate+soak green; previous=${OLD_CUR:-none})"
    _log "COMMITTED: current=$VERSION previous=${OLD_CUR:-<none>}"
    retention_evict
    lock_release
    _log "promote complete — $UP_TARGET serves $BIN_VERSION on :$PORT"
    exit 0
fi

# ── 8b. AUTO-ROLLBACK (T5) — the recovery NEVER refuses on cap/cooldown ────
_log "GATE FAILED: $gate_fail_reason — auto-rollback initiating (ADR-005)"

PREV_JSON="$(journal_read)"
PREV="$(_json_field "$PREV_JSON" previous)"
[ "$PREV" = "null" ] && PREV=""

# T5 manifest gate FIRST (D-FA4.5): previous must EXIST and be rollback_safe
if [ -z "$PREV" ]; then
    journal_close_txn
    journal_set_current "$VERSION"
    journal_set_previous "null"
    journal_history_append halt "gate fail ($gate_fail_reason) with NO previous release — halt-for-human, daemon rests on $VERSION (degraded, alerted)"
    _warn "HALT-FOR-HUMAN: no previous release to roll back to — daemon stays on $VERSION (degraded). Notify + human recovery per ADR-028."
    lock_release
    exit 78
fi
if [ ! -d "$REL/$PREV" ]; then
    journal_close_txn
    journal_set_current "$VERSION"
    journal_history_append halt "gate fail ($gate_fail_reason) but previous $PREV is MISSING (evicted/manually deleted) — halt-for-human, NO repoint"
    _warn "HALT-FOR-HUMAN: previous release $PREV missing — daemon stays on $VERSION (degraded). NO repoint (ADR-005 M5)."
    lock_release
    exit 78
fi
PREV_SAFE="$(manifest_field "$PREV" rollback_safe 2>/dev/null)"
if [ "$PREV_SAFE" != "true" ]; then
    journal_close_txn
    journal_set_current "$VERSION"
    journal_history_append halt "gate fail ($gate_fail_reason) but previous $PREV has rollback_safe=$PREV_SAFE — halt-for-human, NO repoint (schema-drift guard D-FA4.5)"
    _warn "HALT-FOR-HUMAN: previous $PREV is NOT rollback_safe — daemon stays on $VERSION (degraded) rather than flipping into schema drift. NO repoint."
    lock_release
    exit 78
fi
PREV_BIN_VERSION="$(manifest_field "$PREV" binary_version 2>/dev/null)"

# rollback: stop → launcher from PREV → repoint → restart → short re-gate
# (B4 note: these abort sites already follow the leave-txn-open policy —
# the txn is flipped:true here, so an open txn makes the next launcher
# start sweep-ROLLBACK to previous; closing it would strand the env.)
lock_heartbeat
if ! stop_via_stop_script; then
    _warn "rollback: stop of the failed release FAILED — halting for human (txn left open — the next launcher start sweep-rolls-back to previous)"
    journal_history_append halt "rollback stop failed for $VERSION — halt-for-human; txn left open for sweep recovery"
    exit 1
fi
launcher_swap "$PREV"
if ! atomic_flip "$PREV"; then
    _warn "rollback: repoint to $PREV FAILED — halting for human (txn left open — the next launcher start sweep-rolls-back to previous)"
    journal_history_append halt "rollback repoint to $PREV failed — halt-for-human; txn left open for sweep recovery"
    exit 1
fi
restart_via_launcher

# short re-gate: livez + readyz + version (no soak)
REGATE_FAIL=""
if RG_LIVEZ="$(gate_livez)"; then
    _log "rollback livez OK"; _logv "$RG_LIVEZ"
else
    REGATE_FAIL="/livez unreachable"
fi
if [ -z "$REGATE_FAIL" ] && RG_READY="$(gate_readyz)"; then
    _log "rollback readyz OK"; _logv "$RG_READY"
elif [ -z "$REGATE_FAIL" ]; then
    REGATE_FAIL="/readyz unreachable"
fi
if [ -z "$REGATE_FAIL" ] && [ -n "$PREV_BIN_VERSION" ]; then
    gate_version "$PREV_BIN_VERSION" > /dev/null || REGATE_FAIL="version mismatch on previous"
fi

# journal: rollback bookkeeping (counts toward cap — ADR-005; cooldown armed).
# Restore the pre-promote pairing: current=PREV, previous=old current (the
# release we were serving before this promote began).
journal_set_current "$PREV"
journal_set_previous "${CUR:-null}"
NEW_COUNT="$(journal_count_rollback 1)"
journal_history_append rollback "auto-rollback $VERSION → $PREV (gate fail: $gate_fail_reason; re-gate ${REGATE_FAIL:-green})"
journal_quarantine "$VERSION"
journal_history_append quarantine "$VERSION quarantined after gate failure (skipped by future promotes)"

if [ -n "$REGATE_FAIL" ]; then
    # ADR-028: the rollback target failing its gate is halt-for-human; the
    # rollback-class event already counted above.
    journal_close_txn
    journal_history_append halt "previous $PREV failed re-gate ($REGATE_FAIL) — halt-for-human with release list; recovery = user-chosen version via promote"
    _warn "HALT-FOR-HUMAN: rollback landed on $PREV but re-gate FAILED ($REGATE_FAIL). Human picks the next version (ADR-028)."
    lock_release
    exit 78
fi

if [ "$NEW_COUNT" -ge "$ROLLBACK_CAP_24H" ]; then
    journal_close_txn
    journal_history_append halt "rollback cap $ROLLBACK_CAP_24H/24h reached (count=$NEW_COUNT) — halt-for-human; promotes refused until the window resets"
    _warn "HALT-FOR-HUMAN: rollback cap reached ($NEW_COUNT/$ROLLBACK_CAP_24H in 24h) — environment restored to $PREV; further promotes refused until window reset (ADR-005 D2)"
    lock_release
    exit 1
fi

journal_close_txn
journal_history_append rollback "rollback complete: serving $PREV_BIN_VERSION on :$PORT; cooldown armed (${COOLDOWN_S}s); window count $NEW_COUNT/$ROLLBACK_CAP_24H"
_log "ROLLBACK COMPLETE: current=$PREV serving ${PREV_BIN_VERSION:-?}; quarantine=$VERSION; cooldown ${COOLDOWN_S}s; count $NEW_COUNT/$ROLLBACK_CAP_24H"
retention_evict
lock_release
exit 1   # the PROMOTE failed — environment recovered, human attention wanted
