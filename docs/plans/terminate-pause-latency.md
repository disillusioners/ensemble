# Plan: Reduce Pause / Terminate Latency (Cascade + Graph Unwind + Job State)

| Field | Value |
|---|---|
| **Status** | REVISION 2 — addresses review at `.agents/reviewer/RESULTS/2026-06-05-terminate-pause-latency-plan-review.md` |
| **Scope** | `daemon/services/instance_lifecycle.py`, `daemon/services/dispatch_event_bus.py` |
| **Estimated effort** | ~0.5 day implementation + ~0.5 day test/review |
| **Defers to follow-up** | Fix 1 (synchronous MESSAGE job cancel) — see §6 Investigation Plan; pause-path graph-task unwind — see §4.1 |

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
23:06:49  Job transition: 0cc15623... | processing -> cancelled (abort)   ← 27 s after DELETE
```

User-visible: deleting an instance with a child and an in-flight MESSAGE job takes **~30 s** to fully settle, and the cascade log to the child appears to lag the DELETE by several seconds.

This plan compresses the graph-task unwind latency and adds diagnostic logging. The MESSAGE-job state transition is **deferred to an investigation** (see §6) because review revealed that step 7.6 should already be handling it, and the root cause of the 27 s delay is not yet known.

---

## 2. Root Cause Analysis

| # | Symptom | Root cause | Status |
|---|---|---|---|
| **RC1** | MESSAGE job stays in `processing` 27 s | Step 7.6 *should* transition it synchronously; the 27 s delay strongly suggests 7.6 didn't run, threw, or didn't see the job. **Unverified.** | **INVESTIGATE** (§6) |
| **RC2** | JobProcessor is 30 s polling, not event-driven for terminations | `daemon/services/job_processor.py:146-149` waits on global event with 30 s timeout. No wakeup path from `terminate_instance`. | **FIX in §4.3** (reuses `notify_all()`; **attribute path is `self._manager._job_queue_mgmt_service._dispatch_bus`** — see review W-NEW-1) |
| **RC3** | Graph task `cancel()` is fire-and-forget; DELETE returns 200 while LLM stream still in flight | `daemon/services/instance_lifecycle.py:438-442` calls `task.cancel()` without awaiting the unwind | **FIX A** (this PR) |
| **RC4** | 4 s DELETE → "Cascading terminate to child" gap | Suspected: a different code path emitted that log (`manager.py:1059` or `routers/projects.py:818`); the DELETE handler's own `meta.children` was empty. **Hypothesis only.** | **DIAGNOSE** (Fix B's `trigger=...` tag) |

---

## 3. Goals & Non-Goals

### Goals
- **G1.** DELETE response time is bounded by graph-task unwind (≤ 5 s) plus cascade (parallel, ≤ 5 s), not by handler teardown race.
- **G2.** Total settle time (job-row terminal state visible to clients) ≤ 5 s in the common case after Fix 1's investigation lands.
- **G3.** Preserve pause/terminate semantics: pause is resumable, terminate is final, cascade is recursive, token-based cancellation still works.
- **G4.** Add diagnostic logging so the next occurrence of RC4/RC1 is self-explanatory.

### Non-Goals
- **NG1.** Touching the pause path's graph-task unwind (see §4.1 — `_pause_single` is sync, and applying Fix A there introduces 5 s × N regression for tree pauses).
- **NG2.** Re-architecting JobProcessor's poll model for new-job dispatch.
- **NG3.** Changing LLM client per-call timeouts.
- **NG4.** Touching TTL eviction (`manager.py:1059`) or project cleanup (`routers/projects.py:818`) beyond what consistency with Fix A requires.
- **NG5.** Removing the 30 s JobProcessor poll (it remains a safety net; Fix B's `notify_all` shortens the typical wait).

---

## 4. Proposed Changes

### 4.1 Fix A — Bounded-await graph task + parallelize cascade (terminate only)

**Why.** Closes RC3 for the terminate path. Makes DELETE latency deterministic. Parallelization keeps the cascade from compounding per-child unwind time.

**Scope rationale.** `_pause_single` at `daemon/services/instance_lifecycle.py:603` is a **sync** function (`def _pause_single(target_id, prefetched_meta=None) -> bool:`), so the proposed `await asyncio.wait_for(...)` cannot be inserted there without cascading changes (caller at `:662-675` is async, so making `_pause_single` async is straightforward but introduces 5 s × N regression for trees with N children — worse than the current behavior). The terminate path is where the user-visible DELETE latency matters; pause latency is dominated by the existing sequential tree walk and is acceptable as-is. Pause-path graph-task unwind is **deferred to a follow-up PR** that will (a) make `_pause_single` async, (b) bound the per-child wait to ~500 ms, and (c) consider parallelization. See §7 Q3.

**File:** `daemon/services/instance_lifecycle.py`
**Lines:** 419-442 (cascade block + graph-task cancel in `terminate_instance`)

Replace the cascade and graph-task-cancel block with:

```python
# Get instance metadata BEFORE modifying state (needed for children cascade)
meta = None
if hasattr(self._manager, '_instance_repository') and self._manager._instance_repository:
    meta = self._manager._instance_repository.get(instance_id)

