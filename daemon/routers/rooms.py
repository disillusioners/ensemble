"""The ``/api/rooms/*`` HTTP room-read surface — the P6 human
observability modal's backend (architecture §13.1, R3).

The Emperor's P6 UI direction (KV ``gc_ui_refinement_p6``, verbatim):
*"CHATROOM ICON next to the WATCHOVER button on the frontend instances
surface. Click → BIG POPUP … showing (a) chatrooms created by that
instance('s tree) and (b) messages in each room … Rides the SAME
room-read API as the agents' read tool (members ∪ owner-ancestry
predicate, fail-closed) with the FE's own auth posture."*

TWO ROUTES (the wire contract is BINDING — the frontend codes against
it in parallel):

* ``GET /api/instances/{instance_id}/rooms`` →
  ``{"rooms": [{"room_id", "name", "created_at", "closed",
  "member_count", "last_message_at", "last_message_seq"}]}``
  — the rooms of the instance's TREE (the room's own scope anchor is
  ``chatrooms.tree_root_id``; §13.1: "the chatrooms created by that
  instance's tree"). A CLOSED room is listed (``closed: true`` — the
  §2.1 capability row keeps closed rooms readable); a PURGED room has
  no rows at all and is therefore NOT listed (R2: purged rooms are
  nonexistent).

* ``GET /api/rooms/{room_id}/messages?after_seq=&limit=`` →
  ``{"messages": [{"seq", "instance_id", "member_name", "content",
  "created_at", "kind"}], "next_after_seq": int|null}``
  — the room log, ascending by ``seq``. ``next_after_seq`` is CLAMPED
  to the last RETURNED seq (the client resumes from there); it is
  ``null`` when the window is exhausted. An unclamped cursor is NEVER
  echoed.

THE POSTURE (no new auth model)
-------------------------------
* The role gate is the EXISTING ``instances:read`` scope — the same
  scope every other instance read route uses (the operator lane). No
  new scope, no new lane, no new auth surface.
* The ROOM-SCOPE predicate is the SAME fail-closed repository
  predicate the agent's ``room_read`` tool uses (active member ∪
  owner-ancestry, state-aware, purged ⇒ not-found). The HTTP surface
  rides it rather than re-implementing an access rule: the route
  resolves the room (absent/purged ⇒ 404) and reads through
  ``RoomChatService.read_messages`` — the very call the tool makes.
* ``?instance_id=`` (OPTIONAL) narrows the read to ONE instance's
  scope: the predicate is then evaluated for THAT instance (an active
  member or an owner-ancestor). A caller that is neither gets the
  typed 404. Without the parameter the operator-lane scope applies
  (the room's own scope anchor — the owner).

THE ERROR VOCABULARY (closed)
------------------------------
``422`` (FastAPI request validation — the int-typed query params
fail there before the handler) · ``401`` (the auth middleware) ·
``403 SCOPE_INSUFFICIENT`` · ``404 instance_not_found`` / ``404
room_not_found`` · ``503 room_service_unavailable``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from daemon.auth.decorators import scope_required
from daemon.repositories.room.models import (
    ROOM_READ_DEFAULT_LIMIT,
    ROOM_READ_MAX_LIMIT,
)
from daemon.repositories.room.repository import RoomNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rooms"])

__all__ = ["router"]

#: The role gate — the SAME scope every other instance read route
#: uses (the operator lane). The room surface introduces NO new
#: scope and NO new auth model.
_SCOPE_READ = "instances:read"

#: The closed error vocabulary of THIS route.
_ERR_INSTANCE_NOT_FOUND = "instance_not_found"
_ERR_ROOM_NOT_FOUND = "room_not_found"
_ERR_ROOM_SERVICE_UNAVAILABLE = "room_service_unavailable"


def _err(status_code: int, error: str, message: str) -> HTTPException:
    """The typed ``{error, message}`` body (the house router shape)."""
    return HTTPException(
        status_code=status_code, detail={"error": error, "message": message}
    )


def _get_manager(request: Request) -> Any:
    """The manager handle (``request.app.state.manager``)."""
    return request.app.state.manager


def _room_service(manager: Any) -> Any:
    """The room chat service handle, or None."""
    if manager is None:
        return None
    return getattr(manager, "_room_chat_service", None)


@router.get(
    "/instances/{instance_id}/rooms",
    dependencies=[scope_required(_SCOPE_READ)],
)
async def list_instance_rooms(instance_id: str, request: Request) -> dict:
    """The chatrooms of ``instance_id``'s tree (the P6 modal, level (a))."""
    manager = _get_manager(request)
    room_service = _room_service(manager)
    if room_service is None:
        raise _err(
            503,
            _ERR_ROOM_SERVICE_UNAVAILABLE,
            "the chat-room service is not available in this daemon",
        )

    # The instance must exist (the house 404 shape of the instance
    # read routes) — an unknown instance has no tree to scope by.
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise _err(
            404, _ERR_INSTANCE_NOT_FOUND, f"Instance not found: {instance_id}"
        )

    instance_repo = getattr(manager, "_instance_repository", None)
    if instance_repo is None:
        raise _err(
            503,
            _ERR_ROOM_SERVICE_UNAVAILABLE,
            "the instance repository is not available in this daemon",
        )
    try:
        tree_root = await asyncio.to_thread(
            instance_repo.get_tree_root_id, instance_id
        )
    except Exception as exc:  # noqa: BLE001 — a broken scope ⇒ not-found
        logger.warning(
            "room list: the tree scope of instance=%s could not be "
            "resolved (%r)",
            instance_id,
            exc,
        )
        raise _err(
            404,
            _ERR_INSTANCE_NOT_FOUND,
            f"Instance not found: {instance_id}",
        ) from exc
    if not tree_root:
        raise _err(
            404,
            _ERR_INSTANCE_NOT_FOUND,
            f"Instance not found: {instance_id}",
        )

    rooms = await room_service.list_rooms_for_tree(tree_root)
    return {
        "rooms": [
            {
                "room_id": room.get("room_id"),
                "name": room.get("name"),
                "created_at": room.get("created_at"),
                "closed": room.get("state") == "closed",
                "member_count": int(room.get("member_count") or 0),
                "last_message_at": room.get("last_post_at"),
                "last_message_seq": room.get("last_message_seq"),
            }
            for room in rooms
        ]
    }


