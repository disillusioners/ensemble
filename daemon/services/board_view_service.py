"""The board READ adapter — the ONE seam onto Phase 1's frozen board service.

ari-tools Phase 3, deliverable B. This module is the **only** place in
Phase 3 that knows Phase 1's surface, and it knows EXACTLY the frozen
seam:

* ``board_service.list_items(status=…, project_id=…, tag=…, due_before=…,
  limit=…, with_notes=…)`` → ``{"items": [...], "count": N, …}``
* ``board_service.get_item(item_id=…)`` → one item (+ ``notes[]``)

The enumeration is VERBATIM from Phase 1 §5.2 (the interface freeze) —
note that ``due_before`` sits BETWEEN ``tag`` and ``limit``; it is
named EXPLICITLY at every call site so a Phase-1 rename or keyword-drop
is caught by this seam rather than by a runtime surprise.

What this module NEVER does
----------------------------
* it never imports a Phase-1 file (no model import, no
  ``_load_notes_bulk``, no private loader);
* it never mutates the board (the board is READ-ONLY here);
* it never raises out of the render path — a broken / unavailable board
  backend degrades to an explicit "board backend unavailable" notice
  (HTTP 200, zero-JS), NEVER a 500.

Shape tolerance
---------------
Phase 1 returns plain ``dict`` rows, but the adapter is tolerant of
BOTH shapes (``obj["x"]`` and ``getattr(obj, "x", None)``) — the
in-tree precedent is ``_row_locked`` / ``_row_generation`` in
``daemon/routers/live_views.py``. A row that carries NO ``notes`` key
means **ZERO notes** — Phase 1's projection OMITS the key for a row
with no notes (``notes=notes_by_item.get(row.id)`` → ``None`` → the
key is never added) — so the adapter projects such a row DIRECTLY. It
never re-hydrates per row: the per-row ``get_item`` fallback was an
N+1 on the DEFAULT board state (a 50-row zero-note board = 153
queries, each taking the process ``_write_lock`` — every public
render contended with the writer).

The ROW ORDER is the SEAM's order (``ORDER BY updated_at DESC``) BY
CONSTRUCTION: the projection is re-assembled by WALKING the fetched
rows, never by concatenating passes. A concatenation reordered the
board (a seam order of A, B, C rendered B, A, C) and — because the
reorder preceded the 50-row cap — DROPPED the newest rows of a
55-row board.

Bounded by construction
-----------------------
* rows rendered: :data:`DEFAULT_RENDER_LIMIT` (50);
* rows fetched (and therefore notes hydrated): :data:`MAX_FETCH_LIMIT`
  (200 — Phase 1's own ``MAX_LIMIT`` clamp). Phase 1's ``count`` is
  the PAGE size, not the number of matching rows, so the adapter
  fetches the wider page: when the match count fits INSIDE the page
  it IS the exact count, and when the bound is hit the count is only
  a LOWER BOUND — the page's disclosure then says so ("N of M+ items
  — fetch bound");
* notes per item: 20; note text: 4 KiB; ``body_md``: 64 KiB — the
  per-field caps, enforced DURING assembly with a VISIBLE notice
  (never a silent cut);
* the rendered body: 2 MiB (enforced in
  :mod:`daemon.services.board_view_pages` during assembly).

The service is SYNCHRONOUS (a sync SQLAlchemy engine), so the router
wraps the WHOLE fetch + assembly in ONE ``asyncio.to_thread`` — one
crossing, not one per service call (each crossing is a thread hop AND
re-splits the snapshot the page renders from).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


#: The reserved root name the board is served under.
BOARD_VIEW_ROOT = "board"

#: The ONE relative path the board is served at — the passcode key is
#: exactly ``(board, "index.html")``. Never per-filter rows: a
#: passcode authorises "may this person see the board", not "may they
#: see a filtered subset" (D11).
BOARD_VIEW_REL = "index.html"

#: The pinned Phase-1 item-id shape (``wi-`` + 8 lowercase hex chars).
#: An item whose id does not match is DROPPED from the page — the id
#: is echoed into the markup, so a non-conforming id is a defect, not
#: something to render.
BOARD_ITEM_ID_RE = re.compile(r"^wi-[0-9a-f]{8}$")

#: How many rows the page RENDERS.
DEFAULT_RENDER_LIMIT = 50

#: How many rows the adapter FETCHES (Phase 1's own clamp is 200).
#: The headroom over :data:`DEFAULT_RENDER_LIMIT` is what keeps the
#: "showing N of M" disclosure truthful: below the bound the fetched
#: count IS the exact match count, above it the count is a lower
#: bound and the page says so.
MAX_FETCH_LIMIT = 200

#: How many values the filter-option set may carry.
DEFAULT_OPTION_LIMIT = 500

#: Per-field caps (A-34) — enforced during assembly, with a VISIBLE
#: notice on the page. Never a silent cut.
MAX_NOTES_PER_ITEM = 20
MAX_NOTE_TEXT_BYTES = 4 * 1024
MAX_BODY_BYTES = 64 * 1024


@dataclass(frozen=True)
class BoardNoteView:
    """One note on an item's append-only timeline."""

    ts: str
    author_instance: str
    text: str
    truncated: bool = False


