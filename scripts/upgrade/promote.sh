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
#   TARGET=live ADDITIONALLY requires BOTH the ENSEMBLE_UPGRADE_LIVE=1
#   guard env AND the explicit --f2-verified-closed operator flag
#   (MINOR-4b: a second factor, not a replacement — see the F2 gate).
#   --allow-stale-plugins explicitly records the slice-⑥ override on
#   the install dir (audit-continuity back-compat — coincides with the
#   new default since the 2026-10-07 allow-stale flip; see lib.sh
#   _plugin_staleness_journal_override). argv-only, mirrors
#   --allow-stale-stage.
#   --block-on-stale opts BACK INTO the slice-⑥ refuse-on-stale
#   behavior (PROMOTE_STRICT_STALENESS=1). Without it, stale plugins
#   journal `plugin_staleness_observed` and the promote PROCEEDS
#   (ratified 2026-10-07 default — stale pins are the steady state,
#   not a promote-blocking anomaly). STRICT wins over OVERRIDE on
#   conflict (strict is the "loud failure" choice).
#
# EXIT CODES: 0 committed · 1 rolled back (env recovered, promote failed) ·
# 78 refusal (preflight/halt/cooldown/cap/quarantine/integrity/busy/live/
# plugin-staleness) ·
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
F2_VERIFIED_CLOSED=0
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
        --f2-verified-closed)
            # MINOR-4b (P2.3 review cycle 1): explicit operator flag —
            # TARGET=live additionally requires it (see the F2 gate).
            # No value; presence is the attestation.
            F2_VERIFIED_CLOSED=1
            ;;
        --allow-stale-plugins)
            # Slice ⑥ (REC comp 13): argv-only override for the
            # plugin-staleness predicate. Since the 2026-10-07
            # allow-stale flip this coincides with the DEFAULT
            # (stale pins are the steady state; the predicate's
            # verdict is journaled and the promote PROCEEDS).
            # Keeping the flag as audit-continuity back-compat:
            # when an operator explicitly passes it, the journal
            # records the OLD `plugin_staleness_override` event
            # (operator_accepted=true) — distinct from the
            # unattended `plugin_staleness_observed` default.
            PROMOTE_STALENESS_OVERRIDE=1
            ;;
        --block-on-stale)
            # Allow-stale flip 2026-10-07 changed the default to
            # observe-and-proceed. This flag opts BACK INTO the
            # slice-⑥ refuse-on-stale behavior (stale ⇒ exit 78
            # refusal journaled). Opt-in only — strict wins over
            # override on conflict.
            PROMOTE_STRICT_STALENESS=1
            ;;
        -h|--help) sed -n '2,50p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "promote: unknown flag '$arg' — set VERSION=<ver> env or use --help" >&2; exit 78 ;;
    esac
    i=$((i + 1))
done

# shellcheck source=scripts/upgrade/lib.sh
. "$SCRIPT_DIR/lib.sh"

# M5: journal_fail_loud (lib.sh) is the fail-loud handler for every
# post-flip journal write below — see its comment there.

resolve_env "${TARGET_ARG:-${TARGET:-demo}}"
require_live_guard "$UP_TARGET"

# MINOR-4b (P2.3 review cycle 1): live-target F2 gate. promote.sh live is
# a DEAD PATH until F2 (the unauthenticated loopback API user-origin forge
# lane) closes — but the gate is enforced NOW: TARGET=live ADDITIONALLY
# requires the explicit --f2-verified-closed operator flag. This is a
# SECOND factor on top of the ENSEMBLE_UPGRADE_LIVE=1 guard above (which
# stays) — not a replacement. Distinct D-FA2.2 reason token
# (f2-not-verified) so the journal refusal record + SSE alert are
# distinguishable from the live-guard refusal. When the flag IS present,
# the attestation is recorded on the live txn at open time
# (journal_mark_f2_verified, lib.sh) — auditable at the enforcement point.
if [ "$UP_TARGET" = "live" ] && [ "$F2_VERIFIED_CLOSED" != "1" ]; then
    _refuse f2-not-verified "promote refused (f2-not-verified): TARGET=live requires the explicit --f2-verified-closed operator flag — F2 user-origin forge lane verified closed (runbook §9 hard block; ENSEMBLE_UPGRADE_LIVE=1 remains a separate, still-required factor)"
fi

echo_env_triple

if [ -z "$VERSION" ]; then
    _warn "explicit VERSION required — e.g. VERSION=v0.10.6 bash scripts/upgrade/promote.sh demo"
    exit 78
fi

