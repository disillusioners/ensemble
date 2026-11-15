"""The room chat service — the OFF-LOOP async facade over the repository.

Two jobs, both P1:

1. **Off-loop discipline (house constraint §4):** every repository call
   is wrapped in ``asyncio.to_thread`` — the canonical idiom
   (``instance_messaging.py:1179-1184``). NEVER call the sync
   :class:`~daemon.repositories.room.repository.RoomRepository`
   directly on the event loop.

2. **Per-room write ordering (architecture §1.3 "Seq"):** the append
   path holds a per-room ``asyncio.Lock`` from a bounded LRU registry —
   a verbatim port of the canonical per-key idiom
   (``sources/dispatcher.py:451-452`` fields, ``:810-838``
   ``_get_send_lock``), with ``MAX_ROOM_LOCKS=1000`` per the design.
   Under the lock, the repository's single-statement
   ``INSERT ... COALESCE(MAX(seq), 0) + 1`` + ``UNIQUE(room_id, seq)``
   backstop gives the per-room monotonic sequence (RK-13
   single-daemon assumption; the UNIQUE constraint is the DB-level
   belt for anything the lock cannot see).

P1 surface: room birth, append, cursor advance, fail-closed
reads, roster, purge, boot-regen derivation.

P2 surface (membership/spawn/naming): ``validate_room_join`` (the
pinned 5-conjunct spawn-time admission), ``join_room`` (the member-add
surface under the per-room lock — suffix registry), ``leave_room``
(owner exempt), ``restore_eligibility`` (the D5 two-conjunct rule),
and the public ``get_room_lock`` (the spawn tool holds it across the
spawn call so the tier-4 suffix registry's read→insert — inside the
spawn transaction — cannot interleave with a concurrent same-room
join). Tag resolution / routing / nudges are P3–P5 and are
deliberately absent.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from sqlalchemy.engine import Engine

from daemon.repositories.room.membership import ROLE_CHILD
from daemon.repositories.room.models import RoomMessageKind
from daemon.repositories.room.repository import (
    MemberNameCollisionError,
    MemberNotFoundError,
    RoomClosedError,
    RoomError,
    RoomJoinError,
    RoomJoinCrossTreeError,
    RoomJoinMemberCapError,
    RoomJoinRoomCapError,
    RoomJoinRoomStateError,
    RoomJoinUnknownRoomError,
    RoomLeaveOwnerError,
    RoomNotFoundError,
    RoomRepository,
    RoomSeqConflictError,
)

__all__ = [
    "RoomChatService",
    "MAX_ROOM_LOCKS",
    # re-exported typed vocabulary — callers of the service never need
    # to import the repository module.
    "RoomError",
    "RoomNotFoundError",
    "RoomClosedError",
    "MemberNotFoundError",
    "MemberNameCollisionError",
    "RoomSeqConflictError",
    # P2 join/leave vocabulary
    "RoomJoinError",
    "RoomJoinUnknownRoomError",
    "RoomJoinRoomStateError",
    "RoomJoinCrossTreeError",
    "RoomJoinMemberCapError",
    "RoomJoinRoomCapError",
    "RoomLeaveOwnerError",
]

# Architecture §1.3: MAX_ROOM_LOCKS=1000 (the dispatcher's
# MAX_SEND_LOCKS=10000 scaled to the room population).
MAX_ROOM_LOCKS = 1000

logger = logging.getLogger(__name__)


class RoomChatService:
    """Async facade: LRU per-room locks + ``asyncio.to_thread`` wrapping."""

    def __init__(self, engine: Engine) -> None:
        self._repo = RoomRepository(engine)
        # Port of sources/dispatcher.py:451-452 — bounded LRU lock
        # registry + the double-check guard.
        self._room_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._locks_guard = asyncio.Lock()

    async def get_room_lock(self, room_id: str) -> asyncio.Lock:
        """Get or create the per-room write lock (dispatcher :810-838 port).

        Double-check locking under the guard; LRU move-to-end on hit;
        oldest-eviction at capacity so the registry cannot leak.

        Public since P2: the spawn tool holds this lock ACROSS the
        spawn call when joining a room, so the tier-4 suffix
        registry's read→insert (inside ``_spawn_instance_db_sync``'s
        transaction) cannot interleave with a concurrent same-room
        join. The ``UNIQUE(room_id, member_name)`` backstop remains
        the DB-level belt for anything the lock cannot see.
        """
        async with self._locks_guard:
            if room_id in self._room_locks:
                # Move to end (most recently used).
                self._room_locks.move_to_end(room_id)
                return self._room_locks[room_id]
            # Evict oldest if at capacity.
            if len(self._room_locks) >= MAX_ROOM_LOCKS:
                oldest_id, _ = self._room_locks.popitem(last=False)
                logger.debug(
                    "Evicted room lock for inactive room: %s", oldest_id
                )
            lock = asyncio.Lock()
            self._room_locks[room_id] = lock
            return lock

    # Back-compat private alias (P1 internal callers).
    _get_room_lock = get_room_lock

    # ── room birth ─────────────────────────────────────────────────────

    async def create_room(
        self,
        *,
        name: str,
        tree_root_id: str,
        owner_instance_id: str,
        owner_member_name: str = "owner",
        owner_agent_id: str | None = None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Room + owner seed in one transaction (off-loop)."""
        return await asyncio.to_thread(
            self._repo.create_room,
            name=name,
            tree_root_id=tree_root_id,
            owner_instance_id=owner_instance_id,
            owner_member_name=owner_member_name,
            owner_agent_id=owner_agent_id,
            room_id=room_id,
        )

    # ── append — the ONLY path that takes the per-room lock ────────────

    async def append_message(
        self,
        *,
        room_id: str,
        author_instance_id: str,
        author_name: str,
        text_body: str,
        tagged_members: list[str] | None = None,
        addressed_members: list[str] | None = None,
        kind: str = RoomMessageKind.MESSAGE.value,
    ) -> dict[str, Any]:
        """Append under the per-room lock (off-loop).

        Lock scope: the ENTIRE append (room-state gate + seq INSERT +
        counters bump). The lock serializes writers within the daemon;
        ``UNIQUE(room_id, seq)`` backstops anything else.
        """
        lock = await self._get_room_lock(room_id)
        async with lock:
            return await asyncio.to_thread(
                self._repo.append_message,
                room_id=room_id,
                author_instance_id=author_instance_id,
                author_name=author_name,
                text_body=text_body,
                tagged_members=tagged_members,
                addressed_members=addressed_members,
                kind=kind,
            )

    # ── membership (P2) ────────────────────────────────────────────────

    async def validate_room_join(
        self, *, room_id: str, joiner_tree_root: str | None
    ) -> dict[str, Any]:
        """Spawn-time admission predicate (off-loop).

        5 conjuncts, one distinct typed :class:`RoomJoinError`
        subclass per failure; the room payload on success. The spawn
        tool calls this BEFORE any instance row is written, so a
        refusal leaves no partial state.
        """
        return await asyncio.to_thread(
            self._repo.validate_room_join,
            room_id=room_id,
            joiner_tree_root=joiner_tree_root,
        )

    async def join_room(
        self,
        *,
        room_id: str,
        instance_id: str,
        base_name: str,
        role: str = ROLE_CHILD,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Member-add under the per-room lock (off-loop).

        The lock spans the suffix-registry read → INSERT so
        concurrent same-name joins serialize (one bare bearer +
        suffixed colliders); ``UNIQUE(room_id, member_name)`` is the
        DB backstop. NOTE: the SPAWN path inserts inside the instance
        transaction instead — the tool holds THIS lock across the
        spawn call to get the same ordering.
        """
        lock = await self.get_room_lock(room_id)
        async with lock:
            return await asyncio.to_thread(
                self._repo.join_room,
                room_id=room_id,
                instance_id=instance_id,
                base_name=base_name,
                role=role,
                agent_id=agent_id,
            )

    async def leave_room(
        self, *, room_id: str, instance_id: str
    ) -> dict[str, Any]:
        """Membership ``state → left`` (owner exempt; off-loop)."""
        return await asyncio.to_thread(
            self._repo.leave_room,
            room_id=room_id,
            instance_id=instance_id,
        )

    async def restore_eligibility(
        self, *, room_id: str, instance_id: str
    ) -> dict[str, Any]:
        """D5 two-conjunct restore predicate (off-loop)."""
        return await asyncio.to_thread(
            self._repo.restore_eligibility,
            room_id=room_id,
            instance_id=instance_id,
        )

    # ── cursors ────────────────────────────────────────────────────────

    async def advance_read_cursor(
        self, room_id: str, instance_id: str, seq: int
    ) -> bool:
        return await asyncio.to_thread(
            self._repo.advance_read_cursor, room_id, instance_id, seq
        )

    async def advance_delivered_cursor(
        self, room_id: str, instance_id: str, seq: int
    ) -> bool:
        return await asyncio.to_thread(
            self._repo.advance_delivered_cursor, room_id, instance_id, seq
        )

    # ── reads / roster ─────────────────────────────────────────────────

    async def get_room(self, room_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._repo.get_room, room_id)

    async def get_member(
        self, room_id: str, instance_id: str
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._repo.get_member, room_id, instance_id
        )

    async def list_members(
        self, room_id: str, *, caller_instance_id: str, active_only: bool = True
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._repo.list_members,
            room_id,
            caller_instance_id=caller_instance_id,
            active_only=active_only,
        )

    async def read_messages(
        self,
        room_id: str,
        *,
        caller_instance_id: str,
        after_seq: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._repo.list_messages,
            room_id,
            caller_instance_id=caller_instance_id,
            after_seq=after_seq,
            limit=limit,
        )

    async def unread_count(
        self, room_id: str, *, caller_instance_id: str
    ) -> int:
        return await asyncio.to_thread(
            self._repo.unread_count,
            room_id,
            caller_instance_id=caller_instance_id,
        )

    # ── purge ──────────────────────────────────────────────────────────

    async def purge_room(self, room_id: str, *, reason: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._repo.purge_room, room_id, reason=reason
        )

    # ── boot regeneration snapshot (pure derivation) ───────────────────

    async def derive_undelivered_tagged(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._repo.derive_undelivered_tagged)
