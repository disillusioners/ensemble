"""Agent group chat room substrate — Phase 2 / P1 (Emperor-ratified design).

Three tables + sync repository + off-loop async facade:

* :mod:`~daemon.repositories.room.models` — ``chatrooms``,
  ``chatroom_members`` (roster + the per-participant cursor pair),
  ``room_messages`` (the append-only per-room seq log).
* :mod:`~daemon.repositories.room.repository` — engine-passed sync
  mechanics (create+owner seed / append / cursors / fail-closed reads /
  purge / boot-regen derivation).
* :mod:`~daemon.repositories.room.service` — the async facade: the
  per-room LRU lock registry (port of ``sources/dispatcher.py``) +
  ``asyncio.to_thread`` wrapping (off-loop house discipline).

Importing this package registers the three models with
``SQLModel.metadata`` (the pre-``create_all`` convention — the board /
POK precedent), so ``from daemon.repositories.room import ...`` at
manager boot is enough to land the tables on fresh PG databases.
"""

from daemon.repositories.room.models import (
    NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS,
    TERMINAL_INSTANCE_STATUSES_FOR_ROOMS,
    ChatRoom,
    ChatRoomMember,
    RoomMemberRole,
    RoomMemberState,
    RoomMessage,
    RoomMessageKind,
    RoomState,
)
from daemon.repositories.room.membership import (
    MEMBER_NAME_MAX_LEN,
    ROOM_MAX_ACTIVE_ROOMS_PER_TREE,
    ROOM_MAX_MEMBERS,
    RoomJoinCrossTreeError,
    RoomJoinError,
    RoomJoinMemberCapError,
    RoomJoinRoomCapError,
    RoomJoinRoomStateError,
    RoomJoinUnknownRoomError,
    RoomLeaveOwnerError,
    resolve_member_name,
    restore_eligibility,
    sanitize_member_name,
    validate_room_join,
)
from daemon.repositories.room.repository import (
    MemberNameCollisionError,
    MemberNotFoundError,
    RoomClosedError,
    RoomError,
    RoomNotFoundError,
    RoomRepository,
    RoomSeqConflictError,
)
from daemon.repositories.room.service import (
    MAX_ROOM_LOCKS,
    RoomChatService,
)

__all__ = [
    # models
    "ChatRoom",
    "ChatRoomMember",
    "RoomMessage",
    "RoomState",
    "RoomMemberState",
    "RoomMemberRole",
    "RoomMessageKind",
    "TERMINAL_INSTANCE_STATUSES_FOR_ROOMS",
    "NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS",
    # repository
    "RoomRepository",
    "RoomError",
    "RoomNotFoundError",
    "RoomClosedError",
    "MemberNotFoundError",
    "MemberNameCollisionError",
    "RoomSeqConflictError",
    # membership (P2)
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
    "validate_room_join",
    "restore_eligibility",
    "sanitize_member_name",
    "resolve_member_name",
    # service
    "RoomChatService",
    "MAX_ROOM_LOCKS",
]
