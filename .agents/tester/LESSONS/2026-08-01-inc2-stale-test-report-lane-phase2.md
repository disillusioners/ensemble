# Stale Test: test_report_lane_phase2.py — Increment 2 Carve-out Deletion

**Date:** 2026-08-01
**Branch:** `feature/turn-reconciler-named-transitions`
**Commit:** `c5192f6f` (Inc 2: delete carve-out pile)
**Test:** `tests/test_report_lane_phase2.py:512` — `TestReportLaneGuard::test_process_message_blocked_by_cross_system_guard`

## Root Cause

Increment 2 deleted the message_id-matching carve-out SQL from `claim_pending_task`, replacing it with the simpler `_active_jobitem_with_inflight_task_sql` predicate that only blocks when there's a backing Task with `t.work_id = j.job_id`.

The Inc 2 commit (`c5192f6f`) updated 4 of 5 affected test files but **missed `test_report_lane_phase2.py`**. The test at line 512 sets up a JobItem with `job_metadata.message_id="msg-user-123"` and a PROCESS_MESSAGE Task with `message_id="msg-other-789"`, expecting the cross-system guard to BLOCK the claim (different message_ids). Under the new simplified guard, this block no longer fires because there's no backing Task with matching `work_id`.

## Also Stale (passes for wrong reason)

The companion test `test_process_message_unblocked_when_message_id_matches` (line 523) uses the same setup pattern and now passes only by accident — under the new guard, the matching case is claimed for a different reason (the EXISTS check fails) than the test's docstring claims.

## Resolution Options

1. **Delete the stale test** — the carve-out is gone, the contract no longer exists
2. **Rewrite to exercise the new guard** — create a backing Task with `work_id` matching the JobItem's `job_id`
3. **Audit line 523** — update docstring/assertion to match the new behavior

## Severity

🟠 Important — not a production regression (the Inc 2 change is correct), but a test-suite hygiene issue. The test documents a contract that was deliberately removed.

## Discovery

Found by the concurrency pack worker (instance d8986de0) during full regression testing of Inc 2.
