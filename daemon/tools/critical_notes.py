"""Critical Notes tools for project-scoped experience management.

Tools for adding, listing, and removing critical notes entries
that capture important lessons learned during project work.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from langchain_core.tools import tool

from ..repositories.project.models import (
    CriticalNotes,
    CriticalNotesCategory,
    CriticalNotesPriority,
)
from ..repositories.project.repository import SQLModelProjectRepository
from ..repositories.project.models import CriticalNoteModel
from ._tool_registry import register_tool_category

CATEGORY_NAME = "critical_notes"
CATEGORY_DOC = "Manage critical notes entries for projects — lessons learned, important observations, and key insights."

_MAX_ENTRIES = 30
_MAX_SUMMARY_LEN = 200
_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2}


def _is_valid_category(category: str) -> bool:
    """Check if category is a valid CriticalNotesCategory value."""
    return category in CriticalNotesCategory._value2member_map_


def _is_valid_priority(priority: str) -> bool:
    """Check if priority is a valid CriticalNotesPriority value."""
    return priority in CriticalNotesPriority._value2member_map_


def _find_similar_entry(
    entries: list[CriticalNotes], category: str, summary: str
) -> CriticalNotes | None:
    """Find an entry with same category and similar theme via keyword overlap."""
    new_keywords = {w.lower() for w in summary.split() if len(w) > 3}
    if len(new_keywords) < 2:
        return None

    for entry in entries:
        if entry.category != category:
            continue
        existing_keywords = {w.lower() for w in entry.summary.split() if len(w) > 3}
        overlap = new_keywords & existing_keywords
        if len(overlap) >= 2:
            return entry
    return None


def _merge_entries(existing: CriticalNotes, new: CriticalNotes) -> CriticalNotes:
    """Merge a new entry into an existing one. Keep concise summary, preserve timestamps."""
    # Keep the shorter summary (more concise), or new one if equal
    merged_summary = (
        existing.summary if len(existing.summary) <= len(new.summary) else new.summary
    )
    # Keep reference from whichever has one
    merged_reference = new.reference or existing.reference
    # Keep the higher priority
    merged_priority = (
        existing.priority
        if _PRIORITY_ORDER.get(existing.priority, 2) <= _PRIORITY_ORDER.get(new.priority, 2)
        else new.priority
    )

    return CriticalNotes(
        id=existing.id,
        category=existing.category,
        priority=merged_priority,
        summary=merged_summary,
        reference=merged_reference,
        source_agent=new.source_agent,
        created_at=existing.created_at,
        updated_at=datetime.utcnow().isoformat(),
    )


def _evict_if_needed(entries: list[CriticalNotes]) -> list[CriticalNotes]:
    """Evict oldest lowest-priority entry if at max capacity."""
    if len(entries) < _MAX_ENTRIES:
        return entries

    priority_order = _PRIORITY_ORDER
    sorted_entries = sorted(
        entries,
        key=lambda e: (priority_order.get(e.priority, 2), e.created_at),
    )
    # Remove the first one (lowest priority, oldest)
    return sorted_entries[1:]


def create_critical_notes_tools(
    repo: SQLModelProjectRepository, current_instance_id: str = "", agent_id: str = ""
) -> list:
    """Create critical notes management tools bound to a project repository."""

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_cn_add(
        project_id: str,
        category: str,
        priority: str,
        summary: str,
        reference: str | None = None,
    ) -> dict:
        """Add or update a critical notes entry for a project. Use tool_help() for details."""
        # Step 1: Validate inputs
        if not _is_valid_category(category):
            return {"error": f"Invalid category '{category}'. Valid: {[c.value for c in CriticalNotesCategory]}"}
        if not _is_valid_priority(priority):
            return {"error": f"Invalid priority '{priority}'. Valid: {[p.value for p in CriticalNotesPriority]}"}
        if len(summary) > _MAX_SUMMARY_LEN:
            return {"error": f"Summary must be <= {_MAX_SUMMARY_LEN} chars, got {len(summary)}"}
        if not summary.strip():
            return {"error": "Summary cannot be empty"}

        # Step 2: Check project exists
        project = repo.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        # Step 3: Load current entries
        notes_list = repo.list_critical_notes(project_id)
        entries = [
            CriticalNotes(**note.to_dict()) if isinstance(note, CriticalNoteModel) else note
            for note in notes_list
        ]

        # Step 4: Check for merge
        similar = _find_similar_entry(entries, category, summary)

        if similar is not None:
            # Step 5: MERGE PATH
            merged = _merge_entries(similar, CriticalNotes(
                category=category,
                priority=priority,
                summary=summary,
                reference=reference,
                source_agent=agent_id,
            ))
            # Update via repository
            updated = repo.update_critical_note(
                project_id,
                merged.id,
                priority=merged.priority,
                summary=merged.summary,
                reference=merged.reference,
            )
            if updated:
                return updated.to_dict()
            return merged.to_dict()
        else:
            # Step 6: NEW ENTRY PATH — evict first, then add
            original_count = len(entries)

            # Identify the entry to evict before calling _evict_if_needed
            evicted_entry_id = None
            if original_count >= _MAX_ENTRIES:
                # Find the entry that would be evicted (oldest, lowest priority)
                sorted_entries = sorted(
                    entries,
                    key=lambda e: (_PRIORITY_ORDER.get(e.priority, 2), e.created_at),
                )
                evicted_entry_id = sorted_entries[0].id

            entries = _evict_if_needed(entries)

            # Remove evicted entry from repository if needed
            if evicted_entry_id:
                repo.remove_critical_note(project_id, evicted_entry_id)

            # Add new entry via repository
            added = repo.add_critical_note(
                project_id,
                source_agent=agent_id,
                category=category,
                priority=priority,
                summary=summary,
                reference=reference,
            )
            # Return as CriticalNotes dict
            return CriticalNotes(**added.to_dict()).to_dict()

    project_cn_add._full_doc_ = """Add or update a critical notes entry for a project.

