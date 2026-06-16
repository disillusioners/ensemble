# Phase 1: CorrelationManager Introduction

## Objective
Create the `CorrelationManager` — a new in-memory component that tracks **pending message-response correlations** (not child existence) and emits `correlation.complete` events when all responses arrive. Runs in **shadow mode** alongside the existing `waiting_for` counter, logging mismatches for validation without affecting runtime behavior.

## Coupling
- **Depends on**: None (can run in parallel with Phase 0)
- **Coupling type**: independent
- **Shared files with other phases**: None — new file only
- **Shared APIs/interfaces**: EventBus `subscribe_all`, Instance repository queries, message queue queries
- **Why this coupling**: Shadow mode means zero interaction with existing logic; pure additive component

## Context
- EventBus has exactly 1 subscriber today (JobFeedbackObserver) and supports N subscribers via `subscribe_all()`
- CorrelationManager becomes the 2nd subscriber
- In-memory state is rebuildable from DB on restart

### ⚠️ Critical Semantic Understanding (Fix C1)

**`waiting_for` tracks pending message RESPONSES from children, NOT child existence.**

| Operation | `waiting_for` effect | Code Location |
|-----------|---------------------|---------------|
| `spawn_instance(agent_id)` | **NO change** | `tools/instance.py:462-500` — does NOT touch `waiting_for` |
| `send_message(child_id, msg)` | **+1** | `tools/instance.py:554-583` — only when `target.parent_id == sender_id` |
| Child processes message + sends completion report | **-1** | `child_reports.py:424-438` — atomic SQL decrement |

This means a parent can spawn children and complete its own job without waiting — it only waits when it explicitly `send_message()` to a child expecting a response. The CorrelationManager must track the **send_message → completion_report** correlation, not the spawn → child_complete correlation.

## Architecture

### CorrelationManager Design

```
┌──────────────────────────────────────────────────────────────┐
│                      CorrelationManager                       │
│                                                               │
│  pending_responses[parent_id] → {correlation_key → ChildMsg} │
│                                                               │
│  correlation_key = (child_id, message_id)                     │
│  — one entry per send_message that expects a response         │
│                                                               │
│  Hooks:                                                       │
│  • on_send_message(parent, child, message_id) → add entry     │
│  • on_child_response(child, message_id) → remove entry        │
│    → if set empty: emit correlation.complete(parent_id)       │
│                                                               │
│  Concurrency: asyncio.Lock per parent_id                      │
│  Event Channel: Direct callback (not EventBus queue)          │
└──────────────────────────────────────────────────────────────┘
```

### Data Model (Fix C1 — Message-Response Level, Not Child Level)

```python
@dataclass
class PendingResponse:
    """Tracks a single outstanding send_message → response correlation."""
    parent_id: str       # The instance that sent the message (and waits)
    child_id: str        # The instance that received the message
    message_id: str      # The specific message sent (for correlation)
    created_at: float    # For timeout / staleness detection
    status: str          # "pending" | "responded" | "error"

@dataclass
class ParentCorrelation:
    """All outstanding message-response correlations for one parent."""
    parent_id: str
    pending: dict[str, PendingResponse]  # correlation_key → PendingResponse
    # correlation_key = f"{child_id}:{message_id}"
    had_error: bool = False  # Fix N2: set True when any response resolves with error

    @property
    def is_complete(self) -> bool:
        """True when all sent messages have received responses."""
        return len(self.pending) == 0

    @property
    def pending_count(self) -> int:
        """Number of outstanding responses — should match DB waiting_for."""
        return len(self.pending)
```

### CorrelationManager Class Interface

