#!/bin/bash
# ============================================================================
# scripts/upgrade/restart.sh — intentional-restart executor payload
# (P2.2 T7 / phase2-plan D2 / architecture D-FA1.3)
# ============================================================================
# FIRED BY (in order of likelihood):
#   1. the post-turn callback (PRIMARY — daemon.tools drain fires this
#      daemonized at exact turn-end after system_restart armed the op);
#   2. manual / fallback invocation (daemon died between tool-return and
#      the callback — the journal pending_op is the durable authority and
#      this script completes it; boot sweep converges the rest, D-FA5.4).
#
# SEQUENCE:
#   0 preflight   resolve env (lib.sh) · live guard · journal + pending_op
#                validation (kind=restart, run_id match) · lock ADOPTION
#                (owner → this pid; run_id must match the armed op)
#   1 grace      bounded courtesy wait for turn-completion flush (the
#                primary trigger fires post-turn, so the default is short;
#                SINGLE-TERM's own graceful drain does the real work)
#   2 stop       scripts/stop-ensemble.sh (D6: SIGTERM-bounded, ownership-
#                scoped, NEVER a raw kill — SINGLE-TERM contract)
#   3 start      detached launcher re-exec (restart_via_launcher — the
#                launcher does NOT respawn on clean exit, so the executor's
#                detached re-exec is REQUIRED, not optional)
#   4 gate       /livez ≤60s (ADR-016) + version verify vs the CURRENT
#                release manifest binary_version (ADR-027; skipped with a
#                warning when no current manifest resolves)
#   5 journal    history 'restart' event · close the restart txn
#                (in_flight=null) · clear pending_restart + pending_op ·
#                lock release
#
# LOCK PROTOCOL (D-FA5.1 — the protocol is the contract): the tool acquired
# rollback.lock.d at ARM time (owner = the arming daemon's pid). This
# script ADOPTS it: the lock's run_id file must equal --run-id (external
# interference otherwise → refusal), then owner is rewritten to this pid so
# the ownership-guarded heartbeat/release work for the executor. If the
# lock is absent or mismatches, REFUSE (halt event; the pending_op remains
# for the boot sweep / operator).
#
# USAGE:
#   bash scripts/upgrade/restart.sh <demo|live|sandbox> --run-id <r-...> \
#        [--reason "<text>"] [--grace-s <n>]
#   (sandbox also needs INSTALL_DIR=<dir> PORT=<port> [POSTGRES_DB=<db>])
#   The target is REQUIRED (positional arg or TARGET env) — no silent
#   default; absent/invalid → exit 78 before anything else.
#
# EXIT CODES: 0 restarted + gated · 1 gate failed (markers still cleared —
# the launcher's own burst protection owns a crash-looping daemon) ·
# 78 refusal (env / journal / lock-adoption / live guard).
#
# NO raw kills anywhere in this script: the stop is ALWAYS
# stop-ensemble.sh (SINGLE-TERM ownership-scoped contract).
# ============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_TAG="upgrade-restart"

TARGET_ARG=""
RUN_ID=""
REASON=""
GRACE_S=5
args=("$@")
i=0
while [ $i -lt ${#args[@]} ]; do
    arg="${args[$i]}"
    case "$arg" in
        demo|live|sandbox) TARGET_ARG="$arg" ;;
        --run-id)   i=$((i + 1)); RUN_ID="${args[$i]:-}" ;;
        --reason)   i=$((i + 1)); REASON="${args[$i]:-}" ;;
        --grace-s)  i=$((i + 1)); GRACE_S="${args[$i]:-}" ;;
        -h|--help)  sed -n '2,52p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "restart: unknown flag '$arg' — see --help" >&2; exit 78 ;;
    esac
    i=$((i + 1))
done

# shellcheck source=scripts/upgrade/lib.sh
. "$SCRIPT_DIR/lib.sh"

# M6 (P2.2 fix pass 2026-08-23): the target must be EXPLICIT — the old
# silent ${TARGET:-demo} default let a no-arg invocation operate on the
# REAL demo install. Require the positional target arg or a TARGET env;
# absent → refusal 78 (resolve_env's own 78 handles invalid values).
UP_TARGET_SEL="${TARGET_ARG:-${TARGET:-}}"
if [ -z "$UP_TARGET_SEL" ]; then
    _warn "explicit target required — pass <demo|live|sandbox> as the first arg or set TARGET (no silent default)"
    exit 78
