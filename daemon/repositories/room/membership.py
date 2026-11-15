"""Room membership mechanics — admission + the naming registry (P2).

Phase-2 of the agent group chat lane (design:
``.agents/shared/planning/group-chat/architecture-recommendation.md``
§2/§6/§9 — Emperor-ratified 2026-11-15, the spec of record). This module
owns the THREE P2 membership primitives:

1. ``validate_room_join`` — THE pinned spawn-time admission predicate
   (architecture §2, verbatim shape): room exists ∧ ``state=active`` ∧
   the joiner's resolved tree root equals ``room.tree_root_id`` ∧
   ACTIVE membership rows (owner included) < ``ROOM_MAX_MEMBERS``
   (LEFT rows are free — excluded from the count) ∧ the tree's
   active-room budget is respected. EVERY failed conjunct raises its
   OWN typed error (all distinct, all fail-closed) — the spawn never
   proceeds on a refusal and no instance row is written.

2. The tier-4 per-room suffix registry: ``resolve_member_name`` picks
   the bare base name for the FIRST bearer and the smallest free
   ``-N`` suffix for colliders. Uniqueness is scoped PER-ROOM (never
   global), spans ALL membership rows (LEFT rows keep their names —
   roster history — and the ``UNIQUE(room_id, member_name)`` backstop
   spans them too), and is assigned INSIDE the caller's transaction
   (``build_member_row_in_session``) under the per-room lock held by
   the service layer. The ``UNIQUE`` constraint is the DB-level
   backstop for anything the lock cannot see
   (:class:`MemberNameCollisionError`).

3. ``restore_eligibility`` — the D5 two-conjunct restore rule as a
   pure predicate: eligible iff ``room.state ∈ {active, closed}`` AND
   ``member.state = active``. A LEFT member's restore grants nothing
   (the pin); a purged/absent room grants nothing (nonexistent).

Layer discipline: every DB-touching function here is SYNC and takes
the CALLER'S engine or session (the P1 repository convention — this
module never builds an engine and never reads ambient ``POSTGRES_*``).
Async callers go through ``RoomChatService`` / the spawn tool path,
which wrap in ``asyncio.to_thread`` (off-loop house discipline).

Caps (architecture §9): ``ROOM_MAX_MEMBERS=16`` counts ACTIVE rows
only (the owner counts, LEFT rows are free);
``ROOM_MAX_ACTIVE_ROOMS_PER_TREE=8`` bounds the tree's active-room
budget. Counting base for the room cap at JOIN time excludes the
target room itself (it already exists — the join adds a member, not a
room; the conjunct is the fail-closed anomaly guard for a tree
somehow over its budget). Resolution recorded in
``decisions.md`` appendix A-P2-1.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from daemon.repositories.room.models import (
    ChatRoom,
    ChatRoomMember,
    RoomMemberRole,
    RoomMemberState,
    RoomState,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ROOM_MAX_MEMBERS",
    "ROOM_MAX_ACTIVE_ROOMS_PER_TREE",
    "MEMBER_NAME_MAX_LEN",
    "RoomJoinError",
    "RoomJoinUnknownRoomError",
    "RoomJoinRoomStateError",
    "RoomJoinCrossTreeError",
    "RoomJoinMemberCapError",
    "RoomJoinRoomCapError",
    "RoomLeaveOwnerError",
    "RoomJoinSpec",
    "sanitize_member_name",
    "resolve_member_name",
    "validate_room_join",
    "join_room_in_session",
    "restore_eligibility",
    "leave_room",
]


# ── Naming-cascade vocabulary (the pinned string values) ──────────────────
# The tier labels ride the RoomJoinSpec → spawn result line (no silent
# naming); their VALUES are pinned by tests (byte-identical) — the
# constants only name them. ROLE_CHILD is the default membership role
# (RoomMemberRole.CHILD's value, named for the spawn-tool call sites).
TIER_PARENT_PROVIDED = "parent_provided"
TIER_QUICK_MODEL = "quick_model"
TIER_AGENT_FALLBACK = "agent_fallback"
ROLE_CHILD = RoomMemberRole.CHILD.value


@dataclass(frozen=True)
class RoomJoinSpec:
    """Spawn-path room-join contract (tool → lifecycle).

    Carries the RESOLVED join parameters: ``room_id`` (admission
    validated by the caller BEFORE the spawn call), ``base_name``
    (the cascade tiers 1-3 result — tier 4's suffix is applied at
    registration INSIDE the spawn transaction), the membership
    ``role``, the child's ``agent_id`` (denormalized tag-resolution
    aid), and the cascade ``tier`` label the spawn result string
    surfaces (no silent naming).

    ``None`` everywhere = the zero-change legacy spawn path.
    """

    room_id: str
    base_name: str
    role: str = ROLE_CHILD
    agent_id: str | None = None
    tier: str = TIER_PARENT_PROVIDED

# Architecture §9 caps — typed fail-closed at join admission.
ROOM_MAX_MEMBERS = 16
ROOM_MAX_ACTIVE_ROOMS_PER_TREE = 8

# Member names are kebab-case purpose names (the @tag/address surface,
# P3): bounded, lowercase, single-dash. The sanitize ladder never
# invents content — it normalizes or rejects.
MEMBER_NAME_MAX_LEN = 48

_KEBAB_RE = re.compile(r"[^a-z0-9]+")


class RoomJoinError(Exception):
    """Base for every spawn-time room-join admission refusal.

    The spawn tool maps each subclass to its OWN agent-readable
    refusal text (all distinct); catching this base catches the whole
    admission family. The commit-time naming backstop
    (:class:`~daemon.repositories.room.repository.MemberNameCollisionError`)
    is ALSO a member of this family (dual inheritance, deliberate):
    it is semantically a join failure, so the spawn tool's room-join
    handler catches it too — its DISTINCT commit-time text is matched
    BEFORE the admission-refusal mapping. The remaining typed room
    errors (:class:`~daemon.repositories.room.repository.RoomError`
    members below the collision) are untouched by this base.
    """


class RoomJoinUnknownRoomError(RoomJoinError):
    """Conjunct 1 — the room_id does not resolve to a row (absent or
    purged; R2: purged rooms are nonexistent)."""


class RoomJoinRoomStateError(RoomJoinError):
    """Conjunct 2 — the room exists but ``state != active`` (closed
    rooms admit no new members; the capability row is read-only)."""


class RoomJoinCrossTreeError(RoomJoinError):
    """Conjunct 3 — the joiner's resolved tree root does not equal
    ``room.tree_root_id`` (rooms are tree-scoped). Fail-closed when
    the joiner's tree root cannot be resolved at all."""