```python
class CorrelationManager:
    """
    In-memory correlation tracker for parent → child message-response pairs.

    Tracks: "parent sent message X to child Y, awaiting response."
    Emits: correlation.complete(parent_id) when all responses arrive.

    Semantic alignment with waiting_for:
    - waiting_for is incremented by send_message (tools/instance.py:571)
    - waiting_for is decremented when child completion is processed (child_reports.py:424)
    - CorrelationManager.register_message_send() mirrors the increment
    - CorrelationManager.resolve_response() mirrors the decrement

    State is purely in-memory. On restart, rebuilt from DB by querying
    the instances table for waiting_for > 0 and matching message_queue entries.
    """

    def __init__(
        self,
        instance_repository: SQLModelInstanceRepository,
        message_queue_repository: MessageQueueRepository,
        completion_callback: Callable[[str, str], Awaitable[None]] | None = None,
        # completion_callback(parent_id, terminal_status) called on correlation.complete
    ) -> None:
        self._instance_repo = instance_repository
        self._message_queue_repo = message_queue_repository
        self._completion_callback = completion_callback
        self._pending: dict[str, ParentCorrelation] = {}  # parent_id → ParentCorrelation
        self._locks: dict[str, asyncio.Lock] = {}  # parent_id → Lock (Fix C4)

    def _get_lock(self, parent_id: str) -> asyncio.Lock:
        """Get or create a per-parent lock for serialized access (Fix C4)."""
        if parent_id not in self._locks:
            self._locks[parent_id] = asyncio.Lock()
        return self._locks[parent_id]

    async def register_message_send(
        self, parent_id: str, child_id: str, message_id: str
    ) -> None:
        """Called when parent sends a message to child (mirrors waiting_for++).
        
        Hook point: tools/instance.py:565-583, alongside the SQL increment.
        """
        async with self._get_lock(parent_id):
            if parent_id not in self._pending:
                self._pending[parent_id] = ParentCorrelation(
                    parent_id=parent_id, pending={}
                )
            key = f"{child_id}:{message_id}"
            self._pending[parent_id].pending[key] = PendingResponse(
                parent_id=parent_id, child_id=child_id,
                message_id=message_id, created_at=time.time(), status="pending",
            )

    async def resolve_response(
        self, parent_id: str, child_id: str, message_id: str, status: str = "responded"
    ) -> bool:
        """Called when child's response is processed (mirrors waiting_for--).
        
        Returns True if this resolution triggered correlation.complete.
        
        Hook point: child_reports.py:424-438, alongside the SQL decrement.
        Also called from error_reporting.py:197-211 for error responses.
        """
        async with self._get_lock(parent_id):
            parent_state = self._pending.get(parent_id)
            if parent_state is None:
                return False  # Parent not tracked (wasn't waiting)

            key = f"{child_id}:{message_id}"
            entry = parent_state.pending.get(key)
            if entry is None:
                logger.debug(
                    f"CM: resolve_response for untracked key {key} "
                    f"(parent={parent_id[:8]}...)"
                )
                return False

            # Fix N2: Set had_error BEFORE popping — so _determine_terminal_status
            # can read it after the pending set is empty.
            if status in ("error", "failed"):
                parent_state.had_error = True

            entry.status = status
            parent_state.pending.pop(key, None)

            # Check if all responses resolved
            if parent_state.is_complete:
                terminal_status = self._determine_terminal_status(parent_state)
                # Clean up
                del self._pending[parent_id]

                # Emit via callback (Fix C2/C3 — direct call, not EventBus queue)
                if self._completion_callback:
                    await self._completion_callback(parent_id, terminal_status)
                return True

            return False

    def get_pending_count(self, parent_id: str) -> int:
        """Number of outstanding responses — should match DB waiting_for."""
        parent_state = self._pending.get(parent_id)
        return parent_state.pending_count if parent_state else 0

    def is_complete(self, parent_id: str) -> bool:
        """True if all sent messages have received responses."""
        parent_state = self._pending.get(parent_id)
        return parent_state.is_complete if parent_state else True

    async def rebuild_from_db(self) -> None:
        """Reconstruct pending state from DB after daemon restart."""
        ...

    def _determine_terminal_status(self, parent_state: ParentCorrelation) -> str:
        """Determine 'completed' vs 'error' from response history.
        
        Conservative: any error response → parent 'error'.
        Reads parent_state.had_error which is set before the last entry is popped.
        """
        # Fix N2: had_error is set during resolve_response before popping,
        # so it survives even when pending set is empty at this point.
        if parent_state.had_error:
            return "error"
        return "completed"
```

### Event Delivery: Direct Callback (Fix C2 + C3)

**Problem with EventBus for correlation events:**
- C2: `EventBus.create_event()` ALWAYS persists to DB (`event_bus.py:174-181`). No ephemeral mode.
- C3: `EventBus._broadcast_to_global()` uses `put_nowait()` (`event_bus.py:347`) which silently drops events on queue full (`except QueueFull: logger.warning(...)`).

**Solution: Direct callback, not EventBus queue.**

