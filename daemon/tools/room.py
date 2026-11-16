"""Agent-facing room tools — the ``room`` category (4 tools).

Agent group chat, Phase 2 / P5 (Emperor-ratified design, 2026-11-15 —
``.agents/shared/planning/group-chat/architecture-recommendation.md``,
the spec of record). The tool surface the Emperor's requirement named:
*"When joined, a new tool + guidelines for working with the team (send
milestone reports, changes to the chatroom via toolcall, ...) are
injected into the instance's SYSTEM MESSAGE."*

The four verbs
--------------

* ``room_create`` — open a room (room row + the creator's OWNER
  membership row, ONE transaction — D2). The design of record names
  this verb twice (architecture §12 build-vs-reuse ledger + §13 P5
  row); P1-P3 landed the persistence but NO daemon-side creator, so
  without this verb the whole substrate is unreachable. (A-P5-1.)
* ``room_post`` — post a message, optionally ``@tagging`` members
  (ESCALATION — interrupts) and/or ``addressed_to`` members (ROUTING
  — never interrupts; architecture §4.4).
* ``room_read`` — read the room log (the full history lives BEHIND
  this tool; the system message carries only a short digest).
* ``room_task_decision`` — the PARENT's answer to a task proposal:
  the Ruling-A loop-back. The decision is RECORDED ON THE ROOM, so
  the proposal never stays unanswered (the parent-decided tasking
  rule).

Access posture — TWO layers, BOTH required
------------------------------------------
1. The ``room`` category is PRIVILEGED (entry #23 of
   ``PRIVILEGED_TOOL_CATEGORIES``), so it is opt-in-only and never
   joins the default-grant universe. The explicit ``tools.allow:
   ["room"]`` entries are the visibility path.
2. Every tool body carries its own in-body runtime gate — the
   MEMBERSHIP gate (RK-7-before-append): a caller that is not an
   ACTIVE member of the target room is refused, and the refusal
   happens BEFORE any write. A LITERAL tool name in an allow list
   bypasses the category gate, so the category alone cannot spell the
   audience. The gate is the FIRST logic in each body.

The membership gate is FAIL-CLOSED and it is the pre-append gate:
``room_post`` resolves the caller's membership row BEFORE the append
(the append is the commit point of the room log — an append that
bypassed the membership check would let a non-member write into a
room they cannot even read). RK-2 (the inherited fail-open gate
defect) is the reason this is a hard gate and not a best-effort one.

Construction-time grant only
----------------------------
Membership is granted AT SPAWN (the ``join_room`` spawn parameter),
so a member's membership row exists before its tools are built — the
in-body gate is satisfiable from the first turn. No mid-life tool
mutation is needed or supported.

House rules honored here:

* Tools NEVER raise — every failure returns a typed envelope string.
* An UNEXPECTED exception is logged SERVER-SIDE and answered with a
  generic envelope (the PB-F1 leak class: ``str(exc)`` /
  ``traceback`` / ``repr(exc)`` never reach the payload).
* Every message-path DB touch is OFF-LOOP (``asyncio.to_thread`` via
  the room service — the v0.27.14 event-loop discipline).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Room"

CATEGORY_DOC = """\
Agent group chat — the sibling-collaboration room surface.

Tools in this category:
- ``room_create`` — open a chat-room (the creator becomes its OWNER member)
- ``room_post`` — post a message; ``@name`` tags INTERRUPT a member, ``to:`` names ROUTE to one
- ``room_read`` — read the room log (after_seq + limit)
- ``room_task_decision`` — the parent's recorded answer to a task proposal

