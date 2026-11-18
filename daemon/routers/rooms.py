"""The ``/api/rooms/*`` HTTP room-read surface — the P6 human
observability modal's backend (architecture §13.1, R3).

The Emperor's P6 UI direction (KV ``gc_ui_refinement_p6``, verbatim):
*"CHATROOM ICON next to the WATCHOVER button on the frontend instances
surface. Click → BIG POPUP … showing (a) chatrooms created by that
instance('s tree) and (b) messages in each room … Rides the SAME
room-read API as the agents' read tool (members ∪ owner-ancestry
predicate, fail-closed) with the FE's own auth posture."*

THREE ROUTES (the wire contract is BINDING — the frontend codes against
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

* ``GET /api/rooms?instance_id=&limit=`` →
  ``{"rooms": [{"room_id", "name", "tree_root_id",
  "owner_instance_id", "state", "member_count",
  "member_instance_ids", "last_message_at", "last_message_seq",
  "created_at"}], "truncated": bool}``
  — the NEST-WIDE browser list (gc-fe-surfaces phase 1). ``limit`` is
  advisory: the server clamps to ``[1, ROOM_LIST_MAX_LIMIT=500]``
  (default 200) and ``truncated`` says whether rows were cut. With
  ``instance_id`` the list is the caller-scoped readable set (the SAME
  fail-closed predicate as the messages route, filter-then-clamp);
  without it the read is the operator-lane nest-wide breadth, gated by
  the module knob ``_ALLOW_ANCHORLESS_GLOBAL_LIST`` (knob-OFF ⇒ ``404
  instance_not_found``). A ``state`` field names the lifecycle state
  (``active`` / ``closed``); a purged room has no rows at all.

KNOB DEFAULT — OPERATOR-TIGHTENABLE WITHOUT A RELEASE (A3)
-----------------------------------------------------------
The knob's DEFAULT is resolved ONCE, at import, from the environment
variable ``ENSEMBLE_ALLOW_ANCHORLESS_GLOBAL_LIST``:

* **unset** ⇒ the historical wide default (``True``);
* **set to a strictly parseable boolean** (case-insensitive ``1`` /
  ``true`` / ``yes`` / ``on`` ⇒ True; ``0`` / ``false`` / ``no`` /
  ``off`` ⇒ False) ⇒ that value;
* **set to anything else** ⇒ the historical default (``True``) plus a
  ``WARNING`` in the daemon log — an unparseable tightening request
  never tightens silently and never crashes the import.

The resolution happens at import time only (a restart, not a release,
is enough), the knob stays a plain module attribute (tests flip it with
``monkeypatch.setattr``), and EXACTLY ONE branch in the handler consults
it — the env var introduces no second anchor-less path.

``state`` vs ``closed`` — a DECIDED divergence, not an accident: the
messages-era room list (``GET /api/instances/{id}/rooms``) emits the
boolean ``closed`` and its contract is FROZEN at v0.31.0 for that
shipped consumer; the new nest-wide route emits the fuller ``state``
string because its row is the richer shape (roster + owner + tree
anchors) and the FE global lane needs the lifecycle truth rather than a
derived boolean. The two routes share no row schema by design; adding
``state`` to the frozen route is a v0.32.0-class contract change, not a
drive-by.

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

INFRA faults stay inside this closed vocabulary (A-P6-25, the M3
adjudication): a faulting room-service call is WRAPPED into the typed
envelope instead of escaping to the global exception handler (whose
``{code: INTERNAL_ERROR, message: str(exc)}`` body is the wrong shape
for this surface AND leaks raw fault text to the client — the
security note). The mapping: a faulted room-LIST or room-LOG read
answers ``503 room_service_unavailable`` with a GENERIC message (the
service is present but faulted — retryable); a faulted room
RESOLUTION answers ``404 room_not_found`` — the SAME fail-closed
not-found the SSE lane's room-resolution pre-branch answers on a
broken read (A-P6-11 family: an unresolvable room is never
distinguished from an absent one on the wire). Fault details go to
the server log, never into a response body.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from daemon.auth.decorators import scope_required
from daemon.repositories.room.models import (
    ROOM_READ_DEFAULT_LIMIT,
    ROOM_READ_MAX_LIMIT,
)
from daemon.repositories.room.repository import (
    ROOM_LIST_MAX_LIMIT,
    RoomNotFoundError,
)

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
_ERR_BAD_REQUEST = "bad_request"

#: The room-list route's DEFAULT page size — advisory, the server clamps
#: every request into ``[1, ROOM_LIST_MAX_LIMIT]``.
_ROOM_LIST_DEFAULT_LIMIT = 200


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

    # The tree's rooms. INFRA-FAULT WRAP (A-P6-25, M3): a faulting
    # list is answered in the typed envelope — never a raw escape to
    # the global 500 handler (wrong body shape + fault text to the
    # client). The room service is PRESENT but faulted ⇒ 503
    # (retryable), message generic, detail server-side only.
    try:
        rooms = await room_service.list_rooms_for_tree(tree_root)
    except Exception as exc:  # noqa: BLE001 — infra faults stay typed (M3)
        logger.warning(
            "room list: listing rooms for tree=%s failed (%r)",
            tree_root,
            exc,
        )
        raise _err(
            503,
            _ERR_ROOM_SERVICE_UNAVAILABLE,
            "the room list could not be read right now",
        ) from exc
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
    "/rooms",
    dependencies=[scope_required(_SCOPE_READ)],
)
async def list_rooms(
    request: Request,
    limit: int = Query(_ROOM_LIST_DEFAULT_LIMIT),
    instance_id: str | None = Query(
        None,
        description=(
            "OPTIONAL scope instance (BLOCKER-1(d)). Declared as "
            "Query(None) so the parameter is part of the OpenAPI "
            "surface; PRESENCE is still read from the raw query "
            "string below — Query(None) cannot distinguish absent "
            "from present-but-empty, and the N9 taxonomy depends on "
            "the difference."
        ),
    ),
) -> dict:
    """Nest-wide room list (the global browser, gc-fe-surfaces phase 1).

    Optional ``instance_id`` query param anchors the read to that
    instance's scope (fail-closed predicate). Absent ⇒ anchor-less
    (knob-gated by ``_ALLOW_ANCHORLESS_GLOBAL_LIST``).
    """
    manager = _get_manager(request)
    room_service = _room_service(manager)
    if room_service is None:
        raise _err(
            503,
            _ERR_ROOM_SERVICE_UNAVAILABLE,
            "the chat-room service is not available in this daemon",
        )

    # Presence detection via raw query string (Query(None) cannot
    # distinguish absent from present-but-empty).
    raw_params = request.query_params
    present = "instance_id" in raw_params
    raw_value = raw_params.get("instance_id") if present else None

    # Server-side clamp. ``limit`` is ALREADY int-coerced by FastAPI (a
    # non-integer query value 422s before the handler runs), so a
    # defensive re-coercion here would be dead code.
    clamped_limit = max(1, min(limit, ROOM_LIST_MAX_LIMIT))

    # Present-but-empty / whitespace ⇒ 422 bad_request (malformed), in
    # BOTH knob states (presence check precedes the knob gate).
    if present:
        stripped = (raw_value or "").strip()
        if stripped == "":
            raise _err(
                422,
                _ERR_BAD_REQUEST,
                "instance_id must not be empty",
            )

    # Anchor-less path.
    if not present:
        if not _ALLOW_ANCHORLESS_GLOBAL_LIST:
            raise _err(
                404,
                _ERR_INSTANCE_NOT_FOUND,
                "Instance not found",
            )
        try:
            rows, truncated = await room_service.list_all_rooms(clamped_limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("room list (global): listing failed (%r)", exc)
            raise _err(
                503,
                _ERR_ROOM_SERVICE_UNAVAILABLE,
                "the room list could not be read right now",
            ) from exc
        return _shape_rows(rows, truncated)

    # Anchored path.
    anchor = (raw_value or "").strip()
    # Pure DB read for existence (never manager.get_instance — C-4).
    # A-3: ONE fixed, input-free message in BOTH refusal branches of this
    # route — the knob-OFF refusal below and the bad-anchor 404 share the
    # exact same string, so the two are wire-indistinguishable and neither
    # discloses the knob nor echoes client input.
    try:
        await asyncio.to_thread(manager.get_instance_info, anchor)
    except KeyError:
        raise _err(
            404,
            _ERR_INSTANCE_NOT_FOUND,
            "Instance not found",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("room list (anchored): instance lookup failed (%r)", exc)
        raise _err(
            404,
            _ERR_INSTANCE_NOT_FOUND,
            "Instance not found",
        ) from exc
    # descendant closure off-loop
    try:
        descendant_ids = await asyncio.to_thread(
            manager.get_tree_ids_permanent, anchor
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "room list (anchored): the descendant closure of instance=%s "
            "could not be computed (%r) — falling back to anchor-only",
            anchor,
            exc,
        )
        descendant_ids = [anchor]
    try:
        rows, truncated = await room_service.list_readable_rooms(
            anchor, clamped_limit, descendant_ids=descendant_ids
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("room list (anchored): listing failed (%r)", exc)
        raise _err(
            503,
            _ERR_ROOM_SERVICE_UNAVAILABLE,
            "the room list could not be read right now",
        ) from exc
    return _shape_rows(rows, truncated)


def _shape_rows(rows: list[dict[str, Any]], truncated: bool) -> dict[str, Any]:
    return {
        "rooms": [
            {
                "room_id": r.get("room_id"),
                "name": r.get("name"),
                "tree_root_id": r.get("tree_root_id"),
                "owner_instance_id": r.get("owner_instance_id"),
                "state": r.get("state"),
                "member_count": int(r.get("member_count") or 0),
                "member_instance_ids": list(r.get("member_instance_ids") or []),
                "last_message_at": r.get("last_post_at"),
                "last_message_seq": r.get("last_message_seq"),
                "created_at": r.get("created_at"),
            }
            for r in rows
        ],
        "truncated": bool(truncated),
    }


#: The historical wide DEFAULT of the F-3 knob — unchanged behaviour.
#: Kept as its own name so the env-var resolution below has an explicit
#: fallback to fall back TO (unset / blank / unparseable ⇒ this).
_ALLOW_ANCHORLESS_GLOBAL_LIST_DEFAULT: bool = True

#: The operator-facing env var that re-defaults the knob WITHOUT a code
#: release (a daemon restart re-runs this import). Name + parse rule +
#: fallback are documented in the module docstring (A3).
_ANCHORLESS_GLOBAL_LIST_ENV = "ENSEMBLE_ALLOW_ANCHORLESS_GLOBAL_LIST"
_ANCHORLESS_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_ANCHORLESS_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


def _resolve_allow_anchorless_global_list() -> bool:
    """Resolve the knob's default ONCE, at import (A3).

    Strict parse of ``ENSEMBLE_ALLOW_ANCHORLESS_GLOBAL_LIST``:
    case-insensitive ``1/true/yes/on`` ⇒ True, ``0/false/no/off`` ⇒
    False. Unset, blank, or unparseable ⇒ the historical wide default
    (:data:`_ALLOW_ANCHORLESS_GLOBAL_LIST_DEFAULT`). An unparseable
    value is LOUD (WARNING) but never fatal — a typo cannot take the
    anchor-less lane down, and it cannot silently tighten it either.
    """
    raw = os.environ.get(_ANCHORLESS_GLOBAL_LIST_ENV)
    if raw is None or not raw.strip():
        return _ALLOW_ANCHORLESS_GLOBAL_LIST_DEFAULT
    token = raw.strip().lower()
    if token in _ANCHORLESS_TRUE_TOKENS:
        return True
    if token in _ANCHORLESS_FALSE_TOKENS:
        return False
    logger.warning(
        "rooms: %s=%r is not a boolean token (accepted: %s / %s) — "
        "falling back to the default %r",
        _ANCHORLESS_GLOBAL_LIST_ENV,
        raw,
        sorted(_ANCHORLESS_TRUE_TOKENS),
        sorted(_ANCHORLESS_FALSE_TOKENS),
        _ALLOW_ANCHORLESS_GLOBAL_LIST_DEFAULT,
    )
    return _ALLOW_ANCHORLESS_GLOBAL_LIST_DEFAULT


# Module-level knob (F-3 tightening switch) — single branch consults it.
# Its DEFAULT is env-resolved at import (A3); tests flip the attribute
# itself via ``monkeypatch.setattr``.
_ALLOW_ANCHORLESS_GLOBAL_LIST: bool = _resolve_allow_anchorless_global_list()


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
    # agent's read tool answers with. INFRA-FAULT WRAP (A-P6-25, M3):
    # a faulted RESOLUTION answers the SAME fail-closed 404 the SSE
    # lane's room-resolution pre-branch answers on a broken read
    # (A-P6-11 family) — an unresolvable room is never distinguished
    # from an absent one on the wire; the fault detail is server-side.
    try:
        room = await room_service.get_room(room_id)
    except Exception as exc:  # noqa: BLE001 — infra faults stay typed (M3)
        logger.warning(
            "room read: resolving room=%s failed (%r)",
            room_id,
            exc,
        )
        raise _err(
            404, _ERR_ROOM_NOT_FOUND, f"room not found: {room_id}"
        ) from exc
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
    except Exception as exc:  # noqa: BLE001 — infra faults stay typed (M3)
        # The room EXISTS (resolved above) — a faulted LOG read is a
        # present-but-faulted service ⇒ 503 (retryable), generic
        # message, detail server-side only (A-P6-25, M3).
        logger.warning(
            "room read: reading the log of room=%s failed (%r)",
            room_id,
            exc,
        )
        raise _err(
            503,
            _ERR_ROOM_SERVICE_UNAVAILABLE,
            "the room log could not be read right now",
        ) from exc

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
