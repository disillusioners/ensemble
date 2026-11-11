#!/usr/bin/env bash
# Test Pack: upgrade_tool_interlock_unit_test
# Tests: tests/unit/tools/test_upgrade_journal.py + tests/unit/tools/test_upgrade_tools.py
# Timeout: 2 minutes (120s)
#
# P2.2 (self-restart-upgrade phase 2, Dispatch C): the system_upgrade tool
# surface + its journal/lock/nonce interlocks — the review-minor-#3 closure
# making the phase's session-smoke evidence reproducible from the tree.
# Covers phase2-plan T4/T5/T6/T7/T8 acceptance in pytest form:
#   - release_info field parity 1:1 vs scripts/upgrade/status.sh on a
#     /tmp fixture written by the REAL lib.sh (T6)
#   - upgrade_status run_id round-trip: armed pending_op → in-flight read →
#     terminal read keyed by the SAME run_id (the cross-death join)
#   - the full refusal-token matrix (every distinct reason=<token>, each its
#     own test) for both actor tools + read-pair env gates + fail-open reads
#   - the LIVE 3-factor gate under a FAKE live marker + /tmp fixture ONLY:
#     each factor missing alone refuses; spoofed origins stamp no window;
#     fabricated user_confirmed alone refuses; the full PASS case consumes
#     the nonce and arms; replay refused; TTL expiry
#   - sequencing (D2/D3): armed tools return with ZERO spawns
#     inside the call (restart banner RESTART SCHEDULED, upgrade banner
#     UPGRADE ARMED — N5 wording, P2.3); marker + journal set instead;
#     second arm → pipeline-busy naming the active run_id
#   - dry_run default TRUE: zero journal mutation, no lock, no marker
#   - journal primitives: kill -9 atomic-write safety (real SIGKILL, bounded
#     <2s), torn detection, BOTH lock stale-break branches, lib.sh interop
#     both directions, ADR-034 splice tolerance (tested, not violated)
#   - executor spawn: env allowlist (API-key-class + ENSEMBLE_UPGRADE_LIVE
#     absent), process-group independence, no-BashProcessRegistry static pin
# v0.15.3 P1 (tool-lane live promote fix, 2026-09-26) — extends the SAME
# two unit files; no pack-structure change:
#   - verified-arm (P1 Item 1): is_verified_arm 5-conjunct truth table
#     (incl. env=demo/dev/sandbox + op=None rows) + _verified_arm_extras
#     (name-frozen) + the drain seam appending --f2-verified-closed and
#     ENSEMBLE_UPGRADE_LIVE/F2_VERIFIED_NOTE ONLY for a verified live arm
#     (unverified path byte-identical — the pre-existing argv/allowed-set
#     pins above ARE the unverified side) + executor_orphaned pre-spawn
#     refusal + the sole-production-caller pin
#   - arm preflight before the nonce burn (P1 Item 3): each refusal token
#     its own test — executor-scripts-unavailable (reused),
#     preflight-argv-unconstructable, preflight-argv-malformed (nonce
#     NEVER burned on refusal)
#   - child-exit watcher (P1 Items 2+4): the drain spawn seam enqueues
#     EVERY armed executor into the sweep service's reaper queue (patching
#     daemon.tools.upgrade_journal.spawn_executor — NEVER subprocess.Popen,
#     P2.2 gotcha); reaper journals executor_exit (real exit-78 child) /
#     executor_still_running on timeout with benign-detach / continues
#     after a journal-write OSError; boot sweep clears a stale pending_op;
#     live-executor liveness guard skips clearing; pending_actions GC
#   - terminal-outcome filter (P1 Item 5): nonce_consumed no longer
#     masquerades as TERMINAL; label demoted to awaiting-executor (pending)
# D1 + W2 + W6 (promote-full-auto lane 1, 2026-11-11) — extends the SAME
# two unit files; no pack-structure change:
#   - D1 (ONE stale-txn reclaim mechanism): the shared predicate
#     (upgrade_journal.txn_is_reclaimable — the shell lane's comp3 FAST
#     PATH rule: heartbeat-stale AND owner-dead ⇒ reclaimable; a live owner
#     OR a fresh heartbeat ⇒ refuse, fail-closed) + the claim-before-adopt
#     disposal (upgrade_journal.reclaim_stale_txn) — consumed by the TOOL
#     busy-check arms (system_upgrade / system_restart) AND the daemon-side
#     sweep (the 2026-11-11 starvation: three disagreeing mechanisms); the
#     pins cover the live-owner+fresh-hb refusal, the dead-owner+stale-hb
#     reclaim (tool lane AND sweep), the fresh-hb refusal regardless of
#     owner state, and the single-winner claim
#   - W2 (sibling checks for the restart/rollback lanes): the system_restart
#     arm's stop-script sibling gate — token preflight-stop-script-missing
#     — the restart lane's mirror of PR1's promote-arm gate (the restart
#     stop step resolves the SAME two legs)
#   - W6 (taxonomy sweep): the refusal tokens preflight-stop-script-missing,
#     preflight-argv-unconstructable, preflight-argv-malformed and
#     scripts-dir-unresolvable are registered in the upgrade-target
#     _full_doc_ list AND in the UPGRADE_TOKENS / RESTART_TOKENS registries;
#     preflight-stop-script-missing ALSO lives in the restart-target
#     _full_doc_ list (MT-3 tidier round 3: the two argv tokens are
#     upgrade-lane only — the restart lane cannot construct a promote argv,
#     so claiming BOTH lists for them was false)
# All fixtures /tmp-only; live/production NEVER touched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: upgrade_tool_interlock_unit_test ==="

cd "$PROJECT_DIR"

# Unit pack — 2 min hard limit. Dual-layer timeout.
# Internal watchdog (Layer 2): 110s `timeout` wrap below — interrupts hung tests.
# Layer 1 (outer) is the dispatcher's `timeout 120s` wrap.
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
  tests/unit/tools/test_upgrade_journal.py \
  tests/unit/tools/test_upgrade_tools.py \
  --tb=short -q 2>&1 || EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
