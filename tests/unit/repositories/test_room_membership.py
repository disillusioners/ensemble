"""Behavior matrix for group-chat P2 room membership.

Real file-backed SQLite (``tmp_path`` + per-connection PRAGMAs — the
P1 room-matrix harness; ``PRAGMA foreign_keys=ON``), NOT
``StaticPool``/``:memory:``.

Coverage (commission P2 scope items 2-4 + plan-overview §6 P2 row):

* ``_validate_room_join`` — the pinned 5-conjunct admission
  predicate, ONE DISTINCT typed error per failure (unknown room /
  room-state / cross-tree / member-cap / room-cap), room payload on
  success;
* cap accounting — ACTIVE rows only: owner counts, LEFT rows FREE;
* the tier-4 suffix registry — bare first bearer, smallest-free
  ``-N`` colliders, LEFT-row names still taken, concurrent same-name
  joins under the room lock (one bare + suffixed others), the
  ``UNIQUE(room_id, member_name)`` backstop typed as
  :class:`MemberNameCollisionError`;
* leave semantics — member → left, OWNER EXEMPT (typed), left member
  fails active predicates, spawn-side JSONB echo tolerated inert;
* the D5 two-conjunct restore rule + the left-member-restore pin;
* roster resolution (active-only vs full);
* content-free ``room_join`` / ``room_leave`` audit events.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Iterator

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

import daemon.repositories.event.models  # noqa: F401 — register event
import daemon.repositories.instance.models  # noqa: F401 — register instances
from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.room import (
    MemberNameCollisionError,
    MemberNotFoundError,
    RoomJoinCrossTreeError,
    RoomJoinError,
    RoomJoinMemberCapError,
    RoomJoinRoomCapError,
    RoomJoinRoomStateError,
    RoomJoinUnknownRoomError,
    RoomLeaveOwnerError,
    RoomNotFoundError,
    RoomRepository,
    RoomChatService,
    resolve_member_name,
    sanitize_member_name,
    validate_room_join,
)
from daemon.repositories.room.models import ChatRoom, ChatRoomMember
from daemon.repositories.room.repository import member_payload
from tests.helpers.room_harness import (
    count_rows,
    join_member,
    make_room,
    make_room_engine,
    seed_instance,
    seed_raw_room,
)


# ---------------------------------------------------------------------------
# F9-parity harness (P1 precedent)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "room_membership_matrix.db")
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> RoomRepository:
    return RoomRepository(engine)


@pytest.fixture
def tree(engine: Engine) -> dict[str, str]:
    """grand-1 (root) → parent-1 (room owner) → {child-1, child-2}."""
    seed_instance(engine, "grand-1", None)
    seed_instance(engine, "parent-1", "grand-1")
    seed_instance(engine, "child-1", "parent-1")
    seed_instance(engine, "child-2", "parent-1")
    seed_instance(engine, "other-root", None)
    return {
        "root": "grand-1",
        "owner": "parent-1",
        "child1": "child-1",
        "child2": "child-2",
        "foreign_root": "other-root",
    }


@pytest.fixture
def room_id(repo: RoomRepository, tree: dict[str, str]) -> str:
    rid = make_room(
        repo,
        tree_root=tree["root"],
        owner=tree["owner"],
        owner_member_name="lead",
    )
    # child-1 rides the roster (the P1 fixture shape): member rows
    # carry role/name/state for the roster + restore legs.
    join_member(repo._engine, rid, tree["child1"], "worker-a")
    return rid


@pytest.fixture
def svc(engine: Engine) -> RoomChatService:
    return RoomChatService(engine)


# ---------------------------------------------------------------------------
# (a) _validate_room_join — the pinned 5-conjunct matrix
# ---------------------------------------------------------------------------


class TestValidateRoomJoin:
    def test_valid_join_returns_room_payload(self, engine, repo, tree, room_id):
        out = validate_room_join(
            engine, room_id=room_id, joiner_tree_root=tree["root"]
        )
        assert out["room_id"] == room_id
        assert out["state"] == "active"
        assert out["tree_root_id"] == tree["root"]

    def test_unknown_room_typed(self, engine, tree):
        ghost = str(uuid.uuid4())
        with pytest.raises(RoomJoinUnknownRoomError):
            validate_room_join(
                engine, room_id=ghost, joiner_tree_root=tree["root"]
            )

    def test_purged_room_is_unknown_room(self, engine, repo, tree, room_id):
        """R2: purged rooms are nonexistent — the join conjunct 1
        resolves them as unknown-room, never as a state error."""
        repo.purge_room(room_id, reason="test")
        with pytest.raises(RoomJoinUnknownRoomError):
            validate_room_join(
                engine, room_id=room_id, joiner_tree_root=tree["root"]
            )

    def test_closed_room_state_refusal_typed(
        self, engine, repo, tree, room_id
    ):
        # State transition helper — close is a P5 tool, the repo has
        # no close method at P2; flip directly (seeding, not policy).
        with Session(engine) as s:
            room = s.get(ChatRoom, room_id)
            room.state = "closed"
            s.add(room)
            s.commit()
        with pytest.raises(RoomJoinRoomStateError):
            validate_room_join(
                engine, room_id=room_id, joiner_tree_root=tree["root"]
            )

    def test_cross_tree_refusal_typed(self, engine, tree, room_id):
        with pytest.raises(RoomJoinCrossTreeError):
            validate_room_join(
                engine, room_id=room_id, joiner_tree_root=tree["foreign_root"]
            )

    def test_unresolvable_tree_root_fails_closed(self, engine, room_id):
        """None tree root (caller row unresolvable) → cross-tree
        refusal (fail-closed), never an admit."""
        with pytest.raises(RoomJoinCrossTreeError):
            validate_room_join(
                engine, room_id=room_id, joiner_tree_root=None
            )

    def test_all_five_refusals_are_distinct_types(
        self, engine, repo, tree, room_id
    ):
        """The typed-error matrix: five conjuncts, five DISTINCT types,
        all under the RoomJoinError family, none colliding."""
        observed: list[type] = []

        with pytest.raises(RoomJoinUnknownRoomError):
            validate_room_join(engine, room_id=str(uuid.uuid4()),
                               joiner_tree_root=tree["root"])
        observed.append(RoomJoinUnknownRoomError)

        with Session(engine) as s:
            room = s.get(ChatRoom, room_id)
            room.state = "closed"
            s.add(room)
            s.commit()
        with pytest.raises(RoomJoinRoomStateError):
            validate_room_join(engine, room_id=room_id,
                               joiner_tree_root=tree["root"])
        observed.append(RoomJoinRoomStateError)
        with Session(engine) as s:
            room = s.get(ChatRoom, room_id)
            room.state = "active"
            s.add(room)
            s.commit()

        with pytest.raises(RoomJoinCrossTreeError):
            validate_room_join(engine, room_id=room_id,
                               joiner_tree_root=tree["foreign_root"])
        observed.append(RoomJoinCrossTreeError)

        # Member cap: fill to 16 ACTIVE rows (owner + 15 children).
        for i in range(15):
            member_id = f"fill-{i}"
            seed_instance(engine, member_id, tree["owner"])
            join_member(engine, room_id, member_id, f"worker-{i}")
        with pytest.raises(RoomJoinMemberCapError):
            validate_room_join(engine, room_id=room_id,
                               joiner_tree_root=tree["root"])
        observed.append(RoomJoinMemberCapError)
        # Free 3 members so the member-cap conjunct passes and the
        # room-cap conjunct can fire on its own (each refusal leg
        # must be exercised in isolation).
        for i in range(3):
            repo.leave_room(room_id=room_id, instance_id=f"fill-{i}")

        # Room cap: seed 8 OTHER active rooms in the same tree
        # (the over-budget anomaly). The create path is CAPPED now
        # (P5 re-gate, A-P5-14) — 8 creates after the fixture room
        # would refuse the 9th — so the over-budget tree is seeded
        # RAW; the join-side conjunct stays the anomaly backstop.
        for i in range(8):
            seed_raw_room(engine, tree["root"], f"crowd-{i}")
        with pytest.raises(RoomJoinRoomCapError):
            validate_room_join(engine, room_id=room_id,
                               joiner_tree_root=tree["root"])
        observed.append(RoomJoinRoomCapError)

        assert len(set(observed)) == 5
        assert all(issubclass(t, RoomJoinError) for t in observed)

    def test_member_cap_counts_active_only_left_free(
        self, engine, repo, tree, room_id
    ):
        """LEFT rows are FREE: at 15 active the 16th slot is open even
        with 5 LEFT rows present — LEFT rows never tighten the cap."""
        # 2 active at fixture (owner + child-1). Fill to 15.
        for i in range(13):
            member_id = f"fill-{i}"
            seed_instance(engine, member_id, tree["owner"])
            join_member(engine, room_id, member_id, f"worker-{i}")
        # Add 5 LEFT rows on top — they must NOT tighten the cap.
        for i in range(5):
            left_id = f"gone-{i}"
            seed_instance(engine, left_id, tree["owner"])
            join_member(engine, room_id, left_id, f"gone-{i}")
            repo.leave_room(room_id=room_id, instance_id=left_id)
        assert count_rows(engine, ChatRoomMember, room_id=room_id, state="active") == 15
        assert count_rows(engine, ChatRoomMember, room_id=room_id, state="left") == 5
        # The 16th ACTIVE slot is open despite the 5 LEFT rows.
        joiner = "fit-16"
        seed_instance(engine, joiner, tree["owner"])
        validate_room_join(engine, room_id=room_id,
                           joiner_tree_root=tree["root"])
        join_member(engine, room_id, joiner, "worker-16")
        assert count_rows(engine, ChatRoomMember, room_id=room_id, state="active") == 16
        # At cap now — the next ACTIVE join refuses.
        joiner17 = "over-17"
        seed_instance(engine, joiner17, tree["owner"])
        with pytest.raises(RoomJoinMemberCapError):
            validate_room_join(engine, room_id=room_id,
                               joiner_tree_root=tree["root"])

    def test_owner_counts_toward_member_cap(
        self, engine, repo, tree, room_id
    ):
        """The owner's seeded row COUNTS: the cap arithmetic only adds
        up when the owner is included (16 = owner + child-1 + 14
        children; the 17th active row refuses)."""
        # 2 active at fixture (owner + child-1). Fill to 15.
        for i in range(13):
            member_id = f"fill-{i}"
            seed_instance(engine, member_id, tree["owner"])
            join_member(engine, room_id, member_id, f"worker-{i}")
        assert count_rows(engine, ChatRoomMember, room_id=room_id, state="active") == 15
        # 16th fits; 17th refuses.
        joiner = "fit-16"
        seed_instance(engine, joiner, tree["owner"])
        validate_room_join(engine, room_id=room_id,
                           joiner_tree_root=tree["root"])
        join_member(engine, room_id, joiner, "worker-16")
        assert count_rows(engine, ChatRoomMember, room_id=room_id, state="active") == 16
        joiner17 = "over-17"
        seed_instance(engine, joiner17, tree["owner"])
        with pytest.raises(RoomJoinMemberCapError):
            validate_room_join(engine, room_id=room_id,
                               joiner_tree_root=tree["root"])

    def test_room_cap_counts_active_rooms_only(
        self, engine, repo, tree, room_id
    ):
        """Closed rooms do not count toward the tree's active-room
        budget (the cap is on ACTIVE rooms)."""
        # 7 crowd rooms via the create path → fixture + 7 = 8 ACTIVE
        # (at the create cap, A-P5-14). The JOIN-side conjunct fires
        # at total >= 9, so the over-budget room is seeded RAW.
        for i in range(7):
            repo.create_room(
                name=f"crowd-{i}",
                tree_root_id=tree["root"],
                owner_instance_id=tree["owner"],
                owner_member_name=f"lead-{i}",
            )["room"]["room_id"]
        raw_id = seed_raw_room(engine, tree["root"], "over-budget")
        with pytest.raises(RoomJoinRoomCapError):
            validate_room_join(engine, room_id=room_id,
                               joiner_tree_root=tree["root"])
        # Close the raw room → 8 ACTIVE → the join fits again.
        with Session(engine) as s:
            room = s.get(ChatRoom, raw_id)
            room.state = "closed"
            s.add(room)
            s.commit()
        validate_room_join(engine, room_id=room_id,
                           joiner_tree_root=tree["root"])

    def test_room_cap_counting_base_excludes_target(
        self, engine, repo, tree
    ):
        """A-P2-1: the join-side room-cap count EXCLUDES the target
        room (the join adds a member, not a room). Eight active rooms
        in the tree, joining the EIGHTH (the last created, at-cap
        scenario) must SUCCEED — a design that allowed creating room
        #8 but refused joining it would be incoherent."""
        rid8 = None
        for i in range(8):
            rid8 = repo.create_room(
                name=f"room-{i}",
                tree_root_id=tree["root"],
                owner_instance_id=tree["owner"],
                owner_member_name=f"lead-{i}",
            )["room"]["room_id"]
        # 8 rooms exist (7 others); joining any of them fits.
        validate_room_join(engine, room_id=rid8,
                           joiner_tree_root=tree["root"])