fi
resolve_env "$UP_TARGET_SEL"
require_live_guard "$UP_TARGET"
echo_env_triple

if [ -z "$RUN_ID" ]; then
    _warn "explicit --run-id required (the armed pending_op's run id)"
    exit 78
fi
case "$GRACE_S" in
    ''|*[!0-9]*) _warn "invalid --grace-s='$GRACE_S' (digits only)"; exit 78 ;;
esac
[ "$GRACE_S" -gt 600 ] && GRACE_S=600

# ═══════════════════════════ 0. PREFLIGHT ══════════════════════════════════
_log "preflight: env triple · journal · pending_op run_id=$RUN_ID · lock adoption"

if ! journal_init; then
    _warn "cannot initialize journal at $(journal_path)"
    exit 78
fi
if ! J="$(journal_read)"; then
    _warn "journal unreadable/TORN — halt-for-human (repair before any pipeline action)"
    exit 78
fi

# pending_op must exist, be kind=restart, and match the run id.
POP="$(_json_sub "$J" pending_op)"
if [ -z "$POP" ] || [ "$POP" = "null" ]; then
    _warn "no pending_op in the journal — nothing armed (run stage/arm via system_restart or check the run id)"
    exit 78
fi
POP_KIND="$(_json_field "$POP" kind)"
POP_RUN="$(_json_field "$POP" run_id)"
if [ "$POP_KIND" != "restart" ]; then
    _warn "pending_op kind=$POP_KIND is not a restart — refusing (this script completes restart ops only)"
    exit 78
fi
if [ "$POP_RUN" != "$RUN_ID" ]; then
    _warn "pending_op run_id=$POP_RUN does not match --run-id $RUN_ID — refusing (external interference?)"
    exit 78
fi

# The restart txn must be open (kind=restart).
INF="$(_json_sub "$J" in_flight)"
INF_KIND="$(_json_field "$INF" kind)"
if [ "$INF_KIND" != "restart" ]; then
    _warn "journal in_flight kind='${INF_KIND:-none}' is not a restart txn — refusing (state divergence; inspect $(journal_path))"
    exit 78
fi

# ── Lock ADOPTION (D-FA5.1; see header) ─────────────────────────────────────
LOCK="$(lock_dir_path)"
if [ ! -d "$LOCK" ]; then
    journal_history_append halt "restart executor run_id=$RUN_ID: pipeline lock MISSING at adoption — refusing (manual cleanup?); pending_op left for boot sweep"
    _warn "pipeline lock missing at adoption — refusing; pending_op left in place"
    exit 78
fi
LOCK_RUN="$(cat "$LOCK/run_id" 2>/dev/null)"
if [ "$LOCK_RUN" != "$RUN_ID" ]; then
    journal_history_append halt "restart executor run_id=$RUN_ID: lock run_id='${LOCK_RUN:-?}' mismatch — refusing (another owner?); pending_op left for boot sweep"
    _warn "lock run_id='${LOCK_RUN:-?}' ≠ --run-id $RUN_ID — refusing"
    exit 78
fi
printf '%s\n' "$$" > "$LOCK/owner" 2>/dev/null || {
    _warn "lock adoption failed (cannot write owner) — refusing"
    exit 78
}
lock_heartbeat
_log "pipeline lock adopted (run_id=$RUN_ID, owner pid $$)"

# D-FA5.1 safety net: every failure path AFTER adoption (stop failure exit 1,
# journal_fail_loud exits) must not leave the adopted lock dangling. Same
# trap form as promote.sh; lock_release is a silent no-op when the dir is
# already gone (normal paths release deliberately before exit), so the
# deliberate releases below never double-fire noisily.
trap 'lock_release' EXIT

# Re-stamp the txn owner to the executor (advisory identity in the journal).
INF_TARGET="$(_json_field "$INF" target)"
case "$INF_TARGET" in ""|null) INF_TARGET_JSON="null" ;; *) INF_TARGET_JSON="\"$INF_TARGET\"" ;; esac
journal_update "in_flight" \
    "{\"kind\":\"restart\",\"target\":$INF_TARGET_JSON,\"started_at\":\"$(_json_field "$INF" started_at)\",\"flipped\":false,\"owner_pid\":$$,\"run_id\":\"$RUN_ID\"}" \
    || _warn "could not re-stamp in_flight owner_pid (continuing — advisory field)"