class RoomJoinMemberCapError(RoomJoinError):
    """Conjunct 4 — ACTIVE membership rows (owner included) already at
    ``ROOM_MAX_MEMBERS``. LEFT rows are free (excluded)."""


class RoomJoinRoomCapError(RoomJoinError):
    """Conjunct 5 — the tree's active-room budget is exhausted
    (anomaly guard: the join itself adds no room; see the counting
    base note in the module docstring / decisions appendix A-P2-1)."""


class RoomLeaveOwnerError(Exception):
    """Leave refused for the OWNER row — the owner membership is
    retired only WITH the room (D2; the purge is the only remover).

    Deliberately OUTSIDE the :class:`RoomError` family (base
    ``Exception``): this module cannot import the repository's error
    vocabulary — repository.py imports THIS module (the one-direction
    constraint that keeps the room package cycle-free). P5 catchers
    must catch this type EXPLICITLY; no room-error base will carry
    it.
    """


# ── Name normalization + the tier-4 suffix registry (pure) ────────────────


def sanitize_member_name(raw: str | None) -> str | None:
    """Normalize a purpose-name candidate to a safe per-room name.

    Lowercase; every non-``[a-z0-9]`` run collapses to ONE dash;
    leading/trailing dashes stripped; truncated to
    ``MEMBER_NAME_MAX_LEN`` (trailing dash trimmed again). Returns
    ``None`` for an empty/whitespace/unusable input — callers treat
    that as tier-2 failure (never invent a name).
    """
    if raw is None:
        return None
    cleaned = _KEBAB_RE.sub("-", raw.strip().lower()).strip("-")
    if not cleaned:
        return None
    if len(cleaned) > MEMBER_NAME_MAX_LEN:
        cleaned = cleaned[:MEMBER_NAME_MAX_LEN].rstrip("-")
    return cleaned or None


