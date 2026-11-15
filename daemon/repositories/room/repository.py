"""The room repository — engine-passed sync CRUD over the chat substrate.

Layer discipline (the POK / memoir precedent): this module NEVER builds
an engine and NEVER reads an ambient ``POSTGRES_*`` variable — the
repository is a thin wrapper around the CALLER'S engine
(``manager._engine``). Tests use a throwaway file-backed SQLite engine;
no ``InstanceManager`` is ever constructed.

What lives here (mechanics only, no policy):

* ``create_room`` — the transactional room birth: the ``chatrooms`` row
  + the OWNER membership row (``role=owner``, both cursors 0 — D2) in
  ONE commit.
* ``append_message`` — the per-room monotonic-seq append. The seq is
  assigned by a SINGLE-STATEMENT INSERT
  (``INSERT ... SELECT COALESCE(MAX(seq), 0) + 1 ...``) so no read-max
  race exists inside the statement; ``UNIQUE(room_id, seq)`` is the
  DB-level backstop. Callers MUST hold the per-room lock from
  ``daemon/repositories/room/service.py`` — the lock is the
  single-daemon write-ordering primitive (architecture §1.3 "Seq").
* ``advance_read_cursor`` / ``advance_delivered_cursor`` — the two
  monotonic cursors (MAX guards via ``WHERE cursor < :seq``).
* ``read_messages`` / ``unread_count`` — room-scoped reads behind the
  FAIL-CLOSED predicate: active member (owner included — the seeded
  row) ∪ owner-ancestry chain. A LEFT member and a non-member both get
  the typed NOT-FOUND (the room "does not exist" for them — no
  existence leak). Purged rooms have no rows ⇒ the same typed
  not-found (R2: purged = nonexistent).
* ``purge_room`` — the R2 transaction: ONE content-free ``room_purged``
  event row (ids + counts only) inserted in the SAME transaction, then
  ``room_messages`` → ``chatroom_members`` → ``chatrooms`` deletes —
  commit or roll back atomically. NO archive tables, NO tombstone.
* ``derive_undelivered_tagged`` — the boot regeneration snapshot
  (P1): TAGGED rows with
  ``seq > max(last_read_seq, last_delivered_seq)`` for ACTIVE members
  INCLUDING THE OWNER, across rooms in ``active | closed`` (purged
  rooms have no rows). PURE re-derivation — it routes, wakes, and
  revives NOTHING (the pinned pair, architecture §10).

Policy (caps, admission, tag/address resolution, lane routing, nudges)
lives in the later phases (P2–P5); this file deliberately has none.

Event-loop discipline: every method here is SYNC and blocks its
thread. Async callers MUST go through
``daemon/repositories/room/service.py`` which wraps each call in
``asyncio.to_thread`` (the canonical off-loop idiom — exemplar
``instance_messaging.py:1179-1184``). NEVER call these methods
directly on the event loop (house constraint §4, v0.27.14-class).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import bindparam, text as sql_text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.instance.models import Instance
from daemon.repositories.room import membership as _membership
from daemon.repositories.room.models import (
    ChatRoom,
    ChatRoomMember,
    RoomMemberRole,
    RoomMemberState,
    RoomMessage,
    RoomMessageKind,
    RoomState,
)
from daemon.repositories.infra.types import JSONBType

__all__ = [
    "RoomError",
    "RoomNotFoundError",
    "RoomClosedError",
    "MemberNotFoundError",
    "MemberNameCollisionError",
    "RoomSeqConflictError",
    "RoomJoinError",
    "RoomJoinUnknownRoomError",
    "RoomJoinRoomStateError",
    "RoomJoinCrossTreeError",
    "RoomJoinMemberCapError",
    "RoomJoinRoomCapError",
    "RoomLeaveOwnerError",
    "RoomRepository",
]

# Re-exported join/leave vocabulary (membership module owns the
# definitions; the repository is the callers' one-stop import). This
# binding MUST precede the MemberNameCollisionError class body below —
# the dual-family collision (RoomError + RoomJoinError) consumes the
# name at class-creation time.
RoomJoinError = _membership.RoomJoinError
RoomJoinUnknownRoomError = _membership.RoomJoinUnknownRoomError
RoomJoinRoomStateError = _membership.RoomJoinRoomStateError
RoomJoinCrossTreeError = _membership.RoomJoinCrossTreeError
RoomJoinMemberCapError = _membership.RoomJoinMemberCapError
RoomJoinRoomCapError = _membership.RoomJoinRoomCapError
RoomLeaveOwnerError = _membership.RoomLeaveOwnerError

logger = logging.getLogger(__name__)

# Ancestry-walk bound — mirrors ``instance/repository.py``'s
# ``_MAX_TRAVERSAL_DEPTH`` guard shape (fail-closed on pathological
# parent cycles: the walk gives up and the caller is simply not in the
# chain ⇒ typed not-found).
_ANCESTRY_WALK_DEPTH = 256


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_room_id() -> str:
    return str(uuid.uuid4())


# ── Typed errors (fail-closed vocabulary) ─────────────────────────────────


class RoomError(Exception):
    """Base class for every typed room-repository failure."""


class RoomNotFoundError(RoomError):
    """The room does not exist for THIS caller.

    Raised for: a genuinely absent room_id, a PURGED room (R2 — purged
    = nonexistent, no tombstone), and — fail-closed — a room that
    exists but that the caller cannot see (LEFT member / non-member:
    the read predicate reports not-found rather than access-denied so
    room existence never leaks across the boundary).
    """


class RoomClosedError(RoomError):
    """The room exists and is readable, but its state is ``closed`` —
    posts are refused (capability row: closed = read-only)."""


class MemberNotFoundError(RoomError):
    """No membership row for (room_id, instance_id)."""


class MemberNameCollisionError(RoomError, RoomJoinError):
    """``UNIQUE(room_id, member_name)`` refused the insert — the
    per-room naming-registry backstop (P2 owns the suffix registry;
    P1 only surfaces the typed refusal).

    DUAL FAMILY, deliberate: it stays a :class:`RoomError` (this
    module's typed repository vocabulary) AND becomes a
    :class:`~daemon.repositories.room.membership.RoomJoinError`
    because it is semantically a JOIN failure — the spawn aborted
    because the room could not admit the member under its name. That
    routes it into the spawn tool's room-join handler, which matches
    it BEFORE the 5 admission-refusal texts and surfaces the DISTINCT
    commit-time message (catch-site audit 2026-11-15: the only
    ``except RoomJoinError`` site is the spawn tool; nothing catches
    ``RoomError``/``MemberNameCollisionError`` directly, so the only
    behavioral delta is the intended one).
    """


class RoomSeqConflictError(RoomError):
    """``UNIQUE(room_id, seq)`` refused the append. The per-room lock
    makes this unreachable within one daemon (RK-13 single-writer); it
    is the DB-level backstop, never expected, always surfaced."""


def is_room_member_integrity(exc: BaseException) -> bool:
    """Non-raising fingerprint probe — True iff the IntegrityError is
    the room member-name ``UNIQUE(room_id, member_name)`` fire (the
    ONLY room-table write the spawn commit makes).

    The lifecycle's spawn-commit re-typing GATES on this: a matching
    error re-types to :class:`MemberNameCollisionError`; every other
    IntegrityError (instances-PK, hierarchy, anything non-room)
    propagates UN-RE-TYPED. The fingerprints are the SAME strings the
    raising classifier below matches — one source, extracted here so
    the gate can probe without raising.
    """
    msg = str(exc).lower()
    return (
        "uq_chatroom_members_room_member_name" in msg
        or (
            "chatroom_members.room_id, chatroom_members.member_name"
            in msg
        )
    )


def raise_typed_member_integrity(
    exc: IntegrityError, room_id: str, member_name: str
) -> None:
    """Classify a membership-write IntegrityError → typed failure.

    Module-level (public) so cross-module callers — the lifecycle's
    spawn commit — classify without reaching into the repository
    class. The staticmethod ``RoomRepository._raise_typed_integrity``
    remains as a thin delegating wrapper over this function.
    """
    # Constraint FINGERPRINTS ONLY (the shared probe above is the
    # single source): the named UNIQUE constraint (PG / name-reporting
    # drivers) or the other driver's dotted-pair column message. A
    # bare ``member_name`` substring over-matches — a composite-PK
    # duplicate or any other violation must fall through to the base
    # RoomError, never the name-collision type.
    if is_room_member_integrity(exc):
        raise MemberNameCollisionError(
            f"member_name={member_name!r} already registered in "
            f"room={room_id} (UNIQUE(room_id, member_name) backstop)"
        ) from exc
    raise RoomError(
        f"room write integrity failure for room={room_id}: {exc}"
    ) from exc


# ── The single-statement seq-assigning append (module-level, built ONCE) ──
#
# ``INSERT ... SELECT COALESCE(MAX(seq), 0) + 1 ...`` so no read-max
# race exists inside the statement; ``UNIQUE(room_id, seq)`` is the
# DB-level backstop. RETURNING is supported by PG and by SQLite
# ≥ 3.35 — the house runner already gates on sqlite>=3.35. The JSON
# params carry the PYTHON LIST bound via the house dialect-aware
# JSONBType — a raw json.dumps string binds as VARCHAR and PG refuses
# it (JSONB ≠ varchar); SQLite's JSON branch serializes the same
# list. (Driver fork caught by the PG proof run.) Per-call state
# travels only in the params dict at ``session.execute``.
_APPEND_MESSAGE_STMT = sql_text(
    "INSERT INTO room_messages ("
    "  room_id, seq, author_instance_id, author_name,"
    "  text, tagged_members, addressed_members, kind,"
    "  source_message_id, trigger, member,"
    "  created_at"
    ") SELECT "
    "  :room_id,"
    "  COALESCE(MAX(seq), 0) + 1,"
    "  :author_instance_id,"
    "  :author_name,"
    "  :text_body,"
    "  :tagged_members,"
    "  :addressed_members,"
    "  :kind,"
    "  :source_message_id,"
    "  :trigger,"
    "  :member,"
    "  :created_at"
    " FROM room_messages WHERE room_messages.room_id = :room_id"
    " RETURNING id, seq"
).bindparams(
    bindparam("room_id"),
    bindparam("author_instance_id"),
    bindparam("author_name"),
    bindparam("text_body"),
    bindparam("tagged_members", type_=JSONBType),
    bindparam("addressed_members", type_=JSONBType),
    bindparam("kind"),
    bindparam("source_message_id"),
    bindparam("trigger"),
    bindparam("member"),
    bindparam("created_at"),
)


# ── Detached payload shapes ────────────────────────────────────────────────


def room_payload(room: ChatRoom) -> dict[str, Any]:
    return {
        "room_id": room.room_id,
        "name": room.name,
        "tree_root_id": room.tree_root_id,
        "owner_instance_id": room.owner_instance_id,
        "state": room.state,
        "closed_at": room.closed_at,
        "last_post_at": room.last_post_at,
        "message_count": room.message_count,
        "created_at": room.created_at,
        "updated_at": room.updated_at,
    }


def member_payload(member: ChatRoomMember) -> dict[str, Any]:
    return {
        "room_id": member.room_id,
        "instance_id": member.instance_id,
        "member_name": member.member_name,
        "agent_id": member.agent_id,
        "role": member.role,
        "joined_at": member.joined_at,
        "last_read_seq": member.last_read_seq,
        "last_delivered_seq": member.last_delivered_seq,
        "state": member.state,
    }


def _message_payload(message: RoomMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "room_id": message.room_id,
        "seq": message.seq,
        "author_instance_id": message.author_instance_id,
        "author_name": message.author_name,
        "text": message.text,
        "tagged_members": list(message.tagged_members or []),
        "addressed_members": list(message.addressed_members or []),
        "kind": message.kind,
        "source_message_id": message.source_message_id,
        "trigger": message.trigger,
        "member": message.member,
        "created_at": message.created_at,
    }


class RoomRepository:
    """Sync CRUD + the room transactions over the caller's engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ── room birth (a: chatrooms row + OWNER membership row, one tx) ──

    def create_room(
        self,
        *,
        name: str,
        tree_root_id: str,
        owner_instance_id: str,
        owner_member_name: str = "owner",
        owner_agent_id: str | None = None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Create the room AND seed the owner membership row (D2).

        ONE transaction: the ``chatrooms`` row + the creator's
        ``chatroom_members`` row (``role=owner``, ``state=active``,
        both cursors 0, ``member_name`` per the naming registry —
        the caller resolves it; P1's default is ``"owner"``). The
        owner row is retired only WITH the room.

        Returns the detached ``{"room": ..., "owner_member": ...}``
        payload pair. Raises :class:`MemberNameCollisionError` when
        ``UNIQUE(room_id, member_name)`` refuses the seed (P2's
        suffix registry handles the retry; P1 surfaces the typed
        refusal only).
        """
        rid = room_id or _new_room_id()
        now = _utc_now_iso()
        # D2 / naming-cascade tier 4: the owner's member_name goes
        # through the SAME per-room registry as every other member
        # (P2's resolve_member_name). A fresh room has zero registered
        # names, so the caller-provided name survives bare — the
        # registry call is the shared-mechanic pin, not a behavior
        # change (it becomes load-bearing if room-create is ever
        # re-entered for an existing room_id).
        owner_member_name = _membership.resolve_member_name(
            set(), owner_member_name
        )
        room = ChatRoom(
            room_id=rid,
            name=name,
            tree_root_id=tree_root_id,
            owner_instance_id=owner_instance_id,
            state=RoomState.ACTIVE.value,
            closed_at=None,
            last_post_at=None,
            message_count=0,
            created_at=now,
            updated_at=now,
        )
        owner_row = ChatRoomMember(
            room_id=rid,
            instance_id=owner_instance_id,
            member_name=owner_member_name,
            agent_id=owner_agent_id,
            role=RoomMemberRole.OWNER.value,
            joined_at=now,
            last_read_seq=0,
            last_delivered_seq=0,
            state=RoomMemberState.ACTIVE.value,
        )
        # Detached payloads FIRST — every field is client-set (nothing
        # server-generated), so the snapshot is exact; after
        # ``commit()`` the ORM instances expire and reading them
        # detached would raise DetachedInstanceError (the board
        # service's expunge discipline, adapted to snapshot form).
        room_snap = room_payload(room)
        member_snap = member_payload(owner_row)
        try:
            with Session(self._engine) as session:
                session.add(room)
                session.add(owner_row)
                session.commit()
        except IntegrityError as exc:
            self._raise_typed_integrity(exc, rid, owner_member_name)
        return {
            "room": room_snap,
            "owner_member": member_snap,
        }

    @staticmethod
    def _raise_typed_integrity(
        exc: IntegrityError, room_id: str, member_name: str
    ) -> None:
        # Thin delegating wrapper over the module-level
        # ``raise_typed_member_integrity`` (kept: tests/other callers
        # reference the class attribute).
        raise_typed_member_integrity(exc, room_id, member_name)

    @staticmethod
    def _raise_typed_append_integrity(
        exc: IntegrityError, room_id: str
    ) -> None:
        """Append-path classification — the same fingerprints-only
        discipline as :meth:`_raise_typed_integrity`: ONLY the
        ``UNIQUE(room_id, seq)`` fingerprints (named constraint /
        dotted-pair columns) map to :class:`RoomSeqConflictError`; an
        FK violation or any other integrity failure falls back to the
        base :class:`RoomError` — never mis-typed as a seq conflict."""
        msg = str(exc).lower()
        if (
            "uq_room_messages_room_seq" in msg
            or "room_messages.room_id, room_messages.seq" in msg
        ):
            raise RoomSeqConflictError(
                f"append for room={room_id} hit UNIQUE(room_id, seq): {exc}"
            ) from exc
        raise RoomError(
            f"append integrity failure for room={room_id}: {exc}"
        ) from exc

    # ── reads (room / member lookups) ──────────────────────────────────

    def get_room(self, room_id: str) -> dict[str, Any] | None:
        """Room payload or None — existence WITHOUT any ACL predicate."""
        with Session(self._engine) as session:
            room = session.get(ChatRoom, room_id)
            return room_payload(room) if room is not None else None

    def get_member(
        self, room_id: str, instance_id: str
    ) -> dict[str, Any] | None:
        with Session(self._engine) as session:
            row = session.get(ChatRoomMember, (room_id, instance_id))
            return member_payload(row) if row is not None else None

    def list_members(
        self,
        room_id: str,
        *,
        caller_instance_id: str,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Roster read — behind the SAME fail-closed read predicate."""
        self._require_room_access(room_id, caller_instance_id)
        stmt = select(ChatRoomMember).where(
            ChatRoomMember.room_id == room_id
        )
        if active_only:
            stmt = stmt.where(ChatRoomMember.state == RoomMemberState.ACTIVE.value)
        stmt = stmt.order_by(ChatRoomMember.joined_at)
        with Session(self._engine) as session:
            return [
                member_payload(m) for m in session.exec(stmt).all()
            ]

    # ── the fail-closed read predicate (d) ─────────────────────────────

    def _owner_ancestry_chain(self, session: Session, room: ChatRoom) -> set[str]:
        """The room owner + every ancestor via ``instances.parent_id``.

        Walk bound ``_ANCESTRY_WALK_DEPTH`` (fail-closed on cycles —
        the walk stops and returns what it has; a caller outside the
        collected chain is simply denied). The owner id anchors the
        chain even if its instance row is gone.
        """
        chain: set[str] = {room.owner_instance_id}
        current: str | None = room.owner_instance_id
        for _ in range(_ANCESTRY_WALK_DEPTH):
            if current is None:
                break
            row = session.get(Instance, current)
            if row is None:
                break
            parent = getattr(row, "parent_id", None)
            if parent is None or parent in chain:
                break
            chain.add(parent)
            current = parent
        return chain

    def _require_room_access(
        self, room_id: str, caller_instance_id: str
    ) -> dict[str, Any]:
        """THE read predicate — active member ∪ owner-ancestry, fail-closed.

        Resolution order (all miss ⇒ typed :class:`RoomNotFoundError`):
        1. room row exists? (no row ⇒ not-found — covers purged, R2)
        2. caller has a membership row with ``state=active``? (the
           owner's seeded row satisfies this — D2)
        3. caller ∈ owner-ancestry chain (owner included)?
        A LEFT membership row does NOT satisfy (2) and does NOT
        short-circuit (3) — a left member who is ALSO an ancestor is
        still ancestor-readable (the chain is scope, membership is
        roster; the two predicates are independent).
        """
        with Session(self._engine) as session:
            room = session.get(ChatRoom, room_id)
            if room is None:
                raise RoomNotFoundError(
                    f"room={room_id} not found (absent or purged — R2: "
                    f"purged rooms are nonexistent)"
                )
            member = session.get(ChatRoomMember, (room_id, caller_instance_id))
            if member is not None and member.state == RoomMemberState.ACTIVE.value:
                return room_payload(room)
            chain = self._owner_ancestry_chain(session, room)
            if caller_instance_id in chain:
                return room_payload(room)
            raise RoomNotFoundError(
                f"room={room_id} not found for caller={caller_instance_id} "
                f"(fail-closed: not an active member and not in the "
                f"owner-ancestry chain)"
            )

    # ── membership (P2): admission, join, leave, restore ────────────────

    def validate_room_join(
        self, *, room_id: str, joiner_tree_root: str | None
    ) -> dict[str, Any]:
        """THE pinned spawn-time admission predicate (P2).

        Thin delegation to
        :func:`daemon.repositories.room.membership.validate_room_join`
        — 5 conjuncts, one distinct typed :class:`RoomJoinError`
        subclass per failure, room payload on success. Sync; async
        callers go through the service's ``asyncio.to_thread`` wrap.
        """
        return _membership.validate_room_join(
            self._engine, room_id=room_id, joiner_tree_root=joiner_tree_root
        )

    def join_room(
        self,
        *,
        room_id: str,
        instance_id: str,
        base_name: str,
        role: str = RoomMemberRole.CHILD.value,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """The member-add public surface (tester F1 gap, closed).

        ONE transaction: read the room's registered names (ALL rows —
        LEFT included), run the tier-4 suffix registry
        (:func:`membership.resolve_member_name` — bare for the first
        bearer, smallest-free ``-N`` for colliders), INSERT the
        membership row. Caller MUST hold the per-room lock (the
        service's ``join_room`` takes it); ``UNIQUE(room_id,
        member_name)`` is the DB backstop — its fire surfaces as
        :class:`MemberNameCollisionError` via the same
        fingerprint-only classifier as the create path.

        NOTE: the SPAWN path does NOT use this method — it calls
        :func:`membership.join_room_in_session` inside
        ``_spawn_instance_db_sync``'s transaction so the membership
        row and the instance JSONB keys commit atomically. This
        method is the standalone surface (tests, future late-join).
        """
        try:
            with Session(self._engine) as session:
                row = _membership.join_room_in_session(
                    session,
                    room_id=room_id,
                    instance_id=instance_id,
                    base_name=base_name,
                    role=role,
                    agent_id=agent_id,
                )
                payload = member_payload(row)
                session.commit()
        except IntegrityError as exc:
            self._raise_typed_integrity(exc, room_id, base_name)
        return payload

    def leave_room(self, *, room_id: str, instance_id: str) -> dict[str, Any]:
        """Membership ``state → left`` (owner exempt — D2).

        The row is NEVER deleted (roster history + cursor substrate);
        its NAME stays registered (the UNIQUE backstop spans LEFT
        rows); LEFT rows are FREE vs ``ROOM_MAX_MEMBERS``. Emits ONE
        content-free ``room_leave`` audit event. Raises
        :class:`RoomNotFoundError` / :class:`MemberNotFoundError` /
        :class:`RoomLeaveOwnerError`.
        """
        return _membership.leave_room(
            self, room_id=room_id, instance_id=instance_id
        )

    def restore_eligibility(
        self, *, room_id: str, instance_id: str
    ) -> dict[str, Any]:
        """The D5 two-conjunct restore rule as a predicate payload.

        Eligible iff ``room.state ∈ {active, closed}`` AND
        ``member.state = active``; a LEFT member's restore grants
        nothing (the pin); purged/absent room → ``room_not_found``.
        The TOOL-grant side of restore is P5 — this predicate is the
        P2 substrate it will consult.
        """
        return _membership.restore_eligibility(
            self._engine, room_id=room_id, instance_id=instance_id
        )

    # ── message append (b) ─────────────────────────────────────────────

    def append_message(
        self,
        *,
        room_id: str,
        author_instance_id: str,
        author_name: str,
        text_body: str,
        tagged_members: list[str] | None = None,
        addressed_members: list[str] | None = None,
        kind: str = RoomMessageKind.MESSAGE.value,
        source_message_id: int | None = None,
        trigger: str | None = None,
        member: str | None = None,
    ) -> dict[str, Any]:
        """Append one message — per-room monotonic seq, single statement.

        The caller MUST hold the per-room lock
        (``daemon/repositories/room/service.py``). The seq is computed
        INSIDE the INSERT statement
        (``SELECT COALESCE(MAX(seq), 0) + 1 FROM room_messages WHERE
        room_id = :room_id``) so the statement is atomic on both
        drivers; ``UNIQUE(room_id, seq)`` is the backstop
        (:class:`RoomSeqConflictError` if it ever fires).

        Room-state gate (capability row): active ⇒ append; closed ⇒
        :class:`RoomClosedError`; absent/purged ⇒
        :class:`RoomNotFoundError`. The append ALSO bumps the room's
        denormalized ``message_count`` + ``last_post_at`` in the same
        transaction.
        """
        now = _utc_now_iso()
        try:
            with Session(self._engine) as session:
                room = session.get(ChatRoom, room_id)
                if room is None:
                    raise RoomNotFoundError(
                        f"room={room_id} not found (absent or purged)"
                    )
                if room.state != RoomState.ACTIVE.value:
                    raise RoomClosedError(
                        f"room={room_id} is closed — posts are refused "
                        f"(capability row: closed = read-only)"
                    )
                # The typed binds live on the module-level statement —
                # the JSONBType'd list params are the load-bearing part
                # (PG JSONB ≠ varchar; see _APPEND_MESSAGE_STMT).
                result = session.execute(_APPEND_MESSAGE_STMT, {
                    "room_id": room_id,
                    "author_instance_id": author_instance_id,
                    "author_name": author_name,
                    "text_body": text_body,
                    "tagged_members": list(tagged_members or []),
                    "addressed_members": list(addressed_members or []),
                    "kind": kind,
                    "source_message_id": source_message_id,
                    "trigger": trigger,
                    "member": member,
                    "created_at": now,
                }).first()
                if result is None:
                    raise RoomSeqConflictError(
                        f"append for room={room_id} returned no row — "
                        f"UNIQUE(room_id, seq) backstop fired"
                    )
                new_id, new_seq = int(result[0]), int(result[1])
                self._bump_room_counters(session, room_id, now)
                session.commit()
        except IntegrityError as exc:
            self._raise_typed_append_integrity(exc, room_id)
        return {
            "id": new_id,
            "room_id": room_id,
            "seq": new_seq,
            "author_instance_id": author_instance_id,
            "author_name": author_name,
            "text": text_body,
            "tagged_members": list(tagged_members or []),
            "addressed_members": list(addressed_members or []),
            "kind": kind,
            "source_message_id": source_message_id,
            "trigger": trigger,
            "member": member,
            "created_at": now,
        }

    @staticmethod
    def _bump_room_counters(session: Session, room_id: str, now: str) -> None:
        """Same-transaction denormalized counters bump (no drift by
        construction) — the append's ``chatrooms`` UPDATE, extracted
        so the append body reads as gate → INSERT → bump → commit."""
        session.execute(
            sql_text(
                "UPDATE chatrooms SET message_count = message_count + 1,"
                " last_post_at = :now, updated_at = :now"
                " WHERE room_id = :room_id"
            ).bindparams(now=now, room_id=room_id)
        )

    def list_messages(
        self,
        room_id: str,
        *,
        caller_instance_id: str,
        after_seq: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Room log read (seq-ordered) behind the fail-closed predicate.

        State-aware per the capability row: ``active`` and ``closed``
        both read; purged/absent resolves typed not-found.
        """
        # Entry bounds guard (house precedent: shared_meta_kv P0-1) —
        # reject before ANY DB work; the service pass-through is
        # covered by the same guard.
        if limit <= 0:
            raise ValueError(f"limit must be positive: {limit}")
        self._require_room_access(room_id, caller_instance_id)
        stmt = (
            select(RoomMessage)
            .where(
                RoomMessage.room_id == room_id,
                RoomMessage.seq > after_seq,
            )
            .order_by(RoomMessage.seq)
            .limit(limit)
        )
        with Session(self._engine) as session:
            return [_message_payload(m) for m in session.exec(stmt).all()]

    def unread_count(
        self, room_id: str, *, caller_instance_id: str
    ) -> int:
        """COUNT(*) WHERE seq > last_read_seq — behind the predicate.

        Closed rooms still compute unread (capability row); purged
        rooms resolve typed not-found (no rows — nothing to count).
        """
        self._require_room_access(room_id, caller_instance_id)
        member = self.get_member(room_id, caller_instance_id)
        if member is None or member["state"] != RoomMemberState.ACTIVE.value:
            # Non-member ancestors (owner-ancestry read scope) have no
            # cursor row — unread is a MEMBER concept (the digest is
            # per-participant). Reports 0 for pure-ancestry callers.
            return 0
        stmt = sql_text(
            "SELECT COUNT(*) FROM room_messages"
            " WHERE room_id = :room_id AND seq > :cursor"
        ).bindparams(room_id=room_id, cursor=int(member["last_read_seq"]))
        with Session(self._engine) as session:
            return int(session.execute(stmt).scalar())

    # ── cursor advance (c) — monotonic MAX guards ──────────────────────

    def advance_read_cursor(
        self, room_id: str, instance_id: str, seq: int
    ) -> bool:
        """Advance ``last_read_seq`` — monotonic (never decreases).

        ``WHERE last_read_seq < :seq`` IS the MAX guard: the UPDATE
        only fires when it advances. Returns True iff it advanced.
        """
        return self._advance_cursor(
            room_id, instance_id, "last_read_seq", seq
        )

    def advance_delivered_cursor(
        self, room_id: str, instance_id: str, seq: int
    ) -> bool:
        """Advance ``last_delivered_seq`` — monotonic (never decreases)."""
        return self._advance_cursor(
            room_id, instance_id, "last_delivered_seq", seq
        )

    def _advance_cursor(
        self,
        room_id: str,
        instance_id: str,
        column: Literal["last_read_seq", "last_delivered_seq"],
        seq: int,
    ) -> bool:
        if column not in ("last_read_seq", "last_delivered_seq"):
            raise ValueError(f"unknown cursor column: {column}")
        stmt = sql_text(
            f"UPDATE chatroom_members SET {column} = :seq"
            " WHERE room_id = :room_id AND instance_id = :instance_id"
            f" AND {column} < :seq"
        ).bindparams(seq=int(seq), room_id=room_id, instance_id=instance_id)
        with Session(self._engine) as session:
            result = session.execute(stmt)
            session.commit()
            return bool(result.rowcount and result.rowcount > 0)

    # ── purge (f) — R2: event + deletes in ONE transaction ─────────────

    def purge_room(self, room_id: str, *, reason: str) -> dict[str, Any]:
        """Purge the room from the database (R2 — literal DB cleanup).

        ONE transaction per room:
        1. count the rows about to die (messages, members),
        2. insert ONE content-free ``room_purged`` event row (ids +
           counts ONLY — no message content; ``instance_id`` = the
           room owner so the trace rides the owner's event stream),
        3. DELETE ``room_messages`` → ``chatroom_members`` →
           ``chatrooms`` (child-first so the NO ACTION FKs allow it).

        Commit or roll back atomically. NO archive tables, NO
        tombstone — afterwards the room is nonexistent: every read
        resolves typed :class:`RoomNotFoundError`.
        """
        now = _utc_now_iso()
        try:
            with Session(self._engine) as session:
                room = session.get(ChatRoom, room_id)
                if room is None:
                    raise RoomNotFoundError(
                        f"room={room_id} not found (absent or already purged)"
                    )
                message_count = int(
                    session.execute(
                        sql_text(
                            "SELECT COUNT(*) FROM room_messages WHERE room_id = :r"
                        ).bindparams(r=room_id)
                    ).scalar()
                )
                member_count = int(
                    session.execute(
                        sql_text(
                            "SELECT COUNT(*) FROM chatroom_members WHERE room_id = :r"
                        ).bindparams(r=room_id)
                    ).scalar()
                )
                # (2) the ONLY trace — content-free, ids + counts only.
                session.add(
                    Event(
                        instance_id=room.owner_instance_id,
                        kind=EventKind.ROOM_PURGED.value,
                        data=json.dumps(
                            {
                                "room_id": room_id,
                                "owner_instance_id": room.owner_instance_id,
                                "tree_root_id": room.tree_root_id,
                                "messages_purged": message_count,
                                "members_purged": member_count,
                            }
                        ),
                        created_at=datetime.now(timezone.utc),
                    )
                )
                # (3) child-first — the NO ACTION FKs demand it.
                session.execute(
                    sql_text(
                        "DELETE FROM room_messages WHERE room_id = :r"
                    ).bindparams(r=room_id)
                )
                session.execute(
                    sql_text(
                        "DELETE FROM chatroom_members WHERE room_id = :r"
                    ).bindparams(r=room_id)
                )
                session.execute(
                    sql_text(
                        "DELETE FROM chatrooms WHERE room_id = :r"
                    ).bindparams(r=room_id)
                )
                session.commit()
        except IntegrityError as exc:
            # Uniform typed wrap (create/append precedent): the purge
            # is child-first so an integrity failure here is abnormal —
            # surface it typed, never raw.
            raise RoomError(
                f"purge integrity failure for room={room_id}: {exc}"
            ) from exc
        logger.info(
            "room purge complete: room=%s reason=%s messages=%d members=%d",
            room_id,
            reason,
            message_count,
            member_count,
        )
        return {
            "room_id": room_id,
            "messages_purged": message_count,
            "members_purged": member_count,
            "reason": reason,
            "purged_at": now,
        }

    # ── P3 delivery helpers (instances-table CAS + content-free event) ──
    #
    # These two methods are the P3 lane-(b) CAS and the content-free
    # room_* EventKind emission surface. They touch the ``instances``
    # / ``event`` tables (NOT the room tables) but are policy-free
    # mechanics owned by this repository so that ALL DB I/O stays
    # engine-passed, sync, and off-loop-wrapped by the service layer.
    # They are used ONLY by ``daemon/services/room_delivery.py``.

    def cas_instance_status_to_running(
        self, instance_id: str, allowed: frozenset[str]
    ) -> int:
        """Atomically flip ``instances.status`` to ``running`` iff it is
        currently in ``allowed`` (the derived CAS set). Returns the
        affected row count (0 or 1). The caller derives ``allowed`` from
        the 10-state enum (non-terminal minus {running, paused}) — this
        method never interprets the set.

        Used by lane (b) to defeat the enqueue's terminal auto-revival:
        by the time ``manager.enqueue_message`` runs, the instance is
        already ``running``, so its internal revive predicate never
        fires for a room-routed member.
        """
        if not allowed:
            return 0
        placeholders = ", ".join(f":s{i}" for i in range(len(allowed)))
        stmt = sql_text(
            f"UPDATE instances SET status = 'running' "
            f"WHERE instance_id = :iid AND status IN ({placeholders})"
        )
        params: dict[str, Any] = {"iid": instance_id}
        for i, v in enumerate(sorted(allowed)):
            params[f"s{i}"] = v
        with Session(self._engine) as session:
            result = session.execute(stmt.bindparams(**params))
            session.commit()
            return int(result.rowcount or 0)

    def get_instance_status(self, instance_id: str) -> str | None:
        """Read the current status of an instance (sync, engine-passed).

        Used by the delivery router for lane assignment + the rowcount-0
        fallback re-read. Returns None when the row is gone.
        """
        with Session(self._engine) as session:
            row = session.get(Instance, instance_id)
            return row.status if row is not None else None

    def emit_room_event(
        self, instance_id: str, kind: str, data: dict[str, Any]
    ) -> None:
        """Insert ONE content-free ``room_*`` EventKind row.

        Content-free contract: ``data`` carries ids/counts/outcomes only,
        never message text. ``instance_id`` is the stream the trace
        rides (the member for tag-delivery, the parent for proposals).
        """
        from daemon.repositories.event.models import Event, EventKind

        now = datetime.now(timezone.utc)
        with Session(self._engine) as session:
            session.add(
                Event(
                    instance_id=instance_id,
                    kind=kind,
                    data=__import__("json").dumps(data),
                    created_at=now,
                )
            )
            session.commit()

    # ── boot regeneration snapshot (P1 — the pinned pair, §10) ─────────

    def derive_undelivered_tagged(self) -> list[dict[str, Any]]:
        """Re-derivation snapshot: TAGGED rows above the cursor pair.

        THE PINNED PAIR (architecture §10, D4):

        1. TAGGED undelivered rows for MEMBERS — INCLUDING THE OWNER
           ROW (the owner has a seeded membership row, so the same
           join covers it) — across rooms in ``active | closed``
           (purged rooms have NO rows and are therefore structurally
           excluded; there is no "archived" state to exclude).
        2. This method ROUTES, WAKES, and REVIVES NOTHING — it is a
           pure SELECT returning the per-participant pending set.
           Lane assignment (10-state map) is P3; boot only PREPARES.

        Predicate per the design: a row needs delivery iff
        ``seq > max(last_read_seq, last_delivered_seq)`` for that
        participant — expressed driver-neutrally as ``(seq >
        last_read_seq AND seq > last_delivered_seq)`` (exceed BOTH
        cursors = exceed the max; an ``OR`` here would degrade the
        predicate to exceed-the-MIN and resurrect delivered rows —
        pinned by test_rederivation_delivered_cursor_gates). The
        TAGGED filter (tagged_members non-empty) is applied in Python
        — the JSON emptiness predicate differs across drivers and the
        pending set is bounded by room GC (RK-5).

        SEMANTICS NOTE: this is the SUBSTRATE candidate set —
        per (member, row) pairs where the row is tagged and above
        that member's cursors. TARGET resolution (which @name the row
        is actually FOR — including "not for this member at all") is
        lane-assignment territory (P3's tag resolution + the 10-state
        map); the substrate deliberately over-includes (fail-safe:
        under-inclusion would silently drop deliverables, P3 filters).
        """
        stmt = sql_text(
            "SELECT"
            " m.room_id AS room_id,"
            " m.instance_id AS instance_id,"
            " m.member_name AS member_name,"
            " m.role AS role,"
            " r.state AS room_state,"
            " msg.id AS message_id,"
            " msg.seq AS seq,"
            " msg.tagged_members AS tagged_members"
            " FROM chatroom_members m"
            " JOIN chatrooms r ON r.room_id = m.room_id"
            " JOIN room_messages msg ON msg.room_id = m.room_id"
            "  AND (msg.seq > m.last_read_seq AND msg.seq > m.last_delivered_seq)"
            " WHERE m.state = :member_active"
            "  AND r.state IN (:state_active, :state_closed)"
            " ORDER BY m.room_id, m.instance_id, msg.seq"
        ).bindparams(
            member_active=RoomMemberState.ACTIVE.value,
            state_active=RoomState.ACTIVE.value,
            state_closed=RoomState.CLOSED.value,
        )
        pending: list[dict[str, Any]] = []
        with Session(self._engine) as session:
            for row in session.execute(stmt).all():
                # JSONB arrives NATIVE (list) on PG via psycopg3; JSON
                # (TEXT) on SQLite. Handle both — json.loads on a
                # native list would TypeError into the except and
                # SILENTLY DROP the row (under-delivery).
                raw = row.tagged_members
                if isinstance(raw, (list, tuple)):
                    tagged = list(raw)
                elif isinstance(raw, (str, bytes, bytearray)):
                    try:
                        tagged = json.loads(raw or "[]")
                    except (TypeError, ValueError):
                        tagged = []
                else:
                    tagged = []
                if not tagged:
                    continue
                pending.append(
                    {
                        "room_id": row.room_id,
                        "instance_id": row.instance_id,
                        "member_name": row.member_name,
                        "role": row.role,
                        "room_state": row.room_state,
                        "message_id": row.message_id,
                        "seq": int(row.seq),
                    }
                )
        return pending