# ---------------------------------------------------------------------------
# (b) tier-4 suffix registry
# ---------------------------------------------------------------------------


class TestSuffixRegistry:
    def test_resolve_bare_first_bearer(self):
        assert resolve_member_name(set(), "capturer") == "capturer"

    def test_resolve_smallest_free_suffix(self):
        taken = {"capturer", "capturer-1", "capturer-3"}
        assert resolve_member_name(taken, "capturer") == "capturer-2"

    def test_resolve_suffix_chain(self):
        taken: set[str] = set()
        names = []
        for _ in range(4):
            name = resolve_member_name(taken, "capture")
            names.append(name)
            taken.add(name)
        assert names == ["capture", "capture-1", "capture-2", "capture-3"]
        assert len(set(names)) == 4

    def test_join_room_bare_then_suffixed(self, engine, repo, tree, room_id):
        """Duplicates are FIRST-CLASS: two joins with the same base
        name land as bare + '-1'."""
        for member_id in ("dup-a", "dup-b"):
            seed_instance(engine, member_id, tree["owner"])
        first = repo.join_room(
            room_id=room_id, instance_id="dup-a", base_name="capture"
        )
        second = repo.join_room(
            room_id=room_id, instance_id="dup-b", base_name="capture"
        )
        assert first["member_name"] == "capture"
        assert second["member_name"] == "capture-1"

    def test_left_row_name_stays_taken(self, engine, repo, tree, room_id):
        """The UNIQUE backstop spans LEFT rows: a left member's name
        is NOT recycled — the next same-base join suffixes past it."""
        seed_instance(engine, "leaver", tree["owner"])
        seed_instance(engine, "late-arrival", tree["owner"])
        repo.join_room(room_id=room_id, instance_id="leaver",
                       base_name="scout")
        repo.leave_room(room_id=room_id, instance_id="leaver")
        late = repo.join_room(room_id=room_id, instance_id="late-arrival",
                              base_name="scout")
        assert late["member_name"] == "scout-1"

    def test_unique_backstop_typed_collision(self, engine, repo, tree, room_id):
        """Direct same-name insert (bypassing the registry) → the
        ``UNIQUE(room_id, member_name)`` backstop fires, and the
        repository's fingerprint-only classifier maps the REAL driver
        message to :class:`MemberNameCollisionError` (never mis-typed
        for a composite-PK duplicate)."""
        from sqlalchemy.exc import IntegrityError as sa_IntegrityError

        seed_instance(engine, "c-1", tree["owner"])
        seed_instance(engine, "c-2", tree["owner"])
        repo.join_room(room_id=room_id, instance_id="c-1", base_name="head")
        with pytest.raises(sa_IntegrityError) as exc_info:
            join_member(engine, room_id, "c-2", "head")
        # The classifier over the REAL driver message → typed surface.
        with pytest.raises(MemberNameCollisionError):
            repo._raise_typed_integrity(exc_info.value, room_id, "head")

    def test_composite_pk_duplicate_never_mistyped_as_name_collision(
        self, engine, repo, tree, room_id
    ):
        """A composite-PK duplicate (same instance twice, DIFFERENT
        names) must fall through to the BASE RoomError — the
        fingerprint-only discipline (P1 pin, extended to the join
        path)."""
        from sqlalchemy.exc import IntegrityError as sa_IntegrityError

        from daemon.repositories.room.repository import RoomError

        seed_instance(engine, "pk-dup", tree["owner"])
        repo.join_room(room_id=room_id, instance_id="pk-dup",
                       base_name="first-name")
        with pytest.raises(sa_IntegrityError) as exc_info:
            join_member(engine, room_id, "pk-dup", "second-name")
        with pytest.raises(RoomError) as typed:
            repo._raise_typed_integrity(exc_info.value, room_id, "second-name")
        assert not isinstance(typed.value, MemberNameCollisionError)

    async def test_concurrent_same_name_joins_one_bare_rest_suffixed(
        self, engine, repo, svc, tree, room_id
    ):
        """The registry race: N concurrent same-base joins under the
        per-room lock → ALL succeed, names all DISTINCT (one bare +
        smallest-free suffixes in join-completion order)."""
        n = 6
        for i in range(n):
            seed_instance(engine, f"racer-{i}", tree["owner"])

        async def join_one(i: int) -> str:
            out = await svc.join_room(
                room_id=room_id, instance_id=f"racer-{i}", base_name="wing"
            )
            return out["member_name"]

        names = await asyncio.gather(*(join_one(i) for i in range(n)))
        assert len(set(names)) == n
        assert "wing" in names
        for name in names:
            assert name == "wing" or name.startswith("wing-")

    async def test_concurrent_join_same_instance_pk_collision_typed(
        self, engine, repo, svc, tree, room_id
    ):
        """The composite PK is the other backstop: a double-join of
        the SAME instance surfaces as the BASE RoomError — a
        composite-PK duplicate must NEVER be mis-typed as a name
        collision (fingerprint-only discipline)."""
        from daemon.repositories.room.repository import RoomError as _RoomError

        seed_instance(engine, "double", tree["owner"])
        await svc.join_room(room_id=room_id, instance_id="double",
                            base_name="twin")
        with pytest.raises(_RoomError) as typed:
            await svc.join_room(room_id=room_id, instance_id="double",
                                base_name="twin-again")
        assert not isinstance(typed.value, MemberNameCollisionError)


