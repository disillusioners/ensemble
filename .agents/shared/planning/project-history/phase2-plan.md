# Phase 2: Tool Layer — Project History Tools

## Objective
Create 4 agent tools (`project_history_add`, `project_history_list`, `project_history_search`, `project_history_delete`) following the existing factory pattern with `@register_tool_category`, `@tool`, and `_full_doc_` metadata.

## Coupling
- **Depends on**: Phase 1 (Data Layer)
- **Coupling type**: tight
- **Shared files with other phases**: 
  - `daemon/tools/project_history.py` — new file (Phase 3 imports the factory function)
- **Shared APIs/interfaces**: 
  - `SQLModelProjectRepository` methods from Phase 1 (add_history_entry, list_history_entries, search_history_entries, delete_history_entry, get_history_entry)
- **Why this coupling**: Tools directly call repository methods defined in Phase 1; integration layer (Phase 3) imports the factory function.

## Context
- Reference implementation: `daemon/tools/critical_experience.py` (252 lines)
- Factory pattern: `create_<category>_tools(store, current_instance_id, agent_id) -> list`
- Module-level constants: `CATEGORY_NAME`, `CATEGORY_DOC`, `_FULL_DOCS`
- Each tool uses `@register_tool_category(CATEGORY_NAME)` + `@tool` decorators
- Each tool sets `._full_doc_ = _FULL_DOCS["tool_name"]`
- Factory returns list of all created tools

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create module skeleton | New file `daemon/tools/project_history.py` with `CATEGORY_NAME = "project_history"`, `CATEGORY_DOC`, and `_FULL_DOCS` dict skeleton. | `daemon/tools/project_history.py` |
| 2 | Implement `project_history_add` tool | Parameters: project_id (required), entry_type (required, validate against HistoryEntryType), summary (required, max 300 chars), details (optional, max 5000 chars), entry_metadata (optional dict). Auto-inject agent_id and instance_id from factory closure. Returns created entry as dict. | `daemon/tools/project_history.py` |
| 3 | Implement `project_history_list` tool | Parameters: project_id (required), limit (default 20), offset (default 0), entry_type (optional filter). Returns entries list + total count + pagination info. | `daemon/tools/project_history.py` |
| 4 | Implement `project_history_search` tool | Parameters: project_id (required), query (required), limit (default 20), offset (default 0). Returns matching entries + total count. | `daemon/tools/project_history.py` |
| 5 | Implement `project_history_delete` tool | Parameters: project_id (required), entry_id (required). Validates entry exists AND belongs to the given project before deletion. Returns success/failure. | `daemon/tools/project_history.py` |
| 6 | Write `_FULL_DOCS` entries | Detailed documentation for each tool including description, parameters, return values, and usage examples. | `daemon/tools/project_history.py` |
| 7 | Add testing for tools | Integration tests: tool add/list/search/delete with valid params, error cases (invalid entry_type, wrong project_id for delete), return format consistency. | `tests/` (new or existing test file) |

## Key Files
- `daemon/tools/project_history.py` — New file (~250-300 lines, similar to critical_experience.py)

## Detailed Implementation Notes

