# Architecture Decisions: Jober Agent (REVISED v4)

## ADR-001: Shared Notification Service — ALL 7 Terminal Paths (REVISED)

**Decision**: Extract `notify_watchers()` into `JobQueueService` as the single notification function. Call it from EVERY terminal transition path — all **7** of them.

**Context**: Jobs reach terminal states through 7 code paths:

| Path | Method | File | Terminal State | When Notified |
|------|--------|------|----------------|---------------|
| 1 | `_process_event()` | `job_feedback_observer.py` | COMPLETED, FAILED | After `atomic_transition()` |
| 2 | `cancel_job()` | `job_queue_service.py` | CANCELLED | After `atomic_transition()` |
| 3 | `complete_job()` / `complete_job_sync()` | `job_queue_service.py` | COMPLETED, FAILED, TERMINATED | After successful transition |
| 4 | `terminate_instance()` | `instance_lifecycle.py` | TERMINATED | Via `complete_job_sync()` (Path 3) |
| 5 | `move_to_dlq_standalone()` | `dead_letter_service.py` | DEAD_LETTER | After `session.commit()` |
| 6 | `maybe_retry()` → `move_to_dlq()` | `job_retry_engine.py` | DEAD_LETTER | After `session.commit()` in `maybe_retry()` |
| 7 | `_fail_orphaned_job()` | `job_recovery_service.py` | FAILED | After `atomic_transition()` during startup |

Previous plan only hooked into Path 1, meaning 6 of 7 paths silently lost notifications.

**Key design constraints**:
- Paths 5 and 6: Run inside synchronous DB transactions. `move_to_dlq()` participates in a shared session. Notify AFTER commit using `asyncio.run_coroutine_threadsafe()`.
- Path 7: Runs during daemon startup BEFORE `JobFeedbackObserver` starts. The watching instance may not be running yet. Notifications queue as DB messages for later delivery.

**Consequences**:
- (+) Guaranteed notification for ALL terminal states from ALL paths
- (+) Single implementation to test and maintain
- (+) Observer stays simple — just calls the shared function
- (-) Dead letter notification is async-scheduled after sync commit (acceptable — fire-and-forget)

---

## ADR-002: SQLite-Backed Watcher Store with JSON Events (REVISED)

**Decision**: Use a new SQLite table `job_watchers` with a JSON column for `watch_events`.

**Schema**:
```
job_watchers:
  watch_id: str (PK)
  job_id: str (FK → job_queue_items.job_id, indexed)
  instance_id: str (FK → instances.instance_id, indexed)
  created_at: datetime
  watch_events: JSON (list[str])
```

**Default `watch_events`**: `["completed", "failed", "cancelled", "terminated", "dead_letter"]` — includes ALL terminal states. Previous version omitted `dead_letter`, causing jobs entering DEAD_LETTER to silently skip notification.

**Rationale for JSON column**:
- Type-safe event list (no string parsing)
- Consistent with existing `metadata_json` pattern (`Column(JSON)`)
- Easy to query: `WHERE watch_events LIKE '%"dead_letter"%'` if needed

**Consequences**:
- (+) All 5 terminal states covered by default
- (+) No comma-string parsing bugs
- (+) Crash recovery: watches survive daemon restart
- (+) Easy queries by job_id (indexed)

---

## ADR-003: Atomic Watch on job_create — Watch BEFORE Enqueue

**Decision**: `watch=True` on `job_create` registers the watch BEFORE calling `enqueue()`.

**Context**: `enqueue()` creates a PENDING job. The observer only processes PROCESSING → terminal transitions. So registering a watch while the job is PENDING is inherently safe — no race condition.

**Implementation order**:
```python
job_item = await job_service.enqueue(...)  # Creates PENDING job
if watch and watcher_repo:
    watcher_repo.add_watch(job_item.job_id, current_instance_id)  # Safe: still PENDING
```

**Consequences**:
- (+) Truly atomic — no race condition
- (+) Simpler agent workflow (one call)
- (+) Backward compatible (default False)

---

## ADR-004: No Bash, No Filesystem for Jober

**Decision**: The jober agent has NO bash or filesystem tools — it delegates all work.

**Context**: (Unchanged) Pure orchestrator. Execution tools would encourage direct work.

**Consequences**: (Unchanged)

---

## ADR-005: watch_queue — Deferred

**Decision**: Do NOT implement `watch_queue` in this plan. Can be added later.

**Context**: (Unchanged)

---

## ADR-006: Internal Agent Message Prefix

**Decision**: Use `source=f"internal_agent:job_event:{job_id}:{status}"` for all watcher notifications.

**Context**: `enqueue_message()` classifies by prefix: `internal_agent:` → AGENT, everything else → HUMAN (triggers project context injection).