# D1 (reviewer ruling, P2.3 final batch): the direct exit-78s above and the
# SOAK_S case below are INPUT-VALIDATION refusals — journal-less BY DESIGN
# (nothing has been locked, opened, or mutated yet; the reviewed carve-out
# set is CLOSED — no conversion to _refuse journaling).

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
# Signal trap discipline (component 2 of r-20260928-005506-f82e): bash's
# EXIT trap does NOT fire on untrapped TERM/HUP/INT. Install explicit
# handlers that journal a halt event and release the lock before exit
# — otherwise the systemd-cgroup kill leaves an orphan txn AND a
# stale lock. The helper ALSO swaps the EXIT trap to _trap_safe_exit
# (no double-release against the signal handler's release).
_trap_install_signal_handlers "promote:preflight"

# lock held from here on — heartbeat before every long wait
lock_heartbeat

# 1b. staged mode sanity
if [ ! -d "$TARGET_REL" ]; then
    _refuse not-staged "target release $TARGET_REL does not exist — stage it first (stage.sh $UP_TARGET --version $VERSION)"
fi
journal_init || { _warn "cannot initialize journal"; exit 1; }
J="$(journal_read)" || {
    _warn "journal unreadable/TORN at $(journal_path) — halt-for-human (repair the journal before any pipeline mutation)"
    exit 78
}

# 1c. adopt-or-refuse an existing in_flight (sweep-mirroring, D-FA4.3)
adopt_stale_txn || _refuse txn-busy "promote refused: unresolved in_flight txn (see the adopt diagnostics above — pipeline-busy)"

# 1d. ENTRY-side checks (D-FA4.2): cap/halt · cooldown · quarantine.
# L11 (tidier): promote_entry_check only ever returns 0 — every refusal
# path inside it exits 78 itself (_refuse) — so no caller-side exit arm.
promote_entry_check "$VERSION"

# 1d-sup. Supervision classification (ownership-mode P1, 2026-09-29):
# classify + ONE machine-readable ENSEMBLE_SUPERVISION_RESULT line +
# explicit-unit exit-78 refusal + the §6 DUAL_FIGHT fault check — PRE-TXN
# (before 1f opens the journal txn; before any stop/flip mutation).
supervision_preflight

# 1d-ter. Plugin-staleness predicate (REC comp 13, slice ⑥ — default
# FLIPPED 2026-10-07 to observe-and-proceed). Predicate output:
#   - fresh  ⇒ proceed (rc=0).
#   - stale  ⇒ DEFAULT (no strict, no override): journal
#     plugin_staleness_observed + PROCEED.
#   - stale  + PROMOTE_STRICT_STALENESS=1 (env) / --block-on-stale
#     (argv, opt-in): refuse exit 78 (slice-⑥ blocking restored;
#     journaled as a `refusal` event with the predicate's code).
#   - stale  + PROMOTE_STALENESS_OVERRIDE=1 (env) / --allow-stale-plugins
#     (argv, audit-continuity back-compat): journal
#     plugin_staleness_override + PROCEED.
#   - STRICT wins OVER OVERRIDE on conflict (strict = "loud failure").
#   - predicate unevaluable (no python, no module, predicate crash) ⇒
#     refuse exit 78 unless overridden (fail-closed — unknown tooling
#     is LOUD; stale pins are the steady state).
# A repo with no plugins/ passes trivially (no spurious blocks on
# plugin-less promotes). Runs BEFORE integrity so staleness is
# visible at the earliest refusal point, and BEFORE any stop/flip
# mutation.
promote_plugin_staleness_check

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
# MINOR-4b: on the live lane, stamp the operator's F2 attestation ON the
# open txn (f2_verified_closed:true + timestamp + optional operator note
# from F2_VERIFIED_NOTE) — auditable at the enforcement point; the field
# rides the txn's window into the journal record. Additive fields only —
# existing readers parse named fields and ignore extras (D4 splice
# discipline). Demo/sandbox lanes stamp nothing (the flag is live-only).
if [ "$UP_TARGET" = "live" ] && [ "$F2_VERIFIED_CLOSED" = "1" ]; then
    journal_mark_f2_verified || journal_fail_loud "preflight: journal_mark_f2_verified (F2 attestation record)"
fi
# P1 §5: stamp supervision=<state> / unit=<name|null> ADDITIVELY on the
# open txn (D4 splice discipline — additive-only textual splice; existing
# readers parse named fields and ignore extras). Advisory: a stamp failure
# never aborts the promote.
journal_mark_supervision || _warn "supervision txn stamp failed (advisory — continuing)"
PROMOTE_START="$(_now_epoch)"
_log "txn open: promote target=$VERSION pid=$$ (outer window $((SWEEP_STALE_S))s from txn start)"

