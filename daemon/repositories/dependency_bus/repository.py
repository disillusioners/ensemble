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

from sqlalchemy import update as sa_update
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

    # --------------------------------------------------------
    # STATE TRANSITION (atomic backpressure primitive)
    # --------------------------------------------------------

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
