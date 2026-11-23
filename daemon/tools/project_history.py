"""Project history tools for tracking project events.

Tools for adding, listing, searching, and removing project history entries
that provide a chronological record of what happened in a project.
"""

from __future__ import annotations

import logging


from ._schema_registry import tool
from ..repositories.project.models import HistoryEntryType
from daemon.constants import BLUEPRINT_ACTIVE_METADATA_KEY, SYSTEM_DEFAULT_PROJECT_NAME
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "project_history"
CATEGORY_DOC = """Manage project history entries — record milestones, commits, phase completions,
bugfixes, deployments, features, and other project events. History provides a chronological record of
what happened in a project, complementing critical notes (which tracks learned lessons)."""

_MAX_SUMMARY_LEN = 300
_MAX_DETAILS_LEN = 5000


# Full documentation strings for each tool
_FULL_DOCS = {
    "project_history_add": """Add a history entry to a project.

Records a project event such as a milestone, commit, deployment, or other
notable occurrence. Entries are timestamped and can be filtered by type.

The project is addressed by ``project_id``, ``name`` or ``shortname``
(``project_id`` wins, then ``name``, then ``shortname``). Name/shortname
lookups run the resolution ladder that resolves to exactly ONE project; a
reference matching several projects is refused with the candidates so the
caller can retry with an explicit project_id.

Args:
    project_id: The project ID to add the entry to (optional when name or
               shortname is given).
    name: The project name to add the entry to (used if project_id is not
          provided).
    shortname: A project shortname/nickname (used if project_id and name
               are not provided).
    entry_type: Type of event — milestone, commit, phase, bugfix, deployment,
               note, config_change, feature, or other.
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

The project is addressed by ``project_id``, ``name`` or ``shortname``
(``project_id`` wins, then ``name``, then ``shortname``). Name/shortname
lookups run the resolution ladder that resolves to exactly ONE project; a
reference matching several projects is refused with the candidates so the
caller can retry with an explicit project_id.

Args:
    project_id: The project ID to list entries for (optional when name or
               shortname is given).
    name: The project name to list entries for (used if project_id is not
          provided).
    shortname: A project shortname/nickname (used if project_id and name
               are not provided).
    limit: Maximum entries to return (default 20, max 100).
    offset: Number of entries to skip for pagination.
    entry_type: Optional filter by entry type.

Returns:
    Dict with entries list, total count, limit, and offset.""",

    "project_history_search": """Search history entries by query.

Searches both summary and details fields for matching text.

The project is addressed by ``project_id``, ``name`` or ``shortname``
(``project_id`` wins, then ``name``, then ``shortname``). Name/shortname
lookups run the resolution ladder that resolves to exactly ONE project; a
reference matching several projects is refused with the candidates so the
caller can retry with an explicit project_id.

Args:
    project_id: The project ID to search within (optional when name or
               shortname is given).
    name: The project name to search within (used if project_id is not
          provided).
    shortname: A project shortname/nickname (used if project_id and name
               are not provided).
    query: Search string to match.
    limit: Maximum entries to return (default 20, max 100).
    offset: Number of entries to skip for pagination.

Returns:
    Dict with matching entries, total count, limit, offset, and query.""",

    "project_history_delete": """Delete a history entry by ID.

Removes a specific history entry from the project. Requires entry_id
and a matching project reference for ownership verification.

The project is addressed by ``project_id``, ``name`` or ``shortname``
(``project_id`` wins, then ``name``, then ``shortname``). Name/shortname
lookups run the resolution ladder that resolves to exactly ONE project; a
reference matching several projects is refused with the candidates so the
caller can retry with an explicit project_id.

Args:
    project_id: The project ID the entry belongs to (optional when name or
               shortname is given).
    name: The project name the entry belongs to (used if project_id is not
          provided).
    shortname: A project shortname/nickname (used if project_id and name
               are not provided).
    entry_id: The ID of the entry to delete.

Returns:
    Dict with success status and deleted_entry_id, or error message.""",
}


def _is_valid_entry_type(entry_type: str) -> bool:
    """Check if entry_type is a valid HistoryEntryType value."""
    return entry_type in HistoryEntryType._value2member_map_