# ---------------------------------------------------------------------------
# (c) leave semantics
# ---------------------------------------------------------------------------


class TestLeave:
    def test_member_leave_sets_left(self, engine, repo, tree, room_id):
        seed_instance(engine, "leaver-1", tree["owner"])
        repo.join_room(room_id=room_id, instance_id="leaver-1",
                       base_name="deployer")
        out = repo.leave_room(room_id=room_id, instance_id="leaver-1")
        assert out["state"] == "left"
        assert count_rows(engine, ChatRoomMember, room_id=room_id, state="left") == 1
        # The row survives (roster history) — never deleted.
        member = repo.get_member(room_id, "leaver-1")
        assert member is not None

    def test_owner_leave_refused_typed(self, engine, repo, tree, room_id):
        with pytest.raises(RoomLeaveOwnerError):
            repo.leave_room(room_id=room_id, instance_id=tree["owner"])

    def test_leave_unknown_room_and_member_typed(
        self, engine, repo, tree, room_id
    ):
        with pytest.raises(RoomNotFoundError):
            repo.leave_room(room_id=str(uuid.uuid4()),
                            instance_id=tree["child1"])
        with pytest.raises(MemberNotFoundError):
            repo.leave_room(room_id=room_id, instance_id="never-joined")

    def test_left_member_fails_active_membership(
        self, engine, repo, tree, room_id
    ):
        """A LEFT row fails every active-membership predicate: not
        listed active-only, not a valid read cursor holder."""
        seed_instance(engine, "quiet-1", tree["owner"])
        repo.join_room(room_id=room_id, instance_id="quiet-1",
                       base_name="sleeper")
        repo.leave_room(room_id=room_id, instance_id="quiet-1")
        active_roster = repo.list_members(
            room_id, caller_instance_id=tree["owner"], active_only=True
        )
        assert "quiet-1" not in {m["instance_id"] for m in active_roster}
        full_roster = repo.list_members(
            room_id, caller_instance_id=tree["owner"], active_only=False
        )
        assert "quiet-1" in {m["instance_id"] for m in full_roster}

    def test_leave_emits_content_free_room_leave_event(
        self, engine, repo, tree, room_id
    ):
        seed_instance(engine, "leaver-2", tree["owner"])
        repo.join_room(room_id=room_id, instance_id="leaver-2",
                       base_name="mailer")
        repo.leave_room(room_id=room_id, instance_id="leaver-2")
        with Session(engine) as s:
            rows = s.exec(
                select(Event).where(Event.kind == EventKind.ROOM_LEAVE.value)
            ).all()
            assert len(rows) == 1
            data = json.loads(rows[0].data)
            assert data["room_id"] == room_id
            assert data["instance_id"] == "leaver-2"
            assert data["member_name"] == "mailer"

    def test_join_emits_room_join_context_via_registry_names(
        self, engine, repo, tree, room_id
    ):
        """The registry compute is room-scoped: names from ANOTHER
        room never collide into this room's registry (per-room
        uniqueness, never global)."""
        other = repo.create_room(
            name="beta",
            tree_root_id=tree["root"],
            owner_instance_id=tree["owner"],
            owner_member_name="lead2",
        )["room"]["room_id"]
        seed_instance(engine, "xroom-1", tree["owner"])
        repo.join_room(room_id=other, instance_id="xroom-1", base_name="pilot")
        seed_instance(engine, "xroom-2", tree["owner"])
        mine = repo.join_room(room_id=room_id, instance_id="xroom-2",
                              base_name="pilot")
        assert mine["member_name"] == "pilot"  # bare — different room


