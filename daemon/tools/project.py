"""Project management tools for agents.

Provides CRUD operations for projects with:
- Main directory and related directories tracking
- Status management (active, paused, completed, archived)
- Flexible metadata and tagging
- Relationships to instances, agents, and other projects
"""

from langchain_core.tools import tool

from ..repositories.project.repository import SQLModelProjectRepository
from ..repositories.project.models import ProjectStatus, ProjectType
from ._tool_registry import register_tool_category
from ._truncate import truncate_dict_result

CATEGORY_NAME = "Project Management"
CATEGORY_DOC = """\
Create, update, search, and manage projects.

**Status values:** `active`, `paused`, `completed`, `archived`
**Project types:** `software`, `documentation`, `research`, `task`, `general`, or custom
"""


def _validate_directory(path: str | None) -> tuple[str | None, str | None]:
    """Validate and sanitize a directory path.
    
    Returns:
        Tuple of (validated_path_or_none, error_message_or_none).
        If error_message is not None, validation failed.
    """
    if path is None:
        return None, None
    
    # Check for null bytes
    if '\x00' in path:
        return None, "Invalid path: null bytes not allowed"
    
    # Resolve to absolute path and check for traversal
    try:
        from pathlib import Path
        resolved = Path(path).expanduser().resolve()
        
        # Block obvious path traversal attempts
        if '..' in path:
            return None, "Invalid path: path traversal not allowed"
        
        return str(resolved), None
    except Exception as e:
        return None, f"Invalid path: {e}"


