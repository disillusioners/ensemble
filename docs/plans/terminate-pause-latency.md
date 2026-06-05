# Plan: Reduce Pause / Terminate Latency (Cancel + Cascade + Job State Transition)

| Field | Value |
|---|---|
| **Status** | DRAFT — awaiting team review |
| **Author** | Kilo (proposed), prepared 2026-06-05 |
| **Scope** | `daemon/services/instance_lifecycle.py`, `daemon/services/job_processor.py`, `daemon/services/dispatch_event_bus.py`, `daemon/services/message_job_handler.py`, `daemon/repositories/instance/repository.py` |
| **Estimated effort** | ~1 day implementation + ~1 day test/review |

---

## 1. Context

The following timeline was observed in production logs:

```
23:06:18  POST  /api/instances/9a230d55.../pause  → 200
23:06:18  Cancelled graph task for instance 9a230d55...
23:06:18  Paused instance 9a230d55...
23:06:18  Paused instance 3bbc43af...
23:06:18  Graph execution cancelled for instance 9a230d55... (message_id=ac126fb6...)
23:06:18  DELETE /api/instances/9a230d55... → 200
23:06:22  Cascading terminate to child instance: 3bbc43af...   ← 4 s after DELETE
23:06:49  JobProcessor: MESSAGE job 0cc15623... terminated (instance status=terminated)
23:06:49  Job transition: 0cc15623... | processing -> cancelled (abort)   ← 27 s after DELETE, 31 s after pause
```

User-visible behavior: deleting an instance that has a child and an in-flight MESSAGE job takes **~30 s** to fully settle, and the cascade to the child appears to lag the DELETE by several seconds.

This plan proposes four targeted changes to compress that window to < 5 s with deterministic state transitions.

---

## 2. Root Cause Analysis

Three structural issues were identified (verified by reading the code paths):

### RC1 — MESSAGE job `processing → cancelled` is never written in the terminate path
`daemon/services/instance_lifecycle.py:505-514` (step 7.5) calls `cancel_message_job` for every MESSAGE job of the instance. `cancel_message_job` (`daemon/services/message_job_handler.py:267-292`) does this for a `processing` job:

```python
elif job.status == "processing":
    # Signal CancellationToken — handler will catch OperationCancelledError
    cts = self._active_tokens.get(job_id)
    if cts:
        cts.cancel(reason=CancellationReason.MANUAL)
    # ... falls through without writing the job row
```

Only the `CancellationToken` is signalled. The DB row stays in `processing` until *either* the handler coroutine observes `OperationCancelledError` / `asyncio.CancelledError` and calls `complete_job(..., CANCELLED)` (lines 231-237, 244-253), *or* the JobProcessor's 30 s sweep catches the mismatch (`daemon/services/job_processor.py:279-289`).

> Note: step 7.6 (lines 516-549) does call `complete_job(..., CANCELLED)` for processing jobs, but it runs **after** 7.5 and only catches jobs whose status is still non-terminal. The MESSAGE jobs in 7.5 are still in `processing`, so 7.6 *should* catch them — but 7.5's `cancel_message_job` may race with 7.6 by flipping the token. In practice the 27 s delay shows 7.6 is not the path that lands the transition. Investigation is required to confirm (see Open Question Q1).

### RC2 — `JobProcessor` is a 30 s polling loop, not event-driven for terminations
`daemon/api.py:320-325` constructs `JobProcessor(..., poll_interval=30.0)`. `_process_loop` (`daemon/services/job_processor.py:138-175`) gates on `await self._dispatch_bus.wait_for_job(None, timeout=30.0)` (line 146-149) — so it can sleep up to 30 s before sweeping queues. There is **no wakeup path from `terminate_instance` to the JobProcessor** for terminations.

### RC3 — Graph task `cancel()` is fire-and-forget
`daemon/services/instance_lifecycle.py:438-442` and `:629-631`:

```python
graph_task = self._manager._graph_tasks.pop(instance_id, None)
if graph_task and not graph_task.done():
    graph_task.cancel()    # NOT awaited
```