When adding an entry, the system checks if a similar entry already exists:
- If a similar entry (same category, >=2 keyword overlap) exists, it merges them
- If the list is full (30 entries), the oldest lowest-priority entry is evicted

Args:
    project_id: The project to add the note to
    category: One of: convention, pattern, risk, decision, constraint
    priority: One of: critical, high, medium
    summary: Brief description (max 200 chars)
    reference: Optional reference URL or path

Returns:
    The created or merged entry as a dict."""

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_cn_list(project_id: str) -> dict:
        """List all critical notes entries for a project. Use tool_help() for details."""
        project = repo.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        notes_list = repo.list_critical_notes(project_id)
        entries = [
            CriticalNotes(**note.to_dict()) if isinstance(note, CriticalNoteModel) else note
            for note in notes_list
        ]
        return {
            "project_id": project_id,
            "count": len(entries),
            "entries": [e.to_dict() for e in entries],
        }

    project_cn_list._full_doc_ = """List all critical notes entries for a project.

Args:
    project_id: The project to list notes for

Returns:
    Dict with project_id, count, and entries list."""

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_cn_remove(project_id: str, entry_id: str) -> dict:
        """Remove a specific critical notes entry by ID. Use tool_help() for details."""
        project = repo.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        # Get the entry first to return its details
        removed_entry = repo.get_critical_note(project_id, entry_id)
        if removed_entry is None:
            return {"error": f"Entry '{entry_id}' not found"}

        summary = removed_entry.summary

        # Remove via repository
        removed = repo.remove_critical_note(project_id, entry_id)

        return {
            "removed": removed,
            "entry_id": entry_id,
            "summary": summary,
        }

    project_cn_remove._full_doc_ = """Remove a specific critical notes entry by ID.

Args:
    project_id: The project to remove the entry from
    entry_id: The ID of the entry to remove

Returns:
    Confirmation dict with removed entry details."""

    return [project_cn_add, project_cn_list, project_cn_remove]
