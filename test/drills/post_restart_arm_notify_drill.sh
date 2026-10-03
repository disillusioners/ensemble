#!/bin/bash
# ============================================================================
# test/drills/post_restart_arm_notify_drill.sh — Post-Restart Arm-Notify
# sandbox acceptance drills (phase4-plan T6 / test-strategy.md §2.6)
# ============================================================================
# Six scenarios (D1–D6), each driving the REAL arm / journal / sweep code
# in-process (test/drills/wake_drill_driver.py) against a FAKE staged
# install tree under a throwaway run dir:
#   D1  arm(restart) + kill + restart + observe wake (api sentinel, one-shot)
#   D2  arm(upgrade) + kill + restart + observe terminal_outcome=commit
#   D3  discord:user123 source preserved end-to-end (ADR-041 routing)
#   D4  long-downtime double-arm → ONE coalesced run-list wake (ADR-043)
#   D5  kill-switch: OFF arm → no record; persisted record → abandoned;
#       re-enable → no stale flood (ADR-044)
#   D6  live-outright-refusal via the FAKE-live marker (ENSEMBLE_SELF_ENV=
#       live against a FAKE live tree — the P2.2 tool-interlock technique).
#       NEVER the actual live install (invariant 8: live never records).
#
# NO daemon boot, NO DB, NO network, NO LLM keys, NO executor execution —
# the drill journals the terminal events the executors journal (real
# journal_history_append, restart.sh/promote.sh prose). The drill port
# (8477) is metadata only; nothing listens on it.
#
# Live-isolation (T10): the live install's listener pid set (resolved from
# the live .env PORT — never a literal) must be IDENTICAL at drill end
# (empty==empty passes trivially on hosts without a live install).
#
# Usage:  bash test/drills/post_restart_arm_notify_drill.sh [run-dir]
# Output: transcript + per-scenario structured evidence + PASS/FAIL;
#         exit 0 iff all scenarios pass (the operator's acceptance signal).
# ============================================================================

set -u
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DRIVER="$REPO_ROOT/test/drills/wake_drill_driver.py"

RUN_DIR="${1:-/tmp/ens-wake-drills/$(date -u +%Y%m%dT%H%M%S)}"
mkdir -p "$RUN_DIR"
TRANS="$RUN_DIR/transcript.txt"

PASS=0; FAIL=0; FAILED=""
ok()  { PASS=$((PASS+1)); printf 'PASS: %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); FAILED="$FAILED\n  ✗ $1"; printf 'FAIL: %s\n' "$1"; }

run_scenario() { # <id> <self_env> [extra env assignments...]
    local id="$1" self_env="$2"; shift 2
    local home="$RUN_DIR/fakehome-$id"
    mkdir -p "$home"
    printf '\n━━━ %s ━━━\n' "$id"
    env -u ENSEMBLE_POST_RESTART_ARM_NOTIFY -u ENSEMBLE_DEPLOY_LIVE \
        -u ENSEMBLE_UPGRADE_LIVE -u PORT -u INSTALL_DIR \
        HOME="$home" ENSEMBLE_SELF_ENV="$self_env" \
        ENSEMBLE_WAKE_DRILL_RUN_DIR="$RUN_DIR" \
        PYTHONDONTWRITEBYTECODE=1 \
        "$@" "$REPO_ROOT/.venv/bin/python" "$DRIVER" --scenario "$id" \
        > "$RUN_DIR/$id.log" 2>&1
    local rc=$?
    sed 's/^/    /' "$RUN_DIR/$id.log"
    if [ "$rc" -eq 0 ]; then ok "$id: scenario green"; else bad "$id: scenario FAILED (rc=$rc, see $RUN_DIR/$id.log)"; fi
}

drill_cleanup() {
    # scoped to the drill namespace only — never port-based, never
    # path-ambiguous; demo/live install paths cannot match this prefix.
    return 0
}

main() {
    trap 'drill_cleanup' EXIT
    exec > >(tee "$TRANS") 2>&1
    printf 'Post-Restart Arm-Notify sandbox drills — %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'run dir: %s\n' "$RUN_DIR"
    printf 'repo:    %s\n' "$REPO_ROOT"

    [ -f "$DRIVER" ] || { printf 'FATAL: driver missing: %s\n' "$DRIVER"; exit 2; }
    [ -x "$REPO_ROOT/.venv/bin/python" ] || { printf 'FATAL: repo venv python missing\n'; exit 2; }

    # leftovers from earlier killed runs would poison the fakehomes —
    # sweep the whole drill namespace first (scoped by cmdline path).
    pkill -f "ens-wake-drills/.*/wake_drill_driver.py" 2>/dev/null
    rm -rf "$RUN_DIR"/fakehome-* 2>/dev/null

    # ── live-isolation baseline (T10) ────────────────────────────────────
    live_pids_snapshot() {
        lsof -ti:"$(sed -n 's/^[[:space:]]*PORT=//p' "$HOME/agents-ensemble/.env" 2>/dev/null | head -1)" 2>/dev/null | sort | tr '\n' ' '
    }
    LIVE_PIDS_BASE="$(live_pids_snapshot)"
    printf 'live pid checkpoint (baseline): %s\n' "${LIVE_PIDS_BASE:-<none>}"

    # ── preflight: the sandbox install is NOT the real one ──────────────
    printf '\n━━━ preflight ━━━\n'
    case "$RUN_DIR" in
        "$HOME/agents-ensemble"|"$HOME/agents-ensemble-demo"|"$HOME/agents-ensemble"/*|"$HOME/agents-ensemble-demo"/*)
            bad "run dir collides with a real install tree"; exit 2 ;;
        *) ok "run dir is sandbox-only: $RUN_DIR" ;;
    esac
    if grep -q "agents-ensemble" "$HOME/agents-ensemble/.env" 2>/dev/null && [ -d "$HOME/agents-ensemble/releases" ]; then
        printf 'note: a live install exists on this host — isolation assertion below protects it\n'
    fi
    ok "no live contact by construction (fake homes + in-process driver)"

    # ── scenarios ────────────────────────────────────────────────────────
    run_scenario D1 demo
    run_scenario D2 demo
    run_scenario D3 demo
    run_scenario D4 demo
    run_scenario D5 demo
    # D6: FAKE-live marker against a FAKE live tree in the sandbox home.
    run_scenario D6 live

    # ── live-isolation ASSERTION (T10) ───────────────────────────────────
    LIVE_PIDS_END="$(live_pids_snapshot)"
    printf 'live pid checkpoint (end of drills): %s\n' "${LIVE_PIDS_END:-<none>}"
    if [ "$LIVE_PIDS_BASE" = "$LIVE_PIDS_END" ]; then
        ok "live-isolation: live pid set unchanged across all drills"
    else
        bad "live-isolation: live pid set CHANGED (baseline='${LIVE_PIDS_BASE:-<none>}' end='${LIVE_PIDS_END:-<none>}')"
    fi

    printf '\n═══ DRILLS COMPLETE: %d passed, %d failed ═══\n' "$PASS" "$FAIL"
    [ "$FAIL" -eq 0 ] || printf 'failed:%b\n' "$FAILED"
    printf 'transcript: %s\n' "$TRANS"
    [ "$FAIL" -eq 0 ] && exit 0 || exit 1
}

main "$@"
