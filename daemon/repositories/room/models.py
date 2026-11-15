"""Agent group chat — room substrate SQL models (Phase 2 / P1).

Three tables, ONE package (the lane's repo home; design
``.agents/shared/planning/group-chat/architecture-recommendation.md``
§1.3, Emperor-ratified 2026-11-15 — the spec of record):

* :class:`ChatRoom` — ``chatrooms`` — room identity + scope + lifecycle.
  One row per chat-room. ``tree_root_id`` anchors the room scope (the
  join predicate compares the joiner's tree root against it);
  ``owner_instance_id`` is the CREATOR (D2 — the owner membership row
  is seeded at create). ``state`` carries exactly ``active | closed`` —
  PURGE is a terminal TRANSITION (whole-row deletion), NOT a persisted
  state (R2: "purged rooms are nonexistent"; no tombstone, no archive
  tables).
* :class:`ChatRoomMember` — ``chatroom_members`` — roster truth + the
  per-participant cursor substrate. EVERY participant has a row,
  INCLUDING the owner (seeded at room create with ``role=owner`` and
  both cursors at 0 — D2). Two UNIQUE constraints: ``(room_id,
  instance_id)`` (one membership row per participant) and ``(room_id,
  member_name)`` (the tier-4 per-room naming-registry backstop).
  ``last_read_seq`` / ``last_delivered_seq`` are the read-watermark
  pair; the re-derivation predicate is
  ``seq > max(last_read_seq, last_delivered_seq)``.
* :class:`RoomMessage` — ``room_messages`` — the durable append-only
  log (BoardNote-adapted). ``UNIQUE(room_id, seq)`` is the per-room
  monotonic-sequence backstop behind the per-room write lock.
  ``addressed_members`` is the R1 structured routing field (resolved
  ``addressed_to`` targets — routing, never escalation);
  ``tagged_members`` is the escalation field (@tags).

Append-only discipline: there is NO update / delete path for a
``room_messages`` row anywhere in this package. The child FKs to
``chatrooms.room_id`` are **NO ACTION** (no ``ondelete=`` clause) so a
hard delete of the parent room is refused by the database UNLESS the
children are deleted first — which is exactly what the purge
transaction does (event row → messages → members → room, one commit).

MIGRATION: the SQLite-side canonical DDL lives in ONE file —
``daemon/migrations/versions/20261115_080000_create_chatroom_tables.sql``
— with all 3 CREATE TABLE statements (the multi-table-single-file
precedent: board tables 20261020_060000, memoir-v2 20261109_021154;
the generic own-.sql-per-table doctrine is explicitly superseded for
this lane by the ratified item #1). Rollback = drop the 3 tables +
delete the migration row (additive-only; ``rollback_safe=true``).
PostgreSQL: ``SQLModel.metadata.create_all`` is the operative
fresh-PG artifact via the pre-create_all registration in
``daemon/manager.py`` (the app_settings / board / POK precedent) —
brand-new tables need NO ``_ensure_postgres_columns`` mirror.

Conventions (house): every string column TEXT on both drivers (never
VARCHAR); ISO-8601 TEXT timestamps; JSON-shaped columns via
:class:`~daemon.repositories.infra.types.JSONBType` (JSONB on PG,
JSON on SQLite); ``seq`` is BigInteger on both drivers (SQLite stores
it as INTEGER — the affinity is dynamic).
"""

from __future__ import annotations

import enum

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType
from daemon.repositories.instance.models import InstanceStatus

__all__ = [
    "RoomState",
    "RoomMemberState",
    "RoomMemberRole",
    "RoomMessageKind",
    "ROOM_CLOSED_STATES",
    "ROOM_LIVE_STATES",
    "TERMINAL_INSTANCE_STATUSES_FOR_ROOMS",
    "NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS",
    "ChatRoom",
    "ChatRoomMember",
    "RoomMessage",
]


# ── Room-domain enums ─────────────────────────────────────────────────────


