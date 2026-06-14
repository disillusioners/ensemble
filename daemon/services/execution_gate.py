"""Execution Gate: single owner of ``graph.astream`` per ``thread_id``.

Background
----------

The daemon has two physical dispatchers that can both call
``_process_message_with_tracking`` (and therefore ``graph.astream``) for
the same instance:

1. **JobQueue side** — ``MessageJobHandler.handle`` is driven by
   ``JobProcessor._process_loop`` polling the ``job_queue_items`` table.
   Triggered by ``enqueue_message_via_jq`` (the API/HTTP entry point and
   some internal paths).

2. **WorkerPool side** — ``ProcessMessageProcessor.process`` is driven
   by worker threads in ``worker_pool.py`` polling the ``task`` table.
   Triggered by ``enqueue_message`` (used by
   ``child_reports._create_completion_report`` for completion reports
   from child instances).

Before this gate existed, the two dispatchers could both reach
``graph.astream`` for the same instance concurrently. Each call would
read the same langgraph checkpoint version, append its own message via
the ``add_messages`` reducer, and try to write a new version. The
write-side lost-update race caused one of the appended messages to
disappear from the final checkpoint. This was the root cause of the
"giter's completion report was never seen by the parent LLM" bug
documented in
``docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md``.

What this service does
----------------------

``ExecutionGateService`` is the **single chokepoint** for
``graph.astream``. It owns a DB-backed per-instance lease
(``instance_execution_leases``). The contract is:

- Only one dispatcher (caller) holds the lease for a given instance at
  a time. The lease is the *authoritative* "is this thread_id busy?"
  answer.
- Acquisition is atomic (``INSERT ... ON CONFLICT DO NOTHING`` /
  ``INSERT OR IGNORE``) and idempotent.
- The lease is held for the entire duration of the
  ``_process_message_with_tracking`` call. Release is atomic and
  conditional on ``holder_id`` matching — a stale loser cannot
  accidentally evict a fresh winner's lease.
- On contention (the lease is already held by someone else), the
  caller receives a ``LeaseContention`` signal and is expected to back
  off and re-queue, not call ``graph.astream``.

The dispatchers (MessageJobHandler, ProcessMessageProcessor) are
refactored to wrap their ``_process_message_with_tracking`` call in
``gate.run(...)``. If ``gate.run`` returns ``LeaseContention``, the
dispatcher takes the same back-off path it already had for the
Message-vs-Message case (atomic_transition PROCESSING→PENDING for
``message_job``; leave the task in 'running' so the worker re-polls
once the lease is released for ``task``).

Crash recovery
--------------

If the daemon dies while holding a lease, the lease row is left
behind. The next startup runs ``recover_stale_leases`` which deletes
rows whose ``heartbeat_at`` is older than a threshold. The default
threshold is conservative (5 minutes) so a long-running astream
isn't accidentally killed. The recovery is logged so operators can
audit when stale leases had to be cleared.

What this service does NOT do
------------------------------

- It does not serialize *messages* — scheduling is still the
  Scheduling Layer's job. The Gate only serializes the *moment* of
  graph execution. Once a caller holds the lease, it can run any
  number of message batches in a single pass (matching the current
  "process one message and exit" pattern in the dispatchers).
- It does not own the ``graph.astream`` call directly. It owns the
  *lease* around it. The actual streaming call still happens in
  ``InstanceMessagingService._process_message_with_tracking``. This
  keeps the lease reusable from contexts where a custom function
  needs to be wrapped (e.g. tests, future sync invoke paths).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

from ..repositories.execution_lease.models import LeaseHolderKind
from ..repositories.execution_lease.repository import ExecutionLeaseRepository

logger = logging.getLogger(__name__)


class LeaseContentionReason(str, Enum):
    """Why a lease could not be acquired. The caller uses this to decide
    how to back off (atomic_transition, re-poll, retry-after-delay, etc.).
    """

    HELD_BY_OTHER = "held_by_other"
    """The lease row exists and is held by a different holder."""


@dataclass
class LeaseContention:
    """Returned by ``ExecutionGateService.run`` when the lease is held by
    someone else. ``holder_kind`` and ``holder_id`` let the caller decide
    how to back off (e.g. message_job callers atomically back-transition
    the job to PENDING; task callers re-poll the task table).
    """

    reason: LeaseContentionReason
    holder_id: str
    holder_kind: str
    acquired_at: Any  # datetime


class LeaseLostError(Exception):
    """Raised inside ``gate.run`` if the caller lost the lease mid-execution.

    This happens when ``recover_stale_leases`` (or any other code path)
    deletes the lease out from under the holder — e.g. a process crash
    recovery loop on a different node. The caller should treat this as
    a transient error and let the dispatcher decide whether to re-queue.
    """


WorkFn = Callable[[], Awaitable[Any]]
"""A user-supplied async callable that performs the actual work
(typically a call to ``_process_message_with_tracking``). It runs only
if the lease was acquired; otherwise ``run`` returns ``LeaseContention``
without calling it."""


class ExecutionGateService:
    """Single-owner-of-graph.astream gate with a DB-backed per-instance lease.

    Lifetime: one instance per daemon process. Constructed during
    ``InstanceManager.initialize()`` and stored on
    ``InstanceManager._execution_gate``. Dispatchers (MessageJobHandler,
    ProcessMessageProcessor) reach it through the manager.

    Threading: the service is safe to call from worker threads (the
    WorkerPool path) and from the asyncio event loop (the JobQueue
    path). The repository methods it delegates to are synchronous
    SQLAlchemy calls; the service bridges to async via
    ``asyncio.to_thread`` for the DB-touching methods so it can be
    ``await``-ed from the event loop without blocking it.
    """

    # Default heartbeat staleness threshold. Must be larger than the
    # longest expected ``_process_message_with_tracking`` call so a
    # healthy long-running astream isn't killed. 5 minutes is the same
    # ballpark as the per-task heartbeat threshold used by
    # ``StaleTaskRecovery``.
    DEFAULT_STALE_LEASE_SECONDS = 300

    def __init__(
        self,
        lease_repo: ExecutionLeaseRepository,
        stale_lease_seconds: int = DEFAULT_STALE_LEASE_SECONDS,
    ):
        """Initialize the gate.

        Args:
            lease_repo: The DB-backed lease repository.
            stale_lease_seconds: How old a lease's ``heartbeat_at`` can
                be before ``recover_stale_leases`` considers it stale.
        """
        self._lease_repo = lease_repo
        self._stale_lease_seconds = stale_lease_seconds
        # In-process fast path: which (instance_id, holder_id) pairs
        # are *currently* running in this Python process?
        # ``is_held_locally`` answers "is anyone in this process the
        # holder?" in O(1); ``_local_holder_id`` answers "is *this*
        # holder_id the local holder?" for the re-entrant fast path
        # in ``run``. The DB acquire is the authoritative check, so
        # the fast path is purely an optimisation.
        self._local_holders: dict[str, str] = {}
        self._local_holders_lock = threading.Lock()
        # Stash an in-process asyncio.Task per instance so
        # ``cancel_instance_execution`` can interrupt a running
        # ``gate.run`` from elsewhere (e.g. terminate, pause).
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._running_tasks_lock = threading.Lock()
        # PID of the current process, captured once at construction.
        # Stored on the lease row for diagnostics.
        self._pid = os.getpid()

    # --------------------------------------------------------
    # IN-PROCESS FAST PATH
    # --------------------------------------------------------

    def is_held_locally(self, instance_id: str) -> bool:
        """True iff this Python process holds the lease for ``instance_id``.

        Cheap in-process check used by dispatchers before doing a DB
        acquire — they should still call ``run`` (which double-checks
        in the DB so a different process can't have stolen the lease
        out from under them), but if the local check is False the
        dispatcher knows it definitely doesn't hold the lease yet.
        """
        with self._local_holders_lock:
            return instance_id in self._local_holders

    def _local_holder_id(self, instance_id: str) -> str | None:
        """Return the holder_id of the local holder, or None.

        Used by ``run`` for the re-entrant fast path. The fast path
        only applies when the *same* holder_id is calling — a
        different dispatcher in the same process must still go
        through the DB acquire so it can see cross-dispatcher
        contention.
        """
        with self._local_holders_lock:
            return self._local_holders.get(instance_id)

    def _mark_local(self, instance_id: str, holder_id: str) -> None:
        with self._local_holders_lock:
            self._local_holders[instance_id] = holder_id

    def _unmark_local(self, instance_id: str) -> None:
        with self._local_holders_lock:
            self._local_holders.pop(instance_id, None)

    # --------------------------------------------------------
    # PRIMARY ENTRY POINT
    # --------------------------------------------------------

    async def run(
        self,
        instance_id: str,
        holder_id: str,
        holder_kind: str,
        work_fn: WorkFn,
    ) -> Any | LeaseContention:
        """Acquire the lease, run ``work_fn``, release the lease.

        Behaviour:
            - If the lease is free (or already held by us), acquire it
              and call ``work_fn()``. The result of ``work_fn()`` is
              returned to the caller.
            - If the lease is held by someone else, return a
              ``LeaseContention`` without running ``work_fn``. The
              caller is expected to back off and re-queue.
            - If the work raises, the lease is still released before
              the exception propagates.
            - If the lease is somehow lost mid-execution (e.g.
              ``recover_stale_leases`` evicted the row), ``work_fn`` is
              cancelled and ``LeaseLostError`` is raised. The
              dispatcher treats this as a transient error.

        Args:
            instance_id: The langgraph thread_id == instance_id.
            holder_id: Stable identifier for the caller. Use the
                dispatcher-side id (e.g. ``message_job:{job_id}`` or
                ``task:{task_id}``) so release can be conditional on it.
            holder_kind: One of ``LeaseHolderKind`` values.
            work_fn: Async callable that performs the actual
                ``_process_message_with_tracking`` call.

        Returns:
            The return value of ``work_fn()``, or a
            ``LeaseContention`` if the lease was held by another
            caller.
        """
        # Fast local pre-check: if THIS process holds the lease AND
        # it's the same holder_id, skip the DB roundtrip. We need
        # BOTH conditions — just "this process holds it" is not
        # enough because a different dispatcher in the same process
        # could be the holder. (Example: a MESSAGE job from the
        # JobQueue holds the lease; a task in the WorkerPool tries
        # gate.run — same process, different holder_id, must NOT
        # take the fast path. The DB acquire is the authoritative
        # check.)
        if self._local_holder_id(instance_id) == holder_id:
            return await self._execute_under_lease(
                instance_id, holder_id, holder_kind, work_fn
            )

        acquired = await asyncio.to_thread(
            self._lease_repo.try_acquire,
            instance_id, holder_id, holder_kind, self._pid,
        )
        if not acquired:
            # Lost the race. Find out who holds it so the caller can
            # decide how to back off.
            current = await asyncio.to_thread(
                self._lease_repo.get_holder, instance_id
            )
            if current is None:
                # Vanishingly rare: the holder released between our
                # failed acquire and our get_holder. Treat as
                # contention-with-no-holder and let the caller retry.
                return LeaseContention(
                    reason=LeaseContentionReason.HELD_BY_OTHER,
                    holder_id="",
                    holder_kind="",
                    acquired_at=None,
                )
            return LeaseContention(
                reason=LeaseContentionReason.HELD_BY_OTHER,
                holder_id=current.holder_id,
                holder_kind=current.holder_kind,
                acquired_at=current.acquired_at,
            )

        # We have the lease. Mark locally and run the work.
        self._mark_local(instance_id, holder_id)
        try:
            return await self._execute_under_lease(
                instance_id, holder_id, holder_kind, work_fn
            )
        finally:
            # Release in DB and in-process. The DB release is
            # conditional on holder_id; a no-op if we somehow lost
            # the row (e.g. recover_stale_leases evicted it).
            await asyncio.to_thread(
                self._lease_repo.release, instance_id, holder_id
            )
            self._unmark_local(instance_id)

    async def _execute_under_lease(
        self,
        instance_id: str,
        holder_id: str,
        holder_kind: str,
        work_fn: WorkFn,
    ) -> Any:
        """Run ``work_fn`` while holding the lease, tracking the task for
        cancellation.

        Subroutine of ``run`` so the ``try/finally`` for release stays
        at the call site. Registers the running ``asyncio.Task`` in
        ``_running_tasks`` so ``cancel_instance_execution`` (used by
        terminate / pause) can interrupt it from elsewhere in the
        daemon.
        """
        current_task = asyncio.current_task()
        if current_task is not None:
            with self._running_tasks_lock:
                # If there's already a task for this instance, the
                # caller is buggy (re-entered without releasing). We
                # don't crash; the existing task is preserved so
                # cancel_instance_execution can still reach it.
                self._running_tasks.setdefault(instance_id, current_task)
        try:
            return await work_fn()
        finally:
            if current_task is not None:
                with self._running_tasks_lock:
                    if self._running_tasks.get(instance_id) is current_task:
                        self._running_tasks.pop(instance_id, None)

    # --------------------------------------------------------
    # DIRECT (NON-WORK) OPERATIONS
    # --------------------------------------------------------

    async def is_held(self, instance_id: str) -> bool:
        """True iff the lease is held by anyone (this process or another)."""
        holder = await asyncio.to_thread(
            self._lease_repo.get_holder, instance_id
        )
        return holder is not None

    async def is_held_by(self, instance_id: str, holder_id: str) -> bool:
        """True iff the lease is currently held by ``holder_id``."""
        return await asyncio.to_thread(
            self._lease_repo.is_held_by, instance_id, holder_id
        )

    async def cancel_instance_execution(self, instance_id: str) -> bool:
        """Interrupt the ``gate.run`` (and therefore ``graph.astream``) for
        ``instance_id`` if it is running in this process.

        Used by ``terminate_instance`` / ``pause_instance_cascade`` to
        ensure that when an instance is being torn down, its in-flight
        graph execution is cancelled promptly.

        Returns True if a running task was found and signalled to
        cancel. False otherwise (no running task in this process, or
        the lease is held by a different process).
        """
        with self._running_tasks_lock:
            task = self._running_tasks.get(instance_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    # --------------------------------------------------------
    # CRASH RECOVERY
    # --------------------------------------------------------

    async def recover_stale_leases(
        self, max_age_seconds: int | None = None
    ) -> int:
        """Clear leases whose holder has died.

        Called from daemon startup. For each lease whose
        ``heartbeat_at`` is older than ``max_age_seconds``, the row is
        deleted (no holder_id check — this is recovery, not release).
        The next attempt to acquire the lease for that instance will
        succeed.

        Returns the number of leases cleared. Operators can read this
        in the startup log to know whether a previous run died
        mid-execution.

        Args:
            max_age_seconds: Override the staleness threshold.
                Default is the constructor default
                (``DEFAULT_STALE_LEASE_SECONDS``).
        """
        threshold = (
            max_age_seconds
            if max_age_seconds is not None
            else self._stale_lease_seconds
        )
        stale = await asyncio.to_thread(
            self._lease_repo.find_stale_leases, threshold
        )
        cleared = 0
        for lease in stale:
            ok = await asyncio.to_thread(
                self._lease_repo.clear_stale, lease.instance_id
            )
            if ok:
                cleared += 1
                logger.warning(
                    "ExecutionGate.recover_stale_leases: cleared stale lease "
                    f"instance={lease.instance_id[:8]}... "
                    f"holder_id={lease.holder_id} "
                    f"holder_kind={lease.holder_kind} "
                    f"acquired_at={lease.acquired_at} "
                    f"heartbeat_at={lease.heartbeat_at}"
                )
        if cleared:
            logger.info(
                f"ExecutionGate: cleared {cleared} stale lease(s) on startup"
            )
        return cleared

    def recover_stale_leases_sync(
        self, max_age_seconds: int | None = None
    ) -> int:
        """Synchronous wrapper around ``recover_stale_leases``.

        ``InstanceManager.setup_worker_pool`` calls this from the
        lifespan's pre-event-loop section. We run an ad-hoc asyncio
        loop to drive the async implementation; the underlying
        repository calls are already thread-safe and we don't expect
        startup recovery to be on the hot path. If a real event loop
        is already running (test fixtures), we fall back to scheduling
        the coroutine on it.
        """
        try:
            _loop = asyncio.get_event_loop()
            if _loop.is_running():
                # A loop is already running — schedule and return 0
                # immediately. The caller can check the result via the
                # scheduled task if needed. This is the test fixture
                # path; in production startup we're not in a loop.
                _loop.create_task(self.recover_stale_leases(max_age_seconds))
                return 0
            return _loop.run_until_complete(
                self.recover_stale_leases(max_age_seconds)
            )
        except RuntimeError:
            # No event loop at all. Spin one up just for the recovery.
            return asyncio.run(self.recover_stale_leases(max_age_seconds))

    # --------------------------------------------------------
    # HEARTBEAT (used by future work; not required for correctness
    # of acquire/release under the current 'one call acquires, one
    # call releases' model, but exposed for callers that want to
    # demonstrate liveness to an external observer).
    # --------------------------------------------------------

    async def heartbeat(self, instance_id: str, holder_id: str) -> bool:
        """Refresh the lease's ``heartbeat_at`` timestamp.

        Returns False if the lease is no longer held by the caller
        (stolen by recovery, or replaced by a different holder). The
        caller should treat False as a signal that its execution
        should be cancelled and re-queued.
        """
        return await asyncio.to_thread(
            self._lease_repo.heartbeat, instance_id, holder_id
        )
