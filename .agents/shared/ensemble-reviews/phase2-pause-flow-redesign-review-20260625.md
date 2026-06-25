# Phase 2 Pause Flow Redesign — Deep Review

**Commit**: `ab8447eb`
**Reviewer**: ensemble-orchestrator + 3 parallel explorers + (council pending)
**Files reviewed**: 7 files, +1119 lines
**Date**: 2026-06-25

---

## Summary

Phase 2 implements atomic pause/resume with three coordinated changes: a 3-table atomic UPDATE in `_pause_cascade_db_sync`, a `B2 worker race` fix in the resume path, and bus-watcher preservation. The core atomicity claim is **correct and verifiable** — all three UPDATEs share one `WriteGuardSession` and one commit, with implicit SQLAlchemy rollback on exception.

**Severity breakdown**:
- 🔴 Critical: **0**
- 🟡 Warning: **4**
- 🟢 Suggestion: **3**

The implementation is sound for the core pause contract. The main concerns are: (1) bus-watcher compaction hook is defined but never wired in, (2) resume-after-pause-before-cascade-commits race leaves orphaned PAUSED tasks (Phase 3 responsibility but Phase 2 creates the condition), (3) terminal-state pre-classification is incomplete, (4) worker `on_success` callback relies solely on the DB-level guard.

---

## 🟡 Warning 1: `_compact_fired_watchers_for_paused` is never called in production

**File**: `daemon/services/instance_lifecycle.py:2034-2122`
**Severity rationale**: Decision 2 (preserve watchers during pause) without Decision 3 (cleanup) recreates the C3 unbounded-growth bug that the redesign explicitly addresses.

### Evidence

Definition exists at lines 2034-2122. The docstring (lines 2067-2073) states:
> "This method is INTENDED to be wired into the resume path (Phase 3) — it is registered here as part of Phase 2 so the surface is stable."

**Production call sites**: ZERO. Only a comment at line 1081 references it.

**Test call sites**: 4 (in `tests/unit/test_pause_flow_redesign.py:525,553,579,590`).

### Failure scenario

During a long pause:
1. Parent instance is PAUSED.
2. Child tasks complete; `DependencyBus` fires their `dependency_watchers` PENDING → FIRED.
3. `PROCESS_REPORT` delivery is blocked by `claim_pending_task` pause gate (`task/repository.py:319-340`).
4. FIRED FollowUp payloads accumulate in `dependency_watchers` with `fired_at` set, `enqueued_at` null.
5. After 60s grace window, the compaction's `fired_at <= cutoff_iso` predicate matches — but compaction is never called.

Row size for `dependency_watchers` is ~150 bytes (estimated). With 100 children completing during a multi-hour pause, that's ~15 KB. Not catastrophic, but unbounded across many pause cycles.

### Suggested fix

Two options:
1. **Defer to Phase 3 explicitly** — add a TODO marker in the docstring with a deadline/owner so it doesn't get forgotten.
2. **Wire into the resume path NOW** — add a single line to `_resume_cascade_db_sync` or `resume_instance_cascade` calling `_compact_fired_watchers_for_paused(root_id)` after the resume UPDATE commits.

Option 2 is safer and matches the documented design intent. The hook is idempotent and never raises (lines 2114-2121 swallow exceptions and log).

---

## 🟡 Warning 2: Resume-after-pause-before-cascade-commits race orphans PAUSED tasks

