"""Project management tools for agents.

Provides CRUD operations for projects with:
- Main directory and related directories tracking
- Status management (active, paused, completed, archived)
- Flexible metadata and tagging
- Relationships to sessions, agents, and other projects
"""

from typing import TYPE_CHECKING
from langchain_core.tools import tool

from ..project_store import ProjectStore, ProjectStatus

if TYPE_CHECKING:
    import sqlite3


def create_project_tools(conn: "sqlite3.Connection", current_session_id: str = "", agent_dir: str = ""):
    """Create project management tools with injected database connection.
    
    Args:
        conn: SQLite database connection.
        current_session_id: The current session ID (used for creator tracking).
        agent_dir: The current agent directory (used for creator tracking).
    
    Returns:
        List of tool functions for project management.
    """
    store = ProjectStore(conn)
    
    @tool
    def project_create(
        name: str,
        project_type: str = "general",
        main_directory: str | None = None,
        related_directories: list[str] | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Create a new project.
        
        Projects are abstract containers for organizing work. They can represent
        software projects, documentation efforts, research tasks, or any other
        work that benefits from tracking directories, status, and metadata.
        
        Args:
            name: Project name (required, must be unique).
            project_type: Type of project - "software", "documentation", "research", 
                          "task", "general", or any custom type. Default: "general".
            main_directory: Primary directory where project files are located.
            related_directories: Additional directories related to this project.
            description: Brief description of the project's purpose.
            tags: List of tags for categorization and filtering.
            metadata: Custom key-value pairs for type-specific data.
        
        Returns:
            Dictionary with project details including project_id.
        
        Example:
            project_create(
                name="My Web App",
                project_type="software",
                main_directory="/home/user/projects/my-web-app",
                tags=["web", "frontend", "react"],
                metadata={"framework": "React", "language": "TypeScript"}
            )
        """
        try:
            project = store.create(
                name=name,
                project_type=project_type,
                main_directory=main_directory,
                related_directories=related_directories,
                description=description,
                tags=tags,
                metadata=metadata,
                creator_session_id=current_session_id or None,
                creator_agent_dir=agent_dir or None,
            )
            return store.to_dict(project)
        except ValueError as e:
            return {"error": str(e)}
    
    @tool
    def project_get(project_id: str | None = None, name: str | None = None) -> dict | None:
        """Get a project by ID or name.
        
        Provide either project_id or name (project_id takes precedence).
        
        Args:
            project_id: The unique project identifier.
            name: The project name (used if project_id not provided).
        
        Returns:
            Project dictionary with all details, or None if not found.
        """
        if project_id:
            project = store.get(project_id)
        elif name:
            project = store.get_by_name(name)
        else:
            return {"error": "Must provide either project_id or name"}
        
        if project is None:
            return None
        return store.to_dict(project)
    
    @tool
    def project_list(
        status: str | None = None,
        project_type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List projects with optional filters.
        
        Args:
            status: Filter by status - "active", "paused", "completed", "archived".
            project_type: Filter by project type.
            tags: Filter by tags (projects must have ALL specified tags).
            limit: Maximum number of results (default: 50).
        
        Returns:
            List of project dictionaries, sorted by most recently updated.
        """
        projects = store.list(
            status=status,
            project_type=project_type,
            tags=tags,
            limit=limit,
        )
        return [store.to_dict(p) for p in projects]
    
    @tool
    def project_search(query: str, limit: int = 20) -> list[dict]:
        """Search projects by name or description.
        
        Args:
            query: Search string to match against name and description.
            limit: Maximum number of results (default: 20).
        
        Returns:
            List of matching project dictionaries.
        """
        projects = store.search(query, limit=limit)
        return [store.to_dict(p) for p in projects]
    
    @tool
    def project_get_by_session(session_id: str) -> list[dict]:
        """Get all projects linked to a session.
        
        Returns projects where:
        - The session created the project, OR
        - The session is linked via project_link("sessions", session_id)
        
        Args:
            session_id: The session ID to search for.
        
        Returns:
            List of project dictionaries linked to this session.
        """
        projects = store.get_by_session(session_id)
        return [store.to_dict(p) for p in projects]
    
    @tool
    def project_get_by_directory(directory: str) -> list[dict]:
        """Get all projects that reference a directory.
        
        Searches both main_directory and related_directories.
        Useful for discovering which projects are associated with a path.
        
        Args:
            directory: The directory path to search for.
        
        Returns:
            List of project dictionaries referencing this directory.
        """
        projects = store.get_by_directory(directory)
        return [store.to_dict(p) for p in projects]
    
    @tool
    def project_update(
        project_id: str,
        name: str | None = None,
        project_type: str | None = None,
        description: str | None = None,
        main_directory: str | None = None,
        related_directories: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict | None:
        """Update project fields.
        
        Args:
            project_id: The project ID to update.
            name: New project name (must be unique).
            project_type: New project type.
            description: New description.
            main_directory: New main directory path.
            related_directories: Replace all related directories.
            tags: Replace all tags (use project_add_tag/remove_tag for incremental).
        
        Returns:
            Updated project dictionary, or None if not found.
        """
        updates = {}
        if name is not None:
            updates["name"] = name
        if project_type is not None:
            updates["project_type"] = project_type
        if description is not None:
            updates["description"] = description
        if main_directory is not None:
            updates["main_directory"] = main_directory
        if related_directories is not None:
            updates["related_directories"] = related_directories
        if tags is not None:
            updates["tags"] = tags
        
        if not updates:
            project = store.get(project_id)
            return store.to_dict(project) if project else None
        
        try:
            project = store.update(project_id, **updates)
            return store.to_dict(project) if project else None
        except ValueError as e:
            return {"error": str(e)}
    
    @tool
    def project_set_status(project_id: str, status: str) -> dict | None:
        """Update project status.
        
        Use this to track project lifecycle:
        - "active": Currently being worked on
        - "paused": Temporarily stopped
        - "completed": Finished successfully
        - "archived": Stored for reference, no longer active
        
        Args:
            project_id: The project ID.
            status: New status - must be "active", "paused", "completed", or "archived".
        
        Returns:
            Updated project dictionary, or None if not found.
        """
        if not ProjectStatus.is_valid(status):
            return {
                "error": f"Invalid status '{status}'. "
                         f"Must be one of: {', '.join(ProjectStatus)}"
            }
        
        try:
            project = store.update_status(project_id, status)
            return store.to_dict(project) if project else None
        except ValueError as e:
            return {"error": str(e)}
    
    @tool
    def project_add_directory(
        project_id: str, 
        directory: str, 
        as_main: bool = False
    ) -> dict | None:
        """Add a directory to a project.
        
        Args:
            project_id: The project ID.
            directory: Directory path to add.
            as_main: If True, set as main directory. If False, add to related directories.
        
        Returns:
            Updated project dictionary, or None if not found.
        """
        if as_main:
            project = store.update(project_id, main_directory=directory)
        else:
            project = store.add_related_directory(project_id, directory)
        return store.to_dict(project) if project else None
    
    @tool
    def project_remove_directory(project_id: str, directory: str) -> dict | None:
        """Remove a directory from project's related directories.
        
        Note: This only removes from related_directories, not main_directory.
        Use project_update to change main_directory.
        
        Args:
            project_id: The project ID.
            directory: Directory path to remove.
        
        Returns:
            Updated project dictionary, or None if not found.
        """
        project = store.remove_related_directory(project_id, directory)
        return store.to_dict(project) if project else None
    
    @tool
    def project_set_tags(project_id: str, tags: list[str]) -> dict | None:
        """Replace all tags on a project.
        
        This atomically replaces all tags. For incremental changes,
        use project_add_tag and project_remove_tag.
        
        Args:
            project_id: The project ID.
            tags: New list of tags (replaces existing tags entirely).
        
        Returns:
            Updated project dictionary, or None if not found.
        """
        project = store.set_tags(project_id, tags)
        return store.to_dict(project) if project else None
    
    @tool
    def project_add_tag(project_id: str, tag: str) -> dict | None:
        """Add a tag to a project.
        
        Args:
            project_id: The project ID.
            tag: Tag to add.
        
        Returns:
            Updated project dictionary, or None if not found.
        """
        project = store.add_tag(project_id, tag)
        return store.to_dict(project) if project else None
    
    @tool
    def project_remove_tag(project_id: str, tag: str) -> dict | None:
        """Remove a tag from a project.
        
        Args:
            project_id: The project ID.
            tag: Tag to remove.
        
        Returns:
            Updated project dictionary, or None if not found.
        """
        project = store.remove_tag(project_id, tag)
        return store.to_dict(project) if project else None
    
    @tool
    def project_set_metadata(project_id: str, key: str, value) -> dict | None:
        """Set a custom metadata field on a project.
        
        Use metadata for type-specific data that doesn't fit in standard fields.
        The value must be JSON-serializable (string, number, boolean, list, dict).
        
        Args:
            project_id: The project ID.
            key: Metadata key name.
            value: Metadata value (JSON-serializable).
        
        Returns:
            Updated project dictionary, or None if not found.
        
        Example:
            project_set_metadata(project_id, "priority", "high")
            project_set_metadata(project_id, "deadline", "2024-12-31")
            project_set_metadata(project_id, "tech_stack", ["Python", "FastAPI", "React"])
        """
        project = store.set_metadata(project_id, key, value)
        return store.to_dict(project) if project else None
    
    @tool
    def project_delete_metadata(project_id: str, key: str) -> dict | None:
        """Delete a metadata field from a project.
        
        Args:
            project_id: The project ID.
            key: Metadata key to delete.
        
        Returns:
            Updated project dictionary, or None if not found.
        """
        project = store.delete_metadata(project_id, key)
        return store.to_dict(project) if project else None
    
    @tool
    def project_link(
        project_id: str, 
        entity_type: str, 
        entity_id: str
    ) -> dict | None:
        """Link a project to another entity.
        
        Use this to establish relationships between projects and:
        - sessions: Link to agent sessions working on this project
        - projects: Link to related/sub-projects
        - agents: Link to agents assigned to this project
        - Any custom entity type you need
        
        Args:
            project_id: The project ID.
            entity_type: Type of entity (e.g., "sessions", "projects", "agents").
            entity_id: ID of the related entity.
        
        Returns:
            Updated project dictionary, or None if not found.
        """
        project = store.add_relationship(project_id, entity_type, entity_id)
        return store.to_dict(project) if project else None
    
    @tool
    def project_unlink(
        project_id: str, 
        entity_type: str, 
        entity_id: str
    ) -> dict | None:
        """Remove a link between a project and another entity.
        
        Args:
            project_id: The project ID.
            entity_type: Type of entity.
            entity_id: ID of the related entity.
        
        Returns:
            Updated project dictionary, or None if not found.
        """
        project = store.remove_relationship(project_id, entity_type, entity_id)
        return store.to_dict(project) if project else None
    
    @tool
    def project_delete(project_id: str) -> dict:
        """Delete a project permanently.
        
        Warning: This cannot be undone. The project and all its metadata
        will be removed from the database.
        
        Args:
            project_id: The project ID to delete.
        
        Returns:
            Dictionary with deletion result: {"deleted": bool, "project_id": str, "name": str}
        """
        return store.delete(project_id)
    
    return [
        project_create,
        project_get,
        project_list,
        project_search,
        project_get_by_session,
        project_get_by_directory,
        project_update,
        project_set_status,
        project_add_directory,
        project_remove_directory,
        project_set_tags,
        project_add_tag,
        project_remove_tag,
        project_set_metadata,
        project_delete_metadata,
        project_link,
        project_unlink,
        project_delete,
    ]
