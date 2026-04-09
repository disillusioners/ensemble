# Plan Overview: Message Queue Redesign

## Objective

Redesign the low-level message queue and instance management from an in-memory + SQLite dual-state architecture to a **database-as-single-source-of-truth** architecture, eliminating 5 fundamental concurrency flaws (race conditions, duplicate reports, consumer deadlocks, lost messages, async/threading mixing) by making these bugs **impossible by construction**.

## Scope Assessment

**LARGE** — This affects 4 core subsystems (instance management, message queue, worker pool, SSE events), touches ~20 files, introduces 2 new tables + 2 enhanced tables, and requires a carefully staged additive migration. Estimated 3-5 days of focused implementation.

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Design Doc**: `docs/architecture/message-queue-redesign.md`
- **Requested by**: Leader
- **Key Constraint**: The high-level Job Queue (`daemon/services/job_queue_service.py`, `daemon/routers/jobs.py`, `daemon/routers/queues.py`) remains largely untouched — this redesign affects the low-level message queue and instance management layer only.

## Critical Technical Finding

### ⚠️ SQLite Does NOT Support `FOR UPDATE SKIP LOCKED`

The design doc proposes `FOR UPDATE SKIP LOCKED` for the worker pool. **SQLite ignores `FOR UPDATE` entirely.** This is not a blocking issue — we use an alternative pattern:

**Atomic Claim Pattern for SQLite:**
```sql
-- Single atomic statement: find + claim in one step
UPDATE message
SET status = 'processing', processing_task_id = ?, started_at = NOW()
WHERE id = (
    SELECT id FROM message
    WHERE instance_id = ? AND status = 'pending'
    ORDER BY priority ASC, created_at ASC
    LIMIT 1
)
RETURNING *;
```

**Why this works on SQLite:**
1. WAL mode allows concurrent reads during writes
2. SQLite serializes writes (only one writer at a time via `busy_timeout`)
3. The `UPDATE ... WHERE id = (SELECT ...)` is a single statement — atomic under SQLite's write lock
4. Two concurrent workers cannot claim the same row because the second UPDATE will find status ≠ 'pending'

**Worker poll loop adaptation:**
- Use `BEGIN IMMEDIATE` to acquire write lock at transaction start
- Use the atomic UPDATE-RETURNING pattern to claim tasks
- No need for `SKIP LOCKED` — SQLite's write serialization handles it
- Keep poll interval at 0.5s for responsiveness

## Current Architecture (Summary)

| Layer | Components | State |
|-------|-----------|-------|
| **In-Memory** | `self.instances`, `_processing`, `_instance_queues`, `_consumer_tasks` | Lost on restart |
| **SQLite** | `message_queue`, `instance`, `instance_hierarchy` tables | Persists |
| **SSE** | `EventBroadcaster` with per-instance `asyncio.Queue` | Lost on restart |
| **Watchdog** | `InstanceWatchdog` (thread, 30s checks) | Recreated on restart |
| **Circuit Breaker** | `InstanceCircuitBreaker` (5 failures → 5min) | Lost on restart |

**The Problem**: In-memory and DB state can diverge, causing 5 categories of bugs documented in the design doc.

## New Architecture (Summary)

| Layer | Components | State |
|-------|-----------|-------|
| **SQLite (enhanced)** | `instance` (status, version, children), `message` (typed, tracked), `task` (new), `event` (new) | **Single source of truth** |
| **Worker Pool** | Stateless workers polling DB with atomic claim | No persistent state |
| **SSE** | Reads from `event` table via cursor-based delivery (`Last-Event-ID`) | Survives restart, multi-client safe |
| **No in-memory state** | No `_processing`, no `_instance_queues`, no `_consumer_tasks` | Everything in DB |

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Database Schema | Create new tables and enhance existing models alongside current code | None | — | 4-6h |
| 2 | Worker Pool | Build stateless worker infrastructure with atomic task claiming | Phase 1 | loose | 6-8h |
| 3 | Migrate Message Flow | Switch message processing from consumers to worker pool | Phase 1, 2 | tight | 8-12h |
| 4 | Migrate SSE Events | Replace in-memory broadcaster with event table reads | Phase 1 | loose | 4-6h |
| 5 | Remove Old Code | Clean up deprecated code, simplify manager | Phase 3, 4 | tight | 4-6h |

