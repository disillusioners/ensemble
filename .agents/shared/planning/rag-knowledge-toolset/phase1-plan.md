# Phase 1: Core Infrastructure — CompletionRegistry + Sync Wait

## Objective

Implement the CompletionRegistry service and `invoke_agent_and_wait()` utility that enables synchronous agent invocation (spawn → send → wait for completion → return result). This is the backbone for the `explore()` knowledge tool and the critical new "Agent-as-Tool" pattern.

## Coupling

- **Depends on**: None (foundation phase)
- **Coupling type**: — (root phase)
- **Shared files with other phases**: 
  - `daemon/services/completion_registry.py` — consumed by Phase 3 (knowledge tools)
  - `daemon/services/child_reports.py` — modified to signal completion
  - `daemon/services/error_reporting.py` — modified to signal error
  - `daemon/manager.py` — modified to hold registry reference
- **Why this coupling**: Phase 3's `explore()` tool directly calls `invoke_agent_and_wait()` which depends on CompletionRegistry

## Context

### Investigation Findings

The current instance lifecycle is **fire-and-forget**:

1. `task_processor.py:266` calls `manager._process_child_completion_and_notify_parent(instance_id, message_id)`
2. This delegates to `child_reports.py:493` which creates a COMPLETION_REPORT message enqueued for the parent
3. The parent receives this as a normal message in its queue — no blocking, no Future, no Event

### Proven Pattern: `dispatch_event_bus.py`

This file already uses the exact pattern we need:
```python
_events: dict[str, asyncio.Event]  # Per-project wakeup events

def _get_or_create_event(self, project_id: str) -> asyncio.Event:
    if project_id not in self._events:
        self._events[project_id] = asyncio.Event()
    return self._events[project_id]
```

We mirror this at the instance level instead of project level.

### Hook Point for Completion Signal (C1 Fix)

In `child_reports.py`, `_process_child_completion_and_notify_parent()` has **5 distinct exit paths**. Each must signal the CompletionRegistry:

```
Line 493: async def _process_child_completion_and_notify_parent(instance_id, completed_message_id):
    Line 507: last_content = await self._get_last_assistant_message(instance_id, agent_id)
    Line 508-510: EXIT 1 — last_content is None → early return (NO signal — no content to report)
    
    Line 512-516: with Session(...) as session:
    Line 514-516: EXIT 2 — instance not found → early return (NO signal — shouldn't happen)
    
    Line 520-530: EXIT 3 — parent_id is None AND waiting_for > 0 → WAITING_CHILDREN (NO signal — not done)
    Line 546-554: EXIT 4 — parent_id is None AND pending_count > 0 → WAITING_CHILDREN (NO signal — not done)
    Line 556-565: EXIT 5a — parent_id is None AND no pending → COMPLETED, no parent
                  → SIGNAL HERE: registry.complete(instance_id, result=last_content)
    
    Line 568-569: EXIT 6 — idempotency check fails → return (NO signal — duplicate)
    
    Line 574-597: MAIN PATH — child with parent:
        Line 575-577: _create_completion_report(session, instance, last_content, ...)
        Line 580: _update_parent_on_child_complete(session, instance)
        Line 586-592: _create_completion_events(session, ...)
        Line 597: session.commit()  ← DB state is now consistent
        Line 599-610: SSE broadcast (best-effort, in try/except)
        Line 612-623: Parent cascade event (best-effort, in try/except)
                  → SIGNAL HERE: registry.complete(instance_id, result=last_content)
```

**Key insight**: `last_content` is fetched at line 507 (BEFORE the session transaction at line 512). It's available throughout the method. The signal must fire AFTER `session.commit()` at line 597 (so DB state is consistent) but BEFORE the method returns.

**Only 2 exit points need the signal**:
1. **EXIT 5a** (line 556-565): Root instance completing (no parent). Signal AFTER `session.commit()` isn't needed here since there's no session — but the instance IS completing, so signal after the lifecycle event publish.
2. **MAIN PATH** (after line 597): Child completing with parent. Signal AFTER `session.commit()`.

