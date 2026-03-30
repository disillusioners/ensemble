"""Session-related database models (tables).

This module contains the SQLModel table definitions for the Session entity
and its related junction tables.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field


class SessionStatus(str, enum.Enum):
    """Session status enum."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    TERMINATED = "terminated"
    
    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls._value2member_map_


class SessionHierarchy(SQLModel, table=True):
    """Junction table for session parent-child hierarchy."""
    __tablename__ = "session_hierarchy"

    parent_id: str = Field(primary_key=True)
    child_id: str = Field(primary_key=True)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Session(SQLModel, table=True):
    """SQLModel Session table - internal ORM representation."""
    __tablename__ = "sessions"

    session_id: str = Field(primary_key=True)
    agent_id: str = Field(index=True)
    agent_dir: str = Field(index=True)
    agent_name: Optional[str] = Field(default=None, index=True)
    parent_id: Optional[str] = Field(default=None, index=True)
    status: str = Field(default=SessionStatus.IDLE.value, index=True)
    
    session_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON)
    )
    
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    
    # Runtime-only attribute (not stored in DB)
    _children: list[str]
    
    def __init__(self, **data):
        super().__init__(**data)
        self._children: list[str] = []
    
    @property
    def children(self) -> list[str]:
        return getattr(self, '_children', [])
    
    @children.setter
    def children(self, value: list[str]):
        self._children = value
    
    @property
    def title(self) -> Optional[str]:
        """Extract title from session_metadata."""
        return self.session_metadata.get("title") if self.session_metadata else None
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "agent_dir": self.agent_dir,
            "agent_name": self.agent_name,
            "parent_id": self.parent_id,
            "status": self.status,
            "title": self.title,
            "metadata": dict(self.session_metadata) if self.session_metadata else {},
            "children": list(self._children),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
