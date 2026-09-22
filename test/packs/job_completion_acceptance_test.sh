#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: job_completion_acceptance_test ==="
cd "$PROJECT_DIR"

# Acceptance pack for the v0.13.9 round-2 job-completion repair:
# - Empty job-completed event + null result_summary + premature terminal
#   emission fix (DEFECT 1 + DEFECT 2)
# - Dead-letter failed-terminal chain (publisher branch at
#   child_reports.py:4189-4231)
# - Cascade gate reality + root mirror gate (FINDING 1+5)
# - Wedge-resolver deadlock guards (FINDING 2 — stale-readable,
#   empty-final-turn, dead-letter)
# - Stale-task-recovery wedge hook (root only)
# - MESSAGE-job deferral to observer (post-D13 — MessageJobHandler
#   deleted in commit 8d20ffb6)
#
# File pointers for runtime intent-point coverage:
#   Intent 1 (job-completed events carry Result body):
#     tests/job_queue/test_job_result_summary_and_gate.py
#     ::TestObserverResultSummary::test_watchers_receive_non_empty_result_body
#   Intent 2 (result_summary written at completion):
#     tests/job_queue/test_job_result_summary_and_gate.py
#     ::TestObserverResultSummary::test_completed_event_carries_result_summary
#     (asserts the production seam ``manager._get_last_assistant_message_raw``
#     was awaited for the instance — the canonical extraction seam on this
#     lineage; JobItem's mirror column was dropped in Phase 5)
#   Intent 3 (premature terminal emission gated on true subtree completion):
#     tests/job_queue/test_job_result_summary_and_gate.py
#     ::TestRootCompletionGate (4 tests) — ROOT emission gate
#     tests/job_queue/test_job_result_summary_and_gate.py
#     ::TestCascadeCompletionGate (3 tests) — cascade lane
#     tests/job_queue/test_round2_council_fixes.py
#     ::TestRootMirrorGate (2 tests) — root mirror downgrade + happy path
#     tests/job_queue/test_round2_council_fixes.py
#     ::TestCascadeEmissionGateFailOpen (2 tests) — wrap fail-open contract
#   Intent 4 (failed/dead-letter events carry Error body):
#     tests/job_queue/test_round2_council_fixes.py
#     ::TestWedgeResolverDeadLetter::test_dead_letter_failed_terminal_emits_failed_with_error_body
#     (publisher branch child_reports.py:4189-4231)
#     tests/job_queue/test_round2_council_fixes.py
#     ::TestWedgeResolverDeadLetter::test_dead_letter_non_failed_terminal_emits_completed
#     (control branch — terminal COMPLETED → "completed" with no error)
#
# BEHAVIORAL DELTAS (documented in tests; see BLOCKER section in dispatch):
# - JobItem mirror columns ``result_summary`` / ``error_message`` were
#   dropped in Phase 5 (daemon/repositories/job_queue/repository.py:50-65);
#   the source-commit lineage's ``row.result_summary == X`` assertions are
#   replaced with production-seam verification
#   (``manager._get_last_assistant_message_raw.assert_awaited_once_with``
#   or watcher-notification-body substring matches)
# - ``Instance.waiting_for`` was dropped in D10 — the cascade lane on this
#   lineage is bus-authoritative and returns ``(False, None, None)`` from
#   ``_update_parent_on_child_complete`` (the bus callback owns parent
#   terminal); source-commit's ``transitioned is True`` / ``completed_parent_id == "parent-1"``
#   assertions are replaced with the new wire shape
# - ``daemon.services.message_job_handler`` was deleted in commit
#   8d20ffb6 (D12); the message-job deferral acceptance test exercises
#   the SAME intent via ``JobFeedbackObserver._process_event`` with
#   ``bus_pending`` toggled to simulate the defer + finalize path
#
# Intent 5 — v0.13.9 result_summary / job-completed emission-surface
# regression test (fix/job-completed-result-arm, 2026-09-22):
# - The producer→consumer→event-row pipeline was never re-wired on the
#   new ``Task.result`` durable home after Phase 5 Batch 2 dropped the
#   JobItem mirror columns. The Intent5 regression test exercises the
#   FULL real-daemon emission surface against a running daemon (skip-if-
#   not-running guard; mock LLM is fine — the seam under test is the
#   daemon's, not the LLM's). It asserts on FOUR surfaces:
#     (a) /api/jobs/{job_id}/events SSE → terminal ``event: completed``
#         → ``data.result_summary`` non-null and not the fallback marker.
#     (b) GET /api/jobs/{job_id} → ``result_summary`` non-null.
#     (c) Read-only DB query — event row ``kind='job_completed'`` for
#         the ``job_id`` with ``data.result_summary`` non-null.
#     (d) /api/notifications/stream SSE → notification for the instance
#         carries ``result_summary``.
# - Pre-fix (v0.13.9 base): all four surfaces fail. Post-fix (this
#   commit set): all four pass.
#
# KNOWN PRODUCTION-CODE DEFECT (BLOCKER — flagged in dispatch):
# - ``child_reports.py:2007`` reads ``instance.waiting_for`` which doesn't
#   exist on this lineage's SQLModel; the production fail-open wrap at
#   lines 4133-4143 (``_root_completion_gate`` exception handler) catches
#   the AttributeError and treats the gate as passed. Tests that exercise
#   the gate's true semantics patch the gate via
#   ``patch.object(service, "_root_completion_gate", ...)`` so the
#   production wrap isn't relied on for those scenarios. See
#   ``tests/job_queue/test_round2_council_fixes.py`` line 343 docstring.
#
# Runtime: serial pytest (no xdist — see PACKS.md xdist sensitivity
# section; this acceptance set is xdist-sensitive).
# Self-timer (Layer 2): 280s
# Caller wraps `timeout 300` (Layer 1) per PACKS.md dual-layer pattern.
# ``EXIT_CODE=0; ... || EXIT_CODE=$?`` mirrors the Intent5 wrap at :131-134 —
# closes the set -euo pipefail trap (a bare failing pytest terminates the
# shell before the verdict logic at :153-156 runs, so the mock-FAIL and
# TIMEOUT verdict lines become unreachable dead code).
EXIT_CODE=0
timeout 280s .venv/bin/pytest \
  tests/job_queue/test_job_result_summary_and_gate.py \
  tests/job_queue/test_round2_council_fixes.py \
  -v --override-ini="addopts=" --tb=short -q 2>&1 || EXIT_CODE=$?
