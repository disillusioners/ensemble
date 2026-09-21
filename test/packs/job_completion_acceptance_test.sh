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
timeout 280s .venv/bin/pytest \
  tests/job_queue/test_job_result_summary_and_gate.py \
  tests/job_queue/test_round2_council_fixes.py \
  -v --override-ini="addopts=" --tb=short -q 2>&1
EXIT_CODE=$?
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
