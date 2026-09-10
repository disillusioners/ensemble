"""Critical Notes tools for project-scoped experience management.

Tools for adding, listing, and removing critical notes entries
that capture important lessons learned during project work.

Design notes (2026-09-10):
- No silent fuzzy-upsert into unrelated rows. Strict collision detection:
  two summaries collide only if their NORMALIZED forms are equal
  (lowercased + whitespace-collapsed). Token-overlap matching is forbidden
  because it falsely merged distinct notes sharing generic vocabulary
  ("fixed", "restart", "merge") in prod (2026-09-10 hijack incident).
- On collision, the tool REJECTS with an error naming the colliding
  entry's id and summary. Caller decides whether to remove+re-add or
  pass entry_id explicitly to update.
- entry_id (optional) supports explicit in-place update. No fuzzy.
- Cap raised to 50 (live store was already at 31). Past the cap, the
  tool REJECTS with an error naming eviction candidates — no silent
  data loss.
"""

from __future__ import annotations

from datetime import datetime, timezone

from langchain_core.tools import tool

from ..repositories.project.models import (
    CriticalNotes,
    CriticalNotesCategory,
    CriticalNotesPriority,
    CriticalNoteModel,
)
from ..repositories.project.repository import SQLModelProjectRepository
from ._tool_registry import register_tool_category

CATEGORY_NAME = "critical_notes"
CATEGORY_DOC = "Manage critical notes entries for projects — lessons learned, important observations, and key insights."

# Raised from 30 to 50 (live store was already at 31 on 2026-09-10;
# silent eviction is removed, so we need headroom for genuine growth).
_MAX_ENTRIES = 50
_MAX_SUMMARY_LEN = 200
_MAX_EVICTION_CANDIDATES_NAMED = 3
_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2}


def _is_valid_category(category: str) -> bool:
    """Check if category is a valid CriticalNotesCategory value."""
    return category in CriticalNotesCategory._value2member_map_


def _is_valid_priority(priority: str) -> bool:
    """Check if priority is a valid CriticalNotesPriority value."""
    return priority in CriticalNotesPriority._value2member_map_


def _normalize_summary(summary: str) -> str:
    """Normalize a summary for strict equality matching.

    Lowercases + collapses all whitespace runs to a single space + strips.
    This is the ONLY form compared when checking duplicates — distinct
    summaries must never collide.
    """
    return " ".join(summary.lower().split())


def _find_near_duplicate_entry(
    entries: list[CriticalNotes], summary: str
) -> CriticalNotes | None:
    """Return a colliding entry if its normalized summary equals the new one.

    Replaces the old token-overlap _find_similar_entry: that matcher
    silently merged unrelated rows that shared generic vocabulary
    ("fixed", "restart", "merge", "pending"). Normalized equality is
    strict by design — a summary differing in any word, punctuation,
    or number is NOT a duplicate.
    """
    target = _normalize_summary(summary)
    for entry in entries:
        if _normalize_summary(entry.summary) == target:
            return entry
    return None


