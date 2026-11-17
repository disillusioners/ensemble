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

P6 surface (the closed-state producer + the spawn-path join): the
F-3 lifecycle notifier (``set_lifecycle_notifier``) fans every
lifecycle transition to the SSE room channel — CONTENT-FREE, best
effort, never failing the mutation. ``close_room`` is the ONLY
producer of the ``closed`` state (the forward-only ``active → closed``
move that makes the ratified P-close GC arm reachable), and
``notify_spawn_join`` is the producer for a SPAWN-time membership
join (the spawn path inserts the row inside the spawn transaction, so
it never rides ``join_room``).
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
    RoomNotOwnerError,
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
    "RoomNotOwnerError",
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
        # F-3 lifecycle notifier (A-P6-26): an OPTIONAL async callable
        # ``(room_id=..., kind=..., data=...)`` the MANAGER wires to the
        # SSE room channel (``live_hub.broadcast_room_event``) at boot.
        # Every lifecycle transition fires it BEST-EFFORT after the
        # durable commit — a notifier failure never fails the mutation.
        # Payloads are CONTENT-FREE (ids / counts / outcomes; no message
        # text, no names).
        self._lifecycle_notifier = None

    def set_lifecycle_notifier(self, notifier) -> None:
        """Wire the lifecycle notifier (manager → SSE room channels).

        Args:
            notifier: An async callable accepting ``room_id``, ``kind``
                and ``data`` keyword arguments; ``None`` unsets.
        """
        self._lifecycle_notifier = notifier

    async def _notify_lifecycle(
        self, room_id: str, kind: str, data: dict[str, Any]
    ) -> None:
        """Fire the lifecycle notifier BEST-EFFORT (never raises).

        Args:
            room_id: The room the transition completed on.
            kind: The ``room_*`` audit-class event kind.
            data: The CONTENT-FREE payload (ids / counts / outcomes).
        """
        notifier = self._lifecycle_notifier
        if notifier is None:
            return
        try:
            await notifier(room_id=room_id, kind=kind, data=data)
        except Exception as e:  # noqa: BLE001 — observability must not break mutation
            logger.debug(
                "room lifecycle notifier failed (room=%s kind=%s): %r",
                room_id,
                kind,
                e,
            )

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
        room = await asyncio.to_thread(
            self._repo.create_room,
            name=name,
            tree_root_id=tree_root_id,
            owner_instance_id=owner_instance_id,
            owner_member_name=owner_member_name,
            owner_agent_id=owner_agent_id,
            room_id=room_id,
        )
        # F-3 lifecycle broadcast — content-free (ids only; the room
        # NAME is deliberately NOT carried onto the SSE channel).
        await self._notify_lifecycle(
            room_id=str(room.get("room_id") or ""),
            kind="room_created",
            data={
                "room_id": str(room.get("room_id") or ""),
                "tree_root_id": str(room.get("tree_root_id") or ""),
                "owner_instance_id": str(room.get("owner_instance_id") or ""),
            },
        )
        return room

    # ── the close transition (the ONLY producer of ``closed``) ───────

    async def close_room(
        self, *, room_id: str, owner_instance_id: str
    ) -> dict[str, Any]:
        """Close the room (off-loop) — the ONLY producer of ``closed``.

        The forward-only ``active → closed`` transition (architecture
        §3/§5: "close = parent tool only"). The P-close GC arm
        (``state=closed AND closed_at < now − 14d`` → purge) becomes
        REACHABLE through this producer.

        The F-3 lifecycle broadcast fires AFTER the durable commit —
        content-free (ids + the state; NO room name, NO member name).
        A notifier failure never fails the close (best-effort, the
        observability-must-not-break-mutation rule).
        """
        result = await asyncio.to_thread(
            self._repo.close_room,
            room_id,
            owner_instance_id=owner_instance_id,
        )
        await self._notify_lifecycle(
            room_id=room_id,
            kind="room_closed",
            data={
                "room_id": room_id,
                "owner_instance_id": owner_instance_id,
                "state": str(result.get("state") or ""),
                "closed_at": str(result.get("closed_at") or ""),
            },
        )
        return result

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
        source_message_id: int | None = None,
        trigger: str | None = None,
        member: str | None = None,
        resolution_warnings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Append under the per-room lock (off-loop).

        Lock scope: the ENTIRE append (room-state gate + seq INSERT +
        counters bump). The lock serializes writers within the daemon;
        ``UNIQUE(room_id, seq)`` backstops anything else.

        ``resolution_warnings`` (P5 / A-P3-16) rides the SAME insert —
        the log is append-only, so the durable warning set is written
        at append time (the commit point).
        """
        lock = await self.get_room_lock(room_id)
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
                source_message_id=source_message_id,
                trigger=trigger,
                member=member,
                resolution_warnings=resolution_warnings,
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
            member = await asyncio.to_thread(
                self._repo.join_room,
                room_id=room_id,
                instance_id=instance_id,
                base_name=base_name,
                role=role,
                agent_id=agent_id,
            )
        # F-3 lifecycle broadcast — content-free (ids + role; the final
        # member NAME is deliberately NOT carried onto the SSE channel).
        # The SPAWN-path join (the membership row inserted INSIDE the
        # spawn transaction) does NOT ride this method — it announces
        # itself through :meth:`notify_spawn_join` (the A-P6-26 gap,
        # closed by the D item: the post-spawn read-back point is the
        # producer), so both producers emit the SAME payload shape.
        await self._notify_lifecycle(
            room_id=room_id,
            kind="room_joined",
            data={
                "room_id": room_id,
                "instance_id": instance_id,
                "role": role,
            },
        )
        return member

    async def notify_spawn_join(
        self, *, room_id: str, instance_id: str, role: str
    ) -> None:
        """Announce a SPAWN-path membership join (the F-3 producer gap).

        The spawn path inserts the membership row INSIDE the spawn
        transaction — it does NOT ride :meth:`join_room` — so its join
        event had no producer (the gap A-P6-26 disclosed). This method
        is the producer's PUBLIC seam: the spawn lane calls it AFTER
        the spawn commit, with the SAME content-free payload shape
        :meth:`join_room` emits (ids + role; NO names, NO content) —
        so the two producers cannot drift.

        Best-effort: a notifier failure never fails the spawn (the
        spawn already committed — observability must not break the
        mutation).
        """
        await self._notify_lifecycle(
            room_id=room_id,
            kind="room_joined",
            data={
                "room_id": room_id,
                "instance_id": instance_id,
                "role": role,
            },
        )

    async def leave_room(
        self, *, room_id: str, instance_id: str
    ) -> dict[str, Any]:
        """Membership ``state → left`` (owner exempt; off-loop)."""
        result = await asyncio.to_thread(
            self._repo.leave_room,
            room_id=room_id,
            instance_id=instance_id,
        )
        # F-3 lifecycle broadcast — content-free (ids only).
        await self._notify_lifecycle(
            room_id=room_id,
            kind="room_left",
            data={"room_id": room_id, "instance_id": instance_id},
        )
        return result

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

    async def list_member_rooms(
        self, instance_id: str, *, active_only: bool = True
    ) -> list[dict[str, Any]]:
        """Every room this instance holds a membership row in (P5).

        The digest builder's "which rooms am I in?" lookup — the
        INVERSE of the roster read. The subject of the query is the
        caller themself, so no read predicate applies.
        """
        return await asyncio.to_thread(
            self._repo.list_member_rooms, instance_id, active_only=active_only
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

    # ── P6 read surface (the HTTP observability modal) ────────────────

    async def list_rooms_for_tree(
        self, tree_root_id: str
    ) -> list[dict[str, Any]]:
        """Every room in the tree scope (off-loop) — P6 FE modal."""
        return await asyncio.to_thread(
            self._repo.list_rooms_for_tree, tree_root_id
        )

    # ── P6 GC scans (off-loop wrappers over the repository) ───────────

    async def list_rooms_by_state(self, state: str) -> list[dict[str, Any]]:
        """Every room in one lifecycle state (the GC scan's input)."""
        return await asyncio.to_thread(self._repo.list_rooms_by_state, state)

    async def list_active_member_instance_ids(
        self, room_ids: list[str]
    ) -> dict[str, list[str]]:
        """``{room_id: [instance_id, ...]}`` for the ACTIVE roster."""
        return await asyncio.to_thread(
            self._repo.list_active_member_instance_ids, room_ids
        )

    async def get_instance_records(
        self, instance_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """``{instance_id: {"status", "parent_id"}}`` — liveness inputs."""
        return await asyncio.to_thread(
            self._repo.get_instance_records, instance_ids
        )

    async def rooms_with_undelivered_tagged(
        self, room_ids: list[str]
    ) -> set[str]:
        """Rooms holding an undelivered TAGGED row (the cursor guard)."""
        return await asyncio.to_thread(
            self._repo.rooms_with_undelivered_tagged, room_ids
        )

    # ── purge ──────────────────────────────────────────────────────────

    async def purge_room(self, room_id: str, *, reason: str) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self._repo.purge_room, room_id, reason=reason
        )
        # F-3 lifecycle broadcast — content-free (id + outcome reason).
        await self._notify_lifecycle(
            room_id=room_id,
            kind="room_purged",
            data={"room_id": room_id, "reason": reason},
        )
        return result

    # ── P3 delivery helpers (off-loop wrappers) ─────────────────────
    async def cas_instance_status_to_running(
        self, instance_id: str, allowed: frozenset[str]
    ) -> int:
        return await asyncio.to_thread(
            self._repo.cas_instance_status_to_running, instance_id, allowed
        )

    async def get_instance_status(self, instance_id: str) -> str | None:
        return await asyncio.to_thread(self._repo.get_instance_status, instance_id)

    async def emit_room_event(
        self, instance_id: str, kind: str, data: dict[str, Any]
    ) -> None:
        await asyncio.to_thread(self._repo.emit_room_event, instance_id, kind, data)

    # ── stop symmetry (P3 carry-over) ──────────────────────────────────

    async def clear_lock_registry(self) -> int:
        """Drop every per-room lock (manager.shutdown step).

        Symmetry with ``get_room_lock``'s registry maintenance: the
        registry is loop-local RAM (locks are never persisted), so the
        clear is HYGIENE, not correctness — a restart re-derives locks
        on demand and the ``UNIQUE(room_id, member_name)`` /
        ``UNIQUE(room_id, seq)`` DB backstops own correctness. Returns
        the number of locks dropped (for the shutdown log line).
        """
        async with self._locks_guard:
            dropped = len(self._room_locks)
            self._room_locks.clear()
            return dropped

    # ── boot regeneration snapshot (pure derivation) ───────────────────

    async def derive_undelivered_tagged(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._repo.derive_undelivered_tagged)

    async def nudge_snapshot_for_instance(
        self, instance_id: str
    ) -> list[dict[str, Any]]:
        """P4 nudge scan (off-loop facade) — see the repository method.

        Read-only per-room pending picture for ONE instance; the
        nudge coordinator's only DB entry point."""
        return await asyncio.to_thread(
            self._repo.nudge_snapshot_for_instance,
            instance_id,
        )