```python
# CorrelationManager is initialized with a completion callback:
correlation_manager = CorrelationManager(
    instance_repository=...,
    message_queue_repository=...,
    completion_callback=handle_correlation_complete,  # async function
)

# When correlation.complete fires, it calls the callback directly:
async def handle_correlation_complete(parent_id: str, terminal_status: str) -> None:
    """Called synchronously within CM's lock-protected resolve_response."""
    # Phase 1 (shadow mode): just log
    logger.info(f"CM: correlation.complete(parent={parent_id[:8]}..., status={terminal_status})")
    # Phase 2: this is where JobFeedbackObserver logic moves
```

**Why callback instead of EventBus:**
1. No DB persistence needed (in-memory state, rebuildable on restart)
2. No queue overflow risk (direct function call within async context)
3. No event ordering issues (called within the per-parent Lock)
4. Simpler than modifying EventBus to support ephemeral mode
5. CorrelationManager can still subscribe to EventBus for *inbound* lifecycle events — it just doesn't use EventBus for *outbound* correlation events

### Inbound Event Subscription (Unchanged — Still Uses EventBus)

CorrelationManager subscribes to EventBus for **inbound** `instance_lifecycle` events to learn about child completions/errors. This is safe because:
- Inbound events are consumed (not produced) — no DB write issue
- The existing queue (maxsize=1000) is adequate for inbound — even if dropped, CM can rebuild from DB
- The CM's own event loop processes inbound events to *validate* its state

