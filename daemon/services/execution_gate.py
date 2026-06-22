"""Per-instance execution gate using asyncio.Lock.

Serializes message processing within a single daemon process. All
WorkerPool threads funnel through MainLoopBridge.run_async, which
schedules coroutines on the main event loop. The asyncio.Lock is
acquired on the main loop, not on worker threads.

Cross-process coordination is NOT supported — this gate is for
single-process WorkerPool serialization only. Multi-node deployment
is a follow-up.

What this service does
----------------------

``ExecutionGateService`` is the **single chokepoint** for
``graph.astream``. It owns a per-instance ``asyncio.Lock`` keyed by
``instance_id``. The contract is:

- Only one ``gate.run`` for a given instance is in flight at a time.
  The second caller blocks (on the same event loop) until the first
  caller releases the lock, then runs its ``work_fn``.
- The lock is held for the entire duration of the
  ``_process_message_with_tracking`` call. Release is unconditional
  on exit (success, exception, or cancellation).
- Distinct instances have distinct locks, so unrelated work_fns
  run in parallel — the gate does NOT false-serialize the world.

Why asyncio.Lock, not a DB-backed lease
---------------------------------------

All gate callers (WorkerPool threads, JobQueue async handlers, the
resume path) funnel their work into the main event loop via
``MainLoopBridge.run_async``. Because every ``gate.run`` body
acquires the lock on the main loop, a single ``asyncio.Lock`` per
instance is sufficient to serialize concurrent ``graph.astream``
calls for that instance — no DB round-trip, no heartbeat, no
cross-process coordination.

The previous DB-backed implementation added:

- a per-instance lease row in ``instance_execution_leases``
- an in-process heartbeat task to keep the row alive
- a startup recovery sweep (``recover_stale_leases``)
- a mid-flight lease-revocation escalation path

None of these are needed in a single-process daemon where every
caller is on the same event loop. The ``asyncio.Lock`` is the
correct primitive for this model.

The constructor accepts (and ignores) old positional/keyword
arguments (``lease_repo``, ``stale_lease_seconds``,
``heartbeat_interval_seconds``, ``heartbeat_max_consecutive_errors``)
so call sites that still pass them — e.g. ``InstanceManager``
passing ``lease_repo=...`` — keep working.

``cancel_instance_execution`` is a no-op because cancellation is
now handled by the caller's ``CancellationToken`` (the
``pause_instance_cascade`` path), not by the gate. ``recover_stale_leases``
is a no-op because there is no longer a lease row to recover.
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


WorkFn = Callable[[], Awaitable[Any]]


# ─── Service ──────────────────────────────────────────────────────────────────


class ExecutionGateService:
    """Per-instance execution gate using ``asyncio.Lock``.

    Lifetime: one instance per daemon process. Constructed during
    ``InstanceManager.initialize()`` and stored on
    ``InstanceManager._execution_gate``. Dispatchers
    (``MessageJobHandler``, ``ProcessMessageProcessor``,
    ``_resume_processing_background``) reach it through the manager.

    Threading: the service is safe to ``await`` from coroutines on
    the main event loop. All callers (WorkerPool threads, JobQueue
    async handlers, the resume path) funnel their work onto the
    main loop via ``MainLoopBridge.run_async`` before calling
    ``gate.run``, so every acquisition happens on the same loop and
    a single ``asyncio.Lock`` per instance is sufficient to
    serialize concurrent ``graph.astream`` calls.
    """

    # Backward-compat class-level constants. Old callers (and
    # InstanceManager) read these from the class. They are no longer
    # used internally but the names must remain to keep imports
    # working.
    DEFAULT_STALE_LEASE_SECONDS = 300
    DEFAULT_LEASE_HEARTBEAT_SECONDS = 30.0
    DEFAULT_HEARTBEAT_MAX_CONSECUTIVE_ERRORS = 5

    def __init__(self, *args, **kwargs):
        """Accept and ignore all old constructor args for backward compat.

        Old signature was ``(lease_repo, stale_lease_seconds,
        heartbeat_interval_seconds, heartbeat_max_consecutive_errors)``.
        New signature is empty — the gate has no configuration knobs
        because the lock is per-process, per-instance, and free.
        """
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, instance_id: str) -> asyncio.Lock:
        """Return (lazily creating) the lock for ``instance_id``."""
        lock = self._locks.get(instance_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[instance_id] = lock
        return lock

    async def run(
        self,
        instance_id: str,
        holder_id: str,  # Ignored (backward compat)
        holder_kind: str,  # Ignored (backward compat)
        work_fn: WorkFn,
    ) -> Any:
        """Acquire the per-instance lock, run ``work_fn``, release the lock.

        Behaviour:
            - If the lock is free, acquire it and call ``work_fn()``. The
              result of ``work_fn()`` is returned to the caller.
            - If the lock is held by someone else (on the same event
              loop), the call blocks until the holder releases. The
              second caller's ``work_fn`` runs *after* the first
              caller's ``work_fn`` finishes — there is no contention
              return path.
            - If ``work_fn`` raises, the lock is still released before
              the exception propagates.
            - If the awaited task is cancelled (e.g. by
              ``pause_instance_cascade`` via the
              ``CancellationToken``), the lock is released as the
              ``async with`` block unwinds.
        """
        lock = self._lock_for(instance_id)
        async with lock:
            return await work_fn()

    # ─── Diagnostic helpers (backward compat) ─────────────────────────────

    async def is_held(self, instance_id: str) -> bool:
        """True iff the per-instance lock is currently held.

        Used by tests and by diagnostic paths. The lock is held iff
        some caller is currently inside ``gate.run`` for this
        instance and has not yet returned.
        """
        lock = self._locks.get(instance_id)
        return lock is not None and lock.locked()

    async def is_held_by(self, instance_id: str, holder_id: str) -> bool:
        """True iff the lock is currently held (holder identity not tracked).

        The asyncio.Lock implementation does not track which holder
        acquired the lock, only whether the lock is held at all. The
        ``holder_id`` argument is accepted (and ignored) for backward
        compat with old callers.
        """
        return await self.is_held(instance_id)

    def cancel_instance_execution(self, instance_id: str) -> None:
        """No-op under the asyncio.Lock gate.

        Cancellation is handled by the caller's ``CancellationToken``
        (see ``daemon/cancellation.py``), not by the gate. This
        method is preserved for backward compat with old call sites
        (``pause_instance_cascade``, ``terminate_instance``).
        """
        return None

    # ─── Recovery (no-op, backward compat) ───────────────────────────────

    async def recover_stale_leases(
        self, max_age_seconds: int | None = None
    ) -> int:
        """No-op under the asyncio.Lock gate.

        There is no lease row to recover. Kept as a no-op so the
        startup recovery sweep in ``InstanceManager`` keeps working.
        """
        return 0

    # ─── Heartbeat (no-op, backward compat) ───────────────────────────────

    async def heartbeat(self, instance_id: str, holder_id: str) -> bool:
        """No-op under the asyncio.Lock gate.

        Returns True (the gate considers the caller "live") for
        backward compat with old callers that may still invoke it.
        """
        return True

    # ─── Backward-compat properties (kept for old code paths) ────────────

    @property
    def _lease_repo(self):
        """Deprecated. Returns None under the asyncio.Lock gate."""
        return None
