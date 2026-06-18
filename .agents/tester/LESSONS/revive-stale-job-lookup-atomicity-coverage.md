
# 2026-06-18: Revive Stale Job Lookup Fix — Coverage Gap Lesson

## Context
Testing the `fix/revive-stale-job-lookup` bug fix revealed a coverage gap pattern worth remembering.

## Key Finding: Atomicity Coverage Gap
The existing test `test_terminate_resets_waiting_for_to_zero_on_instance_repo` only checked that exactly one `update(waiting_for=0)` call existed. But a regression that split writes into two separate `update()` calls (one for status, one for waiting_for) would STILL pass that test.

## Lesson
When verifying atomic operations (single update() with multiple fields):
- Don't just count update() calls
- Assert that ONE call carries ALL expected fields together
- Example: `assert len(update_calls) == 1; assert update_calls[0].kwargs == {"status": "terminated", "waiting_for": 0}`

## New Test Added
`test_terminate_writes_status_and_waiting_for_in_single_atomic_update` (commit 3eca1484) closes this gap.

## Quick Fix Applied
`test_terminate_instance_success` in `tests/services/test_manager.py` needed assertion update because the atomic update() changed the call pattern (commit d26bf795).

## Branch State
The fix branch `fix/revive-stale-job-lookup` was merged into `latest` as commits b1218739, 9376ab4d, 82182b26. Test-only commits d26bf795 and 3eca1484 sit on top.
