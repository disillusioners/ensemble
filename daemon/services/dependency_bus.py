"""DependencyBus: DB-backed parent-waits-for-children service (Phase D).

This is the in-process service layer over the
:class:`~daemon.repositories.dependency_bus.repository.DependencyWatcherRepository`.
It replaces the CorrelationManager's in-memory ``_pending`` dict with
DB-backed watcher rows that survive process restart, gated by the
``USE_DEPENDENCY_BUS`` runtime flag (the flag is checked at the call
sites in ``send_message`` and ``task_processor`` — this class itself
is flag-agnostic and always wired; the flag decides which delivery
path is used).

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

Flag contract
-------------

The ``USE_DEPENDENCY_BUS`` flag is checked at the call sites, NOT
in this class. The class is always wired; the flag decides whether
``send_message`` calls :meth:`watch` and whether the task processor
calls :meth:`emit_terminal`. When the flag is OFF, the bus is
inert (not called) and the legacy CM path is the active delivery
mechanism.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

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
        # C2 fix (orphan race re-opened on bus path): bump the
        # CorrelationManager's per-parent generation counter BEFORE
        # acquiring the per-task lock. Phase A closed the orphan
        # race via this generation counter (see correlation_manager.py
        # L281 — ``register_message_send`` bumps ``_generation``
        # BEFORE its per-parent lock, mirroring this exact pattern).
        # ``_finalize_job`` reads ``pre_gen`` BEFORE acquiring the
        # per-job lock and ``post_gen`` AFTER releasing it; if
        # ``post_gen > pre_gen``, a register was in-flight and the
        # job is re-armed (COMPLETED → PROCESSING) so a late child's
        # resolve can find it.
        #
        # When ``use_dependency_bus=ON``, ``send_message`` calls
        # ``bus.watch()`` instead of ``cm.register_message_send()``,
        # so the CM's bump never happens — and ``_finalize_job``
        # reads ``cm.get_generation()`` which stays unchanged, so
        # the orphan-race re-arm check fails to detect the late
        # register. This bump closes the gap by performing the
        # counter increment here, OUTSIDE the bus lock (the bump is
        # a plain dict assignment — atomic in CPython, no lock
        # needed) and exactly the same way the CM does it on the
        # legacy path. The target is the parent (``follow_up.
        # target_instance_id``) — the instance whose finalization
        # the watcher is registered against.
        #
        # Wrapped in try/except because the CM may be unwired
        # (testing, missing init) — a missing CM is non-fatal on
        # the bus path; the re-arm just won't fire for this watch
        # (same behavior as the pre-fix state, so the bus is no
        # worse off). Logged at debug level to avoid log noise.
        try:
            from .correlation_manager import get_correlation_manager
            cm = get_correlation_manager()
            if cm is not None:
                _parent_id = follow_up.target_instance_id
                cm._generation[_parent_id] = (
                    cm._generation.get(_parent_id, 0) + 1
                )
        except Exception as _gen_bump_err:
            logger.debug(
                f"bus watch: could not bump CM generation for "
                f"target={follow_up.target_instance_id[:8]} "
                f"(CM not wired): {_gen_bump_err}",
                extra={"completion_delivery_path": "bus"},
            )

        lock = await self._get_lock(source_task_id)
        async with lock:
            payload = follow_up.to_payload()
            watcher = DependencyWatcher(
                source_task_id=source_task_id,
                target_instance_id=follow_up.target_instance_id,
                follow_up_payload=payload,
            )
            await asyncio.to_thread(self._repo.insert, watcher)

            # Update cache. Append, don't replace — multiple watches
            # on the same source_task_id (siblings watching the same
            # child) all land here and all must be returned by
            # :meth:`pending_watchers`.
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
            pending_rows = await asyncio.to_thread(
                self._repo.fetch_pending_for_source, task_id
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
        logger.info(
            f"bus start: warmed={warmed} pending watchers, "
            f"recovered={len(recovered)} fired-but-unsent watchers",
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


# -------------------------------------------------------------------------
# Module-level singleton for dependency injection
# -------------------------------------------------------------------------

_dependency_bus: DependencyBus | None = None


def get_dependency_bus() -> DependencyBus | None:
    """Get the module-level DependencyBus instance.

    Returns:
        The singleton DependencyBus instance, or None if not
        initialized. Callers should treat None as "bus not wired
        up — skip hooks" (graceful degradation, same pattern as
        :func:`get_correlation_manager`).
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