class RoomState(str, enum.Enum):
    """Room lifecycle state — exactly ``active | closed``.

    Forward-only: ``active → closed`` (close is the parent tool's job,
    P5). The third lifecycle outcome, PURGE, is NOT a state — it is the
    transactional deletion of every room row (R2; architecture §2.1
    capability row, third column). A purged room is nonexistent: every
    predicate resolves it as not-found.
    """

    ACTIVE = "active"
    CLOSED = "closed"


class RoomMemberState(str, enum.Enum):
    """Membership row state — ``active | left``.

    Leave sets ``state=left`` (owner exempt — the owner row is retired
    only WITH the room, D2). LEFT rows stay on disk (roster history +
    cursor substrate) but fail every active-membership predicate and
    are FREE vs the membership caps.
    """

    ACTIVE = "active"
    LEFT = "left"


class RoomMemberRole(str, enum.Enum):
    """Membership role.

    ``owner`` = the creator (seeded at room create, D2 — exactly one
    per room). ``child`` = a spawned member. ``parent`` is carried for
    the P2 join surface (a parent instance joining a room it did not
    create); P1 only ever writes ``owner``.
    """

    OWNER = "owner"
    PARENT = "parent"
    CHILD = "child"


class RoomMessageKind(str, enum.Enum):
    """Message row kind.

    ``message`` = an ordinary room post. ``system`` = a room-machinery
    row (reserved). ``task_proposal`` / ``task_decision`` = the
    Ruling-A proposal machinery rows (minted at P3, never by P1 — the
    kind VALUES exist at birth so the P3 rows need no migration).
    """

    MESSAGE = "message"
    SYSTEM = "system"
    TASK_PROPOSAL = "task_proposal"
    TASK_DECISION = "task_decision"


# ── Shared instance-status sets (ONE home; GC + boot regen) ──────────────
#
# Derivation is FROM THE 10-MEMBER ``InstanceStatus`` ENUM, never from
# ``ALIVE_INSTANCE_STATUSES`` (which omits ``waiting`` — a live
# awaiting-input state; constants.py:1066-1072). ``waiting`` counts as
# non-terminal (alive) for room liveness (P-idle GC trigger + boot
# reasoning). The enum-totality pin test asserts the two sets below
# partition the enum exactly (disjoint union == all 10 members) — a
# future enum addition reddens until it is classified here.

TERMINAL_INSTANCE_STATUSES_FOR_ROOMS: frozenset[str] = frozenset({
    "completed",
    "error",
    "terminated",
    "failed",
})

# Derived STRUCTURALLY from the enum: non-terminal ≡ enum − terminal,
# so the partition (disjoint union == all members) holds by
# construction; the totality pin's exact-content assertions keep their
# teeth on the TERMINAL classification (hand-written) and the derived
# result.
NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS: frozenset[str] = frozenset(
    s.value
    for s in InstanceStatus
    if s.value not in TERMINAL_INSTANCE_STATUSES_FOR_ROOMS
)

# Room states whose rows are read/targetable. Purged rooms are NOT in
# any set — they have no rows at all.
ROOM_LIVE_STATES: frozenset[str] = frozenset({RoomState.ACTIVE.value})
ROOM_CLOSED_STATES: frozenset[str] = frozenset({RoomState.CLOSED.value})


# ── chatrooms ──────────────────────────────────────────────────────────────


