"""Project history tools for tracking project events.

Tools for adding, listing, searching, and removing project history entries
that provide a chronological record of what happened in a project.
"""

from __future__ import annotations

from langchain_core.tools import tool

from ..repositories.project.models import HistoryEntryType
from ._tool_registry import register_tool_category

CATEGORY_NAME = "project_history"
CATEGORY_DOC = """Manage project history entries — record milestones, commits, phase completions,
bugfixes, deployments, and other project events. History provides a chronological record of
what happened in a project, complementing critical experience (which tracks learned lessons)."""

_MAX_SUMMARY_LEN = 300
_MAX_DETAILS_LEN = 5000


# Full documentation strings for each tool
_FULL_DOCS = {
    "project_history_add": """Add a history entry to a project.

Records a project event such as a milestone, commit, deployment, or other
notable occurrence. Entries are timestamped and can be filtered by type.

Args:
    project_id: The project ID to add the entry to.
    entry_type: Type of event — milestone, commit, phase, bugfix, deployment,
               note, config_change, or other.
    summary: Brief description of the event (max 300 chars, auto-truncated).
    details: Optional detailed description (max 5000 chars, auto-truncated).
    entry_metadata: Optional custom metadata dict for additional context.

Returns:
    The created history entry as a dict.

Example:
    project_history_add(
        project_id="abc-123",
        entry_type="deployment",
        summary="Deployed v2.0.0 to production",
        details="Blue-green deployment with zero downtime"
    )""",

    "project_history_list": """List history entries for a project.

Returns entries in reverse chronological order (newest first).

Args:
    project_id: The project ID to list entries for.
    limit: Maximum entries to return (default 20, max 100).
    offset: Number of entries to skip for pagination.
    entry_type: Optional filter by entry type.

Returns:
    Dict with entries list, total count, limit, and offset.""",

    "project_history_search": """Search history entries by query.

Searches both summary and details fields for matching text.

Args:
    project_id: The project ID to search within.
    query: Search string to match.
    limit: Maximum entries to return (default 20, max 100).
    offset: Number of entries to skip for pagination.

Returns:
    Dict with matching entries, total count, limit, offset, and query.""",

    "project_history_delete": """Delete a history entry by ID.

Removes a specific history entry from the project. Requires entry_id
and matching project_id for ownership verification.

Args:
    project_id: The project ID the entry belongs to.
    entry_id: The ID of the entry to delete.

Returns:
    Dict with success status and deleted_entry_id, or error message.""",
}


def _is_valid_entry_type(entry_type: str) -> bool:
    """Check if entry_type is a valid HistoryEntryType value."""
    return entry_type in HistoryEntryType._value2member_map_


def create_project_history_tools(
    store, current_instance_id: str = "", agent_id: str = ""
) -> list:
    """Create project history tools bound to a project store."""

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_history_add(
        project_id: str,
        entry_type: str,
        summary: str,
        details: str | None = None,
        entry_metadata: dict | None = None,
    ) -> dict:
        """Add a history entry to a project. Use tool_help() for details."""
        # Step 1: Validate entry_type
        if not _is_valid_entry_type(entry_type):
            valid_types = [e.value for e in HistoryEntryType]
            return {"error": f"Invalid entry_type '{entry_type}'. Valid: {valid_types}"}

        # Step 2: Validate project exists
        project = store.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        # Step 3: Validate non-empty summary
        if not summary or not summary.strip():
            return {"error": "Summary cannot be empty"}

        # Step 4: Truncate inputs
        summary = summary[:_MAX_SUMMARY_LEN]
        if details:
            details = details[:_MAX_DETAILS_LEN]

        # Step 5: Add entry via store (returns dict already)
        entry = store.add_history_entry(
            project_id=project_id,
            entry_type=entry_type,
            summary=summary,
            details=details,
            source_agent=agent_id,
            source_instance_id=current_instance_id,
            entry_metadata=entry_metadata,
        )

        return entry
    project_history_add._full_doc_ = _FULL_DOCS["project_history_add"]

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_history_list(
        project_id: str,
        limit: int = 20,
        offset: int = 0,
        entry_type: str | None = None,
    ) -> dict:
        """List history entries for a project. Use tool_help() for details."""
        # Validate project exists
        project = store.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        # Clamp and cap limit at 100
        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        # Validate entry_type if provided
        if entry_type is not None and not _is_valid_entry_type(entry_type):
            valid_types = [e.value for e in HistoryEntryType]
            return {"error": f"Invalid entry_type '{entry_type}'. Valid: {valid_types}"}

        # Call store (returns dict with entries, total, limit, offset)
        result = store.list_history_entries(
            project_id=project_id,
            entry_type=entry_type,
            limit=limit,
            offset=offset,
        )

        return result
    project_history_list._full_doc_ = _FULL_DOCS["project_history_list"]

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_history_search(
        project_id: str,
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Search history entries by query. Use tool_help() for details."""
        # Validate project exists
        project = store.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        # Clamp and cap limit at 100
        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        # Call store (returns dict with entries, total, limit, offset, query)
        result = store.search_history_entries(
            project_id=project_id,
            query=query,
            limit=limit,
            offset=offset,
        )

        return result
    project_history_search._full_doc_ = _FULL_DOCS["project_history_search"]

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_history_delete(
        project_id: str,
        entry_id: str,
    ) -> dict:
        """Delete a history entry by ID. Use tool_help() for details."""
        # Validate project exists
        project = store.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        # Delete entry
        deleted = store.delete_history_entry(entry_id, project_id=project_id)

        if not deleted:
            return {"error": f"Entry '{entry_id}' not found in project '{project_id}'"}

        return {"success": True, "deleted_entry_id": entry_id}
    project_history_delete._full_doc_ = _FULL_DOCS["project_history_delete"]

    return [project_history_add, project_history_list, project_history_search, project_history_delete]