@router.get(
    "/rooms/{room_id}/messages",
    dependencies=[scope_required(_SCOPE_READ)],
)
async def read_room_messages(
    room_id: str,
    request: Request,
    after_seq: int = Query(0, description="Return rows with seq > after_seq."),
    limit: int = Query(
        ROOM_READ_DEFAULT_LIMIT,
        description=f"Window size (max {ROOM_READ_MAX_LIMIT}).",
    ),
    instance_id: str = Query(
        "",
        description=(
            "OPTIONAL scope instance: the room is read through THAT "
            "instance's predicate (active member ∪ owner-ancestry). "
            "Empty ⇒ the operator-lane scope (the room's own scope)."
        ),
    ),
) -> dict:
    """The room log (the P6 modal, level (b)) — ascending by ``seq``."""
    manager = _get_manager(request)
    room_service = _room_service(manager)
    if room_service is None:
        raise _err(
            503,
            _ERR_ROOM_SERVICE_UNAVAILABLE,
            "the chat-room service is not available in this daemon",
        )

    after = after_seq
    if after < 0:
        after = 0
    window = limit
    if window <= 0:
        window = ROOM_READ_DEFAULT_LIMIT
    window = min(window, ROOM_READ_MAX_LIMIT)

    # A PURGED room is NONEXISTENT (R2) — the same typed not-found the
    # agent's read tool answers with.
    room = await room_service.get_room(room_id)
    if room is None:
        raise _err(
            404, _ERR_ROOM_NOT_FOUND, f"room not found: {room_id}"
        )

    # The SCOPE for the fail-closed repository predicate: the caller's
    # instance when given, else the room's own scope anchor (the owner
    # — always inside the owner-ancestry chain, so the predicate's
    # existence branch holds and a purged/absent room still 404s).
    scope = instance_id.strip() if instance_id else ""
    if not scope:
        scope = str(room.get("owner_instance_id") or "")

    try:
        # ONE extra row probes the exhaustion — the cursor is clamped
        # to the last RETURNED seq, never echoed unclamped.
        rows = await room_service.read_messages(
            room_id,
            caller_instance_id=scope,
            after_seq=after,
            limit=window + 1,
        )
    except RoomNotFoundError:
        raise _err(
            404,
            _ERR_ROOM_NOT_FOUND,
            f"room not found for the requested scope: {room_id}",
        )

    has_more = len(rows) > window
    window_rows = rows[:window]
    next_after_seq = (
        int(window_rows[-1]["seq"]) if (window_rows and has_more) else None
    )
    return {
        "messages": [
            {
                "seq": int(row.get("seq") or 0),
                "instance_id": row.get("author_instance_id"),
                "member_name": row.get("author_name"),
                "content": row.get("text"),
                "created_at": row.get("created_at"),
                "kind": row.get("kind"),
            }
            for row in window_rows
        ],
        "next_after_seq": next_after_seq,
    }