def _eviction_candidates(entries: list[CriticalNotes]) -> list[CriticalNotes]:
    """Return the entries that WOULD be evicted, oldest-first among lowest priority.

    Used to name candidates in the cap-rejection error so the caller can
    make an informed decision. Pure function — does NOT mutate.

    Sort key: negative priority_value first (lowest priority surfaces),
    then created_at ASC (oldest among ties). Note this differs from the
    pre-existing buggy ascending-priority sort — the new contract is
    "evict the LEAST important first", which is the sensible semantic.
    """
    return sorted(
        entries,
        key=lambda e: (-_PRIORITY_ORDER.get(e.priority, 2), e.created_at),
    )


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
        entry_id: str | None = None,
    ) -> dict:
        """Add or update a critical notes entry for a project. Use tool_help() for details.

        Behavior:
        - If entry_id is provided, that specific row is updated exactly
          (summary/priority/reference/source_agent). No fuzzy matching.
        - Otherwise, if a near-duplicate summary already exists in the
          same project, the call REJECTS with an error naming the
          colliding entry's id and summary — caller decides next step.
        - Otherwise, a new entry is appended. If the project is at the
          cap (_MAX_ENTRIES=50), the call REJECTS with an error naming
          eviction candidates — no silent data loss.
        """
        # Step 1: Validate inputs
        if not _is_valid_category(category):
            return {"error": f"Invalid category '{category}'. Valid: {[c.value for c in CriticalNotesCategory]}"}
        if not _is_valid_priority(priority):
            return {"error": f"Invalid priority '{priority}'. Valid: {[p.value for p in CriticalNotesPriority]}"}
        if len(summary) > _MAX_SUMMARY_LEN:
            return {"error": f"Summary must be <= {_MAX_SUMMARY_LEN} chars, got {len(summary)}"}
        if not summary.strip():
            return {"error": "Summary cannot be empty"}
        if entry_id is not None and not entry_id.strip():
            return {"error": "entry_id must be non-empty when provided"}

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

        # Step 4: EXPLICIT UPDATE PATH (entry_id provided)
        if entry_id is not None:
            target = next((e for e in entries if e.id == entry_id), None)
            if target is None:
                return {"error": f"Entry '{entry_id}' not found in project '{project_id}'"}

            updated = repo.update_critical_note(
                project_id,
                entry_id,
                priority=priority,
                summary=summary,
                reference=reference,
                source_agent=agent_id,
            )
            if updated:
                return updated.to_dict()
            # Repo returned None — fall back to echoing intended new state
            return CriticalNotes(
                id=entry_id,
                category=target.category,
                priority=priority,
                summary=summary,
                reference=reference,
                source_agent=agent_id,
            ).to_dict()

        # Step 5: STRICT COLLISION CHECK (new-add path)
        duplicate = _find_near_duplicate_entry(entries, summary)
        if duplicate is not None:
            return {
                "error": (
                    f"Near-duplicate critical note already exists "
                    f"(id={duplicate.id}): {duplicate.summary}. "
                    f"To update, pass entry_id='{duplicate.id}'."
                )
            }

        # Step 6: CAP CHECK (new-add path)
        if len(entries) >= _MAX_ENTRIES:
            candidates = _eviction_candidates(entries)[:_MAX_EVICTION_CANDIDATES_NAMED]
            candidate_lines = [
                f"  - id={c.id} priority={c.priority} created_at={c.created_at} summary={c.summary!r}"
                for c in candidates
            ]
            return {
                "error": (
                    f"Project '{project_id}' is at the cap of {_MAX_ENTRIES} critical notes. "
                    f"Remove one of the following eviction candidates and re-add, or use "
                    f"project_cn_remove + project_cn_add.\n"
                    + "\n".join(candidate_lines)
                )
            }

        # Step 7: ADD NEW ENTRY
        added = repo.add_critical_note(
            project_id,
            source_agent=agent_id,
            category=category,
            priority=priority,
            summary=summary,
            reference=reference,
        )
        return CriticalNotes(**added.to_dict()).to_dict()

    project_cn_add._full_doc_ = """Add or update a critical notes entry for a project.

When adding an entry:
- If a near-duplicate summary (normalized equality) already exists for the
  project, the call REJECTS with an error naming the colliding entry's id
  and summary. The caller decides whether to remove it via project_cn_remove
  or update it via entry_id.
- If the list is full (50 entries), the call REJECTS with an error naming
  eviction candidates. No silent eviction.
- Pass entry_id to update a specific entry in place (exact match, no fuzzy).

Args:
    project_id: The project to add the note to
    category: One of: convention, pattern, risk, decision, constraint
    priority: One of: critical, high, medium
    summary: Brief description (max 200 chars)
    reference: Optional reference URL or path
    entry_id: Optional. When provided, that entry is updated exactly
       (no fuzzy match). If omitted, a near-duplicate check rejects the
       call instead of silently merging.

Returns:
    The created or updated entry as a dict, or an error dict on
    validation failure, collision, or cap hit."""

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