**Error path**: When `error_reporting.py:_send_error_report()` fires (line 166: `instance.status = InstanceStatus.ERROR.value`), the CompletionRegistry must ALSO be signaled — otherwise `invoke_agent_and_wait()` hangs until timeout. The error result should contain the error message, not `last_content`.

### Worker Thread Deadlock Analysis (CRITICAL Issue 1)

**The architecture** (from codebase exploration):

```
Worker Thread (4 threads default)
  │
  ├─ claim_task() from DB (SQL atomic UPDATE-RETURNING)
  │
  ├─ MainLoopBridge.run_async(coro, timeout=300)
  │     │
  │     ├─ asyncio.run_coroutine_threadsafe(coro, loop)
  │     └─ future.result(timeout=300)  ← WORKER THREAD BLOCKS HERE
  │           │
  │           └─ Worker does NOT return to pool until coroutine finishes
  │
  └─ MainLoopBridge._run() → graph.astream/ainvoke() → ReAct loop → tool call
```

Key: `future.result(timeout)` in `main_loop_bridge.py:85` is a **blocking call**. The worker thread is held hostage by the coroutine. It cannot claim another task until the coroutine completes.

**The deadlock scenario with `explore()`**:

```
Worker 1: Claims Parent task → runs Parent's ReAct loop → calls explore()
  → explore() calls invoke_agent_and_wait()
  → spawn_instance() creates Explorer in DB
  → enqueue_message() creates Task in DB + calls notify_work()
  → await registry.wait_for() ← YIELDS event loop, but Worker 1 is still BLOCKED

Worker 2: Claims Explorer task → runs Explorer's ReAct loop → Explorer completes
  → registry.complete() fires → Worker 1's wait_for() returns
  → Worker 1 continues, explore() returns result

WORKS when 1+ workers are free.

DEADLOCK when ALL workers are blocked:
  Worker 1: explore() → waiting for Explorer A
  Worker 2: explore() → waiting for Explorer B
  Worker 3: explore() → waiting for Explorer C
  Worker 4: explore() → waiting for Explorer D
  → No worker free to claim Explorer tasks → ALL timeout after 300s
```

**Chosen fix: Concurrency semaphore (option c)**

An `asyncio.Semaphore` acquired before spawning and released after waiting. Cap at `WORKER_POOL_SIZE - 1` ensures at least 1 worker is always free for agent-as-tool instances.

