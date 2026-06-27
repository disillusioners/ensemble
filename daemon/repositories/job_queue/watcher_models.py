"""JobWatcher model for job subscription tracking."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import SQLModel, Field

from daemon.repositories.infra.types import JSONBType

ALL_TERMINAL_STATES: list[str] = ["completed", "failed", "cancelled", "dead_letter"]

# All events a watcher can receive, including non-terminal (progress) events
ALL_WATCHABLE_EVENTS: list[str] = ALL_TERMINAL_STATES + ["in_progress"]


class JobWatcher(SQLModel, table=True):
    """Job watcher - subscribes to job lifecycle events.

    Allows an agent instance to receive notifications when a job reaches
    a terminal state.
    """
    __tablename__ = "job_watchers"
    __table_args__ = (
        UniqueConstraint("job_id", "instance_id", name="uq_job_watchers_job_instance"),
        Index("idx_job_watchers_job_id", "job_id"),
        Index("idx_job_watchers_instance_id", "instance_id"),
    )

    watch_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True
    )

    # Phase 2 (Batch 1) of feature/virtual-job-management-surface:
    # the FK on ``job_id`` was REMOVED so ``job_watchers`` no longer
    # requires a matching ``job_queue_items.job_id`` row to exist. The
    # column is semantically a ``work_id`` (UUID4 string) — a virtual
    # job resolver correlates it with the appropriate Task/JobItem row
    # at notification time, instead of relying on a hard SQL FK that
    # blocked virtual (task-only) work from being watched. The SQLite
    # counterpart lives in
    # ``daemon/migrations/versions/20260627_000002_drop_job_watchers_fk.sql``;
    # the PostgreSQL counterpart lives in
    # ``daemon/manager.py::_ensure_postgres_columns`` (DROP CONSTRAINT).
    job_id: str = Field(index=True)

    # Foreign key to instances.instance_id
    instance_id: str = Field(foreign_key="instances.instance_id", index=True)

    # JSON list of job lifecycle events to watch for.
    # Default includes ALL events (terminal + in_progress progress updates).
    watch_events: list[str] = Field(
        default_factory=lambda: list(ALL_WATCHABLE_EVENTS),
        sa_column=Column(JSONBType)
    )

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
