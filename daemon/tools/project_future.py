"""Project future tools — the dev-team forward-improvement wishlist.

Tools for adding, listing, and removing forward-looking improvement ideas for
a project. Each project keeps at most 32 items; when full, the OLDEST idea is
evicted automatically and the add response names what was removed (disclosed,
never silent). This complements project history (what HAS happened) and
critical notes (lessons and traps) — future ideas belong HERE.
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from ..repositories.project.repository import ConcurrentWishlistUpdate
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "project_future"
CATEGORY_DOC = """Manage project future entries — forward-looking improvement ideas and wishlist items for \
a project. Use this for "we should add/try/improve X later" suggestions; use project_history for \
what HAS happened and critical notes for lessons and traps. Each project keeps at most 32 items; \
when full, the OLDEST idea is evicted automatically and the add response names what was removed."""

_MAX_ENTRIES = 32
_MAX_SUMMARY_LEN = 300
_MAX_DETAILS_LEN = 5000


# Full documentation strings for each tool
_FULL_DOCS = {
    "project_future_add": """Add a future-improvement idea to a project's wishlist.

FUTURE IDEAS GO HERE — not critical notes (lessons/traps, capped at 200,
REFUSES when full) and not project history (what already happened). A wish
describes what could BE; this is the lane for "we should add/try/improve X
later".

THE CAP IS EXACTLY 32 ITEMS PER PROJECT. When the wishlist is full, the
OLDEST idea (lowest seq) is evicted AUTOMATICALLY to make room, and the add
response carries an ``"evicted"`` list naming each removed item's id +
summary (and seq + created_at) — eviction is DISCLOSED, never silent. This
deliberately differs from critical notes, which refuse to add when full.

Caps are truncation, not rejection: summary >300 chars and details >5000
chars are silently truncated to fit. There is NO duplicate detection — the
32-item cap + FIFO rotation IS the discipline; if an idea already sits in
the list, adding it again is harmless (the list churns).

Args:
    project_id: The project ID to add the idea to.
    summary: The idea, briefly (max 300 chars, auto-truncated).
    details: Optional elaboration (max 5000 chars, auto-truncated).

Returns:
    The created entry as a flat dict (id, project_id, summary, details,
    source_agent, source_instance_id, created_at, seq) PLUS ``"evicted"``:
    a list of removed items (empty when the wishlist was not full). Correlate
    disclosures with ``id`` — seq is only monotone among LIVE rows (removing
    the newest row lets the next add reuse its seq), never by seq alone.

Example:
    project_future_add(
        project_id="abc-123",
        summary="Add a streaming retry path for long LLM calls",
    )""",

    "project_future_list": """List the future-improvement wishlist for a project.

FUTURE IMPROVEMENT IDEAS GO HERE -- not critical notes (lessons/traps, capped at
200, REFUSES when full) and not project history (what already happened). Use
this to review what the dev team wants to try or build next.

THE CAP IS EXACTLY 32 ITEMS PER PROJECT. When the wishlist is full, the OLDEST
idea (lowest seq) is evicted AUTOMATICALLY on the next add, and that add
response carries an ``"evicted"`` list naming each removed item -- eviction is
DISCLOSED, never silent (FIFO rotation; this list is the read side of that
contract).

Returns entries NEWEST-FIRST (seq descending -- the most recent idea first).
Reads are uncapped by design: if ``total`` ever exceeds 32, the table has
drifted (direct DB writes) and self-heals on the next add.

Args:
    project_id: The project ID to list entries for.
    limit: Maximum entries to return (default 20, max 100).
    offset: Number of entries to skip for pagination.

Returns:
    Dict with entries list, total count, limit, offset, and ``cap`` (always
    32 -- the per-project cap).""",

    "project_future_remove": """Remove (retire) a future-improvement idea by ID.

Retiring an obsolete idea is a NORMAL operation -- normal maintenance of the
wishlist -- no ownership requirement; any granted agent may remove a wish that
has been fulfilled or abandoned (an idea's author and its retiree are usually
different agents).

