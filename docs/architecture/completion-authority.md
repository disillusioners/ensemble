# Completion Authority

> **Status:** Authoritative for the post-cleanup architecture (2026-06-24).
> **Owns:** The decision "is this parent's correlation complete?".
> **Companion code:** [`daemon/services/dependency_bus.py`](../../daemon/services/dependency_bus.py) — `DependencyBus` (sole completion authority).
> **Companion code:** [`daemon/repositories/dependency_bus/`](../../daemon/repositories/dependency_bus/) — `DependencyWatcherRepository` over the `dependency_watchers` table.

---

## 1. Overview

A parent instance is **complete** when every child it sent a message to has reported back (with or without error). The architecture has one and only one mechanism for answering this question: the **`DependencyBus`**, a DB-backed service that watches terminal events from child tasks and enqueues FollowUp messages onto the parent.

The `DependencyBus` replaced the older `CorrelationManager` (an in-memory `_pending` set with a per-parent `asyncio.Lock`) and the older `waiting_for` SQL counter (a denormalized cache decremented alongside completion reports). Both older mechanisms have been removed; the bus is the sole completion authority.

---

## 2. The Mechanism

### 2.1 Watcher rows

When a parent sends a message to a child (via `send_message`), the bus writes a row to the `dependency_watchers` table:

| Column | Purpose |
|--------|---------|
| `source_task_id` | The child's task ID — the row is keyed by this so a terminal event can find its watcher in O(1). |
| `target_instance_id` | The parent that should receive the FollowUp. |
| `follow_up_payload` | The pre-built FollowUp message (completion report or error report) that the parent will receive when the child terminates. |
| `state` | `PENDING` → `FIRED` (success) or `CANCELLED` (parent terminated). |
| `fired_at` / `cancelled_at` | Timestamps. |

The DB row IS the watcher state. There is no in-memory mirror that needs to be rebuilt on restart.

### 2.2 Terminal event flow

When a child's task reaches a terminal event (`COMPLETED` / `ERROR` / `TERMINATED`), the task processor calls:

```python
await dependency_bus.emit_terminal(source_task_id, outcome)
```

The bus:

1. Looks up the watcher row by `source_task_id` (O(1) via the unique index).
2. Atomically transitions `PENDING → FIRED` via a guarded `UPDATE ... WHERE state = 'PENDING'`. The rowcount tells the bus whether this call won the race — if the watcher was already `FIRED` or `CANCELLED`, the call is a no-op (idempotent).
3. Enqueues the `FollowUp` payload onto the parent instance.

The atomic state transition is the backpressure primitive that prevents double-fire under concurrent terminal events.

### 2.3 Cancellation

When a parent is paused, resumed, or terminated, the bus calls `cancel_for_target(target_instance_id)`. This cancels all watchers whose `target_instance_id` matches, so no FollowUp is enqueued onto a dead or paused instance. The transition is `PENDING → CANCELLED` and is also atomic.

---

## 3. Crash Recovery

Watcher state is the DB row. On daemon restart, the bus loads all `PENDING` watchers into an in-memory cache and continues. There is no separate `rebuild_from_db` step like the old `CorrelationManager` had — the DB is the source of truth by construction.

A subtle case: if the process crashes *after* the bus transitions a watcher to `FIRED` but *before* the FollowUp is enqueued, the row is `FIRED` but the FollowUp was lost. The bus handles this in `start()` via `_recover_fired_unsent`, which loads FIRED rows that have not been marked enqueued and returns them to the caller for re-enqueueing.

---

## 4. Multi-Process Limitation

The per-parent **generation counter** (used by `JobFeedbackObserver._finalize_job` for the orphan-race re-arm) is **in-memory only** — it is NOT persisted to the DB and is NOT restored on bus restart. After a bus restart the counter starts fresh at `{}` and every parent returns `0` until the next `watch()` bumps it back.

This is safe in single-process deployments (the only writer is the in-process `watch` and the only reader is the in-process `_finalize_job` — both reset/restart together). For multi-process deployments, the counter MUST be shared across processes (e.g. via Redis or a DB column on `dependency_watchers`). Until then, multi-process deployments are not supported.

---

## 5. API Reference

| Method | Purpose |
|--------|---------|
| `watch(source_task_id, target_instance_id, follow_up_payload, metadata)` | Called on `send_message`. Writes a `dependency_watchers` row keyed by `source_task_id`. |
| `emit_terminal(source_task_id, outcome)` | Called on task terminal. Atomically fires the watcher (or no-ops if already FIRED / CANCELLED) and enqueues the FollowUp. |
| `cancel_for_target(target_instance_id)` | Cancels all watchers targeting this instance (pause / terminate). |
| `pending_watchers(source_task_id)` | Returns the PENDING watchers for a source task (cache → DB fallback). |
| `count_pending_for_target(target_instance_id)` | Counts PENDING watchers for an instance (used by parent-completion checks). |
| `start()` | Loads PENDING watchers into the cache and recovers any FIRED-but-not-enqueued rows from a prior crash. |
| `stop()` | Cleanup on shutdown. |
| `mark_enqueued(watch_id)` | Marks a FIRED row as enqueued (used by the FollowUp enqueue path to recover from crashes). |
| `get_generation(parent_id)` / `increment_generation(parent_id)` | In-memory generation counter for orphan-race re-arming (see §4). |

---

## 6. Related Documents

- [`docs/architecture/message-processing-and-correlation.md`](message-processing-and-correlation.md) — current message-processing architecture (the bus's role in the pipeline).
- [`docs/architecture/job-task-pause-resume.md`](job-task-pause-resume.md) — pause/resume uses `cancel_for_target` to clean up watchers.
- [`daemon/services/dependency_bus.py`](../../daemon/services/dependency_bus.py) — implementation.
- [`daemon/repositories/dependency_bus/`](../../daemon/repositories/dependency_bus/) — DB layer.
- [`daemon/migrations/versions/20260621_000001_create_dependency_watchers.sql`](../../daemon/migrations/versions/20260621_000001_create_dependency_watchers.sql) — schema.