# ═══════════════════════════ 2. STOP (D6) ══════════════════════════════════
# P2 (ownership-mode commission 2026-09-29): stop_via_stop_script now
# re-runs the DUAL_FIGHT check pre-stop (refuses exit 78 before any stop
# action) and, when the P1 classification says UNIT_MANAGED with a
# resolvable unit, routes the stop through `systemctl stop <unit>` +
# UNIT-STATE polling (b″ fix) — see stop_via_stop_script in lib.sh.
# Script/scope-survivor shapes keep the SIGTERM-bounded path unchanged.
lock_heartbeat
if ! stop_via_stop_script; then
    # B4 policy (leave-txn-open, applied at all four abort sites): the txn
    # stays OPEN so the sweep self-recovers. `current` still points at the
    # last-known-good release, so the next launcher start boots LKG, and
    # the sweep clears the stale pre-flip txn at that same start. The dark
    # window is bounded, not instant: watchdog-watcher is NOTIFY-ONLY
    # (launchd hands off after its exit-0; it never restarts anything), so
    # recovery rides the watcher notify (~600-900s) and then the next
    # operator/launchd start of the launcher. Closing the txn here would
    # tell the sweep "nothing happened" while the env may be dark. We do
    # NOT restart via launcher either: a FAILED stop leaves daemon liveness
    # unknown — a blind boot could double-boot onto a still-running daemon.
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

# ═════════════ 6. RESTART — supervision-aware hand-back (P3) ═════════════════
# The executor's P1 classification — consumed from the stop-site globals
# (SUPERVISION_* set while the daemon still ran; never re-derived here,
# the daemon is down) — selects the hand-back mode: UNIT_MANAGED → unit
# hand-back (NO nohup fallback — Amendment #1: a fallback re-creates the
# survivor lineage; systemd brings up the flipped release); SCOPE_SURVIVOR
# + unit → same unit hand-back + the scope→unit self-heal journal event;
# script shapes → today's nohup / comp7 paths. A unit hand-back failure
# is NEVER a false success: halt journal event + B4 leave-txn-open (the
# txn is flipped:true here — the next launcher start sweep-ROLLS-BACK to
# previous; the halt event alerts the human).
if ! restart_via_launcher; then
    _warn "unit hand-back FAILED — halting promote (txn left open for sweep recovery; NO nohup fallback, never a false success)"
    journal_history_append halt "promote aborted post-flip: unit hand-back failed for $VERSION — halt-for-human, txn left open for sweep recovery (declared=${SUPERVISION_MODE:-?} verified=${SUPERVISION_STATE:-?} unit=${SUPERVISION_UNIT:-?}; no nohup fallback — Amendment #1)" \
                              || true   # history is advisory; the open txn IS the B4 contract
    exit 1
fi

# ═══════════════════════════ 7. HEALTH GATE (D2) ═══════════════════════════
gate_fail_reason=""

if LIVEZ_JSON="$(gate_livez)"; then
    _log "livez OK:"; _logv "$LIVEZ_JSON"
else
    gate_fail_reason="/livez unreachable >${LIVEZ_BUDGET_S}s"
fi