def resolve_member_name(taken: set[str], base_name: str) -> str:
    """Tier-4 suffix registry — smallest free name for ``base_name``.

    The FIRST bearer gets the bare base; each collider gets the
    smallest free ``-N`` suffix (``capturer-1``, ``capturer-2``, …).
    ``taken`` spans ALL membership rows of the room (LEFT included —
    their names stay registered under the ``UNIQUE`` backstop).
    Uniqueness is PER-ROOM by construction: ``taken`` is the caller's
    room-scoped read.
    """
    if base_name not in taken:
        return base_name
    n = 1
    while f"{base_name}-{n}" in taken:
        n += 1
    return f"{base_name}-{n}"


# ── Admission (the pinned predicate) ──────────────────────────────────────


def validate_room_join(
    engine: Engine,
    *,
    room_id: str,
    joiner_tree_root: str | None,
) -> dict[str, Any]:
    """THE pinned spawn-time admission predicate — 5 conjuncts, typed.

    Conjuncts (architecture §2, exact shape; every failure typed and
    DISTINCT):

    1. room exists (absent/purged → :class:`RoomJoinUnknownRoomError`)
    2. ``room.state == active`` (→ :class:`RoomJoinRoomStateError`)
    3. ``joiner_tree_root == room.tree_root_id`` — ``None`` (the
       joiner's root could not be resolved) fails CLOSED
       (→ :class:`RoomJoinCrossTreeError`)
    4. ``COUNT(members WHERE state=active) < ROOM_MAX_MEMBERS`` — the
       owner's seeded row COUNTS, LEFT rows are FREE
       (→ :class:`RoomJoinMemberCapError`)
    5. the tree's active-room budget — ACTIVE rooms sharing the
       room's ``tree_root_id``, EXCLUDING the target room, must be
       ``< ROOM_MAX_ACTIVE_ROOMS_PER_TREE``
       (→ :class:`RoomJoinRoomCapError`; counting base recorded in
       decisions appendix A-P2-1)

    Returns the detached room payload on success.
    """
    with Session(engine) as session:
        room = session.get(ChatRoom, room_id)
        if room is None:
            raise RoomJoinUnknownRoomError(
                f"room join refused: room={room_id} not found (absent or "
                f"purged — R2: purged rooms are nonexistent)"
            )
        if room.state != RoomState.ACTIVE.value:
            raise RoomJoinRoomStateError(
                f"room join refused: room={room_id} state={room.state!r} "
                f"(only active rooms admit members)"
            )
        if joiner_tree_root is None or joiner_tree_root != room.tree_root_id:
            raise RoomJoinCrossTreeError(
                f"room join refused: joiner tree root "
                f"{joiner_tree_root!r} != room tree root "
                f"{room.tree_root_id!r} for room={room_id} (rooms are "
                f"tree-scoped; cross-tree joins fail closed)"
            )
        active_members = len(
            session.exec(
                select(ChatRoomMember).where(
                    ChatRoomMember.room_id == room_id,
                    ChatRoomMember.state == RoomMemberState.ACTIVE.value,
                )
            ).all()
        )
        if active_members >= ROOM_MAX_MEMBERS:
            raise RoomJoinMemberCapError(
                f"room join refused: room={room_id} already has "
                f"{active_members} active members (ROOM_MAX_MEMBERS="
                f"{ROOM_MAX_MEMBERS}; owner counts, LEFT rows are free)"
            )
        sibling_rooms = len(
            session.exec(
                select(ChatRoom).where(
                    ChatRoom.tree_root_id == room.tree_root_id,
                    ChatRoom.state == RoomState.ACTIVE.value,
                    ChatRoom.room_id != room_id,
                )
            ).all()
        )
        if sibling_rooms >= ROOM_MAX_ACTIVE_ROOMS_PER_TREE:
            raise RoomJoinRoomCapError(
                f"room join refused: tree {room.tree_root_id} already "
                f"holds {sibling_rooms} other active rooms "
                f"(ROOM_MAX_ACTIVE_ROOMS_PER_TREE="
                f"{ROOM_MAX_ACTIVE_ROOMS_PER_TREE})"
            )
        from daemon.repositories.room.repository import room_payload

        return room_payload(room)


