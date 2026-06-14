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
- The holder must keep the lease's ``heartbeat_at`` fresh while the
  work is in flight; ``recover_stale_leases`` on another node uses
  ``COALESCE(heartbeat_at, acquired_at) < :cutoff`` to detect a
  crashed holder. ``_execute_under_lease`` spawns an
  ``asyncio.create_task`` heartbeat at the configured interval so
  long-running ``graph.astream`` calls cannot be evicted mid-execution.
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
behind. The next startup runs ``recover_stale_leases`` which performs
a single ``DELETE WHERE COALESCE(heartbeat_at, acquired_at) < :cutoff``
to clear rows whose holder has not heartbeated within the threshold.
The default threshold is conservative (5 minutes) so a long-running
astream isn't accidentally killed. The recovery is logged so
operators can audit when stale leases had to be cleared.

``LeaseLostError``
------------------

If the holder's lease row is deleted out from under it (by
``recover_stale_leases`` on another node, or any other code path
that bypasses the holder_id check), the holder's in-flight
``work_fn`` is cancelled and ``LeaseLostError`` is raised. The
dispatcher treats this as a transient error and re-queues. Detection
relies on the heartbeat: if the heartbeat returns False (the row was
deleted or replaced), we know we lost the lease and cancel.

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

Non-sentinel return value
-------------------------

``LeaseContention`` is a regular dataclass, not a sentinel/exception.
``run`` returns the value of ``work_fn()`` on success — if your
``work_fn`` ever returns a ``LeaseContention`` instance, the
``isinstance(gate_outcome, LeaseContention)`` check in the dispatchers
will misinterpret the success result as contention. Current
``work_fn``s return ``MessageResult`` (a pydantic model), so the
collision is impossible in practice. Do not return
``LeaseContention`` from a custom ``work_fn``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

from ..repositories.execution_lease.models import LeaseHolderKind
from ..repositories.execution_lease.repository import ExecutionLeaseRepository

logger = logging.getLogger(__name__)


# Default lease heartbeat interval. The recovery threshold is 5
# minutes; we heartbeat at 30 s so ~10 missed beats (worker process
# death) are required before the lease is considered stale. Sized to
# match the existing task-heartbeat convention.
DEFAULT_LEASE_HEARTBEAT_INTERVAL_SECONDS = 30.0


class LeaseContentionReason(str, Enum):
    """Why a lease could not be acquired. The caller uses this to decide
    how to back off (atomic_transition, re-poll, retry-after-delay, etc.).
    """

    HELD_BY_OTHER = "held_by_other"
    """The lease row exists and is held by a different holder."""

    HELD_BY_LOST = "held_by_lost"
    """The lease row existed at ``try_acquire`` time but was gone by
    the time we asked ``get_holder`` (vanishingly rare; the previous
    holder released between our two queries). The caller cannot use
    ``holder_id``/``holder_kind`` for diagnostics — both are empty."""


@dataclass
class LeaseContention:
    """Returned by ``ExecutionGateService.run`` when the lease is held by
    someone else. ``holder_kind`` and ``holder_id`` let the caller decide
    how to back off (e.g. message_job callers atomically back-transition
    the job to PENDING; task callers re-poll the task table).

    ``reason`` is the authoritative signal: ``HELD_BY_OTHER`` means
    another holder has the lease (``holder_id``/``holder_kind``
    identify them); ``HELD_BY_LOST`` means the row vanished between
    ``try_acquire`` and ``get_holder`` (vanishingly rare;
    ``holder_id``/``holder_kind`` are empty).

    ``holder_lost_during_contention`` is a deprecated convenience
    alias for ``reason == HELD_BY_LOST``. Prefer checking ``reason``
    directly. Kept for backward compatibility.
    """

    reason: LeaseContentionReason
    holder_id: str
    holder_kind: str
    acquired_at: Optional[Any] = None  # datetime
    holder_lost_during_contention: bool = False