```python
async def start(self) -> None:
    """Subscribe to EventBus for inbound lifecycle events (shadow validation)."""
    self._queue = self._event_bus.subscribe_all(
        "correlation_manager", maxsize=500  # smaller queue, OK if dropped (rebuild)
    )
    await self.rebuild_from_db()
    self._event_task = asyncio.create_task(self._event_loop())
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create CorrelationManager class | Implement with message-response data model, per-parent locks, direct callback | `daemon/services/correlation_manager.py` (new) |
| 2 | Implement `register_message_send` | Called from `send_message` path alongside `waiting_for++` SQL | `daemon/tools/instance.py:565-583`, `daemon/services/correlation_manager.py` |
| 3 | Implement `resolve_response` | Called from child completion/error paths alongside `waiting_for--` SQL | `daemon/services/child_reports.py:424-438`, `daemon/services/error_reporting.py:197-211`, `daemon/services/correlation_manager.py` |
| 4 | Implement `rebuild_from_db` | Query `instances WHERE waiting_for > 0` + cross-reference `message_queue` for pending messages to children | `daemon/services/correlation_manager.py` |
| 5 | Implement per-parent `asyncio.Lock` | Serialize all register/resolve calls for the same parent_id (Fix C4) | `daemon/services/correlation_manager.py` |
| 6 | Implement direct callback for `correlation.complete` | Callback function called within Lock scope; no EventBus publish for outbound | `daemon/services/correlation_manager.py` |
| 7 | Implement shadow mode validation | After each register/resolve, compare `CM.get_pending_count(parent)` with `instance.waiting_for`; log mismatches | `daemon/services/correlation_manager.py` |
| 8 | Wire `register_message_send` into `send_message` | Add hook alongside the SQL increment at `instance.py:565` | `daemon/tools/instance.py:565-583` |
| 9 | Wire `resolve_response` into child completion | Add hook alongside the SQL decrement at `child_reports.py:424` and `error_reporting.py:197` | `daemon/services/child_reports.py`, `daemon/services/error_reporting.py` |
| 10 | Wire startup/shutdown | Initialize in `manager.py:initialize()`, call `start()`/`stop()` | `daemon/manager.py` |
| 11 | Add rate-limited mismatch logging | Cap at 100/min; summary logging every 5 min after that | `daemon/services/correlation_manager.py` |
| 12 | Write unit tests | Test register/resolve, terminal detection, lock serialization, rebuild, shadow comparison | `tests/test_correlation_manager.py` (new) |
| 13 | Write integration test (shadow mode) | Full daemon, trigger send_message → child completion, verify log shows match | `tests/test_correlation_shadow.py` (new) |

## Key Design Decisions

### 1. Message-Response Correlation, NOT Child Existence (Fix C1)
**Decision**: CorrelationManager tracks `(parent, child, message_id)` triples — one entry per `send_message` that expects a response.
**Rationale**:
- `waiting_for` tracks pending message responses, NOT child existence
- `spawn_instance` does NOT increment `waiting_for` — only `send_message` does
- A parent can spawn children and complete independently — it only waits when it sends messages expecting responses
- The correlation key `f"{child_id}:{message_id}"` matches each `send_message` to its specific response
- A parent can send multiple messages to the same child — each needs its own correlation entry

**How CM knows it's waiting for a specific response:**
- `register_message_send(parent, child, message_id)` is called at the same point as the SQL increment
- `resolve_response(parent, child, message_id)` is called at the same point as the SQL decrement
- The `message_id` creates the 1:1 correlation between send and response

### 2. In-Memory State Only (No DB Table for Correlation)
**Decision**: CorrelationManager state is purely in-memory.
**Rationale**:
- Durable state already exists: `waiting_for` in `instances` table + messages in `message_queue` table
- Rebuild is O(pending messages), cheap at scale (max 100 instances, 50 children/parent, ~1 msg/sec)
- Mirrors the proven `_graph_tasks` pattern: volatile cache, rebuild from DB on restart

### 3. Direct Callback for Outbound Events (Fix C2 + C3)
**Decision**: `correlation.complete` is delivered via direct async callback, NOT through EventBus.
**Rationale**:
- EventBus `create_event()` ALWAYS persists to DB (`event_bus.py:174-181`) — no ephemeral mode (C2)
- EventBus `put_nowait()` silently drops events on queue full (`event_bus.py:347-351`) — a dropped `correlation.complete` means a parent stuck in PROCESSING forever (C3)
- Direct callback has no persistence overhead, no queue overflow risk, and preserves ordering within the per-parent Lock
- CM still subscribes to EventBus for *inbound* lifecycle events (shadow validation only)

### 4. Per-Parent `asyncio.Lock` for Concurrency (Fix C4)
**Decision**: Each `parent_id` gets its own `asyncio.Lock`. All `register_message_send` and `resolve_response` calls for the same parent are serialized.
**Rationale**:
- `check_parent_completion()` callers come from 4 concurrent contexts (WorkerPool threads, JobQueue tasks, resume path) — NOT within the EventBus loop
- Without locks, two concurrent `resolve_response` calls for the same parent could both see `pending_count > 0` and both decide "not complete" — then neither fires `correlation.complete`
- Per-parent locks are fine-grained: different parents process in parallel, same parent serializes
- At ~1 msg/sec volume, lock contention is negligible

**Alternatives considered:**
- *(B) Route all checks through EventBus as events*: Rejected — would require every cascade caller to publish to EventBus, massive refactor, and still has C2/C3 issues
- *(C) DB-level locking (`SELECT ... FOR UPDATE`)*: Rejected — not portable (SQLite doesn't support `FOR UPDATE`), adds DB round-trips, and the decision is in-memory

### 5. Shadow Mode with Rate-Limited Logging
**Decision**: Phase 1 runs shadow mode — CM tracks state and compares with `waiting_for`, but the callback only logs.
**Rationale**:
- Validates that CM's message-response tracking exactly matches `waiting_for` counter behavior
- Can run for days/weeks gathering confidence data
- Rate limiting prevents log storms during edge cases

### 6. Rebuild Queries Real message_ids from `message_queue` (Fix C1 + N1)
**Decision**: On restart, rebuild by querying `instances WHERE waiting_for > 0` to find waiting parents, then cross-referencing `message_queue` for the actual `(instance_id, message_id)` pairs of messages sent to children. Uses real UUIDs as correlation keys so `resolve_response` can match them.
**Rationale**:
- `waiting_for > 0` tells us WHICH parents are waiting
- The `message_queue` table contains the real `message_id` UUIDs for messages currently in flight to children
- Using real UUIDs ensures that when a child eventually completes and `resolve_response(child_id, message_id)` is called, the key will match
- Placeholder keys (e.g. `rebuild_0`) would NEVER match real UUIDs, causing CM state to permanently diverge after any restart

## Rebuild Logic (Fix C1 + N1)

```python
async def rebuild_from_db(self) -> None:
    """Reconstruct pending state from DB after daemon restart.

    Queries message_queue for real (instance_id, message_id) pairs where
    the message is in-flight (READY/PROCESSING/RETRYING) and the target
    instance is a child of a parent with waiting_for > 0.

    Uses real message_id UUIDs as correlation keys so that resolve_response()
    can match them when children complete.
    """
    # Step 1: Find all parents with waiting_for > 0
    waiting_parents = self._instance_repository.get_all_with_waiting_for()

    for parent in waiting_parents:
        parent_id = parent.instance_id

        # Step 2: Get children of this parent
        children = self._instance_repository.get_children(parent_id)
        child_ids = [c.instance_id for c in children]

        if not child_ids:
            logger.warning(
                f"CM rebuild: parent {parent_id[:8]}... has waiting_for="
                f"{parent.waiting_for} but no children found — skipping"
            )
            continue

        # Step 3: Query message_queue for real pending messages to these children
        # These are the actual (child_id, message_id) pairs the parent is waiting for.
        pending_messages = self._message_queue_repo.get_pending_for_instances(child_ids)
        # Returns: list of (instance_id, message_id) where status IN (READY, PROCESSING, RETRYING)

        parent_state = ParentCorrelation(parent_id=parent_id, pending={})

        for child_id, message_id in pending_messages:
            key = f"{child_id}:{message_id}"
            parent_state.pending[key] = PendingResponse(
                parent_id=parent_id,
                child_id=child_id,
                message_id=message_id,
                created_at=time.time(),
                status="pending",
            )

        # Step 4: Handle count mismatch — waiting_for may not exactly match
        # the number of pending messages in the queue (messages may have been
        # processed but completion not yet decremented, or vice versa).
        expected_count = parent.waiting_for or 0
        actual_count = parent_state.pending_count
        if actual_count != expected_count:
            logger.warning(
                f"CM rebuild: parent {parent_id[:8]}... waiting_for={expected_count} "
                f"but found {actual_count} pending messages in queue"
            )
            # If we found fewer messages than waiting_for, the extra waiting_for
            # count will be resolved naturally — when a child completes, CM's
            # resolve_response will simply not find a matching key and log a debug.
            # If we found more, the extras will be resolved as children complete.

        self._pending[parent_id] = parent_state

    logger.info(
        f"CorrelationManager rebuilt: {len(self._pending)} parents tracking "
        f"{sum(p.pending_count for p in self._pending.values())} pending responses"
    )