if [ -z "$gate_fail_reason" ] && READYZ_JSON="$(gate_readyz)"; then
    _log "readyz OK:"; _logv "$READYZ_JSON"
    # M7 (commission v0.16.6 component 2, interface contract): /readyz MAY
    # carry an optional advisory field `detail.inflight_turns` (int,
    # RUNNING tasks with fresh heartbeats — NESTED under `detail`, NOT
    # top-level; the producer emits it inside the `detail` object, see
    # daemon/services/readiness.py:225-230). When present and > 0,
    # print a non-blocking INFO note — busy-daemon promote is LEGAL
    # under the new amnesty (operator awareness only; this is a
    # deliberate departure from the pre-v0.16 era where a busy gate
    # was a fail).
    #
    # POSITION-INDEPENDENT EXTRACTION: scope by the `detail` key first
    # (via _json_sub — a depth-counting balanced-brace parser that finds
    # the FIRST occurrence of `"detail"` and extracts the `{...}` body),
    # THEN extract `inflight_turns` from that body. A top-level regex
    # would silently match by accident of position (e.g. the JSON
    # already has a top-level `"reasons"` key; future fields could
    # collide). The nested-only path is the producer's contract; the
    # flat-top-level path was rejected in Dev A's review and would
    # also break against the `reasons` alias duplicate at both levels.
    #
    # TOLERATE all of:
    #   - field absent (older daemon / base build; `detail` key missing)
    #   - field present but null (Python twin emits None → JSON null)
    #   - field present but non-integer (defensive parse — the producer
    #     is a Python int but a future proxy or stub might write garbage)
    #   - field = 0 (silent — no note; promote is a normal exit)
    #   - /readyz unreachable (READY gate fails FIRST, rc 1 → no note)
    # Degrade gracefully via _json_sub + _json_field; no jq dependency
    # (the repo's shell toolchain has none). The note is informational;
    # the gate result above is the only thing that matters for promotion.
    if [ -n "$READYZ_JSON" ]; then
        DETAIL_JSON="$(_json_sub "$READYZ_JSON" detail 2>/dev/null)" || DETAIL_JSON=""
        if [ -n "$DETAIL_JSON" ]; then
            INFLIGHT="$(_json_field "$DETAIL_JSON" inflight_turns 2>/dev/null)" \
                || INFLIGHT=""
            case "$INFLIGHT" in
                ""|null) ;;   # absent or null — older daemon, silent
                *[!0-9]*) _logv "readyz advisory: detail.inflight_turns present but non-numeric ($INFLIGHT) — ignored (defensive)";;
                0) ;;         # present but no in-flight turns, silent
                *)
                    _log "readyz advisory: detail.inflight_turns=$INFLIGHT (busy daemon — promote is legal under v0.16.6 amnesty; operator awareness only)"
                    ;;
            esac
        fi
    fi
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
    # commit — M5: every journal write is checked; a failed write must
    # never let the promote exit 0 with the symlink/journal diverged (see
    # journal_fail_loud in lib.sh).
    OLD_CUR="$CUR"
    journal_set_current "$VERSION"        || journal_fail_loud "commit: journal_set_current $VERSION"
    journal_set_previous "${OLD_CUR:-null}" || journal_fail_loud "commit: journal_set_previous ${OLD_CUR:-null}"
    journal_close_txn                     || journal_fail_loud "commit: journal_close_txn"
    journal_history_append commit "promote $VERSION committed (gate+soak green; previous=${OLD_CUR:-none})" \
                                          || journal_fail_loud "commit: history append"
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

# T5 manifest gate FIRST (D-FA4.5): previous must EXIST, be rollback_safe,
# and NOT be quarantined (M4 — a quarantined previous is a known-bad release;
# flipping onto it is what quarantine exists to prevent).
if [ -z "$PREV" ]; then
    journal_close_txn                       || journal_fail_loud "halt(no-previous): close_txn" 78
    journal_set_current "$VERSION"          || journal_fail_loud "halt(no-previous): set_current" 78
    journal_set_previous "null"             || journal_fail_loud "halt(no-previous): set_previous" 78
    journal_history_append halt "gate fail ($gate_fail_reason) with NO previous release — halt-for-human, daemon rests on $VERSION (degraded, alerted)" \
                                            || true   # history is advisory; state writes above are the contract
    _warn "HALT-FOR-HUMAN: no previous release to roll back to — daemon stays on $VERSION (degraded). Notify + human recovery per ADR-028."
    lock_release
    exit 78
fi
if journal_is_quarantined "$PREV"; then
    journal_close_txn                       || journal_fail_loud "halt(quarantined-previous): close_txn" 78
    journal_set_current "$VERSION"          || journal_fail_loud "halt(quarantined-previous): set_current" 78
    journal_history_append halt "gate fail ($gate_fail_reason) but previous $PREV is QUARANTINED (known-bad) — halt-for-human, NO repoint (M4: never auto-flip onto a quarantined release)" \
                                            || true
    _warn "HALT-FOR-HUMAN: previous release $PREV is QUARANTINED — daemon stays on $VERSION (degraded). NO repoint (M4). Human picks the next version (ADR-028)."
    lock_release
    exit 78
fi
if [ ! -d "$REL/$PREV" ]; then
    journal_close_txn                       || journal_fail_loud "halt(missing-previous): close_txn" 78
    journal_set_current "$VERSION"          || journal_fail_loud "halt(missing-previous): set_current" 78
    journal_history_append halt "gate fail ($gate_fail_reason) but previous $PREV is MISSING (evicted/manually deleted) — halt-for-human, NO repoint" \
                                            || true
    _warn "HALT-FOR-HUMAN: previous release $PREV missing — daemon stays on $VERSION (degraded). NO repoint (ADR-005 M5)."
    lock_release
    exit 78