class LeaseLostError(Exception):
    """Raised inside ``gate.run`` if the caller lost the lease mid-execution.

    Raised when the in-flight heartbeat returns False (the lease row
    was deleted by ``recover_stale_leases`` on another node, or
    replaced by a different holder) — i.e. the process is no longer
    the authoritative driver of ``graph.astream`` for this instance.
    The in-flight ``work_fn`` is cancelled and this exception
    propagates to the caller. Dispatchers treat it as a transient
    error and re-queue.
    """


WorkFn = Callable[[], Awaitable[Any]]
"""A user-supplied async callable that performs the actual work
(typically a call to ``_process_message_with_tracking``). It runs only
if the lease was acquired; otherwise ``run`` returns ``LeaseContention``
without calling it.

NOTE: do NOT return a ``LeaseContention`` instance from ``work_fn``;
see the module docstring under "Non-sentinel return value"."""


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

    # Default lease heartbeat interval (the in-process refresh
    # cadence). Mirrors the module-level
    # ``DEFAULT_LEASE_HEARTBEAT_INTERVAL_SECONDS`` so external
    # callers (e.g. ``InstanceManager.__init__``) can reach it via
    # the class without importing the module constant.
    DEFAULT_LEASE_HEARTBEAT_SECONDS = DEFAULT_LEASE_HEARTBEAT_INTERVAL_SECONDS

    # After this many consecutive heartbeat DB errors (e.g. the
    # database is unreachable), escalate to ``LeaseLostError`` so the
    # in-flight work is cancelled deterministically rather than left
    # running against an un-refreshed lease that ``recover_stale_leases``
    # on another node would then evict. 5 consecutive failures at the
    # default 30s interval = 2.5 minutes of inability to refresh,
    # well below the 5-minute staleness threshold.
    DEFAULT_HEARTBEAT_MAX_CONSECUTIVE_ERRORS = 5

    def __init__(
        self,
        lease_repo: ExecutionLeaseRepository,
        stale_lease_seconds: int = DEFAULT_STALE_LEASE_SECONDS,
        heartbeat_interval_seconds: float = DEFAULT_LEASE_HEARTBEAT_SECONDS,
        heartbeat_max_consecutive_errors: int = (
            DEFAULT_HEARTBEAT_MAX_CONSECUTIVE_ERRORS
        ),
    ):
        """Initialize the gate.

        Args:
            lease_repo: The DB-backed lease repository.
            stale_lease_seconds: How old a lease's ``heartbeat_at`` can
                be before ``recover_stale_leases`` considers it stale.
            heartbeat_interval_seconds: How often the in-process
                heartbeat task refreshes ``heartbeat_at`` while a
                ``gate.run`` is in flight. Should be at least 5-10x
                smaller than ``stale_lease_seconds`` so a few missed
                beats don't false-positive flag a live lease.
            heartbeat_max_consecutive_errors: How many consecutive
                heartbeat DB errors before the heartbeat loop
                escalates to ``lease_lost`` (cancelling the
                in-flight work and raising ``LeaseLostError``).
                Prevents the gate from sitting on a long-running
                ``graph.astream`` whose lease can no longer be
                refreshed — that lease would be considered stale by
                ``recover_stale_leases`` on another node and
                cancelled anyway, but only after a full
                ``stale_lease_seconds`` window and only on the
                recovery side. This knob makes the in-process
                holder react sooner.
        """
        self._lease_repo = lease_repo
        self._stale_lease_seconds = stale_lease_seconds
        self._heartbeat_interval = max(0.1, heartbeat_interval_seconds)
        self._heartbeat_max_consecutive_errors = max(
            1, heartbeat_max_consecutive_errors
        )
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
            - If the lease is somehow lost mid-execution (the
              heartbeat returns False, meaning the row was deleted by
              ``recover_stale_leases`` on another node), ``work_fn``
              is cancelled and ``LeaseLostError`` is raised. The
              dispatcher treats this as a transient error and
              re-queues.

        The in-flight heartbeat is critical: without it, a
        ``graph.astream`` call longer than
        ``stale_lease_seconds`` (default 5 min) would be eligible
        for eviction by another node's ``recover_stale_leases``,
        re-introducing the dual-driver race the gate is designed to
        prevent.

        IMPORTANT: ``work_fn`` must NOT return a ``LeaseContention``
        instance — see the module docstring under "Non-sentinel
        return value".

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
            caller. Raises ``LeaseLostError`` if the lease was
            evicted mid-execution.
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
                # failed acquire and our get_holder. Surface this
                # distinctly via ``holder_lost_during_contention``
                # so callers don't try to log a non-existent
                # ``holder_id``.
                return LeaseContention(
                    reason=LeaseContentionReason.HELD_BY_LOST,
                    holder_id="",
                    holder_kind="",
                    acquired_at=None,
                    holder_lost_during_contention=True,
                )
            return LeaseContention(
                reason=LeaseContentionReason.HELD_BY_OTHER,
                holder_id=current.holder_id,
                holder_kind=current.holder_kind,
                acquired_at=current.acquired_at,
                holder_lost_during_contention=False,
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
        cancellation AND keeping the lease's ``heartbeat_at`` fresh.

        Subroutine of ``run`` so the ``try/finally`` for release stays
        at the call site.

        Cancellation: registers the running ``asyncio.Task`` in
        ``_running_tasks`` so ``cancel_instance_execution`` (used by
        terminate / pause) can interrupt it from elsewhere in the
        daemon.

        Heartbeat: spawns a child task that calls
        ``lease_repo.heartbeat`` every ``_heartbeat_interval``
        seconds. If the heartbeat returns False (the row was deleted
        by another node's ``recover_stale_leases``), the work_fn is
        cancelled and ``LeaseLostError`` is raised so the dispatcher
        can re-queue.
        """
        current_task = asyncio.current_task()
        if current_task is not None:
            with self._running_tasks_lock:
                # If there's already a task for this instance, the
                # caller is buggy (re-entered without releasing). We
                # don't crash; the existing task is preserved so
                # cancel_instance_execution can still reach it.
                self._running_tasks.setdefault(instance_id, current_task)
        heartbeat_task: asyncio.Task | None = None
        work_task: asyncio.Task | None = None
        lease_lost = asyncio.Event()
        try:
            heartbeat_task = asyncio.create_task(
                self._lease_heartbeat_loop(
                    instance_id, holder_id, lease_lost
                )
            )
            work_task = asyncio.create_task(work_fn())
            lease_lost_wait = asyncio.create_task(lease_lost.wait())
            done, pending = await asyncio.wait(
                {work_task, lease_lost_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if work_task in done:
                lease_lost_wait.cancel()
                try:
                    await lease_lost_wait
                except asyncio.CancelledError:
                    pass
                return work_task.result()
            # Heartbeat reported lease loss. Cancel work_fn so it
            # doesn't keep driving graph.astream for an instance whose
            # lease has been revoked.
            work_task.cancel()
            try:
                await work_task
            except asyncio.CancelledError:
                pass
            raise LeaseLostError(
                f"Lost execution lease for instance={instance_id[:8]}... "
                f"holder_id={holder_id} mid-execution (row was cleared by "
                "another process or replaced by a different holder)"
            )
        finally:
            # Cancel both child tasks and drain them. Without this,
            # an outer cancel (``cancel_instance_execution``) or a
            # raised exception would leave the in-flight ``work_task``
            # — the actual ``graph.astream`` driver — running detached,
            # which is exactly the in-flight stream the cancel was
            # meant to stop. The same applies to the heartbeat.
            if work_task is not None and not work_task.done():
                work_task.cancel()
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()
            for t in (work_task, heartbeat_task):
                if t is None:
                    continue
                try:
                    await t
                except BaseException:
                    # Swallow EVERYTHING: CancelledError is the normal
                    # exit for both tasks (we just cancelled them);
                    # work_fn's own exception is the caller's concern
                    # and is re-raised by the path that awaited
                    # ``work_task.result()`` above. BaseException
                    # (not Exception) is intentional because
                    # CancelledError is a BaseException subclass on
                    # Python 3.8+.
                    pass
            if current_task is not None:
                with self._running_tasks_lock:
                    if self._running_tasks.get(instance_id) is current_task:
                        self._running_tasks.pop(instance_id, None)

    async def _lease_heartbeat_loop(
        self,
        instance_id: str,
        holder_id: str,
        lease_lost: asyncio.Event,
    ) -> None:
        """Refresh the lease's ``heartbeat_at`` every
        ``_heartbeat_interval`` seconds. If a heartbeat returns False
        (the row was deleted or replaced), set ``lease_lost`` so
        ``_execute_under_lease`` cancels the work and raises
        ``LeaseLostError``.

        Errors other than False-return are swallowed and logged: a
        transient DB error should not kill an in-flight
        ``graph.astream`` call on the first failure. However, after
        ``_heartbeat_max_consecutive_errors`` consecutive failures we
        escalate to ``lease_lost`` so the in-flight work is
        cancelled deterministically rather than left running against
        a lease that ``recover_stale_leases`` on another node would
        eventually evict.

        Throttled logging: the first error in a run is logged at
        WARNING, subsequent consecutive errors at DEBUG; the recovery
        escalation logs at WARNING once more.
        """
        consecutive_errors = 0
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                try:
                    ok = await self.heartbeat(instance_id, holder_id)
                except Exception as e:  # noqa: BLE001
                    consecutive_errors += 1
                    if consecutive_errors == 1:
                        logger.warning(
                            f"ExecutionGate.heartbeat error "
                            f"instance={instance_id[:8]}... "
                            f"holder_id={holder_id}: "
                            f"{type(e).__name__}: {e} "
                            f"(suppressing further errors until "
                            f"recovery or success; "
                            f"consecutive_errors={consecutive_errors})"
                        )
                    else:
                        logger.debug(
                            f"ExecutionGate.heartbeat error "
                            f"instance={instance_id[:8]}... "
                            f"consecutive_errors={consecutive_errors}"
                        )
                    if (
                        consecutive_errors
                        >= self._heartbeat_max_consecutive_errors
                    ):
                        logger.warning(
                            f"ExecutionGate escalating to lease_lost "
                            f"after {consecutive_errors} consecutive "
                            f"heartbeat errors "
                            f"instance={instance_id[:8]}... "
                            f"holder_id={holder_id}"
                        )
                        lease_lost.set()
                        return
                    continue
                if not ok:
                    lease_lost.set()
                    return
                # Success resets the failure counter.
                consecutive_errors = 0
        except asyncio.CancelledError:
            return

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

        Called from daemon startup. Single bulk
        ``DELETE WHERE COALESCE(heartbeat_at, acquired_at) < :cutoff``
        (one round-trip rather than N+1). Returns the number of
        rows cleared; operators read this in the startup log to know
        whether a previous run died mid-execution.

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
        cleared = await asyncio.to_thread(
            self._lease_repo.clear_stale_leases, threshold
        )
        if cleared:
            logger.warning(
                f"ExecutionGate: cleared {cleared} stale lease(s) on startup "
                f"(threshold={threshold}s)"
            )
        return cleared

    # --------------------------------------------------------
    # HEARTBEAT
    # --------------------------------------------------------

    async def heartbeat(self, instance_id: str, holder_id: str) -> bool:
        """Refresh the lease's ``heartbeat_at`` timestamp.

        Returns False if the lease is no longer held by the caller
        (stolen by recovery, or replaced by a different holder).
        Internal callers (``_lease_heartbeat_loop``) treat False as a
        signal that the in-flight work must be cancelled and
        re-queued via ``LeaseLostError``.

        External callers (e.g. a worker that wants to demonstrate
        liveness to an external observer) can also use this; the
        return value carries the same meaning.
        """
        return await asyncio.to_thread(
            self._lease_repo.heartbeat, instance_id, holder_id
        )