if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
fi

# Intent5 — real-daemon emission-surface test. Skip-if-no-daemon guard
# inside the test (pytest.mark.skipif on ``_daemon_running()``; plus the
# F1 env guard, which refuses a prod-like resolved POSTGRES_DB).
# Mock LLM is fine — the seam under test is the daemon's, not the LLM's.
# NOTE: Intent5 needs a daemon WITH an LLM upstream — use
# ``./dev_with_mock.sh`` (sanctioned mock LLM); plain ``./dev.sh`` has no
# upstream (:4001 refused) and no job can complete.
#
# F5 gating contract (2026-09-22) — implements LESSONS/
# 2026-09-22-intent5-skip-guard-coverage-hole.md rule 2 ("a PASS without
# proof the flagship EXECUTED is a partial verdict") and closes council
# MINOR #4 ("Intent5 exit code recorded but never gated"):
#   * Intent5 RAN and FAILED (daemon available) → pack FAILS (exit 1).
#     A live emission-surface regression can no longer hide behind the
#     mock layer.
#   * Intent5 SKIPPED (no daemon / PG unreachable / F1 env refusal /
#     nothing collected) → LOUD distinct verdict
#     "RESULT: PASS-WITH-SKIP (intent5=SKIPPED: <reason>)", exit 0.
#     Exit-choice rationale: the 30 mock-layer cases are the pack's core
#     contract and the pack must stay runnable in daemon-less
#     environments (CI/cron) — a nonzero exit on skip would regress
#     that. The LESSONS hazard (silent exit-0 PASS masking an
#     unexecuted flagship) is closed by making the skip LOUD and
#     NAMED instead; consumers can grep the verdict line to tell the
#     two PASS shapes apart.
INTENT5_CODE=0
INTENT5_OUT="$(timeout 90s .venv/bin/pytest \
  tests/e2e/test_result_summary_emission.py \
  -v --override-ini="addopts=" --tb=short -ra -q 2>&1)" || INTENT5_CODE=$?
echo "$INTENT5_OUT"
echo "RESULT: intent5_exit_code=$INTENT5_CODE"

SKIP_REASON=""
if [ "$INTENT5_CODE" -eq 5 ]; then
  SKIP_REASON="exit 5: no tests collected — e2e prerequisites absent in this environment"
elif [ "$INTENT5_CODE" -ne 0 ]; then
  # Nonzero and not the "nothing collected" code: tests RAN (daemon was
  # available) and failed — this is exactly the regression the pack
  # must surface. 124 (timeout) lands here too.
  echo "RESULT: FAIL (intent5 failed with daemon available — emission-surface regression; exit=$INTENT5_CODE)"
  exit 1
else
  # Exit 0: either executed-and-passed (no SKIPPED lines) or
  # skipped-by-guard (LOUD SKIPPED reason lines via -ra).
  SKIP_REASON="$(printf '%s\n' "$INTENT5_OUT" | grep -m1 '^SKIPPED' || true)"
fi

if [ "$EXIT_CODE" -ne 0 ]; then
  echo "RESULT: FAIL (mock layer exit=$EXIT_CODE)"
  exit 1
fi

if [ -n "$SKIP_REASON" ]; then
  echo "RESULT: PASS-WITH-SKIP (mock layer PASS; intent5=SKIPPED: $SKIP_REASON)"
  exit 0
fi

echo "RESULT: PASS (mock layer PASS; intent5 EXECUTED and PASSED — all four emission surfaces asserted)"
exit 0