# Re-entrancy guard: if already terminated, return early
if meta and meta.status == InstanceStatus.TERMINATED.value:
    logger.info(f"Instance {instance_id[:8]}... already terminated, skipping")
    return True

# Cascade to children FIRST - terminate all child instances in parallel
# (Parallel because each child may itself unwind an in-flight LLM call;
# serial cascade would compound to 5s*N worst case.)
child_ids: list[str] = list(meta.children) if meta and meta.children else []
if child_ids:
    await asyncio.gather(
        *(self.terminate_instance(cid) for cid in child_ids),
        return_exceptions=True,
    )
    for cid in child_ids:
        logger.info(
            f"Cascading terminate to child instance: {cid[:8]}... "
            f"(trigger=DELETE, parent={instance_id[:8]}...)"
        )

# 1. Cancel active requests for this instance
self._manager._request_registry.cancel_by_instance(instance_id)

# 1.5. Cancel any running graph task for this instance, bounded-await unwind
graph_task = self._manager._graph_tasks.pop(instance_id, None)
if graph_task and not graph_task.done():
    graph_task.cancel()
    try:
        # Bounded wait: graph task unwinds when its in-flight LLM call
        # returns or hits the LLM client's socket timeout. We cap so a
        # stuck LLM call doesn't make DELETE hang; the LLM client's
        # timeout is the real backstop.
        await asyncio.wait_for(asyncio.shield(graph_task), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning(
            f"Graph task {instance_id[:8]}... did not unwind within 5s; "
            f"relying on LLM socket timeout to free resources"
        )
    except asyncio.CancelledError:
        logger.debug(f"Graph task {instance_id[:8]}... cancelled during await")
    logger.info(f"Cancelled graph task for instance {instance_id[:8]}...")
```

**Why `asyncio.shield`.** Protects the inner `await` from being cancelled if the outer coroutine (request handler) is itself cancelled (e.g., client disconnect). Without shield, a client disconnect during the 5 s wait would leak the unwinding graph task — the very problem Fix A is closing.

**Why 5 s.** Long enough to flush a normal SSE write and let the LLM client's typical 10-30 s timeout take over (we want to be gone before that). Short enough that DELETE feels responsive. If real-world data suggests a different value, revisit (see §7 Q4).

**Scope notes.**
- `daemon/manager.py:1059-1062` (TTL eviction): runs in a background task, not a request handler. Blocking it for 5 s is acceptable but not necessary for user-visible latency. **Out of scope.**
- `daemon/routers/projects.py:818` (project cleanup): runs from a project-delete request, latency matters. **In scope** for the same bounded-await pattern, in a separate commit (or bundled if small). *Team decision — see §7 Q3.*

**Test plan.**
- Unit: mock graph task that sleeps 2 s → `terminate_instance` returns in ~2 s with `cancelled` status.
- Unit: mock graph task that sleeps 10 s → `terminate_instance` returns in ~5 s with a warning log; task continues unwinding in the background.
- Unit: parent with 3 children each sleeping 2 s → `terminate_instance` returns in ~2 s (parallel), not ~6 s (serial).
- Integration: existing `tests/test_instance_cascade.py` should still pass (cascade semantics unchanged).

---

### 4.2 Fix B — Diagnostic logging: cascade trigger + terminate summary

**Why.** Closes RC4 (diagnose, not fix). Future occurrences of the 4 s cascade gap will be self-explanatory once we know which code path emitted the log.

**File:** `daemon/services/instance_lifecycle.py`
**Lines:** 419-433 (cascade log), end of `terminate_instance` (before `return True`)

Two logging changes:

**B.1.** Tag the cascade log with a trigger (covered above in Fix A's snippet — `trigger=DELETE`). For symmetry, when this code path is reused from pause or cleanup, the same log shape applies with a different `trigger` value.

**B.2.** Add a single summary log at the end of `terminate_instance`:

```python
duration_ms = int((time.monotonic() - t0) * 1000)
jobs_cancelled = <count from step 7.5 + 7.6>
logger.info(
    f"[TRACE] terminate_instance: {instance_id[:8]}... complete "
    f"(graph_unwind_ms={graph_unwind_ms}, jobs_cancelled={jobs_cancelled}, "
    f"children={len(child_ids)}, duration_ms={duration_ms})"
)
```

**Logging style note.** The daemon uses `[TRACE]` prefix liberally (e.g., `daemon/services/job_processor.py:140, 192`; `daemon/services/instance_lifecycle.py:555`). The plan uses the same prefix for consistency.

**Test plan.**
- Unit: assert the summary log is emitted with all four fields populated.
- Integration: a regression test that asserts cascade log line contains `trigger=DELETE` for DELETE-originated cascades, `trigger=PAUSE` for pause-originated cascades.

---

### 4.3 Wakeup — reuse `DispatchEventBus.notify_all()` (no new method)

**Why.** Closes RC2 with zero new code. `notify_all()` at `daemon/services/dispatch_event_bus.py:108-125` already sets the global event and all per-project events, which is exactly what the previous plan's `notify_terminated()` proposed — and the previous plan's `instance_id` parameter was unused (cosmetic only).

**File:** `daemon/services/instance_lifecycle.py`
**Lines:** end of `terminate_instance`, *after* the DB status update at `:473` and the job-row transitions in step 7.5/7.6

```python
# 9. Wake the JobProcessor so it can sweep TERMINATED-instance artifacts
# immediately rather than waiting up to 30s for the next poll boundary.
# Safe to call even if the DB writes haven't fully settled — early wakeup
# is benign (JobProcessor's orphan-check at job_processor.py:304-305 will
# just see RUNNING and skip, then catch TERMINATED on its next pass).
# Attribute path: manager → _job_queue_mgmt_service → _dispatch_bus.
# Set at daemon/api.py:210 (direct assignment, not via setter).
# NOT self._manager._dispatch_bus — InstanceManager has no such attribute.
mgmt = getattr(self._manager, '_job_queue_mgmt_service', None)
bus = getattr(mgmt, '_dispatch_bus', None) if mgmt is not None else None
if bus is not None:
    bus.notify_all()
```

**Why this attribute path.** `daemon/api.py:206-210` creates one `DispatchEventBus` instance and stores it on `job_queue_mgmt_service._dispatch_bus` (direct assignment). The same bus is also passed to `JobProcessor` via constructor at `daemon/api.py:326` — so calling `notify_all()` on the mgmt-service's reference wakes the JobProcessor's `_process_loop`. `InstanceManager` has no `_dispatch_bus` attribute; it has `self._job_queue_mgmt_service` (declared at `daemon/manager.py:591`, set at `daemon/api.py:256`). A naive `hasattr(self._manager, '_dispatch_bus')` guard would silently never fire, defeating the fix.

**Why this is sufficient.** JobProcessor's `_process_loop` waits on `self._dispatch_bus.wait_for_job(None, timeout=30.0)` (`daemon/services/job_processor.py:146-149`). `notify_all()` sets `self._global_event`, which is what `wait_for_job(None, ...)` waits on. Wakeup happens on the next event-loop tick — sub-millisecond latency.

**Alternative paths (do not use here).**
- `self._manager._job_queue_service._dispatch_bus` (set via `set_dispatch_bus` at `daemon/api.py:225`) — also points to the same bus, but goes through a setter and is a longer chain. Use only if `_job_queue_mgmt_service` is unavailable.
- New `manager.get_dispatch_bus()` accessor — out of scope; would be a separate refactor.

**Why not a new method.** A new `notify_terminated(instance_id)` would set the same events with the same body; the only added value is the parameter name in a log line, which the summary log in Fix B.2 already covers.

**Test plan.**
- Unit: mock the dispatch bus on `_manager._job_queue_mgmt_service._dispatch_bus` and assert `notify_all()` is called once per `terminate_instance` call. **Important:** the test must mock at the *correct attribute path* (`_job_queue_mgmt_service._dispatch_bus`), otherwise the test passes while the production code is silently no-op.
- Integration: start a JobProcessor in a test, force it into `wait_for_job` (via a short poll interval), call `terminate_instance`, assert `_process_next_job` is invoked within 100 ms.

---

## 5. Rollout Ordering

Two commits, ordered by risk.

| # | Commit | Files touched | Risk | Test surface |
|---|---|---|---|---|
| 1 | Fix B: diagnostic logging (cascade trigger + summary) | `instance_lifecycle.py` | **Trivial** — read-only additions | Unit (log assertions) |
| 2 | Fix A + Wakeup: bounded-await + parallel cascade + `notify_all` | `instance_lifecycle.py` | **Medium** — changes DELETE latency, touches cascade ordering | Unit (timing) + integration |

**No feature flag.** Both changes are small and have unit-test coverage; a flag would add complexity without proportional safety. If Fix A's behavior is unexpected in production, revert the commit.

---

## 6. Investigation Plan — RC1 (MESSAGE job 27 s delay)

**This is a prerequisite for any future "Fix 1" implementation.** Without understanding why step 7.6 doesn't catch the MESSAGE job, adding a fallback in 7.5 risks papering over a real bug.

### 6.1 Reproduce locally

Add `[TRACE]` logging to steps 7.5 and 7.6 in `daemon/services/instance_lifecycle.py:505-549`:

```python
# 7.5 — at the top of the MESSAGE-jobs loop
logger.info(
    f"[TRACE] terminate_instance: 7.5 processing MESSAGE job "
    f"{msg_job.job_id[:8]}... (status={msg_job.status}, "
    f"instance_id_match={msg_job.instance_id == instance_id})"
)
# ... and after cancel_message_job:
logger.info(f"[TRACE] terminate_instance: 7.5 done for {msg_job.job_id[:8]}...; row state unchanged (token-only)")

# 7.6 — at the top of the loop and around the complete_job call
logger.info(
    f"[TRACE] terminate_instance: 7.6 processing job "
    f"{remaining_job.job_id[:8]}... (type={remaining_job.job_type}, "
    f"status={remaining_job.status})"
)
try:
    if remaining_job.status == "processing":
        await self._job_queue_service.complete_job(
            remaining_job.job_id,
            demand_state=DemandState.CANCELLED,
            error="Instance terminated during cleanup",
        )
        logger.info(f"[TRACE] terminate_instance: 7.6 complete_job({remaining_job.job_id[:8]}...) succeeded")
    else:
        await self._job_queue_service.cancel_job(remaining_job.job_id)
        logger.info(f"[TRACE] terminate_instance: 7.6 cancel_job({remaining_job.job_id[:8]}...) returned")
except Exception as e:
    logger.warning(
        f"[TRACE] terminate_instance: 7.6 raised for {remaining_job.job_id[:8]}...: "
        f"{type(e).__name__}: {e}"
    )
```

### 6.2 Capture the JobProcessor side

In `daemon/services/job_processor.py:279-290`, add a `[TRACE]` line just before the `complete_job` call:

```python
logger.info(
    f"[TRACE] job_processor: orphan-check sees MESSAGE job {proc_job.job_id[:8]}... "
    f"with instance {proc_job.instance_id[:8]}... status={instance_meta.status} — "
    f"firing complete_job(CANCELLED)"
)
```

### 6.3 Run the original repro

Re-create the original conditions (spawn leader → spawn child → enqueue MESSAGE job → pause → DELETE), capture both sides' logs, and identify:

| Possible cause | Diagnostic | Resolution |
|---|---|---|
| 7.6 reached and `complete_job` succeeded | Both `[TRACE]` lines fire, job is `cancelled` before 7.6 returns, JobProcessor never sees it | RC1 doesn't exist. The 27 s was actually 7.5's token signal, not a delay. Close investigation. |
| 7.6 reached and `complete_job` raised | 7.6's `try/except` log fires with the exception text | Fix the underlying bug (likely a state-machine violation; check `job_state_machine.py:20-32`) |
| 7.6 reached but MESSAGE job not in `find_jobs_by_instance` results | 7.5 fires for the MESSAGE job, 7.6 does not | Investigate `find_jobs_by_instance(job_type=None)` query — may be filtering by `job_type` despite the `None` argument |
| 7.6 never reached | No 7.6 log line; JobProcessor's orphan-check fires 27 s later | Likely an exception between 7.5 and 7.6 swallowed at `:502-503` or `:513-514` — narrow down with try/except in 7.5 |

### 6.4 Outcome

Based on what the investigation finds, the next revision of this plan will propose one of:

- **Outcome A** — 7.6 works. No Fix 1 needed. JobProcessor's orphan check is the safety net, not a hot path. Update plan to reflect.
- **Outcome B** — 7.6 has a bug. Fix 7.6 (e.g., `find_jobs_by_instance` is dropping MESSAGE rows, or `complete_job` is raising due to a state-machine edge case).
- **Outcome C** — 7.6 doesn't reach MESSAGE jobs. Add the `complete_job(CANCELLED)` call in 7.5's processing branch (the original Fix 1 from revision 1 of this plan, justified by the new evidence).

Q2 from revision 1 is **resolved** — `complete_job` is idempotent via the `except (ValueError, InvalidTransitionError)` swallow at `daemon/services/job_queue_service.py:1181-1183` (and `:1265-1267` for the sync variant). `terminate_job` raises `InvalidTransitionError` from `atomic_transition` at `daemon/repositories/job_queue/repository.py:434-439` when `job.status != from_status`, and the state machine at `daemon/services/job_state_machine.py:20-32` has no transitions out of terminal states. The handler's later `complete_job` write (if it wins the race) will swallow cleanly. Lock release in the `finally` block at `:1184-1194` is also safe (idempotent via `release_by_job` returning False on no-op). No guard or feature flag is required for any future Fix 1.

---

## 7. Open Questions for the Team

- **Q1.** (investigation) Why doesn't 7.6 catch MESSAGE jobs? — see §6. **Blocker for Fix 1.**
- **Q2.** ~~Is `complete_job` idempotent?~~ **RESOLVED.** Yes, via exception swallow at `daemon/services/job_queue_service.py:1181-1183`. No code change required.
- **Q3.** Apply the bounded-await pattern to `daemon/routers/projects.py:818` (project cleanup) in the same PR, or split? Recommend **split** — keep this PR small.
- **Q4.** Make the 5 s timeout configurable via `config.yaml`? Recommend **no** for v1; revisit if real-world data shows a different value is needed.
- **Q5.** Pause-path graph-task unwind (the follow-up mentioned in §4.1): what's the right design? Options are (a) make `_pause_single` async and bound each wait to ~500 ms, (b) only await the root's graph task (children typically don't have in-flight LLM calls during pause cascade), or (c) parallelize the tree. This needs its own PR with a latency-budget analysis.
- **Q6.** Should we add a regression integration test that times `POST /pause` → `DELETE` → `GET /jobs/{id}` and asserts the job is in `cancelled` within, say, 6 s? Recommend **yes**, in the test surface for the Fix 1 follow-up PR.
- **Q7.** The `meta.children` field is populated by `_enrich_instance` from `InstanceHierarchy` on every `get()` (`daemon/repositories/instance/repository.py:59-65`). The `children` column on the `Instance` model (`models.py:63`) is denormalized but **never read on the read path**. Should we remove the column? (Out of scope here, but flag for follow-up.)
- **Q8.** `resume_processing_job` at `daemon/manager.py:2022-2109` queries `find_processing_message_jobs_by_instance` which only returns PROCESSING rows. If pause+resume+terminate happen quickly, Fix 1's eventual `complete_job(CANCELLED)` may run between resume's `find` and its `enqueue_message`, causing resume to see an empty list and fall into the "child instance" branch at `manager.py:2064+`. This is **benign** but the follow-up plan for Fix 1 should document it.
- **Q9.** Test file references: this plan refers to `tests/integration/test_terminate_cascade.py`, which does **not exist**. The actual cascade test is `tests/test_instance_cascade.py` (different focus: FK cascade in repo layer). New tests for Fix A's bounded-await and parallel-cascade behavior should be added to `tests/integration/` (or a new `tests/services/test_instance_lifecycle_terminate.py`).

---

## 8. Out of Scope (Explicitly)

- Fix 1 (synchronous MESSAGE job cancel) — deferred to a follow-up PR gated on the §6 investigation.
- Pause-path graph-task unwind — deferred to a follow-up PR (Q5).
- Project-cleanup path bounded-await — deferred (Q3).
- TTL eviction bounded-await (`manager.py:1059`) — not user-visible.
- Re-architecting JobProcessor's 30 s poll into a push model for new jobs.
- Changing LLM client per-call timeouts.
- Removing the 30 s `JobProcessor` poll entirely (it remains a safety net).
- Any change to the SSE / live-hub streaming code beyond what Fix A's bounded-await implies.
- Removing the `children` column from the `Instance` model (Q7).

---

## 9. Appendix: Timeline Reconstruction

### Pre-fix (observed)

| Log line | Timestamp | Root cause | Addressed by |
|---|---|---|---|
| `POST /pause 200` | T+0 | — | — |
| `Cancelled graph task for instance 9a230d55...` | T+0 | log at `cancel()` call site | Fix A's bounded await makes this log emit *after* the await, so the timestamp reflects the unwind time |
| `Paused instance 9a230d55...` | T+0 | pause path | — |
| `Paused instance 3bbc43af...` | T+0 | `pause_instance_cascade` tree walk | — |
| `DELETE 200` | T+0 | — | Fix A bounds the response time at ~5 s (LLM unwind) |
| `Cascading terminate to child ...` | T+4 s | emitted by a *second* code path; `meta.children` empty in DELETE handler | Fix B's `trigger=...` tag diagnoses this |
| `JobProcessor: MESSAGE job ... terminated` | T+27 s | `job_processor.py:281-289` on next 30 s poll | **Deferred** to §6 investigation + follow-up Fix 1 PR |
| `Job transition: processing -> cancelled` | T+27 s | `job_processor.py:285-289` write | Same as above |

### Post-fix (expected for the two fixes in this PR; assumes Fix 1 not yet landed)

For a single instance with one in-flight LLM call and one child (similar to observed log):

```
T+0ms       POST /pause
T+0ms       Paused instance 9a230d55...
T+0ms       Paused instance 3bbc43af...      (pause cascades to child)
T+~3s       DELETE arrives
T+~3s       Cancelled graph task 9a230d55...  (after bounded await, 3s unwind)
T+~3s       Cascading terminate to child 3bbc43af... (trigger=DELETE, parallel)
T+~3s       terminate_instance: 9a230d55... complete (summary log)
T+~3s       JobProcessor: MESSAGE job ... terminated  (notify_all wakeup, but 7.5/7.6 may not have fired — see §6)
T+~3s       DELETE 200
T+~3s       [Job may still be in 'processing' if RC1 root cause is in 7.6] — see §6
T+~30s      Job transition: processing -> cancelled  (JobProcessor safety net, or Fix 1)
```

For an N-child tree in the pause path, the pause latency is unchanged (Fix A doesn't apply). For an N-child tree in the terminate path, terminate latency is **max(per-child unwind)** rather than **sum**, because of the parallel cascade — worst case 5 s for any reasonable N.

---

## 10. Review Checklist (for reviewers)

- [ ] Q1 (RC1 root cause) — has the §6 investigation been completed? **Required before any future Fix 1 PR.**
- [ ] Does Fix A's `await` block on a sync context? (No — `terminate_instance` is already async.)
- [ ] Does Fix A's `asyncio.shield` correctly protect against outer-cancel during the 5 s wait?
- [ ] Does Fix A's `asyncio.gather(..., return_exceptions=True)` correctly handle a child whose `terminate_instance` raises? (Yes — `return_exceptions=True` aggregates, doesn't propagate.)
- [ ] Is the pause-path latency budget for N-child trees acceptable as-is? (Yes — current behavior, not regressed.)
- [ ] Are existing tests in `tests/test_instance_cascade.py` (and any related integration tests) still passing?
- [ ] Do new test files go in `tests/integration/` per Q9?
- [ ] Does the `[TRACE]` prefix in Fix B match the daemon's existing style? (Yes — see `daemon/services/job_processor.py:140, 192` and `daemon/services/instance_lifecycle.py:555`.)
- [ ] Is the `notify_all()` reuse in §4.3 appropriate, or should it be a new method? (Per W3 in the review, reuse is preferred.)
- [ ] Is the dispatch-bus attribute path `self._manager._job_queue_mgmt_service._dispatch_bus` correct? (Yes — see §4.3 "Why this attribute path". A unit test that mocks at the wrong path will pass while production is silently no-op.)