# ── In-transaction member-row construction (the registry mechanics) ───────


def join_room_in_session(
    session: Session,
    *,
    room_id: str,
    instance_id: str,
    base_name: str,
    role: str = RoomMemberRole.CHILD.value,
    agent_id: str | None = None,
) -> ChatRoomMember:
    """Build the membership row INSIDE the caller's transaction.

    Reads the room's registered names (ALL rows — LEFT included; the
    ``UNIQUE(room_id, member_name)`` backstop spans them), runs the
    tier-4 suffix registry (:func:`resolve_member_name`), and
    ``session.add()``s the row — the caller's commit covers it, which
    is what makes "membership row + instance JSONB in one transaction"
    (P2 exit criterion) literally one commit.

    CONTRACT: the caller MUST hold the per-room lock
    (:class:`~daemon.repositories.room.service.RoomChatService`) so
    concurrent same-room joins cannot interleave read→insert; the
    ``UNIQUE`` constraint remains the DB-level backstop and surfaces
    as :class:`MemberNameCollisionError` via the repository's typed
    integrity classifier.
    """
    # The room row is read FIRST — before any pending add — so no
    # query-invoked autoflush fires after the membership row is
    # pending (the collision must keep failing at the CALLER's commit,
    # where the typed backstop classifies it; A-P2-11 emission note).
    room = session.get(ChatRoom, room_id)
    registered: set[str] = set(
        session.exec(
            select(ChatRoomMember.member_name).where(
                ChatRoomMember.room_id == room_id
            )
        ).all()
    )
    final_name = resolve_member_name(registered, base_name)
    row = ChatRoomMember(
        room_id=room_id,
        instance_id=instance_id,
        member_name=final_name,
        agent_id=agent_id,
        role=role,
        joined_at=datetime.now(timezone.utc).isoformat(),
        last_read_seq=0,
        last_delivered_seq=0,
        state=RoomMemberState.ACTIVE.value,
    )
    session.add(row)
    # A-P2-11 (closed in P3): admission emits the reserved content-free
    # ``room_join`` EventKind — SAME shape as ``room_leave`` (ids +
    # per-room name only, the OWNER's event stream) and the SAME
    # transaction, so a rolled-back admission (name collision) leaves
    # NO phantom event. The asymmetric P2 posture (join silent, leave
    # emitting) is retired; A-P2-11 documented the deferral. NO query
    # after this point — the owner stream came from the pre-add read.
    from daemon.repositories.event.models import Event, EventKind

    owner_stream = room.owner_instance_id if room is not None else instance_id
    if room is None:
        logger.warning(
            "room_join: room row missing for room=%s — event parked on joiner stream",
            room_id,
        )
    session.add(
        Event(
            instance_id=owner_stream,
            kind=EventKind.ROOM_JOIN.value,
            data=json.dumps(
                {
                    "room_id": room_id,
                    "instance_id": instance_id,
                    "member_name": final_name,
                }
            ),
            created_at=datetime.now(timezone.utc),
        )
    )
    return row