THE CAP IS EXACTLY 32 ITEMS PER PROJECT, and REMOVAL does NOT evict or shift
anything: it only retires the named entry and frees its seq. Removing the
newest row lets the next add reuse its seq, so identify entries by ``id``,
never by ``seq``.

Wishlist items do NOT feed the Blueprint pending queue -- they are ideas,
not structural project events.

Args:
    project_id: The project ID the entry belongs to.
    entry_id: The ID of the entry to remove.

Returns:
    Dict with ``success`` and the REMOVED entry's full dict (so the caller
    knows exactly what was retired), or an error message when the entry is
    absent from the project.""",
}


def create_project_future_tools(
    store, current_instance_id: str = "", agent_id: str = "",
    manager=None,
) -> list:
    """Create project future (wishlist) tools bound to a project store.

    Args:
        store: The project store.
        current_instance_id: The ID of the current instance.
        agent_id: The ``agent_id`` of the calling instance.
        manager: Accepted for call-site parity with the history lane and
            deliberately UNUSED in v1 — no Blueprint pending-queue sidecar
            (wishlist items are not structural event streams). Do not wire
            it up in a "helpful" refactor.
    """

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_future_add(
        project_id: str,
        summary: str,
        details: str | None = None,
    ) -> dict:
        """Add a future-improvement idea to a project. Use tool_help() for details."""
        # Step 1: Validate project exists
        project = store.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        # Step 2: Validate non-empty summary
        summary = summary.strip()
        if not summary:
            return {"error": "Summary cannot be empty"}

        # Step 3: Truncate inputs (truncate-not-reject)
        summary = summary[:_MAX_SUMMARY_LEN]
        details = details[:_MAX_DETAILS_LEN] if details else None

        # Step 4 + 5: Add via store with closure-stamped provenance.
        # ConcurrentWishlistUpdate is the typed D16 translation (seq race /
        # stale-victim guard — the repo always rolls back the WHOLE
        # transaction before raising); the generic backstop keeps the
        # errors-always-{"error": str} contract. NO automatic retry
        # anywhere (leader ruling D16) — the calling agent may retry.
        try:
            result = store.add_future_entry(
                project_id, summary, details,
                agent_id, current_instance_id,
                max_entries=_MAX_ENTRIES,
            )
        except ConcurrentWishlistUpdate:
            logger.info(
                "project_future_add concurrent wishlist update for project %s (retryable)",
                project_id,
            )
            return {"error": f"Concurrent wishlist update for project '{project_id}' — please retry"}
        except Exception as exc:
            logger.warning("project_future_add failed for project %s: %s", project_id, exc)
            return {"error": str(exc)}

        # Step 6: flat entry dict including "evicted" (verbatim pass-through)
        return result
    project_future_add._full_doc_ = _FULL_DOCS["project_future_add"]

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_future_list(
        project_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """List a project's future-improvement wishlist. Use tool_help() for details."""
        # Validate project exists
        project = store.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        # Clamp and cap limit at 100
        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        # Call store (returns dict with entries, total, limit, offset)
        result = store.list_future_entries(project_id, limit, offset)

        # Merge the cap constant (self-describing envelope — the tool owns it)
        result["cap"] = _MAX_ENTRIES

        return result
    project_future_list._full_doc_ = _FULL_DOCS["project_future_list"]

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_future_remove(
        project_id: str,
        entry_id: str,
    ) -> dict:
        """Remove a future-improvement idea from a project. Use tool_help() for details."""
        # Validate project exists
        project = store.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        # Remove entry
        removed = store.remove_future_entry(entry_id, project_id)

        if not removed:
            return {"error": f"Entry '{entry_id}' not found in project '{project_id}'"}

        return {"success": True, "removed": removed}
    project_future_remove._full_doc_ = _FULL_DOCS["project_future_remove"]

    return [project_future_add, project_future_list, project_future_remove]
