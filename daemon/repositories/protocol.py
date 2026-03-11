"""Abstract repository protocol defining database-agnostic contracts.

This module defines the interface that all repository implementations must follow,
enabling easy switching between SQLite, PostgreSQL, and other database backends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
import uuid


@dataclass
class ProjectData:
    """Data transfer object for Project - database-agnostic representation.
    
    This is the canonical representation used throughout the application layer,
    decoupled from any specific ORM or database implementation.
    """
    project_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    project_type: str = "general"
    status: str = "active"
    main_directory: str | None = None
    related_directories: list[str] = field(default_factory=list)
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    shortnames: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    relationships: dict[str, list[str]] = field(default_factory=dict)
    creator_session_id: str | None = None
    creator_agent_dir: str | None = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "project_type": self.project_type,
            "status": self.status,
            "main_directory": self.main_directory,
            "related_directories": self.related_directories,
            "description": self.description,
            "tags": self.tags,
            "shortnames": self.shortnames,
            "metadata": self.metadata,
            "relationships": self.relationships,
            "creator_session_id": self.creator_session_id,
            "creator_agent_dir": self.creator_agent_dir,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@runtime_checkable
class ProjectRepositoryProtocol(Protocol):
    """Protocol defining the contract for Project repositories.
    
    This abstract interface allows the application layer to work with
    projects without knowing the underlying database implementation.
    
    All methods should handle their own transactions via the Unit of Work pattern.
    """
    
    # ==================== CREATE ====================
    
    def create(
        self,
        name: str,
        project_type: str = "general",
        main_directory: str | None = None,
        related_directories: list[str] | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        shortnames: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        project_id: str | None = None,
        creator_session_id: str | None = None,
        creator_agent_dir: str | None = None,
    ) -> ProjectData:
        """Create a new project.
        
        Args:
            name: Project name (must be unique).
            project_type: Type of project.
            main_directory: Primary project directory path.
            related_directories: Additional related directory paths.
            description: Project description.
            tags: List of tags for categorization.
            shortnames: List of alternative short names/nicknames.
            metadata: Type-specific metadata.
            project_id: Optional custom project ID.
            creator_session_id: Session ID that created this project.
            creator_agent_dir: Agent directory of the creator.
        
        Returns:
            The created ProjectData object.
        
        Raises:
            ValueError: If name is duplicate or type is invalid.
        """
        ...
    
    # ==================== READ ====================
    
    def get(self, project_id: str) -> ProjectData | None:
        """Get a project by ID."""
        ...
    
    def get_by_name(self, name: str) -> ProjectData | None:
        """Get a project by name."""
        ...
    
    def get_by_shortname(self, shortname: str) -> ProjectData | None:
        """Get a project by shortname."""
        ...
    
    def get_by_session(self, session_id: str) -> list[ProjectData]:
        """Get all projects linked to a session."""
        ...
    
    def get_by_directory(self, directory: str) -> list[ProjectData]:
        """Get all projects that reference a directory."""
        ...
    
    # ==================== LIST ====================
    
    def list_projects(
        self,
        status: str | None = None,
        project_type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProjectData]:
        """List projects with optional filters."""
        ...
    
    # ==================== SEARCH ====================
    
    def search(self, query: str, limit: int = 20) -> list[ProjectData]:
        """Search projects by name, description, or shortnames."""
        ...
    
    def match_by_keywords(self, keywords: list[str]) -> ProjectData | None:
        """Find best matching project by keywords against name and shortnames."""
        ...
    
    # ==================== UPDATE ====================
    
    def update(self, project_id: str, **updates) -> ProjectData | None:
        """Update a project's fields."""
        ...
    
    def update_status(self, project_id: str, status: str) -> ProjectData | None:
        """Update project status."""
        ...
    
    # ==================== TAGS ====================
    
    def set_tags(self, project_id: str, tags: list[str]) -> ProjectData | None:
        """Replace all tags on a project."""
        ...
    
    def add_tag(self, project_id: str, tag: str) -> ProjectData | None:
        """Add a tag to a project."""
        ...
    
    def remove_tag(self, project_id: str, tag: str) -> ProjectData | None:
        """Remove a tag from a project."""
        ...
    
    # ==================== SHORTNAMES ====================
    
    def set_shortnames(self, project_id: str, shortnames: list[str]) -> ProjectData | None:
        """Replace all shortnames on a project."""
        ...
    
    def add_shortname(self, project_id: str, shortname: str) -> ProjectData | None:
        """Add a shortname to a project."""
        ...
    
    def remove_shortname(self, project_id: str, shortname: str) -> ProjectData | None:
        """Remove a shortname from a project."""
        ...
    
    # ==================== DIRECTORIES ====================
    
    def add_related_directory(self, project_id: str, directory: str) -> ProjectData | None:
        """Add a related directory to a project."""
        ...
    
    def remove_related_directory(self, project_id: str, directory: str) -> ProjectData | None:
        """Remove a related directory from a project."""
        ...
    
    # ==================== METADATA ====================
    
    def set_metadata(self, project_id: str, key: str, value: Any) -> ProjectData | None:
        """Set a metadata key-value pair."""
        ...
    
    def delete_metadata(self, project_id: str, key: str) -> ProjectData | None:
        """Delete a metadata key."""
        ...
    
    # ==================== RELATIONSHIPS ====================
    
    def add_relationship(
        self, project_id: str, entity_type: str, entity_id: str
    ) -> ProjectData | None:
        """Add a relationship to another entity."""
        ...
    
    def remove_relationship(
        self, project_id: str, entity_type: str, entity_id: str
    ) -> ProjectData | None:
        """Remove a relationship to another entity."""
        ...
    
    # ==================== DELETE ====================
    
    def delete(self, project_id: str) -> dict[str, Any]:
        """Delete a project. Returns deletion result."""
        ...
