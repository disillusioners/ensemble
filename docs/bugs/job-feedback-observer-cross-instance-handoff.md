# Bug: JobFeedbackObserver cross-instance handoff crash

**Date**: 2026-06-10
**Severity**: High (stuck job queue, repeated crashes, no recovery)
**Status**: Open — deeper architectural issue, not a small fix

## Summary

When a tool-spawned worker instance (e.g., an `external_opencode` session like `v1-re-review`) completes, the `JobFeedbackObserver` treats its `instance_lifecycle:completed` event as a job completion and fires a "trigger next pending job" handoff. That handoff has no instance-scoping and unconditionally calls `spawn_instance_with_mcp` for the next pending job in the project. If the next pending job targets a *different* root instance that is still busy, the spawn hits `UniqueViolation` on `instances_pkey`, the job is marked FAILED with a retry, and the message becomes stuck in the queue indefinitely (the poller correctly SKIPs it on each poll, the observer crashes on it each handoff).

## Reproduction (from production log)

```
12:08:06 - external_opencode session v1-re-review finishes its task on root project dadc814a
12:08:11 - graph tool call: external_opencode_wait_for_result
12:08:16 - child_reports: Instance 993ae643... (v1-re-review worker) status=COMPLETED
12:08:16 - job transition: 73058291 (worker task) processing -> completed
12:08:16 - job transition: 73f6413b (user message for efb507da) pending -> processing
12:08:16 - observer calls spawn_instance_with_mcp(instance_id=efb507da)
12:08:17 - UniqueViolation on instances_pkey for instance_id=efb507da
12:08:17 - job 73f6413b marked failed, retry scheduled
12:19:55 - poller picks up retry, sees message job for busy instance, SKIPs
            (and will keep SKIPping forever, while the observer keeps crashing on it)
```

The user was on root instance `efb507da` (busy) and sent a follow-up message. The daemon queued it as a `message`-type job (the "queue on running instance" design is unfinished). Meanwhile a different root instance `993ae643` (the v1-re-review worker) finished and triggered the buggy handoff.

## Root cause

`JobFeedbackObserver` (`daemon/services/job_feedback_observer.py`) processes every `instance_lifecycle` event the same way:

1. Look up the `JobItem` by `instance_id` (line 239: `get_job_by_instance(instance_id)`).
2. If found and PROCESSING, transition it to COMPLETED.
3. **Unconditionally** call `_get_next_job(job.project_id)` and spawn that job's instance.

The "next pending" handoff (lines 335-368) assumes the next pending job belongs to the instance that just completed. That assumption is wrong because:

- `instance_lifecycle:completed` events fire for *every* instance, including tool-spawned workers, not just the root instance whose own job chain is being driven by the queue.
- Tool-spawned workers get their own `JobItem` rows (created when the worker's task is enqueued via the queue), even though the architecture intends jobs to bind to root instances only.
- `_get_next_job` returns whatever is next in the project's queue, regardless of which instance is targeted.
- The handoff has no `job_type` branch, so `message` jobs (which should go to `MessageJobHandler.handle()`, not spawn) are mishandled the same as task jobs.

## Architectural violation

Per the intended design, `JobItem.instance_id` should reference **root instances only**. Tool-spawned workers (OpenCode sessions, sub-agents, etc.) should not have `JobItem` rows — they are internal to the root instance's execution, not first-class jobs in the queue.

The current code creates `JobItem` rows for these workers anyway. As a result:

- The observer can't tell "this instance's own job finished, hand off" apart from "this worker finished, the root is still busy".
- A worker completing fires the same handoff as a root completing, and the next pending job in the project may target a *different* root.

## Compounding issues

1. **Cross-instance handoff**: `_get_next_job(project_id)` is not instance-scoped. It returns the next job for the whole project, so a worker completing on instance A can pick up a job for instance B.
2. **No `job_type` branch in observer**: `MessageJobHandler` exists for `message` jobs and is wired into `JobProcessor._process_next_job` (line 528), but the observer's handoff path has no such branch. It always calls `spawn_instance_with_mcp`.
3. **No busy-instance check in observer**: `JobProcessor._process_next_job` has a pre-check (line 493-505) that SKIPs message jobs for busy instances. The observer has no equivalent guard.
4. **Failed retry chain compounds the stuck state**: After the UniqueViolation, the job is retried. The retry lands back in PENDING. The poller SKIPs it (busy instance). The observer crashes on it (if a worker completes). Net result: the job never makes progress.

## Impact

- Stuck message jobs (and potentially other queued jobs) for instances that happen to share a project with tool-spawned workers.
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

1. **Enforce the architecture**: stop creating `JobItem` rows for tool-spawned workers. Worker lifecycles should not flow through the queue's `JobFeedbackObserver` at all. Workers should report back to their parent via a different mechanism (already partially in place via `child_reports.py`).
2. **Scope the handoff by instance**: the "trigger next pending" code should only fire when the *root* instance's own job completed. One way: include a flag on the lifecycle event (e.g., `event.data.is_root_completion` or `event.data.parent_id is None and queue_id matches`). Another way: have the observer check the instance's `parent_id` and only hand off when `parent_id is None`.
3. **Branch on `job_type` in the observer**: if the next pending job is a `message` job, route to `MessageJobHandler.handle()` instead of `spawn_instance_with_mcp`. (Still requires the message-injection feature to actually deliver the message.)
4. **Add a busy-instance check**: before calling `spawn_instance_with_mcp`, check if the targeted instance is already running. If yes, route the job to the appropriate handler (message → `MessageJobHandler`, task → leave PENDING for the poller).

The right fix is probably a combination: (1) + (2) + (4). Item (3) depends on the deferred "queue on running instance" feature work.

## Files involved

- `daemon/services/job_feedback_observer.py` — buggy handoff (lines 335-368)
- `daemon/services/job_processor.py` — poller with the correct SKIP pre-check (lines 493-505)
- `daemon/services/message_job_handler.py` — correct handler for `message` jobs (not reached by the observer)
- `daemon/repositories/job_queue/repository.py:104` — `get_by_instance` returns whatever row matches, no root/worker distinction
- `daemon/services/instance_lifecycle.py:316` — `instance_repository.create` is the crash site
- `daemon/services/child_reports.py` — child report handler (related but separate path)
