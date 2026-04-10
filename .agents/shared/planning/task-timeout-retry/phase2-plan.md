# Phase 2: CancellationToken & TimeoutMonitor

## Objective

Enhance the existing CancellationToken with TASK_TIMEOUT reason and timestamp tracking, and create a TimeoutMonitor daemon thread that fires cancellation after a configurable duration. These are the concurrency primitives that Phase 4 (Worker) will use.

## Coupling

- **Depends on**: None
- **Coupling type**: independent (can run in parallel with Phase 1)
- **Shared files with other phases**: `daemon/cancellation.py` (shared with Phase 4, Phase 5)
- **Shared APIs/interfaces**: CancellationToken, CancellationTokenSource, CancellationReason, OperationCancelledError
- **Why this coupling**: Worker (Phase 4) creates TimeoutMonitor + CancellationToken per task; StaleTaskRecovery (Phase 5) uses CancellationTokenSource to cancel tasks

## Context

### Existing CancellationToken System
- `CancellationToken` — dataclass with `threading.Event`, `check()`, `async_check()`, `is_cancelled` property, `reason` property
- `CancellationTokenSource` — factory with `cancel(reason)`, `register_callback()`, idempotent cancel
- `CancellationReason` — enum with TIMEOUT, WATCHDOG_RETRY, MANUAL, SHUTDOWN, SESSION_TERMINATED, USER_STOPPED
- `OperationCancelledError` — exception with reason

### What's Missing
- No `TimeoutMonitor` class exists
- No timestamp tracking on CancellationToken (cancelled_at)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Reuse CancellationReason.TIMEOUT | <!-- FIX: W4 --> No new enum value — reuse existing `TIMEOUT` for task-level timeouts from TimeoutMonitor | `daemon/cancellation.py` |
| 2 | Add cancelled_at timestamp to CancellationToken | Track when cancellation occurred (for logging/debugging) | `daemon/cancellation.py` |
| 3 | Create TimeoutMonitor class | Daemon thread that waits for timeout then cancels token. Uses threading.Event.wait() for clean shutdown | `daemon/services/timeout_monitor.py` (new) |
| 4 | Write unit tests for TimeoutMonitor | Test: fires cancel on timeout, doesn't fire when stopped early, handles multiple start/stop | `tests/message_queue_redesign/test_timeout_monitor.py` (new) |
| 5 | Write unit tests for enhanced CancellationToken | Test: TIMEOUT reason, cancelled_at timestamp | `tests/test_cancellation.py` (new or extend existing) |

## Key Files

- `daemon/cancellation.py` — CancellationToken, CancellationTokenSource, CancellationReason, OperationCancelledError
- `daemon/services/timeout_monitor.py` — **NEW** file

## Detailed Implementation

### 1. CancellationReason Update

<!-- FIX: W4 — Reuse existing TIMEOUT instead of adding TASK_TIMEOUT.
     The existing TIMEOUT = "timeout" is the general-purpose timeout reason.
     Adding TASK_TIMEOUT would create overlap and confusion.
     TimeoutMonitor will use CancellationReason.TIMEOUT.
     If finer-grained distinction is needed later, add a context string to
     OperationCancelledError rather than proliferating enum values. -->

```python
class CancellationReason(Enum):
    TIMEOUT = "timeout"                # Used by TimeoutMonitor for task timeouts
    WATCHDOG_RETRY = "watchdog_retry"
    MANUAL = "manual"
    SHUTDOWN = "shutdown"
    SESSION_TERMINATED = "session_terminated"
    USER_STOPPED = "user_stopped"
# No new enum value needed — reuse existing TIMEOUT
```

**Rationale for reusing `TIMEOUT`**: The existing `TIMEOUT` enum value is semantically correct for task-level timeouts. `WATCHDOG_RETRY` already exists for watchdog-specific timeouts. Adding `TASK_TIMEOUT` would overlap with `TIMEOUT` and create ambiguity about which to use when. The `OperationCancelledError` can carry context strings for debugging.

### 2. CancellationToken Enhancement

Add `cancelled_at` tracking. The existing CancellationTokenSource.cancel() method should record the timestamp:

```python
@dataclass
class CancellationToken:
    _cancelled: threading.Event = field(default_factory=threading.Event)
    _reason: Optional[CancellationReason] = field(default=None, init=False)
    _cancelled_at: Optional[float] = field(default=None, init=False)  # NEW: monotonic timestamp
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    
    @property
    def cancelled_at(self) -> Optional[float]:
        """Get the monotonic timestamp when cancellation occurred."""
        with self._lock:
            return self._cancelled_at
```