`task.cancel()` schedules `CancelledError` to be raised on the coroutine's next yield. The actual unwinding happens later, asynchronously, when the in-flight LLM HTTP request returns or times out. The HTTP DELETE handler returns 200 immediately; the graph coroutine continues to consume resources, emit SSE events, and hold MCP connections until the LLM call resolves.

The same fire-and-forget pattern exists in `daemon/manager.py:1059-1062` (TTL eviction) and `daemon/routers/projects.py:818` (project cleanup). See Scope Notes for whether those should be fixed in the same change.

### RC4 (Diagnostic) — 4 s DELETE → cascade lag
The "Cascading terminate to child instance" log fires inside `terminate_instance` at line 432 *immediately* after the `meta.children` check. There is no `await` between the DB read (line 422) and the log emission, so a 4 s gap cannot originate inside this single call. The most plausible explanation: when DELETE on 9a230d55 ran, `meta.children` was already empty (possibly mutated by the pause that happened milliseconds earlier, or never populated for this parent/child pair), so the cascade log was **not** emitted by the DELETE handler. The 23:06:22 log was emitted by a *different* code path — most likely the project cleanup or TTL eviction in `daemon/manager.py:1059` or `daemon/routers/projects.py:818`, which use their own child-discovery mechanism. Diagnosis requires a `trigger` tag on the log line (Fix 4).

---

## 3. Goals & Non-Goals

### Goals
- **G1.** Compress pause → job-row-cancelled from 30 s to < 5 s in the common case.
- **G2.** Compress DELETE → child-instance-terminated log from 4 s to < 100 ms.
- **G3.** Make the DELETE response time predictable (currently dominated by `task.cancel()` racing with the LLM socket).
- **G4.** Preserve existing semantics: paused instance is still resumable; child cascade is still recursive; token-based cancellation still works for in-flight handlers.
- **G5.** Add diagnostic logging so the next occurrence is self-explanatory.

### Non-Goals
- **NG1.** Reworking the JobProcessor's polling strategy for new-job dispatch (that's a separate concern with different latency targets).
- **NG2.** Changing the LLM client's per-call timeout values.
- **NG3.** Touching the TTL eviction path (`manager.py:1059`) or project cleanup path (`routers/projects.py:818`) beyond what is required to keep them consistent with Fix 2.
- **NG4.** Removing the 30 s `JobProcessor` poll interval — it remains a safety net.

---

## 4. Proposed Changes

### Fix 1 — Write MESSAGE job terminal state in terminate, not in the poll loop

**Why.** Closes RC1. The terminate path is already the authoritative point for marking jobs as cancelled (step 7.6 does this for non-MESSAGE jobs). MESSAGE jobs should be treated symmetrically.

**File:** `daemon/services/instance_lifecycle.py`
**Lines:** 505-514 (step 7.5)

Replace the current body of the `for msg_job in message_jobs:` loop with logic that branches on the job's current status:

```python
for msg_job in message_jobs:
    try:
        if msg_job.status == "pending":
            # PENDING → CANCELLED via the canonical message-handler path
            await self._job_queue_service.cancel_message_job(msg_job.job_id)
        elif msg_job.status == "processing":
            # Processing MESSAGE jobs: signal the token (soft cancel) AND
            # synchronously write the terminal row. The row write is the
            # source of truth; the token is best-effort for the running handler.
            await self._job_queue_service.cancel_message_job(msg_job.job_id)
            await self._job_queue_service.complete_job(
                msg_job.job_id,
                demand_state=DemandState.CANCELLED,
                error="Instance terminated during message processing",
            )
        else:
            # terminal or unknown — skip
            continue
    except Exception as e:
        logger.warning(
            f"Failed to cancel MESSAGE job {msg_job.job_id[:8]}... on terminate: {e}"
        )
```

**Idempotency / races.** Two concerns:

1. The handler coroutine may *also* call `complete_job(..., CANCELLED)` when it observes `OperationCancelledError` / `asyncio.CancelledError` (`message_job_handler.py:231-253`). This is a benign double-write **only if `complete_job` is idempotent** — i.e., rejects writes when the job is already in a terminal state.
   - **Action required:** verify `complete_job` / `terminate_job` in `daemon/services/job_queue_service.py:1256-1259` and `daemon/repositories/job_queue/repository.py:599` reject state transitions from a terminal status. If they don't, add that guard. (Open Question Q2.)
2. The `cancel_message_job` token signal is now strictly informational; the handler can still observe it and try to write — same idempotency requirement applies.

**Test plan.**
- Unit: spawn instance → enqueue MESSAGE job → call `terminate_instance` → assert job row is `cancelled` within the same await.
- Unit: spawn instance with a *blocked* handler (e.g., mock handler that never observes cancellation) → call `terminate_instance` → assert job row is still `cancelled` (i.e., Fix 1 doesn't depend on the handler cooperating).
- Integration: existing `tests/job_queue/` and `tests/integration/test_terminate_cascade.py` should still pass.

---

### Fix 2 — Bounded-await the cancelled graph task

**Why.** Closes RC3. Makes DELETE latency deterministic; ensures MCP/SSE cleanup runs before the response.

**File:** `daemon/services/instance_lifecycle.py`
**Lines:** 438-442 (terminate path) and 629-631 (pause path)

Replace the bare `graph_task.cancel()` with a bounded wait. Apply identically in both places.

```python
graph_task = self._manager._graph_tasks.pop(instance_id, None)
if graph_task and not graph_task.done():
    graph_task.cancel()
    try:
        # Bounded wait: graph task unwinds when its in-flight LLM call returns
        # or hits the LLM client's socket timeout. We cap so a stuck LLM call
        # doesn't make DELETE hang; the LLM client timeout is the real backstop.
        await asyncio.wait_for(asyncio.shield(graph_task), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning(
            f"Graph task {instance_id[:8]}... did not unwind within 5s; "
            f"relying on LLM socket timeout to free resources"
        )
    except asyncio.CancelledError:
        # Defensive: shield prevents the outer cancel from reaching the inner
        # task, but a propagation from elsewhere can still bubble up.
        logger.debug(f"Graph task {instance_id[:8]}... cancelled during await")
    logger.info(f"Cancelled graph task for instance {instance_id[:8]}...")
```

**Why 5 s.** Long enough to flush a normal SSE write and let the LLM client's typical 10-30 s timeout take over (we want to be gone before that). Short enough that DELETE feels responsive. Configurable later if real-world data suggests a different value.

**`asyncio.shield` rationale.** Protects the inner `await` from being cancelled if the *outer* coroutine (the request handler) is itself cancelled. Without shield, a client disconnect during the 5 s wait would leak the unwinding graph task and we'd return to the same problem we're trying to fix.

**Scope notes.**
- `daemon/manager.py:1059-1062` (TTL eviction): this runs from a background task, not a request handler. Blocking it for 5 s is acceptable but not necessary for user-visible latency. **Out of scope for this PR** — leave a TODO comment.
- `daemon/routers/projects.py:818` (project cleanup): runs from a project-delete request, latency matters. **In scope** — apply the same bounded-await pattern, but as a separate commit so this PR is reviewable in isolation. *Confirm with team whether to bundle.*

**Test plan.**
- Unit: mock a graph task that sleeps 2 s → `terminate_instance` returns in ~2 s with `cancelled` status.
- Unit: mock a graph task that sleeps 10 s → `terminate_instance` returns in ~5 s with a warning log; task continues unwinding in the background.
- Integration: SSE subscriber sees the final `status_change: terminated` event before the HTTP DELETE response.

---

### Fix 3 — Event-driven wakeup of JobProcessor on instance termination

**Why.** Closes RC2. Even after Fix 1 makes the MESSAGE job row transition synchronous, the JobProcessor still has other responsibilities (retry scheduling, deferred queue draining, watcher notifications) that benefit from an immediate wakeup. Cheap to add; complements Fix 1.

**File 1:** `daemon/services/dispatch_event_bus.py`
**Lines:** 1-125 (class `DispatchEventBus`)

Add a public method:

```python
def notify_terminated(self, instance_id: str) -> None:
    """Wake JobProcessor immediately when an instance terminates.
    
    Sets the global event so any project-scoped wait_for_job() call wakes up
    on its next event-loop tick. The JobProcessor's next sweep will then see
    the TERMINATED instance and process the corresponding MESSAGE jobs.
    """
    if self._loop is None:
        return
    
    def _set():
        self._global_event.set()
        # Also wake all known project events (defensive — in case a future
        # JobProcessor refactor narrows the wait scope to a project).
        for event in self._events.values():
            event.set()
    
    try:
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(_set)
        else:
            _set()
    except RuntimeError:
        logger.debug("[TRACE] notify_terminated: SKIP — event loop closed")
```

**File 2:** `daemon/services/instance_lifecycle.py`
**Lines:** end of `terminate_instance` (just before `return True` at line 569)

Add the wakeup call *after* Fix 1's job-cancellation completes:

```python
# 9. Wake the JobProcessor so it can sweep TERMINATED-instance artifacts
# immediately rather than waiting up to 30s for the next poll boundary.
if hasattr(self, "_dispatch_bus") and self._dispatch_bus is not None:
    self._dispatch_bus.notify_terminated(instance_id)
```

**Note on ordering.** The wakeup must happen *after* the job-row transitions (Fix 1) and *after* the DB status update (line 473). Otherwise the JobProcessor may wake, sweep, and find the instance still in a non-terminal state.

**Test plan.**
- Unit: assert `notify_terminated` calls `self._global_event.set()` (use a mock event).
- Integration: start a JobProcessor, put it to sleep on `wait_for_job`, call `terminate_instance`, assert the JobProcessor's `_process_next_job` is invoked within 100 ms (not 30 s).

---

### Fix 4 — Make child cascade robust and diagnostic

**Why.** Closes RC4. Even though the 4 s gap is symptom-of-bigger-issues, two improvements make the next occurrence self-explanatory and prevent a class of similar bugs (cascade silently skipped because `meta.children` is stale or empty).

**File 1:** `daemon/services/instance_lifecycle.py`
**Lines:** 419-433 (the cascade block at the top of `terminate_instance`)

Replace the cascade with a repository-based child lookup, and tag the log line with the trigger:

```python
# Get instance metadata BEFORE modifying state
meta = None
if hasattr(self._manager, '_instance_repository') and self._manager._instance_repository:
    meta = self._manager._instance_repository.get(instance_id)

# Re-entrancy guard
if meta and meta.status == InstanceStatus.TERMINATED.value:
    logger.info(f"Instance {instance_id[:8]}... already terminated, skipping")
    return True

# Cascade to children — use repository as source of truth, not meta.children
# (which may be empty if pause ran first, or stale if children were spawned
# after the meta was last written). The hierarchy table is the canonical store.
if hasattr(self._manager, '_instance_repository') and self._manager._instance_repository:
    try:
        children = self._manager._instance_repository.list_by_parent(instance_id)
    except Exception as e:
        logger.warning(f"list_by_parent failed for {instance_id[:8]}...: {e}; falling back to meta.children")
        children = list(meta.children) if meta and meta.children else []
    for child in children:
        child_id = child.id if hasattr(child, 'id') else child
        logger.info(
            f"Cascading terminate to child instance: {child_id[:8]}... "
            f"(trigger=DELETE, parent={instance_id[:8]}...)"
        )
        await self.terminate_instance(child_id)
```

**Note.** `list_by_parent` already exists at `daemon/repositories/instance/repository.py:329` and uses the `InstanceHierarchy` join table (canonical store). No schema change needed.

**File 2 (optional, smaller change):** apply the same `trigger=...` tag to the pause cascade. Pause already uses `repo.get_tree_ids(root_id)` (line 594) which is also repository-based — the gap in pause is not the same as in terminate, but the trigger tag is still useful for log correlation.

**Test plan.**
- Unit: spawn a parent + 2 children → `terminate_instance(parent)` → assert both children are terminated and the cascade log lines carry `trigger=DELETE`.
- Unit: delete an instance whose `meta.children` is empty but `InstanceHierarchy` still references children → assert cascade still runs (this is the RC4 case).
- Integration: existing `test_terminate_cascade.py` should still pass.

---

## 5. Rollout Ordering

Land as four separate commits, in this order. Each commit is independently revertable.

| # | Commit | Files touched | Risk | Test surface |
|---|---|---|---|---|
| 1 | Fix 2: bounded-await graph task | `instance_lifecycle.py` | Low — only changes wait behavior, not semantics | Unit + integration |
| 2 | Fix 1: synchronous MESSAGE job cancel | `instance_lifecycle.py` | Medium — changes job-row state machine timing; depends on idempotency check (Q2) | Unit + integration + concurrency test |
| 3 | Fix 3: event-driven JobProcessor wakeup | `dispatch_event_bus.py`, `instance_lifecycle.py` | Low — only adds an event set; JobProcessor logic unchanged | Unit + integration |
| 4 | Fix 4: repository-based child cascade + trigger log | `instance_lifecycle.py` | Low — only changes how children are discovered; cascade semantics unchanged | Unit + integration |

**Feature flag (optional).** If the team is risk-averse, wrap Fix 1's synchronous `complete_job` call in a config flag `terminate.sync_cancel_message_jobs: bool = true` for one release cycle, defaulting to `true` in dev and `false` in prod. Not strictly required if the idempotency check (Q2) passes.

---

## 6. Observability Additions

Beyond the `trigger=...` log line in Fix 4, add one structured log at the end of `terminate_instance` summarizing the cleanup:

```python
logger.info(
    f"terminate_instance: {instance_id[:8]}... complete "
    f"(graph_unwind_ms={graph_unwind_ms}, jobs_cancelled={jobs_cancelled}, "
    f"children={len(children)}, duration_ms={duration_ms})"
)
```

Where:
- `graph_unwind_ms` is measured around the Fix 2 bounded await.
- `jobs_cancelled` is the count from the Fix 1 loop.
- `duration_ms` is the wall time for the whole `terminate_instance` call.

Wrap the function body in a `t0 = time.monotonic()` and compute at the end. Negligible overhead, big debuggability win.

**Optional.** Emit a Prometheus counter `instance_terminate_duration_seconds{outcome="ok|error"}` if the daemon already exposes metrics. (Check `daemon/api.py` and `daemon/services/` for existing metrics before adding new ones.)

---

## 7. Open Questions for the Team

- **Q1.** Step 7.6 (`instance_lifecycle.py:516-549`) already calls `complete_job(..., CANCELLED)` for any remaining processing job, which should in theory catch MESSAGE jobs left in `processing` by step 7.5. Why doesn't it? Possible causes: (a) 7.6 runs in the same coroutine, so the token-cancel from 7.5 may not have yielded control yet — but `await` should yield; (b) `complete_job` is rejecting the transition because the job was *already* marked terminal by the token path; (c) some ordering issue with `_repository.find_jobs_by_instance(job_type=None)` excluding MESSAGE jobs. **Action:** reproduce locally and add a `[TRACE]` log to step 7.6 confirming whether MESSAGE jobs are being seen there.
- **Q2.** Is `complete_job` / `terminate_job` idempotent against a job already in a terminal state? Required for Fix 1's safety. If not, we need to add a guard.
- **Q3.** Should Fix 2's bounded-await pattern be applied to the project-cleanup path (`routers/projects.py:818`) in the same PR, or split out?
- **Q4.** Should the 5 s timeout in Fix 2 be configurable via `config.yaml`? Recommend **no** for v1; revisit if real-world data shows it should be tunable.
- **Q5.** Is the `meta.children` field on `InstanceMeta` still needed, or is `InstanceHierarchy` the new source of truth? If only the hierarchy table is used, the field can be removed. (Separate cleanup; out of scope here, but flag for follow-up.)
- **Q6.** Should we add a regression integration test that times `POST /pause` → `DELETE` → `GET /jobs/{id}` and asserts the job is in `cancelled` within, say, 6 s? This would catch future regressions of RC1.

---

## 8. Out of Scope (Explicitly)

- Re-architecting the JobProcessor's 30 s poll into a push model for new jobs.
- Changing LLM client timeouts.
- Removing the 30 s `JobProcessor` poll entirely (it remains a safety net).
- TTL eviction path (`manager.py:1059`) — Fix 2 may be applied later, not in this PR.
- Any change to the SSE / live-hub streaming code beyond what the bounded-await in Fix 2 implies.
- Removing the `meta.children` field on `InstanceMeta` (separate cleanup).

---

## 9. Appendix: Timeline Reconstruction

Mapping observed log lines to the proposed fixes:

| Log line | Timestamp | Root cause | Fixed by |
|---|---|---|---|
| `POST /pause 200` | 23:06:18 | — | — |
| `Cancelled graph task for instance 9a230d55...` | 23:06:18 | log emitted at `cancel()` call site, not at unwind | — |
| `Paused instance 9a230d55...` | 23:06:18 | pause path | — |
| `Paused instance 3bbc43af...` | 23:06:18 | pause path (`pause_instance_cascade` tree walk) | — |
| `Graph execution cancelled for instance 9a230d55...` | 23:06:18 | log emitted when handler observes the cancel | Fix 2 makes the await bounded |
| `DELETE 200` | 23:06:18 | — | Fix 2 makes the response time ~5 s, not instantaneous-but-lying |
| `Cascading terminate to child instance: 3bbc43af...` | 23:06:22 | emitted by a *second* code path; `meta.children` was empty for the DELETE call | Fix 4 makes the cascade repository-based and tags the trigger |
| `JobProcessor: MESSAGE job ... terminated` | 23:06:49 | emitted by `job_processor.py:282` on the next 30 s poll boundary | Fix 1 makes this happen synchronously in terminate |
| `Job transition: processing -> cancelled` | 23:06:49 | written by `job_processor.py:285-289` | Fix 1 makes this write happen in terminate (line 7.5) |

After all four fixes, the expected post-fix timeline is:

```
T+0ms      POST /pause
T+0ms      Cancelled graph task ...
T+~3s      DELETE ... (after LLM stream finishes or hits socket)
T+~3s      Paused instance ...
T+~3s      Cascading terminate to child ... (trigger=DELETE)
T+~3s      MESSAGE job ... processing -> cancelled (synchronous in 7.5)
T+~3s      JobProcessor: MESSAGE job ... terminated (immediate wake via Fix 3)
T+~3s      DELETE 200
```

i.e. a 3-5 s settle time, bounded by the LLM stream unwind.

---

## 10. Review Checklist (for reviewers)

- [ ] Has Q1 (why step 7.6 doesn't catch MESSAGE jobs) been investigated and resolved?
- [ ] Has Q2 (`complete_job` idempotency) been verified or a guard added?
- [ ] Does Fix 1's `complete_job(..., CANCELLED)` interaction with the handler's later `complete_job` produce the right final state and error message?
- [ ] Does Fix 2's `asyncio.shield` correctly protect against outer-cancel during the 5 s wait?
- [ ] Does Fix 3's wakeup happen *after* Fix 1's DB write and the DB status update? (Race condition if reordered.)
- [ ] Does Fix 4's `list_by_parent` return the same set of children that `meta.children` would, modulo the cases Fix 4 is trying to fix?
- [ ] Are existing tests in `tests/job_queue/` and `tests/integration/test_terminate_cascade.py` updated or still passing?
- [ ] Is the observability log at the end of `terminate_instance` consistent with the daemon's logging style (no `[TRACE]` prefix, structured fields)?