**Consequences**:
- (+) Correct `MessageType.AGENT` classification
- (+) No unwanted project context injection

---

## ADR-007: Structured JSON in Notifications

**Decision**: Include a JSON code block at the end of every notification for reliable LLM parsing.

**Format**:
```
[JOB_EVENT] Job {id}... reached status '{status}'.
Agent: ...
Result: ...
Error: ...

```json
{"job_id": "...", "status": "...", ...}
```
```

**Consequences**: (Unchanged)

---

## ADR-008: Startup Reconciliation

**Decision**: On daemon startup, scan `job_watchers` for jobs already in terminal states (including `dead_letter`) and deliver missed notifications.

**Context**: Crash between terminal transition and notification delivery leaves stale watches.

**Reconciliation query**:
```sql
SELECT w.* FROM job_watchers w
JOIN job_queue_items j ON w.job_id = j.job_id
WHERE j.status IN ('completed', 'failed', 'cancelled', 'terminated', 'dead_letter')
```

**Consequences**: (Unchanged)

---

## ADR-009: Dead Letter Notification After Transaction Commit (NEW)

**Decision**: `move_to_dlq()` (shared-session version) does NOT contain notification code. Instead, notification is called at each call site AFTER `session.commit()`.

**Context**: `DeadLetterService.move_to_dlq()` is designed to participate in shared transactions — it does NOT commit. Adding notification inside it would mean notification fires before commit (could be rolled back) and would need async-in-sync context.

**Call sites and their notification approach**:
- `move_to_dlq_standalone()` — calls `session.commit()` itself. Add `notify_watchers()` via `run_coroutine_threadsafe()` after commit.
- `maybe_retry()` — calls `move_to_dlq()` then `session.commit()`. Add `notify_watchers()` via `run_coroutine_threadsafe()` after commit.

**Rationale**:
- Notification only fires for committed state changes (correct semantics)
- No async-in-transaction contamination
- If transaction rolls back, no spurious notification
- Same pattern used for `complete_job_sync()` (sync → async bridge)

**Consequences**:
- (+) Notification only on committed state
- (+) No transaction contamination
- (-) Must remember to add notification at future call sites of `move_to_dlq()` (documented in code comments)

---

## ADR-010: Orphan Recovery Notification — Bootstrap Ordering (NEW)

**Decision**: Wire `JobQueueService` into `JobRecoveryService` so `_fail_orphaned_job()` can call `notify_watchers()`. Ensure `watcher_repo` is created and wired BEFORE `recover_on_startup()` runs.

**Context**: `JobRecoveryService._fail_orphaned_job()` (line 149-179 in `job_recovery_service.py`) directly calls `self._job_repository.atomic_transition()` to mark orphaned PROCESSING jobs as FAILED. It bypasses `JobQueueService.complete_job()` entirely, so Path 3's notification hook doesn't fire.

**Why this matters**:
- `recover_on_startup()` runs at daemon startup (line 193 in `api.py`)
- It runs BEFORE `JobFeedbackObserver` starts (line ~205)
- If a watched job was in PROCESSING when the daemon crashed, recovery marks it FAILED
- Without notification, the watcher never learns the job failed — it hangs forever

**Implementation**:
- Add optional `job_queue_service` parameter to `JobRecoveryService.__init__()` (backward compatible — default None)
- In `_fail_orphaned_job()`, after successful `atomic_transition()`:
  ```python
  if self._job_queue_service is not None:
      await self._job_queue_service.notify_watchers(job.job_id, "failed", error_message)
  ```
- `_fail_orphaned_job()` is already async — can call `notify_watchers()` directly (no `run_coroutine_threadsafe`)

**Bootstrap ordering in `api.py`**:
```
1. Create engine, existing repos
2. CREATE watcher_repo              ← early
3. Wire watcher_repo into JobQueueService
4. Wire JobQueueService into JobRecoveryService
5. Run recover_on_startup()         ← Path 7 fires here
6. Run reconcile_terminal_watches() ← catches anything missed
7. Initialize DeadLetterService, JobFeedbackObserver, etc.
```

**Startup message delivery**: The watching instance may not be running yet when Path 7 fires. `enqueue_message()` persists the message to DB. When the instance is later spawned/resumed, queued messages are delivered normally. No special handling needed — this is how `enqueue_message()` already works.

**Consequences**:
- (+) No missed notifications for orphan-recovered jobs
- (+) Reuses existing `enqueue_message()` DB persistence for offline delivery
- (+) No special startup handling needed for message delivery
- (-) `JobRecoveryService` gains dependency on `JobQueueService` (acceptable — same domain, optional param)
- (-) Bootstrap ordering must be maintained (documented in `api.py` comments)
