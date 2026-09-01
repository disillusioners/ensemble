# V-1 Verification: ExecutionGate release on pause-cancel + resume-path gate coverage

- **Date:** 2026-08-31
- **Branch:** `feature/slash-commands` @ `5e16f791`
- **Verifier:** Worker (read-only code verification; no code modified)
- **Plan ref:** `phase1-plan.md` WS-6 (V-1 row), Risks R-1 / R-13
- **Method:** full-file reads of `execution_gate.py`, `instance_lifecycle.py` (pause/resume cascades), `task_processor.py`, `message_processing_pipeline.py`, plus repo-wide grep census of every `gate.run`, `_graph_tasks[...]`, `create_task`, `astream`/`ainvoke` site.
- **Line-drift note:** confirmed — ExecutionGate run site is `daemon/services/execution_gate.py:118` (plan's `:108` is stale).

---

## 1. Architecture in one paragraph

`ExecutionGateService` is a per-instance `asyncio.Lock` wrapper. Its entire serializing core is three lines — `daemon/services/execution_gate.py:142-144`:

```python
lock = self._lock_for(instance_id)   # :142
async with lock:                     # :143
    return await work_fn()           # :144
```

`async def run(...)` is declared at `execution_gate.py:118`. There is **exactly one** live `graph.astream` invocation in the daemon: `daemon/services/instance_messaging.py:3781`, inside `_process_message_with_tracking`. There are **exactly two** `gate.run` acquisition sites, and both wrap a `work_fn` that awaits `manager._process_message_with_tracking` **in the caller's own task** (no task-layering between lock holder and graph driver — this is the fact the whole release analysis rests on).

The `graph.ainvoke` bypass was deleted (`daemon/manager.py:6336-6342`: legacy `send_message` ainvoke path "were DELETED … every surviving path must cross the T6 choke point"). The only remaining `ainvoke` match in `daemon/` is `llm_failover.py:608`, which is an LLM-client callable (and itself a removed-method docstring), **not** a graph invocation.

---

## 2. Q1 — Is the gate ALWAYS released when `pause_instance_cascade` cancels the graph task?

**Answer: YES. The release mechanism is structural, not best-effort.**

### The lock holder and the cancelled task are the SAME task

1. Both gate sites await `work_fn` directly:
   - Pipeline lane: `message_processing_pipeline.py:432-437` (`gate.run(work_fn=_do_process)`), where `_do_process` (:399-411) awaits `manager._process_message_with_tracking`.
   - Resume lane: `manager.py:9396-9404` (`gate.run(work_fn=_do_process)`), where `_do_process` (:9380-9391) awaits `self._process_message_with_tracking` with `is_retry=True` (:9386).
2. Inside `_process_message_with_tracking`, **the same task** registers itself for cancellation: `instance_messaging.py:3771-3777` — `current_task = asyncio.current_task()` → `self._manager._graph_tasks[instance_id] = current_task` — before entering the astream loop at :3781.

So the task stored in `_graph_tasks[instance_id]` is, at that moment, the task executing `gate.run`'s `async with lock:` body. There is no intermediate task that could hold the lock while the cancelled task dies.

### The cancel site

`pause_instance_cascade` (`instance_lifecycle.py:2685`) per node:
- `:2814` — `graph_task = self._manager._graph_tasks.pop(node_id, None)` (synchronous pop, prevents stale re-cancel)
- `:2826-2827` — `if graph_task and not graph_task.done(): graph_task.cancel()`

(The `terminate_instance` path cancels the same way at `instance_lifecycle.py:1997` + `:2012`.)

### The release path (CancelledError propagation)

1. `task.cancel()` delivers `CancelledError` at the task's current await — typically the astream loop at `instance_messaging.py:3781`.
2. `_process_message_with_tracking` catches it and **re-raises**: `instance_messaging.py:3956-3961` (`except asyncio.CancelledError: … raise`) — explicit comment: "Re-raise so caller … can distinguish pause-cancel from normal completion".
3. The exception propagates out of `work_fn` into `gate.run`'s `await work_fn()` (`execution_gate.py:144`). The `async with lock:` context manager's `__aexit__` runs `asyncio.Lock.release()` — a **synchronous, unconditional** call that cannot be interrupted even by a second `cancel()`. Release happens on *every* exit: success (`:135`), exception (`:135-136`), cancellation (`:137-140`, documented in the docstring and asserted by test).
4. Resume lane: the CancelledError then reaches `gate.run`'s caller, where `manager.py:9405-9406` captures it (`except BaseException as e: gate_raised = e`) and `:9416` re-raises it inside the unified error-handling `try` (job FAILED / instance ERROR paths). The outer `try/finally` (:9372-9375) pops `_graph_tasks` leftovers at :9666-9680.
5. Pipeline lane: `_process_message_with_tracking`'s `finally` (:3971-4058) runs identity-checked deregistration `:4060-4067` — it only pops if `existing is current_task`, so the pause-cascade's earlier synchronous pop (:2814) is not double-popped.

### Edge cases examined — no leak vector found

| Vector | Outcome |
|---|---|
| Cancel while a *second* caller is blocked in `lock.acquire()` | `acquire()` cancellation removes the waiter cleanly (modern asyncio semantics; project on Python 3.13). Lock never acquired → nothing to release. The waiter is not in `_graph_tasks` (registration happens only inside the gated body), so pause never targets it. |
| Cancel between `acquire()` returning and `work_fn()` starting | Unwind releases via `__aexit__`. Window is one loop iteration. |
| `except BaseException: pass` swallow in a drain block (known historical bug class, fixed 2026-07-12) | Even a swallow would not leak: work_fn would *return* and `async with` would release. The current code re-raises (:3961), so the task dies correctly. |
| Unshielded awaits in the `finally` (:3971-4058: deferred question-pause `asyncio.shield` at :4027-4032, watchover drain :4052, system-execution drain :4058) | These run **inside** `work_fn`, i.e. before the lock releases. They delay release by their (bounded, DB-commit-scale) duration but cannot prevent it. The question-pause cascade is shielded against double-cancel. |
| `asyncio.to_thread` workers never receiving cancellation (plan O16 note) | Not applicable to the gate path: `gate.run`/`work_fn`/`astream` all run as coroutines on the main loop; `to_thread` is used only for DB access around them. |
| Holder task GC'd without cancellation | Impossible: the running loop holds a strong reference to scheduled tasks; only `cancel()` can stop them, which triggers `__aexit__`. |

### Timing nuance relevant to /compact (not a leak, but record it)

`wait_for_instance_quiescent` (`manager.py:3392-3408`) decides quiescence from `_graph_tasks.get(instance_id)` — but pause **pops that entry synchronously** (`instance_lifecycle.py:2814`) *before* the cancelled task has unwound. In the WS-6 RUNNING sequence (pause → quiescence probe → take gate), the probe can return `True` within the same loop iteration as the pause, while the dying task still holds the gate for a few more ticks. This is **safe by construction**: the executor's next step is `gate.run`, which *blocks* on `lock.acquire()` until the dying task releases — serialization, not deadlock (see §5, R-1 residual test).

---

## 3. Q2 — Resume-path gate coverage (entry-point census)

`resume_instance_cascade` (`instance_lifecycle.py:2971`) is **DB-only**: it filters PAUSED nodes (:3040-3045), batch-flips PAUSED→RUNNING + Tasks PAUSED→PENDING (:3057-3065), emits SSE (:3084-3096), compacts fired watchers (:3115-3135), and wakes the WorkerPool (`worker_pool.notify_work()`, :3137-3145). It never touches the graph. The actual resumed graph turn is driven by one of the paths below — every one crosses a gate site.

| # | Entry point | Graph-drive route | Gate acquired? | Evidence |
|---|---|---|---|---|
| 1 | Normal dispatch — API chat, agent-to-agent, sources, child-report wake | enqueue → Task claim → `ProcessMessageProcessor.process` → `pipeline.execute` → `gate.run` | ✅ | `task_processor.py:126-131` (pipeline wired with `execution_gate=instance_manager.execution_gate`), `:387` (`pipeline.execute`), `message_processing_pipeline.py:337`, `:432-437` |
| 2 | JobQueue `job_type='message'` lane | Same `ProcessMessageProcessor`/pipeline stack | ✅ | `job_queue_service.py:59` ("Picks the FIRST … task_processor"), pipeline shared per :270-281 of `message_processing_pipeline.py` |
| 3 | WorkerPool retry (`retry_count > 0`) | `is_retry = task.retry_count > 0 or resume_mode` → `resume_mode=is_retry` → pipeline → `gate.run` | ✅ | `task_processor.py:354-370` → `:387` → pipeline gate |
| 4 | Auto-resume on POST /messages to PAUSED instance (`daemon/routers/messages.py:252-378`) | `resume_instance_cascade` (DB-only, :257) → `manager.resume_processing_job` (:279-284) → gate; fallback `enqueue_message(source="api_resume_fallback")` (:312-317) → lane #1 gate | ✅ (both branches) | `manager.py:8604` / `:8644` → `_schedule_explicit_handle_resume` |
| 5 | `/resume` endpoint (explicit) | `resume_processing_job` → same as #4 | ✅ | `routers/instances.py:1008`, `:1277`; `manager.py:8493` |
| 6 | `resume_processing_job` route `answer_gate_existing_turn` | `_schedule_explicit_handle_resume` → `asyncio.create_task(_resume_processing_background)` → `gate.run` | ✅ | `manager.py:8599-8613`, `:8824`, task create `:9287-9298`, gate `:9396-9404`, `is_retry=True` `:9386` |
| 7 | `resume_processing_job` route `report_or_external_resume` | Same as #6 | ✅ | `manager.py:8639-8653` → `:9287-9298` → `:9396` |
| 8 | `resume_processing_job` route `internal_child_noop` | No graph run (parent owns the work) | n/a | `manager.py:8525-8528` (route contract) |
| 9 | `resume_processing_job` route `invalid_or_missing_handle` | Returns `None`, no graph run; router fallback enqueues → lane #1 | n/a / ✅ via fallback | `manager.py:8529-8534`; `messages.py:288-317` |
| 10 | Watchover resume — `_resume_with_graph_restart` | `resume_instance_cascade` (DB) + `resume_processing_job` (gate) + `enqueue_message(source="cascade_resume")` fallback (gate) | ✅ | `watchover_service.py:556`, `:613`, `:643`, `:671-676` |
| 11 | Revive-on-send to terminal instance (COMPLETED/TERMINATED/ERROR/FAILED) | Enqueue transaction only flips status RUNNING (`instance_messaging.py:1641-1673`); the graph turn happens later via Task claim → lane #1 | ✅ | `:1650-1673` ("reviving a terminated instance is the same machinery"), gate at pipeline `:432` |
| 12 | Job retry engine | `atomic_retry` re-queues JobItem; downstream `start_job` mints a fresh Task → WorkerPool claim → lane #1 | ✅ (indirect) | `job_retry_engine.py:352-360` |
| 13 | Legacy `graph.ainvoke` bypass | **Deleted** — no surviving call | n/a | `manager.py:6336-6342` |
| 14 | Watchover activation pause (`pause_instance_cascade` at `watchover_service.py:1004`) | Pause lane, not a resume; release mechanics per §2 | n/a (release ✅) | plan WS-6 row; §2 above |

**Completeness checks:** repo-wide grep found (a) only two `gate.run` call sites (`manager.py:9396`, `message_processing_pipeline.py:432`); (b) only three callers of `_process_message_with_tracking` — `manager.py:6536` is the manager facade delegating to the same messaging-service implementation (`:6536` is *inside* the manager's own `_process_message_with_tracking` def, itself reached only from the two gated work_fns), `manager.py:9381` and `pipeline:400` are the gated work_fns; (c) `_graph_tasks[...]` assignments only at `manager.py:9298` and `instance_messaging.py:3776`; (d) no `create_task` in `daemon/` wraps any graph-driving coroutine other than `manager.py:9287-9298` (the others are SSE snapshots, websocket proxies, MCP pools, source adapters, watchdogs — none run the graph).

---

## 4. VERDICT

> **No bypass found.**

Both questions resolve cleanly:

1. **Gate release on pause-cancel: guaranteed.** The gate-holding task *is* the registered graph task; `pause_instance_cascade` cancels exactly that task (`instance_lifecycle.py:2814`, `:2826-2827`); `CancelledError` re-raises at `instance_messaging.py:3956-3961` and unwinds through `gate.run`'s `async with lock:` whose `__aexit__` releases the `asyncio.Lock` synchronously and unconditionally (`execution_gate.py:143-144`). No cancellation path can leak a held lock; blocked waiters cancel out of `acquire()` cleanly.
2. **Resume-path gate coverage: complete.** All 12 graph-driving entry points funnel through exactly one graph implementation (`instance_messaging.py:3781` astream) behind exactly two gated acquisition sites; the remaining routes either perform no graph run (DB-only resume, `internal_child_noop`, `invalid_or_missing_handle`) or were deleted (ainvoke bypass). R-13's feared race — a resume starting a graph run outside the gate while /compact writes checkpoints — cannot occur with the current call graph.

**No fix is required; WS-6 exit is not blocked by V-1.**

---

## 5. Residual risks worth a test even with the clean verdict

1. **Quiescence-vs-gate-release skew (recommended integration test).** `wait_for_instance_quiescent` judges quiescence from `_graph_tasks` (`manager.py:3392-3398`), which pause pops synchronously (`instance_lifecycle.py:2814`) — *before* the cancelled task unwinds and releases the lock. If the /compact executor probes quiescence and immediately calls `gate.run`, it will simply block for the remaining unwind ticks (safe). But if the executor ever wraps gate acquisition in a short `asyncio.wait_for`, it could spuriously time out in this window. **Test:** pause a mid-astream instance → assert `gate.is_held()` is momentarily still True after quiescence probe returns True → assert a subsequent `gate.run` acquires within a bounded timeout and compaction proceeds.
2. **End-to-end pause-cancel-release regression at the *service* level.** The unit test `tests/unit/services/test_execution_gate.py:125-144` (`test_run_releases_lock_on_cancellation`) proves the lock primitive; no test proves the *wiring* — that the task registered in `_graph_tasks` during a real `_process_message_with_tracking` run is the same task holding the gate. A regression that moved `gate.run` into a separate wrapper task would silently break the invariant §2 rests on. **Test:** fake graph that sleeps in astream → run through pipeline → call `pause_instance_cascade` → assert `gate.is_held() is False` and a follow-up turn proceeds.
3. **Dying-task finally drains run *before* gate release.** The deferred question-pause (`instance_messaging.py:4025-4043`), watchover drain (:4052), and system-execution drain (:4058) execute inside `work_fn`, so a /compact executor waiting on the gate implicitly waits for them. This is correct (no checkpoint writes can race), but it means gate-acquire latency after pause is not constant. Worth one assertion in test #1 that these drains completed before compaction starts.
4. **`resume_instance_cascade` + `notify_work` vs. a still-held gate.** Resume wakes the WorkerPool (:3137-3145) immediately after the DB flip; the worker's claim → `gate.run` will block until any straggler releases. Benign (bounded), but if a future change makes worker dispatch fail closed on gate contention, resume could stall — keep the blocking-then-proceed semantics pinned by a test if the dispatch path is ever touched.

---

## 6. Baseline test run

```
uv run pytest tests/unit/services/test_execution_gate.py -q
→ 26 passed, 17 warnings in 2.05s
```
(No test files modified. Warnings are pre-existing SQLAlchemy sqlite datetime deprecations, unrelated.)