Update `CancellationTokenSource.cancel()` to record timestamp:

```python
def cancel(self, reason: CancellationReason = CancellationReason.MANUAL) -> None:
    with self._lock:
        if self._token._cancelled.is_set():
            return  # Already cancelled
        
        self._token._reason = reason
        self._token._cancelled_at = time.monotonic()  # NEW
        self._token._cancelled.set()
        
        for callback in self._callbacks:
            try:
                callback()
            except Exception:
                pass
```

### 3. TimeoutMonitor Implementation

```python
"""TimeoutMonitor - daemon thread that cancels a token after a timeout."""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class TimeoutMonitor:
    """Monitors task timeout and cancels token when exceeded.
    
    Starts a daemon thread that waits for the specified timeout.
    If not stopped before timeout, cancels the provided token.
    
    Usage:
        source = CancellationTokenSource()
        monitor = TimeoutMonitor(task_id=42, source=source, timeout_seconds=900)
        monitor.start()
        try:
            # ... do work ...
        finally:
            monitor.stop()
    """
    
    def __init__(
        self,
        task_id: int,
        source: "CancellationTokenSource",
        timeout_seconds: float,
    ):
        self._task_id = task_id
        self._source = source
        self._timeout = timeout_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fired = False
    
    @property
    def fired(self) -> bool:
        """Whether the timeout was fired (not cancelled early)."""
        return self._fired
    
    def start(self) -> None:
        """Start the timeout monitor thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning(f"TimeoutMonitor for task {self._task_id} already running")
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"TimeoutMonitor-task-{self._task_id}",
            daemon=True,
        )
        self._thread.start()
        logger.debug(
            f"TimeoutMonitor started for task {self._task_id} "
            f"(timeout={self._timeout}s)"
        )
    
    def _run(self) -> None:
        """Wait for timeout or stop signal."""
        if self._stop_event.wait(timeout=self._timeout):
            # Stopped before timeout
            return
        
        # Timeout elapsed — cancel the token
        self._fired = True
        from daemon.cancellation import CancellationReason
        self._source.cancel(CancellationReason.TIMEOUT)  <!-- FIX: W4 — reuse TIMEOUT, not TASK_TIMEOUT -->
        logger.warning(
            f"TimeoutMonitor: task {self._task_id} timed out "
            f"after {self._timeout}s"
        )
    
    def stop(self) -> None:
        """Stop the monitor before timeout fires."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                logger.warning(
                    f"TimeoutMonitor for task {self._task_id} "
                    f"did not stop within 2s"
                )
    
    def is_running(self) -> bool:
        """Check if the monitor thread is alive."""
        return self._thread is not None and self._thread.is_alive()
```

## Design Decisions

### Why CancellationTokenSource (not just CancellationToken)?

The existing code already separates `CancellationTokenSource` (producer) from `CancellationToken` (consumer). TimeoutMonitor holds the **source** — it can cancel. The Worker holds the **token** — it can only check. This matches the C# pattern already established.

### Why time.monotonic() for cancelled_at?

`time.monotonic()` is not affected by system clock changes, making it reliable for duration calculations. If human-readable timestamps are needed, the caller can compute `datetime.now(timezone.utc)` at the point of handling.

### Why threading.Event.wait() for timeout?

It's interruptible (via `.set()`), efficient (no polling), and already used throughout the codebase (Worker._stop_event, StaleTaskRecovery._stop_event). No need for asyncio here — this is a daemon thread.

## Constraints

- TimeoutMonitor must be a daemon thread (dies with main process)
- Must clean up thread on stop (join with timeout)
- CancellationToken changes must be backward compatible (cancelled_at is optional)
- Import of CancellationReason inside _run() avoids circular imports

## Deliverables

- [ ] No new CancellationReason needed — reuses existing TIMEOUT (W4 fix)
- [ ] CancellationToken tracks cancelled_at timestamp
- [ ] CancellationTokenSource records cancelled_at on cancel()
- [ ] TimeoutMonitor class created with start/stop/fired interface
- [ ] Unit tests for TimeoutMonitor: timeout fires, early stop, double-stop
- [ ] Unit tests for CancellationToken cancelled_at
