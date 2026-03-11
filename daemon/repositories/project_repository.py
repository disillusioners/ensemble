"""SQLModel-based Project Repository implementation.

This module provides the concrete implementation of ProjectRepositoryProtocol
using SQLModel/SQLAlchemy, with fixes for the flush warning issue.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, delete as sql_delete, insert
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field, Session, select, col

from .protocol import ProjectData, ProjectRepositoryProtocol


# ============================================================
# ENUMS
# ============================================================

class ProjectStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    
    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls._value2member_map_


class ProjectType(str, enum.Enum):
    SOFTWARE = "software"
    DOCUMENTATION = "documentation"
    RESEARCH = "research"
    TASK = "task"
    GENERAL = "general"
    
    @classmethod
    def is_valid(cls, project_type: str) -> bool:
        return bool(project_type and project_type.strip())


# ============================================================
# LINK TABLES (Junction tables for tags and shortnames)
# ============================================================

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


# ============================================================
# PROJECT MODEL (SQLModel table)
# ============================================================

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
    
    def to_data(self) -> ProjectData:
        """Convert ORM model to ProjectData DTO."""
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


# ============================================================
# REPOSITORY IMPLEMENTATION
# ============================================================

class SQLModelProjectRepository(ProjectRepositoryProtocol):
    """SQLModel-based implementation of ProjectRepositoryProtocol.
    
    This implementation fixes the flush warning by using bulk operations
    for junction table syncs instead of add() in a loop.
    
    Key improvements over ProjectStore:
    1. Uses bulk INSERT for tags/shortnames (no flush warning)
    2. Proper transaction management
    3. Returns database-agnostic ProjectData DTOs
    4. Better separation of concerns
    """
    
    def __init__(self, session: Session):
        """Initialize repository with a database session.
        
        Args:
            session: SQLModel/SQLAlchemy session for database operations.
        """
        self.session = session

    # --------------------------------------------------------
    # INTERNAL HELPERS - Using bulk operations to avoid flush warning
    # --------------------------------------------------------

    def _load_tags(self, project_id: str) -> list[str]:
        """Load tags from junction table."""
        links = self.session.exec(
            select(ProjectTagLink).where(ProjectTagLink.project_id == project_id)
        ).all()
        return [link.tag for link in links]

    def _load_shortnames(self, project_id: str) -> list[str]:
        """Load shortnames from junction table."""
        links = self.session.exec(
            select(ProjectShortnameLink).where(ProjectShortnameLink.project_id == project_id)
        ).all()
        return [link.shortname for link in links]

    def _enrich_project(self, project: Project | None) -> ProjectData | None:
        """Load tags/shortnames and convert to ProjectData."""
        if project is None:
            return None
        project.tags = self._load_tags(project.project_id)
        project.shortnames = self._load_shortnames(project.project_id)
        return project.to_data()

    def _enrich_projects(self, projects: list[Project]) -> list[ProjectData]:
        """Load tags/shortnames for multiple projects."""
        result = []
        for p in projects:
            p.tags = self._load_tags(p.project_id)
            p.shortnames = self._load_shortnames(p.project_id)
            result.append(p.to_data())
        return result

    def _sync_tags_bulk(self, project_id: str, tags: list[str]) -> None:
        """Sync tags using bulk operations - NO FLUSH WARNING.
        
        The key fix: We use DELETE + bulk INSERT in a single transaction,
        and commit AFTER both operations are queued, not during the loop.
        """
        # Delete existing tags
        self.session.exec(
            sql_delete(ProjectTagLink).where(ProjectTagLink.project_id == project_id)
        )
        
        # Bulk insert new tags using SQLAlchemy core (not ORM add())
        # This queues the insert without triggering a flush
        if tags:
            self.session.execute(
                insert(ProjectTagLink),
                [{"project_id": project_id, "tag": tag} for tag in tags]
            )
        
        # Single commit after all operations are queued
        self.session.commit()

    def _sync_shortnames_bulk(self, project_id: str, shortnames: list[str]) -> None:
        """Sync shortnames using bulk operations - NO FLUSH WARNING."""
        # Delete existing shortnames
        self.session.exec(
            sql_delete(ProjectShortnameLink).where(ProjectShortnameLink.project_id == project_id)
        )
        
        # Bulk insert new shortnames
        if shortnames:
            self.session.execute(
                insert(ProjectShortnameLink),
                [{"project_id": project_id, "shortname": s} for s in shortnames]
            )
        
        # Single commit after all operations are queued
        self.session.commit()

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

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
        """Create a new project."""
        # Validate
        if not ProjectType.is_valid(project_type):
            raise ValueError(f"Invalid project_type: {project_type}")
        
        existing = self.get_by_name(name)
        if existing:
            raise ValueError(f"Project with name '{name}' already exists")

        now = datetime.utcnow().isoformat()
        project_id = project_id or str(uuid.uuid4())
        tags = tags or []
        shortnames = shortnames or []

        project = Project(
            project_id=project_id,
            name=name,
            project_type=project_type,
            status=ProjectStatus.ACTIVE.value,
            main_directory=main_directory,
            related_directories=related_directories or [],
            description=description,
            project_metadata=metadata or {},
            relationships={},
            creator_session_id=creator_session_id,
            creator_agent_dir=creator_agent_dir,
            created_at=now,
            updated_at=now,
        )

        self.session.add(project)
        self.session.commit()
        self.session.refresh(project)

        # Sync tags/shortnames using bulk operations
        self._sync_tags_bulk(project.project_id, tags)
        self._sync_shortnames_bulk(project.project_id, shortnames)

        return self._enrich_project(project) or project.to_data()

    # --------------------------------------------------------
    # GETTERS
    # --------------------------------------------------------

    def get(self, project_id: str) -> ProjectData | None:
        """Get a project by ID."""
        project = self.session.get(Project, project_id)
        return self._enrich_project(project)

    def get_by_name(self, name: str) -> ProjectData | None:
        """Get a project by name."""
        project = self.session.exec(
            select(Project).where(Project.name == name)
        ).first()
        return self._enrich_project(project)

    def get_by_shortname(self, shortname: str) -> ProjectData | None:
        """Get a project by shortname."""
        stmt = (
            select(Project)
            .join(ProjectShortnameLink)
            .where(ProjectShortnameLink.shortname == shortname)
        )
        project = self.session.exec(stmt).first()
        return self._enrich_project(project)

    def get_by_session(self, session_id: str) -> list[ProjectData]:
        """Get all projects linked to a session."""
        stmt = select(Project).where(
            (Project.creator_session_id == session_id)
            | col(Project.relationships).contains(f'"sessions"')
        )
        projects = list(self.session.exec(stmt))
        result = []
        for p in projects:
            if p.creator_session_id == session_id:
                result.append(p)
            elif "sessions" in p.relationships and session_id in p.relationships.get("sessions", []):
                result.append(p)
        return self._enrich_projects(result)

    def get_by_directory(self, directory: str) -> list[ProjectData]:
        """Get all projects that reference a directory."""
        stmt = select(Project).where(
            (Project.main_directory == directory)
            | col(Project.related_directories).contains(f'"{directory}"')
        )
        projects = list(self.session.exec(stmt))
        result = []
        for p in projects:
            if p.main_directory == directory or directory in p.related_directories:
                result.append(p)
        return self._enrich_projects(result)

    # --------------------------------------------------------
    # LIST
    # --------------------------------------------------------

    def list_projects(
        self,
        status: str | None = None,
        project_type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProjectData]:
        """List projects with optional filters."""
        if tags:
            return self._list_with_tags(status, project_type, tags, limit, offset)

        stmt = select(Project)

        if status:
            stmt = stmt.where(Project.status == status)
        if project_type:
            stmt = stmt.where(Project.project_type == project_type)

        stmt = stmt.order_by(col(Project.updated_at).desc()).offset(offset).limit(limit)
        projects = list(self.session.exec(stmt))
        return self._enrich_projects(projects)

    def _list_with_tags(
        self,
        status: str | None,
        project_type: str | None,
        tags: list[str],
        limit: int,
        offset: int,
    ) -> list[ProjectData]:
        """List projects using junction table for tag filtering."""
        stmt = select(Project)
        
        for tag in tags:
            stmt = stmt.where(
                col(Project.project_id).in_(
                    select(ProjectTagLink.project_id).where(ProjectTagLink.tag == tag)
                )
            )

        if status:
            stmt = stmt.where(Project.status == status)
        if project_type:
            stmt = stmt.where(Project.project_type == project_type)

        stmt = stmt.order_by(col(Project.updated_at).desc()).offset(offset).limit(limit)
        projects = list(self.session.exec(stmt))
        return self._enrich_projects(projects)

    # --------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------

    def search(self, query: str, limit: int = 20) -> list[ProjectData]:
        """Search projects by name, description, or shortnames."""
        stmt = (
            select(Project)
            .join(ProjectShortnameLink, isouter=True)
            .where(
                (col(Project.name).contains(query))
                | (col(Project.description).contains(query))
                | (col(ProjectShortnameLink.shortname).contains(query))
            )
            .distinct()
            .order_by(col(Project.updated_at).desc())
            .limit(limit)
        )
        projects = list(self.session.exec(stmt))
        return self._enrich_projects(projects)

    def match_by_keywords(self, keywords: list[str]) -> ProjectData | None:
        """Find best matching project by keywords."""
        if not keywords:
            return None

        stmt = select(Project).where(Project.status == ProjectStatus.ACTIVE.value)
        projects = list(self.session.exec(stmt))
        projects = self._enrich_projects(projects)

        if not projects:
            return None

        best_project: ProjectData | None = None
        best_score = 0

        for project in projects:
            score = 0
            identifiers = [project.name.lower()] + [s.lower() for s in project.shortnames]

            for keyword in keywords:
                kw = keyword.lower()
                for identifier in identifiers:
                    if kw == identifier:
                        score += 2
                    elif kw in identifier or identifier in kw:
                        score += 1

            if score > best_score:
                best_score = score
                best_project = project

        return best_project if best_score > 0 else None

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    def update(self, project_id: str, **updates) -> ProjectData | None:
        """Update a project's fields."""
        project = self.session.get(Project, project_id)
        if project is None:
            return None

        if 'status' in updates and not ProjectStatus.is_valid(updates['status']):
            raise ValueError(f"Invalid status: {updates['status']}")

        if 'name' in updates and updates['name'] != project.name:
            existing = self.get_by_name(updates['name'])
            if existing:
                raise ValueError(f"Project with name '{updates['name']}' already exists")

        tags_update = updates.pop('tags', None)
        shortnames_update = updates.pop('shortnames', None)

        for key, value in updates.items():
            if hasattr(project, key):
                setattr(project, key, value)

        project.updated_at = datetime.utcnow().isoformat()
        self.session.commit()
        self.session.refresh(project)

        if tags_update is not None:
            self._sync_tags_bulk(project_id, tags_update)
        if shortnames_update is not None:
            self._sync_shortnames_bulk(project_id, shortnames_update)

        return self._enrich_project(project)

    def update_status(self, project_id: str, status: str) -> ProjectData | None:
        """Update project status."""
        return self.update(project_id, status=status)

    # --------------------------------------------------------
    # TAGS
    # --------------------------------------------------------

    def set_tags(self, project_id: str, tags: list[str]) -> ProjectData | None:
        """Replace all tags on a project."""
        return self.update(project_id, tags=tags)

    def add_tag(self, project_id: str, tag: str) -> ProjectData | None:
        """Add a tag to a project."""
        project = self.session.get(Project, project_id)
        if project is None:
            return None

        current_tags = self._load_tags(project_id)
        if tag not in current_tags:
            current_tags.append(tag)
            self._sync_tags_bulk(project_id, current_tags)
            project.updated_at = datetime.utcnow().isoformat()
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)

    def remove_tag(self, project_id: str, tag: str) -> ProjectData | None:
        """Remove a tag from a project."""
        project = self.session.get(Project, project_id)
        if project is None:
            return None

        current_tags = self._load_tags(project_id)
        if tag in current_tags:
            current_tags.remove(tag)
            self._sync_tags_bulk(project_id, current_tags)
            project.updated_at = datetime.utcnow().isoformat()
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)

    # --------------------------------------------------------
    # SHORTNAMES
    # --------------------------------------------------------

    def set_shortnames(self, project_id: str, shortnames: list[str]) -> ProjectData | None:
        """Replace all shortnames on a project."""
        return self.update(project_id, shortnames=shortnames)

    def add_shortname(self, project_id: str, shortname: str) -> ProjectData | None:
        """Add a shortname to a project."""
        project = self.session.get(Project, project_id)
        if project is None:
            return None

        current_shortnames = self._load_shortnames(project_id)
        if shortname not in current_shortnames:
            current_shortnames.append(shortname)
            self._sync_shortnames_bulk(project_id, current_shortnames)
            project.updated_at = datetime.utcnow().isoformat()
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)

    def remove_shortname(self, project_id: str, shortname: str) -> ProjectData | None:
        """Remove a shortname from a project."""
        project = self.session.get(Project, project_id)
        if project is None:
            return None

        current_shortnames = self._load_shortnames(project_id)
        if shortname in current_shortnames:
            current_shortnames.remove(shortname)
            self._sync_shortnames_bulk(project_id, current_shortnames)
            project.updated_at = datetime.utcnow().isoformat()
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)

    # --------------------------------------------------------
    # DIRECTORIES
    # --------------------------------------------------------

    def add_related_directory(self, project_id: str, directory: str) -> ProjectData | None:
        """Add a related directory to a project."""
        project = self.session.get(Project, project_id)
        if project is None:
            return None

        if directory not in project.related_directories:
            project.related_directories.append(directory)
            project.updated_at = datetime.utcnow().isoformat()
            flag_modified(project, "related_directories")
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)

    def remove_related_directory(self, project_id: str, directory: str) -> ProjectData | None:
        """Remove a related directory from a project."""
        project = self.session.get(Project, project_id)
        if project is None:
            return None

        if directory in project.related_directories:
            project.related_directories.remove(directory)
            project.updated_at = datetime.utcnow().isoformat()
            flag_modified(project, "related_directories")
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)

    # --------------------------------------------------------
    # METADATA
    # --------------------------------------------------------

    def set_metadata(self, project_id: str, key: str, value: Any) -> ProjectData | None:
        """Set a metadata key-value pair."""
        project = self.session.get(Project, project_id)
        if project is None:
            return None

        project.project_metadata[key] = value
        project.updated_at = datetime.utcnow().isoformat()
        flag_modified(project, "project_metadata")
        self.session.commit()
        self.session.refresh(project)

        return self._enrich_project(project)

    def delete_metadata(self, project_id: str, key: str) -> ProjectData | None:
        """Delete a metadata key."""
        project = self.session.get(Project, project_id)
        if project is None:
            return None

        project.project_metadata.pop(key, None)
        project.updated_at = datetime.utcnow().isoformat()
        flag_modified(project, "project_metadata")
        self.session.commit()
        self.session.refresh(project)

        return self._enrich_project(project)

    # --------------------------------------------------------
    # RELATIONSHIPS
    # --------------------------------------------------------

    def add_relationship(
        self, project_id: str, entity_type: str, entity_id: str
    ) -> ProjectData | None:
        """Add a relationship to another entity."""
        project = self.session.get(Project, project_id)
        if project is None:
            return None

        if entity_type not in project.relationships:
            project.relationships[entity_type] = []

        if entity_id not in project.relationships[entity_type]:
            project.relationships[entity_type].append(entity_id)
            project.updated_at = datetime.utcnow().isoformat()
            flag_modified(project, "relationships")
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)

    def remove_relationship(
        self, project_id: str, entity_type: str, entity_id: str
    ) -> ProjectData | None:
        """Remove a relationship to another entity."""
        project = self.session.get(Project, project_id)
        if project is None:
            return None

        if entity_type in project.relationships:
            if entity_id in project.relationships[entity_type]:
                project.relationships[entity_type].remove(entity_id)
                project.updated_at = datetime.utcnow().isoformat()
                flag_modified(project, "relationships")
                self.session.commit()
                self.session.refresh(project)

        return self._enrich_project(project)

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(self, project_id: str) -> dict[str, Any]:
        """Delete a project."""
        project = self.session.get(Project, project_id)
        if project is None:
            return {"deleted": False, "project_id": project_id, "error": "Not found"}

        # Delete from junction tables
        self.session.exec(
            sql_delete(ProjectTagLink).where(ProjectTagLink.project_id == project_id)
        )
        self.session.exec(
            sql_delete(ProjectShortnameLink).where(ProjectShortnameLink.project_id == project_id)
        )

        # Delete project
        self.session.delete(project)
        self.session.commit()

        return {
            "deleted": True,
            "project_id": project_id,
            "name": project.name
        }