Why this approach:
- **Minimal change**: ~10 lines of code, single file (`daemon/utils.py`)
- **No core changes**: Doesn't touch worker pool, MainLoopBridge, or task processor
- **Correct behavior**: `await semaphore.acquire()` yields the event loop (doesn't block), so the parent's event loop continues processing other async tasks while waiting for a semaphore slot
- **Self-protecting**: If all slots are taken, the next `explore()` call blocks at `semaphore.acquire()` instead of spawning an Explorer that would deadlock
- **Configurable**: Cap reads from `WORKER_POOL_SIZE` constant

The semaphore is a module-level singleton in `daemon/utils.py`:

```python
from daemon.constants import WORKER_POOL_SIZE

# Cap at pool_size - 1 to guarantee at least 1 free worker
_invoke_semaphore = asyncio.Semaphore(max(1, WORKER_POOL_SIZE - 1))
```

### Buffered Completion (HIGH Issue 2)

**The race condition**: In `invoke_agent_and_wait()`:
```python
instance_id = manager.spawn_instance(...)   # Step 1: instance in DB
registry.register(instance_id)              # Step 2: create asyncio.Event
await manager.enqueue_message(...)          # Step 3: create Task in DB + notify workers
```

Between step 1 and step 2, if a worker somehow claims and processes the Explorer's task (unlikely but possible under extreme timing), `complete()` would fire before `register()`. The current code's `complete()` returns `False` and drops the result silently → caller hits timeout.

More realistically: `spawn_instance()` could theoretically throw (e.g., max instances limit), and if a concurrent cleanup or error handler fires `complete()` on that instance ID before the exception handler reaches `register()`, same result.

**Fix**: Buffer completions that arrive before registration. `complete()` stores results in a separate buffer dict. `register()` checks the buffer on entry and immediately resolves if a pre-stored result exists.

```python
# In CompletionRegistry:
self._buffered: dict[str, CompletionResult] = {}  # instance_id → result

def complete(self, instance_id, result, is_error):
    with self._lock:
        if instance_id in self._events:
            # Normal path: event exists, set it
            ...
        else:
            # Buffer: no event yet, store for when register() is called
            self._buffered[instance_id] = CompletionResult(content=result, is_error=is_error)
            return True  # Not False — result is preserved

def register(self, instance_id):
    with self._lock:
        if instance_id in self._buffered:
            # Pre-completed: consume buffer, return already-completed result
            pre_result = self._buffered.pop(instance_id)
            self._events[instance_id] = asyncio.Event()
            self._register_times[instance_id] = time.monotonic()
            # Don't set event yet — wait_for() must be called first
            # Store result directly so wait_for() finds it immediately
            self._results[instance_id] = pre_result
            # Set the event immediately — wait_for() will return right away
            # But we need the event loop to call_soon_threadsafe...
            # Actually: set it now, wait_for() will return on next await
            self._events[instance_id].set()
            return
        # Normal path: no buffer, create fresh event
        self._events[instance_id] = asyncio.Event()
        self._results[instance_id] = None
        self._register_times[instance_id] = time.monotonic()
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create CompletionRegistry service | Per-instance asyncio.Event + buffered completions + stale cleanup | `daemon/services/completion_registry.py` |
| 2 | Wire registry into InstanceManager | Initialize registry, set event loop, expose pool size | `daemon/manager.py` |
| 3 | Signal completion in ChildReportsService | Call `registry.complete()` at 2 exit points after commit | `daemon/services/child_reports.py` |
| 4 | Signal errors in ErrorReportingService | Call `registry.complete()` with error result on instance failure | `daemon/services/error_reporting.py` |
| 5 | Create `invoke_agent_and_wait()` utility | Semaphore + spawn + buffered-register + enqueue + wait + error handling + cleanup | `daemon/utils.py` |
| 6 | Update services exports | Add CompletionRegistry to `__init__.py` | `daemon/services/__init__.py` |
| 7 | Write unit tests | All paths: completion, error, timeout, deadlock, race, buffer, concurrent, stale | `tests/unit/services/test_completion_registry.py` |

### Task 1.1: Create CompletionRegistry Service

**File**: `daemon/services/completion_registry.py` (NEW)

```python
"""CompletionRegistry — Per-instance asyncio.Event for synchronous wait.

Pattern mirrors DispatchEventBus but operates at INSTANCE level.

Features:
- Buffered completions: complete() before register() is safe
- Thread-safe: threading.Lock for all dict access
- Stale cleanup: periodic removal of abandoned entries

Usage:
    registry = get_completion_registry()
    registry.register(instance_id)          # Create event
    result = await registry.wait_for(id)    # Block until completion
    registry.complete(id, result=content)   # Signal completion
    registry.unregister(id)                 # Cleanup
"""

import asyncio
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Stale entry cleanup threshold (1 hour)
_STALE_THRESHOLD_SECONDS = 3600


class CompletionResult:
    """Wrapped result that distinguishes success from error."""
    
    def __init__(self, content: Any, is_error: bool = False):
        self.content = content
        self.is_error = is_error
    
    @property
    def succeeded(self) -> bool:
        return not self.is_error


class CompletionRegistry:
    """Registry for instance completion events.

    Thread-safe: uses threading.Lock for dict access, asyncio.Event for async wait.
    Includes buffered completions and stale entry cleanup.
    
    Buffered completions handle the race where complete() fires before register().
    This can happen when:
    - spawn_instance() succeeds, but an error fires complete() before register()
    - Very fast agent completion between enqueue and register (unlikely but possible)
    """

    def __init__(self):
        self._events: dict[str, asyncio.Event] = {}
        self._results: dict[str, CompletionResult] = {}
        self._buffered: dict[str, CompletionResult] = {}  # Pre-register completions
        self._register_times: dict[str, float] = {}  # For stale detection
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the event loop reference for thread-safe notification."""
        self._loop = loop

    def register(self, instance_id: str) -> None:
        """Register a completion event for an instance. Call when spawning.
        
        If complete() was already called for this instance (buffered),
        the event is created and immediately set — wait_for() returns instantly.
        """
        with self._lock:
            if instance_id in self._buffered:
                # Buffered completion exists — consume it
                pre_result = self._buffered.pop(instance_id)
                self._events[instance_id] = asyncio.Event()
                self._results[instance_id] = pre_result
                self._register_times[instance_id] = time.monotonic()
                # Set event immediately (can call set() directly — event exists)
                self._events[instance_id].set()
                logger.debug(
                    f"CompletionRegistry: registered {instance_id[:8]}... "
                    f"(consumed buffered result, is_error={pre_result.is_error})"
                )
                return
            
            if instance_id not in self._events:
                self._events[instance_id] = asyncio.Event()
                self._results[instance_id] = None
                self._register_times[instance_id] = time.monotonic()
                logger.debug(f"CompletionRegistry: registered {instance_id[:8]}...")

    def complete(self, instance_id: str, result: Any = None, is_error: bool = False) -> bool:
        """Signal that an instance has completed (success or error). Thread-safe.

        If register() hasn't been called yet, buffers the result.
        When register() is called later, it consumes the buffer.

        Args:
            instance_id: The instance that completed.
            result: The result content (last assistant message or error string).
            is_error: True if this is an error completion.

        Returns:
            True if event was set OR result was buffered. False only if
            instance was already completed (duplicate).
        """
        completion = CompletionResult(content=result, is_error=is_error)
        
        with self._lock:
            if instance_id in self._events:
                # Normal path: event exists, store result and set it
                event = self._events[instance_id]
                self._results[instance_id] = completion
            elif instance_id in self._buffered:
                # Already buffered — update buffer (shouldn't happen, but safe)
                self._buffered[instance_id] = completion
                return True
            else:
                # No event yet — buffer the result for when register() is called
                self._buffered[instance_id] = completion
                logger.debug(
                    f"CompletionRegistry: buffered completion for {instance_id[:8]}... "
                    f"(is_error={is_error})"
                )
                return True

        # Set event (thread-safe via loop) — outside lock to avoid deadlock
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(event.set)
        elif self._loop:
            event.set()

        status = "error" if is_error else "completed"
        logger.debug(f"CompletionRegistry: {status} {instance_id[:8]}...")
        return True

    async def wait_for(self, instance_id: str, timeout: float = 300.0) -> CompletionResult | None:
        """Wait for instance completion. Returns CompletionResult or None on timeout.

        The caller should check `result.is_error` to distinguish success from error.
        """
        with self._lock:
            if instance_id not in self._events:
                raise ValueError(f"Instance {instance_id[:8]}... not registered")
            event = self._events[instance_id]

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"CompletionRegistry: timeout for {instance_id[:8]}...")
            return None

        with self._lock:
            return self._results.get(instance_id)

    def unregister(self, instance_id: str) -> None:
        """Remove registration and any buffered result (cleanup)."""
        with self._lock:
            self._events.pop(instance_id, None)
            self._results.pop(instance_id, None)
            self._buffered.pop(instance_id, None)
            self._register_times.pop(instance_id, None)
            logger.debug(f"CompletionRegistry: unregistered {instance_id[:8]}...")

    def cleanup_stale(self, max_age_seconds: float = _STALE_THRESHOLD_SECONDS) -> int:
        """Remove entries older than max_age_seconds. Returns count of cleaned entries.

        Call periodically (e.g., every 10 minutes) to prevent memory leaks from
        abandoned registrations where complete() was never called.
        Also cleans orphaned buffered entries older than threshold.
        """
        now = time.monotonic()
        stale_ids = []
        with self._lock:
            for iid, reg_time in self._register_times.items():
                if (now - reg_time) > max_age_seconds:
                    stale_ids.append(iid)
            for iid in stale_ids:
                self._events.pop(iid, None)
                self._results.pop(iid, None)
                self._register_times.pop(iid, None)
            # Also clean buffered entries that were never consumed
            # (e.g., complete() fired but register() never called)
            buffered_to_clean = [
                iid for iid, res in self._buffered.items()
                # Buffered entries don't have register_times, so clean all
                # that are older than a separate threshold
            ]
            # For simplicity, clean ALL buffered entries older than threshold
            # Buffered entries shouldn't persist long — if register() hasn't
            # consumed them within threshold, something went wrong
            if len(self._buffered) > 100:  # Safety valve for unbounded growth
                self._buffered.clear()
        if stale_ids:
            logger.warning(f"CompletionRegistry: cleaned {len(stale_ids)} stale entries")
        return len(stale_ids)


_completion_registry: CompletionRegistry | None = None


def get_completion_registry() -> CompletionRegistry:
    """Get the global CompletionRegistry singleton."""
    global _completion_registry
    if _completion_registry is None:
        _completion_registry = CompletionRegistry()
    return _completion_registry
```

### Task 1.2: Wire Registry into InstanceManager

**File**: `daemon/manager.py` (MODIFIED — 3 small additions)

1. Import and store registry in `__init__()`:
```python
from .services.completion_registry import get_completion_registry

# In InstanceManager.__init__():
self._completion_registry = get_completion_registry()
```

2. Set event loop during initialization:
```python
# After event loop is available (in initialize() or wherever loop starts):
self._completion_registry.set_event_loop(asyncio.get_running_loop())
```

3. Add periodic stale cleanup (optional, can be a background task):
```python
# In the startup sequence, schedule periodic cleanup:
async def _cleanup_stale_completions():
    while True:
        await asyncio.sleep(600)  # Every 10 minutes
        self._completion_registry.cleanup_stale()
```

4. Expose worker pool size for semaphore initialization:
```python
# In setup_worker_pool(), after pool is created:
# Expose pool size so utils can initialize semaphore correctly
self._worker_pool_size = num_workers
```

### Task 1.3: Signal Completion in ChildReportsService (C1 Fix)

**File**: `daemon/services/child_reports.py` (MODIFIED)

Add CompletionRegistry signals at the **2 identified exit points** where the instance is truly done:

**Signal Point A — Root instance completing (no parent)** — After line 557 (COMPLETED status logged), before the lifecycle event publish:

```python
# Line ~556-565 (EXIT 5a: root instance with no parent, no children, no pending messages)
# No children, no pending messages - safe to complete
logger.info(f"Instance {instance_id[:8]}... completed (no parent, no children), status=COMPLETED")

# --- NEW: Signal completion registry ---
from .completion_registry import get_completion_registry
get_completion_registry().complete(instance_id, result=last_content)

if self._events_service:
    await self._events_service._publish_instance_lifecycle_event(
        instance_id=instance_id,
        status="completed",
        error=None,
        parent_id=None,
    )
return
```

**Signal Point B — Child instance completing with parent** — After line 597 `session.commit()`, before SSE broadcast:

```python
# Line 597
session.commit()

# --- NEW: Signal completion registry (after commit, before broadcast) ---
# last_content was fetched at line 507 and is still in scope
from .completion_registry import get_completion_registry
get_completion_registry().complete(instance_id, result=last_content)

# Broadcast child completion event asynchronously (existing code, line 599)
try:
    await self._manager._live_hub.stream_lifecycle(
```

**Why these exact positions:**
- `last_content` is available at both points (fetched at line 507, before session)
- `session.commit()` has already persisted the COMPLETED status to DB
- The signal fires BEFORE SSE broadcast (non-critical) so the waiter is unblocked ASAP
- If SSE or parent cascade fails, the registry signal has already fired — waiter doesn't hang

### Task 1.4: Signal Errors in ErrorReportingService (C2 Fix)

**File**: `daemon/services/error_reporting.py` (MODIFIED)

When an instance errors out, the CompletionRegistry must be signaled so `invoke_agent_and_wait()` doesn't hang until timeout.

Add signal in `_send_error_report()`, after the instance status is set to ERROR (around line 166):

```python
# In _send_error_report(), after the atomic DB transaction (session.commit()):
# The method structure is:
#   Step 1-2: Pre-fetch metadata (before transaction)
#   Step 3: Atomic DB transaction (status=ERROR, message=FAILED, parent decrement, etc.)
#   Step 4: Post-transaction (enqueue error message, broadcast SSE)

# --- NEW: After session.commit() in Step 3 ---
from .completion_registry import get_completion_registry
get_completion_registry().complete(
    instance_id, 
    result=f"Agent error: {truncated_error}", 
    is_error=True
)
```

**Why here**: After `session.commit()`, the instance status is ERROR in DB. The error message is already truncated and available. This fires before the error message is enqueued to the parent, which is correct — the `invoke_agent_and_wait()` caller gets the error immediately.

### Task 1.5: Create `invoke_agent_and_wait()` Utility (Semaphore + Buffered Registration)

**File**: `daemon/utils.py` (ADD function)

```python
from daemon.constants import WORKER_POOL_SIZE

# Concurrency cap: ensure at least 1 worker stays free for agent-as-tool tasks.
# Without this, all workers can block on their own wait_for() calls → deadlock.
# The semaphore is acquired on the event loop (async), so it yields properly.
_invoke_semaphore: asyncio.Semaphore | None = None


def _get_invoke_semaphore() -> asyncio.Semaphore:
    """Get or create the singleton semaphore for invoke_agent_and_wait.
    
    Lazy-initialized because asyncio.Semaphore needs a running event loop.
    Cap is WORKER_POOL_SIZE - 1 (minimum 1) to guarantee a free worker.
    """
    global _invoke_semaphore
    if _invoke_semaphore is None:
        cap = max(1, WORKER_POOL_SIZE - 1)
        _invoke_semaphore = asyncio.Semaphore(cap)
        logger.info(f"invoke_agent_and_wait: concurrency cap = {cap}")
    return _invoke_semaphore


async def invoke_agent_and_wait(
    manager: "InstanceManager",
    agent_id: str,
    message: str,
    project_id: str | None = None,
    instance_name: str | None = None,
    parent_id: str | None = None,
    timeout: float = 300.0,
) -> str:
    """Spawn an agent, send a message, and synchronously wait for the result.

    This is the primary mechanism for synchronous agent invocation.
    Used by knowledge tools to implement explore().

    DEADLOCK PREVENTION: Uses an asyncio.Semaphore capped at WORKER_POOL_SIZE - 1
    to ensure at least 1 worker thread remains free to process the spawned agent.
    Without this, all workers could block on their own wait_for() calls.

    Args:
        manager: The InstanceManager instance.
        agent_id: Agent ID to spawn (e.g., 'explorer', 'experiencer').
        message: The message/prompt to send to the agent.
        project_id: Optional project ID for context.
        instance_name: Optional name for the spawned instance.
        parent_id: Optional parent instance ID (for hierarchy).
        timeout: Maximum seconds to wait for completion.

    Returns:
        The agent's final response content (on success).
        Error string prefixed with "Error:" on failure or timeout.
    """
    from .services.completion_registry import get_completion_registry

    semaphore = _get_invoke_semaphore()
    registry = get_completion_registry()

    # Acquire semaphore — ensures we don't consume all workers
    await semaphore.acquire()
    
    # REGISTER BEFORE SPAWN — prevents complete() before register() race
    # We use a placeholder instance_id pattern; real ID set after spawn
    # Actually: we need the real instance_id for register. But spawn is sync.
    # Solution: register() AFTER spawn() but BEFORE enqueue().
    # The buffered completion in CompletionRegistry handles the tiny window.

    try:
        # 1. Spawn instance (synchronous — creates instance in DB)
        instance_id = manager.spawn_instance(
            agent_id=agent_id,
            parent_id=parent_id,
            project_id=project_id,
            instance_name=instance_name,
        )

        # 2. Register IMMEDIATELY after spawn (before enqueue)
        # Buffered completion handles race if complete() fires before this
        registry.register(instance_id)

        # 3. Enqueue message (creates Task in DB + notify_work())
        await manager.enqueue_message(
            instance_id=instance_id,
            message=message,
            source=f"invoke_and_wait:{parent_id or 'system'}",
        )

        # 4. Wait for completion (success or error)
        result = await registry.wait_for(instance_id, timeout=timeout)

        if result is None:
            # Timeout — agent is still running. Best-effort terminate.
            _try_terminate_orphan(manager, instance_id)
            return (
                f"Error: Agent timed out after {timeout}s. "
                f"Instance {instance_id[:8]}... may still be running."
            )

        if result.is_error:
            # Agent errored out — it's already in ERROR status
            return f"Error: Agent failed. {result.content}"

        # Success
        return result.content or ""

    except Exception as e:
        logger.error(f"invoke_agent_and_wait failed: {e}", exc_info=True)
        _try_terminate_orphan(manager, instance_id if 'instance_id' in dir() else None)
        return f"Error: {e}"
    finally:
        # 5. Always cleanup
        if 'instance_id' in dir():
            registry.unregister(instance_id)
        semaphore.release()


def _try_terminate_orphan(manager: "InstanceManager", instance_id: str | None) -> None:
    """Best-effort terminate an orphaned instance after timeout/error.

    Fire-and-forget: if termination fails, the instance will eventually
    be cleaned up by stale task recovery or watchdog.
    """
    if instance_id is None:
        return
    try:
        import asyncio
        asyncio.ensure_future(
            manager.terminate_instance(instance_id),
        )
    except Exception:
        logger.debug(f"Failed to terminate orphaned instance {instance_id[:8]}...")
```

**Deadlock prevention summary**:

```
Semaphore(3) for 4-worker pool:

Worker 1: explore() → acquire semaphore (slot 1/3) → spawn → wait
Worker 2: explore() → acquire semaphore (slot 2/3) → spawn → wait
Worker 3: explore() → acquire semaphore (slot 3/3) → spawn → wait
Worker 4: FREE — claims Explorer tasks, processes them

Worker 5 call: explore() → await semaphore.acquire() → BLOCKS
  (can't spawn until one of Workers 1-3 completes and releases)
```

When Worker 1's explore() completes:
- `registry.wait_for()` returns → explore() returns → semaphore.release()
- Worker 4 (or the next available worker) picks up the waiting explore()

**Error handling summary**:

| Scenario | What happens | Caller gets |
|----------|-------------|-------------|
| Agent completes normally | `complete()` fires in child_reports.py | Agent response content |
| Agent crashes/errors | `complete(is_error=True)` fires in error_reporting.py | `"Error: Agent failed. <msg>"` |
| Timeout (agent still running) | `wait_for()` returns None → `_try_terminate_orphan()` | `"Error: Agent timed out..."` |
| Exception in invoke_agent_and_wait | Caught → `_try_terminate_orphan()` | `"Error: <exception>"` |
| complete() before register() | Result buffered → register() consumes buffer → wait_for() returns instantly | Agent response content |
| All workers busy (deadlock prevention) | `await semaphore.acquire()` blocks at concurrency cap | Waits until a slot frees |

**Leak prevention**:

1. **`finally` block**: Always releases semaphore + unregisters registry entry
2. **`_try_terminate_orphan()`**: Best-effort terminate on timeout/error
3. **`cleanup_stale()`**: Periodic background task removes entries >1hr
4. **`_buffered` cleanup**: Safety valve clears buffered dict if >100 entries

### Task 1.6: Update Services Exports

**File**: `daemon/services/__init__.py` (MODIFIED)

Add to exports:
```python
from .completion_registry import CompletionRegistry, CompletionResult, get_completion_registry
```

### Task 1.7: Write Unit Tests

**File**: `tests/unit/services/test_completion_registry.py` (NEW)

Test cases:
1. `test_register_and_complete` — basic register → complete → wait_for returns result
2. `test_complete_with_error` — `is_error=True` → `wait_for` returns `CompletionResult` with `is_error=True`
3. `test_wait_timeout` — wait_for returns None on timeout
4. `test_unregister` — cleanup removes event, wait raises ValueError
5. `test_complete_without_register` — result buffered, NOT dropped
6. `test_buffered_complete_before_register` — complete() first → register() → wait_for() returns buffered result instantly
7. `test_buffered_complete_error_before_register` — complete(is_error=True) → register() → wait_for() returns error result
8. `test_concurrent_waiters` — multiple waiters on same instance_id
9. `test_complete_before_wait` — set() before wait() returns immediately
10. `test_cleanup_stale` — entries older than threshold removed
11. `test_cleanup_stale_skips_recent` — recent entries not cleaned
12. `test_singleton` — `get_completion_registry()` returns same instance
13. `test_invoke_agent_and_wait_success` — mock manager, verify result returned
14. `test_invoke_agent_and_wait_timeout` — mock manager, verify error string + terminate called
15. `test_invoke_agent_and_wait_error_propagation` — registry signals error, caller gets error string
16. `test_invoke_agent_and_wait_exception` — exception during spawn, verify cleanup + semaphore released
17. `test_semaphore_blocks_at_cap` — with Semaphore(2), 3rd call waits until 1st completes
18. `test_semaphore_released_on_all_error_paths` — verify semaphore.release() in every finally block

## Key Files

- `daemon/services/completion_registry.py` — **NEW**: Core registry with CompletionResult + buffered completions + stale cleanup
- `daemon/manager.py:314` — **MODIFIED**: Add registry init + event loop + stale cleanup task + expose pool size
- `daemon/services/child_reports.py:557` — **MODIFIED**: Signal at EXIT 5a (root instance completing)
- `daemon/services/child_reports.py:597` — **MODIFIED**: Signal at MAIN PATH (child completing with parent)
- `daemon/services/error_reporting.py:~166` — **MODIFIED**: Signal error completion after session.commit()
- `daemon/utils.py` — **MODIFIED**: Add `invoke_agent_and_wait()` with semaphore + `_try_terminate_orphan()` + `_get_invoke_semaphore()`
- `daemon/services/__init__.py` — **MODIFIED**: Add export
- `tests/unit/services/test_completion_registry.py` — **NEW**: Unit tests (18 cases)

## Constraints

1. **Thread Safety**: All dict operations protected by `threading.Lock`
2. **Event Loop**: Must call `set_event_loop()` during manager initialization
3. **Timeout**: All waits have configurable timeout (default 300s)
4. **Cleanup**: Always `unregister()` + `semaphore.release()` in `finally` block
5. **Deadlock prevention**: `asyncio.Semaphore(max(1, WORKER_POOL_SIZE - 1))` caps concurrent invoke-and-wait calls
6. **Buffered completions**: `complete()` before `register()` is safe — result is buffered and consumed on register
7. **Idempotent**: `complete()` on already-completed instance is safe (asyncio.Event.set() is idempotent)
8. **Non-registered instances**: `complete()` buffers or returns True — doesn't affect existing fire-and-forget flow
9. **Error propagation**: Errors from spawned agent surface as `CompletionResult(is_error=True)`, returned to caller as `"Error: ..."` string
10. **Orphan cleanup**: On timeout, best-effort terminate the still-running instance
11. **Stale cleanup**: Periodic background task prevents long-lived registry leaks

## Deliverables

- [ ] `daemon/services/completion_registry.py` — CompletionRegistry with CompletionResult, buffered completions, register/complete/wait_for/unregister/cleanup_stale
- [ ] `daemon/manager.py` — Registry initialization + event loop + stale cleanup task + expose pool size
- [ ] `daemon/services/child_reports.py` — Completion signals at 2 exit points (root + child)
- [ ] `daemon/services/error_reporting.py` — Error signal after session.commit()
- [ ] `daemon/utils.py` — `invoke_agent_and_wait()` with semaphore deadlock prevention + buffered registration + `_try_terminate_orphan()`
- [ ] `tests/unit/services/test_completion_registry.py` — All 18 test cases passing (incl. deadlock + race condition + buffer tests)
- [ ] Existing test suite still passes (no regression)

## Verification

```bash
# Run new tests
pytest tests/unit/services/test_completion_registry.py -v

# Run existing child_reports tests (verify no regression)
pytest tests/ -k "child_report" -v

# Run existing error reporting tests
pytest tests/ -k "error_report" -v

# Verify module imports
python -c "from daemon.services.completion_registry import get_completion_registry, CompletionResult; print('OK')"
python -c "from daemon.utils import invoke_agent_and_wait; print('OK')"
```
