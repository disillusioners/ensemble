# Approval Tracking: SQLite → PostgreSQL Migration

## Iteration 001 — 2026-06-02

**Verdict: REJECTED**

### Blocking Issues

1. **asyncio.Lock incompatible with synchronous SQLAlchemy (Write-Pause Mechanism)**
   - The codebase uses ~200 `asyncio.to_thread()` call sites and a `ThreadPoolExecutor(max_workers=4)` — synchronous SQLAlchemy sessions run inside worker threads
   - `asyncio.Lock` raises `RuntimeError` when acquired from a non-event-loop thread
   - The `WriteGuardSession` wrapper lives *inside* the thread — the event loop cannot preempt a running thread
   - This makes the entire write-pause mechanism non-functional as designed
   - Expected: A lock mechanism that works across the async/sync boundary (e.g., `threading.Lock`, or acquiring the gate above `to_thread()` boundary)
   - Found: `asyncio.Lock` + session-level enforcement inside threads — will crash or silently fail

2. **Internal contradiction in drain mechanism design**
   - `decisions.md` Decision 4 describes: "Atomic counter + `asyncio.Event`" with `write_enter()`/`write_exit()` bracketing + `WriteGuardSession` wrapper
   - `phase3-plan.md` Write-Pause section describes: "shared `asyncio.Lock`" (`_write_gate`) where `pause_writes()` acquires exclusively
   - These are two fundamentally different mechanisms. The plan does not resolve which one is the actual design.
   - Expected: Single consistent drain mechanism described across all documents
   - Found: Two contradictory descriptions in decisions.md vs phase3-plan.md

### Non-Blocking Concerns (do not block approval but should be addressed)

- **Q2 — Unguarded write paths**: `engine.connect()` + `conn.execute(text(...))` in factory.py, runner.py, task/repository.py bypass `WriteGuardSession`. Need audit to determine which are active at runtime vs. startup-only.
- **Q3 — ON CONFLICT targets**: Should build explicit per-table conflict target map instead of relying on implicit behavior. Mechanical fix.
- **Q7 — Type coercion during data migration**: Boolean 0/1→true/false and TEXT→JSONB conversion needed. ORM-layer migration (read as SQLModel objects, write to PG) handles this automatically — should be specified.
- **Q10 — FK ordering for crash recovery**: Must use `SQLModel.metadata.sorted_tables` for topological ordering. PG validates FK before conflict detection, so out-of-order inserts produce violations that ON CONFLICT cannot swallow. One-line fix but must be in the plan.
- **Minor claim inaccuracies**: `_engine` access is across 6 files not 7; line 336 in maintenance.py is `adelete_thread`, not a `conn`/`lock` access; "8 model files" claim is imprecise about where SQLModel table definitions live.

## Iteration 002 — 2026-06-03

**Verdict: APPROVED**

### Resolution of Iteration 001 Blocking Issues

1. **asyncio.Lock incompatibility → RESOLVED**: Plan now uses `threading.Event` + `threading.Lock`-protected atomic counter. Works across async/sync boundary. Two-layer enforcement (async gate above `to_thread()` + sync guard via `WriteGuardSession`) is sound.
2. **Internal contradiction → RESOLVED**: `decisions.md` Decision 4 and `phase3-plan.md` write-pause section are now fully consistent — both describe the same `threading.Event` + atomic counter mechanism.

### Non-Blocking Notes

1. **Maintenance.py checkpoint writes not covered by write-pause** — `CheckpointCleanupJob` runs via direct `await` in async context (zero `to_thread()` calls), so Layer 1's `to_thread()` gate does not cover it. During migration, cleanup could race with checkpoint export. Risk is bounded (only orphaned/expired data affected, 15-min check interval makes collision unlikely). Recommend: stop maintenance service during migration or add `is_write_paused` check to `CheckpointCleanupJob.execute()`.

2. **persistence.py PRAGMAs not explicitly mentioned** — Lines 53-54 (`PRAGMA busy_timeout`, `PRAGMA synchronous`) execute on the SQLite path only. Phase 2's `get_checkpointer()` restructuring implicitly covers this, but the task list should call it out explicitly for clarity.

3. **Minor documentation** — "8 model files" should be ~11; `get_checkpoint_ids` method name could be `get_recent_checkpoint_ids` for clarity; runner.py SQLite sites should be explicitly documented as "skipped entirely for PostgreSQL" rather than individually guarded.