### Coupling Assessment

| From → To | Coupling | Justification |
|-----------|----------|---------------|
| Phase 1 → Phase 2 | **loose** | Phase 2 depends on schema interfaces, not implementation details |
| Phase 2 → Phase 3 | **tight** | Phase 3 directly uses worker pool code from Phase 2 |
| Phase 1 → Phase 4 | **loose** | SSE migration only needs event table schema |
| Phase 3 + Phase 4 → Phase 5 | **tight** | Removal depends on both migrations being complete |

**Scheduling Recommendation:**
- Phases 1 → 2 → 3 must be sequential (tight coupling chain)
- Phase 4 can run **in parallel** with Phases 2-3 (only depends on Phase 1)
- Phase 5 must wait for both Phase 3 and Phase 4

### Parallel Execution Opportunities

```
Phase 1 (Schema) ─────────────────────┐
                                       ├── Phase 4 (SSE) ─────────┐
Phase 1 → Phase 2 (Workers) ──────────┤                          │
              │                        │                          │
              └── Phase 3 (Migration) ─┤                          │
                                       │                          │
                                       └── Phase 5 (Cleanup) ────┘
```

## Risks & Mitigations

| Risk | Impact | Phase | Mitigation |
|------|--------|-------|------------|
| SQLite atomic claim performance under load | Medium | 2 | Benchmark with 100+ concurrent tasks; WAL mode + busy_timeout=30s should handle it |
| LangGraph graph execution doesn't fit worker pattern | High | 3 | Keep graph execution in-process; workers just orchestrate the flow |
| SSE latency increases with DB polling vs in-memory | Medium | 4 | Use hybrid: DB for persistence, in-memory notification for real-time delivery |
| Migration breaks running instances | High | 3 | Additive-only changes; cutover behind global feature flag (`use_worker_pool`) |
| Data loss during migration | Critical | 3,5 | New tables alongside old; dual-write during transition; validate before removing old |
| SQLite doesn't support `RETURNING` in all versions | Medium | 2 | SQLite 3.35+ supports RETURNING; verify minimum version, provide fallback |
| Worker pool starvation | Low | 2 | Auto-scale workers based on pending task count |
| Async/thread boundary in workers | High | 2 | Workers use `asyncio.run_coroutine_threadsafe()` to call async code from threads (established pattern in manager.py:349) <!-- FIX: C1 --> |
| Multi-client SSE event delivery | High | 4 | Cursor-based delivery via `Last-Event-ID`, no `delivered` boolean; each client tracks its own position <!-- FIX: C2 --> |

## Success Criteria

- [ ] No in-memory state for message/instance status (everything in DB)
- [ ] No race conditions in dequeue/claim operations (atomic DB claims)
- [ ] No duplicate completion reports (explicit status checks)
- [ ] Messages survive application restart (DB persistence)
- [ ] SSE events survive application restart (event table)
- [ ] Worker crash recovery within 5 minutes (stale task detection)
- [ ] All existing tests pass (backward compatibility during migration)
- [ ] New test coverage for all new code paths (atomic claims, worker pool, event delivery)
- [ ] No regression in message processing latency (<100ms overhead from DB polling)

## Testing Strategy Overview

| Phase | Test Type | Focus |
|-------|-----------|-------|
| 1 | Unit tests | Schema creation, model validation, migration idempotency |
| 2 | Unit + Integration | Worker lifecycle, atomic claims, concurrent claiming, crash recovery |
| 3 | Integration + E2E | Message flow, child completion, error handling, restart recovery |
| 4 | Integration | SSE delivery, event ordering, reconnection, delivery tracking |
| 5 | Regression | Full test suite passes, no old code references remain |

## Migration Strategy

The migration follows the **Strangler Fig pattern** — new code alongside old, gradually replacing:

1. **Phase 1**: Add new tables (zero disruption)
2. **Phase 2**: Build workers (read-only, no production traffic)
3. **Phase 3**: Global feature-flag cutover — new path activated for all instances <!-- FIX: W2 -->
4. **Phase 4**: SSE cutover — clients reconnect to new endpoint
5. **Phase 5**: Remove old code after validation period

## Tracking

- **Created**: 2026-04-08
- **Last Updated**: 2026-04-08 (rev2 — addressed review feedback C1-C5, W1-W8)
- **Status**: draft
