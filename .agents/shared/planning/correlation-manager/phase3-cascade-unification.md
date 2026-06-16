# Phase 3: Cascade Unification

## Objective
Unify the 3 divergent cascade decision sites into a single delegation to CorrelationManager. This eliminates Race #3 (HIGH severity) through **pure in-memory set operations** (not DB queries) and removes 3 separate copies of cascade logic with divergent behavior.

## Coupling
- **Depends on**: Phase 1 (CorrelationManager), Phase 2 (observer uses CM callback)
- **Coupling type**: tight
- **Shared files with other phases**: `child_reports.py`, `error_reporting.py`, `message_job_handler.py` — Phase 4 will further modify these to remove `waiting_for`
- **Shared APIs/interfaces**: `CorrelationManager.resolve_response()`, per-parent Lock
- **Why this coupling**: Phase 3 replaces cascade logic that Phase 2's callback consumes. Event semantics must be coordinated.

## Context
Three sites currently make parent-completion decisions with divergent logic:

| Site | File:Lines | Status Guard | Reads pending_count? |
|------|-----------|-------------|---------------------|
| 1A | `child_reports.py:478-524` | `!= COMPLETED AND != ERROR` | ✅ Yes (lines 484-493) |
| 1B | `child_reports.py:685-715` | `parent is None AND wf==0` | ✅ Yes (lines 685-695) |
| 2 | `error_reporting.py:240-296` | `!= COMPLETED` only | ✅ Yes (lines 242-251) |

### ⚠️ Race #3 Elimination Strategy (Fix C5)

**The original plan claimed Race #3 was eliminated but still used `count_pending` DB query — the TOCTOU window remained.**

**Correct approach: Pure in-memory set operations, no DB query.**

CorrelationManager already tracks pending responses as a set:
```
pending[parent_id] = {correlation_key_1, correlation_key_2, ...}
```

The completion decision is:
1. `resolve_response(parent, child, message_id)` removes a key from the set
2. If set becomes empty → fire `correlation.complete` callback
3. **No DB query needed** — the set IS the source of truth

This eliminates Race #3 because:
- The set operation (`set.discard(key)`) is atomic within the per-parent Lock
- There is no `SELECT COUNT(*)` → decide → commit window
- A concurrent `register_message_send` (new message enqueued) acquires the same Lock and adds to the set BEFORE the completion check runs

### ⚠️ "Message arrives after child completes" Edge Case

**Scenario**: CM fires `correlation.complete` because all responses resolved. Then a new `send_message` arrives for the same parent.

**This is NOT a race** — it's a new work cycle:
1. CM fires callback → observer completes the job
2. `send_message` calls `register_message_send` → CM re-adds parent to tracking
3. Instance revival logic (`instance_messaging.py:773-783`) revives from COMPLETED → RUNNING
4. When new child responds → CM fires callback again → new job cycle completes

**The only dangerous case**: `register_message_send` and `resolve_response` race within the same Lock cycle. But the per-parent Lock (Fix C4) prevents this:
- If `register` runs first: set grows from 0 to 1, `resolve` later removes it → no premature completion
- If `resolve` runs first: set is empty, callback fires; then `register` adds a new entry → new cycle

**The Lock makes the ordering deterministic, not racy.**

### Concurrency Model (Fix C4)

The 3 cascade sites are called from 4 different concurrent contexts:
1. `task_processor.py:389` — WorkerPool thread
2. `message_job_handler.py:317` — JobQueue asyncio task
3. `manager.py:2743` — resume background asyncio task
4. `worker_pool.py:400` — WorkerPool thread via MainLoopBridge

**These are NOT within the EventBus consumer loop.** The original plan's serialization assumption was wrong.

**Solution**: All `resolve_response` and `register_message_send` calls use the per-parent `asyncio.Lock` from Phase 1. This serializes all operations for the same parent across all calling contexts.

## Tasks