# Full documentation strings for each tool
_FULL_DOCS = {
    "project_create": """Create a new project.

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
    )""",
    
    "project_get": """Get a project by ID, name, or shortname.

Provide either project_id, name, or shortname (project_id takes precedence, then name).

Args:
    project_id: The unique project identifier.
    name: The project name (used if project_id not provided).
    shortname: A project shortname/nickname (used if project_id and name not provided).

Returns:
    Project dictionary with all details, or None if not found.""",
    
    "project_list": """List projects with optional filters.

Args:
    status: Filter by status - "active", "paused", "completed", "archived".
    project_type: Filter by project type.
    tags: Filter by tags (projects must have ALL specified tags).
    offset: Number of results to skip (default: 0).
    limit: Maximum number of results (default: 50).

Returns:
    Dictionary with projects list and pagination metadata.
    Includes _pagination field when results are truncated.""",
    
    "project_search": """Search projects by name, description, or shortnames.

Args:
    query: Search string to match against name, description, and shortnames.
    limit: Maximum number of results (default: 20).

Returns:
    List of matching project dictionaries.""",
    
    "project_get_by_instance": """Get all projects linked to an instance.

Returns projects where:
- The instance created the project, OR
- The instance is linked via project_link("instances", instance_id)

Args:
    instance_id: The instance ID to search for.

Returns:
    List of project dictionaries linked to this instance.""",
    
    "project_get_by_directory": """Get all projects that reference a directory.

Searches both main_directory and related_directories.
Useful for discovering which projects are associated with a path.

Args:
    directory: The directory path to search for.

Returns:
    List of project dictionaries referencing this directory.""",
    
    "project_update": """Update project fields.

Args:
    project_id: The project ID to update.
    name: New project name (must be unique).
    project_type: New project type.
    description: New description.
    main_directory: New main directory path.
    related_directories: Replace all related directories.
    tags: Replace all tags (use project_add_tag/remove_tag for incremental).
    shortnames: Replace all shortnames (use project_add_shortname/remove_shortname for incremental).

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_set_status": """Update project status.

Use this to track project lifecycle:
- "active": Currently being worked on
- "paused": Temporarily stopped
- "completed": Finished successfully
- "archived": Stored for reference, no longer active

Args:
    project_id: The project ID.
    status: New status - must be "active", "paused", "completed", or "archived".

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_add_directory": """Add a directory to a project.

Args:
    project_id: The project ID.
    directory: Directory path to add.
    as_main: If True, set as main directory. If False, add to related directories.

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_remove_directory": """Remove a directory from project's related directories.

Note: This only removes from related_directories, not main_directory.
Use project_update to change main_directory.

Args:
    project_id: The project ID.
    directory: Directory path to remove.

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_set_tags": """Replace all tags on a project.

This atomically replaces all tags. For incremental changes,
use project_add_tag and project_remove_tag.

Args:
    project_id: The project ID.
    tags: New list of tags (replaces existing tags entirely).

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_add_tag": """Add a tag to a project.

Args:
    project_id: The project ID.
    tag: Tag to add.

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_remove_tag": """Remove a tag from a project.

Args:
    project_id: The project ID.
    tag: Tag to remove.

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_set_shortnames": """Replace all shortnames on a project.

Shortnames are alternative names/nicknames for quick reference.
For example, "agents-ensemble" might have shortnames ["ensemble", "ae"].

This atomically replaces all shortnames. For incremental changes,
use project_add_shortname and project_remove_shortname.

Args:
    project_id: The project ID.
    shortnames: New list of shortnames (replaces existing entirely).

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_add_shortname": """Add a shortname to a project.

Shortnames are alternative names/nicknames for quick reference.

Args:
    project_id: The project ID.
    shortname: Shortname to add.

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_remove_shortname": """Remove a shortname from a project.

Args:
    project_id: The project ID.
    shortname: Shortname to remove.

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_set_metadata": """Set a custom metadata field on a project.

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
    project_set_metadata(project_id, "tech_stack", ["Python", "FastAPI", "React"])""",
    
    "project_delete_metadata": """Delete a metadata field from a project.

Args:
    project_id: The project ID.
    key: Metadata key to delete.

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_link": """Link a project to another entity.

Use this to establish relationships between projects and:
- instances: Link to agent instances working on this project
- projects: Link to related/sub-projects
- agents: Link to agents assigned to this project
- Any custom entity type you need

Args:
    project_id: The project ID.
    entity_type: Type of entity (e.g., "instances", "projects", "agents").
    entity_id: ID of the related entity.

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_unlink": """Remove a link between a project and another entity.

Args:
    project_id: The project ID.
    entity_type: Type of entity.
    entity_id: ID of the related entity.

Returns:
    Updated project dictionary, or None if not found.""",
    
    "project_delete": """Delete a project permanently.

Warning: This cannot be undone. The project and all its metadata
will be removed from the database.

Args:
    project_id: The project ID to delete.

Returns:
    Dictionary with deletion result: {"deleted": bool, "project_id": str, "name": str}""",
}


def create_project_tools(store: SQLModelProjectRepository, current_instance_id: str = "", agent_id: str = ""):
    """Create project management tools with injected repository.
    
    Args:
        store: SQLModelProjectRepository instance for database operations.
        current_instance_id: The current instance ID (used for creator tracking).
        agent_id: The current agent ID (primary parameter).
    
    Returns:
        List of tool functions for project management.
    """
    @register_tool_category("project")
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
        """Create a new project. Use tool_help("project_create") for details."""
        # Validate project_type
        if not ProjectType.is_valid(project_type):
            return {
                "error": f"Invalid project_type '{project_type}'. Must be one of: {', '.join(ProjectType._value2member_map_.keys())}"
            }
        
        # Validate and sanitize paths
        main_directory, main_dir_error = _validate_directory(main_directory)
        if main_dir_error:
            return {"error": main_dir_error}
        
        related_directories_validated = []
        if related_directories:
            for d in related_directories:
                validated, error = _validate_directory(d)
                if error:
                    return {"error": error}
                related_directories_validated.append(validated)
        
        try:
            project = store.create(
                name=name,
                project_type=project_type,
                main_directory=main_directory,
                related_directories=related_directories_validated or None,
                description=description,
                tags=tags,
                metadata=metadata,
                creator_instance_id=current_instance_id or None,
                creator_agent_id=agent_id or None,
            )
            return project.to_dict()
        except ValueError as e:
            return {"error": str(e)}
    project_create._full_doc_ = _FULL_DOCS["project_create"]
    
    @register_tool_category("project")
    @tool
    def project_get(project_id: str | None = None, name: str | None = None, shortname: str | None = None) -> dict | None:
        """Get a project by ID or name. Use tool_help("project_get") for details."""
        if project_id:
            project = store.get(project_id)
        elif name:
            project = store.get_by_name(name)
        elif shortname:
            project = store.get_by_shortname(shortname)
        else:
            return {"error": "Must provide either project_id, name, or shortname"}
        
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_get._full_doc_ = _FULL_DOCS["project_get"]
    
    @register_tool_category("project")
    @tool
    def project_list(
        status: str | None = None,
        project_type: str | None = None,
        tags: list[str] | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        """List projects with optional filters. Use tool_help("project_list") for details."""
        projects = store.list_projects(
            status=status,
            project_type=project_type,
            tags=tags,
            offset=offset,
            limit=limit,
        )
        
        result = {"projects": [p.to_dict() for p in projects]}
        
        # Add pagination metadata
        return truncate_dict_result(result, list_key="projects", limit=limit)
    project_list._full_doc_ = _FULL_DOCS["project_list"]
    
    @register_tool_category("project")
    @tool
    def project_search(query: str, limit: int = 20) -> list[dict]:
        """Search projects by name or description. Use tool_help("project_search") for details."""
        projects = store.search(query, limit=limit)
        return [p.to_dict() for p in projects]
    project_search._full_doc_ = _FULL_DOCS["project_search"]
    
    @register_tool_category("project")
    @tool
    def project_get_by_instance(instance_id: str) -> list[dict]:
        """Get projects linked to an instance. Use tool_help("project_get_by_instance") for details."""
        projects = store.get_by_instance(instance_id)
        return [p.to_dict() for p in projects]
    project_get_by_instance._full_doc_ = _FULL_DOCS["project_get_by_instance"]
    
    @register_tool_category("project")
    @tool
    def project_get_by_directory(directory: str) -> list[dict]:
        """Get projects referencing a directory. Use tool_help("project_get_by_directory") for details."""
        projects = store.get_by_directory(directory)
        return [p.to_dict() for p in projects]
    project_get_by_directory._full_doc_ = _FULL_DOCS["project_get_by_directory"]
    
    @register_tool_category("project")
    @tool
    def project_update(
        project_id: str,
        name: str | None = None,
        project_type: str | None = None,
        description: str | None = None,
        main_directory: str | None = None,
        related_directories: list[str] | None = None,
        tags: list[str] | None = None,
        shortnames: list[str] | None = None,
    ) -> dict | None:
        """Update project fields. Use tool_help("project_update") for details."""
        # Validate project_type if provided
        if project_type is not None and not ProjectType.is_valid(project_type):
            return {
                "error": f"Invalid project_type '{project_type}'. Must be one of: {', '.join(ProjectType._value2member_map_.keys())}"
            }
        
        updates = {}
        if name is not None:
            updates["name"] = name
        if project_type is not None:
            updates["project_type"] = project_type
        if description is not None:
            updates["description"] = description
        
        # Validate main_directory if provided
        if main_directory is not None:
            validated, error = _validate_directory(main_directory)
            if error:
                return {"error": error}
            updates["main_directory"] = validated
        
        # Validate related_directories if provided
        if related_directories is not None:
            validated_dirs = []
            for d in related_directories:
                validated, error = _validate_directory(d)
                if error:
                    return {"error": error}
                validated_dirs.append(validated)
            updates["related_directories"] = validated_dirs
        
        if tags is not None:
            updates["tags"] = tags
        if shortnames is not None:
            updates["shortnames"] = shortnames
        
        if not updates:
            project = store.get(project_id)
            if project is None:
                return {"error": "Project not found", "error_code": "NOT_FOUND"}
            return project.to_dict()
        
        try:
            project = store.update(project_id, **updates)
            if project is None:
                return {"error": "Project not found", "error_code": "NOT_FOUND"}
            return project.to_dict()
        except ValueError as e:
            return {"error": str(e)}
    project_update._full_doc_ = _FULL_DOCS["project_update"]
    
    @register_tool_category("project")
    @tool
    def project_set_status(project_id: str, status: str) -> dict | None:
        """Update project status. Use tool_help("project_set_status") for details."""
        if not ProjectStatus.is_valid(status):
            return {
                "error": f"Invalid status '{status}'. "
                         f"Must be one of: {', '.join(ProjectStatus._value2member_map_.keys())}"
            }
        
        try:
            project = store.update_status(project_id, status)
            if project is None:
                return {"error": "Project not found", "error_code": "NOT_FOUND"}
            return project.to_dict()
        except ValueError as e:
            return {"error": str(e)}
    project_set_status._full_doc_ = _FULL_DOCS["project_set_status"]
    
    @register_tool_category("project")
    @tool
    def project_add_directory(
        project_id: str, 
        directory: str, 
        as_main: bool = False
    ) -> dict | None:
        """Add a directory to a project. Use tool_help("project_add_directory") for details."""
        validated_dir, error = _validate_directory(directory)
        if error:
            return {"error": error}
        if validated_dir is None:
            return {"error": "Invalid directory path"}
        
        if as_main:
            project = store.update(project_id, main_directory=validated_dir)
        else:
            project = store.add_related_directory(project_id, validated_dir)
        
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_add_directory._full_doc_ = _FULL_DOCS["project_add_directory"]
    
    @register_tool_category("project")
    @tool
    def project_remove_directory(project_id: str, directory: str) -> dict | None:
        """Remove a directory from project. Use tool_help("project_remove_directory") for details."""
        project = store.remove_related_directory(project_id, directory)
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_remove_directory._full_doc_ = _FULL_DOCS["project_remove_directory"]
    
    @register_tool_category("project")
    @tool
    def project_set_tags(project_id: str, tags: list[str]) -> dict | None:
        """Replace all tags on a project. Use tool_help("project_set_tags") for details."""
        project = store.set_tags(project_id, tags)
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_set_tags._full_doc_ = _FULL_DOCS["project_set_tags"]
    
    @register_tool_category("project")
    @tool
    def project_add_tag(project_id: str, tag: str) -> dict | None:
        """Add a tag to a project. Use tool_help("project_add_tag") for details."""
        project = store.add_tag(project_id, tag)
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_add_tag._full_doc_ = _FULL_DOCS["project_add_tag"]
    
    @register_tool_category("project")
    @tool
    def project_remove_tag(project_id: str, tag: str) -> dict | None:
        """Remove a tag from a project. Use tool_help("project_remove_tag") for details."""
        project = store.remove_tag(project_id, tag)
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_remove_tag._full_doc_ = _FULL_DOCS["project_remove_tag"]
    
    @register_tool_category("project")
    @tool
    def project_set_shortnames(project_id: str, shortnames: list[str]) -> dict | None:
        """Replace all shortnames on a project. Use tool_help("project_set_shortnames") for details."""
        project = store.set_shortnames(project_id, shortnames)
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_set_shortnames._full_doc_ = _FULL_DOCS["project_set_shortnames"]
    
    @register_tool_category("project")
    @tool
    def project_add_shortname(project_id: str, shortname: str) -> dict | None:
        """Add a shortname to a project. Use tool_help("project_add_shortname") for details."""
        project = store.add_shortname(project_id, shortname)
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_add_shortname._full_doc_ = _FULL_DOCS["project_add_shortname"]
    
    @register_tool_category("project")
    @tool
    def project_remove_shortname(project_id: str, shortname: str) -> dict | None:
        """Remove a shortname from a project. Use tool_help("project_remove_shortname") for details."""
        project = store.remove_shortname(project_id, shortname)
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_remove_shortname._full_doc_ = _FULL_DOCS["project_remove_shortname"]
    
    @register_tool_category("project")
    @tool
    def project_set_metadata(project_id: str, key: str, value) -> dict | None:
        """Set a custom metadata field. Use tool_help("project_set_metadata") for details."""
        # Validate that value is JSON-serializable
        import json
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            return {"error": "Metadata value must be JSON-serializable", "error_code": "INVALID_VALUE"}
        
        project = store.set_metadata(project_id, key, value)
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_set_metadata._full_doc_ = _FULL_DOCS["project_set_metadata"]
    
    @register_tool_category("project")
    @tool
    def project_delete_metadata(project_id: str, key: str) -> dict | None:
        """Delete a metadata field. Use tool_help("project_delete_metadata") for details."""
        project = store.delete_metadata(project_id, key)
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_delete_metadata._full_doc_ = _FULL_DOCS["project_delete_metadata"]
    
    @register_tool_category("project")
    @tool
    def project_link(
        project_id: str, 
        entity_type: str, 
        entity_id: str
    ) -> dict | None:
        """Link a project to another entity. Use tool_help("project_link") for details."""
        project = store.add_relationship(project_id, entity_type, entity_id)
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_link._full_doc_ = _FULL_DOCS["project_link"]
    
    @register_tool_category("project")
    @tool
    def project_unlink(
        project_id: str, 
        entity_type: str, 
        entity_id: str
    ) -> dict | None:
        """Remove a link to another entity. Use tool_help("project_unlink") for details."""
        project = store.remove_relationship(project_id, entity_type, entity_id)
        if project is None:
            return {"error": "Project not found", "error_code": "NOT_FOUND"}
        return project.to_dict()
    project_unlink._full_doc_ = _FULL_DOCS["project_unlink"]
    
    @register_tool_category("project")
    @tool
    def project_delete(project_id: str) -> dict:
        """Delete a project permanently. Use tool_help("project_delete") for details."""
        return store.delete(project_id)
    project_delete._full_doc_ = _FULL_DOCS["project_delete"]
    
    return [
        project_create,
        project_get,
        project_list,
        project_search,
        project_get_by_instance,
        project_get_by_directory,
        project_update,
        project_set_status,
        project_add_directory,
        project_remove_directory,
        project_set_tags,
        project_add_tag,
        project_remove_tag,
        project_set_shortnames,
        project_add_shortname,
        project_remove_shortname,
        project_set_metadata,
        project_delete_metadata,
        project_link,
        project_unlink,
        project_delete,
    ]
