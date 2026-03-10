"""Project management storage layer using SQLModel."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional, Any

from sqlmodel import SQLModel, Field, Session, select, col
from sqlalchemy import Column, delete as sql_delete
from sqlalchemy.types import JSON


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
        # Allow custom types beyond the predefined ones
        return bool(project_type and project_type.strip())


# ============================================================
# LINK TABLES
# ============================================================

class ProjectTagLink(SQLModel, table=True):
    __tablename__ = "project_tags"

    project_id: str = Field(foreign_key="projects.project_id", primary_key=True)
    tag: str = Field(primary_key=True)


class ProjectShortnameLink(SQLModel, table=True):
    __tablename__ = "project_shortnames"

    project_id: str = Field(foreign_key="projects.project_id", primary_key=True)
    shortname: str = Field(primary_key=True)


# ============================================================
# PROJECT MODEL
# ============================================================

class Project(SQLModel, table=True):
    """SQLModel Project table - internal representation.
    
    Note: tags and shortnames are stored in junction tables and loaded
    via helper methods in ProjectStore to maintain the original interface
    where Project.tags and Project.shortnames are list[str].
    """
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
    # Maps to 'metadata' column in DB, exposed as 'metadata' via to_dict()
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
    
    # These are loaded dynamically, not stored in DB
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


# ============================================================
# STORE
# ============================================================

class ProjectStore:
    """SQLModel-based project storage with CRUD operations.
    
    Maintains the same interface as the original SQLite-based ProjectStore
    to minimize changes for callers.
    """

    def __init__(self, session: Session):
        self.session = session


    # --------------------------------------------------------
    # INTERNAL HELPERS
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

    def _enrich_project(self, project: Project | None) -> Project | None:
        """Load tags and shortnames into a Project object."""
        if project is None:
            return None
        project.tags = self._load_tags(project.project_id)
        project.shortnames = self._load_shortnames(project.project_id)
        return project

    def _enrich_projects(self, projects: list[Project]) -> list[Project]:
        """Load tags and shortnames into multiple Project objects."""
        for p in projects:
            p.tags = self._load_tags(p.project_id)
            p.shortnames = self._load_shortnames(p.project_id)
        return projects

    def _sync_tags(self, project_id: str, tags: list[str]) -> None:
        """Sync tags to the junction table."""
        self.session.exec(
            sql_delete(ProjectTagLink).where(ProjectTagLink.project_id == project_id)
        )
        for tag in tags:
            self.session.add(ProjectTagLink(project_id=project_id, tag=tag))
        self.session.commit()

    def _sync_shortnames(self, project_id: str, shortnames: list[str]) -> None:
        """Sync shortnames to the junction table."""
        self.session.exec(
            sql_delete(ProjectShortnameLink).where(ProjectShortnameLink.project_id == project_id)
        )
        for s in shortnames:
            self.session.add(ProjectShortnameLink(project_id=project_id, shortname=s))
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
    ) -> Project:
        """Create a new project.
        
        Args:
            name: Project name (must be unique).
            project_type: Type of project (software, documentation, research, custom).
            main_directory: Primary project directory path.
            related_directories: Additional related directory paths.
            description: Project description.
            tags: List of tags for categorization.
            shortnames: List of alternative short names/nicknames for the project.
            metadata: Type-specific metadata.
            project_id: Optional custom project ID (auto-generated if None).
            creator_session_id: Session ID that created this project.
            creator_agent_dir: Agent directory of the creator.
        
        Returns:
            The created Project object.
        
        Raises:
            ValueError: If name is duplicate or type is invalid.
        """
        # Validate project_type (allow custom types)
        if not ProjectType.is_valid(project_type):
            raise ValueError(f"Invalid project_type: {project_type}")
        
        # Check for duplicate name
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
            metadata=metadata or {},
            relationships={},
            creator_session_id=creator_session_id,
            creator_agent_dir=creator_agent_dir,
            created_at=now,
            updated_at=now,
        )
        project.tags = tags
        project.shortnames = shortnames

        self.session.add(project)
        self.session.commit()
        self.session.refresh(project)

        self._sync_tags(project.project_id, tags)
        self._sync_shortnames(project.project_id, shortnames)

        return self._enrich_project(project) or project


    # --------------------------------------------------------
    # GETTERS
    # --------------------------------------------------------

    def get(self, project_id: str) -> Project | None:
        """Get a project by ID."""
        project = self.session.get(Project, project_id)
        return self._enrich_project(project)

    def get_by_name(self, name: str) -> Project | None:
        """Get a project by name."""
        project = self.session.exec(
            select(Project).where(Project.name == name)
        ).first()
        return self._enrich_project(project)

    def get_by_shortname(self, shortname: str) -> Project | None:
        """Get a project by shortname."""
        stmt = (
            select(Project)
            .join(ProjectShortnameLink)
            .where(ProjectShortnameLink.shortname == shortname)
        )
        project = self.session.exec(stmt).first()
        return self._enrich_project(project)

    def get_by_session(self, session_id: str) -> list[Project]:
        """Get all projects linked to a session."""
        # Search by creator_session_id or in relationships JSON
        stmt = select(Project).where(
            (Project.creator_session_id == session_id)
            | col(Project.relationships).contains(f'"sessions"')
        )
        projects = list(self.session.exec(stmt))
        # Filter in Python for JSON contains check
        result = []
        for p in projects:
            if p.creator_session_id == session_id:
                result.append(p)
            elif "sessions" in p.relationships and session_id in p.relationships.get("sessions", []):
                result.append(p)
        return self._enrich_projects(result)

    def get_by_directory(self, directory: str) -> list[Project]:
        """Get all projects that reference a directory."""
        stmt = select(Project).where(
            (Project.main_directory == directory)
            | col(Project.related_directories).contains(f'"{directory}"')
        )
        projects = list(self.session.exec(stmt))
        # Filter in Python for JSON array contains
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
    ) -> list[Project]:
        """List projects with optional filters.
        
        Args:
            status: Filter by status.
            project_type: Filter by project type.
            tags: Filter by tags (projects must have ALL specified tags).
            limit: Maximum number of results.
            offset: Offset for pagination.
        
        Returns:
            List of matching Project objects.
        """
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
    ) -> list[Project]:
        """List projects using junction table for tag filtering."""
        # Build query with JOINs for each tag
        # Projects must have ALL specified tags
        stmt = select(Project)
        
        for tag in tags:
            alias = ProjectTagLink  # For simplicity, we'll use subqueries
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

    def search(self, query: str, limit: int = 20) -> list[Project]:
        """Search projects by name, description, or shortnames."""
        # Use DISTINCT to avoid duplicates from JOIN
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

    def match_by_keywords(self, keywords: list[str]) -> Project | None:
        """Find best matching project by keywords against name and shortnames.
        
        Simple scoring: exact match = 2 points, case-insensitive partial match = 1 point.
        Returns highest scoring project or None if no matches.
        """
        if not keywords:
            return None

        stmt = select(Project).where(Project.status == ProjectStatus.ACTIVE.value)
        projects = list(self.session.exec(stmt))
        projects = self._enrich_projects(projects)

        if not projects:
            return None

        best_project: Project | None = None
        best_score = 0

        for project in projects:
            score = 0
            
            # Build list of identifiers to match against (name + shortnames)
            identifiers = [project.name.lower()] + [s.lower() for s in project.shortnames]

            for keyword in keywords:
                kw = keyword.lower()
                for identifier in identifiers:
                    if kw == identifier:
                        score += 2  # Exact match
                    elif kw in identifier or identifier in kw:
                        score += 1  # Partial match

            if score > best_score:
                best_score = score
                best_project = project

        return best_project if best_score > 0 else None


    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    def update(
        self,
        project_id: str,
        **updates
    ) -> Project | None:
        """Update a project's fields.
        
        Raises:
            ValueError: If name is duplicate or status is invalid.
        """
        project = self.get(project_id)
        if project is None:
            return None

        # Validate status if provided
        if 'status' in updates and not ProjectStatus.is_valid(updates['status']):
            raise ValueError(
                f"Invalid status: {updates['status']}. "
                f"Must be one of: {', '.join(ProjectStatus)}"
            )

        # Check for duplicate name if name is being updated
        if 'name' in updates and updates['name'] != project.name:
            existing = self.get_by_name(updates['name'])
            if existing:
                raise ValueError(f"Project with name '{updates['name']}' already exists")

        # Handle tags and shortnames separately (they're in junction tables)
        tags_update = updates.pop('tags', None)
        shortnames_update = updates.pop('shortnames', None)

        # Apply other updates
        for key, value in updates.items():
            if hasattr(project, key):
                setattr(project, key, value)

        project.updated_at = datetime.utcnow().isoformat()

        self.session.add(project)
        self.session.commit()
        self.session.refresh(project)

        # Sync tags/shortnames if provided
        if tags_update is not None:
            self._sync_tags(project_id, tags_update)
            project.tags = tags_update

        if shortnames_update is not None:
            self._sync_shortnames(project_id, shortnames_update)
            project.shortnames = shortnames_update

        return self._enrich_project(project)

    def update_status(self, project_id: str, status: str) -> Project | None:
        """Update project status."""
        return self.update(project_id, status=status)


    # --------------------------------------------------------
    # TAGS
    # --------------------------------------------------------

    def set_tags(self, project_id: str, tags: list[str]) -> Project | None:
        """Replace all tags on a project."""
        return self.update(project_id, tags=tags)

    def add_tag(self, project_id: str, tag: str) -> Project | None:
        """Add a tag to a project."""
        project = self.get(project_id)
        if project is None:
            return None

        if tag not in project.tags:
            project.tags.append(tag)
            self._sync_tags(project_id, project.tags)
            project.updated_at = datetime.utcnow().isoformat()
            self.session.add(project)
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)

    def remove_tag(self, project_id: str, tag: str) -> Project | None:
        """Remove a tag from a project."""
        project = self.get(project_id)
        if project is None:
            return None

        if tag in project.tags:
            project.tags.remove(tag)
            self._sync_tags(project_id, project.tags)
            project.updated_at = datetime.utcnow().isoformat()
            self.session.add(project)
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)


    # --------------------------------------------------------
    # SHORTNAMES
    # --------------------------------------------------------

    def set_shortnames(self, project_id: str, shortnames: list[str]) -> Project | None:
        """Replace all shortnames on a project."""
        return self.update(project_id, shortnames=shortnames)

    def add_shortname(self, project_id: str, shortname: str) -> Project | None:
        """Add a shortname to a project."""
        project = self.get(project_id)
        if project is None:
            return None

        if shortname not in project.shortnames:
            project.shortnames.append(shortname)
            self._sync_shortnames(project_id, project.shortnames)
            project.updated_at = datetime.utcnow().isoformat()
            self.session.add(project)
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)

    def remove_shortname(self, project_id: str, shortname: str) -> Project | None:
        """Remove a shortname from a project."""
        project = self.get(project_id)
        if project is None:
            return None

        if shortname in project.shortnames:
            project.shortnames.remove(shortname)
            self._sync_shortnames(project_id, project.shortnames)
            project.updated_at = datetime.utcnow().isoformat()
            self.session.add(project)
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)


    # --------------------------------------------------------
    # DIRECTORIES
    # --------------------------------------------------------

    def add_related_directory(self, project_id: str, directory: str) -> Project | None:
        """Add a related directory to a project."""
        project = self.get(project_id)
        if project is None:
            return None

        if directory not in project.related_directories:
            project.related_directories.append(directory)
            project.updated_at = datetime.utcnow().isoformat()
            self.session.add(project)
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)

    def remove_related_directory(self, project_id: str, directory: str) -> Project | None:
        """Remove a related directory from a project."""
        project = self.get(project_id)
        if project is None:
            return None

        if directory in project.related_directories:
            project.related_directories.remove(directory)
            project.updated_at = datetime.utcnow().isoformat()
            self.session.add(project)
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)


    # --------------------------------------------------------
    # METADATA
    # --------------------------------------------------------

    def set_metadata(self, project_id: str, key: str, value: Any) -> Project | None:
        """Set a metadata key-value pair."""
        project = self.get(project_id)
        if project is None:
            return None

        project.project_metadata[key] = value
        project.updated_at = datetime.utcnow().isoformat()
        self.session.add(project)
        self.session.commit()
        self.session.refresh(project)

        return self._enrich_project(project)

    def delete_metadata(self, project_id: str, key: str) -> Project | None:
        """Delete a metadata key."""
        project = self.get(project_id)
        if project is None:
            return None

        project.project_metadata.pop(key, None)
        project.updated_at = datetime.utcnow().isoformat()
        self.session.add(project)
        self.session.commit()
        self.session.refresh(project)

        return self._enrich_project(project)


    # --------------------------------------------------------
    # RELATIONSHIPS
    # --------------------------------------------------------

    def add_relationship(
        self,
        project_id: str,
        entity_type: str,
        entity_id: str
    ) -> Project | None:
        """Add a relationship to another entity."""
        project = self.get(project_id)
        if project is None:
            return None

        if entity_type not in project.relationships:
            project.relationships[entity_type] = []

        if entity_id not in project.relationships[entity_type]:
            project.relationships[entity_type].append(entity_id)
            project.updated_at = datetime.utcnow().isoformat()
            self.session.add(project)
            self.session.commit()
            self.session.refresh(project)

        return self._enrich_project(project)

    def remove_relationship(
        self,
        project_id: str,
        entity_type: str,
        entity_id: str
    ) -> Project | None:
        """Remove a relationship to another entity."""
        project = self.get(project_id)
        if project is None:
            return None

        if entity_type in project.relationships:
            if entity_id in project.relationships[entity_type]:
                project.relationships[entity_type].remove(entity_id)
                project.updated_at = datetime.utcnow().isoformat()
                self.session.add(project)
                self.session.commit()
                self.session.refresh(project)

        return self._enrich_project(project)


    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(self, project_id: str) -> dict:
        """Delete a project.
        
        Returns:
            Dictionary with deletion result: {"deleted": bool, "project_id": str}
        """
        project = self.get(project_id)
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


    # --------------------------------------------------------
    # SERIALIZATION
    # --------------------------------------------------------

    def to_dict(self, project: Project) -> dict:
        """Convert a Project object to a dictionary for tool output."""
        return {
            "project_id": project.project_id,
            "name": project.name,
            "project_type": project.project_type,
            "status": project.status,
            "main_directory": project.main_directory,
            "related_directories": project.related_directories,
            "description": project.description,
            "tags": project.tags,
            "shortnames": project.shortnames,
            "metadata": project.metadata,
            "relationships": project.relationships,
            "creator_session_id": project.creator_session_id,
            "creator_agent_dir": project.creator_agent_dir,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
        }