Working rules (the guidelines text, mirrored in the joined-room system-message block):
- Report MILESTONES, not a stream of consciousness. One post per milestone; the digest carries the recent ones.
- ``@name`` is for "this needs you now" (it interrupts). ``to: name`` is for "this is for your attention" (it never interrupts).
- Address teammates by their ROOM NAME — the roster is injected with your own name so you can recognise it.
- Reading is cheap; pulling the whole history into a prompt is not — use room_read with a cursor.
"""

#: The category key — EXACTLY ``room`` (single word, snake_case family
#: word; never a variant like ``rooms`` / ``chat`` / ``group-chat``).
ROOM_CATEGORY_KEY = "room"

#: The four tool names this module constructs — the canonical list the
#: registry's ``KNOWN_TOOL_NAMES`` / ``DYNAMIC_TOOL_NAMES`` and the
#: registration pin pack agree with.
ROOM_TOOLS: tuple[str, ...] = (
    "room_create",
    "room_post",
    "room_read",
    "room_task_decision",
)

#: The refusal tokens (the in-process mirror of the door's status
#: semantics — the house refusal envelope shape).
TOKEN_UNKNOWN_AGENT = "unknown-agent"
TOKEN_NOT_AUTHORIZED = "not-authorized"
TOKEN_NO_ROOM_SERVICE = "room-service-unavailable"
TOKEN_NOT_A_MEMBER = "not-a-member"
TOKEN_ROOM_NOT_FOUND = "room-not-found"
TOKEN_ROOM_CLOSED = "room-closed"
TOKEN_UNKNOWN_ROOM = "unknown-room"
TOKEN_INVALID_INPUT = "invalid-input"
TOKEN_INTERNAL_ERROR = "internal-error"
TOKEN_NOT_OWNER = "not-the-owner"
TOKEN_NO_PROPOSAL = "no-such-proposal"
TOKEN_DUPLICATE_DECISION = "duplicate-decision"
TOKEN_ROOM_CAP = "room-cap"

#: The post body cap (architecture §9 — ``post <= 4000 chars``).
ROOM_POST_MAX_CHARS = 4000
#: The read window cap (the read tool is the FULL-HISTORY window; the
#: digest is what stays small).
ROOM_READ_MAX_LIMIT = 200
ROOM_READ_DEFAULT_LIMIT = 50


def _refusal(label: str, token: str, message: str) -> str:
    """The house refusal envelope.

    ``Error: {label} REFUSED — reason={token}: {message}`` — the same
    shape the other privileged categories use, so one parser reads them
    all.
    """
    return f"Error: {label} REFUSED — reason={token}: {message}"


def _is_authorized(agent_id: str) -> bool:
    """Is this agent allowed to hold the room tools at all?

    The category gate (privileged + explicit ``tools.allow``) is the
    FIRST layer; this is the SECOND, independent layer (a literal tool
    name in an allow list bypasses the category gate).

    The room audience is NOT a fixed agent list — it is whoever the
    Emperor's team lanes are. The gate is therefore structural, not
    name-based: the caller must present a NON-EMPTY agent id. The
    AUTHORITY that actually matters is the MEMBERSHIP gate in each
    body (an authorized agent that is not a member of the room still
    gets nothing).
    """
    return isinstance(agent_id, str) and bool(agent_id.strip())


def _room_service(manager: Any):
    """The room service handle, or None when the daemon has none.

    The ``*_if_available`` posture: a test double / partial-init
    manager without the room service yields a typed refusal, never a
    raise.
    """
    if manager is None:
        return None
    return getattr(manager, "_room_chat_service", None)


def _delivery_service(manager: Any):
    """The delivery-service handle, or None."""
    if manager is None:
        return None
    return getattr(manager, "_room_delivery_service", None)


def _tree_root_id(manager: Any, instance_id: str) -> str | None:
    """Resolve the caller's tree root (the room's scope anchor).

    Sync call — the caller runs it inside ``asyncio.to_thread``.
    """
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return None
    return repo.get_tree_root_id(instance_id)


def _member_dict(member: dict[str, Any]) -> dict[str, Any]:
    """The public shape of a roster member (no cursor internals)."""
    return {
        "member_name": member.get("member_name"),
        "role": member.get("role"),
        "state": member.get("state"),
        "agent_id": member.get("agent_id"),
    }


def create_room_tools(
    manager,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Create the four room tools for one instance/agent.

    Args:
        manager: The instance manager — supplies the room service
            (``_room_chat_service``), the delivery service
            (``_room_delivery_service``) and the instance repository
            (the tree-root lookup).
        current_instance_id: The CALLER's instance id — the author
            identity on every write (the membership gate resolves the
            caller's roster row from it).
        agent_id: The caller's agent id — captured into the layer-2
            gate.
        agent_tag: Caller's version tag (signature parity; unused).
    """
    caller_instance_id = current_instance_id or ""
    caller_agent_id = agent_id or ""

    class RoomCreateInput(BaseModel):
        """Input schema for room_create."""

        name: Annotated[
            str,
            Field(
                description=(
                    "Human-readable room name (what the team calls it, "
                    "e.g. 'scene-3 build')."
                )
            ),
        ]
        member_name: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Your purpose name in this room (e.g. 'producer'). "
                    "Omit to use your agent id. Teammates address you by "
                    "this name."
                ),
            ),
        ]

    class RoomPostInput(BaseModel):
        """Input schema for room_post."""

        room_id: Annotated[
            str,
            Field(description="The chatroom UUID you are a member of."),
        ]
        text: Annotated[
            str,
            Field(
                description=(
                    "The message body — a MILESTONE report, not a "
                    "stream of consciousness."
                )
            ),
        ]
        tagged: Annotated[
            list[str],
            Field(
                default=[],
                description=(
                    "Member names to INTERRUPT (``@name`` semantics) — "
                    "use sparingly; an interrupt stops their current work."
                ),
            ),
        ]
        addressed_to: Annotated[
            list[str],
            Field(
                default=[],
                description=(
                    "Member names to ROUTE to (``to: name`` semantics) — "
                    "they see it in their next digest; it never "
                    "interrupts them."
                ),
            ),
        ]

    class RoomReadInput(BaseModel):
        """Input schema for room_read."""

        room_id: Annotated[
            str,
            Field(description="The chatroom UUID you are a member of."),
        ]
        after_seq: Annotated[
            int,
            Field(
                default=0,
                description=(
                    "Return messages with seq > this (0 = from the "
                    "start). Use the last seq you saw as the cursor."
                ),
            ),
        ]
        limit: Annotated[
            int,
            Field(
                default=ROOM_READ_DEFAULT_LIMIT,
                description=f"How many messages to return (max {ROOM_READ_MAX_LIMIT}).",
            ),
        ]

    class RoomTaskDecisionInput(BaseModel):
        """Input schema for room_task_decision."""

        room_id: Annotated[
            str,
            Field(description="The chatroom UUID."),
        ]
        proposal_seq: Annotated[
            int,
            Field(
                description=(
                    "The seq of the task_proposal message you are "
                    "answering (the seq from the notice)."
                )
            ),
        ]
        decision: Annotated[
            str,
            Field(
                description=(
                    "Your decision, e.g. 'revive', 'reroute', "
                    "'answer-in-room', 'defer'."
                )
            ),
        ]
        note: Annotated[
            str,
            Field(default="", description="Optional one-line rationale."),
        ]

    @register_tool_category(ROOM_CATEGORY_KEY)
    @tool(args_schema=RoomCreateInput)
    async def room_create(name: str, member_name: str = "") -> str:
        """Open a chat-room for your team. Use tool_help("room_create") for details."""
        # ── the layer-2 gate: FIRST logic in the body.
        if not _is_authorized(caller_agent_id):
            return _refusal(
                "room_create",
                TOKEN_UNKNOWN_AGENT,
                "the room tools require a resolved agent identity",
            )
        if not caller_instance_id:
            return _refusal(
                "room_create",
                TOKEN_NOT_AUTHORIZED,
                "the room tools require a caller instance id",
            )
        room_service = _room_service(manager)
        if room_service is None:
            return _refusal(
                "room_create",
                TOKEN_NO_ROOM_SERVICE,
                "the chat-room service is not available in this daemon",
            )
        try:
            from daemon.repositories.room.membership import (
                ROOM_MAX_ACTIVE_ROOMS_PER_TREE,
                RoomJoinRoomCapError,
            )

            tree_root = await asyncio.to_thread(
                _tree_root_id, manager, caller_instance_id
            )
            if not tree_root:
                return _refusal(
                    "room_create",
                    TOKEN_INVALID_INPUT,
                    "the caller's tree root could not be resolved — the "
                    "room scope is unknown (fail-closed)",
                )
            owner_name = (member_name or caller_agent_id or "owner").strip()
            if not owner_name:
                owner_name = "owner"
            created = await room_service.create_room(
                name=name,
                tree_root_id=tree_root,
                owner_instance_id=caller_instance_id,
                owner_member_name=owner_name,
                owner_agent_id=caller_agent_id,
            )
            room = created.get("room") or {}
            owner_member = created.get("owner_member") or {}
            return json.dumps(
                {
                    "room_id": room.get("room_id"),
                    "name": room.get("name"),
                    "state": room.get("state"),
                    "your_member_name": owner_member.get("member_name"),
                    "hint": (
                        "spawn teammates with join_room=<room_id> and "
                        "member_name=<their purpose name>; the roster and "
                        "your guidelines arrive in their system message"
                    ),
                },
                indent=2,
            )
        except RoomJoinRoomCapError:
            # The tree's active-room budget is exhausted (the
            # repository refuses BEFORE the insert, A-P5-14) — a typed
            # policy refusal, never the raw repository text.
            logger.warning(
                "room_create refused: the caller's tree is at its "
                "active-room cap (%s)",
                ROOM_MAX_ACTIVE_ROOMS_PER_TREE,
            )
            return _refusal(
                "room_create",
                TOKEN_ROOM_CAP,
                "this tree already holds the maximum number of active "
                f"rooms (ROOM_MAX_ACTIVE_ROOMS_PER_TREE="
                f"{ROOM_MAX_ACTIVE_ROOMS_PER_TREE}) — close a room "
                "before opening another",
            )
        except Exception as exc:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("room_create failed")
            return _refusal(
                "room_create",
                TOKEN_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side); retry later",
            )

    @register_tool_category(ROOM_CATEGORY_KEY)
    @tool(args_schema=RoomPostInput)
    async def room_post(
        room_id: str,
        text: str,
        tagged: list[str] | None = None,
        addressed_to: list[str] | None = None,
    ) -> str:
        """Post a milestone message to a chat-room. Use tool_help("room_post") for details."""
        # ── the layer-2 gate: FIRST logic in the body.
        if not _is_authorized(caller_agent_id):
            return _refusal(
                "room_post",
                TOKEN_UNKNOWN_AGENT,
                "the room tools require a resolved agent identity",
            )
        if not caller_instance_id:
            return _refusal(
                "room_post",
                TOKEN_NOT_AUTHORIZED,
                "the room tools require a caller instance id",
            )
        room_service = _room_service(manager)
        if room_service is None:
            return _refusal(
                "room_post",
                TOKEN_NO_ROOM_SERVICE,
                "the chat-room service is not available in this daemon",
            )
        body = (text or "").strip()
        if not body:
            return _refusal(
                "room_post", TOKEN_INVALID_INPUT, "the message body is empty"
            )
        if len(body) > ROOM_POST_MAX_CHARS:
            return _refusal(
                "room_post",
                TOKEN_INVALID_INPUT,
                f"the message is {len(body)} chars — the cap is "
                f"{ROOM_POST_MAX_CHARS} (post a milestone, not a dump)",
            )
        try:
            from daemon.repositories.room.repository import (
                RoomClosedError,
                RoomNotFoundError,
            )

            # A PURGED room is NONEXISTENT (R2) — the existence check
            # comes BEFORE the membership check so a purged room reads
            # as not-found, never as "you are not a member".
            room = await room_service.get_room(room_id)
            if room is None:
                return _refusal(
                    "room_post",
                    TOKEN_ROOM_NOT_FOUND,
                    f"room {room_id} does not exist (absent or purged)",
                )
            # ── THE MEMBERSHIP GATE (RK-7-before-append, fail-closed):
            # the caller's roster row is resolved BEFORE the append —
            # the append is the commit point of the room log, so a
            # non-member must never reach it. A LEFT row is NOT an
            # active membership.
            member = await room_service.get_member(room_id, caller_instance_id)
            if member is None or member.get("state") != "active":
                return _refusal(
                    "room_post",
                    TOKEN_NOT_A_MEMBER,
                    f"you are not an active member of room {room_id} — "
                    "posting is refused (fail-closed)",
                )
            roster = await room_service.list_members(
                room_id, caller_instance_id=caller_instance_id, active_only=True
            )
            from daemon.services.room_delivery import resolve_room_targets

            tagged_names = list(tagged or [])
            addressed_names = list(addressed_to or [])
            resolved_tags, tag_warnings = resolve_room_targets(
                roster, tagged_names, kind="tag"
            )
            resolved_addressed, addr_warnings = resolve_room_targets(
                roster, addressed_names, kind="addressed"
            )
            warnings = list(tag_warnings) + list(addr_warnings)
            # The RESOLVED names persist (architecture §4.4); the
            # UNRESOLVED ones persist as the durable warning set
            # (A-P3-16) — the row carries both, machine-shaped and
            # human-shaped respectively.
            appended = None
            try:
                appended = await room_service.append_message(
                    room_id=room_id,
                    author_instance_id=caller_instance_id,
                    author_name=member["member_name"],
                    text_body=body,
                    tagged_members=[m["member_name"] for m in resolved_tags],
                    addressed_members=[m["member_name"] for m in resolved_addressed],
                    resolution_warnings=warnings,
                )
            except RoomClosedError:
                return _refusal(
                    "room_post",
                    TOKEN_ROOM_CLOSED,
                    f"room {room_id} is closed — posting is refused "
                    "(the capability row: closed = read-only)",
                )
            except RoomNotFoundError:
                return _refusal(
                    "room_post",
                    TOKEN_ROOM_NOT_FOUND,
                    f"room {room_id} does not exist (absent or purged)",
                )
            # Route the post (the delivery router). Typed error
            # surfacing: the router has no try/except of its own, so
            # the tool owns the boundary — a routing failure NEVER
            # loses the post (the row is already durable) and never
            # raises into the agent turn.
            routing: dict[str, Any] = {"tag_outcomes": [], "addressed_outcomes": []}
            delivery = _delivery_service(manager)
            if delivery is not None:
                try:
                    routing = await delivery.route_post(
                        room_id=room_id, post=appended
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "room_post routing failed (room=%s seq=%s): %r",
                        room_id, appended.get("seq"), exc,
                    )
                    routing = {
                        "tag_outcomes": [],
                        "addressed_outcomes": [],
                        "routing_error": True,
                    }
            return json.dumps(
                {
                    "seq": appended.get("seq"),
                    "room_id": room_id,
                    "author": member["member_name"],
                    "tagged": [m["member_name"] for m in resolved_tags],
                    "addressed_to": [m["member_name"] for m in resolved_addressed],
                    "warnings": warnings,
                    "tag_outcomes": routing.get("tag_outcomes", []),
                    "addressed_outcomes": routing.get("addressed_outcomes", []),
                    "routing_error": bool(routing.get("routing_error")),
                },
                indent=2,
            )
        except Exception as exc:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("room_post failed")
            return _refusal(
                "room_post",
                TOKEN_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side); retry later",
            )

    @register_tool_category(ROOM_CATEGORY_KEY)
    @tool(args_schema=RoomReadInput)
    async def room_read(room_id: str, after_seq: int = 0, limit: int = ROOM_READ_DEFAULT_LIMIT) -> str:
        """Read a chat-room's messages. Use tool_help("room_read") for details."""
        # ── the layer-2 gate: FIRST logic in the body.
        if not _is_authorized(caller_agent_id):
            return _refusal(
                "room_read",
                TOKEN_UNKNOWN_AGENT,
                "the room tools require a resolved agent identity",
            )
        if not caller_instance_id:
            return _refusal(
                "room_read",
                TOKEN_NOT_AUTHORIZED,
                "the room tools require a caller instance id",
            )
        room_service = _room_service(manager)
        if room_service is None:
            return _refusal(
                "room_read",
                TOKEN_NO_ROOM_SERVICE,
                "the chat-room service is not available in this daemon",
            )
        try:
            after = max(int(after_seq or 0), 0)
            window = int(limit or ROOM_READ_DEFAULT_LIMIT)
            if window <= 0:
                window = ROOM_READ_DEFAULT_LIMIT
            window = min(window, ROOM_READ_MAX_LIMIT)
            room = await room_service.get_room(room_id)
            if room is None:
                return _refusal(
                    "room_read",
                    TOKEN_ROOM_NOT_FOUND,
                    f"room {room_id} does not exist (absent or purged)",
                )
            # The read rides the repository's fail-closed read
            # predicate (active member ∪ owner-ancestry); a
            # non-member / purged room surfaces as the typed
            # not-found.
            from daemon.repositories.room.repository import RoomNotFoundError

            try:
                messages = await room_service.read_messages(
                    room_id,
                    caller_instance_id=caller_instance_id,
                    after_seq=after,
                    limit=window,
                )
            except RoomNotFoundError:
                return _refusal(
                    "room_read",
                    TOKEN_ROOM_NOT_FOUND,
                    f"room {room_id} is not available to you (not a member, "
                    "or it was purged)",
                )
            # The read ACK: the read cursor advances to the highest
            # seq returned (that is what the cursor pair is FOR — the
            # re-derivation predicate is seq > max(read, delivered), so
            # an undelivered row still re-derives).
            if messages:
                highest = max(int(m.get("seq") or 0) for m in messages)
                await room_service.advance_read_cursor(
                    room_id, caller_instance_id, highest
                )
            roster = await room_service.list_members(
                room_id, caller_instance_id=caller_instance_id, active_only=True
            )
            return json.dumps(
                {
                    "room_id": room_id,
                    "room_name": room.get("name"),
                    "state": room.get("state"),
                    "messages": [
                        {
                            "seq": m.get("seq"),
                            "author": m.get("author_name"),
                            "kind": m.get("kind"),
                            "text": m.get("text"),
                            "tagged": m.get("tagged_members") or [],
                            "addressed_to": m.get("addressed_members") or [],
                            "resolution_warnings": m.get("resolution_warnings") or [],
                        }
                        for m in messages
                    ],
                    "roster": [_member_dict(m) for m in roster],
                    "next_after_seq": max(
                        [int(m.get("seq") or 0) for m in messages] or [after]
                    ),
                },
                indent=2,
            )
        except Exception as exc:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("room_read failed")
            return _refusal(
                "room_read",
                TOKEN_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side); retry later",
            )

    @register_tool_category(ROOM_CATEGORY_KEY)
    @tool(args_schema=RoomTaskDecisionInput)
    async def room_task_decision(
        room_id: str,
        proposal_seq: int,
        decision: str,
        note: str = "",
    ) -> str:
        """Record your decision on a task proposal in a chat-room. Use tool_help("room_task_decision") for details."""
        # ── the layer-2 gate: FIRST logic in the body.
        if not _is_authorized(caller_agent_id):
            return _refusal(
                "room_task_decision",
                TOKEN_UNKNOWN_AGENT,
                "the room tools require a resolved agent identity",
            )
        if not caller_instance_id:
            return _refusal(
                "room_task_decision",
                TOKEN_NOT_AUTHORIZED,
                "the room tools require a caller instance id",
            )
        room_service = _room_service(manager)
        delivery = _delivery_service(manager)
        if room_service is None or delivery is None:
            return _refusal(
                "room_task_decision",
                TOKEN_NO_ROOM_SERVICE,
                "the chat-room service is not available in this daemon",
            )
        if not (decision or "").strip():
            return _refusal(
                "room_task_decision",
                TOKEN_INVALID_INPUT,
                "the decision text is empty",
            )
        try:
            from daemon.repositories.room.repository import (
                RoomError,
                RoomNotFoundError,
            )

            room = await room_service.get_room(room_id)
            if room is None:
                return _refusal(
                    "room_task_decision",
                    TOKEN_ROOM_NOT_FOUND,
                    f"room {room_id} does not exist (absent or purged)",
                )
            # TASKING IS PARENT-DECIDED: only the room's OWNER (the
            # parent that opened the room) records a decision. A member
            # that is not the owner is refused — fail-closed.
            if room.get("owner_instance_id") != caller_instance_id:
                return _refusal(
                    "room_task_decision",
                    TOKEN_NOT_OWNER,
                    "only the room owner (the parent) can decide a task "
                    "proposal — tasking is parent-decided",
                )
            member = await room_service.get_member(room_id, caller_instance_id)
            if member is None or member.get("state") != "active":
                return _refusal(
                    "room_task_decision",
                    TOKEN_NOT_A_MEMBER,
                    f"you are not an active member of room {room_id}",
                )
            try:
                result = await delivery.record_task_decision(
                room_id=room_id,
                proposal_seq=int(proposal_seq),
                decision=decision.strip(),
                note=(note or "").strip(),
                decider_instance_id=caller_instance_id,
                decider_member_name=member["member_name"],
            )
            except RoomNotFoundError as exc:
                return _refusal(
                    "room_task_decision",
                    TOKEN_NO_PROPOSAL,
                    f"no task proposal at seq {proposal_seq} in room {room_id}",
                )
            except RoomError as exc:
                # PB-F1: the repository raises RoomError with the
                # IntegrityError EMBEDDED (the dedup-UNIQUE refusal —
                # constraint name + DETAIL key values). That detail is
                # server-side only: a static typed refusal below, the
                # raw text to the server log (never the payload).
                logger.warning(
                    "room_task_decision refused: room=%s seq=%s: %s",
                    room_id,
                    proposal_seq,
                    exc,
                )
                return _refusal(
                    "room_task_decision",
                    TOKEN_DUPLICATE_DECISION,
                    "a decision for this proposal is already recorded "
                    "(one decision per proposal, per member — the "
                    "dedup rule); the room log is unchanged",
                )
            return json.dumps({"recorded": True, **result}, indent=2)
        except Exception as exc:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("room_task_decision failed")
            return _refusal(
                "room_task_decision",
                TOKEN_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side); retry later",
            )

    room_create._full_doc_ = _build_full_doc("room_create")
    room_post._full_doc_ = _build_full_doc("room_post")
    room_read._full_doc_ = _build_full_doc("room_read")
    room_task_decision._full_doc_ = _build_full_doc("room_task_decision")

    return [room_create, room_post, room_read, room_task_decision]


def _build_full_doc(label: str) -> str:
    """The tool's full documentation (tool_help surface).

    Carries the working rules (the guidelines text, in the agent-facing
    voice) and the access posture.
    """
    return (
        f"{label} — one verb of the agent group-chat room surface "
        "(the ``room`` privileged category).\n\n"
        "Working rules\n"
        "-------------\n"
        "Report MILESTONES, not a stream of consciousness. One post per "
        "milestone; the injected digest carries the recent ones and the "
        "full history stays behind room_read.\n"
        "@name INTERRUPTS a member (they stop what they are doing) — "
        "use it when the work genuinely needs them now.\n"
        "to: name ROUTES to a member (they see it in their next digest) "
        "and never interrupts.\n"
        "Address teammates by their ROOM NAME — the roster (and your "
        "own name) arrive in the joined-room system-message block.\n\n"
        "Access\n"
        "------\n"
        "The category is PRIVILEGED (entry #23) — opt-in via an explicit "
        "tools.allow entry — AND every call is gated on ROOM MEMBERSHIP: "
        "a non-member is refused BEFORE any write (fail-closed).\n"
    )
