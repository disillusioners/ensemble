# Post-Deadlock Fix E2E — Auto-Resume Message Loss Finding

**Date:** 2026-08-02
**Trigger:** `test_paused_auto_resume_unchanged` FAILED during post-fix E2E suite
**Related commit:** `cced02cc` (NOT `338a72b0`)

## Symptom
Test sends a message to a PAUSED instance expecting auto-resume. The daemon returns `auto_resumed: true` but the message is silently lost — never persisted, never queued, never processed.

## Root Cause
Commit `cced02cc` removed the `cascade_resume` fallback in `resume_processing_job` (§9.4 — answer-gate design decision). When an instance is paused BEFORE its initial Task is claimed (no in-flight task, no explicit handle), `resume_processing_job` returns `None` with `route_outcome=invalid_or_missing_handle`.

The PAUSED message route in `messages.py` trusts this return value and responds `auto_resumed: true` without calling `enqueue_message_job`. The user's message is lost.

## Timeline (from daemon log)
```
11:57:05  POST /messages 200           ← LONG_PROMPT stored as PENDING job 03c9e970
11:57:05  POST /pause   200            ← instance paused BEFORE task claimed
11:57:05  JobProcessor SKIP            ← "instance is paused, staying PENDING"
11:57:05  resume_processing_job called ← message='AUTO_RESUME_TEST_MARKER'
11:57:06  route_outcome=invalid_or_missing_handle ← no suspended/paused turn
11:57:36  poll timeout → job 03c9e970 claimed ← only OLD message runs
11:57:42  Instance completed           ← marker NEVER delivered
```

## Impact
- Breaks C4 contract: "PAUSED must remain a hard auto-resume trigger"
- User sends message to paused instance → message silently dropped
- Frontend UX: pause→send-message→resume silently loses the message

## Fix Recommendation (Option A — minimal, targeted)
In `daemon/routers/messages.py` PAUSED branch (~line 242), when `resume_processing_job` returns `None`, fall through to normal `enqueue_message_job`:

```python
if job_result is None:
    await manager.enqueue_message_job(
        instance_id=resumed_id,
        message=message.content if is_target else "resume",
        source="api_resume_fallback",
        images=message.images if is_target else None,
    )
```

This keeps the §9.4 answer-gate design intact and only fixes the user-message-to-PAUSED-idle edge case.

## NOT Related to `338a72b0`
The self-deadlock fix touched only `repository.py` (+47 lines). It did NOT touch `messages.py` or `manager.py`. The guard worked correctly during the failing test.