# ---------------------------------------------------------------------------
# A-P2-11 closed in P3 — room_join emission on admission
# ---------------------------------------------------------------------------


class TestRoomJoinEventEmission:
    def test_join_emits_content_free_room_join_event(
        self, engine, repo, tree, room_id
    ):
        """A-P2-11 closed in P3: admission emits the reserved
        content-free ``room_join`` EventKind — SAME shape as
        ``room_leave`` (ids + per-room name only), riding the room
        OWNER's event stream, committed in the SAME transaction as the
        membership row."""
        seed_instance(engine, "joiner-1", tree["owner"])
        repo.join_room(room_id=room_id, instance_id="joiner-1",
                       base_name="tracker")
        with Session(engine) as s:
            rows = s.exec(
                select(Event).where(Event.kind == EventKind.ROOM_JOIN.value)
            ).all()
            assert len(rows) == 1
            data = json.loads(rows[0].data)
            assert data["room_id"] == room_id
            assert data["instance_id"] == "joiner-1"
            assert data["member_name"] == "tracker"
            # The trace rides the OWNER's stream (the room_leave shape).
            assert rows[0].instance_id == tree["owner"]

    def test_join_collision_leaves_no_phantom_room_join_event(
        self, engine, repo, tree, room_id, monkeypatch
    ):
        """The event lives in the SAME transaction as the membership
        row: a rolled-back admission (UNIQUE backstop fire) leaves NO
        phantom room_join event. The collision is forced by pinning
        the suffix registry to an already-registered name."""
        import daemon.repositories.room.membership as _membership

        seed_instance(engine, "first-1", tree["owner"])
        repo.join_room(room_id=room_id, instance_id="first-1",
                       base_name="mailer")
        monkeypatch.setattr(
            _membership, "resolve_member_name", lambda _reg, base: base
        )
        seed_instance(engine, "second-1", tree["owner"])
        with pytest.raises(MemberNameCollisionError):
            repo.join_room(room_id=room_id, instance_id="second-1",
                           base_name="mailer")
        with Session(engine) as s:
            join_events = s.exec(
                select(Event).where(Event.kind == EventKind.ROOM_JOIN.value)
            ).all()
            assert len(join_events) == 1, (
                "a rolled-back admission must leave NO phantom room_join "
                "event (same-transaction atomicity) — only the FIRST "
                "join's event exists"
            )
            assert "second-1" not in join_events[0].data
            # owner + fixture child-1 + first-1 — the rolled-back join
            # added nothing.
            assert count_rows(engine, ChatRoomMember,
                              room_id=room_id) == 3

    def test_join_leave_event_parity(self, engine, repo, tree, room_id):
        """The A-P2-11 asymmetry is retired: a member's lifecycle now
        emits BOTH the room_join and the room_leave event (same shape,
        same stream)."""
        seed_instance(engine, "sym-1", tree["owner"])
        repo.join_room(room_id=room_id, instance_id="sym-1",
                       base_name="courier")
        repo.leave_room(room_id=room_id, instance_id="sym-1")
        with Session(engine) as s:
            kinds = sorted(
                s.exec(
                    select(Event.kind).where(
                        Event.kind.in_(
                            [EventKind.ROOM_JOIN.value,
                             EventKind.ROOM_LEAVE.value]
                        )
                    )
                ).all()
            )
        assert kinds == ["room_join", "room_leave"]