class ChatRoom(SQLModel, table=True):
    """One chat-room: identity + scope + lifecycle."""

    __tablename__ = "chatrooms"

    __table_args__ = (
        # Room-scope anchor — the join predicate resolves the joiner's
        # tree root and compares it against this column; the per-tree
        # active-room cap counts rows by it (P2).
        Index("idx_chatrooms_tree_root_id", "tree_root_id"),
        # P-close GC trigger: state=closed AND closed_at < now-14d.
        Index("idx_chatrooms_state_closed_at", "state", "closed_at"),
        # P-idle GC trigger: state=active AND last_post_at < now-72h.
        Index("idx_chatrooms_state_last_post_at", "state", "last_post_at"),
    )

    room_id: str = Field(
        sa_column=Column("room_id", Text, primary_key=True),
        description="Room UUID — the PK and every child table's FK target.",
    )
    name: str = Field(
        sa_column=Column("name", Text, nullable=False),
        description="Human-readable room name (parent-provided at create).",
    )
    tree_root_id: str = Field(
        sa_column=Column("tree_root_id", Text, nullable=False),
        description=(
            "Instance-tree root the room is scoped to — join admission "
            "compares the joiner's resolved tree root against this."
        ),
    )
    owner_instance_id: str = Field(
        sa_column=Column("owner_instance_id", Text, nullable=False),
        description=(
            "The CREATOR's instance id (D2). Also the owner membership "
            "row's instance_id; the owner row is retired only WITH the "
            "room."
        ),
    )
    state: str = Field(
        default=RoomState.ACTIVE.value,
        sa_column=Column("state", Text, nullable=False),
        description="Lifecycle: active | closed. Purge deletes the row.",
    )
    closed_at: str | None = Field(
        default=None,
        sa_column=Column("closed_at", Text, nullable=True),
        description="Fixed-width UTC ISO-8601 close stamp; NULL while active.",
    )
    last_post_at: str | None = Field(
        default=None,
        sa_column=Column("last_post_at", Text, nullable=True),
        description=(
            "UTC ISO-8601 stamp of the most recent append — the P-idle "
            "GC trigger input. NULL until the first post."
        ),
    )
    message_count: int = Field(
        default=0,
        sa_column=Column("message_count", BigInteger, nullable=False, default=0),
        description=(
            "Denormalized append counter — bumped in the SAME transaction "
            "as each append (no drift). Digests read it instead of "
            "COUNT(*) over the log."
        ),
    )
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False),
        description="Fixed-width UTC ISO-8601 create stamp.",
    )
    updated_at: str = Field(
        sa_column=Column("updated_at", Text, nullable=False),
        description="UTC ISO-8601 — bumped on any room-row mutation.",
    )


# ── chatroom_members ───────────────────────────────────────────────────────


class ChatRoomMember(SQLModel, table=True):
    """Roster truth + per-participant cursor substrate.

    Every participant has a row — INCLUDING the owner (seeded at room
    create in the same transaction as the room row, D2). The composite
    PK ``(room_id, instance_id)`` makes duplicate membership
    structurally impossible; the ``UNIQUE(room_id, member_name)``
    backstop makes duplicate per-room names structurally impossible
    (the tier-4 naming-registry backstop — colliders get suffixed at
    registration, P2).
    """

    __tablename__ = "chatroom_members"

    __table_args__ = (
        # Per-room name-registry backstop (tier-4). Distinct from the
        # composite PK so the constraint names stay stable on both
        # drivers and the naming collision surfaces as THIS constraint.
        UniqueConstraint(
            "room_id",
            "member_name",
            name="uq_chatroom_members_room_member_name",
        ),
        # Restore / join validation path: find rooms for an instance.
        Index("idx_chatroom_members_instance_id", "instance_id"),
    )

    room_id: str = Field(
        sa_column=Column(
            "room_id",
            Text,
            ForeignKey("chatrooms.room_id"),
            primary_key=True,
        ),
        description=(
            "Owning room — composite PK part 1. Child FK to "
            "chatrooms.room_id is NO ACTION (no ``ondelete=`` clause, "
            "the BoardNote shape): a hard delete of the parent room is "
            "refused by the database UNLESS the children are deleted "
            "first — the purge transaction's child-first order is the "
            "ONLY legal deletion path."
        ),
    )
    instance_id: str = Field(
        sa_column=Column("instance_id", Text, primary_key=True),
        description="Participant instance id — composite PK part 2.",
    )
    member_name: str = Field(
        sa_column=Column("member_name", Text, nullable=False),
        description=(
            "Per-room purpose name (@tag/address target). UNIQUE per "
            "room — the tier-4 registry backstop."
        ),
    )
    agent_id: str | None = Field(
        default=None,
        sa_column=Column("agent_id", Text, nullable=True),
        description="Denormalized agent id of the participant (tag resolution aid).",
    )
    role: str = Field(
        sa_column=Column("role", Text, nullable=False),
        description="owner | parent | child. The owner row is seeded at create.",
    )
    joined_at: str = Field(
        sa_column=Column("joined_at", Text, nullable=False),
        description="Fixed-width UTC ISO-8601 join stamp.",
    )
    last_read_seq: int = Field(
        default=0,
        sa_column=Column(
            "last_read_seq", BigInteger, nullable=False, default=0
        ),
        description=(
            "The only read ack — the read cursor. Re-derivation "
            "predicate input (max of the two cursors)."
        ),
    )
    last_delivered_seq: int = Field(
        default=0,
        sa_column=Column(
            "last_delivered_seq", BigInteger, nullable=False, default=0
        ),
        description=(
            "Delivery floor — advanced on lane admission (P3) and at "
            "proposal admission (Y14). Re-derivation predicate input."
        ),
    )
    state: str = Field(
        default=RoomMemberState.ACTIVE.value,
        sa_column=Column("state", Text, nullable=False),
        description="active | left. LEFT rows fail every active predicate.",
    )


