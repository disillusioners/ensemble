"""Agent-facing board tools — the ``board`` category (4 tools).

Thin closure layer over :class:`daemon.services.board_service.BoardService`.
The tools inject the caller identity (instance id, agent id) and
translate the agent's input into the service's canonical shape. They
NEVER write to the DB directly.

House rules honored here (ADR-005):

* Tools NEVER raise — every failure returns an ``"ERROR: {msg}"``
  string (precedent: ``daemon/tools/time.py``,
  ``daemon/tools/scheduling.py``).
* The PB-F1 error envelope applies: ``str(exc)``, ``traceback`` and
  ``repr(exc)`` are NEVER carried into the response payload. Expected
  typed errors from the ``BoardError`` hierarchy MAY carry their safe
  authored message; an unexpected exception is logged SERVER-SIDE and
  answered with the generic ``"ERROR: board operation failed"``.

Access posture — TWO layers, BOTH required
--------------------------------------------
1. The ``board`` category is PRIVILEGED (entry #9 of
   ``PRIVILEGED_TOOL_CATEGORIES``), so the board is opt-in-only and
   never joins the default-grant universe.
2. A privileged category alone is NOT sufficient — a **literal tool
   name** in an allow list bypasses the category gate entirely. So
   every tool body ALSO calls the one shared module-level helper
   :func:`_is_board_authorized`.

Both gates are required; neither substitutes for the other.

The board is NOT a replacement for the ``todo`` tool family: ``todo``
is an in-memory, per-instance DAG that dies with the instance, while
the board is durable, global, and shared by every Ari instance.

Tool NAMES are flat snake_case (``board_create`` / ``board_update`` /
``board_list`` / ``board_get``) — the repo convention established by
``secret_store`` and ``ens_env_read``.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..services.board_service import (
    BoardError,
    BoardItemNotFound,
    BoardValidationError,
)
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Board"
CATEGORY_DOC = """\
Durable, cross-instance work board — one global namespace shared by every
Ari instance.

Tools in this category:
- ``board_create`` — create a work item
- ``board_update`` — change an item's status / body / deadline, link a job, append a note
- ``board_list``   — list items (filter by status, project, tag, deadline)
- ``board_get``    — fetch one item with its full note timeline

How it differs from the ``todo`` family: ``todo`` is an in-memory,
per-instance plan for THIS turn; the board is durable, global, and
survives restarts and instance boundaries. Use the board for work the
user expects to still be there tomorrow; use ``todo`` for scratch
planning inside the current turn.

The board is ONE global namespace — there is no per-chat board and no
per-project board. Project and channel are ATTRIBUTES of an item: a
project is a nullable ``project_id``, a channel (e.g. ``telegram``) is
a plain tag. Use the same item from any chat surface and you will see
the same state.

Status values: ``todo`` | ``in_progress`` | ``blocked`` | ``done``.
Transitions are unrestricted — any status may follow any other,
including reopening a ``done`` item. ``board_list(status="open")`` is
the convention for "not finished yet": it expands to
``todo|in_progress|blocked`` (``done`` is the only terminal status).

Notes are APPEND-ONLY. ``board_update(note=...)`` adds to the
timeline; nothing ever edits or deletes a note. ``board_get`` returns
the timeline oldest-first.