# ── D5 restore predicate (pure read) ──────────────────────────────────────


def restore_eligibility(
    engine: Engine, *, room_id: str, instance_id: str
) -> dict[str, Any]:
    """The D5 two-conjunct restore rule as a predicate payload.

    Eligible iff BOTH conjuncts hold: ``room.state ∈ {active, closed}``
    AND ``member.state == active``. A LEFT member's restore grants
    NOTHING (the pin); a purged/absent room resolves ``reason=
    "room_not_found"`` (nonexistent — nothing to restore into); a
    caller with no membership row was never a member.
    """
    with Session(engine) as session:
        room = session.get(ChatRoom, room_id)
        if room is None:
            return {
                "eligible": False,
                "reason": "room_not_found",
                "room_state": None,
                "member_state": None,
                "member_name": None,
            }
        member = session.get(ChatRoomMember, (room_id, instance_id))
        member_state = member.state if member is not None else None
        member_name = member.member_name if member is not None else None
        room_ok = room.state in (RoomState.ACTIVE.value, RoomState.CLOSED.value)
        member_ok = member is not None and (
            member.state == RoomMemberState.ACTIVE.value
        )
        if member is None:
            reason = "not_a_member"
        elif not member_ok:
            reason = "member_left"
        elif not room_ok:
            reason = "room_state"
        else:
            reason = "eligible"
        return {
            "eligible": bool(room_ok and member_ok),
            "reason": reason,
            "room_state": room.state,
            "member_state": member_state,
            "member_name": member_name,
        }


# ── Leave (owner exempt — D2) ─────────────────────────────────────────────


def leave_room(
    repo: Any,
    *,
    room_id: str,
    instance_id: str,
) -> dict[str, Any]:
    """Set the membership row ``state=left`` (roster history kept).

    Owner exempt: the owner row is retired only WITH the room (D2) —
    :class:`RoomLeaveOwnerError`. The row itself is never deleted
    (LEFT rows stay on disk: roster history + cursor substrate) and
    its NAME stays registered (the ``UNIQUE`` backstop spans LEFT
    rows). Emits ONE content-free ``room_leave`` audit event. The
    spawn-side ``instance_metadata["join_room"]`` echo is tolerated
    and left INERT (the design's "JSONB echo tolerated" — no JSONB
    write back).

    ``repo`` is the owning :class:`RoomRepository` (engine + typed
    error vocabulary + payload helpers).
    """
    from daemon.repositories.event.models import Event, EventKind
    from daemon.repositories.room.repository import (
        MemberNotFoundError,
        RoomNotFoundError,
        member_payload,
    )

    now = datetime.now(timezone.utc)
    with Session(repo._engine) as session:
        room = session.get(ChatRoom, room_id)
        if room is None:
            raise RoomNotFoundError(
                f"room={room_id} not found (absent or purged)"
            )
        member = session.get(ChatRoomMember, (room_id, instance_id))
        if member is None:
            raise MemberNotFoundError(
                f"no membership row for room={room_id} "
                f"instance={instance_id}"
            )
        if member.role == RoomMemberRole.OWNER.value:
            raise RoomLeaveOwnerError(
                f"room={room_id}: the OWNER row cannot leave (retired "
                f"only with the room, D2)"
            )
        payload = member_payload(member)
        already_left = member.state == RoomMemberState.LEFT.value
        if not already_left:
            member.state = RoomMemberState.LEFT.value
            session.add(
                Event(
                    instance_id=room.owner_instance_id,
                    kind=EventKind.ROOM_LEAVE.value,
                    # Content-free: ids + per-room name only.
                    data=json.dumps(
                        {
                            "room_id": room_id,
                            "instance_id": instance_id,
                            "member_name": payload["member_name"],
                        }
                    ),
                    created_at=now,
                )
            )
            session.commit()
    payload = dict(payload)
    payload["state"] = RoomMemberState.LEFT.value
    return payload