### Part A: Replace Cascade Logic with CM Delegation

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Remove `count_pending` DB query from cascade sites | The `SELECT COUNT(*) FROM MessageQueue` at child_reports.py:484-493 and error_reporting.py:242-251 is replaced by CM's in-memory set check | `daemon/services/child_reports.py`, `daemon/services/error_reporting.py` |
| 2 | Delegate Site 1A to CM `resolve_response` | `child_reports.py:478-524` — call `cm.resolve_response()` instead of inline cascade; CM's set-empty check replaces the count_pending check | `daemon/services/child_reports.py` |
| 3 | Delegate Site 2 to CM `resolve_response` | `error_reporting.py:240-296` — same delegation; unifies the divergent ERROR status guard | `daemon/services/error_reporting.py` |
| 4 | Fix Site 1B (root fallback) — read-only CM check + existing queue check | `child_reports.py:685-715` — root instance does NOT call `resolve_response` (it's not a child response). Uses `cm.is_complete(instance_id)` as read-only check for "all children done?", keeps existing `message_queue` pending-count logic for "does root have pending work?" (Fix A2) | `daemon/services/child_reports.py` |
| 5 | Keep `waiting_for` SQL decrement (for now) | The SQL decrement stays for DB consistency; CM hooks alongside it. Phase 4 removes both. | `daemon/services/child_reports.py:424-438` |
| 6 | Implement `_determine_terminal_status` in CM | Track response outcomes: any error → "error", all success → "completed" | `daemon/services/correlation_manager.py` |

### Part B: Move Event Publication to CM

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 7 | CM fires lifecycle event on completion | When CM's callback fires, it publishes `_publish_instance_lifecycle_event` — single publication point | `daemon/services/correlation_manager.py` or observer callback |
| 8 | Remove inline lifecycle event from cascade sites | `child_reports.py:879` and `error_reporting.py:275` no longer publish inline — CM callback handles it | `daemon/services/child_reports.py`, `daemon/services/error_reporting.py` |

### Part C: Testing

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 9 | Test pure set-based completion | Register 3 sends, resolve 3, verify callback fires on last resolve (no DB query) | `tests/test_cascade_unified.py` (new) |
| 10 | Test Race #3 elimination | Concurrently `register_message_send` + `resolve_response` for same parent; verify no premature completion | `tests/test_cascade_race3.py` (new) |
| 11 | Test error+success path symmetry | Test that a parent whose last response is error gets same treatment regardless of order | `tests/test_cascade_unified.py` |
| 12 | Test concurrency (Fix C4) | Spawn 3 concurrent `resolve_response` calls for same parent; verify Lock serializes them correctly | `tests/test_cascade_concurrency.py` (new) |
| 13 | Regression: all existing tests pass | Verify no behavioral change in happy-path scenarios | `tests/test_child_reports*.py` |

## Unified Completion Flow (Fix C5 — No DB Query)

### Before (Race #3 Present)
```
child_reports.py:424 — waiting_for decrement SQL
child_reports.py:478 — if waiting_for == 0:
child_reports.py:484 —   parent_pending = SELECT COUNT(*) FROM MessageQueue ← RACE WINDOW
child_reports.py:495 —   if parent_pending == 0: parent.status = COMPLETED
```

### After (Pure Set Operations)
```
child_reports.py:424 — waiting_for decrement SQL (kept for now, Phase 4 removes)
child_reports.py:4XX — await cm.resolve_response(parent_id, child_id, message_id)
                        ↓ (within CM's per-parent Lock)
cm: pending[parent].discard(key)
cm: if pending[parent].is_empty:
cm:     callback(parent_id, terminal_status)   ← fires here, no DB query
cm:     → observer: atomic_transition(job, COMPLETED)
cm:     → observer: notify_watchers(job, "completed")
```

**No `SELECT COUNT(*)` anywhere in the completion path.** The in-memory set IS the source of truth.

## Changes to Each Cascade Site

### Site 1A: `child_reports.py:_update_parent_on_child_complete` (lines 478-524)

**Before:**
```python
# lines 478-524
if (parent.waiting_for == 0 
    and parent.status != InstanceStatus.COMPLETED.value
    and parent.status != InstanceStatus.ERROR.value):
    parent_pending = session.exec(select(func.count())...)  # RACE #3
    if parent_pending == 0:
        parent.status = InstanceStatus.COMPLETED.value
        ...
    else:
        parent.status = InstanceStatus.WAITING_CHILDREN.value
        ...
```

**After:**
```python
# The CM resolve_response handles everything:
completed = await self._correlation_manager.resolve_response(
    parent_id=parent.instance_id,
    child_id=instance.instance_id,
    message_id=completed_message_id,
    status="responded",
)
# CM fires callback internally if all responses resolved.
# No count_pending query. No inline status transition. No inline event publish.
# Return values mirror old behavior for caller compatibility:
if completed:
    return False, parent.instance_id, parent.parent_id  # cascade to grandparent
return True, None, None  # still waiting
```

### Site 2: `error_reporting.py:_send_error_report` (lines 240-296)

**Before:**
```python
# lines 240-296 — divergent: only checks != COMPLETED, not != ERROR
if parent.waiting_for == 0 and parent.status != InstanceStatus.COMPLETED.value:
    parent_pending = session.exec(select(func.count())...)
    if parent_pending == 0:
        parent.status = InstanceStatus.COMPLETED.value
        session.commit()
        await self._events_service._publish_instance_lifecycle_event(...)
```

**After:**
```python
# Same delegation — unified logic, fixes the ERROR status divergence
completed = await self._correlation_manager.resolve_response(
    parent_id=parent_id,
    child_id=instance_id,
    message_id=message_id,
    status="error",  # mark this response as error
)
# CM applies unified status guard + determines terminal_status conservatively
```

### Site 1B: `child_reports.py:_process_child_completion_and_notify_parent` (lines 685-715)

**Before:** Root instance `pending_count > 0` fallback using `SELECT COUNT(*)`.

**⚠️ Fix A2: Root completion is NOT a child response — do not call `resolve_response`**

The original plan proposed `cm.resolve_response(parent_id=instance_id, child_id=instance_id, ...)` for the root fallback. This is wrong because:
1. The root instance checking its own pending messages is NOT a child-response correlation
2. The self-referential key `f"{instance_id}:{message_id}"` would never match any registered key
3. `resolve_response` would always return `False` → completion silently skipped

Root completion has **two independent conditions** that must BOTH be true:
1. **All child responses received** → CM check: `cm.is_complete(instance_id)` (read-only, does not modify state)
2. **No pending messages in own queue** → existing `SELECT COUNT(*)` logic stays as-is

**After:**
```python
# Site 1B: Root instance self-completion check
# This is NOT a child-response resolution — it's a read-only check.
# Do NOT call cm.resolve_response() here.

# Condition 1: Are all child responses received?
# (read-only check — does not modify CM state)
all_children_done = self._correlation_manager.is_complete(instance_id)
if not all_children_done:
    # Still waiting for child responses — stay in current status
    return

# Condition 2: Does root have pending messages in its own queue?
# (existing logic — kept as-is, this is a different concern than correlation)
pending_count = session.exec(
    select(func.count())
    .select_from(MessageQueue)
    .where(MessageQueue.instance_id == instance_id)
    .where(MessageQueue.message_id != completed_message_id)
    .where(MessageQueue.status.in_([
        MessageStatus.READY.value,
        MessageStatus.PROCESSING.value,
        MessageStatus.RETRYING.value,
    ]))
).scalar_one()

if pending_count > 0:
    # Has pending messages but all children done — wait
    instance.status = InstanceStatus.WAITING_CHILDREN.value
    session.commit()
    # ... SSE emit ...
    return

# Both conditions met: all children done + no pending messages
instance.status = InstanceStatus.COMPLETED.value
session.commit()
# ... lifecycle event publish (or CM callback handles it) ...
```

**Why `count_pending` stays here but not in Site 1A/2:**
- Site 1A and Site 2 handle child→parent response correlation — the CM set IS the source of truth there (Race #3 eliminated)
- Site 1B checks the root's own message queue for self-pending-work — this is a fundamentally different question that CM doesn't track
- The root's pending messages are messages from external sources (HTTP, scheduler, etc.), not child responses
- Therefore the `SELECT COUNT(*)` in Site 1B is NOT subject to Race #3 (no concurrent `enqueue_message` from a child that would affect the root's completion decision in the same way)

## Key Design Decisions

### 1. Pure In-Memory Set Operations Eliminate Race #3 (Fix C5)
**Decision**: Completion check uses `pending[parent].is_empty` (in-memory set), NOT `SELECT COUNT(*) FROM MessageQueue`.
**Rationale**:
- The original plan still had `count_pending` query — the TOCTOU window remained
- Set operations within per-parent Lock are atomic — no window for concurrent inserts
- A concurrent `register_message_send` for the same parent acquires the Lock first, adds to set, then releases — the `resolve_response` sees the non-empty set and doesn't fire premature completion
- **The set IS the correlation state. No DB query needed.**

### 2. Per-Parent Lock Serializes All Concurrent Callers (Fix C4)
**Decision**: All `register_message_send`, `resolve_response`, and `check_parent_completion` calls for the same parent are serialized via `asyncio.Lock`.
**Rationale**:
- 4 concurrent calling contexts (WorkerPool, JobQueue, resume, MainLoopBridge) are NOT within EventBus loop
- Without Lock: two concurrent resolves could both see `pending_count > 0`, neither fires completion → parent stuck
- Per-parent Lock is fine-grained: different parents process in parallel
- At ~1 msg/sec volume, contention is negligible

### 3. CM Owns Status Transition + Event Publication
**Decision**: CM's callback sets parent status AND publishes lifecycle event.
**Rationale**:
- Eliminates divergent event publication timing
- Single code path for "emit completed lifecycle event" → consistent ordering
- Callback runs within Lock — event published before Lock releases, preventing a concurrent caller from seeing stale state

### 4. Conservative Error Propagation
**Decision**: If any response was an error, parent terminal status is "error".
**Rationale**:
- Current behavior is inconsistent: Site 1A preserves ERROR, Site 2 overwrites to COMPLETED
- Conservative approach: a parent with any errored child should reflect that

### 5. `waiting_for` Decrement Remains (Until Phase 4)
**Decision**: The SQL decrement stays alongside CM hooks.
**Rationale**:
- DB consistency: `waiting_for` is still read by other code paths
- Rollback safety: if CM has a bug, DB counter still works
- Phase 4 removes both CM hooks and SQL together

## Key Files

| File | Purpose |
|------|---------|
| `daemon/services/correlation_manager.py` | `resolve_response()` with set-based completion check |
| `daemon/services/child_reports.py:378-526` | Site 1A — delegate to CM |
| `daemon/services/child_reports.py:627-715` | Site 1B — delegate root fallback to CM |
| `daemon/services/error_reporting.py:195-296` | Site 2 — delegate error cascade to CM |
| `daemon/services/message_job_handler.py:330-352` | JobQueue deferral — uses CM pending count |
| `daemon/services/job_feedback_observer.py` | `handle_correlation_complete` callback — the completion handler |

## Constraints
- Sites 1A and 2 delegate cascade decisions to CM `resolve_response` (child→parent correlation)
- Site 1B does NOT call `resolve_response` — it uses read-only `cm.is_complete()` + existing queue check (Fix A2)
- Root completion requires TWO independent conditions: (1) all children done [CM], (2) no pending queue messages [existing query]
- `resolve_response` must be safe to call concurrently for different parents (different Locks)
- For the same parent, calls must be serialized (same Lock)
- Must not change LangGraph checkpoint interaction
- `waiting_for` SQL decrement remains for DB consistency until Phase 4
- No `SELECT COUNT(*) FROM MessageQueue` in Sites 1A/2 completion path (Fix C5); Site 1B retains it (different concern)

## Verification Strategy

1. **Unit test — pure set completion**: Register 3 sends, resolve 3 one by one, verify callback fires ONLY on the 3rd resolve (no DB query involved)
2. **Unit test — Race #3 elimination**: Concurrently `register_message_send` + `resolve_response` for same parent within test; verify no premature completion (the Lock prevents the race)
3. **Unit test — concurrency (Fix C4)**: 3 concurrent `resolve_response` calls for same parent; verify Lock serializes them — callback fires exactly once, after the last resolve
4. **Unit test — error path symmetry**: Error response and success response for same parent → verify terminal_status is "error" (conservative)
5. **Integration test**: Spawn children, send messages, complete them, verify parent transitions correctly through CM (no count_pending query in logs)
6. **Unit test — Site 1B root completion (Fix A2)**: Root instance with no children → `cm.is_complete()` returns True + queue empty → COMPLETED; root with pending queue messages → stays WAITING_CHILDREN; root with children still pending → stays (children not done)
7. **Regression**: All existing child_reports and error_reporting tests pass
8. **Shadow mode validation**: After switching to CM decisions, shadow mode shows ZERO mismatches

## Rollback Plan

1. Restore inline cascade logic from git (including `count_pending` queries)
2. CM's `resolve_response` remains but is no longer called by cascade sites
3. Shadow mode validation resumes

The rollback is **safe** because:
- `waiting_for` decrement was never removed (still in DB)
- No DB schema changes
- The inline cascade logic is restored to pre-Phase-3 state

## Deliverables
- [ ] `resolve_response` implements pure set-based completion (no DB query) (Fix C5)
- [ ] Site 1A delegates to CM (no count_pending query)
- [ ] Site 2 delegates to CM (unified ERROR guard)
- [ ] Site 1B uses read-only `cm.is_complete()` + existing queue check (Fix A2 — NOT resolve_response)
- [ ] Per-parent Lock serializes all callers (Fix C4)
- [ ] `_determine_terminal_status` implements conservative error propagation
- [ ] Event publication centralized in CM callback
- [ ] Race #3 regression test (concurrent register + resolve)
- [ ] Concurrency test (parallel resolves for same parent)
- [ ] Site 1B root completion test (two-condition check)
- [ ] All existing tests pass