def _resolve_target_project(store, project_id, name, shortname):
    """Resolve a project reference to exactly ONE project.

    This mirrors the ``project_get`` dispatch (``daemon/tools/project.py``)
    EXACTLY — the resolution ladder has ONE owner
    (``SQLModelProjectRepository.resolve_project_reference``) and this
    helper CALLS it. No matching logic is duplicated here.

    Dispatch order:

    * ``project_id`` — direct ``store.get`` lookup. The resolver is NEVER
      called on this path (byte-compat + mock-store compat: a store stub
      without ``resolve_project_reference`` keeps working).
    * ``name`` — the full resolution ladder (``scope="name"``).
    * ``shortname`` — the shortname-only ladder (``scope="shortname"``).
    * none of them — a refusal demanding one of the three.

    Returns:
        tuple: ``(project, resolved_id, error)``. On success ``error`` is
        ``None``, ``project`` is the resolved project and ``resolved_id``
        is the project id every store call must use. On a refusal
        ``project``/``resolved_id`` are ``None`` and ``error`` is the typed
        refusal dict to hand back to the caller.
    """
    if project_id:
        project = store.get(project_id)
        if project is None:
            return None, None, {
                "error": "Project not found",
                "error_code": "NOT_FOUND",
                "query": project_id,
                "candidates": [],
            }
        # The id path keeps the CALLER's id verbatim — byte-compat with the
        # pre-resolution behaviour (for the real repository the store's own
        # id is identical).
        return project, project_id, None

    if name:
        resolution = store.resolve_project_reference(name, scope="name")
        query = name
    elif shortname:
        resolution = store.resolve_project_reference(shortname, scope="shortname")
        query = shortname
    else:
        return None, None, {
            "error": "Must provide either project_id, name, or shortname",
        }

    if resolution.get("found"):
        project = resolution["project"]
        return project, project.project_id, None

    if resolution.get("error_code") == "AMBIGUOUS_PROJECT":
        # NEVER silently pick one of several candidates — the caller gets
        # the candidates and must retry with an explicit id.
        return None, None, {
            "error": resolution["error"],
            "error_code": "AMBIGUOUS_PROJECT",
            "query": query,
            "candidates": resolution.get("candidates", []),
            "candidate_count": resolution.get("candidate_count", 0),
        }

    return None, None, {
        "error": "Project not found",
        "error_code": "NOT_FOUND",
        "query": query,
        "candidates": [],
    }


# History entry types whose addition implies a structural project change
# worth feeding the Blueprint pending-queue (C8). Bugfixes, notes,
# commits, etc. are intentionally excluded — only "shape changed" events
# land in the queue.
_PENDING_QUEUE_ENTRY_TYPES = frozenset({"feature", "milestone"})


