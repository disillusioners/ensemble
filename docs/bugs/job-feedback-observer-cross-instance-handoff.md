# Bug: JobFeedbackObserver cross-instance handoff crash

> **✅ Resolved (2026-06).** The observer handoff crash is resolved by the ExecutionGate + CM callback migration. For the current architecture, see [`docs/architecture/message-processing-and-correlation.md`](../architecture/message-processing-and-correlation.md).

**Date**: 2026-06-10
**Severity**: High (stuck job queue, repeated crashes, no recovery)
**Status**: Open — deeper architectural issue, not a small fix

## Summary

There are two paths for picking up the next pending job in the queue:

1. **Quick event path** — `JobFeedbackObserver` fires when an `instance_lifecycle` event arrives, immediately picks up the next pending job and tries to spawn it. **This is the broken path.**
2. **Polling path** — `JobProcessor._process_next_job` runs on a polling interval, picks up the next pending job with proper guards. **This is the correct path.**

When an `experiencer` (or any other) `task` job's instance completes, the quick path fires before the poller can. The quick path has no instance-scoping and no `job_type` awareness, so it picks the next pending job in the project and unconditionally calls `spawn_instance_with_mcp`. If that next job targets a *different* instance that is still busy, the spawn hits `UniqueViolation` on `instances_pkey`, the job is marked FAILED with a retry, and the message becomes stuck in the queue indefinitely (the poller correctly SKIPs it on each poll, the observer keeps crashing on it each handoff).

## Reproduction (from production log)

```
12:08:06 - external_opencode session v1-re-review finishes its task on root project dadc814a
12:08:11 - graph tool call: external_opencode_wait_for_result
12:08:16 - child_reports: Instance 993ae643... (v1-re-review worker) status=COMPLETED
12:08:16 - job transition: 73058291 (worker task) processing -> completed
12:08:16 - job transition: 73f6413b (user message for efb507da) pending -> processing
12:08:16 - observer (QUICK PATH) calls spawn_instance_with_mcp(instance_id=efb507da)
12:08:17 - UniqueViolation on instances_pkey for instance_id=efb507da
12:08:17 - job 73f6413b marked failed, retry scheduled
12:19:55 - poller (CORRECT PATH) picks up retry, sees message job for busy instance, SKIPs
            (and will keep SKIPping forever, while the observer keeps crashing on it)
```

The user was on root instance `efb507da` (busy) and sent a follow-up message. The daemon queued it as a `message`-type job (the "queue on running instance" design is unfinished). Meanwhile a different root instance `993ae643` (the v1-re-review worker) finished and triggered the buggy quick-path handoff.

## The two paths side-by-side

| Concern | Quick path (broken) | Polling path (correct, use as reference) |
|---|---|---|
| Entry point | `JobFeedbackObserver._process_event` (event-driven, fires on `instance_lifecycle:completed`) | `JobProcessor._process_next_job` (interval-driven) |
| Location | `daemon/services/job_feedback_observer.py:335-368` | `daemon/services/job_processor.py:481-565` |
| `job_type == "message"` SKIP pre-check | ❌ None | ✅ Lines 493-505: skips if another MESSAGE is processing for the same instance |
| `job_type` routing (message vs task) | ❌ Always calls `spawn_instance_with_mcp` | ✅ Lines 525-536: routes `message` to `MessageJobHandler.handle()`, `task` to spawn |
| Scope of "next pending" lookup | ❌ `_get_next_job(project_id)` (line 344) — project-wide, includes jobs for *other* instances | ✅ Each `pending` list is fetched per-queue (line 482 onward) — queue-scoped |
| Instance guard before spawn | ❌ None | ✅ Implicit: per-queue iteration and the `message` pre-check |
| Result on busy target | ❌ UniqueViolation crash → FAILED + retry | ✅ SKIP → job stays PENDING, no crash |
| Latency | ✅ Zero-delay (event-driven) | ⚠️ Polling interval (typically 1-2s) |

The quick path trades correctness for latency. It was added as an optimization but never received the guards the poller already has.

## What the broken path does (step by step)

`JobFeedbackObserver._process_event` at `daemon/services/job_feedback_observer.py:204-368`:

1. Filter for `instance_lifecycle` events (line 215).
2. Look up the `JobItem` by `instance_id` (line 239). If found and PROCESSING, transition it to COMPLETED — this part is correct.
3. **Unconditionally enter the handoff block** (lines 335-368):
   - Call `self._job_queue_service._get_next_job(job.project_id)` (line 344) — returns the next pending job in the project, regardless of which instance it targets.
   - Call `start_job(next_job.job_id)` to transition PENDING→PROCESSING (line 350).
   - **Unconditionally** call `self._instance_manager.spawn_instance_with_mcp(instance_id=started_job.instance_id, ...)` (line 358) — this is the crash site.
