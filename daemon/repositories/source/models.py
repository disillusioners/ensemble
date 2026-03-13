"""Source-related database models (tables).

This module contains the SQLModel table definitions for SourceConfig,
SessionMapping, and ProcessedMessage entities.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field


class SourceStatus(str, enum.Enum):
    """Source status enum."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"

    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls._value2member_map_


class SourceConfig(SQLModel, table=True):
    """SQLModel SourceConfig table - internal ORM representation."""
    __tablename__ = "source_configs"

    source_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    source_type: str = Field(index=True)
    name: str = Field(index=True)
    
    config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )
    
    credentials: Optional[str] = None
    enabled: bool = Field(default=True)
    status: str = Field(default=SourceStatus.STOPPED.value)
    error_message: Optional[str] = None
    
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "name": self.name,
            "config": dict(self.config),
            "credentials": self.credentials,
            "enabled": self.enabled,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SessionMapping(SQLModel, table=True):
    """SQLModel SessionMapping table - internal ORM representation."""
    __tablename__ = "session_mappings"

    mapping_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    source_id: str = Field(foreign_key="source_configs.source_id", index=True)
    external_user_id: str = Field(index=True)
    agent_session_id: str = Field(index=True)
    agent_dir: str
    
    mapping_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON)
    )
    
    last_message_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "mapping_id": self.mapping_id,
            "source_id": self.source_id,
            "external_user_id": self.external_user_id,
            "agent_session_id": self.agent_session_id,
            "agent_dir": self.agent_dir,
            "metadata": dict(self.mapping_metadata),
            "last_message_at": self.last_message_at,
            "created_at": self.created_at,
        }


class ProcessedMessage(SQLModel, table=True):
    """SQLModel ProcessedMessage table for message deduplication."""
    __tablename__ = "processed_external_messages"

    source_id: str = Field(primary_key=True)
    external_message_id: str = Field(primary_key=True)
    processed_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "source_id": self.source_id,
            "external_message_id": self.external_message_id,
            "processed_at": self.processed_at,
        }


class ScheduleExecution(SQLModel, table=True):
    """SQLModel ScheduleExecution table for tracking scheduler execution history."""
    __tablename__ = "schedule_executions"

    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    schedule_id: str = Field(foreign_key="source_configs.source_id", index=True)
    triggered_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    session_id: Optional[str] = Field(default=None, index=True)
    status: str = Field(default="triggered")  # 'triggered', 'completed', 'failed'
    error_message: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "execution_id": self.execution_id,
            "schedule_id": self.schedule_id,
            "triggered_at": self.triggered_at,
            "session_id": self.session_id,
            "status": self.status,
            "error_message": self.error_message,
            "completed_at": self.completed_at,
        }
