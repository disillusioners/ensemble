"""Project-related database models (tables).

This module contains the SQLModel table definitions for the Project entity
and its related junction tables.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field


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
        return bool(project_type and project_type.strip())


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
    
    main_directory: Optional[str] = None
    
    related_directories: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON)
    )
    
    description: Optional[str] = None
    
    # Use 'project_metadata' to avoid conflict with SQLAlchemy's reserved 'metadata'
    project_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON)
    )
    
    relationships: dict[str, list[str]] = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )
    
    creator_session_id: Optional[str] = None
    creator_agent_dir: Optional[str] = None
    
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
    
    def to_data(self) -> "ProjectData":
        """Convert ORM model to ProjectData DTO."""
        from .protocol import ProjectData
        return ProjectData(
            project_id=self.project_id,
            name=self.name,
            project_type=self.project_type,
            status=self.status,
            main_directory=self.main_directory,
            related_directories=list(self.related_directories),
            description=self.description,
            tags=list(self._tags),
            shortnames=list(self._shortnames),
            metadata=dict(self.project_metadata),
            relationships=dict(self.relationships),
            creator_session_id=self.creator_session_id,
            creator_agent_dir=self.creator_agent_dir,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
