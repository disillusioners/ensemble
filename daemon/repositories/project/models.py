"""Project-related database models (tables).

This module contains the SQLModel table definitions for the Project entity
and its related junction tables.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field
from pydantic import BaseModel, field_validator


CRITICAL_EXPERIENCE_MAX_ENTRIES = 30


class CriticalExperienceCategory(str, enum.Enum):
    CONVENTION = "convention"
    PATTERN = "pattern"
    RISK = "risk"
    DECISION = "decision"
    CONSTRAINT = "constraint"

class CriticalExperiencePriority(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"

class CriticalExperience(BaseModel):
    """A single critical experience entry for a project."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    source_agent: str = ""
    category: str
    priority: str
    summary: str
    reference: str | None = None

    @field_validator('category')
    @classmethod
    def validate_category(cls, v):
        valid = [e.value for e in CriticalExperienceCategory]
        if v not in valid:
            raise ValueError(f"Invalid category '{v}', must be one of {valid}")
        return v

    @field_validator('priority')
    @classmethod
    def validate_priority(cls, v):
        valid = [e.value for e in CriticalExperiencePriority]
        if v not in valid:
            raise ValueError(f"Invalid priority '{v}', must be one of {valid}")
        return v

    @field_validator('summary')
    @classmethod
    def validate_summary(cls, v):
        if len(v) > 200:
            raise ValueError(f"Summary must be ≤200 chars, got {len(v)}")
        return v

    def to_dict(self) -> dict:
        return self.model_dump()


class ProjectStatus(str, enum.Enum):
    """Project status enum."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    
    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls._value2member_map_


class ProjectType(str, enum.Enum):
    """Project type enum."""
    SOFTWARE = "software"
    DOCUMENTATION = "documentation"
    RESEARCH = "research"
    TASK = "task"
    GENERAL = "general"
    
    @classmethod
    def is_valid(cls, project_type: str) -> bool:
        return project_type in cls._value2member_map_


class HistoryEntryType(str, enum.Enum):
    MILESTONE = "milestone"
    COMMIT = "commit"
    PHASE = "phase"
    BUGFIX = "bugfix"
    DEPLOYMENT = "deployment"
    NOTE = "note"
    CONFIG_CHANGE = "config_change"
    OTHER = "other"


class ProjectTagLink(SQLModel, table=True):
    """Junction table for project-tag many-to-many relationship."""
    __tablename__ = "project_tags"

    project_id: str = Field(foreign_key="projects.project_id", primary_key=True)
    tag: str = Field(primary_key=True)


class ProjectShortnameLink(SQLModel, table=True):
    """Junction table for project-shortname many-to-many relationship."""
    __tablename__ = "project_shortnames"

    project_id: str = Field(foreign_key="projects.project_id", primary_key=True)
    shortname: str = Field(primary_key=True)


class Project(SQLModel, table=True):
    """SQLModel Project table - internal ORM representation."""
    __tablename__ = "projects"

    project_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(index=True, unique=True)
    project_type: str = Field(default="general")
    status: str = Field(default=ProjectStatus.ACTIVE.value)
    
    main_directory: str | None = None
    
    related_directories: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON)
    )
    
    description: str | None = None
    
    job_queue_paused: bool = Field(default=False, description="Whether job queue is paused for this project")
    
    # Use 'project_metadata' to avoid conflict with SQLAlchemy's reserved 'metadata'
    project_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON)
    )
    
    relationships: dict[str, list[str]] = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )

    critical_experience: list[dict] = Field(
        default_factory=list,
        sa_column=Column(JSON)
    )

    creator_instance_id: str | None = None
    creator_agent_id: str | None = None
    
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    
    # Runtime-only attributes (not stored in DB)
    _tags: list[str] = []
    _shortnames: list[str] = []
    
    @property
    def tags(self) -> list[str]:
        return getattr(self, '_tags', [])
    
    @tags.setter
    def tags(self, value: list[str]):
        self._tags = value
    
    @property
    def shortnames(self) -> list[str]:
        return getattr(self, '_shortnames', [])
    
    @shortnames.setter
    def shortnames(self, value: list[str]):
        self._shortnames = value
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "project_type": self.project_type,
            "status": self.status,
            "main_directory": self.main_directory,
            "related_directories": list(self.related_directories),
            "description": self.description,
            "job_queue_paused": self.job_queue_paused,
            "tags": list(self._tags),
            "shortnames": list(self._shortnames),
            "metadata": dict(self.project_metadata),
            "relationships": dict(self.relationships),
            "critical_experience": self.critical_experience if self.critical_experience else [],
            "creator_instance_id": self.creator_instance_id,
            "creator_agent_id": self.creator_agent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ProjectHistoryEntry(SQLModel, table=True):
    """SQLModel ProjectHistoryEntry table - tracks project history entries."""
    __tablename__ = "project_history"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    project_id: str = Field(
        sa_column=Column(String, ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True)
    )
    entry_type: str = Field()
    summary: str = Field(max_length=300)
    details: str | None = Field(default=None, max_length=5000)
    source_agent: str | None = Field(default=None)
    source_instance_id: str | None = Field(default=None)
    entry_metadata: dict | None = Field(
        default=None,
        sa_column=Column(JSON)
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "entry_type": self.entry_type,
            "summary": self.summary,
            "details": self.details,
            "source_agent": self.source_agent,
            "source_instance_id": self.source_instance_id,
            "entry_metadata": self.entry_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