``tags`` are set at create time and are IMMUTABLE afterwards — a new
tag needs a new item. The fields ``title``, ``project_id`` and
``priority`` are also create-only. ``due_at`` can be REPLACED but not
cleared.
"""

#: The agents allowed to operate the board. The privileged category
#: gates VISIBILITY; this set gates AUTHORITY. Both gates are required
#: (the literal-tool-name allow entry bypasses the category gate).
#:
#: ARI-ONLY — the user ruling of 2026-10-20 (Discord, direct) is
#: BINDING and RETRACTS the earlier "ari + leader" audience: the board
#: categories belong to ``ari`` alone. Leader keeps its pre-existing
#: tools (``view_link`` et al.) untouched.
BOARD_AUTHORIZED_AGENTS: frozenset[str] = frozenset({"ari"})


def _is_board_authorized(agent_id: str) -> bool:
    """Is this agent allowed to operate the global board?

    ONE shared helper, called by ALL FOUR tool bodies — there is no
    two-agent-set precedent in ``daemon/tools/`` (the ``blueprint``
    gate is single-agent), so the set lives in exactly one place and
    the four bodies share it.

    Args:
        agent_id: The calling agent's id, captured at factory time.

    Returns:
        True when the agent is in :data:`BOARD_AUTHORIZED_AGENTS`.
    """
    return agent_id in BOARD_AUTHORIZED_AGENTS


def _error(msg: str) -> str:
    """House error envelope — tools never raise."""
    return f"ERROR: {msg}"


def _format_board_error(exc: Exception) -> str:
    """Map a board exception onto the house error envelope.

    The PB-F1 leak class: an unexpected exception is logged SERVER-SIDE
    and answered with a generic message. ``str(exc)`` / ``traceback`` /
    ``repr(exc)`` never reach the payload.
    """
    if isinstance(exc, BoardItemNotFound):
        return _error(str(exc))
    if isinstance(exc, BoardValidationError):
        # An AUTHORED, safe message (e.g. "due_at must be a
        # timezone-aware ISO-8601 string").
        return _error(str(exc))
    if isinstance(exc, BoardError):
        return _error(str(exc))
    logger.exception("board tool failed")
    return _error("board operation failed")


def _compose_list_payload(result: dict, *, requested_limit: int) -> dict:
    """Compose the truthful ``board_list`` payload from the service result.

    Review F1: the service page is ALREADY SQL-limited, so a generic
    list-truncation pass could never fire — the pagination metadata
    was structurally unreachable and a page could look complete while
    being silently partial. The service now reports the truth
    additively (``has_more`` probed via a ``limit + 1`` fetch in the
    same query; ``applied_limit`` post-clamp), and THIS helper is the
    only place that shapes it for the agent:

    * ``_pagination`` is ALWAYS present and truthful — ``has_more``,
      ``returned`` (items in THIS payload) and ``applied_limit``.
    * a clamp is VISIBLE: ``requested_limit != applied_limit`` adds
      ``clamped: true`` + ``requested_limit`` (e.g. ``limit: 500`` →
      ``applied_limit: 200``) — never a silent shrunken page.
    * there is NO ``offset=`` hint — ``board_list`` has no offset
      cursor in v1 (phase1-plan §12-Q3); the truthful way to reach the
      rest is to narrow the filters.
    """
    items = result.get("items", [])
    applied_limit = int(result.get("applied_limit", requested_limit))
    pagination: dict[str, Any] = {
        "has_more": bool(result.get("has_more", False)),
        "returned": len(items),
        "applied_limit": applied_limit,
    }
    if requested_limit != applied_limit:
        # The clamp must never be silent: an agent that asked for 500
        # must be able to SEE that it got 200.
        pagination["clamped"] = True
        pagination["requested_limit"] = requested_limit
    if pagination["has_more"]:
        pagination["note"] = (
            "More items match these filters. board_list has no cursor "
            "in v1 — narrow with status/tag/project_id/due_before to "
            "reach the rest."
        )
    return {
        "items": items,
        "count": result.get("count", len(items)),
        "_pagination": pagination,
    }


def create_board_tools(
    board_service: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the four board tools for one instance/agent.

    Args:
        board_service: The ``daemon.services.board_service.BoardService``
            instance. Injected per-instance from
            ``InstanceManager.board_service``.
        current_instance_id: The caller's instance id. Stamped onto
            every created item and every appended note — the agent can
            NEVER supply an author value.
        agent_id: The caller's agent id. Captured at factory time and
            checked by :func:`_is_board_authorized` in every tool body.
            An agent rename invalidates the closure at the next daemon
            restart (documented, acceptable).
        agent_tag: The caller's version tag. Currently unused by the
            tools themselves; reserved so the factory signature matches
            the other per-instance factories (``create_job_tools`` et
            al.).

    Returns:
        A list of the four ``@tool``-decorated callables.
    """
    caller_instance_id = current_instance_id or ""
    caller_agent_id = agent_id or ""

    class BoardCreateInput(BaseModel):
        """Input schema for board_create."""

        title: Annotated[str, Field(description="Short human title (1-512 chars)")]
        body: Annotated[str, Field(default="", description="Markdown body. Stored as body_md.")]
        project_id: Annotated[str | None, Field(default=None, description="Optional project id (nullable FK)")]
        tags: Annotated[list[str] | None, Field(default=None, description="Tags, e.g. ['telegram','deploy']")]
        due_at: Annotated[str | None, Field(default=None, description="tz-aware ISO-8601 deadline, e.g. '2026-10-21T09:00:00+07:00'")]
        priority: Annotated[int | None, Field(default=None, description="1 (lowest) .. 5 (highest); omit for unset")]

    class BoardUpdateInput(BaseModel):
        """Input schema for board_update."""

        id: Annotated[str, Field(description="Board item id, e.g. 'wi-1a2b3c4d'")]
        status: Annotated[str | None, Field(default=None, description="todo | in_progress | blocked | done")]
        note: Annotated[str | None, Field(default=None, description="Append a note to the timeline. Notes are append-only.")]
        add_job_id: Annotated[str | None, Field(default=None, description="Link a job id to this item")]
        body: Annotated[str | None, Field(default=None, description="Replace the markdown body")]
        due_at: Annotated[str | None, Field(default=None, description="Replace the deadline (tz-aware ISO-8601)")]
        # NO `tags` field — and no `title` / `project_id` / `priority`
        # either: those four are CREATE-ONLY. `tags` is IMMUTABLE
        # post-create (the junction writes happen only at create), so
        # the update shape can neither add nor remove a tag.

    class BoardListInput(BaseModel):
        """Input schema for board_list."""

        status: Annotated[str | None, Field(default=None, description="todo | in_progress | blocked | done | 'open' (= todo|in_progress|blocked) | omit for all")]
        project_id: Annotated[str | None, Field(default=None, description="Filter by project id")]
        tag: Annotated[str | None, Field(default=None, description="Filter by a single tag")]
        due_before: Annotated[str | None, Field(default=None, description="Only items due strictly before this tz-aware ISO-8601 instant. Any UTC offset is accepted; the service normalises it to UTC before the comparison — a raw string never reaches the query.")]
        limit: Annotated[int, Field(default=50, description="Max items to return (1-200)")]

    class BoardGetInput(BaseModel):
        """Input schema for board_get."""

        id: Annotated[str, Field(description="Board item id")]

    @register_tool_category("board")
    @tool(args_schema=BoardCreateInput)
    def board_create(
        title: str,
        body: str = "",
        project_id: str | None = None,
        tags: list[str] | None = None,
        due_at: str | None = None,
        priority: int | None = None,
    ) -> dict | str:
        """Create a durable work item on the global board. Use tool_help("board_create") for details."""
        if not _is_board_authorized(caller_agent_id):
            return _error("board operation not permitted for this agent")
        try:
            return board_service.create_item(
                title=title,
                body_md=body,
                project_id=project_id,
                tags=tags,
                due_at=due_at,
                priority=priority,
                created_by_instance=caller_instance_id,
            )
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            return _format_board_error(exc)

    board_create._full_doc_ = """Create a durable work item on the global board.

The board is ONE global namespace — every Ari instance sees the same
items. Use it for work that must survive the current turn; use the
`todo` family for scratch planning inside this turn.

Args:
    title: Short human title (1-512 chars). Required.
    body: Markdown body. Stored as `body_md` on the item. Defaults to
        empty.
    project_id: Optional project id (a nullable FK). Project is an
        ATTRIBUTE of an item, not a separate board.
    tags: Optional tag list, e.g. `["telegram", "deploy"]`. Tags are
        set here and are IMMUTABLE afterwards — a new tag needs a new
        item. A repeated tag is de-duplicated.
    due_at: Optional deadline as a TIMEZONE-AWARE ISO-8601 string,
        e.g. `"2026-10-21T09:00:00+07:00"`. A naive (tz-less) value is
        rejected. Stored as UTC.
    priority: Optional 1 (lowest) to 5 (highest). Omit for unset.

Returns:
    dict — the created item: `id`, `title`, `body_md`, `status`,
    `tags`, `project_id`, `due_at`, `priority`, `linked_job_ids`,
    `created_by_instance`, `created_at`, `updated_at`. A fresh item
    carries no notes.

Examples:
    board_create(title="Reply to the invoice question",
                body="The customer asked about invoice 4412.",
                tags=["telegram"], due_at="2026-10-21T09:00:00+07:00",
                priority=3)
"""

    @register_tool_category("board")
    @tool(args_schema=BoardUpdateInput)
    def board_update(
        id: str,
        status: str | None = None,
        note: str | None = None,
        add_job_id: str | None = None,
        body: str | None = None,
        due_at: str | None = None,
    ) -> dict | str:
        """Update a board item and/or append a note. Use tool_help("board_update") for details."""
        if not _is_board_authorized(caller_agent_id):
            return _error("board operation not permitted for this agent")
        try:
            return board_service.update_item(
                item_id=id,
                status=status,
                note=note,
                add_job_id=add_job_id,
                body_md=body,
                due_at=due_at,
                author_instance=caller_instance_id,
            )
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            return _format_board_error(exc)

    board_update._full_doc_ = """Update a board item, and/or append a note to its timeline.

Args:
    id: The board item id, e.g. `"wi-1a2b3c4d"`. Required.
    status: New status — `todo` | `in_progress` | `blocked` | `done`.
        Transitions are UNRESTRICTED: any status may follow any other,
        including reopening a `done` item. Omit to leave unchanged.
    note: Text to APPEND to the item's timeline. Notes are APPEND-ONLY
        — nothing ever edits or deletes a note. Omit for no note.
    add_job_id: A job id to link to this item. The linked list only
        ever grows; there is no unlink. Omit for no change.
    body: Replacement markdown body. Stored as `body_md`. Omit to leave
        unchanged.
    due_at: Replacement deadline as a TIMEZONE-AWARE ISO-8601 string.
        REPLACEABLE but not clearable — omit to leave unchanged. A
        naive (tz-less) value is rejected.

There is NO `tags` parameter, and no `title` / `project_id` /
`priority` either: those four are CREATE-ONLY. Re-tagging an existing
item is not expressible — create a new item instead.

Returns:
    dict — the updated item (the same shape `board_create` returns)
    plus `note_appended` (bool). `updated_at` is bumped only when
    something actually changed; a call with no mutable field is a
    no-op.

Examples:
    board_update(id="wi-1a2b3c4d", status="in_progress",
                 note="Asked the customer for the invoice number.")
"""

    @register_tool_category("board")
    @tool(args_schema=BoardListInput)
    def board_list(
        status: str | None = None,
        project_id: str | None = None,
        tag: str | None = None,
        due_before: str | None = None,
        limit: int = 50,
    ) -> dict | str:
        """List board items, most recently updated first. Use tool_help("board_list") for details."""
        if not _is_board_authorized(caller_agent_id):
            return _error("board operation not permitted for this agent")
        try:
            result = board_service.list_items(
                status=status,
                project_id=project_id,
                tag=tag,
                due_before=due_before,
                limit=limit,
            )
            return _compose_list_payload(result, requested_limit=limit)
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            return _format_board_error(exc)

    board_list._full_doc_ = """List board items, most recently updated first.

The board is ONE global namespace — the list is the same for every Ari
instance.

Args:
    status: `todo` | `in_progress` | `blocked` | `done`, or `"open"`
        for the not-finished-yet convention (`todo|in_progress|blocked`
        — `done` is the only terminal status). Omit for all items.
    project_id: Only items attached to this project. Omit for all.
    tag: Only items carrying this tag. Omit for all.
    due_before: Only items due STRICTLY before this instant, given as
        a TIMEZONE-AWARE ISO-8601 string. Any UTC offset is accepted
        and is normalised to UTC before the comparison. A naive
        (tz-less) value is rejected.
    limit: Max items to return (1-200, default 50). A request beyond
        the clamp is VISIBLE in `_pagination` (`clamped: true` +
        `requested_limit`) — the page is never silently shrunken.

Returns:
    dict — `{"items": [...], "count": N, "_pagination": {...}}`. Each
    item carries the canonical item shape and NO notes timeline (use
    `board_get` for the notes). Items are ordered by `updated_at` DESC.

    `_pagination` is ALWAYS present and truthful:
    - `has_more` — more items match the current filters than this
      page shows.
    - `returned` — how many items THIS payload carries.
    - `applied_limit` — the page size actually applied after the
      1-200 clamp.
    - when the requested `limit` was clamped (e.g. `limit: 500`),
      also `clamped: true` + `requested_limit`.

    There is NO offset cursor in v1. When `has_more` is true, narrow
    with status/tag/project_id/due_before to reach the rest.

Examples:
    board_list(status="open", limit=20)
    board_list(tag="deploy", due_before="2026-10-21T09:00:00+07:00")
"""

    @register_tool_category("board")
    @tool(args_schema=BoardGetInput)
    def board_get(id: str) -> dict | str:
        """Fetch one board item with its full note timeline. Use tool_help("board_get") for details."""
        if not _is_board_authorized(caller_agent_id):
            return _error("board operation not permitted for this agent")
        try:
            return board_service.get_item(item_id=id)
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            return _format_board_error(exc)

    board_get._full_doc_ = """Fetch one board item together with its full note timeline.

Args:
    id: The board item id, e.g. `"wi-1a2b3c4d"`. Required.

Returns:
    dict — the canonical item shape (same as `board_create` returns)
    PLUS `notes`: the append-only timeline, OLDEST-FIRST. Each note is
    `{"ts", "author_instance", "text"}`.

Examples:
    board_get(id="wi-1a2b3c4d")
"""

    return [board_create, board_update, board_list, board_get]


__all__ = [
    "BOARD_AUTHORIZED_AGENTS",
    "CATEGORY_DOC",
    "CATEGORY_NAME",
    "create_board_tools",
]