4. On exception, mark job FAILED with retry (lines 363-368).

## What the correct path does (step by step) — REFERENCE FOR FIX

`JobProcessor._process_next_job` at `daemon/services/job_processor.py:481-565`:

1. Get the next pending job for the queue (line 482).
2. **Pre-check for `message` jobs** (lines 493-505): if another MESSAGE is already processing for the same instance, SKIP — leave the job PENDING, no DB transitions, no spawn. This is the busy-instance guard.
3. Call `start_job(job.job_id)` (line 512) to transition PENDING→PROCESSING.
4. **`job_type` routing** (lines 525-536): if `job_type == "message"`, route to `MessageJobHandler.handle()` (which routes to the running instance's graph). Otherwise fall through to spawn.
5. **Spawn** (lines 540-545) only for `task` jobs, with the instance_id from `start_job`.
6. **Enqueue the message** (lines 555-559).
7. On any error, mark job FAILED.

This is the model the observer's handoff should mirror.

## Why the architectural violation matters

The "consecutive jobs in the same queue" comment on line 337 is the architectural intent. The actual code violates it by:

1. **Calling `_get_next_job(project_id)`** (line 344) — project-scoped, NOT queue-scoped or instance-scoped.
2. **Not checking `next_job.instance_id`** against the instance that just completed.
3. **Unconditionally calling `spawn_instance_with_mcp(instance_id=next_job.instance_id, ...)`** (line 358) — which crashes if that instance is already running.

The fix is to make the quick path mirror the polling path's guards.

## Impact

- Stuck message jobs (and potentially other queued jobs) for instances that happen to share a project with another active instance.
- Repeated `UniqueViolation` errors in logs, scheduled retries that never succeed.
- No way for the user to send a message to a running instance — every message gets trapped in the queue.
- The "queue on running instance" feature is blocked until this is resolved.

## Related (deferred)

The user has separately noted that the "queue messages on running instances" feature is unfinished. The proper injection-into-running-graph mechanism (route `message` jobs to `MessageJobHandler` from the observer too, plus the actual message-injection API) is out of scope for this bug and is being designed separately.

## What is NOT the fix

- Making `instance_repository.create()` idempotent (insert-or-update on `instance_id`): silences the UniqueViolation but doesn't solve the real problem. The job is still stuck, the spawn is still semantically wrong (the root instance is already running — you don't need to spawn it), and the message still never reaches the graph.
- Adding a "skip if instance exists" guard in the observer: same issue — silences the crash but doesn't deliver the message.
- Bumping retries: doesn't address the root cause.

## Possible fix directions (not implemented)

The fix should make the quick path match the polling path's correctness, while keeping the zero-delay latency benefit. Two options:

**Option A — Quick path delegates to the polling path entirely**

Replace the handoff block (lines 335-368) with a call that nudges the poller to wake up early. The poller is already correct; we just lose the zero-delay handoff.

```python
# Replace lines 335-368 with:
if job.project_id:
    # Wake the poller to pick up the next job (which has all the right guards)
    self._job_queue_service.notify_pollers(project_id=job.project_id)
    return
```

**Pros**: small change, poller logic is the single source of truth.
**Cons**: loses zero-delay handoff latency.

**Option B — Mirror the polling path's guards in the quick path**

Apply the same checks in the handoff:

1. Scope `_get_next_job` by `queue_id` (use `list_pending_by_queue`) instead of `list_pending_by_project` — line 344.
2. If `next_job.job_type == "message"`, route to `MessageJobHandler.handle()` instead of `spawn_instance_with_mcp` — line 358.
3. Add the busy-instance SKIP pre-check (poller's lines 493-505) before the spawn.
4. If the targeted instance is already running, leave the job PENDING and return.

**Pros**: keeps zero-delay handoff.
**Cons**: duplicates poller logic in two places, which the bug itself demonstrates is fragile (drift between paths is what caused this).

The right fix is probably Option A (delegate to the poller), with the option to revisit zero-delay handoff once the "queue on running instance" feature is properly designed.

## Files involved

- `daemon/services/job_feedback_observer.py` — buggy quick-path handoff (lines 335-368)
- `daemon/services/job_processor.py` — correct polling path with SKIP pre-check (lines 493-505) and `job_type` routing (lines 525-536)
- `daemon/services/message_job_handler.py` — correct handler for `message` jobs (not reached by the observer's quick path)
- `daemon/services/instance_lifecycle.py:316` — `instance_repository.create` is the crash site
- `daemon/services/child_reports.py` — child report handler (related but separate path)