@dataclass(frozen=True)
class BoardItemView:
    """One board item, projected for rendering.

    This is the ONLY shape ``daemon.services.board_view_pages``
    consumes — the pages module never imports the board service and
    never sees a Phase-1 row.
    """

    id: str
    title: str
    body_md: str
    status: str
    tags: list[str] = field(default_factory=list)
    project_id: Optional[str] = None
    due_at: Optional[str] = None
    linked_job_ids: list[str] = field(default_factory=list)
    created_by_instance: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    priority: Optional[int] = None
    notes: list[BoardNoteView] = field(default_factory=list)
    #: True when a field was cut by the per-field cap (the page shows
    #: a visible notice).
    body_truncated: bool = False
    notes_truncated: bool = False
    # NOTE: there is deliberately NO "notes unavailable" field — after
    #: the W2 root fix a MISSING ``notes`` key means ZERO notes (a
    #: zero-note row is NOT an error), so the "notes are unavailable"
    #: state CANNOT occur.  A field that describes an impossible state
    #: is a lie the next reader must re-derive.


# ─────────────────────────────────────────────────────────────────
# Shape-tolerant field access
# ─────────────────────────────────────────────────────────────────


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Return ``obj[key]`` or ``getattr(obj, key)`` — either shape.

    Phase 1 returns plain dicts today, but the in-tree precedent
    (``_row_locked`` / ``_row_generation`` in the live-views router)
    reads both shapes, and the adapter must not break if the seam ever
    starts returning rows.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_text(value: Any) -> str:
    """Coerce a value to text (None → ``""``)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _as_int(value: Any) -> Optional[int]:
    """Coerce a value to int, or None when it is not numeric."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str_list(value: Any) -> list[str]:
    """Coerce a value to a list of strings (a bare string → one item)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [_as_text(item) for item in value]
    return [_as_text(value)]


def _truncate_bytes(text: str, cap: int) -> tuple[str, bool]:
    """Cut ``text`` to at most ``cap`` UTF-8 bytes.

    Returns ``(text, truncated)``. The cut never splits a multi-byte
    character (it cuts on a character boundary), so the result is
    always valid text.
    """
    if not text:
        return "", False
    encoded = text.encode("utf-8")
    if len(encoded) <= cap:
        return text, False
    cut = encoded[:cap]
    # Trim any trailing partial UTF-8 sequence.
    while cut:
        try:
            return cut.decode("utf-8"), True
        except UnicodeDecodeError:
            cut = cut[:-1]
    return "", True


# ─────────────────────────────────────────────────────────────────
# The frozen seam
# ─────────────────────────────────────────────────────────────────


def _project_item(raw: Any, *, notes: Optional[list[Any]]) -> Optional[BoardItemView]:
    """Project one Phase-1 row into a :class:`BoardItemView`.

    Returns ``None`` when the row's id does not match the pinned
    ``wi-<8 hex>`` shape — a non-conforming id is DROPPED (the id is
    echoed into the markup).
    """
    item_id = _as_text(_get(raw, "id"))
    if not BOARD_ITEM_ID_RE.match(item_id):
        logger.warning(
            "[board_view] dropping item with non-conforming id %r",
            item_id[:32],
        )
        return None

    body_md, body_truncated = _truncate_bytes(
        _as_text(_get(raw, "body_md")), MAX_BODY_BYTES
    )

    note_views: list[BoardNoteView] = []
    notes_truncated = False
    for raw_note in notes or []:
        if len(note_views) >= MAX_NOTES_PER_ITEM:
            notes_truncated = True
            break
        note_text, note_truncated = _truncate_bytes(
            _as_text(_get(raw_note, "text")), MAX_NOTE_TEXT_BYTES
        )
        if note_truncated:
            notes_truncated = True
        note_views.append(
            BoardNoteView(
                ts=_as_text(_get(raw_note, "ts")),
                author_instance=_as_text(_get(raw_note, "author_instance")),
                text=note_text,
                truncated=note_truncated,
            )
        )

    return BoardItemView(
        id=item_id,
        title=_as_text(_get(raw, "title")),
        body_md=body_md,
        status=_as_text(_get(raw, "status")),
        tags=_as_str_list(_get(raw, "tags")),
        project_id=(
            _as_text(_get(raw, "project_id"))
            if _get(raw, "project_id") is not None
            else None
        ),
        due_at=(
            _as_text(_get(raw, "due_at"))
            if _get(raw, "due_at") is not None
            else None
        ),
        linked_job_ids=_as_str_list(_get(raw, "linked_job_ids")),
        created_by_instance=(
            _as_text(_get(raw, "created_by_instance"))
            if _get(raw, "created_by_instance") is not None
            else None
        ),
        created_at=_as_text(_get(raw, "created_at")),
        updated_at=_as_text(_get(raw, "updated_at")),
        priority=_as_int(_get(raw, "priority")),
        notes=note_views,
        body_truncated=body_truncated,
        notes_truncated=notes_truncated,
    )


def fetch_board_items(
    board_service: Any,
    *,
    status: Optional[str] = None,
    project_id: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = DEFAULT_RENDER_LIMIT,
    fetch_limit: int = MAX_FETCH_LIMIT,
) -> tuple[list[BoardItemView], int, bool]:
    """Fetch the board page through the FROZEN Phase-1 seam.

    Args:
        board_service: The ``daemon.services.board_service.BoardService``
            instance (reached as ``manager.board_service``).
        status: ``todo | in_progress | blocked | done | open`` — or
            ``None`` for no filter.
        project_id: Optional project filter.
        tag: Optional single-tag filter.
        limit: How many rows the caller RENDERS.
        fetch_limit: How many rows the adapter FETCHES. The headroom
            over ``limit`` keeps the "showing N of M" disclosure
            truthful (Phase 1's ``count`` is the page size, not the
            number of matching rows): below the bound the fetched
            count IS the exact match count; above it the count is a
            lower bound and ``has_more`` says so.

    Returns:
        ``(items, total, truncated)`` — the projected rows (at most
        ``limit`` of them), the number of rows that MATCH the filters
        (as far as the bounded fetch can prove it), and whether more
        rows match beyond the fetch bound.
    """
    if board_service is None:
        return [], 0, False

    # The FROZEN call — every keyword named explicitly, ``due_before``
    # included (the board exposes no due-date filter in v1, so it is
    # always ``None``).
    result = board_service.list_items(
        status=status,
        project_id=project_id,
        tag=tag,
        due_before=None,
        limit=fetch_limit,
        with_notes=True,
    )
    raw_items = _get(result, "items", []) or []
    has_more = bool(_get(result, "has_more", False))

    # The FETCH PAGE SIZE — Phase 1's ``count`` is the page size, NOT
    # the number of matching rows. It IS the exact match count only
    # when the bound was NOT hit (``has_more`` False); when the bound
    # WAS hit the true match count is LARGER, and the page's
    # disclosure says so ("N of M+ items — fetch bound").
    total = len(raw_items)
    # The PROJECTION pass — every row is projected DIRECTLY.
    #
    # W2 (council): a MISSING ``notes`` key means ZERO notes, NOT
    # "needs re-hydration". Phase 1's ``_row_to_item`` OMITS the key
    # for a row with no notes (``notes=notes_by_item.get(row.id)`` →
    # ``None`` → the key is never added) ⇒ on the DEFAULT board state
    # (zero-note rows) the old per-row ``get_item`` fallback fired for
    # EVERY row: a 50-row board = 153 queries, each taking the process
    # ``_write_lock``.
    views_by_id: dict[str, BoardItemView] = {}
    for raw in raw_items:
        notes = _get(raw, "notes")
        view = _project_item(raw, notes=notes if notes is not None else [])
        if view is None:
            # A non-conforming id is DROPPED (it is echoed into the
            # markup) — never rendered.
            continue
        views_by_id[view.id] = view

    # The ROW ORDER is the SEAM's order (``ORDER BY updated_at DESC``)
    # BY CONSTRUCTION (W3, council): the projection is re-assembled by
    # WALKING ``raw_items`` — the fetched order — never by
    # concatenating passes. The old shape appended the per-row
    # hydrated rows AFTER the batch rows, so a seam order of A, B, C
    # rendered B, A, C and — because the reorder preceded the 50-row
    # cap — a 55-row board DROPPED its 5 NEWEST rows.
    projected: list[BoardItemView] = []
    for raw in raw_items:
        item_id = _as_text(_get(raw, "id"))
        view = views_by_id.get(item_id)
        if view is not None:
            projected.append(view)

    # The rows actually rendered are the first ``limit`` of the
    # projection — the seam's NEWEST rows; the disclosure compares
    # the rendered count against the number that matched — the EXACT
    # count when the bound was NOT hit, a lower bound when it was.
    rendered = projected[: max(0, int(limit))]
    return rendered, total, has_more


def fetch_filter_options(
    board_service: Any,
    *,
    limit: int = DEFAULT_OPTION_LIMIT,
) -> dict:
    """Derive the filter-option set (projects + tags) from the board.

    ONE additional bounded call over the FROZEN seam — the same
    unfiltered ``list_items`` the plan names (``due_before`` stays at
    ``None``, ``with_notes`` stays at its default ``False`` because
    the option set needs no notes).

    Returns:
        ``{"projects": [...], "tags": [...], "applied_limit": int,
        "truncated": bool}`` — the two required keys plus the
        truncation flags that make a capped option set VISIBLE.
    """
    empty = {
        "projects": [],
        "tags": [],
        "applied_limit": 0,
        "truncated": False,
    }
    if board_service is None:
        return empty
    result = board_service.list_items(
        status=None,
        project_id=None,
        tag=None,
        due_before=None,
        limit=limit,
        with_notes=False,
    )
    raw_items = _get(result, "items", []) or []
    projects: list[str] = []
    tags: list[str] = []
    seen_projects: set[str] = set()
    seen_tags: set[str] = set()
    for raw in raw_items:
        project = _get(raw, "project_id")
        if project:
            text = _as_text(project)
            if text not in seen_projects:
                seen_projects.add(text)
                projects.append(text)
        for tag_value in _as_str_list(_get(raw, "tags")):
            if tag_value and tag_value not in seen_tags:
                seen_tags.add(tag_value)
                tags.append(tag_value)
    projects.sort()
    tags.sort()
    return {
        "projects": projects,
        "tags": tags,
        "applied_limit": _as_int(_get(result, "applied_limit", 0)) or 0,
        "truncated": bool(_get(result, "has_more", False)),
    }


__all__ = [
    "BOARD_ITEM_ID_RE",
    "BOARD_VIEW_REL",
    "BOARD_VIEW_ROOT",
    "BoardItemView",
    "BoardNoteView",
    "DEFAULT_OPTION_LIMIT",
    "DEFAULT_RENDER_LIMIT",
    "MAX_BODY_BYTES",
    "MAX_FETCH_LIMIT",
    "MAX_NOTE_TEXT_BYTES",
    "MAX_NOTES_PER_ITEM",
    "fetch_board_items",
    "fetch_filter_options",
]
