# Lessons: Answer Resume Fix — Test Assertion Staleness Patterns

Date: 2026-08-13
Branch: `debug/answer-resume-fix`

## Pattern 1: Production Signature Drift → Mock Assertion Mismatch

**Symptom**: 5 tests in `test_question_deferred_pause_callback.py` failed with `assert_awaited_once_with(instance_id)` — the mock was called with `suspension_reason='awaiting_answer'` kwarg.

**Root cause**: Production `pause_instance_cascade` call sites in `instance_messaging.py` gained a `suspension_reason` kwarg. Tests used `AsyncMock` with exact-match assertions that didn't account for the new arg.

**Key insight**: Two tests used local helper functions as `AsyncMock(side_effect=fn)`. When production added a kwarg, AsyncMock forwarded it to the side-effect callable — which then raised `TypeError` because the helper only accepted one positional arg. Updating only the assertions would have left the side-effect calls broken.

**Fix**: Update both assertions AND side-effect helper signatures (add `suspension_reason: str | None = None` param).

**Prevention**: When changing a function's call signature, grep test files for `assert_awaited_once_with` / `assert_called_with` patterns that reference the function.

## Pattern 2: Migration Staleness — ResumeTurn Semantics Change

**Symptom**: 3 e2e tests in `test_full_chain_turn_reconciler.py` failed asserting `TaskStatus.CANCELLED.value` for post-ResumeTurn task state.

**Root cause**: Phase 4b/4c migration (2026-08-12) changed `ResumeTurn` transition from `PAUSED→CANCELLED` to `PAUSED→PENDING`. The same `work_id` now stays live for WorkerPool re-claim (closing the T2–T4 race window). Tests were not updated.

**Key insight**: These tests failed in <1.5s (fast-fail mode) — the "fast-fail" signal indicates assertion drift, not a real product regression or timeout. When a test fails in sub-seconds when expected to take minutes, suspect stale assertions.

**Fix**: Mechanically replace `TaskStatus.CANCELLED.value` → `TaskStatus.PENDING.value` for post-ResumeTurn assertions. Also update `find_paused_or_cancellable_turn` selector assertions (returns `None` for PENDING tasks).

**Prevention**: After any lifecycle migration, grep all test files for the old status values: `grep -rn "CANCELLED" tests/e2e/test_full_chain_turn_reconciler.py` catches all stale references in one shot.

## Pre-fix Verification Tip
Before applying staleness fixes, run `grep -n <OLD_VALUE> <test_file>` to surface ALL stale references — prevents a second fix round.
