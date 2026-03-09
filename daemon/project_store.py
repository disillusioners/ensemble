"""Project management storage layer."""

import enum
import json
import sqlite3
import uuid
from datetime import datetime
from typing import Optional, Any
from dataclasses import dataclass, asdict


class ProjectStatus(enum.StrEnum):
    """Valid project statuses."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    
    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls._value2member_map_


class ProjectType(enum.StrEnum):
    """Common project types."""
    SOFTWARE = "software"
    DOCUMENTATION = "documentation"
    RESEARCH = "research"
    TASK = "task"
    GENERAL = "general"
    
    @classmethod
    def is_valid(cls, project_type: str) -> bool:
        # Allow custom types beyond the predefined ones
        return bool(project_type and project_type.strip())


@dataclass
class Project:
    """Represents a project entity."""
    project_id: str
    name: str
    project_type: str
    status: str
    main_directory: str | None
    related_directories: list[str]
    description: str | None
    tags: list[str]
    metadata: dict[str, Any]
    relationships: dict[str, list[str]]
    creator_session_id: str | None
    creator_agent_dir: str | None
    created_at: str
    updated_at: str


class ProjectStore:
    """SQLite-based project storage with CRUD operations.
    
    Note: The projects and project_tags tables are created in persistence.py:init_database()
    to ensure proper WAL mode initialization.
    """
    
    def __init__(self, conn: sqlite3.Connection):
        """Initialize the project store.
        
        Args:
            conn: SQLite database connection.
        """
        self.conn = conn
    
    def create(
        self,
        name: str,
        project_type: str = "general",
        main_directory: str | None = None,
        related_directories: list[str] | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
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
            metadata: Type-specific metadata.
            project_id: Optional custom project ID (auto-generated if None).
            creator_session_id: Session ID that created this project.
            creator_agent_dir: Agent directory of the creator.
        
        Returns:
            The created Project object.
        
        Raises:
            ValueError: If name is duplicate or status/type is invalid.
        """
        # Validate status (always starts as active)
        status = ProjectStatus.ACTIVE.value
        
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
        
        project = Project(
            project_id=project_id,
            name=name,
            project_type=project_type,
            status=status,
            main_directory=main_directory,
            related_directories=related_directories or [],
            description=description,
            tags=tags,
            metadata=metadata or {},
            relationships={},
            creator_session_id=creator_session_id,
            creator_agent_dir=creator_agent_dir,
            created_at=now,
            updated_at=now,
        )
        
        self._save(project)
        return project
    
    def _save(self, project: Project) -> None:
        """Save a project to the database."""
        self.conn.execute("""
            INSERT OR REPLACE INTO projects (
                project_id, name, project_type, status, main_directory,
                related_directories, description, metadata, relationships,
                creator_session_id, creator_agent_dir, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            project.project_id,
            project.name,
            project.project_type,
            project.status,
            project.main_directory,
            json.dumps(project.related_directories),
            project.description,
            json.dumps(project.metadata),
            json.dumps(project.relationships),
            project.creator_session_id,
            project.creator_agent_dir,
            project.created_at,
            project.updated_at,
        ))
        
        # Sync tags to junction table
        self._sync_tags(project.project_id, project.tags)
        
        self.conn.commit()
    
    def _sync_tags(self, project_id: str, tags: list[str]) -> None:
        """Sync tags to the project_tags junction table."""
        # Remove existing tags
        self.conn.execute(
            "DELETE FROM project_tags WHERE project_id = ?",
            (project_id,)
        )
        
        # Insert new tags
        for tag in tags:
            self.conn.execute(
                "INSERT OR IGNORE INTO project_tags (project_id, tag) VALUES (?, ?)",
                (project_id, tag)
            )
    
    def get(self, project_id: str) -> Project | None:
        """Get a project by ID.
        
        Args:
            project_id: The project ID.
        
        Returns:
            Project object or None if not found.
        """
        cursor = self.conn.execute(
            "SELECT * FROM projects WHERE project_id = ?",
            (project_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_project(row)
    
    def get_by_name(self, name: str) -> Project | None:
        """Get a project by name.
        
        Args:
            name: The project name.
        
        Returns:
            Project object or None if not found.
        """
        cursor = self.conn.execute(
            "SELECT * FROM projects WHERE name = ?",
            (name,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_project(row)
    
    def get_by_session(self, session_id: str) -> list[Project]:
        """Get all projects linked to a session.
        
        Args:
            session_id: The session ID to search for.
        
        Returns:
            List of projects linked to this session.
        """
        cursor = self.conn.execute(
            """
            SELECT * FROM projects 
            WHERE creator_session_id = ?
               OR relationships LIKE ?
            ORDER BY updated_at DESC
            """,
            (session_id, f'%"sessions":%"{session_id}"%')
        )
        return [self._row_to_project(row) for row in cursor.fetchall()]
    
    def get_by_directory(self, directory: str) -> list[Project]:
        """Get all projects that reference a directory.
        
        Searches both main_directory and related_directories.
        
        Args:
            directory: The directory path to search for.
        
        Returns:
            List of projects referencing this directory.
        """
        cursor = self.conn.execute(
            """
            SELECT * FROM projects 
            WHERE main_directory = ?
               OR related_directories LIKE ?
            ORDER BY updated_at DESC
            """,
            (directory, f'%"{directory}"%')
        )
        return [self._row_to_project(row) for row in cursor.fetchall()]
    
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
        # Use junction table for efficient tag filtering
        if tags:
            return self._list_with_tags(status, project_type, tags, limit, offset)
        
        query = "SELECT * FROM projects WHERE 1=1"
        params: list[Any] = []
        
        if status:
            query += " AND status = ?"
            params.append(status)
        
        if project_type:
            query += " AND project_type = ?"
            params.append(project_type)
        
        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor = self.conn.execute(query, params)
        return [self._row_to_project(row) for row in cursor.fetchall()]
    
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
        base_query = """
            SELECT DISTINCT p.* FROM projects p
        """
        
        joins = []
        conditions = ["1=1"]
        params: list[Any] = []
        
        for i, tag in enumerate(tags):
            alias = f"pt{i}"
            joins.append(f"JOIN project_tags {alias} ON p.project_id = {alias}.project_id")
            conditions.append(f"{alias}.tag = ?")
            params.append(tag)
        
        if status:
            conditions.append("p.status = ?")
            params.append(status)
        
        if project_type:
            conditions.append("p.project_type = ?")
            params.append(project_type)
        
        query = base_query + "\n" + "\n".join(joins) + "\n"
        query += "WHERE " + " AND ".join(conditions)
        query += " ORDER BY p.updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor = self.conn.execute(query, params)
        return [self._row_to_project(row) for row in cursor.fetchall()]
    
    def search(self, query: str, limit: int = 20) -> list[Project]:
        """Search projects by name or description.
        
        Args:
            query: Search query string.
            limit: Maximum number of results.
        
        Returns:
            List of matching Project objects.
        """
        cursor = self.conn.execute(
            """
            SELECT * FROM projects 
            WHERE name LIKE ? OR description LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", limit)
        )
        return [self._row_to_project(row) for row in cursor.fetchall()]
    
    def update(
        self,
        project_id: str,
        **updates
    ) -> Project | None:
        """Update a project's fields.
        
        Args:
            project_id: The project ID.
            **updates: Fields to update (name, status, description, tags, metadata, etc.)
        
        Returns:
            Updated Project object or None if not found.
        
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
        
        # Apply updates
        for key, value in updates.items():
            if hasattr(project, key):
                setattr(project, key, value)
        
        project.updated_at = datetime.utcnow().isoformat()
        self._save(project)
        return project
    
    def update_status(self, project_id: str, status: str) -> Project | None:
        """Update project status.
        
        Args:
            project_id: The project ID.
            status: New status (active, paused, completed, archived).
        
        Returns:
            Updated Project object or None if not found.
        
        Raises:
            ValueError: If status is invalid.
        """
        return self.update(project_id, status=status)
    
    def set_tags(self, project_id: str, tags: list[str]) -> Project | None:
        """Replace all tags on a project.
        
        Args:
            project_id: The project ID.
            tags: New list of tags (replaces existing).
        
        Returns:
            Updated Project object or None if not found.
        """
        return self.update(project_id, tags=tags)
    
    def add_related_directory(self, project_id: str, directory: str) -> Project | None:
        """Add a related directory to a project.
        
        Args:
            project_id: The project ID.
            directory: Directory path to add.
        
        Returns:
            Updated Project object or None if not found.
        """
        project = self.get(project_id)
        if project is None:
            return None
        
        if directory not in project.related_directories:
            project.related_directories.append(directory)
            project.updated_at = datetime.utcnow().isoformat()
            self._save(project)
        
        return project
    
    def remove_related_directory(self, project_id: str, directory: str) -> Project | None:
        """Remove a related directory from a project.
        
        Args:
            project_id: The project ID.
            directory: Directory path to remove.
        
        Returns:
            Updated Project object or None if not found.
        """
        project = self.get(project_id)
        if project is None:
            return None
        
        if directory in project.related_directories:
            project.related_directories.remove(directory)
            project.updated_at = datetime.utcnow().isoformat()
            self._save(project)
        
        return project
    
    def add_tag(self, project_id: str, tag: str) -> Project | None:
        """Add a tag to a project.
        
        Args:
            project_id: The project ID.
            tag: Tag to add.
        
        Returns:
            Updated Project object or None if not found.
        """
        project = self.get(project_id)
        if project is None:
            return None
        
        if tag not in project.tags:
            project.tags.append(tag)
            project.updated_at = datetime.utcnow().isoformat()
            self._save(project)
        
        return project
    
    def remove_tag(self, project_id: str, tag: str) -> Project | None:
        """Remove a tag from a project.
        
        Args:
            project_id: The project ID.
            tag: Tag to remove.
        
        Returns:
            Updated Project object or None if not found.
        """
        project = self.get(project_id)
        if project is None:
            return None
        
        if tag in project.tags:
            project.tags.remove(tag)
            project.updated_at = datetime.utcnow().isoformat()
            self._save(project)
        
        return project
    
    def set_metadata(self, project_id: str, key: str, value: Any) -> Project | None:
        """Set a metadata key-value pair.
        
        Args:
            project_id: The project ID.
            key: Metadata key.
            value: Metadata value (must be JSON-serializable).
        
        Returns:
            Updated Project object or None if not found.
        """
        project = self.get(project_id)
        if project is None:
            return None
        
        project.metadata[key] = value
        project.updated_at = datetime.utcnow().isoformat()
        self._save(project)
        return project
    
    def delete_metadata(self, project_id: str, key: str) -> Project | None:
        """Delete a metadata key.
        
        Args:
            project_id: The project ID.
            key: Metadata key to delete.
        
        Returns:
            Updated Project object or None if not found.
        """
        project = self.get(project_id)
        if project is None:
            return None
        
        project.metadata.pop(key, None)
        project.updated_at = datetime.utcnow().isoformat()
        self._save(project)
        return project
    
    def add_relationship(
        self, 
        project_id: str, 
        entity_type: str, 
        entity_id: str
    ) -> Project | None:
        """Add a relationship to another entity.
        
        Args:
            project_id: The project ID.
            entity_type: Type of entity (e.g., "sessions", "projects", "agents").
            entity_id: ID of the related entity.
        
        Returns:
            Updated Project object or None if not found.
        """
        project = self.get(project_id)
        if project is None:
            return None
        
        if entity_type not in project.relationships:
            project.relationships[entity_type] = []
        
        if entity_id not in project.relationships[entity_type]:
            project.relationships[entity_type].append(entity_id)
            project.updated_at = datetime.utcnow().isoformat()
            self._save(project)
        
        return project
    
    def remove_relationship(
        self, 
        project_id: str, 
        entity_type: str, 
        entity_id: str
    ) -> Project | None:
        """Remove a relationship to another entity.
        
        Args:
            project_id: The project ID.
            entity_type: Type of entity.
            entity_id: ID of the related entity.
        
        Returns:
            Updated Project object or None if not found.
        """
        project = self.get(project_id)
        if project is None:
            return None
        
        if entity_type in project.relationships:
            if entity_id in project.relationships[entity_type]:
                project.relationships[entity_type].remove(entity_id)
                project.updated_at = datetime.utcnow().isoformat()
                self._save(project)
        
        return project
    
    def delete(self, project_id: str) -> dict:
        """Delete a project.
        
        Args:
            project_id: The project ID.
        
        Returns:
            Dictionary with deletion result: {"deleted": bool, "project_id": str}
        """
        # Get project info before deletion for response
        project = self.get(project_id)
        if project is None:
            return {"deleted": False, "project_id": project_id, "error": "Not found"}
        
        # Delete from project_tags (cascade should handle this, but be explicit)
        self.conn.execute(
            "DELETE FROM project_tags WHERE project_id = ?",
            (project_id,)
        )
        
        # Delete project
        cursor = self.conn.execute(
            "DELETE FROM projects WHERE project_id = ?",
            (project_id,)
        )
        self.conn.commit()
        
        return {
            "deleted": cursor.rowcount > 0,
            "project_id": project_id,
            "name": project.name
        }
    
    def _row_to_project(self, row) -> Project:
        """Convert a database row to a Project object."""
        # Get tags from junction table
        project_id = row[0]
        cursor = self.conn.execute(
            "SELECT tag FROM project_tags WHERE project_id = ?",
            (project_id,)
        )
        tags = [r[0] for r in cursor.fetchall()]
        
        return Project(
            project_id=row[0],
            name=row[1],
            project_type=row[2],
            status=row[3],
            main_directory=row[4],
            related_directories=json.loads(row[5]) if row[5] else [],
            description=row[6],
            tags=tags,
            metadata=json.loads(row[7]) if row[7] else {},
            relationships=json.loads(row[8]) if row[8] else {},
            creator_session_id=row[9],
            creator_agent_dir=row[10],
            created_at=row[11],
            updated_at=row[12],
        )
    
    def to_dict(self, project: Project) -> dict:
        """Convert a Project object to a dictionary for tool output."""
        return asdict(project)
