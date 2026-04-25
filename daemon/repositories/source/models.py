"""Source-related database models (tables).

This module contains the SQLModel table definitions for SourceConfig,
InstanceMapping, and ProcessedMessage entities.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Index
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


class ExecutionStatus(str, enum.Enum):
    """Execution status enum for schedule executions."""
    TRIGGERED = "triggered"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    QUEUED = "queued"

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
    
    credentials: str | None = None
    enabled: bool = Field(default=True)
    status: str = Field(default=SourceStatus.STOPPED.value)
    error_message: str | None = None
    
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

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


class InstanceMapping(SQLModel, table=True):
    """SQLModel InstanceMapping table - internal ORM representation."""
    __tablename__ = "instance_mappings"

    mapping_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    source_id: str = Field(foreign_key="source_configs.source_id", index=True)
    external_user_id: str = Field(index=True)
    agent_instance_id: str = Field(index=True)
    agent_id: str
    agent_dir: str
    
    mapping_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("mapping_metadata", JSON)
    )
    
    last_message_at: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "mapping_id": self.mapping_id,
            "source_id": self.source_id,
            "external_user_id": self.external_user_id,
            "agent_instance_id": self.agent_instance_id,
            "agent_id": self.agent_id,
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
    processed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

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
    __table_args__ = (
        Index("idx_schedule_executions_schedule_id_status", "schedule_id", "status"),
    )

    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    schedule_id: str = Field(foreign_key="source_configs.source_id", index=True)
    triggered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    instance_id: str | None = Field(default=None, index=True)
    status: str = Field(default=ExecutionStatus.TRIGGERED.value)  # 'triggered', 'completed', 'failed'
    error_message: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "execution_id": self.execution_id,
            "schedule_id": self.schedule_id,
            "triggered_at": self.triggered_at,
            "instance_id": self.instance_id,
            "status": self.status,
            "error_message": self.error_message,
            "completed_at": self.completed_at,
        }