```

### New Repository Methods Needed

```python
# daemon/repositories/instance/repository.py
def get_all_with_waiting_for(self) -> list[Instance]:
    """Get all instances where waiting_for > 0."""
    with SQLModelSession(self.engine) as session:
        return session.exec(
            select(Instance).where(Instance.waiting_for > 0)
        ).all()

# daemon/repositories/message_queue/repository.py (or existing message queue repo)
def get_pending_for_instances(
    self, instance_ids: list[str]
) -> list[tuple[str, str]]:
    """Get (instance_id, message_id) pairs for pending messages to the given instances.

    Used by CorrelationManager.rebuild_from_db() to reconstruct correlation
    keys with real message_id UUIDs (Fix N1).
    """
    with SQLModelSession(self.engine) as session:
        rows = session.exec(
            select(MessageQueue.instance_id, MessageQueue.message_id)
            .where(MessageQueue.instance_id.in_(instance_ids))
            .where(MessageQueue.status.in_([
                MessageStatus.READY.value,
                MessageStatus.PROCESSING.value,
                MessageStatus.RETRYING.value,
            ]))
        ).all()
        return [(row[0], row[1]) for row in rows]
```

## Shadow Mode Validation Logic (Fix C1)

```python
async def _validate_shadow_mode(self, parent_id: str) -> None:
    """Compare CM pending count with waiting_for counter."""
    cm_count = self.get_pending_count(parent_id)

    parent = self._instance_repository.get(parent_id)
    db_count = (parent.waiting_for or 0) if parent else 0

    if cm_count != db_count:
        self._mismatch_count += 1
        if self._should_log_mismatch():
            logger.warning(
                f"SHADOW MISMATCH: parent {parent_id[:8]}... "
                f"CM pending={cm_count}, DB waiting_for={db_count}"
            )
    else:
        self._match_count += 1
        if self._should_log_match():
            logger.debug(f"SHADOW MATCH: parent {parent_id[:8]}... both agree: pending={cm_count}")