**Files**:
- `daemon/manager.py:2957-2959` (resume's task read, outside WriteGuardSession)
- `daemon/manager.py:2998-3002` (resume's `complete_task` call)
- `daemon/services/instance_lifecycle.py:1843-2032` (pause cascade)

**Severity rationale**: Real race window, but bounded by WriteGuardSession serialization. Phase 3 owns the PAUSED → PENDING re-arm, but Phase 2 creates the orphaned state.

### Evidence

**Resume path** at `manager.py:2944-3022`:
1. Line 2957-2959: `_paused_task = await asyncio.to_thread(_task_repo.get_by_message, _original_message_id)` — reads task status OUTSIDE WriteGuardSession.
2. Line 2985: If `PAUSED`, skip complete_task. If `RUNNING`, call complete_task.
3. Line 2998-3002: `complete_task` writes via `WHERE status = running` DB guard.

**Pause cascade** at `instance_lifecycle.py:1843-2032`:
1. UPDATE 1 (instances): PAUSED
2. UPDATE 2 (jobs): PROCESSING → PAUSED
3. UPDATE 3 (tasks): RUNNING → PAUSED
4. Single commit at line 2021.

### Race scenario

T0: User calls pause → cascade starts. UPDATE 3 hasn't committed yet.
T1: User immediately calls resume → resume's `_paused_task` read sees task still RUNNING.
T2: Pause cascade UPDATE 3 commits → task is PAUSED in DB.
T3: Resume calls `complete_task` → DB guard `WHERE status = running` rowcount=0, returns None silently.
T4: Resume's `_resume_cascade_db_sync` enters WriteGuardSession → instance PAUSED → RUNNING.
T5: Final state: instance RUNNING, task PAUSED, job PAUSED.

### Why this matters

After T5:
- `claim_pending_task` (`task/repository.py:319-340`) excludes PAUSED instances (line 339: `WHERE status IN (:status_paused, :status_terminated)`). The instance is now RUNNING, so claim proceeds.
- But the task's WHERE clause is `WHERE status = :status_pending` (line 313, 378). PAUSED task doesn't match.
- The task is **orphaned in PAUSED forever** — no one re-claims it.

The comment at `manager.py:2989-2992` acknowledges this:
> "resume will re-claim via PAUSED → PENDING (instance=...)"

…but Phase 2's resume path does NOT actually re-claim. The `manager.py:2944-3022` block only handles the case where the task is RUNNING. PAUSED tasks are deliberately skipped. Phase 3 must implement the re-arm.

### Probability

Low — requires pause and resume to interleave within the pause cascade's commit window. The WriteGuardSession's lock serializes their DB-sync halves, but the resume's read happens BEFORE the WriteGuardSession is entered (line 2957-2959), so the read can race with the pause commit.

### Suggested fix

Three layers, in order of preference:

1. **Move the task read INSIDE WriteGuardSession** in `manager.py:2957`. Wrap the entire read+complete_task block in `WriteGuardSession`. This serializes against pause's UPDATE 3, so the resume always reads a consistent state.

2. **Add a `task.status == PAUSED` re-arm branch** in the resume path. After confirming the instance is resumed, transition any orphaned PAUSED tasks back to PENDING:
   ```sql
   UPDATE task SET status = 'pending'
   WHERE instance_id = :instance_id AND status = 'paused'
   ```
   This is cheap (1 row typically) and bounds the orphan window.

3. **Document the race explicitly** in `manager.py:2944-3022` with a known-issue marker so Phase 3 picks it up.

Option 1 is the most correct. Option 2 is the cheapest mitigation.

---

## 🟡 Warning 3: Worker `on_success` callback has no PAUSED guard at the application level

**File**: `daemon/services/task_processor.py:342-347`

### Evidence

```python
342:         async def on_success(result: ProcessingResult) -> None:
343:             await asyncio.to_thread(
344:                 task_repo.complete_task,
345:                 task_id,
346:                 {"success": True, "message_id": message_id},
347:             )
```

No PAUSED check before calling `complete_task`. Relies entirely on the DB-level guard at `task/repository.py:641` (`WHERE status = :status_running`).

### Failure scenario

T0: Worker successfully completes a task's graph execution.
T1: Worker fires `on_success` callback.
T2: `complete_task` runs — but pause cascade UPDATE 3 just committed, task is now PAUSED.
T3: DB UPDATE rowcount=0, returns None. `_notify_pending_task()` is NOT called (it's after the success path at line 662, gated on the row).
T4: Worker thread exits thinking the task is complete.

### What's actually fine

- DB state is correct (task stays PAUSED).
- Per-instance guard releases correctly because the row didn't change.
- No data corruption.

### What's actually wrong

1. **Silent no-op with no audit trail**. The worker logs nothing about the suppression. If `complete_task` returns None because of a legitimate race (concurrent pause), the operator sees no diagnostic.
2. **The `resume_instance_cascade`'s `manager.py:2944-3022` block** is the only place where this race is acknowledged. If a future developer adds another `complete_task` call site (e.g., a retry path), the same silent suppression can happen with no logging.

### Suggested fix

Add a pre-check in `task_processor.py:344` mirroring the resume path:
```python
async def on_success(result: ProcessingResult) -> None:
    _task = await asyncio.to_thread(task_repo.get, task_id)
    if _task is None:
        logger.warning(f"Task {task_id} disappeared before completion")
        return
    if _task.status == TaskStatus.PAUSED.value:
        logger.info(
            f"Worker on_success: task {task_id} is PAUSED "
            f"(pause cascade won the race); deferring completion to resume"
        )
        return  # Pause cascade owns the write
    await asyncio.to_thread(task_repo.complete_task, task_id, ...)
```

This is defense-in-depth — the DB guard remains the authoritative protection, but the app-layer check produces a clear audit trail.

---

## 🟡 Warning 4: `pause_instance_cascade` does not reject terminal instances in pre-classification

**File**: `daemon/services/instance_lifecycle.py:976-989`

### Evidence

```python
976:         for node_id in tree_ids:
977:             try:
978:                 meta = repo.get(node_id)
979: 
980:                 if meta is None:
981:                     logger.warning(f"Instance {node_id[:8]}... not found in DB, skipping pause")
982:                     skipped_ids.append(node_id)
983:                     continue
984: 
985:                 # Skip if already paused
986:                 if meta.status == InstanceStatus.PAUSED.value:
987:                     logger.info(f"Instance {node_id[:8]}... is already paused, skipping")
988:                     skipped_ids.append(node_id)
989:                     continue
```

The pre-classification only checks for `None` and `PAUSED`. It does NOT check for `COMPLETED`, `ERROR`, or `TERMINATED`.

### Failure scenario

User pauses a root that has a COMPLETED child:
1. Pre-classification accepts the COMPLETED child (passes line 986).
2. Line 992-994: `_request_registry.cancel_by_instance` called — no-op for terminal.
3. Line 999-1003: graph task cancellation — `pop()` returns None (no live graph task for terminal), no-op.
4. Line 1006-1008: child added to `paused_instances_data` despite being terminal.
5. Line 1010: `logger.info(f"Pausing instance ...")` fires falsely.
6. The `_pause_cascade_db_sync` call at line 1017-1024 includes the terminal node in `tree_ids`. All three UPDATEs have `WHERE status` guards that exclude terminal statuses, so rowcount=0 for those rows.
7. SSE emit at line 1037-1044 only fires for `updated_ids`, so no false SSE event.
8. Final result at line 1059 reports `paused_ids` correctly empty for terminal nodes, but `skipped_ids` doesn't include them.

### What's actually wrong

- DB state is correct (terminal instances stay terminal).
- SSE is correct.
- BUT: `logger.info` at line 1010 misleads operators ("Pausing instance X" when X is COMPLETED).
- `paused_instances_data` at line 1006-1008 carries terminal nodes into `_pause_cascade_db_sync`, which is wasted work.
- `skipped_ids` doesn't capture the terminal-skip reason.

### Suggested fix

Extend line 985-989 to:
```python
if meta.status in (
    InstanceStatus.PAUSED.value,
    InstanceStatus.COMPLETED.value,
    InstanceStatus.ERROR.value,
    InstanceStatus.TERMINATED.value,
):
    logger.info(f"Instance {node_id[:8]}... is in terminal/paused state ({meta.status}), skipping pause")
    skipped_ids.append(node_id)
    continue
```

This makes the intent explicit and the logs accurate.

---

## 🟢 Suggestion 1: WriteGuardSession should explicitly rollback in __exit__

**File**: `daemon/write_pause_guard.py:272-285`

### Current behavior

```python
272:     def __exit__(self, exc_type, exc_val, exc_tb) -> None:
273:         ...
281:         self.close()

289:     def close(self) -> None:
290:         ...
295:         try:
296:             self._session.close()
```

The atomicity claim relies on SQLAlchemy's implicit `rollback()` when `Session.close()` is called with a pending transaction. This is correct today, but it's an implicit contract that depends on SQLAlchemy internals.

### Suggested improvement

```python
def __exit__(self, exc_type, exc_val, exc_tb) -> None:
    if exc_type is not None:
        try:
            self._session.rollback()
        except Exception as e:
            logger.warning(f"WriteGuardSession rollback failed: {e}")
    self.close()
```

This makes the rollback explicit and protects against future SQLAlchemy behavior changes.

---

## 🟢 Suggestion 2: PENDING/FAILED cancel UPDATE should include 'paused' for clarity

**File**: `daemon/services/instance_lifecycle.py:1621-1638`

### Current behavior

```python
1621:                 if non_processing_job_ids:
1622:                     session.execute(
1623:                         text(
1624:                             "UPDATE job_queue_items "
1625:                             "SET status = 'cancelled', "
...
1628:                             "WHERE job_id IN :job_ids "
1629:                             "  AND status IN ('pending', 'failed')"
...
1638:                     )
```

PAUSED jobs are correctly selected at line 1563 (`non_terminal_statuses = ("processing", "pending", "failed", "paused")`) and placed in `non_processing_job_ids` at line 1578. They reach this UPDATE but rowcount=0 (silent skip). The post-commit cleanup loop at lines 806-834 then handles PAUSED jobs via `cancel_job()` which has `cancellable_states` including PAUSED (Phase 1 fix).

### Net effect

PAUSED jobs are correctly cancelled via the post-commit `cancel_job()` call. No bug.

### Suggested improvement

Add 'paused' to the WHERE clause at line 1629 for clarity:
```sql
WHERE job_id IN :job_ids AND status IN ('pending', 'failed', 'paused')
```

This makes the intent explicit and lets the UPDATE do the work instead of relying on the post-commit loop. It also avoids the round-trip through `cancel_job()` which acquires its own connection.

---

## 🟢 Suggestion 3: `_cancel_bus_watchers_for` docstring is stale

**File**: `daemon/services/instance_lifecycle.py:44-60`

The docstring says:
> "Called from :meth:`InstanceLifecycleService.pause_instance_cascade` and :meth:`InstanceLifecycleService.terminate_instance` after the DB status transition has committed."

But since Phase 2, it's only called from `terminate_instance` (line 913). The pause cascade no longer calls it. The docstring should be updated to reflect this.

---

## ✅ Verified Correct (no findings)

### Focus 1: ATOMICITY — VERIFIED CORRECT

`_pause_cascade_db_sync` at `instance_lifecycle.py:1843-2032`:
- Three UPDATE statements (lines 1928-1947, 1961-1976, 2001-2015).
- All inside ONE `WriteGuardSession` (line 1912).
- Single `session.commit()` at line 2021.
- Each UPDATE has `WHERE status` guard for idempotency.
- `WriteGuardSession.__exit__` (write_pause_guard.py:272-285) calls `close()` → `_session.close()` → SQLAlchemy implicit rollback on pending transaction.

**Atomic on SQLite** (single full write lock during transaction): Yes.
**Atomic on PostgreSQL** (READ COMMITTED within one transaction): Yes — `Session.close()` rolls back any pending transaction.

### Focus 4: WORKER_POOL CancelledError handler — CORRECT

`worker_pool.py:349-402`:
- CancelledError handler at line 349 returns WITHOUT calling `complete_task` or `fail_task`.
- Comment block (lines 350-400) is exhaustive — documents both orderings (cancel-first vs DB-sync-first) and explains why the silent return is the B2 contract.
- Concurrency slot release is at `Worker.run()` finally block (lines 251-257), not in the CancelledError handler itself.
- Distinguishes pause-cancellation from timeout (TimeoutError at line 340) and explicit token cancellation (OperationCancelledError at line 333).

### Focus 5: claim_pending_task pause gate — CORRECT

`task/repository.py:319-340`:
- Pause gate excludes instances in PAUSED or TERMINATED status.
- Applies to ALL task types (message and report).
- Correctly serializes against per-instance guard.

### Focus 7 edge cases — MOSTLY CORRECT

- **Double-pause**: Idempotent. Pre-classification skips already-PAUSED. All three UPDATEs have `WHERE status` guards that rowcount=0 for already-PAUSED nodes.
- **Pause instance with PENDING job but no PROCESSING**: UPDATE 2 rowcount=0 (no PROCESSING job). UPDATE 1 (instance) and UPDATE 3 (task) may succeed. Correct behavior — job stays PENDING until resume picks it up.

---

## Architectural Notes

### Pattern: WriteGuardSession as the transaction boundary

The Phase 2 implementation correctly treats `WriteGuardSession` as the transaction boundary, not the individual UPDATE statements. This is a good pattern for future cascade operations to follow.

### Pattern: Status guard on every transition UPDATE

Every UPDATE that transitions status has a `WHERE status = <expected>` guard. This makes the operations idempotent under concurrent transitions. The B2 race fix relies on this — the symmetric guards in `complete_task` (line 641) and `_pause_cascade_db_sync` UPDATE 3 (line 2006) ensure exactly one wins.

### Phase 3 dependencies surfaced by this review

1. **PAUSED → PENDING task re-arm on resume** (Warning 2): Phase 3 must implement this. The current resume path only handles RUNNING tasks.
2. **Wire `_compact_fired_watchers_for_paused` into resume** (Warning 1): Phase 3 design says "on resume" but no path calls it.

---

## Test coverage gaps

### Tests that DO exist (14 in `tests/unit/test_pause_flow_redesign.py`)

- DB-level atomicity (3-table UPDATE verification).
- Status guard edge cases (already-paused, no-jobs).
- SSE event emission.
- `_compact_fired_watchers_for_paused` unit tests (lines 525, 553, 579, 590).

### Tests that SHOULD exist but I couldn't verify

1. **B2 race integration test**: A test that fires `pause_instance_cascade` while a real worker thread is mid-execution, then verifies the task ends in PAUSED, not COMPLETED. This would catch Warning 2 and Warning 3.
2. **Resume-after-pause race test**: A test that fires pause followed by resume within milliseconds, then verifies no orphaned PAUSED tasks.
3. **Long-pause watcher accumulation test**: A test that pauses an instance, fires N child completions, verifies FIRED watcher count, runs resume, verifies compaction.
4. **Terminal-state pause rejection test**: A test that attempts to pause a COMPLETED instance and verifies the pre-classification rejects it.

Recommendation: add these as integration tests in `tests/integration/test_pause_resume_concurrency.py` (or similar). The B2 race test in particular is critical — it's the highest-risk area and is only verified by code inspection today.

---

## Confidence levels

| Focus area | Confidence | Notes |
|------------|-----------|-------|
| 1. ATOMICITY | HIGH | Three UPDATEs in one WriteGuardSession + single commit + SQLAlchemy implicit rollback on close. Standard pattern, verified by code. |
| 2. B2 RACE | MEDIUM | The race window is real but bounded by WriteGuardSession serialization. Not a production blocker, but the resume path needs Phase 3 to handle the orphan case. |
| 3. COMPACTION HOOK | HIGH | Function defined, zero production callers. Docstring says Phase 3. |
| 4. WORKER CancelledError | HIGH | Handler is correct, B2 contract documented, concurrency slot released at Worker.run() level. |
| 5. CLAIM_PENDING_TASK | HIGH | Pause gate verified at SQL level, applies to all task types. |
| 6. TERMINATE PAUSED | HIGH | PAUSED jobs flow through cancel_job in post-commit loop; net behavior is correct. |
| 7. EDGE CASES | MEDIUM | Double-pause is idempotent. Pause-with-no-PROCESSING-job is correct. Terminal-state pre-classification has a Warning (4). |