# ---------------------------------------------------------------------------
# (d) D5 restore — two-conjunct rule
# ---------------------------------------------------------------------------


class TestRestoreEligibility:
    def _eligibility(self, repo, room_id, member):
        return repo.restore_eligibility(
            room_id=room_id, instance_id=member
        )

    def test_active_room_active_member_eligible(
        self, engine, repo, tree, room_id
    ):
        out = self._eligibility(repo, room_id, tree["child1"])
        assert out["eligible"] is True
        assert out["reason"] == "eligible"
        assert out["room_state"] == "active"
        assert out["member_state"] == "active"

    def test_closed_room_active_member_eligible(
        self, engine, repo, tree, room_id
    ):
        """Conjunct 1 accepts {active, closed}: read-tool restore
        works through the closed window (capability row)."""
        with Session(engine) as s:
            room = s.get(ChatRoom, room_id)
            room.state = "closed"
            s.add(room)
            s.commit()
        out = self._eligibility(repo, room_id, tree["child1"])
        assert out["eligible"] is True
        assert out["room_state"] == "closed"

    def test_left_member_restore_grants_nothing_pin(
        self, engine, repo, tree, room_id
    ):
        """THE PIN: a LEFT member's restore grants nothing — both
        conjuncts required."""
        repo.leave_room(room_id=room_id, instance_id=tree["child1"])
        out = self._eligibility(repo, room_id, tree["child1"])
        assert out["eligible"] is False
        assert out["reason"] == "member_left"
        assert out["member_state"] == "left"

    def test_purged_room_restore_not_found(
        self, engine, repo, tree, room_id
    ):
        repo.purge_room(room_id, reason="test")
        out = self._eligibility(repo, room_id, tree["child1"])
        assert out["eligible"] is False
        assert out["reason"] == "room_not_found"

    def test_never_a_member_not_eligible(self, engine, repo, tree, room_id):
        out = self._eligibility(repo, room_id, tree["child2"])
        assert out["eligible"] is False
        assert out["reason"] == "not_a_member"

    def test_owner_row_eligible_and_untouched_by_leave_restore(
        self, engine, repo, tree, room_id
    ):
        """The owner row is untouched by leave/restore paths: eligible
        while the room exists; leave refuses the owner."""
        out = self._eligibility(repo, room_id, tree["owner"])
        assert out["eligible"] is True
        with pytest.raises(RoomLeaveOwnerError):
            repo.leave_room(room_id=room_id, instance_id=tree["owner"])
        out2 = self._eligibility(repo, room_id, tree["owner"])
        assert out2["eligible"] is True
        assert out2["member_state"] == "active"


