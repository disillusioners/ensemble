"""SQLModel table definitions for the Dependency Bus (Phase D).

Single table backing the DB-backed parent-waits-for-children
mechanism that replaces the CorrelationManager in-memory pending
map when the ``use_dependency_bus`` flag is ON.

* :class:`DependencyWatcher` — one row per registered FollowUp.
  Inserted by ``send_message`` when a parent calls into a child
  task with a FollowUp payload. Transitioned to FIRED when the
  child emits a terminal event (success/failure), CANCELLED when
  the parent is stopped before the child terminates. The atomic
  state transition is the backpressure primitive that prevents
  double-fire under concurrent terminal events.

The ``follow_up_payload`` and ``metadata`` columns are JSONB
(via :class:`~daemon.repositories.infra.types.JSONBType`) so the
shape can evolve without DDL — the bus is a hot path and the
schema is intentionally narrow (5 indexable columns) plus two
opaque JSON blobs.

GIN indexes for JSONB containment / path queries are intentionally
NOT added here: the bus's hot path is a state machine on the
indexed columns (``source_task_id``, ``target_instance_id``,
``state``), and JSONB lookups would only slow it down. If a future
feature needs JSONB containment on the bus table, add the GIN
index in a follow-up migration and in
``__table_args__`` with ``postgresql_using="gin"`` (the standard
dual-driver pattern — SQLAlchemy silently skips the index on
SQLite, which has no GIN).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Index, String
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


# ============================================================
# Enums
# ============================================================


class DependencyWatcherState(str, enum.Enum):
    """Lifecycle states for a :class:`DependencyWatcher` row.

    * ``PENDING`` — initial state on insert. The parent is still
      waiting for the child task to terminate.
    * ``FIRED`` — the child emitted a terminal event and the
      FollowUp payload has been (or is being) delivered to the
      parent. Terminal — the row will not transition again.
    * ``CANCELLED`` — the parent instance was stopped before the
      child terminated, so the watcher's FollowUp will not fire.
      Terminal — the row will not transition again.

    The state is stored as a TEXT column with these exact string
    values, matching the ``CHECK``-less design used elsewhere in
    the project (the bus application code is the only writer, so
    a CHECK constraint would be belt-and-braces for a non-zero
    CPU cost). Mirror the value strings verbatim in the raw-SQL
    migration (``20260621_000001_create_dependency_watchers.sql``).
    """

    PENDING = "PENDING"
    FIRED = "FIRED"
    CANCELLED = "CANCELLED"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """Return ``True`` iff ``value`` is a known state."""
        return value in cls._value2member_map_


# ============================================================
# Models
# ============================================================


class DependencyWatcher(SQLModel, table=True):
    """A pending parent-waits-for-child registration.

    One row per FollowUp-bearing call. The bus's contract:

    * ``send_message`` INSERTs a row in PENDING state, with
      ``source_task_id`` = child task id and
      ``target_instance_id`` = parent instance id.
    * When the child emits a terminal event, the bus atomically
      transitions matching PENDING rows to FIRED (``fired_at``
      timestamped at the transition time) and delivers the
      FollowUp to the parent.
    * When the parent is stopped before the child terminates, the
      bus atomically transitions its PENDING rows to CANCELLED.

    Attributes:
        watch_id: Primary key. UUID4 by default; callers can
            supply a deterministic value if they need
            idempotency on retry.
        source_task_id: The child task id whose terminal event
            fires this watcher. Indexed together with ``state``
            in the hot-path composite index.
        target_instance_id: The parent instance that registered
            as a watcher. Indexed together with ``state`` in the
            cancellation-scan composite index.
        follow_up_payload: JSONB blob — the FollowUp payload to
            deliver to the parent when the child terminates. The
            shape is owned by the bus service layer; the
            repository stores it opaquely.
        watcher_metadata: JSONB blob for diagnostic / audit
            context (``kind``, ``child_id``, ``parent_id``,
            call-site stack frames, etc.). Named
            ``watcher_metadata`` instead of ``metadata`` because
            ``metadata`` shadows a reserved SQLAlchemy /
            Pydantic attribute on the model and the SQLAlchemy
            ``Row`` class — same precedent as
            :class:`~daemon.repositories.message_queue.models.MessageQueue.message_metadata`
            mapping to the ``metadata`` column.
        created_at: ISO-8601 timestamp, immutable.
        fired_at: ISO-8601 timestamp set on the FIRED transition.
            ``None`` while PENDING or after CANCELLED.
        state: One of :class:`DependencyWatcherState` values.
            Default ``PENDING``. Transitioned atomically by
            :meth:`DependencyWatcherRepository.transition_state`
            (guarded UPDATE — only transitions PENDING rows, so
            a second concurrent terminal event cannot double-fire).
    """

    __tablename__ = "dependency_watchers"
    __table_args__ = (
        # Hot-path lookup: "which parent instances are still
        # waiting on this child task?" Used by the bus on every
        # terminal event emit. The (state) suffix is critical
        # because the vast majority of rows in a long-lived
        # system are FIRED/CANCELLED — without it, every emit
        # would full-scan the source_task_id bucket.
        Index(
            "ix_dependency_watchers_source_state",
            "source_task_id",
            "state",
        ),
        # Cancellation scan: "which child tasks are still
        # pending for this parent instance?" Used by the
        # cancellation service when a parent is stopped. The
        # same state-suffix trade-off applies.
        Index(
            "ix_dependency_watchers_target_state",
            "target_instance_id",
            "state",
        ),
    )

    watch_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    # ``String`` (not ``Text``) for the two indexed FK columns —
    # SQLite and PostgreSQL both index ``String`` more cheaply
    # than ``Text`` (PG stores them as varchar with btree
    # inline; SQLite has no TEXT-vs-VARCHAR distinction but
    # SQLAlchemy still emits the right CREATE TABLE shape).
    source_task_id: str = Field(
        sa_column=Column(String, nullable=False),
        max_length=64,
    )
    target_instance_id: str = Field(
        sa_column=Column(String, nullable=False),
        max_length=64,
    )

    follow_up_payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("follow_up_payload", JSONBType, nullable=False),
    )
    # ``watcher_metadata`` (Python) → ``metadata`` (DB column).
    # The rename avoids the reserved-attribute clash described
    # on the class docstring; the raw-SQL migration uses the
    # column name ``metadata`` so existing ``psql`` / migration
    # tooling sees the same name as the model.
    watcher_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONBType, nullable=False),
    )

    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    fired_at: str | None = Field(default=None)
    state: str = Field(default=DependencyWatcherState.PENDING.value, max_length=16)
