"""JobWatcher model for job subscription tracking."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, Index, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field

ALL_TERMINAL_STATES: list[str] = ["completed", "failed", "cancelled", "dead_letter"]


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

    # Foreign key to job_queue_items.job_id
    job_id: str = Field(foreign_key="job_queue_items.job_id", index=True)

    # Foreign key to instances.instance_id
    instance_id: str = Field(foreign_key="instances.instance_id", index=True)

    # JSON list of terminal states to watch for
    # Default includes ALL terminal states including dead_letter
    watch_events: list[str] = Field(
        default_factory=lambda: list(ALL_TERMINAL_STATES),
        sa_column=Column(JSON)
    )

    created_at: datetime = Field(default_factory=datetime.utcnow)