# ═══════════════════════════ 1. GRACE WAIT ══════════════════════════════════
# The PRIMARY trigger (post-turn callback) fires this script at exact
# turn-end — the turn's final DB writes may still be flushing, and
# SINGLE-TERM's graceful drain (up to CHILD_STOP_WAIT_S) gives them their
# window. The grace sleep is a courtesy head-start only (bounded 0..600).
if [ "$GRACE_S" -gt 0 ]; then
    lock_heartbeat
    _log "grace wait ${GRACE_S}s (turn-completion flush courtesy)"
    sleep "$GRACE_S"
fi
lock_heartbeat

# ═══════════════════════════ 2. STOP (D6 — SINGLE-TERM) ════════════════════
_log "stop: ownership-scoped SINGLE-TERM via stop-ensemble.sh"
if ! stop_via_stop_script; then
    journal_history_append halt "restart run_id=$RUN_ID: stop FAILED — daemon state unknown; txn left open for boot-sweep convergence"
    _warn "stop-ensemble.sh FAILED — daemon state unknown; txn left open (boot sweep owns convergence)"
    exit 1
fi

# ═══════════════════════════ 3. START (detached launcher) ══════════════════
lock_heartbeat
restart_via_launcher

# ═══════════════════════════ 4. GATE (/livez ≤60s + version) ════════════════
GATE_FAIL=""
if LIVEZ_JSON="$(gate_livez)"; then
    _log "livez OK:"; _logv "$LIVEZ_JSON"
else
    GATE_FAIL="/livez unreachable >${LIVEZ_BUDGET_S}s"
fi

# Version verify against the CURRENT release manifest (ADR-027 — runtime
# truth). A restart does not change the release; no resolvable current
# manifest → warn + skip (degraded-but-known, ADR-033 posture).
if [ -z "$GATE_FAIL" ] && [ -L "$INSTALL_DIR/current" ]; then
    CUR_NAME="$(readlink "$INSTALL_DIR/current")"
    CUR_NAME="${CUR_NAME##*/}"
    WANT_VER="$(manifest_field "$CUR_NAME" binary_version 2>/dev/null)"
    if [ -n "$WANT_VER" ]; then
        gate_version "$WANT_VER" || GATE_FAIL="version verify mismatch (expected $WANT_VER)"
    else
        _warn "no binary_version in current release $CUR_NAME manifest — skipping version verify (degraded)"
    fi
fi

# ═══════════════════════════ 5. JOURNAL FINALIZE ═══════════════════════════
# Single convergence: the restart ACTION completed either way; a gate
# failure is journaled as halt and the markers clear (the launcher's burst
# budget owns a crash-looping daemon — restart-under-burst-abort refusal in
# the tool prevents masking it for FUTURE restarts).
if [ -n "$GATE_FAIL" ]; then
    journal_close_txn                     || journal_fail_loud "restart halt: close_txn" 78
    journal_update "pending_op" "null"    || journal_fail_loud "restart halt: clear pending_op" 78
    journal_update "pending_restart" "null" || journal_fail_loud "restart halt: clear pending_restart" 78
    journal_history_append halt "intentional restart run_id=$RUN_ID completed but GATE FAILED ($GATE_FAIL) — daemon may be down/flapping; launcher burst budget owns recovery" \
                                             || true
    _warn "RESTART GATE FAILED: $GATE_FAIL — markers cleared; launcher burst budget owns recovery"
    lock_release
    exit 1
fi

journal_close_txn                       || journal_fail_loud "restart: close_txn"
journal_update "pending_op" "null"      || journal_fail_loud "restart: clear pending_op"
journal_update "pending_restart" "null" || journal_fail_loud "restart: clear pending_restart"
journal_history_append restart "intentional restart run_id=$RUN_ID complete (reason: ${REASON:-<none>}; SINGLE-TERM + launcher re-exec + /livez gate green)" \
                                          || true
_log "RESTART COMPLETE: run_id=$RUN_ID — serving on :$PORT"
lock_release
exit 0