fi
PREV_SAFE="$(manifest_field "$PREV" rollback_safe 2>/dev/null)"
if [ "$PREV_SAFE" != "true" ]; then
    journal_close_txn                       || journal_fail_loud "halt(unsafe-previous): close_txn" 78
    journal_set_current "$VERSION"          || journal_fail_loud "halt(unsafe-previous): set_current" 78
    journal_history_append halt "gate fail ($gate_fail_reason) but previous $PREV has rollback_safe=$PREV_SAFE — halt-for-human, NO repoint (schema-drift guard D-FA4.5)" \
                                            || true
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
# P3: the rollback hand-back obeys the SAME supervision selection — on a
# unit-managed host the rollback target is handed back to the unit (no
# nohup fallback). Failure = halt journal event + B4 leave-txn-open (the
# open flipped txn makes the next launcher start sweep-ROLL-BACK again).
if ! restart_via_launcher; then
    _warn "rollback: unit hand-back of $PREV FAILED — halting for human (txn left open — the next launcher start sweep-rolls-back to previous; NO nohup fallback)"
    journal_history_append halt "rollback hand-back of $PREV failed — halt-for-human; txn left open for sweep recovery (no nohup fallback — Amendment #1)" \
                              || true   # history is advisory; the open txn IS the B4 contract
    exit 1
fi

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
# M4 no-strand guard: when the promote WAS a same-version re-promote
# (CUR == VERSION), the "old current" IS the version being quarantined
# below — recording it as previous would strand previous==quarantined
# and arm a later auto-rollback onto a known-bad release. In that case leave
# previous at its pre-promote value (== the release just rolled back onto;
# degenerate previous==current, but never quarantined — a later failed
# promote still finds a valid, distinct rollback target once current moves).
if [ "$CUR" != "$VERSION" ] || [ -z "$CUR" ]; then
    journal_set_previous "${CUR:-null}"    || journal_fail_loud "rollback bookkeeping: set_previous ${CUR:-null}"
fi
journal_set_current "$PREV"                || journal_fail_loud "rollback bookkeeping: set_current $PREV"
NEW_COUNT="$(journal_count_rollback 1)" || journal_fail_loud "rollback bookkeeping: counter/cooldown (cooldown NOT armed — treat as active until verified)"
journal_history_append rollback "auto-rollback $VERSION → $PREV (gate fail: $gate_fail_reason; re-gate ${REGATE_FAIL:-green})" \
                                            || true
journal_quarantine "$VERSION"              || journal_fail_loud "rollback bookkeeping: quarantine $VERSION (it stays promotable — operator must quarantine manually or re-stage)"
journal_history_append quarantine "$VERSION quarantined after gate failure (skipped by future promotes)"

if [ -n "$REGATE_FAIL" ]; then
    # ADR-028: the rollback target failing its gate is halt-for-human; the
    # rollback-class event already counted above.
    journal_close_txn                     || journal_fail_loud "re-gate halt: close_txn" 78
    journal_history_append halt "previous $PREV failed re-gate ($REGATE_FAIL) — halt-for-human with release list; recovery = user-chosen version via promote" \
                                          || true
    _warn "HALT-FOR-HUMAN: rollback landed on $PREV but re-gate FAILED ($REGATE_FAIL). Human picks the next version (ADR-028)."
    lock_release
    exit 78
fi

if [ "$NEW_COUNT" -ge "$ROLLBACK_CAP_24H" ]; then
    journal_close_txn                     || journal_fail_loud "cap halt: close_txn" 1
    journal_history_append halt "rollback cap $ROLLBACK_CAP_24H/24h reached (count=$NEW_COUNT) — halt-for-human; promotes refused until the window resets" \
                                          || true
    _warn "HALT-FOR-HUMAN: rollback cap reached ($NEW_COUNT/$ROLLBACK_CAP_24H in 24h) — environment restored to $PREV; further promotes refused until window reset (ADR-005 D2)"
    lock_release
    exit 1
fi

journal_close_txn                         || journal_fail_loud "rollback complete: close_txn"
journal_history_append rollback "rollback complete: serving $PREV_BIN_VERSION on :$PORT; cooldown armed (${COOLDOWN_S}s); window count $NEW_COUNT/$ROLLBACK_CAP_24H" \
                                          || true
_log "ROLLBACK COMPLETE: current=$PREV serving ${PREV_BIN_VERSION:-?}; quarantine=$VERSION; cooldown ${COOLDOWN_S}s; count $NEW_COUNT/$ROLLBACK_CAP_24H"
retention_evict
lock_release
exit 1   # the PROMOTE failed — environment recovered, human attention wanted