def create_project_history_tools(
    store, current_instance_id: str = "", agent_id: str = "",
    manager=None,
) -> list:
    """Create project history tools bound to a project store.

    Args:
        store: The project store.
        current_instance_id: The ID of the current instance.
        agent_id: The ``agent_id`` of the calling instance.
        manager: Optional :class:`InstanceManager` reference. When
            provided, the ``project_history_add`` tool feeds the
            Blueprint pending-queue (Phase 3 / C8) for ``feature`` and
            ``milestone`` entries. Passing ``None`` (or omitting) skips
            the pending-queue hook — the legacy behaviour.
    """

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_history_add(
        project_id: str | None = None,
        name: str | None = None,
        shortname: str | None = None,
        *,
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

        # Step 2: Resolve the project reference (id / name / shortname)
        project, resolved_id, ref_error = _resolve_target_project(
            store, project_id, name, shortname,
        )
        if ref_error is not None:
            return ref_error

        # Step 3: Validate non-empty summary
        if not summary or not summary.strip():
            return {"error": "Summary cannot be empty"}

        # Step 4: Truncate inputs
        summary = summary[:_MAX_SUMMARY_LEN]
        if details:
            details = details[:_MAX_DETAILS_LEN]

        # Step 5: Add entry via store (returns dict already)
        entry = store.add_history_entry(
            project_id=resolved_id,
            entry_type=entry_type,
            summary=summary,
            details=details,
            source_agent=agent_id,
            source_instance_id=current_instance_id,
            entry_metadata=entry_metadata,
        )

        # ── C8: Blueprint pending-queue hook for history events ──
        # Feature / milestone entries indicate structural project
        # changes worth a Blueprint incremental update. Fire-and-forget
        # INSERT into the pending queue; a failure must not break the
        # history write that just succeeded (logged at WARNING so
        # operators can spot sustained queue issues).
        pending_repo = (
            getattr(manager, "_blueprint_pending_repo", None)
            if manager is not None
            else None
        )
        if (
            pending_repo is not None
            and entry_type in _PENDING_QUEUE_ENTRY_TYPES
            # Skip the system default project — blueprints are never
            # built for the virtual bookkeeping project.
            and project.name != SYSTEM_DEFAULT_PROJECT_NAME
        ):
            # Per-project opt-in gate (default: false = no enqueue).
            # Stored in project metadata KV under
            # ``BLUEPRINT_ACTIVE_METADATA_KEY``; absent = inactive.
            # ``project_history_add`` is a sync ``@tool`` wrapper, so
            # ``get_metadata`` (also sync SQLAlchemy) is called inline
            # — no ``asyncio.to_thread`` needed. A metadata lookup
            # failure must NOT abort the history write that just
            # succeeded; treat the project as inactive (safer default).
            project_blueprint_active = False
            if manager is not None:
                proj_repo = getattr(manager, "_project_repository", None)
                if proj_repo is not None:
                    try:
                        val = proj_repo.get_metadata(
                            resolved_id, BLUEPRINT_ACTIVE_METADATA_KEY,
                        )
                        project_blueprint_active = bool(val)
                    except Exception:
                        project_blueprint_active = False
            if project_blueprint_active:
                try:
                    pending_repo.enqueue(
                        project_id=resolved_id,
                        source_type="history",
                        source_payload={
                            "entry_type": entry_type,
                            "summary": summary,
                            "details": details[:_MAX_DETAILS_LEN] if details else None,
                        },
                    )
                except Exception as bp_err:
                    logger.warning(
                        "Blueprint pending-queue INSERT failed for history event "
                        "(non-fatal): %s",
                        bp_err,
                    )

        return entry
    project_history_add._full_doc_ = _FULL_DOCS["project_history_add"]

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_history_list(
        project_id: str | None = None,
        name: str | None = None,
        shortname: str | None = None,
        *,
        limit: int = 20,
        offset: int = 0,
        entry_type: str | None = None,
    ) -> dict:
        """List history entries for a project. Use tool_help() for details."""
        # Resolve the project reference (id / name / shortname)
        _project, resolved_id, ref_error = _resolve_target_project(
            store, project_id, name, shortname,
        )
        if ref_error is not None:
            return ref_error

        # Clamp and cap limit at 100
        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        # Validate entry_type if provided
        if entry_type is not None and not _is_valid_entry_type(entry_type):
            valid_types = [e.value for e in HistoryEntryType]
            return {"error": f"Invalid entry_type '{entry_type}'. Valid: {valid_types}"}

        # Call store (returns dict with entries, total, limit, offset)
        result = store.list_history_entries(
            project_id=resolved_id,
            entry_type=entry_type,
            limit=limit,
            offset=offset,
        )

        return result
    project_history_list._full_doc_ = _FULL_DOCS["project_history_list"]

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_history_search(
        project_id: str | None = None,
        name: str | None = None,
        shortname: str | None = None,
        *,
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Search history entries by query. Use tool_help() for details."""
        # Resolve the project reference (id / name / shortname)
        _project, resolved_id, ref_error = _resolve_target_project(
            store, project_id, name, shortname,
        )
        if ref_error is not None:
            return ref_error

        # Clamp and cap limit at 100
        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        # Call store (returns dict with entries, total, limit, offset, query)
        result = store.search_history_entries(
            project_id=resolved_id,
            query=query,
            limit=limit,
            offset=offset,
        )

        return result
    project_history_search._full_doc_ = _FULL_DOCS["project_history_search"]

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_history_delete(
        project_id: str | None = None,
        name: str | None = None,
        shortname: str | None = None,
        *,
        entry_id: str,
    ) -> dict:
        """Delete a history entry by ID. Use tool_help() for details."""
        # Resolve the project reference (id / name / shortname)
        _project, resolved_id, ref_error = _resolve_target_project(
            store, project_id, name, shortname,
        )
        if ref_error is not None:
            return ref_error

        # Delete entry
        deleted = store.delete_history_entry(entry_id, project_id=resolved_id)

        if not deleted:
            return {"error": f"Entry '{entry_id}' not found in project '{resolved_id}'"}

        return {"success": True, "deleted_entry_id": entry_id}
    project_history_delete._full_doc_ = _FULL_DOCS["project_history_delete"]

    return [project_history_add, project_history_list, project_history_search, project_history_delete]
