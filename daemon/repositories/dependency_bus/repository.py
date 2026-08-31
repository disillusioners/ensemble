"""SQLModel-based Dependency Bus repository.

Persistence layer for the ``dependency_watchers`` table. Exposes
the four primitives the bus service needs:

* :meth:`insert` — register a new FollowUp (PENDING row).
* :meth:`fetch_pending_for_source` — list the PENDING watchers
  for a given child task id (the hot path: called on every
  terminal-event emit).
* :meth:`fetch_pending_for_target` — list the PENDING watchers
  for a given parent instance id (cancellation scan: called when
  a parent is stopped).
* :meth:`transition_state` — atomically transition a watcher from
  PENDING to FIRED or CANCELLED. The backpressure primitive: only
  PENDING rows can transition, so a second concurrent terminal
  event cannot double-fire a FollowUp.

Design highlights:

* **Dialect-agnostic.** Uses SQLModel / SQLAlchemy uniformly; the
  ``follow_up_payload`` and ``watcher_metadata`` columns are
  declared with :class:`JSONBType` so the same schema works on
  SQLite and PostgreSQL.
* **Atomic state transitions.** :meth:`transition_state` uses a
  guarded Core UPDATE (``WHERE state = 'PENDING'``) — the row
  count tells the caller whether *this* call won the race to
  fire. The Core UPDATE path is the same pattern used by
  ``SQLModelMessageQueueRepository.complete`` /
  ``SQLModelInfraRepository.update_asset`` for the
  ``expected_version`` optimistic-locking path.
* **Engine sharing.** Constructor takes a SQLAlchemy ``Engine``
  only — the shared engine is created once at the
  ``InstanceManager`` level (see
  :func:`daemon.repositories.factory.create_engine_from_config`)
  and passed to every repository to avoid DB lock contention.

The repository is intentionally sync. Sync calls are bridged to
async at the call sites (``asyncio.to_thread``) consistent with
the rest of the project (see ``error_reporting`` and
``child_reports`` for the same pattern). Callers that need
write-pause enforcement wrap their call in a
``WriteGuardSession`` at the service layer — this repository
does not own a ``WritePauseGuard``; the guard is a manager-level
singleton and only the services that need migration-during-write
semantics consult it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, update as sa_update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from .models import DependencyWatcher, DependencyWatcherState

logger = logging.getLogger(__name__)


# Module-level PENDING state literal, captured once to avoid
# re-evaluating the enum on every ``transition_state`` call. Mirrors
# the value declared on :class:`DependencyWatcherState.PENDING` and
# in the raw-SQL migration
# (``20260621_000001_create_dependency_watchers.sql``).
_PENDING_STATE: str = DependencyWatcherState.PENDING.value


class DependencyWatcherRepository:
    """SQLModel-based repository for the ``dependency_watchers`` table.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread`` (the project's standard pattern) or
    invoke from inside the worker thread pool.
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy Engine bound to a SQLite or
                PostgreSQL database. The same engine should be
                shared across all repositories to avoid lock
                contention — see
                :func:`daemon.repositories.factory.create_engine_from_config`.
        """
        self.engine = engine

    # --------------------------------------------------------
    # INTERNAL HELPERS
    # --------------------------------------------------------

    def _now_iso(self) -> str:
        """Return current UTC time as ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def insert(self, watcher: DependencyWatcher) -> None:
        """Insert a new PENDING watcher.

        High-concurrency insert path: ``send_message`` calls this
        on every FollowUp-bearing call. The DB-level primary-key
        UNIQUE constraint on ``watch_id`` (UUID4 by default) is
        the only dedup mechanism — a duplicate watch_id from a
        retried message would raise
        :class:`sqlalchemy.exc.IntegrityError` and the bus
        service is expected to catch and treat as "already
        registered" (idempotent retry). Callers may pass an
        existing ``DependencyWatcher`` instance populated with
        ``state`` and timestamps, or rely on the model defaults
        (``PENDING`` + ``created_at = now``).

        The insert runs in a single bounded Session — the bus
        application layer is expected to wrap multi-row /
        multi-table operations in a ``WriteGuardSession`` for
        write-pause compliance.

        Args:
            watcher: The :class:`DependencyWatcher` to persist.
                ``watch_id`` defaults to a new UUID4; ``state``
                defaults to ``PENDING``; ``created_at`` defaults
                to ``now(UTC)``. ``fired_at`` is left ``None``.
        """
        with Session(self.engine) as session:
            session.add(watcher)
            session.commit()
            session.refresh(watcher)
            logger.debug(
                f"Inserted dependency_watcher: watch_id={watcher.watch_id}, "
                f"source_task_id={watcher.source_task_id}, "
                f"target_instance_id={watcher.target_instance_id}"
            )

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def fetch_pending_for_source(
        self, source_task_id: str
    ) -> list[DependencyWatcher]:
        """Return the PENDING watchers for a given child task id.

        Hot path: called on every terminal-event emit to discover
        which parent instances are still waiting on the just-
        terminated child task. Hits the
        ``(source_task_id, state)`` composite index — the
        state-suffix is critical because the vast majority of
        rows in a long-lived system are FIRED/CANCELLED.

        Args:
            source_task_id: The child task id whose terminal
                event triggered the lookup.

        Returns:
            List of PENDING :class:`DependencyWatcher` rows for
            the given source task. Empty list if none match.
        """
        with Session(self.engine) as session:
            stmt = (
                select(DependencyWatcher)
                .where(DependencyWatcher.source_task_id == source_task_id)
                .where(DependencyWatcher.state == _PENDING_STATE)
            )
            return list(session.exec(stmt))

    def fetch_pending_for_target(
        self, target_instance_id: str
    ) -> list[DependencyWatcher]:
        """Return the PENDING watchers for a given parent instance id.

        Cancellation scan: called by the cancellation service when
        a parent instance is stopped, to decide which child
        tasks are still pending for that parent. Hits the
        ``(target_instance_id, state)`` composite index.

        Args:
            target_instance_id: The parent instance id whose
                PENDING watchers are being inspected.

        Returns:
            List of PENDING :class:`DependencyWatcher` rows for
            the given target instance. Empty list if none match.
        """
        with Session(self.engine) as session:
            stmt = (
                select(DependencyWatcher)
                .where(DependencyWatcher.target_instance_id == target_instance_id)
                .where(DependencyWatcher.state == _PENDING_STATE)
            )
            return list(session.exec(stmt))

    def fetch_pending_for_target_and_child(
        self,
        target_instance_id: str,
        child_instance_id: str,
    ) -> list[DependencyWatcher]:
        """Return PENDING watchers for a (parent, child) instance pair.

        Corrective emit primitive for multi-turn children: matches by
        the parent's ``target_instance_id`` AND the ``child_id`` field
        embedded in the FollowUp payload's ``metadata`` JSON, NOT by
        ``source_task_id``. This is the fallback path that lets a
        parent's watcher fire when the child reaches its terminal
        graph turn on a task id ≠ the task that registered the
        watcher (e.g. Wanderer processing subsequent
        ``PROCESS_REPORT`` tasks rather than its first
        ``process_message`` task).

        Hot-path characteristics: hits the
        ``(target_instance_id, state)`` composite index for the
        parent/state filter, then filters these matches by
        ``child_id`` in memory. Each parent has at most a few
        PENDING watchers (one per running child), so the in-memory
        filter is O(N) over a tiny set and avoids JSON-path query
        dialect branches. GIN indexes on JSONB columns are
        intentionally missing from this table (see
        ``models.DependencyWatcher`` docstring) for the same hot-path
        reason.

        The matched rows are returned as full
        :class:`DependencyWatcher` instances so the caller can pass
        each ``watch_id`` to :meth:`transition_state` and each
        ``source_task_id`` to the bus's per-task lock. The
        ``follow_up_payload`` JSONB has already been deserialized by
        :class:`JSONBType`, so the ``metadata.child_id`` access is
        plain Python dict navigation.

        Args:
            target_instance_id: The parent instance id (the
                watcher's ``target_instance_id``).
            child_instance_id: The child instance id to match
                against ``follow_up_payload``'s
                ``metadata.child_id`` field.

        Returns:
            List of PENDING :class:`DependencyWatcher` rows whose
            ``target_instance_id == target_instance_id`` and whose
            payload's ``metadata.child_id == child_instance_id``.
            Empty list if none match — the common case when the
            task-keyed :meth:`DependencyBus.emit_terminal` already
            fired the matching watcher.
        """
        with Session(self.engine) as session:
            stmt = (
                select(DependencyWatcher)
                .where(
                    DependencyWatcher.target_instance_id == target_instance_id
                )
                .where(DependencyWatcher.state == _PENDING_STATE)
            )
            rows = list(session.exec(stmt))
            matched: list[DependencyWatcher] = []
            for row in rows:
                payload = row.follow_up_payload
                if not isinstance(payload, dict):
                    continue
                meta = payload.get("metadata")
                if not isinstance(meta, dict):
                    continue
                if meta.get("child_id") == child_instance_id:
                    matched.append(row)
            return matched

    def fetch_all_pending(self) -> list[DependencyWatcher]:
        """Return ALL PENDING watchers (unfiltered by source/target).

        Startup sweep primitive: used by
        :meth:`DependencyBus._sweep_orphan_watchers` to find
        orphaned PENDING watchers whose ``source_task_id`` no
        longer corresponds to an active task (orphan watchers
        can otherwise block parent completion forever — see the
        06f500af incident docstring on
        :meth:`DependencyBus.cancel_for_source`).

        Unlike :meth:`fetch_pending_for_source` /
        :meth:`fetch_pending_for_target` (which hit the
        composite indexes for a single key), this is an
        unfiltered full scan filtered by state. The query is
        expected to be called ONCE per daemon startup from
        :meth:`DependencyBus.start`, never on the hot path —
        so the index choice does not matter and the
        ``(state)``-only filter is intentional.

        Returns:
            List of all PENDING :class:`DependencyWatcher` rows
            across every source/target. Empty list when no
            PENDING watchers exist (the common case after a
            clean restart with no in-flight FollowUps).
        """
        with Session(self.engine) as session:
            stmt = select(DependencyWatcher).where(
                DependencyWatcher.state == _PENDING_STATE
            )
            return list(session.exec(stmt))

    def fetch_pending_for_child_instance(
        self, child_instance_id: str
    ) -> list[DependencyWatcher]:
        """Return PENDING watchers registered on a child instance.

        Phase 2 (pause-resume-terminate-tree-fix, task 2.2). Terminate-side
        fire primitive: matches watchers whose
        ``follow_up_payload.metadata.child_id == child_instance_id``
        across EVERY target — the terminated child may have multiple
        waiting parents, none of which the caller knows up front.

        Mirrors :meth:`fetch_pending_for_target_and_child`'s matching
        strategy (in-memory filter over the PENDING set; the
        ``watcher_metadata`` JSONB alternative is driver-dependent by
        design, and ``watch()`` writes no ``watcher_metadata`` — the
        ``child_id`` key lives only in the serialized FollowUp payload).
        The initial scan is the state-only PENDING filter (same shape
        as :meth:`fetch_all_pending`): terminate events are rare and
        the global PENDING set is bounded (one row per live parent↔child
        registration), so a full PENDING scan filtered in memory is the
        canonical driver-neutral path — never a hot-path query.

        The matched rows are returned as full :class:`DependencyWatcher`
        instances so the caller can pass each ``watch_id`` to
        :meth:`transition_state` and each ``source_task_id`` to the
        bus's per-task lock.

        Args:
            child_instance_id: The terminated child instance id to
                match against ``follow_up_payload``'s
                ``metadata.child_id`` field.

        Returns:
            List of PENDING :class:`DependencyWatcher` rows whose
            payload's ``metadata.child_id == child_instance_id``.
            Empty list if none match.
        """
        with Session(self.engine) as session:
            stmt = select(DependencyWatcher).where(
                DependencyWatcher.state == _PENDING_STATE
            )
            rows = list(session.exec(stmt))
            matched: list[DependencyWatcher] = []
            for row in rows:
                payload = row.follow_up_payload
                if not isinstance(payload, dict):
                    continue
                meta = payload.get("metadata")
                if not isinstance(meta, dict):
                    continue
                if meta.get("child_id") == child_instance_id:
                    matched.append(row)
            return matched

    def fetch_child_outcome_for_fired(
        self, child_instance_id: str
    ) -> str | None:
        """Return the ``child_outcome`` metadata value for a child's FIRED rows.

        Phase 2 (pause-resume-terminate-tree-fix, task 2.13 — W4).
        Surfaces the additive outcome marker that
        :meth:`daemon.services.dependency_bus.DependencyBus.fire_for_terminated_target`
        stamps into fired FollowUps so the child-completion path can
        copy it into the ``report_injection`` payload the parent LLM
        reads.

        Matching mirrors :meth:`fetch_pending_for_child_instance`
        (in-memory filter over the FIRED set by
        ``follow_up_payload.metadata.child_id``). Best-effort by
        design: the compact hook may DELETE old stamped rows 60s
        after delivery — a missing row simply means no marker (the
        report content still delivers; the marker is additive).

        Args:
            child_instance_id: The child instance id to look up.

        Returns:
            A ``child_outcome`` metadata value found on a FIRED row
            for the child (e.g. ``"terminated"``), or ``None`` when
            no FIRED row carries the marker.
        """
        fired_state = DependencyWatcherState.FIRED.value
        with Session(self.engine) as session:
            stmt = select(DependencyWatcher).where(
                DependencyWatcher.state == fired_state
            )
            for row in session.exec(stmt):
                payload = row.follow_up_payload
                if not isinstance(payload, dict):
                    continue
                meta = payload.get("metadata")
                if not isinstance(meta, dict):
                    continue
                if meta.get("child_id") != child_instance_id:
                    continue
                value = meta.get("child_outcome")
                if isinstance(value, str) and value:
                    return value
            return None

    def update_follow_up_payload(self, watch_id: str, payload: dict) -> bool:
        """Overwrite a watcher row's ``follow_up_payload`` (fire-time enrichment).

        Phase 2 (task 2.2/2.13): ``fire_for_terminated_target`` stamps
        the additive ``child_outcome`` marker into the FollowUp it
        returns AND persists the enriched payload back onto the FIRED
        row so downstream lookups
        (:meth:`fetch_child_outcome_for_fired`) can surface it to the
        parent-LLM-visible report payload. The base keys are copied
        verbatim — enrichment is additive only.

        Args:
            watch_id: The watcher row to update.
            payload: The full enriched FollowUp payload dict.

        Returns:
            ``True`` iff the row was updated.
        """
        with Session(self.engine) as session:
            stmt = (
                sa_update(DependencyWatcher)
                .where(DependencyWatcher.watch_id == watch_id)
                .values(follow_up_payload=payload)
            )
            result = session.execute(stmt)
            session.commit()
            return bool(result.rowcount)

    def count_pending_for_target(self, target_instance_id: str) -> int:
        """Return the PENDING watcher COUNT for a given parent instance id.

        Cheap hot-path query used by completion gates
        (``child_reports._process_child_completion_db_sync`` and
        ``job_feedback_observer._finalize_job_db_sync``) to decide
        whether a parent instance is still waiting on children under
        the bus path. The bus DB is the
        authoritative source of pending-children truth — and these
        gates MUST consult it to avoid premature completion.

        Returns 0 when no PENDING watchers exist (the common case
        for completed parents). Uses ``func.count()`` which is
        dialect-portable across SQLite and PostgreSQL — no
        SQL-syntax branches needed.

        Implementation note: ``session.scalar()`` is preferred over
        ``session.exec(stmt).scalar_one()`` here because for a
        single-column scalar select (``select(func.count())``),
        ``session.exec`` returns a ``ScalarResult`` (not a
        ``Result``) on SQLAlchemy ≥ 2.0 — and ``ScalarResult`` has
        no ``scalar_one`` method. ``session.scalar()`` short-
        circuits to the underlying scalar value cleanly across
        SQLAlchemy versions, matching how the project uses
        ``session.exec(select(func.count()))`` elsewhere.

        Args:
            target_instance_id: The parent instance id whose
                PENDING watcher count is being computed.

        Returns:
            Integer count of PENDING :class:`DependencyWatcher`
            rows for the given target instance. Always a non-negative
            int (0 when no rows match).
        """
        with Session(self.engine) as session:
            stmt = (
                select(func.count())
                .select_from(DependencyWatcher)
                .where(
                    DependencyWatcher.target_instance_id == target_instance_id
                )
                .where(DependencyWatcher.state == _PENDING_STATE)
            )
            return int(session.scalar(stmt) or 0)

    # --------------------------------------------------------
    # B.S.1-i (wc-wake-report-integrity Wave 2) — same-tx
    # executable declared-waiting predicate source (D2.7
    # CORROBORATING signal).
    # --------------------------------------------------------

    def count_fired_unenqueued_for_parent(
        self,
        session: Session,
        target_instance_id: str,
    ) -> list[dict[str, str]]:
        """Return per-row detail of FIRED ∧ ``enqueued_at IS NULL`` watchers for a parent.

        B.S.1-i (decisions.md C2-D2.7 LOCKED 2026-08-30): the
        declared-waiting predicate's CORROBORATING signal is a
        ``dependency_watchers`` row in FIRED state whose
        ``enqueued_at`` is NULL — the FIRED-but-unenqueued
        inter-report-gap shape. This method exposes the
        per-watcher detail for the predicate's structured return
        (stage iii will use ``watch_id`` to cite the row in the
        adjudication notice).

        **Same-tx contract (B.S.7 binding).** The caller passes
        an already-open session; the predicate runs INSIDE the
        completion transaction so a freshly-stamped watcher (or a
        freshly-FIRED one in stage ii/iii) is visible to the
        predicate without an intermediate ``session.commit()``.
        This method MUST NOT call ``session.commit()`` and MUST
        NOT open a new transaction.

        **Why this row exists (D2.7 LOCKED rationale).** The
        ``enqueued_at`` column is stamped by the bus AFTER the
        FollowUp is enqueued (``models.py:128`` documents the
        semantics: stamped only via the NORMAL path when the
        parent is NOT paused; ``dependency_bus.py:709`` purges
        the cache post-``emit_terminal``). In the inter-report
        gap, a FIRED-but-not-yet-stamped row sits on disk while
        the in-memory ``pending_watchers`` cache (``dependency_bus.py:960-961``)
        is empty for the same source task — exactly the shape
        the predicate must detect. The 60s-grace DELETE
        predicate ``enqueued_at IS NOT NULL`` (the recovery sweep
        contract) is load-bearing for restart survival and is
        preserved verbatim by this method.

        Args:
            session: An open SQLModel/SQLAlchemy ``Session`` on
                the same engine that holds this repository. The
                session is owned by the caller — the method
                MUST NOT commit / rollback / close it.
            target_instance_id: The parent whose corroborating
                obligations to evaluate.

        Returns:
            A list of ``{"watch_id", "source_task_id", "state",
            "fired_at"}`` dicts — one per FIRED ∧
            ``enqueued_at IS NULL`` row for the parent. Empty
            list when no corroborating obligations exist.
        """
        stmt = (
            select(
                DependencyWatcher.watch_id,
                DependencyWatcher.source_task_id,
                DependencyWatcher.state,
                DependencyWatcher.fired_at,
            )
            .where(
                DependencyWatcher.target_instance_id == target_instance_id
            )
            .where(
                DependencyWatcher.state
                == DependencyWatcherState.FIRED.value
            )
            .where(DependencyWatcher.enqueued_at.is_(None))
        )
        rows = list(session.exec(stmt).all())
        return [
            {
                "watch_id": r.watch_id,
                "source_task_id": r.source_task_id,
                "state": r.state,
                "fired_at": r.fired_at,
            }
            for r in rows
        ]

    # --------------------------------------------------------
    # STATE TRANSITION (atomic backpressure primitive)
    # --------------------------------------------------------

    def mark_enqueued_by_source_target(
        self,
        source_task_id: str,
        target_instance_id: str,
        enqueued_at: str | None = None,
    ) -> int:
        """Stamp all FIRED watcher rows for a (source_task, target) tuple as enqueued.

        Used by ``child_reports._emit_terminal_via_bus`` after firing
        the watchers for a (source_task, target) pair. The stamp
        marks the row as "processed" so a future restart's
        :meth:`DependencyBus._recover_fired_unsent` will not re-
        deliver it.

        This is the post-FollowUp-removal equivalent of the old
        ``manager.enqueue_message(...)`` + ``mark_enqueued(...)``
        pair: the bus no longer enqueues messages, but the dedup
        marker still applies — a row that has been processed by
        the finalization path must NOT be re-processed on restart.

        Filters to ``state='FIRED'`` so the stamp is only applied
        to rows the bus actually fired in this terminal event. A
        row in PENDING state (the watcher hasn't fired yet) is
        left untouched, and a row already stamped by a prior
        emit is a harmless re-stamp (idempotent overwrite).

        Args:
            source_task_id: The child task id that just terminated.
                String form (matches the ``source_task_id`` column
                type).
            target_instance_id: The parent instance id that was
                watching this source.
            enqueued_at: ISO-8601 timestamp (default: now UTC).
                Optional override for tests; production passes
                ``None`` and lets the helper stamp the current
                time.

        Returns:
            The number of rows stamped (0 if no FIRED rows
            matched). The return is informational — callers do
            not branch on it.
        """
        if enqueued_at is None:
            enqueued_at = self._now_iso()
        fired_state = DependencyWatcherState.FIRED.value
        with Session(self.engine) as session:
            stmt = (
                sa_update(DependencyWatcher)
                .where(DependencyWatcher.source_task_id == source_task_id)
                .where(DependencyWatcher.target_instance_id == target_instance_id)
                .where(DependencyWatcher.state == fired_state)
                .values(enqueued_at=enqueued_at)
            )
            result = session.execute(stmt)
            session.commit()
            updated = int(result.rowcount or 0)
            logger.debug(
                f"mark_enqueued_by_source_target: source={source_task_id[:8]}, "
                f"target={target_instance_id[:8]}, updated={updated}"
            )
            return updated

    def mark_enqueued(self, watch_id: str, enqueued_at: str) -> bool:
        """Stamp a FIRED watcher as successfully enqueued.

        Crash-recovery dedup marker (C1 fix, 2026-06-21): the bus
        stamps ``enqueued_at`` AFTER a successful
        ``manager.enqueue_message(...)`` call. On restart,
        :meth:`DependencyBus._recover_fired_unsent` filters to
        ``WHERE state='FIRED' AND enqueued_at IS NULL`` — only truly
        un-enqueued rows are re-delivered, so a crash mid-enqueue
        does not produce duplicate deliveries.

        The stamp is unconditional (no state guard) because the bus
        calls this only on rows it has already successfully fired.
        Idempotent: re-stamping an already-stamped row is a harmless
        overwrite of the timestamp.

        Args:
            watch_id: The watcher to stamp. Caller passes the
                ``watch_id`` returned from
                :meth:`DependencyBus._recover_fired_unsent`.
            enqueued_at: ISO-8601 timestamp (typically
                ``self._now_iso()``).

        Returns:
            ``True`` iff the row was updated (existed). ``False`` if
            no row with that ``watch_id`` exists (caller should treat
            this as a stale entry from a prior migration).
        """
        with Session(self.engine) as session:
            stmt = (
                sa_update(DependencyWatcher)
                .where(DependencyWatcher.watch_id == watch_id)
                .values(enqueued_at=enqueued_at)
            )
            result = session.execute(stmt)
            session.commit()
            updated = result.rowcount > 0
            if updated:
                logger.debug(
                    f"Marked dependency_watcher as enqueued: "
                    f"watch_id={watch_id}, enqueued_at={enqueued_at}"
                )
            else:
                logger.debug(
                    f"mark_enqueued no-op (watch_id not found): "
                    f"watch_id={watch_id}"
                )
            return updated

    def transition_state(
        self,
        watch_id: str,
        new_state: str,
        fired_at: str | None = None,
    ) -> bool:
        """Atomically transition a watcher from PENDING to ``new_state``.

        The backpressure primitive for the bus: a single guarded
        Core UPDATE that only fires on rows currently in PENDING
        state. A second concurrent terminal event arriving after
        the first has already fired will see ``rowcount == 0`` and
        return ``False``, so the FollowUp is delivered exactly
        once. This replaces the CorrelationManager's
        in-process ``set.pop`` race-prone pattern with a
        single-statement, dialect-portable atomic transition.

        Implementation: a Core ``UPDATE`` with
        ``WHERE watch_id = :watch_id AND state = 'PENDING'``. The
        Core UPDATE bypasses the ORM session's change tracker and
        the autoincrement hooks, but the state column has no
        version-counter semantics, so this is the right tool.
        Same pattern as the ``expected_version`` path in
        :meth:`SQLModelInfraRepository.update_asset`.

        Args:
            watch_id: The watcher to transition.
            new_state: The target state. Expected to be one of
                ``DependencyWatcherState.FIRED.value`` or
                ``DependencyWatcherState.CANCELLED.value`` — the
                repository does not validate the value, the
                caller is the contract owner.
            fired_at: ISO-8601 timestamp to set on the row
                (typically ``self._now_iso()``). Pass ``None``
                for transitions that do not stamp a fire time
                (e.g. CANCELLED rows where the bus only cares
                about the state change). The column is set
                unconditionally when ``fired_at is not None``
                so a CANCELLED transition can opt out.

        Returns:
            ``True`` iff the row was actually transitioned
            (the WHERE clause matched — the row existed and
            was PENDING). ``False`` if the row does not exist
            OR is already in a terminal state (FIRED /
            CANCELLED). Callers should treat ``False`` as
            "another caller already handled this watch_id" and
            skip the FollowUp delivery.
        """
        set_values: dict[str, Any] = {"state": new_state}
        if fired_at is not None:
            set_values["fired_at"] = fired_at

        with Session(self.engine) as session:
            stmt = (
                sa_update(DependencyWatcher)
                .where(DependencyWatcher.watch_id == watch_id)
                .where(DependencyWatcher.state == _PENDING_STATE)
                .values(**set_values)
            )
            result = session.execute(stmt)
            session.commit()
            transitioned = result.rowcount > 0
            if transitioned:
                logger.debug(
                    f"Transitioned dependency_watcher: "
                    f"watch_id={watch_id}, new_state={new_state}, "
                    f"fired_at={fired_at}"
                )
            else:
                logger.debug(
                    f"transition_state no-op (already terminal or "
                    f"missing): watch_id={watch_id}, requested={new_state}"
                )
            return transitioned