# ── room_messages ──────────────────────────────────────────────────────────


class RoomMessage(SQLModel, table=True):
    """The durable append-only room log (BoardNote-adapted).

    There is NO update / delete path for a message row anywhere in this
    package. ``UNIQUE(room_id, seq)`` is the per-room monotonic-seq
    backstop behind the per-room write lock; the append INSERT computes
    the seq in a single statement
    (``COALESCE(MAX(seq), 0) + 1``).
    """

    __tablename__ = "room_messages"

    __table_args__ = (
        # THE per-room ordering backstop. Also the room-scoped range
        # read index (room_id prefix + seq ordering).
        UniqueConstraint(
            "room_id",
            "seq",
            name="uq_room_messages_room_seq",
        ),
    )

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
        description=(
            "SERIAL PK on PG (INTEGER PRIMARY KEY AUTOINCREMENT on "
            "SQLite) — the BoardNote shape. Ordering is seq, not id."
        ),
    )
    room_id: str = Field(
        sa_column=Column(
            "room_id",
            Text,
            ForeignKey("chatrooms.room_id"),
            nullable=False,
        ),
        description=(
            "Owning room — child FK to chatrooms.room_id is NO ACTION "
            "(no ``ondelete=`` clause, the BoardNote shape); the purge "
            "transaction deletes children first."
        ),
    )
    seq: int = Field(
        sa_column=Column("seq", BigInteger, nullable=False),
        description=(
            "Per-room monotonic sequence — 1-based, assigned by the "
            "append transaction under the per-room lock; "
            "UNIQUE(room_id, seq) is the backstop."
        ),
    )
    author_instance_id: str = Field(
        sa_column=Column("author_instance_id", Text, nullable=False),
        description="Authoring instance id — stamped by the repository.",
    )
    author_name: str = Field(
        sa_column=Column("author_name", Text, nullable=False),
        description=(
            "Denormalized per-room member name — survives membership "
            "churn for speaker attribution through compaction."
        ),
    )
    text: str = Field(
        sa_column=Column("text", Text, nullable=False),
        description="Post body. Never edited, never deleted (until purge).",
    )
    tagged_members: list[str] | None = Field(
        default=None,
        sa_column=Column(
            "tagged_members", JSONBType, nullable=True, default=list
        ),
        description=(
            "JSONB array of @tagged member names — the ESCALATION "
            "dimension (tags interrupt; addressing routes). Empty list "
            "= untagged."
        ),
    )
    addressed_members: list[str] | None = Field(
        default=None,
        sa_column=Column(
            "addressed_members", JSONBType, nullable=True, default=list
        ),
        description=(
            "JSONB array of RESOLVED addressed_to member names (R1) — "
            "the ROUTING dimension. Non-escalating: addressing alone "
            "never injects."
        ),
    )
    kind: str = Field(
        default=RoomMessageKind.MESSAGE.value,
        sa_column=Column("kind", Text, nullable=False),
        description="message | system | task_proposal | task_decision.",
    )
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False),
        description="Fixed-width UTC ISO-8601 append stamp.",
    )