### Module Structure
```python
"""Project history tools for recording and querying project events."""

CATEGORY_NAME = "project_history"
CATEGORY_DOC = """Manage project history entries — record milestones, commits, phase completions, 
bugfixes, deployments, and other project events. History provides a chronological record of 
what happened in a project, complementing critical experience (which tracks learned lessons)."""

_FULL_DOCS = {
    "project_history_add": """...""",
    "project_history_list": """...""",
    "project_history_search": """...""",
    "project_history_delete": """...""",
}

_MAX_SUMMARY_LEN = 300
_MAX_DETAILS_LEN = 5000
_VALID_ENTRY_TYPES = {t.value for t in HistoryEntryType}


def create_project_history_tools(store, current_instance_id, agent_id) -> list:
    """Create project history tools with instance context."""
    
    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_history_add(
        project_id: str,
        entry_type: str,
        summary: str,
        details: str | None = None,
        entry_metadata: dict | None = None,
    ) -> list[dict]:
        """Record a history entry for a project.
        
        Args:
            project_id: The project to add history to
            entry_type: One of: milestone, commit, phase, bugfix, deployment, note, config_change, feature, other
            summary: Brief description of what happened (max 300 chars)
            details: Optional longer description (max 5000 chars)
            entry_metadata: Optional structured data to attach
        
        Returns:
            List containing the created history entry dict
        """
        if entry_type not in _VALID_ENTRY_TYPES:
            return [{"error": f"Invalid entry_type '{entry_type}'. Must be one of: {sorted(_VALID_ENTRY_TYPES)}"}]
        
        project = store.get(project_id)
        if not project:
            return [{"error": f"Project '{project_id}' not found"}]
        
        entry = store.add_history_entry(
            project_id=project_id,
            entry_type=entry_type,
            summary=summary[:_MAX_SUMMARY_LEN],
            details=details[:_MAX_DETAILS_LEN] if details else None,
            agent_id=agent_id,
            instance_id=current_instance_id,
            entry_metadata=entry_metadata,
        )
        return [entry.to_dict()]
    
    project_history_add._full_doc_ = _FULL_DOCS["project_history_add"]
    
    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_history_list(
        project_id: str,
        limit: int = 20,
        offset: int = 0,
        entry_type: str | None = None,
    ) -> list[dict]:
        """List project history entries with paging.
        
        Args:
            project_id: The project whose history to list
            limit: Max entries to return (default 20, max 100)
            offset: Number of entries to skip
            entry_type: Optional filter by entry type
        
        Returns:
            List with 'entries' array, 'total' count, 'limit', 'offset'
        """
        limit = min(limit, 100)
        entries, total = store.list_history_entries(
            project_id=project_id,
            limit=limit,
            offset=offset,
            entry_type=entry_type,
        )
        return [{
            "entries": [e.to_dict() for e in entries],
            "total": total,
            "limit": limit,
            "offset": offset,
        }]
    
    project_history_list._full_doc_ = _FULL_DOCS["project_history_list"]
    
    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_history_search(
        project_id: str,
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """Search project history entries by text.
        
        Args:
            project_id: The project whose history to search
            query: Search text to find in summaries and details
            limit: Max entries to return (default 20, max 100)
            offset: Number of entries to skip
        
        Returns:
            List with 'entries' array, 'total' count, 'limit', 'offset', 'query'
        """
        limit = min(limit, 100)
        entries, total = store.search_history_entries(
            project_id=project_id,
            query=query,
            limit=limit,
            offset=offset,
        )
        return [{
            "entries": [e.to_dict() for e in entries],
            "total": total,
            "limit": limit,
            "offset": offset,
            "query": query,
        }]
    
    project_history_search._full_doc_ = _FULL_DOCS["project_history_search"]
    
    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_history_delete(
        project_id: str,
        entry_id: str,
    ) -> list[dict]:
        """Delete a specific history entry.
        
        Args:
            project_id: The project this entry belongs to (validates ownership)
            entry_id: The ID of the history entry to delete
        
        Returns:
            List with success status
        """
        deleted = store.delete_history_entry(entry_id, project_id=project_id)
        if not deleted:
            return [{"error": f"History entry '{entry_id}' not found in project '{project_id}'"}]
        return [{"success": True, "deleted_entry_id": entry_id}]
    
    project_history_delete._full_doc_ = _FULL_DOCS["project_history_delete"]
    
    return [project_history_add, project_history_list, project_history_search, project_history_delete]
```

### `_FULL_DOCS` Content
Each entry should follow the pattern in `critical_experience.py`:
- Description of what the tool does
- When to use it
- Parameter details with types and constraints
- Return value format
- Usage examples

## Constraints
- Follow exact same factory pattern as `critical_experience.py` and `project.py`
- All tools must return `list[dict]` (consistent with existing pattern)
- Entry type validation against `HistoryEntryType` enum values
- Summary truncated to 300 chars max
- Details truncated to 5000 chars max (prevents context/storage bloat)
- Limit capped at 100 for list/search to prevent excessive results
- Import model enum from `daemon.repositories.project.models`
- `project_history_delete` requires both `project_id` and `entry_id` — validates entry belongs to project
- All return formats are consistent: both list and search return `{entries, total, limit, offset}`, search additionally returns `query`
- Parameter naming: use `entry_metadata` (not `metadata`) everywhere

## Testing Strategy
Add tests covering the tool layer:
- **Add:** valid entry, invalid entry_type, project not found, summary truncated, details truncated
- **List:** with paging (limit/offset), with entry_type filter, empty results
- **Search:** match in summary, match in details, no match, consistent return format
- **Delete:** valid deletion, entry not found, wrong project_id (ownership validation)
- **Return format consistency:** list and search both include limit/offset fields

## Deliverables
- [ ] `daemon/tools/project_history.py` with 4 tools
- [ ] All tools decorated and documented properly
- [ ] Factory function returns list of 4 tools
- [ ] Tool integration tests
