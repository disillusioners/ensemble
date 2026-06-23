# Execution Gate: Threading Model Decision

**Status**: Implemented (C12a, C12). Per-instance `asyncio.Lock` is the current execution gate primitive. The previous DB-backed lease implementation has been fully removed; `LeaseContention`, `LeaseLostError`, `LeaseHolderKind`, and `LeaseContentionReason` have been deleted from `daemon/services/execution_gate.py`. The `instance_execution_leases` table is no longer written at runtime.

## Decision (TL;DR)

Per-instance **`asyncio.Lock`** owned by the main daemon event loop:

```python
async def run(
    self,
    instance_id: str,
    holder_id: str,      # Accepted-and-ignored for backward compat
    holder_kind: str,    # Accepted-and-ignored for backward compat
    work_fn: WorkFn,
):
    lock = self._lock_for(instance_id)
    async with lock:
        return await work_fn()
```

`holder_id` and `holder_kind` are accepted-and-ignored for backward compat — the `asyncio.Lock` provides no contention-return path, so there is no notion of "who holds the lock" to validate on release. Old call sites (e.g. `InstanceManager`) that pass `lease_repo=...` and a holder identity keep working unchanged.

No heartbeat task. No `recover_stale_leases`. No DB row. The legacy `LeaseContention` / `LeaseLostError` machinery is gone; back-off stays in the dispatchers.

## 1. Which event loop owns the per-instance `asyncio.Lock`?

The **main daemon event loop** — the one `MainLoopBridge.set_loop(...)` is
called with during startup (`daemon/services/main_loop_bridge.py:38-41`).

`ExecutionGateService` is constructed once during `InstanceManager.initialize()`
and stored on `manager._execution_gate`. The manager lives on the main loop,
so `self._locks: dict[str, asyncio.Lock]` is lazily populated **inside `run`**
(not `__init__`) — `asyncio.Lock` binds to the loop that first awaits it,
and creating it from another thread is undefined.

## 2. How do 4 WorkerPool threads contend via `MainLoopBridge`?

Each worker thread (`daemon/services/worker_pool.py:220-249`) calls
`self._task_processor.run_task(task, ...)` (L329). `run_task` ends with:

```python
return MainLoopBridge.run_async(_run(), timeout=timeout)   # task_processor.py:555
```

`MainLoopBridge.run_async` (`main_loop_bridge.py:49-97`) uses
`asyncio.run_coroutine_threadsafe(coro, loop)` (L83). The coroutine is
scheduled on the main loop and the **worker thread blocks** on
`future.result(timeout=...)` (L86) until the coroutine completes.

`_run` (`task_processor.py:549-550`) awaits `processor.process(task, ...)`,
which awaits `self._pipeline.execute(...)` (L221-227), which awaits
`self._execution_gate.run(...)` (`message_processing_pipeline.py:434-439`).

Net effect: every WorkerPool gate call is a coroutine *running on the main
loop*. Four worker threads = four coroutines contending on the main loop's
lock — never four OS threads racing for a `threading.Lock`.

## 3. Is the lock acquired on the main loop or the worker thread?

**Main loop.** The worker thread's `bridge.run_async` (`main_loop_bridge.py:83-86`)
does not touch the lock — it only blocks on a `concurrent.futures.Future`.
Lock acquisition happens entirely inside the coroutine scheduled on the
main loop, so `async with self._lock_for(instance_id)` runs on the loop
that owns the lock. No cross-loop acquisition, no cross-thread `acquire()`.

This is exactly the contract `asyncio.Lock` requires: all `acquire()` /
`release()` calls must happen on the loop the lock was created on. We get
this for free because every gate call funnels through one loop.

## 4. What happens when two threads call `gate.run()` for the same instance?

1. Worker A: `run_async` schedules `gate.run_coro_A` on the main loop;
   blocks on `Future_A`.
2. Worker B: same — `gate.run_coro_B` scheduled, blocks on `Future_B`.
3. Main loop runs A → enters `async with lock` → **acquires**.
4. Main loop runs B → enters `async with lock` → **parks** at the await.
   The loop is free to run other coroutines; B waits in the lock's queue.
5. A finishes, releases. Loop resumes B → acquires, runs, releases.
6. `Future_A.result()` / `Future_B.result()` resolve on their respective
   worker threads.

Sequential execution of `work_fn` for the same instance is **guaranteed**
by the asyncio scheduler — B cannot bypass the lock because contention is
mediated by the scheduler's own awaitable queue.

The same applies to JobQueue: `MessageJobHandler.handle` is already a
coroutine on the main loop and calls `await self._execution_gate.run(...)`.
The resume path does the same (`daemon/manager.py:2817-2826`). Three call
sites, one lock, one loop.

## 5. Is `asyncio.Lock` the right primitive?

**Yes, for single-process.** The single-process constraint is already
documented in the daemon's deployment model — the DB lease's cross-process
semantics were defensive, not a real distributed-systems requirement.
All `gate.run` callers live in one Python process.

Why `asyncio.Lock` works:

- All gate calls land on the main loop (proof: §2, §3, §4).
- `asyncio.Lock` serialises on the main loop's awaitable queue.
- No thread-level contention exists, so `threading.Lock` would be wrong:
  you cannot acquire a `threading.Lock` from inside a coroutine on the main
  loop without defeating the scheduler; conversely, you cannot acquire an
  `asyncio.Lock` from a non-loop thread.

Why the DB lease was overkill:

- Heartbeat (`execution_gate.py:546-615`) — only defeats
  `recover_stale_leases` on another node. No other node exists.
- `recover_stale_leases` (`execution_gate.py:657-686`) — only recovers
  leases held by crashed processes. No other process exists.
- `INSERT ... ON CONFLICT` (`execution_gate.py:41-46`) — only serialises
  across DB sessions from multiple processes. Only one process.

What `asyncio.Lock` gives up (and why that's fine):

- Cross-process safety: not needed (single-process daemon).
- Crash recovery of a held lock: process death releases everything; the in-memory dict dies with it. Next startup has nothing to clean up.
- Mid-execution lease-loss detection: not needed. The only way `work_fn` is interrupted mid-stream is `cancel_instance_execution` (terminate / pause), which already works via `asyncio.current_task()` cancellation — that mechanism is independent of the gate.

## Alternatives considered (and rejected)

**Option B: keep DB lease, drop heartbeat.** Still requires a row write per acquire/release, a startup `recover_stale_leases`, and SQLAlchemy round-trips on the hot path. Adds latency and surface for a threat that doesn't apply.

**Option C: `threading.Lock`.** Wrong primitive. WorkerPool threads never hold the lock — they only block on a `Future`. A `threading.Lock` inside `gate.run` would require acquiring it from the main loop (legal for a thread lock but blocks the loop, defeating the scheduler; and if the worker thread is itself awaiting a main-loop coroutine, the classic "thread waits for loop waits for thread" deadlock becomes possible). The right primitive for "serialize coroutines on one loop" is `asyncio.Lock`.

**Option D: `asyncio.Lock` per `holder_id`.** Rejected. The gate guards "one `graph.astream` per instance". Two holders for the same instance (e.g. one MESSAGE_JOB and one TASK) must still serialise. Per-instance key, not per-holder.

## Conclusion

`asyncio.Lock` is the correct primitive because the threading model funnels every `gate.run` call onto a single event loop. The DB-backed lease, the heartbeat task, `recover_stale_leases`, the `LeaseContention` / `LeaseLostError` / `LeaseHolderKind` / `LeaseContentionReason` classes, and the `instance_execution_leases` table are all removed. Cancellation semantics already work via `asyncio.current_task()` and need no changes.