```

## Key Files

| File | Purpose |
|------|---------|
| `daemon/services/correlation_manager.py` (new) | CorrelationManager implementation |
| `daemon/services/event_bus.py:286-355` | `subscribe_all` — for inbound lifecycle events only |
| `daemon/services/event_bus.py:155-191` | `create_event` — shows DB persistence is always-on (C2) |
| `daemon/services/event_bus.py:345-351` | `put_nowait` + `QueueFull` — shows silent drop (C3) |
| `daemon/repositories/instance/repository.py` | `get_all_with_waiting_for` (new) — rebuild query |
| `daemon/repositories/instance/models.py:65` | `waiting_for` field definition |
| `daemon/manager.py:1240` | `initialize()` — wire startup/shutdown |
| `daemon/tools/instance.py:554-583` | `send_message` — `register_message_send` hook point |
| `daemon/services/child_reports.py:424-438` | `waiting_for` decrement — `resolve_response` hook point |
| `daemon/services/error_reporting.py:197-211` | Error path decrement — `resolve_response` hook point |

## Constraints
- Must NOT affect runtime behavior (shadow mode only)
- Must NOT modify existing event publishing code
- Must support rebuild from DB after crash/restart — uses real message_id UUIDs from message_queue (Fix N1)
- EventBus is async-only — CorrelationManager must be async-compatible
- Max 100 instances, 50 children/parent — no performance optimization needed beyond O(n) rebuild
- `waiting_for` increment/decrement SQL must remain (CM hooks alongside, not replacing)
- Per-parent locks must not deadlock (all locks are per-parent, no cross-parent lock acquisition)
- **Constraint (N3):** All CorrelationManager operations (`register_message_send`, `resolve_response`, `is_complete`) MUST execute on the main asyncio event loop. WorkerPool thread callers must marshal through `MainLoopBridge.run_async()` before calling CM methods. This is required because `asyncio.Lock` only works within the event loop that created it — acquiring a Lock from a different thread causes undefined behavior.

## Verification Strategy

1. **Unit test — register/resolve**: Register 3 message sends for parent P, resolve 2, verify `pending_count=1`; resolve last, verify `correlation.complete` callback fires
2. **Unit test — lock serialization**: Spawn 2 concurrent `resolve_response` calls for same parent; verify they serialize correctly (no race on the terminal check)
3. **Unit test — rebuild with real UUIDs (Fix N1)**: Seed DB with `waiting_for=2` for parent P and 2 real messages in `message_queue` for children of P; call `rebuild_from_db()`; verify `pending_count=2`; then call `resolve_response` with a real `message_id` from those 2 messages — verify it resolves (key matches, no orphaned placeholder)
4. **Unit test — terminal status with had_error (Fix N2)**: Register 2 sends for parent P; resolve 1st with `status="responded"`, resolve 2nd with `status="error"`; verify `correlation.complete` callback fires with `terminal_status="error"` (conservative error propagation)
5. **Unit test — shadow comparison**: Register 2 sends (CM pending=2), set DB `waiting_for=2`, resolve 1 (CM pending=1), verify match; resolve 2nd (CM pending=0), verify match
6. **Integration test — shadow mode**: Run full daemon, trigger `send_message(parent→child)`, verify CM registers; complete child, verify CM resolves + shadow matches
7. **Integration test — multiple messages to same child**: Parent sends 2 messages to same child; verify 2 correlation entries; child completes both; verify CM fires `correlation.complete` only after both resolve
8. **Log review**: After 24h soak, grep for `SHADOW MISMATCH` — should be zero

## Rollback Plan

CorrelationManager is additive — removal is safe:
1. Remove `correlation_manager.py`
2. Remove `register_message_send` hooks from `tools/instance.py`
3. Remove `resolve_response` hooks from `child_reports.py` and `error_reporting.py`
4. Remove initialization calls from `manager.py`
5. Remove tests
6. System reverts to `waiting_for`-only behavior with zero side-effects

No DB migration needed (no schema changes). No data loss (in-memory only).

## Deliverables
- [ ] `CorrelationManager` class with message-response data model
- [ ] `ParentCorrelation.had_error` field for conservative error propagation (Fix N2)
- [ ] `register_message_send` hooked into `send_message` path
- [ ] `resolve_response` sets `had_error` before pop, passes `parent_state` to terminal status (Fix N2)
- [ ] `_determine_terminal_status` reads `parent_state.had_error` (Fix N2)
- [ ] `rebuild_from_db()` queries real `message_id` UUIDs from `message_queue` (Fix N1)
- [ ] Per-parent `asyncio.Lock` for concurrency safety
- [ ] Direct callback for `correlation.complete` (not EventBus)
- [ ] All CM callers marshal through main event loop (N3 constraint documented)
- [ ] Shadow mode validation comparing CM pending count with `waiting_for`
- [ ] Wired into daemon startup/shutdown lifecycle
- [ ] Rate-limited mismatch logging
- [ ] Unit tests: register/resolve, lock serialization, rebuild with real UUIDs, had_error terminal status, shadow comparison
- [ ] Integration test for shadow mode operation
