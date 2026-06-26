"""DependencyBus: DB-backed parent-waits-for-children service.

This is the in-process service layer over the
:class:`~daemon.repositories.dependency_bus.repository.DependencyWatcherRepository`.
It is the SOLE completion authority for parent-waits-for-children.
This class is always wired and flag-agnostic. The call sites in
``send_message`` and ``task_processor`` always consult it.

Architecture
------------

* **DB is the source of truth.** Every state transition goes through
  :meth:`DependencyWatcherRepository.transition_state`, a guarded
  Core UPDATE (``WHERE state = 'PENDING'``). The rowcount tells the
  caller whether *this* call won the race to fire. This is the
  backpressure primitive that prevents double-fire under concurrent
  terminal events.
* **In-memory cache is a hot-path optimization.** The
  ``_pending: dict[str, list[FollowUp]]`` cache, keyed by
  ``source_task_id``, is warmed on :meth:`start` from the DB and
  updated on :meth:`watch` / :meth:`emit_terminal`. Reads via
  :meth:`pending_watchers` hit the cache when present and fall back
  to a DB query otherwise — so a ``watch`` from another process
  (future multi-process deployment) is still observable.
* **Per-source-task lock** serializes concurrent
  :meth:`watch` / :meth:`emit_terminal` for the same task. Creation
  is guarded by a ``_locks_guard: asyncio.Lock`` so concurrent
  first-access on the same task can't race on dict mutation.
* **Crash safety.** If the process crashes after some watchers are
  FIRED but before the caller enqueues them, the rows are already
  FIRED in the DB. :meth:`start` calls :meth:`_recover_fired_unsent`
  to load FIRED rows for the caller to re-enqueue. The bus itself
  only transitions state and returns FollowUps; the caller is
  responsible for actual enqueueing (separation of concerns). The
  bus guarantees :meth:`pending_watchers` works after restart by
  reading DB state on cache miss.

Separation of concerns
----------------------

The bus identifies and atomically marks watchers as fired. The
caller (:meth:`MessageTaskProcessor.process` or whatever subscribes
to the terminal-event stream) does the actual FollowUp enqueueing.
This split keeps the bus narrow (state machine only) and makes the
caller's enqueueing policy independently testable.

Wiring contract
---------------

The class is always wired and is the SOLE completion authority
for parent-waits-for-children. ``send_message`` always calls
:meth:`watch` and the task processor always calls
:meth:`emit_terminal`. There is no fallback path — completion
flows through the bus or not at all.

Multi-process limitation
------------------------

The per-parent **generation counter** (used by
``JobFeedbackObserver._finalize_job`` for the orphan-race re-arm)
is **in-memory only** — it is NOT persisted to the DB and is NOT
restored by :meth:`start` / :meth:`_warm_cache`. After a bus
restart the counter starts fresh at ``{}`` and every parent
returns ``0`` until the next :meth:`watch` bumps it back.

This is safe in single-process deployments (the only writer
is the in-process :meth:`watch` and the only reader is the
in-process ``_finalize_job`` — both reset/restart together).

For future multi-process deployments, the counter MUST be
shared across processes (e.g. via Redis or a DB column on
``dependency_watchers``) so that a ``bus.watch`` in process A
is observable to a ``_finalize_job`` reader in process B.
Until then, multi-process deployments are NOT supported.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, select

from daemon.repositories.dependency_bus import (
    DependencyWatcher,
    DependencyWatcherRepository,
    DependencyWatcherState,
)

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# Public dataclasses
# -------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """The terminal outcome of a source task.

    Produced by the task processor when a task reaches a terminal
    event (success / failure / cancellation) and passed to
    :meth:`DependencyBus.emit_terminal`. The bus only uses ``status``
    for structured logging — the FollowUp delivery itself is
    status-agnostic (the FollowUp is always fired regardless of
    success or failure, because the parent needs to know about
    both outcomes).

    Attributes:
        status: Terminal status string. One of ``"completed"``,
            ``"error"``, ``"terminated"``.
        error: Optional error message when ``status == "error"``.
            Forwarded to the FollowUp payload for diagnostic context.
        summary: Optional human-readable summary of the terminal
            event. Forwarded to the FollowUp payload.
    """

    status: str
    error: str | None = None
    summary: str | None = None


@dataclass(frozen=True)
class FollowUp:
    """A deferred action to enqueue when a watched source task terminates.

    A watcher is created when a parent instance sends a message to a
    child (via ``send_message``). The FollowUp encodes the message
    that should be enqueued back onto the parent's task queue when
    the child reaches a terminal event.

    The dataclass is frozen so a FollowUp cannot be mutated after
    construction — the bus caches FollowUps in memory and returns
    them to callers; mutation would break the cache contract.

    Attributes:
        target_instance_id: The parent instance ID to notify when
            the watched source task terminates.
        message: Pre-built follow-up message content. The caller
            (``send_message``) constructs this before calling
            :meth:`DependencyBus.watch`; the bus does not interpret
            it.
        source: Origin tag for the enqueued task. Defaults to
            ``"dependency_bus"`` so the receiver can distinguish bus-
            delivered messages from direct sends.
        metadata: Opaque dict for diagnostic / audit context
            (``child_id``, call-site stack frames, etc.). Must
            survive the JSON round-trip — see :meth:`to_payload`.
    """

    target_instance_id: str
    message: str
    source: str = "dependency_bus"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict for DB storage.

        Round-trips through ``json.loads(json.dumps(...))`` to
        guarantee JSON compatibility (no ``datetime``, ``set``, or
        other non-serializable types) and to produce a deep copy of
        the metadata dict so subsequent mutations to the caller's
        dict cannot leak into the stored payload.

        The result is stored in the ``follow_up_payload`` JSONB
        column, which the DB layer ser/deserializes — but the
        explicit JSON round-trip here is a defensive check at
        serialization time, before the value reaches the DB.

        Returns:
            A JSON-compatible dict representation of this FollowUp.
        """
        return json.loads(
            json.dumps(
                {
                    "target_instance_id": self.target_instance_id,
                    "message": self.message,
                    "source": self.source,
                    "metadata": dict(self.metadata),
                }
            )
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> FollowUp:
        """Deserialize a FollowUp from a dict (typically from JSONB).

        The inverse of :meth:`to_payload`. Tolerates missing
        ``source`` and ``metadata`` keys for forward compatibility
        — a row written by an older bus version may not have those
        fields, and a newer bus reading it should still produce a
        valid FollowUp.

        Args:
            payload: Dict deserialized from the ``follow_up_payload``
                JSONB column.

        Returns:
            A new FollowUp instance.
        """
        return cls(
            target_instance_id=payload["target_instance_id"],
            message=payload["message"],
            source=payload.get("source", "dependency_bus"),
            metadata=payload.get("metadata", {}),
        )


# -------------------------------------------------------------------------
# Cross-thread bus cancellation helper
# -------------------------------------------------------------------------


async def cancel_bus_watchers_for_task_async(
    cancelled_task_id: int | str,
    retry_task_id: int | str | None = None,
    origin: str = "unspecified",
    bus: "DependencyBus | None" = None,
) -> int:
    """Cancel all PENDING bus watchers for ``cancelled_task_id`` (async).

    Shared helper used by ``StaleTaskRecovery`` (running on its own
    ``threading.Thread`` background loop) and ``WorkerPool._handle_cancellation``
    (running on a worker thread), both of which need to release bus
    watchers when a task is force-cancelled and a retry is scheduled.

    This consolidates the two near-identical bridges that previously
    lived in ``manager._on_stale_task_cancelled_and_retried`` and
    ``worker_pool.Worker._cancel_bus_watchers_for_task``. The single
    helper guarantees both callsites agree on:

    * the bus singleton lookup (``get_dependency_bus()`` by default;
      ``bus=`` parameter overrides for tests that wire a bus instance
      directly without registering the singleton),
    * the no-bus warning copy,
    * the ``cancel_for_source`` call shape,
    * the success / failure log lines.

    The ``origin`` tag distinguishes the call site in the log line —
    useful for debugging which code path is responsible for a given
    cancellation when both run during a single restart cycle.

    The helper does NOT bridge the thread hop itself — callers from
    sync threads must wrap the call in
    ``MainLoopBridge.run_async_no_wait(...)``. ``run_async_no_wait``
    closes the coroutine locally when no event loop is wired (so
    callers don't need to defensively ``coro.close()`` themselves).

    Correctness note — why cancellation is safe even though the retry
    does NOT re-register a fresh bus watcher: retries are scheduled
    internally by ``force_cancel_and_schedule_retry`` /
    ``schedule_retry`` and never re-invoke ``send_message``, so the
    retry task has no FollowUp of its own. Parent completion in the
    retry-succeeded path is satisfied by the child-completion
    post-commit hook in ``child_reports._process_child_completion_and_notify_parent``
    — which routes through ``_emit_terminal_via_bus`` on the
    *retried* message id. The bus-side state this helper releases is
    therefore the ORIGINAL watcher that was waiting on the cancelled
    task id; without releasing it, ``count_pending_for_target(parent)``
    stays > 0 forever and the parent never reaches COMPLETED.

    Args:
        cancelled_task_id: The id of the task that was just cancelled
            (passed as ``str`` because the ``source_task_id`` column
            is VARCHAR; ``int`` is accepted and converted).
        retry_task_id: Optional id of the newly-scheduled retry task.
            Used for log traceability only — does not affect bus state.
        origin: Short tag identifying the call site (e.g.
            ``"stale_recovery"``, ``"worker_pool"``,
            ``"startup_recovery"``).
        bus: Optional explicit :class:`DependencyBus` instance to use.
            When ``None`` (production default), falls back to
            :func:`get_dependency_bus`. The override exists for tests
            that don't register the bus as a singleton via
            :func:`set_dependency_bus`.

    Returns:
        The number of PENDING watchers transitioned to CANCELLED.
        Returns 0 when the bus singleton is missing or has no watchers.
    """
    if bus is None:
        from .dependency_bus import get_dependency_bus as _get_bus
        bus = _get_bus()
    if bus is None:
        logger.warning(
            f"bus singleton is None (origin={origin}) — cannot cancel "
            f"watchers for cancelled task {cancelled_task_id}; parent may "
            f"stay in waiting_children until manual intervention"
        )
        return 0
    try:
        cancelled = await bus.cancel_for_source(str(cancelled_task_id))
        logger.info(
            f"Bus cancel: origin={origin}, cancelled_task={cancelled_task_id}, "
            f"retry_task={retry_task_id}, bus_watchers_cancelled={cancelled}"
        )
        return int(cancelled)
    except Exception as bus_err:
        logger.error(
            f"Failed to cancel bus watchers (origin={origin}) for cancelled "
            f"task {cancelled_task_id} (retry={retry_task_id}): {bus_err}"
        )
        return 0


# -------------------------------------------------------------------------
# DependencyBus service
# -------------------------------------------------------------------------


class DependencyBus:
    """In-process service layer over :class:`DependencyWatcherRepository`.

    See the module docstring for the full architecture overview,
    crash-safety contract, and flag semantics.

    The bus is constructed with a repository (not a raw engine) to
    match the project's dependency-injection pattern: repositories
    own the engine binding, services depend on repositories. This
    makes the bus trivially testable with an in-memory SQLite engine
    and keeps the bus free of engine-config concerns.

    Lifecycle: construct → :meth:`start` (warms cache) → use →
    :meth:`stop` (clears cache, DB state persists for restart).

    All public methods are async and safe to call from the main
    asyncio event loop. DB calls are wrapped in
    ``asyncio.to_thread`` to avoid blocking the event loop, matching
    the project's standard pattern (see ``child_reports.py`` and
    ``error_reporting.py``).
    """

    def __init__(self, repository: DependencyWatcherRepository) -> None:
        """Initialize the bus with a watcher repository.

        Args:
            repository: The :class:`DependencyWatcherRepository`
                bound to the shared SQLAlchemy engine. The bus does
                NOT take the engine directly — it goes through the
                repository for all PENDING scans, but uses the
                repository's ``engine`` attribute directly for the
                cache-warm and recovery queries (which the
                repository's public API doesn't expose — see
                :meth:`_warm_cache` and :meth:`_recover_fired_unsent`).
        """
        self._repo = repository

        # In-memory cache: source_task_id → list of FollowUps.
        # Source of truth is the DB; this is a hot-path read
        # optimization for :meth:`pending_watchers`.
        self._pending: dict[str, list[FollowUp]] = {}

        # Per-source-task asyncio.Lock. Serializes concurrent
        # :meth:`watch` / :meth:`emit_terminal` on the same task.
        # The lock is lazily created and reused — the bus never
        # deletes individual locks (only :meth:`stop` clears the
        # whole dict, on daemon shutdown).
        self._locks: dict[str, asyncio.Lock] = {}
        # Guards lock creation so concurrent first-access on the
        # same task can't race on dict mutation. Held only for the
        # duration of the dict lookup + Lock creation (microseconds).
        self._locks_guard: asyncio.Lock = asyncio.Lock()

        # Per-parent asyncio.Lock (Phase 1, 2026-06-23). Serializes
        # the DB INSERT against a concurrent ``_finalize_job``
        # holding the parent lock. Created lazily via
        # :meth:`_get_parent_lock`; never deleted by individual
        # methods (the whole dict is cleared on :meth:`stop`).
        # Per-task locks above remain the cache serializer.
        self._parent_locks: dict[str, asyncio.Lock] = {}
        # Guards parent lock creation. Same pattern as ``_locks_guard``
        # above: held only for the dict lookup + ``asyncio.Lock()``
        # construction, then released.
        self._parent_locks_guard: asyncio.Lock = asyncio.Lock()

        # Per-parent generation counter (Phase 1, 2026-06-23,
        # extracted from :class:`CorrelationManager`). Monotonic
        # signal bumped by :meth:`watch` BEFORE acquiring the
        # per-parent lock; observed by
        # :meth:`JobFeedbackObserver._finalize_job` (now via
        # :meth:`get_generation`) to detect in-flight registers
        # during finalization and re-arm the job (orphan-race fix).
        #
        # The bump is OUTSIDE any lock (plain dict assignment,
        # atomic in CPython) so it is visible to a reader that
        # holds the per-parent lock — same pattern that
        # :class:`CorrelationManager` previously used. The lock
        # is only needed for the DB INSERT below; the counter is
        # a monotonic signal, not a critical section.
        #
        # Cleared alongside the per-parent locks in :meth:`stop`
        # to avoid unbounded growth across terminate/revive cycles.
        self.generation: dict[str, int] = {}

        # Per-parent error flag (Phase 5, 2026-06-23).
        # Tracks whether ANY child of this parent emitted a
        # terminal event with ``status="error"``. Set by
        # :meth:`emit_terminal` on error outcomes, read by
        # ``JobFeedbackObserver._process_event`` (the sole
        # finalize path after Phase 1) to determine the parent's
        # terminal_status (the conservative rule: any child error
        # → parent "error").
        #
        # Lives on the bus because the bus is the SOLE completion
        # authority after CM removal — the per-parent error signal
        # must be co-located with the per-parent lock and
        # generation counter (all three are per-parent bus state
        # that the observer reads during finalization).
        #
        # Cleared alongside the per-parent locks in :meth:`stop`
        # to avoid unbounded growth across terminate/revive cycles.
        self._parent_errored: dict[str, bool] = {}

        # Phase 1 (2026-06-24, report-lane decoupling): parallel
        # per-parent error-message dictionary. Captures the LAST
        # child error text per parent, so the finalize path in
        # ``JobFeedbackObserver._process_event`` can thread a
        # meaningful error message into ``InstanceStatus.ERROR``
        # transitions — the bus is a pure state machine; it does
        # NOT finalize the parent directly (that was the deleted
        # ``ChildReportsService._retrigger_parent_finalize``
        # short-circuit and the source of the orphan-Task bug).
        # Read via :meth:`parent_error_message`; cleared in
        # :meth:`clear_parent_error` after finalize. In-memory only
        # like ``_parent_errored``; same crash-recovery edge case
        # (a crash between child-error and finalize loses the
        # message — the parent finalizes as ``COMPLETED`` instead
        # of ``ERROR``). Acceptable per the plan's "known
        # limitation" — see api.py crash-recovery docstring.
        self._parent_error_message: dict[str, str] = {}

        # Lifecycle flag. Set True by :meth:`start`, False by
        # :meth:`stop`. Public methods are tolerant of being called
        # outside the started/stopped window (e.g. :meth:`watch`
        # before :meth:`start` will just insert without warming the
        # cache — the next :meth:`pending_watchers` call will fall
        # back to DB).
        self._running: bool = False

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def watch(
        self, source_task_id: str, follow_up: FollowUp
    ) -> None:
        """Register a FollowUp to fire when ``source_task_id`` terminates.

        Called from ``send_message`` when a parent registers as a
        watcher of a child's task. Writes a ``dependency_watchers``
        row in PENDING state and updates the in-memory cache.

        The DB row is the source of truth; the cache is rebuilt on
        :meth:`start` from a DB scan. A ``watch`` that lands after
        :meth:`start` (and so is not in the warm-cache snapshot) is
        still visible to :meth:`pending_watchers` via the cache
        update below.

        Concurrency: serialized per ``source_task_id`` by the
        per-task lock. Concurrent watches for the same task are
        applied sequentially (each gets its own DB row, each
        appends to the cache list). Concurrent watches for
        *different* tasks proceed in parallel.

        Args:
            source_task_id: The child task id whose terminal event
                will fire this watcher.
            follow_up: The FollowUp to deliver to the parent when
                the child terminates. The bus serializes it via
                :meth:`FollowUp.to_payload` and stores the result
                in the JSONB ``follow_up_payload`` column.
        """
        # Phase 1 (2026-06-23): generation counter and per-parent
        # locking moved from :class:`CorrelationManager` onto the
        # bus. The counter bump is OUTSIDE any lock (plain dict
        # assignment, atomic in CPython) so it is visible to a
        # concurrent reader holding the per-parent lock. The
        # per-parent lock wraps ONLY the DB INSERT. The per-task
        # lock (below) wraps ONLY the cache update. Locks are
        # sequential, never nested — see ``_get_parent_lock``
        # docstring for the deadlock-cycle analysis.
        _parent_id = follow_up.target_instance_id
        payload = follow_up.to_payload()
        watcher = DependencyWatcher(
            source_task_id=source_task_id,
            target_instance_id=follow_up.target_instance_id,
            follow_up_payload=payload,
        )

        # Bump the per-parent generation counter BEFORE acquiring
        # the per-parent lock. The bump is a plain dict assignment
        # — atomic in CPython, no extra lock needed — and is
        # visible to any reader that holds the per-parent lock
        # (i.e. ``_finalize_job`` reading post-gen after the lock
        # release sees the bump that a concurrent ``watch`` made
        # while it was blocked on the lock). If the generation
        # changed during finalization, the observer re-arms the
        # job (COMPLETED → PROCESSING) so the late child's resolve
        # can find a PROCESSING job.
        #
        # The bump is intentionally outside the lock so it is
        # observable by readers that hold the lock. The lock is
        # only needed for the DB INSERT below; the generation
        # counter is a monotonic signal, not a critical section.
        self.increment_generation(_parent_id)
        # Per-parent lock — serializes the DB INSERT against a
        # concurrent ``_finalize_job`` that holds the same lock
        # (CM-style per-parent critical section). The lock is held
        # only for the ``asyncio.to_thread`` INSERT and is released
        # before the per-task lock is acquired below.
        async with await self._get_parent_lock(_parent_id):
            # Per-parent lock held — INSERT the watcher row. The
            # worker thread that ``asyncio.to_thread`` runs in NEVER
            # acquires the lock (asyncio.Lock is event-loop-bound);
            # it only runs the sync SQLAlchemy INSERT while the GIL
            # is released during I/O.
            await asyncio.to_thread(self._repo.insert, watcher)

        # Per-source-task lock — protects the in-memory cache from
        # concurrent ``watch`` / ``emit_terminal`` races for the
        # same ``source_task_id``. The DB INSERT above already
        # committed (under the per-parent lock); the cache update
        # is independent of the DB and only needs to serialize
        # against ``emit_terminal`` (which removes the cache entry
        # for this task_id on terminal emit). Keeping the cache
        # update inside the task lock preserves the original
        # cache-isolation contract.
        lock = await self._get_lock(source_task_id)
        async with lock:
            # Update cache. Append, don't replace — multiple
            # watches on the same source_task_id (siblings
            # watching the same child) all land here and all must
            # be returned by :meth:`pending_watchers`.
            if source_task_id not in self._pending:
                self._pending[source_task_id] = []
            self._pending[source_task_id].append(follow_up)

            logger.debug(
                f"bus watch: source_task_id={source_task_id[:8]}, "
                f"target={follow_up.target_instance_id[:8]}, "
                f"watch_id={watcher.watch_id[:8]}",
                extra={"completion_delivery_path": "bus"},
            )

    async def emit_terminal(
        self, task_id: str, outcome: Outcome
    ) -> list[FollowUp]:
        """Fire all PENDING watchers for ``task_id`` and return them.

        Called from the task processor when a task reaches a
        terminal event. Atomically transitions every PENDING
        watcher for ``task_id`` to FIRED, one at a time, and
        returns the list of fired FollowUps so the caller can
        enqueue them as new tasks.

        **Backpressure primitive**: because
        :meth:`DependencyWatcherRepository.transition_state` is a
        guarded ``WHERE state = 'PENDING'`` UPDATE, only one
        caller can fire a given watcher. A second concurrent
        ``emit_terminal`` for the same task will see ``rowcount == 0``
        for every watcher (they're all already FIRED) and return
        ``[]`` — the FollowUps are delivered exactly once.

        **Sequential, not parallel**: the transition loop awaits
        each ``transition_state`` individually (via
        ``asyncio.to_thread``), so the DB sees a sequence of
        single-row UPDATEs, not a batched write. This is the
        "no thundering herd" guarantee: even if a task has 50
        watchers, the loop is serialized and each transition is
        its own short DB write. The per-task lock ensures the
        entire emit is atomic with respect to concurrent
        :meth:`watch` calls on the same task.

        **DB is source of truth**: the loop reads PENDING watchers
        from the DB (not the cache) so a ``watch`` from another
        process or a cache eviction can't cause a missed fire.
        The cache is updated after the loop (removed for this
        ``task_id`` — all watchers fired).

        **Crash safety**: if the process crashes mid-loop, only
        the already-committed FIRED transitions are persisted.
        On restart, :meth:`_recover_fired_unsent` loads the FIRED
        rows so the caller can re-enqueue them. The bus itself
        does NOT auto-replay — the caller is responsible for the
        FollowUp enqueueing (separation of concerns).

        Args:
            task_id: The child task id that just terminated.
            outcome: The terminal outcome. Currently logged but
                not used to filter watchers — all PENDING watchers
                fire regardless of success/failure, because the
                parent needs to know about both outcomes.

        Returns:
            The list of FollowUps that were atomically transitioned
            from PENDING to FIRED. The caller should enqueue each
            as a new task on the parent's queue. Empty list if no
            PENDING watchers existed (the common case for
            fire-and-forget children).
        """
        lock = await self._get_lock(task_id)
        fired: list[FollowUp] = []
        async with lock:
            # Read PENDING watchers from DB — source of truth.
            # A cache read would be cheaper but would miss any
            # watch() that landed from another process (future
            # multi-process) or any cache eviction since start().
            # Must come BEFORE the per-parent error tracking below:
            # the error block iterates over ``pending_rows`` to
            # compute the unique target ids that should be flipped
            # to ``_parent_errored=True`` (Phase 5: "any error →
            # error" conservative rule from the old CM).
            pending_rows = await asyncio.to_thread(
                self._repo.fetch_pending_for_source, task_id
            )

            # Phase 5 (2026-06-23): record per-parent error signal
            # BEFORE the transition loop so a parent whose last
            # child errored is correctly finalized as ``"error"``.
            # ``_parent_errored[target_id]`` is sticky across
            # multiple children (any error flips it to True) and
            # is read by
            # ``JobFeedbackObserver._process_event`` (the sole
            # finalize path after Phase 1) when
            # ``count_pending_for_target() == 0`` for that target.
            # Mirrors the old CM ``_determine_terminal_status``
            # "any error → error" rule that was lost when CM was
            # removed in Phase 5.
            if outcome.status == "error":
                # Collect the unique target ids of the watchers
                # we're about to fire, then mark each as errored.
                # Using a set avoids redundant dict writes when
                # multiple watchers share the same parent.
                errored_targets = {
                    FollowUp.from_payload(r.follow_up_payload).target_instance_id
                    for r in pending_rows
                }
                for tgt in errored_targets:
                    self._parent_errored[tgt] = True
                    # Phase 1 (2026-06-24): capture the LAST child
                    # error text per parent. ``outcome.error`` is the
                    # authoritative source from the bus terminal
                    # emit; we use a non-empty fallback ("child
                    # agent error") when the outcome carries no
                    # message so the finalize path always has a
                    # non-None string for ``InstanceStatus.ERROR``.
                    if outcome.error:
                        self._parent_error_message[tgt] = outcome.error
                    else:
                        self._parent_error_message.setdefault(
                            tgt, "child agent error"
                        )

            if not pending_rows:
                logger.debug(
                    f"bus emit_terminal: task_id={task_id[:8]}, "
                    f"outcome={outcome.status}, no pending watchers",
                    extra={"completion_delivery_path": "bus"},
                )
                return fired

            fired_at = self._now_iso()
            fired_state = DependencyWatcherState.FIRED.value

            for row in pending_rows:
                # One-at-a-time, sequential DB write. The guard
                # in transition_state ensures exactly-once: a
                # second concurrent emit_terminal for the same
                # task will see rowcount==0 for every row (they're
                # all already FIRED by this loop) and return [].
                transitioned = await asyncio.to_thread(
                    self._repo.transition_state,
                    row.watch_id,
                    fired_state,
                    fired_at,
                )
                if transitioned:
                    fu = FollowUp.from_payload(row.follow_up_payload)
                    fired.append(fu)
                    logger.debug(
                        f"bus emit_terminal fired: task_id={task_id[:8]}, "
                        f"watch_id={row.watch_id[:8]}, "
                        f"target={fu.target_instance_id[:8]}, "
                        f"outcome={outcome.status}",
                        extra={"completion_delivery_path": "bus"},
                    )
                else:
                    # rowcount == 0 — another caller already fired
                    # this watcher. Skip without delivering the
                    # FollowUp (exactly-once delivery is the
                    # backpressure guarantee).
                    logger.debug(
                        f"bus emit_terminal: watch_id={row.watch_id[:8]} "
                        f"already fired (skipped, no double-deliver)",
                        extra={"completion_delivery_path": "bus"},
                    )

            # Remove the task_id from the cache. All its PENDING
            # watchers are now FIRED (or were already FIRED by a
            # concurrent emit), so the cache entry is fully
            # consumed. If a new watch() lands for this task after
            # this point, it will re-create the cache entry.
            self._pending.pop(task_id, None)

            logger.info(
                f"bus emit_terminal: task_id={task_id[:8]}, "
                f"outcome={outcome.status}, "
                f"pending_rows={len(pending_rows)}, "
                f"fired={len(fired)}",
                extra={"completion_delivery_path": "bus"},
            )

        return fired

    async def pending_watchers(
        self, source_task_id: str
    ) -> list[FollowUp]:
        """Return the FollowUps currently waiting on ``source_task_id``.

        Reads from the in-memory cache when present; falls back to a
        DB query when the cache is cold (the task was never warmed
        in this process, or its entry was evicted by
        :meth:`emit_terminal`).

        The DB fallback is what makes this method correct after
        process restart: even though :meth:`start` warms the cache
        from PENDING rows at boot, a subsequent ``watch`` from
        another process (or a manual DB insert for recovery) would
        be invisible to the cache but visible to the DB. The
        fallback closes that gap.

        Args:
            source_task_id: The child task id to query.

        Returns:
            List of FollowUps currently PENDING for the given task.
            Empty list if no watchers exist (cache miss + DB
            confirms no rows).
        """
        if source_task_id in self._pending:
            return list(self._pending[source_task_id])

        # Cache miss — fall back to DB. The cache is a best-effort
        # optimization; the DB is authoritative. We do NOT populate
        # the cache here (to avoid racing with emit_terminal's
        # cache removal) — the next watch() or the next
        # pending_watchers() from a different task can warm it.
        rows = await asyncio.to_thread(
            self._repo.fetch_pending_for_source, source_task_id
        )
        return [FollowUp.from_payload(r.follow_up_payload) for r in rows]

    async def count_pending_for_target(
        self, target_instance_id: str
    ) -> int:
        """Async wrapper around the repo's ``count_pending_for_target``.

        Used by async callers (e.g. async completion handlers that
        need to check whether a parent is still waiting on children
        tracked via the bus). The DB call is wrapped in
        ``asyncio.to_thread`` to avoid blocking the event loop,
        matching the project's standard pattern (same as
        :meth:`pending_watchers`).

        Args:
            target_instance_id: The parent instance id to query.

        Returns:
            Non-negative integer count of PENDING watchers for the
            given target instance.
        """
        return await asyncio.to_thread(
            self._repo.count_pending_for_target, target_instance_id
        )

    def count_pending_for_target_sync(
        self, target_instance_id: str
    ) -> int:
        """Sync variant of :meth:`count_pending_for_target`.

        For sync callers — gates running inside ``asyncio.to_thread``
        worker threads (e.g.
        ``child_reports._process_child_completion_db_sync`` and
        ``job_feedback_observer._finalize_job_db_sync``) where an
        ``await`` is impossible. The completion gate is the critical
        reader of pending-children state; without consulting the bus,
        the root-instance completion gate falls through to COMPLETED
        prematurely.

        Mirrors the sync/async API split of the historical
        CorrelationManager (e.g. ``get_pending_count`` was sync,
        ``is_complete`` had both variants): the bus exposes an async
        primary API and a sync convenience API for the rare caller
        inside a worker thread.

        Args:
            target_instance_id: The parent instance id to query.

        Returns:
            Non-negative integer count of PENDING watchers for the
            given target instance.
        """
        return self._repo.count_pending_for_target(target_instance_id)

    async def cancel_for_target(self, target_instance_id: str) -> int:
        """Cancel all PENDING watchers targeting ``target_instance_id``.

        Called when a parent instance is terminated with watchers
        still pending. Transitions matching PENDING rows to
        CANCELLED so the child task's eventual terminal event does
        not deliver a FollowUp into a dead parent.

        The cancellation scan reads PENDING rows from the DB
        (authoritative — a ``watch`` from another process must also
        be cancelled), transitions each one to CANCELLED via the
        guarded ``transition_state`` primitive, and also purges
        matching entries from the in-memory cache.

        Args:
            target_instance_id: The parent instance ID whose
                watchers should be cancelled.

        Returns:
            The number of watchers transitioned from PENDING to
            CANCELLED. Rows that were already FIRED (by a concurrent
            ``emit_terminal`` that won the race) are not counted —
            ``transition_state`` returns ``False`` for non-PENDING
            rows.
        """
        pending_rows = await asyncio.to_thread(
            self._repo.fetch_pending_for_target, target_instance_id
        )

        if not pending_rows:
            logger.debug(
                f"bus cancel_for_target: target={target_instance_id[:8]} "
                f"has no pending watchers",
                extra={"completion_delivery_path": "bus"},
            )
            return 0

        cancelled_state = DependencyWatcherState.CANCELLED.value
        count = 0
        for row in pending_rows:
            transitioned = await asyncio.to_thread(
                self._repo.transition_state,
                row.watch_id,
                cancelled_state,
                None,  # CANCELLED rows do not stamp a fired_at
            )
            if transitioned:
                count += 1

        # Purge matching entries from the cache. The cache is keyed
        # by source_task_id, so we scan all entries and remove
        # FollowUps whose target matches. This is O(n) over the
        # cache but cancellation is rare (only on parent
        # termination), so the cost is acceptable.
        purged = 0
        for source_task_id, fus in list(self._pending.items()):
            remaining = [
                fu
                for fu in fus
                if fu.target_instance_id != target_instance_id
            ]
            if len(remaining) != len(fus):
                purged += len(fus) - len(remaining)
                if remaining:
                    self._pending[source_task_id] = remaining
                else:
                    del self._pending[source_task_id]

        logger.info(
            f"bus cancel_for_target: target={target_instance_id[:8]}, "
            f"cancelled={count}, cache_purged={purged}",
            extra={"completion_delivery_path": "bus"},
        )
        return count

    async def cancel_for_source(self, source_task_id: str) -> int:
        """Cancel all PENDING watchers keyed on ``source_task_id``.

        Called when a child task is force-cancelled and a retry
        task is scheduled to replace it (e.g. ``StaleTaskRecovery``
        step 4+5, ``WorkerPool._handle_cancellation`` timeout-
        retry path, startup crash recovery). Without this, the
        parent-side watcher remains PENDING forever because the
        retry task has a NEW ``source_task_id`` and the bus
        ``emit_terminal`` fired for the retry cannot match the
        original watcher.

        Production incident 2026-06-26 (instance 06f500af stuck in
        ``waiting_children`` for hours): this was the missing
        link between the cancel-and-retry flow and the bus's
        PENDING watcher state. The retry's natural completion
        fired ``emit_terminal(task_id=retry)`` but the watcher
        was registered against the cancelled task's id — the
        watcher stayed PENDING, ``count_pending_for_target``
        stayed > 0, and the leader never reached COMPLETED.

        Correctness note — why cancellation is safe here: retries are
        scheduled internally by ``force_cancel_and_schedule_retry`` /
        ``schedule_retry`` and do NOT re-invoke ``send_message``, so the
        retry task id has no bus watcher of its own. Parent completion
        in the retry-succeeded path is satisfied by the
        child-completion post-commit hook in
        ``child_reports._process_child_completion_and_notify_parent``,
        which routes through ``_emit_terminal_via_bus`` on the
        *retried* message id. Cancelling the ORIGINAL watcher is what
        releases ``count_pending_for_target(parent)`` so the parent
        can transition to COMPLETED. The fix would be unsound if the
        retry relied on the bus to deliver a completion FollowUp — it
        does not, so cancellation here is safe.

        Uses the same guarded ``transition_state`` primitive as
        ``cancel_for_target`` so concurrent ``emit_terminal`` wins
        (PENDING → FIRED) are not overwritten — a FIRED watcher
        that wins the race returns ``False`` from
        ``transition_state`` and is left untouched.

        Args:
            source_task_id: The cancelled child task id whose
                PENDING watchers should be cancelled.

        Returns:
            The number of watchers transitioned from PENDING to
            CANCELLED. Rows that were already FIRED (by a
            concurrent ``emit_terminal`` for the same source) are
            not counted — ``transition_state`` returns ``False``
            for non-PENDING rows.
        """
        pending_rows = await asyncio.to_thread(
            self._repo.fetch_pending_for_source, source_task_id
        )

        if not pending_rows:
            logger.debug(
                f"bus cancel_for_source: source={source_task_id[:8]} "
                f"has no pending watchers",
                extra={"completion_delivery_path": "bus"},
            )
            return 0

        cancelled_state = DependencyWatcherState.CANCELLED.value
        count = 0
        for row in pending_rows:
            transitioned = await asyncio.to_thread(
                self._repo.transition_state,
                row.watch_id,
                cancelled_state,
                None,  # CANCELLED rows do not stamp a fired_at
            )
            if transitioned:
                count += 1

        # The cache is keyed by source_task_id, so the cleanup
        # here is O(1): drop the entire cache entry for this
        # source if it exists.
        #
        # Implementation note — divergence from cancel_for_target:
        # the sibling ``cancel_for_target`` (lines ~768-779) scans
        # every cache entry and filters out individual FollowUps
        # whose target matches. That O(n) scan is needed there
        # because the cache is keyed by source but the cancel key
        # is target — we have no index into the cache for it.
        # Here the cancel key IS the cache key, so an O(1) ``del``
        # is exact. The two methods are intentionally asymmetric
        # for that reason.
        #
        # Race window: a concurrent ``watch(source_task_id, ...)``
        # between the DB fetch above and the ``del`` could land a
        # new PENDING row whose DB-side transition_state has not
        # yet committed (or has committed but the cache update
        # runs after our ``del``). In that case the new row is
        # visible via the DB-backed ``count_pending_for_target`` /
        # ``pending_watchers`` fall-through but missing from the
        # cache. ``emit_terminal`` would then race with
        # ``watch`` per the standard per-source ``asyncio.Lock``
        # contract; the cache miss is harmless because the DB is
        # authoritative. Bounded impact — the source is being
        # cancelled, so the race window is near-zero in practice.
        cache_purged = 0
        if source_task_id in self._pending:
            cache_purged = len(self._pending[source_task_id])
            del self._pending[source_task_id] 

        logger.info(
            f"bus cancel_for_source: source={source_task_id[:8]}, "
            f"cancelled={count}, cache_purged={cache_purged}",
            extra={"completion_delivery_path": "bus"},
        )
        return count

    async def start(self) -> list[tuple[str, FollowUp]]:
        """Warm the in-memory cache from the DB and recover unsent fires.

        Called once at daemon startup, after the repository is
        constructed and the DB is reachable. Three operations:

        1. :meth:`_warm_cache` — load all PENDING rows from the DB
           grouped by ``source_task_id`` into the in-memory cache,
           so :meth:`pending_watchers` hits the cache on the hot
           path instead of going to the DB.
        2. :meth:`_recover_fired_unsent` — load FIRED rows WHERE
           ``enqueued_at IS NULL`` and return them as
           ``(watch_id, FollowUp)`` tuples. The caller is
           responsible for re-enqueueing each FollowUp and calling
           :meth:`mark_enqueued` on success — the bus just
           surfaces the list and stamps the marker. This handles
           the crash-recovery case where the process died after
           transitioning watchers to FIRED but before the caller
           enqueued the FollowUps.

        The ``enqueued_at IS NULL`` filter is the C1 crash-recovery
        dedup marker (2026-06-21): rows stamped in a previous
        process (or in a previous invocation of this same process)
        are skipped, so a restart never double-delivers an already-
        enqueued FollowUp.

        Both queries go through the repository's engine directly
        because the repository's public API doesn't expose
        ``fetch_all_pending`` or ``fetch_all_fired`` — the
        per-source / per-target fetches are the only public
        methods, and both are parameterized by a single key.
        Adding the bulk-fetch methods to the repository is a
        follow-up; the bus is the only caller that needs them and
        it can reach the engine through the repository.

        Returns:
            List of ``(watch_id, FollowUp)`` tuples for FIRED rows
            that have NOT been enqueued yet (caller re-enqueues).
            Empty list on a clean restart.
        """
        self._running = True
        warmed = await self._warm_cache()
        recovered = await self._recover_fired_unsent()
        # Defense-in-depth orphan sweep (Phase 1, 2026-06-27):
        # runs AFTER _warm_cache and _recover_fired_unsent but
        # BEFORE the bus starts processing new events. Any PENDING
        # watchers whose source_task_id no longer corresponds to an
        # active task (i.e. accumulated from a prior crash window
        # where the task was force-cancelled or completed without
        # the bus being notified) are transitioned to CANCELLED
        # here, so the startup window doesn't see parents stuck in
        # waiting_children. Fail-open: a DB error logs a WARNING
        # and startup continues — see _sweep_orphan_watchers
        # docstring for the rationale.
        swept = await self._sweep_orphan_watchers()
        logger.info(
            f"bus start: warmed={warmed} pending watchers, "
            f"recovered={len(recovered)} fired-but-unsent watchers, "
            f"swept={swept} orphan pending watcher(s)",
            extra={"completion_delivery_path": "bus"},
        )
        return recovered

    async def stop(self) -> None:
        """Clear the in-memory cache. DB state persists for restart.

        Called at daemon shutdown. Does NOT touch the DB — the
        watcher rows are the source of truth and survive the
        process boundary. The next :meth:`start` will re-warm the
        cache from the DB.

        Per-task locks are dropped along with the cache; they
        would be re-created lazily on the next
        :meth:`watch` / :meth:`emit_terminal` after restart.
        """
        self._running = False
        cache_size = sum(len(v) for v in self._pending.values())
        self._pending.clear()
        self._locks.clear()
        # Phase 1 (2026-06-23): clear per-parent locks + generation
        # counter alongside the per-task locks so the daemon shutdown
        # path doesn't leak dict entries. Per-parent entries are
        # re-created lazily on the next watch after restart.
        self._parent_locks.clear()
        self.generation.clear()
        # Phase 5 (2026-06-23): clear the per-parent error flag
        # dict alongside the per-parent locks — same rationale
        # (avoid unbounded growth across terminate/revive cycles).
        self._parent_errored.clear()
        # Phase 1 (2026-06-24): also clear the per-parent error
        # message dict — same rationale as the flag dict. Both
        # are in-memory state that must be reset on shutdown so
        # a fresh process starts clean.
        self._parent_error_message.clear()
        logger.info(
            f"bus stop: cleared {cache_size} cached watchers",
            extra={"completion_delivery_path": "bus"},
        )

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    async def _get_lock(self, source_task_id: str) -> asyncio.Lock:
        """Get or create the per-source-task asyncio.Lock.

        Lock creation is guarded by :attr:`_locks_guard` so
        concurrent first-access on the same task can't race on
        the ``_locks`` dict mutation. The guard is held only for
        the dict lookup + ``asyncio.Lock()`` construction
        (microseconds), then released — the returned lock is the
        one the caller holds for the duration of its operation.

        All lock-protected methods MUST run on the main asyncio
        event loop (N3 constraint, same as
        :class:`CorrelationManager`).

        Args:
            source_task_id: The child task id whose lock to get.

        Returns:
            The asyncio.Lock for this source task.
        """
        async with self._locks_guard:
            if source_task_id not in self._locks:
                self._locks[source_task_id] = asyncio.Lock()
            return self._locks[source_task_id]

    async def _get_parent_lock(self, parent_id: str) -> asyncio.Lock:
        """Get or create the per-parent asyncio.Lock (Phase 1, 2026-06-23).

        Lock creation is guarded by :attr:`_parent_locks_guard` so
        concurrent first-access on the same parent can't race on
        the ``_parent_locks`` dict mutation. The guard is held
        only for the dict lookup + ``asyncio.Lock()`` construction
        (microseconds), then released — the returned lock is the
        one the caller holds for the duration of its operation.

        Lock ordering — sequential, NEVER nested:

          * :meth:`watch` acquires the **per-parent** lock (only
            for the DB INSERT), releases it, then acquires the
            **per-task** lock (only for the cache update).
          * No path acquires both locks simultaneously.
          * :meth:`emit_terminal` only takes the per-task lock.
          * The observer's ``_finalize_job`` only takes the
            per-parent lock (via the bus — see Task 1.3).

        This sequence avoids any deadlock cycle: a thread holding
        the per-parent lock never waits for the per-task lock and
        vice versa. ``emit_terminal`` and ``_finalize_job`` are
        serialized on different locks, so they can run
        concurrently.

        Must be called from the main asyncio event loop (N3
        constraint, same as :class:`CorrelationManager`).

        Args:
            parent_id: The parent instance id whose lock to get.

        Returns:
            The asyncio.Lock for this parent.
        """
        async with self._parent_locks_guard:
            if parent_id not in self._parent_locks:
                self._parent_locks[parent_id] = asyncio.Lock()
            return self._parent_locks[parent_id]

    def get_generation(self, parent_id: str) -> int:
        """Return the current per-parent generation counter (Phase 1).

        Monotonic signal bumped by :meth:`watch` BEFORE acquiring
        the per-parent lock. :meth:`JobFeedbackObserver._finalize_job`
        reads this counter before and after its commit; if it
        changed during the critical section, a register was
        in-flight and the job must be re-armed (COMPLETED →
        PROCESSING) so the late child's resolve can find a
        PROCESSING job.

        Extracted from :class:`CorrelationManager` in Phase 1
        (2026-06-23) so the bus no longer needs CM for the
        orphan-race detection. CM's :meth:`get_generation` is now
        a thin passthrough to this method — see
        ``correlation_manager.py`` for the deprecation notice.

        Args:
            parent_id: The parent instance ID.

        Returns:
            The current generation counter, or 0 if the parent
            has no recorded generation (untracked or cleared).
        """
        return self.generation.get(parent_id, 0)

    def increment_generation(self, parent_id: str) -> None:
        """Bump the per-parent generation counter (Phase 1).

        Called from :meth:`watch` BEFORE acquiring the per-parent
        lock. The bump is a plain dict assignment (atomic in
        CPython, no extra lock needed) and is visible to any
        reader that subsequently holds the per-parent lock.

        The bump is intentionally outside the lock so it is
        observable by readers that hold the lock. The lock is
        only needed for the DB INSERT; the generation counter is
        a monotonic signal, not a critical section.

        Args:
            parent_id: The parent instance ID whose counter to
                bump.
        """
        self.generation[parent_id] = self.generation.get(parent_id, 0) + 1

    def had_parent_error(self, parent_id: str) -> bool:
        """Return whether ANY child of ``parent_id`` errored (Phase 5).

        The per-parent error flag is set by :meth:`emit_terminal`
        when a child task emits a terminal event with
        ``status="error"`` and any of its fired FollowUps target
        ``parent_id``. It is sticky — once True, the parent
        finalizes as ``"error"`` regardless of how many subsequent
        children complete normally.

        Mirrors :class:`CorrelationManager._determine_terminal_status`'s
        "any child error → parent error" conservative rule that
        was lost when CM was removed in Phase 5.

        The flag is a plain dict read (atomic in CPython) and is
        safe to call from any context (event loop, worker thread,
        sync gate). No lock is needed — the only writer is the
        :meth:`emit_terminal` transition loop, which is
        serialized per-task by the per-task lock above.

        Args:
            parent_id: The parent instance ID to check.

        Returns:
            True if at least one child of this parent emitted an
            error terminal event; False otherwise (no error
            recorded, or the parent has no recorded state).
        """
        return self._parent_errored.get(parent_id, False)

    def parent_error_message(self, parent_id: str) -> str | None:
        """Return the LAST captured child error message for ``parent_id``.

        Phase 1 (2026-06-24, report-lane decoupling). The
        :attr:`_parent_error_message` dict is populated by
        :meth:`emit_terminal` when a child emits a terminal event
        with ``status="error"``; the same code path that flips
        :meth:`had_parent_error` to True.

        Returns the most-recent non-empty ``outcome.error`` from a
        child, or the ``"child agent error"`` fallback set when
        the outcome carried no message but the status was error.
        Returns ``None`` when no error has been recorded.

        Used by ``JobFeedbackObserver._process_event`` to thread a
        meaningful error message into ``_finalize_job`` when the
        per-parent ``had_parent_error`` flag is True. Replaces the
        message-flow that used to live in the deleted
        ``ChildReportsService._retrigger_parent_finalize`` path.

        Args:
            parent_id: The parent instance ID whose error message
                is being read.

        Returns:
            The last captured child error text, or ``None`` if no
            error has been recorded for this parent.
        """
        return self._parent_error_message.get(parent_id)

    def clear_parent_error(self, parent_id: str) -> None:
        """Clear the per-parent error flag AND error message (Phase 5 / Phase 1).

        Called after a parent has been finalized so the flag does
        not leak into a future revive / re-spawn of the same
        instance id. Without this, a terminated-then-revived
        instance would inherit a sticky ``"error"`` flag from its
        previous incarnation and incorrectly finalize any future
        wave as ``"error"``.

        Phase 1 (2026-06-24): also clears the parallel
        :attr:`_parent_error_message` dict so the revived instance
        does not inherit the previous incarnation's last error
        text. Both clears are idempotent.

        Args:
            parent_id: The parent instance ID whose flag and
                message to clear.
        """
        self._parent_errored.pop(parent_id, None)
        self._parent_error_message.pop(parent_id, None)

    def _now_iso(self) -> str:
        """Return current UTC time as ISO-8601 string.

        Mirrors :meth:`DependencyWatcherRepository._now_iso` so
        timestamps written by the bus and the repository are
        format-compatible. Used as the ``fired_at`` value on
        :meth:`emit_terminal` transitions.
        """
        return datetime.now(timezone.utc).isoformat()

    async def _warm_cache(self) -> int:
        """Load all PENDING rows from the DB into the in-memory cache.

        Called from :meth:`start`. Groups PENDING rows by
        ``source_task_id`` and populates
        ``self._pending[source_task_id]`` with the deserialized
        FollowUps.

        Uses a direct Session/select query (via the repository's
        engine) because the repository's public API only exposes
        per-source / per-target fetches, not a bulk scan. The
        query is a single SELECT with a ``state = 'PENDING'``
        filter — the ``ix_dependency_watchers_source_state``
        composite index covers the ``state`` suffix so the query
        is index-scannable even on a large table.

        Returns:
            The number of PENDING watchers loaded into the cache.
        """
        pending_state = DependencyWatcherState.PENDING.value

        def _load_all_pending() -> list[DependencyWatcher]:
            with Session(self._repo.engine) as session:
                stmt = select(DependencyWatcher).where(
                    DependencyWatcher.state == pending_state
                )
                return list(session.exec(stmt))

        rows = await asyncio.to_thread(_load_all_pending)
        self._pending.clear()
        for row in rows:
            fu = FollowUp.from_payload(row.follow_up_payload)
            self._pending.setdefault(row.source_task_id, []).append(fu)
        return sum(len(v) for v in self._pending.values())

    async def _recover_fired_unsent(self) -> list[tuple[str, FollowUp]]:
        """Load FIRED-but-not-enqueued rows for the caller to re-enqueue.

        **Crash-recovery contract (C1 fix, 2026-06-21)**: if the
        process crashed after transitioning some watchers to FIRED
        but before the caller enqueued the FollowUps, the FIRED
        rows are persisted in the DB with ``enqueued_at IS NULL``
        (the dedup marker). On restart, this method surfaces ONLY
        those un-enqueued rows, paired with their ``watch_id`` so
        the caller can call :meth:`mark_enqueued` after a
        successful enqueue.

        **Caller responsibility**: the bus does NOT auto-replay.
        The caller (the lifespan wiring in
        ``init_dependency_bus``) inspects the returned tuples and
        enqueues each FollowUp as a new task, then calls
        :meth:`mark_enqueued` to stamp the dedup marker. This
        separation keeps the bus narrow (state machine only) and
        makes the caller's enqueueing policy independently
        testable.

        The ``enqueued_at IS NULL`` filter is what makes the
        restart safe: a previously-clean restart (where every FIRED
        row was either enqueued-and-stamped, or never reached
        FIRED at all) returns an empty list, and the caller is a
        no-op. A crash mid-enqueue (rows FIRED, some stamped, some
        not) returns only the un-stamped ones — exactly the rows
        that need re-delivery, no duplicates.

        The bus's own invariant is that
        :meth:`pending_watchers` works correctly after restart by
        reading DB state on cache miss — that guarantee is
        independent of whether the caller chooses to re-enqueue
        FIRED rows.

        Returns:
            List of ``(watch_id, FollowUp)`` tuples for FIRED rows
            where ``enqueued_at IS NULL``. The caller enqueues
            each FollowUp and stamps the row via
            :meth:`mark_enqueued`. Empty list on a clean restart.
        """
        fired_state = DependencyWatcherState.FIRED.value

        def _load_unsent_fired() -> list[DependencyWatcher]:
            with Session(self._repo.engine) as session:
                stmt = select(DependencyWatcher).where(
                    DependencyWatcher.state == fired_state,
                    DependencyWatcher.enqueued_at.is_(None),
                )
                return list(session.exec(stmt))

        rows = await asyncio.to_thread(_load_unsent_fired)
        return [
            (row.watch_id, FollowUp.from_payload(row.follow_up_payload))
            for row in rows
        ]

    async def _sweep_orphan_watchers(self) -> int:
        """Cancel orphan PENDING watchers whose source task is gone.

        Defense-in-depth sweep (Phase 1 of the orphan-watcher
        remediation, 2026-06-27): a PENDING watcher whose
        ``source_task_id`` no longer corresponds to an active
        task is an **orphan** — its FollowUp can never fire (no
        terminal event will ever emit on a missing task id) but
        ``count_pending_for_target(parent)`` keeps it counted as
        pending, blocking the parent from reaching COMPLETED
        forever. This is the production incident pattern recorded
        in commit 06f500af and analyzed in the
        ``cancel_for_source`` docstring.

        The sweep uses a **single atomic conditional UPDATE** —
        NOT a read-then-update loop — to eliminate the TOCTOU
        race window where a concurrent ``emit_terminal`` could
        land between the read and the transition (W2 feedback in
        the plan). The conditional subquery
        ``source_task_id NOT IN (SELECT id FROM task WHERE
        status IN ('running', 'pending', 'paused'))`` filters at
        UPDATE time so a concurrent ``emit_terminal`` that flips
        a task to COMPLETED/FAILED in the same window is
        evaluated atomically by the DB engine.

        **Active-task predicate (the IN-list)** — a task counts
        as "active" when its status is ``running``, ``pending``,
        or ``paused``. The ``paused`` case is INTENTIONAL:
        paused tasks have legitimately PENDING watchers that
        must be preserved for resume semantics (Decision 2 of the
        Pause/Resume redesign — bus watchers survive pause).
        COMPLETED / FAILED / CANCELLED tasks have already emitted
        (or will never emit) their terminal events, so any
        PENDING watchers keyed on those task ids are orphans
        and must be cancelled.

        **State value casing** — ``dependency_watchers.state``
        uses UPPERCASE enum string values (``'PENDING'``,
        ``'FIRED'``, ``'CANCELLED'`` — see
        :class:`DependencyWatcherState`), while ``task.status``
        uses lowercase enum string values (``'pending'``,
        ``'running'``, ``'paused'``, ``'completed'``,
        ``'failed'``, ``'cancelled'`` — see
        :class:`daemon.repositories.task.models.TaskStatus`).
        The mixed casing in the SQL is intentional and matches
        the actual on-disk column values; a single case style
        would silently match zero rows.

        **Fail-open** — startup sweep is best-effort
        defense-in-depth. A DB error during the sweep MUST NOT
        crash the daemon startup (the bus is already wired with
        ``_warm_cache`` + ``_recover_fired_unsent`` — losing the
        sweep just leaves the existing orphan in place; the
        process can still serve requests and a future restart
        will sweep again). On error the method logs a WARNING
        and returns 0.

        The sweep is called from :meth:`start` AFTER
        :meth:`_warm_cache` and :meth:`_recover_fired_unsent`
        complete and BEFORE the bus starts processing new events
        — so orphans cleaned here do not interfere with the
        cache snapshot (the cache is read-from-DB next, not
        populated here).

        Returns:
            The number of orphan PENDING watchers transitioned
            to CANCELLED. Returns 0 when no orphans exist (the
            common case) or when the sweep fails (the DB error
            is logged and startup continues).
        """
        cancelled_state = DependencyWatcherState.CANCELLED.value
        pending_state = DependencyWatcherState.PENDING.value
        fired_at_iso = self._now_iso()

        def _sweep_atomic() -> int:
            """Atomic conditional UPDATE — dialect-portable (SQLite + PG).

            Uses ``sqlalchemy.text()`` with bound parameters
            (``:cancelled_state``,``:pending_state``,``:now``) so the
            runtime substitutes the correct bind-param syntax for the
            active dialect (``?`` for SQLite, ``$1``/``$2``/``$3`` for
            PostgreSQL via psycopg/asyncpg). The IN-list for active
            statuses is intentionally embedded as a string literal
            (no user input flows through it) — this avoids having to
            bind a variable number of params and keeps the query
            plan stable.
            """
            stmt = text(
                "UPDATE dependency_watchers "
                "SET state = :cancelled_state, fired_at = :now "
                "WHERE state = :pending_state "
                "AND source_task_id NOT IN ("
                "  SELECT CAST(id AS TEXT) FROM task "
                "  WHERE status IN ('running', 'pending', 'paused')"
                ")"
            )
            with Session(self._repo.engine) as session:
                result = session.execute(
                    stmt,
                    {
                        "cancelled_state": cancelled_state,
                        "pending_state": pending_state,
                        "now": fired_at_iso,
                    },
                )
                session.commit()
                return int(result.rowcount or 0)

        try:
            rowcount = await asyncio.to_thread(_sweep_atomic)
        except Exception as sweep_err:
            # Fail-open: log the error and let startup continue.
            # The orphan watcher(s) will remain in PENDING state
            # and block their parent's completion — same pre-sweep
            # behavior. A future restart will sweep again.
            logger.warning(
                f"sweep_orphan_watchers: DB error during orphan "
                f"sweep (sweep failed, startup continues): {sweep_err}"
            )
            return 0

        if rowcount > 0:
            logger.info(
                f"sweep_orphan_watchers: cancelled {rowcount} orphan "
                f"PENDING watcher(s) (source_task_id no longer "
                f"corresponds to an active task)"
            )
        else:
            logger.debug(
                "sweep_orphan_watchers: no orphan PENDING watchers found"
            )
        return rowcount

    async def mark_enqueued(self, watch_id: str) -> None:
        """Stamp a FIRED watcher as successfully enqueued.

        Crash-recovery dedup marker (C1 fix, 2026-06-21). Called
        by the caller (the lifespan wiring in
        ``init_dependency_bus``, or any equivalent recovery loop)
        AFTER a successful ``manager.enqueue_message(...)`` call
        for a recovered FollowUp. Once stamped, the row will NOT
        be returned by a future :meth:`_recover_fired_unsent` —
        so a subsequent crash and restart will not re-deliver the
        same FollowUp.

        Idempotent: re-stamping an already-stamped row is a
        harmless overwrite of the timestamp.

        Args:
            watch_id: The watcher to stamp. Typically the
                ``watch_id`` from a tuple returned by
                :meth:`start` / :meth:`_recover_fired_unsent`.
        """
        await asyncio.to_thread(
            self._repo.mark_enqueued, watch_id, self._now_iso()
        )

    async def mark_enqueued_by_source_target(
        self,
        source_task_id: str,
        target_instance_id: str,
        enqueued_at: str | None = None,
    ) -> int:
        """Async wrapper around ``repo.mark_enqueued_by_source_target``.

        Mirrors :meth:`mark_enqueued` (the per-watch_id variant) but
        stamps all FIRED rows for a ``(source_task_id, target_instance_id)``
        pair at once. Used by
        :meth:`ChildReportsService._emit_terminal_via_bus` AFTER the
        bus has fired its watchers but BEFORE the report Task has
        claimed the work and driven the parent's terminal lifecycle
        event, so that:

          1. A crash between ``emit_terminal`` and the report Task's
             lifecycle event leaves the row un-stamped — the next
             restart's :meth:`_recover_fired_unsent` will pick it up
             and re-enqueue the FollowUp (correct semantics: we do
             NOT lock the parent out of retry by stamping too early).
          2. A crash between the report Task's finalize and the
             stamp leaves the row un-stamped too — but finalization
             is idempotent (atomic ``WHERE status = PROCESSING``
             UPDATE in :meth:`_finalize_job_db_sync`), so the retry
             is safe.

        The DB call is wrapped in ``asyncio.to_thread`` to avoid
        blocking the event loop, matching the project's standard
        pattern (same as :meth:`pending_watchers` /
        :meth:`count_pending_for_target`).

        Args:
            source_task_id: The child task id whose FIRED rows
                should be stamped. String form.
            target_instance_id: The parent instance id whose FIRED
                rows should be stamped.
            enqueued_at: ISO-8601 timestamp (default: now UTC).
                Optional override for tests; production passes
                ``None`` and lets the repo stamp the current time.

        Returns:
            The number of rows stamped. Informational only.
        """
        return await asyncio.to_thread(
            self._repo.mark_enqueued_by_source_target,
            source_task_id,
            target_instance_id,
            enqueued_at,
        )


# -------------------------------------------------------------------------
# Module-level singleton for dependency injection
# -------------------------------------------------------------------------

_dependency_bus: DependencyBus | None = None


def get_dependency_bus() -> DependencyBus | None:
    """Get the module-level DependencyBus instance.

    Returns:
        The singleton DependencyBus instance, or None if not
        initialized. Callers must treat None as a hard error (bus
        singleton missing — no fallback path).
    """
    return _dependency_bus


def set_dependency_bus(bus: DependencyBus | None) -> None:
    """Set the module-level DependencyBus instance.

    Args:
        bus: The DependencyBus instance, or None to clear.
    """
    global _dependency_bus
    _dependency_bus = bus
    if bus is not None:
        logger.info("DependencyBus registered")
    else:
        logger.info("DependencyBus unregistered")
