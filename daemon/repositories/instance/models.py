"""Instance-related database models (tables).

This module contains the SQLModel table definitions for the Instance entity
and its related junction tables.
"""

from __future__ import annotations

import enum
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, String
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field


class InstanceStatus(str, enum.Enum):
    """Instance status enum."""
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"  # Active but no in-flight work (e.g. awaiting next user input)
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    TERMINATED = "terminated"
    QUEUED = "queued"  # Idle but has queued messages
    WAITING_CHILDREN = "waiting_children"  # Parent waiting for child completion reports
    FAILED = "failed"  # Task-level failure (distinct from instance ERROR)

    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls._value2member_map_


class InstanceHierarchy(SQLModel, table=True):
    """Junction table for instance parent-child hierarchy."""
    __tablename__ = "instance_hierarchy"

    parent_id: str = Field(primary_key=True)
    child_id: str = Field(primary_key=True)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Instance(SQLModel, table=True):
    """SQLModel Instance table - internal ORM representation."""
    __tablename__ = "instances"

    instance_id: str = Field(primary_key=True)
    project_id: str | None = Field(default=None, sa_column=Column("project_id", String, nullable=True))
    agent_id: str = Field(index=True)
    agent_dir: str = Field(index=True)
    agent_name: str | None = Field(default=None, index=True)
    parent_id: str | None = Field(default=None, index=True)
    status: str = Field(default=InstanceStatus.IDLE.value, index=True)
    
    instance_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON)
    )
    
    # Denormalized cache of child instance IDs (stored as JSON string)
    children: str = Field(default="[]")
    # Count of pending child completions
    # REBUILD-ONLY CACHE: This field is written for crash-recovery rebuild only
    # (incremented at send_message, decremented at child completion).
    # Do NOT read it for runtime control-flow decisions —
    # use CorrelationManager.is_complete() / get_pending_count() instead.
    waiting_for: int = Field(default=0)
    # Optimistic locking version
    version: int = Field(default=1)
    # For watchdog timeout detection
    last_activity_at: datetime | None = Field(default=None)
    
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    paused_at: str | None = Field(default=None, index=True)
    
    @property
    def title(self) -> str | None:
        """Extract title from instance_metadata."""
        return self.instance_metadata.get("title") if self.instance_metadata else None
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "instance_id": self.instance_id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "agent_dir": self.agent_dir,
            "agent_name": self.agent_name,
            "parent_id": self.parent_id,
            "status": self.status,
            "title": self.title,
            "metadata": dict(self.instance_metadata) if self.instance_metadata else {},
            "children": self.children if self.children else [],
            "waiting_for": self.waiting_for,
            "version": self.version,
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "paused_at": self.paused_at,
        }