# ---------------------------------------------------------------------------
# (e) roster resolution
# ---------------------------------------------------------------------------


class TestRoster:
    def test_roster_carries_role_name_state(
        self, repo, tree, room_id
    ):
        roster = repo.list_members(
            room_id, caller_instance_id=tree["owner"], active_only=True
        )
        by_id = {m["instance_id"]: m for m in roster}
        assert by_id[tree["owner"]]["role"] == "owner"
        assert by_id[tree["owner"]]["member_name"] == "lead"
        assert by_id[tree["owner"]]["state"] == "active"
        assert by_id[tree["child1"]]["role"] == "child"
        assert by_id[tree["child1"]]["member_name"] == "worker-a"

    def test_join_room_payload_shape(self, engine, repo, tree, room_id):
        seed_instance(engine, "shape-1", tree["owner"])
        out = repo.join_room(room_id=room_id, instance_id="shape-1",
                             base_name="scout")
        assert set(out) == {
            "room_id",
            "instance_id",
            "member_name",
            "agent_id",
            "role",
            "joined_at",
            "last_read_seq",
            "last_delivered_seq",
            "state",
        }
        assert out["role"] == "child"
        assert out["state"] == "active"
        assert out["last_read_seq"] == 0
        assert out["last_delivered_seq"] == 0


# ---------------------------------------------------------------------------
# (f) name sanitize ladder (pure)
# ---------------------------------------------------------------------------


class TestSanitize:
    def test_basic_normalization(self):
        assert sanitize_member_name("  Capturer One! ") == "capturer-one"
        assert sanitize_member_name("Log Analyzer") == "log-analyzer"
        assert sanitize_member_name("capture") == "capture"

    def test_unusable_returns_none(self):
        assert sanitize_member_name(None) is None
        assert sanitize_member_name("   ") is None
        assert sanitize_member_name("!!!") is None
        assert sanitize_member_name("") is None

    def test_truncation(self):
        long = "a" * 80
        out = sanitize_member_name(long)
        assert len(out) <= 48
        assert not out.endswith("-")
