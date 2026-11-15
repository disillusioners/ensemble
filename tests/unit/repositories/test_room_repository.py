"""Behavior matrix for the group-chat P1 room repository.

Real file-backed SQLite (``tmp_path`` + per-connection PRAGMAs — the
F9-parity harness of
``test_message_queue_clear_all_2arm_disjunction.py``; NOT
``StaticPool``/``:memory:``), ``PRAGMA foreign_keys=ON`` so the NO
ACTION FK shape is actually exercised.

Coverage (commission P1 item 6):
  * owner seed at create (D2 — role/state/cursors, room+member one tx);
  * per-room seq monotonicity (1..N gap-free) + the
    ``UNIQUE(room_id, seq)`` backstop present at the DB level;
  * cursor advance monotonicity + the re-derivation predicate
    (``seq > max(last_read_seq, last_delivered_seq)``);
  * the fail-closed read matrix (active member / owner /
    owner-ancestry / left-member / non-member / purged / absent);
  * closed rooms read-only (capability row);
  * purge transactional completeness (3 tables empty, exactly ONE
    content-free ``room_purged`` event, typed not-found after);
  * the boot-regen pinned pair (members incl. OWNER; active+closed;
    purged structurally excluded).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Iterator

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.event.models  # noqa: F401 — register event
import daemon.repositories.instance.models  # noqa: F401 — register instances
from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.instance.models import Instance
from daemon.repositories.room.models import ChatRoom, ChatRoomMember, RoomMessage
from daemon.repositories.room.repository import (
    MemberNameCollisionError,
    RoomClosedError,
    RoomError,
    RoomNotFoundError,
    RoomRepository,
    RoomSeqConflictError,
)


# ---------------------------------------------------------------------------
# F9-parity harness — file-backed SQLite (per-connection PRAGMAs, NullPool)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = create_engine(
        f"sqlite:///{tmp_path}/room_repo_matrix.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> RoomRepository:
    return RoomRepository(engine)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _seed_instance(
    eng: Engine, instance_id: str, parent_id: str | None = None
) -> None:
    with Session(eng) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="ari",
                agent_dir="/agents/ari",
                parent_id=parent_id,
                status="idle",
            )
        )
        s.commit()


def _join_member(
    eng: Engine, room_id: str, instance_id: str, member_name: str
) -> None:
    """Add a child membership row directly (P2 owns the join path)."""
    with Session(eng) as s:
        s.add(
            ChatRoomMember(
                room_id=room_id,
                instance_id=instance_id,
                member_name=member_name,
                agent_id="ari",
                role="child",
                joined_at="2026-11-15T08:00:00+00:00",
                last_read_seq=0,
                last_delivered_seq=0,
                state="active",
            )
        )
        s.commit()


@pytest.fixture
def tree(engine: Engine) -> dict[str, str]:
    """grand-1 (root) → parent-1 (room owner) → {child-1, child-2}."""
    _seed_instance(engine, "grand-1", None)
    _seed_instance(engine, "parent-1", "grand-1")
    _seed_instance(engine, "child-1", "parent-1")
    _seed_instance(engine, "child-2", "parent-1")
    _seed_instance(engine, "outsider-1", None)  # different tree
    return {
        "root": "grand-1",
        "owner": "parent-1",
        "child1": "child-1",
        "child2": "child-2",
        "outsider": "outsider-1",
    }


@pytest.fixture
def room_id(repo: RoomRepository, tree: dict[str, str]) -> str:
    out = repo.create_room(
        name="alpha",
        tree_root_id=tree["root"],
        owner_instance_id=tree["owner"],
        owner_member_name="lead",
    )
    rid = out["room"]["room_id"]
    _join_member(repo._engine, rid, tree["child1"], "worker-a")
    return rid


# ---------------------------------------------------------------------------
# (a) room birth — the D2 owner seed
# ---------------------------------------------------------------------------


def test_create_room_seeds_owner_membership_row(repo, tree, room_id):
    member = repo.get_member(room_id, tree["owner"])
    assert member is not None
    assert member["role"] == "owner"
    assert member["state"] == "active"
    assert member["member_name"] == "lead"
    assert member["last_read_seq"] == 0
    assert member["last_delivered_seq"] == 0  # cursors 0 at birth


def test_owner_row_is_the_room_row_witness(repo, tree, room_id):
    """Room exists ⇔ owner row exists — the same tx minted both."""
    assert repo.get_room(room_id)["owner_instance_id"] == tree["owner"]
    assert repo.get_member(room_id, tree["owner"]) is not None


def test_create_room_mints_uuid_when_absent(repo, tree):
    out = repo.create_room(
        name="beta",
        tree_root_id=tree["root"],
        owner_instance_id=tree["owner"],
    )
    rid = out["room"]["room_id"]
    assert rid == out["owner_member"]["room_id"]
    # parseable UUID
    uuid.UUID(rid)


def test_duplicate_room_id_refused(repo, tree):
    rid = repo.create_room(
        name="dup", tree_root_id=tree["root"], owner_instance_id=tree["owner"]
    )["room"]["room_id"]
    with pytest.raises(RoomError) as excinfo:
        repo.create_room(
            name="dup",
            tree_root_id=tree["root"],
            owner_instance_id=tree["owner"],
            room_id=rid,
        )
    assert "integrity" in str(excinfo.value).lower()


def test_member_name_collision_typed_mapping(repo):
    """The ``UNIQUE(room_id, member_name)`` mapping recognizes BOTH
    driver message shapes (SQLite constraint-columns form + PG
    constraint-name form) — P2's suffix registry depends on the typed
    refusal. R1: fingerprints ONLY — a composite-PK duplicate (either
    driver shape) must NOT map to the name-collision type."""
    sqlite_shaped = IntegrityError(
        "stmt", {},
        Exception(
            "UNIQUE constraint failed: chatroom_members.room_id, "
            "chatroom_members.member_name"
        ),
    )
    with pytest.raises(MemberNameCollisionError):
        repo._raise_typed_integrity(sqlite_shaped, "room-1", "lead")

    pg_shaped = IntegrityError(
        "stmt", {},
        Exception(
            'duplicate key value violates unique constraint '
            '"uq_chatroom_members_room_member_name"'
        ),
    )
    with pytest.raises(MemberNameCollisionError):
        repo._raise_typed_integrity(pg_shaped, "room-1", "lead")

    unrelated = IntegrityError("stmt", {}, Exception("some other violation"))
    with pytest.raises(RoomError) as excinfo:
        repo._raise_typed_integrity(unrelated, "room-1", "lead")
    assert not isinstance(excinfo.value, MemberNameCollisionError)

    # R1 PK-dup legs — a duplicate composite-PK membership insert (the
    # (room_id, instance_id) PK, NOT the name backstop) falls back to
    # the base RoomError on BOTH driver shapes.
    pk_dup_sqlite = IntegrityError(
        "stmt", {},
        Exception(
            "UNIQUE constraint failed: chatroom_members.room_id, "
            "chatroom_members.instance_id"
        ),
    )
    with pytest.raises(RoomError) as excinfo:
        repo._raise_typed_integrity(pk_dup_sqlite, "room-1", "lead")
    assert not isinstance(excinfo.value, MemberNameCollisionError)

    pk_dup_pg = IntegrityError(
        "stmt", {},
        Exception(
            'duplicate key value violates unique constraint '
            '"chatroom_members_pkey"'
        ),
    )
    with pytest.raises(RoomError) as excinfo:
        repo._raise_typed_integrity(pk_dup_pg, "room-1", "lead")
    assert not isinstance(excinfo.value, MemberNameCollisionError)


def test_append_integrity_fk_violation_is_not_seq_conflict(repo):
    """R2 classification pin: ONLY the ``UNIQUE(room_id, seq)``
    fingerprints (named constraint / dotted-pair columns) map to
    :class:`RoomSeqConflictError`. An FK violation — either driver
    shape — must NOT surface as a seq conflict; it falls back to the
    base :class:`RoomError` with the ``from exc`` chain kept."""
    # FK shapes — SQLite bare message + PG named-FK message.
    fk_sqlite = IntegrityError(
        "stmt", {}, Exception("FOREIGN KEY constraint failed")
    )
    with pytest.raises(RoomError) as excinfo:
        repo._raise_typed_append_integrity(fk_sqlite, "room-1")
    assert not isinstance(excinfo.value, RoomSeqConflictError)
    assert excinfo.value.__cause__ is fk_sqlite  # `from exc` chaining kept

    fk_pg = IntegrityError(
        "stmt", {},
        Exception(
            'insert or update on table "room_messages" violates '
            'foreign key constraint "room_messages_room_id_fkey"'
        ),
    )
    with pytest.raises(RoomError) as excinfo:
        repo._raise_typed_append_integrity(fk_pg, "room-1")
    assert not isinstance(excinfo.value, RoomSeqConflictError)

    # The seq-conflict shapes still map typed (non-vacuous control).
    seq_sqlite = IntegrityError(
        "stmt", {},
        Exception(
            "UNIQUE constraint failed: room_messages.room_id, "
            "room_messages.seq"
        ),
    )
    with pytest.raises(RoomSeqConflictError):
        repo._raise_typed_append_integrity(seq_sqlite, "room-1")

    seq_pg = IntegrityError(
        "stmt", {},
        Exception(
            'duplicate key value violates unique constraint '
            '"uq_room_messages_room_seq"'
        ),
    )
    with pytest.raises(RoomSeqConflictError):
        repo._raise_typed_append_integrity(seq_pg, "room-1")


def test_member_name_unique_backstop_at_db_level(engine, repo, tree, room_id):
    """A second member row with the SAME per-room name is structurally
    impossible (the tier-4 registry backstop)."""
    with pytest.raises(Exception) as excinfo:
        _join_member(engine, room_id, tree["child2"], "worker-a")
        _join_member(engine, room_id, tree["child2"] + "-x", "worker-a")
    assert "unique" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# (b) append — per-room seq monotonicity
# ---------------------------------------------------------------------------


def test_seq_monotonic_gap_free(repo, room_id, tree):
    seqs = [
        repo.append_message(
            room_id=room_id,
            author_instance_id=tree["child1"],
            author_name="worker-a",
            text_body=f"m{i}",
        )["seq"]
        for i in range(5)
    ]
    assert seqs == [1, 2, 3, 4, 5]


def test_append_bumps_room_counters(repo, room_id, tree):
    repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="hello",
        tagged_members=["lead"],
    )
    room = repo.get_room(room_id)
    assert room["message_count"] == 1
    assert room["last_post_at"] is not None


def test_append_to_absent_room_typed_not_found(repo):
    with pytest.raises(RoomNotFoundError):
        repo.append_message(
            room_id="no-such-room",
            author_instance_id="x",
            author_name="x",
            text_body="t",
        )


def test_unique_room_seq_backstop_at_db_level(engine, repo, room_id, tree):
    """The DB-level constraint exists and refuses a duplicated
    (room_id, seq) pair — the backstop behind the per-room lock."""
    seq = repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="first",
    )["seq"]
    assert seq == 1
    with pytest.raises(Exception) as excinfo, Session(engine) as session:
        session.execute(
            text(
                "INSERT INTO room_messages (room_id, seq,"
                " author_instance_id, author_name, text, tagged_members,"
                " addressed_members, kind, created_at)"
                " VALUES (:r, :s, :a, :an, :t, '[]', '[]', 'message', :c)"
            ).bindparams(
                r=room_id,
                s=1,
                a=tree["child1"],
                an="worker-a",
                t="dup",
                c="2026-11-15T08:00:00+00:00",
            )
        )
        session.commit()
    assert "unique" in str(excinfo.value).lower()
    # and the seq machinery self-heals: next append continues 2, 3.
    assert (
        repo.append_message(
            room_id=room_id,
            author_instance_id=tree["child1"],
            author_name="worker-a",
            text_body="second",
        )["seq"]
        == 2
    )


def test_room_seq_conflict_error_in_vocabulary():
    """The typed backstop error exists for the (single-daemon-unreachable,
    DB-level) race — RK-13 belt."""
    assert issubclass(RoomSeqConflictError, RoomError)


# ---------------------------------------------------------------------------
# (c) cursor advance — monotonic MAX guards
# ---------------------------------------------------------------------------


def test_cursor_advance_monotonic(repo, room_id, tree):
    assert repo.advance_read_cursor(room_id, tree["owner"], 3) is True
    assert repo.advance_read_cursor(room_id, tree["owner"], 3) is False
    assert repo.advance_read_cursor(room_id, tree["owner"], 1) is False  # never decreases
    member = repo.get_member(room_id, tree["owner"])
    assert member["last_read_seq"] == 3

    assert repo.advance_delivered_cursor(room_id, tree["owner"], 5) is True
    assert repo.advance_delivered_cursor(room_id, tree["owner"], 2) is False
    member = repo.get_member(room_id, tree["owner"])
    assert member["last_delivered_seq"] == 5


def test_cursor_advance_on_missing_member_fails_clean(repo, room_id):
    assert repo.advance_read_cursor(room_id, "ghost", 1) is False


# ---------------------------------------------------------------------------
# (d) the fail-closed read matrix
# ---------------------------------------------------------------------------


def test_read_matrix_active_member(repo, room_id, tree):
    msgs = repo.list_messages(room_id, caller_instance_id=tree["child1"])
    assert isinstance(msgs, list)


def test_read_matrix_owner_member(repo, room_id, tree):
    repo.append_message(
        room_id=room_id,
        author_instance_id=tree["owner"],
        author_name="lead",
        text_body="hi",
    )
    assert len(repo.list_messages(room_id, caller_instance_id=tree["owner"])) == 1


def test_read_matrix_owner_ancestry(repo, room_id, tree):
    repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="from child",
    )
    # grand-1 is two hops above the owner — pure ancestry, NO member row.
    assert repo.get_member(room_id, tree["root"]) is None
    msgs = repo.list_messages(room_id, caller_instance_id=tree["root"])
    assert len(msgs) == 1
    # unread for a pure-ancestry caller is 0 (no cursor row — member concept)
    assert repo.unread_count(room_id, caller_instance_id=tree["root"]) == 0


def test_read_matrix_left_member_is_not_found(repo, room_id, tree):
    with Session(repo._engine) as s:
        row = s.get(ChatRoomMember, (room_id, tree["child1"]))
        row.state = "left"
        s.add(row)
        s.commit()
    with pytest.raises(RoomNotFoundError):
        repo.list_messages(room_id, caller_instance_id=tree["child1"])
    with pytest.raises(RoomNotFoundError):
        repo.list_members(room_id, caller_instance_id=tree["child1"])


def test_read_matrix_left_ancestor_member_reads_via_ancestry(
    repo, room_id, tree
):
    """PIN (read-predicate docstring, repository.py ~:350-353): the
    owner-ancestry read scope is INDEPENDENT of membership state — a
    member who LEFT the room but sits in the OWNER'S ANCESTRY CHAIN
    still reads via the chain (the chain is scope, membership is
    roster; a typed not-found here would be WRONG).

    B is the owner's ANCESTOR (grand-1, one hop above owner parent-1):
    the chain walk goes UP via ``instances.parent_id``, so a left
    DESCENDANT of the owner is NOT covered — the contrasting case
    (left NON-ancestor member ⇒ typed not-found) is already pinned by
    ``test_read_matrix_left_member_is_not_found`` (referenced, not
    duplicated).
    """
    repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="ancestor still reads",
    )
    _join_member(repo._engine, room_id, tree["root"], "grand-watcher")
    with Session(repo._engine) as s:
        row = s.get(ChatRoomMember, (room_id, tree["root"]))
        row.state = "left"
        s.add(row)
        s.commit()
    # the LEFT row kills the member lane (get_member is a raw row read)
    assert repo.get_member(room_id, tree["root"])["state"] == "left"
    # ...but the ancestry chain still admits the read.
    msgs = repo.list_messages(room_id, caller_instance_id=tree["root"])
    assert len(msgs) == 1


def test_read_matrix_non_member_is_not_found(repo, room_id, tree):
    for caller in (tree["outsider"], tree["child2"], "ghost-id"):
        with pytest.raises(RoomNotFoundError):
            repo.list_messages(room_id, caller_instance_id=caller)


def test_read_matrix_purged_room_is_not_found(repo, room_id, tree):
    repo.purge_room(room_id, reason="test")
    for caller in (tree["owner"], tree["child1"], tree["root"]):
        with pytest.raises(RoomNotFoundError):
            repo.list_messages(room_id, caller_instance_id=caller)


def test_read_matrix_absent_room_is_not_found(repo):
    with pytest.raises(RoomNotFoundError):
        repo.list_messages("no-such-room", caller_instance_id="anyone")


def test_read_matrix_roster_query(repo, room_id, tree):
    roster = repo.list_members(room_id, caller_instance_id=tree["root"])
    names = {m["member_name"] for m in roster}
    assert names == {"lead", "worker-a"}
    roles = {m["role"] for m in roster}
    assert roles == {"owner", "child"}


def test_list_messages_rejects_non_positive_limit(repo, room_id, tree):
    """R3 entry bounds pin (house precedent: shared_meta_kv P0-1): a
    non-positive limit is refused with ValueError BEFORE any DB work
    (bounds, not access — an authorized caller is the subject here)."""
    for bad in (0, -1, -200):
        with pytest.raises(ValueError, match="limit"):
            repo.list_messages(room_id, caller_instance_id=tree["owner"], limit=bad)


# ---------------------------------------------------------------------------
# closed rooms — capability row (read-only)
# ---------------------------------------------------------------------------


def _close_room(repo: RoomRepository, room_id: str) -> None:
    with Session(repo._engine) as s:
        row = s.get(ChatRoom, room_id)
        row.state = "closed"
        row.closed_at = "2026-11-15T09:00:00+00:00"
        s.add(row)
        s.commit()


def test_closed_room_refuses_posts(repo, room_id, tree):
    _close_room(repo, room_id)
    with pytest.raises(RoomClosedError):
        repo.append_message(
            room_id=room_id,
            author_instance_id=tree["child1"],
            author_name="worker-a",
            text_body="late",
        )


def test_closed_room_still_reads(repo, room_id, tree):
    repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="before close",
        tagged_members=["lead"],
    )
    _close_room(repo, room_id)
    assert len(repo.list_messages(room_id, caller_instance_id=tree["owner"])) == 1
    assert repo.unread_count(room_id, caller_instance_id=tree["owner"]) == 1


# ---------------------------------------------------------------------------
# (e) re-derivation predicate + the boot pinned pair
# ---------------------------------------------------------------------------


def test_rederivation_predicate_and_owner_inclusion(repo, room_id, tree):
    """seq > max(last_read, last_delivered); TAGGED rows only; the
    OWNER participates (D2)."""
    s1 = repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="tagged for all",
        tagged_members=["lead"],
    )["seq"]
    repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="untagged",
    )
    s3 = repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="tagged for worker",
        tagged_members=["worker-a"],
    )["seq"]

    # Owner read up to s1 → the substrate candidate set is
    # (tagged ∧ above-cursor) WITHOUT target resolution (P3 owns
    # targeting): s1 is now covered by the read cursor, s3 (tagged,
    # above) remains a candidate for the owner; the router filters
    # targeting later.
    repo.advance_read_cursor(room_id, tree["owner"], s1)
    pending = repo.derive_undelivered_tagged()
    owner_pending = [p for p in pending if p["instance_id"] == tree["owner"]]
    assert sorted(p["seq"] for p in owner_pending) == [s3]

    # child-1 authored both — no cursor advance → s1 (tagged) pending,
    # s2 (UNTAGGED) excluded by the tag filter, s3 tagged pending.
    child_pending = [
        p for p in pending if p["instance_id"] == tree["child1"]
    ]
    assert sorted(p["seq"] for p in child_pending) == [s1, s3]


def test_rederivation_delivered_cursor_gates(repo, room_id, tree):
    s1 = repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="tagged",
        tagged_members=["lead"],
    )["seq"]
    assert repo.derive_undelivered_tagged()  # pending above 0/0
    repo.advance_delivered_cursor(room_id, tree["owner"], s1)
    owner_rows = [
        p
        for p in repo.derive_undelivered_tagged()
        if p["instance_id"] == tree["owner"]
    ]
    assert owner_rows == []


def test_rederivation_covers_closed_not_purged(repo, room_id, tree, engine):
    rid2 = repo.create_room(
        name="gamma",
        tree_root_id=tree["root"],
        owner_instance_id=tree["owner"],
        owner_member_name="lead2",
    )["room"]["room_id"]
    repo.append_message(
        room_id=rid2,
        author_instance_id=tree["owner"],
        author_name="lead2",
        text_body="tagged in closed",
        tagged_members=["lead2"],
    )
    _close_room(repo, rid2)
    repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="tagged in active",
        tagged_members=["worker-a"],
    )

    pending_rooms = {p["room_id"] for p in repo.derive_undelivered_tagged()}
    assert room_id in pending_rooms  # active
    assert rid2 in pending_rooms  # closed — still covered until purge

    repo.purge_room(rid2, reason="test-close-purge")
    pending_rooms = {p["room_id"] for p in repo.derive_undelivered_tagged()}
    assert rid2 not in pending_rooms  # purged = structurally absent


def test_rederivation_skips_left_members(repo, room_id, tree):
    repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="tagged",
        tagged_members=["worker-a"],
    )
    with Session(repo._engine) as s:
        row = s.get(ChatRoomMember, (room_id, tree["child1"]))
        row.state = "left"
        s.add(row)
        s.commit()
    pending_members = {
        p["instance_id"] for p in repo.derive_undelivered_tagged()
    }
    assert tree["child1"] not in pending_members


# ---------------------------------------------------------------------------
# (f) purge — the R2 transaction
# ---------------------------------------------------------------------------


def test_purge_transactional_completeness(repo, engine, room_id, tree):
    other = repo.create_room(
        name="keepme",
        tree_root_id=tree["root"],
        owner_instance_id=tree["owner"],
        owner_member_name="lead2",
    )["room"]["room_id"]
    for _ in range(3):
        repo.append_message(
            room_id=room_id,
            author_instance_id=tree["child1"],
            author_name="worker-a",
            text_body="doomed content xyzzy",
        )
    repo.append_message(
        room_id=other,
        author_instance_id=tree["owner"],
        author_name="lead2",
        text_body="survivor",
    )

    counts = repo.purge_room(room_id, reason="p_close_test")
    assert counts["messages_purged"] == 3
    assert counts["members_purged"] == 2  # owner + child-1
    assert counts["reason"] == "p_close_test"

    with Session(engine) as s:
        assert s.exec(
            select(RoomMessage).where(RoomMessage.room_id == room_id)
        ).all() == []
        assert s.exec(
            select(ChatRoomMember).where(ChatRoomMember.room_id == room_id)
        ).all() == []
        assert s.get(ChatRoom, room_id) is None
        # the OTHER room is untouched
        assert s.get(ChatRoom, other) is not None
        assert len(
            s.exec(
                select(RoomMessage).where(RoomMessage.room_id == other)
            ).all()
        ) == 1

    # subsequent reads: typed not-found for EVERY predicate class
    for caller in (tree["owner"], tree["child1"], tree["root"]):
        with pytest.raises(RoomNotFoundError):
            repo.list_messages(room_id, caller_instance_id=caller)


def test_purge_emits_exactly_one_content_free_event(repo, engine, room_id, tree):
    repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="SECRET-CONTENT-MARKER",
        tagged_members=["lead"],
    )
    repo.purge_room(room_id, reason="p_idle_test")

    with Session(engine) as s:
        events = s.exec(
            select(Event).where(Event.kind == EventKind.ROOM_PURGED.value)
        ).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.instance_id == tree["owner"]  # rides the owner's stream
    data = json.loads(ev.data or "{}")
    # ids + counts ONLY — EXACT key-set equality (§2.1/§2.2 pin): no
    # room_name / room_state_at_purge / reason may ride the event, and
    # no message content may survive the purge.
    assert set(data.keys()) == {
        "room_id",
        "owner_instance_id",
        "tree_root_id",
        "messages_purged",
        "members_purged",
    }
    assert data["room_id"] == room_id
    assert data["owner_instance_id"] == tree["owner"]
    assert data["tree_root_id"] == tree["root"]
    assert data["messages_purged"] == 1
    assert data["members_purged"] == 2
    assert "SECRET-CONTENT-MARKER" not in (ev.data or "")
    assert "SECRET-CONTENT-MARKER" not in str(data)


def test_purge_absent_room_typed_not_found(repo):
    with pytest.raises(RoomNotFoundError):
        repo.purge_room("no-such-room", reason="test")


def test_purge_is_idempotent_via_typed_not_found(repo, room_id):
    repo.purge_room(room_id, reason="first")
    with pytest.raises(RoomNotFoundError):
        repo.purge_room(room_id, reason="second")


# ---------------------------------------------------------------------------
# unread counts
# ---------------------------------------------------------------------------


def test_unread_count_follows_read_cursor(repo, room_id, tree):
    for i in range(3):
        repo.append_message(
            room_id=room_id,
            author_instance_id=tree["child1"],
            author_name="worker-a",
            text_body=f"m{i}",
        )
    assert repo.unread_count(room_id, caller_instance_id=tree["owner"]) == 3
    repo.advance_read_cursor(room_id, tree["owner"], 2)
    assert repo.unread_count(room_id, caller_instance_id=tree["owner"]) == 1
    repo.advance_read_cursor(room_id, tree["owner"], 3)
    assert repo.unread_count(room_id, caller_instance_id=tree["owner"]) == 0


def test_unread_count_closed_room_still_computes(repo, room_id, tree):
    repo.append_message(
        room_id=room_id,
        author_instance_id=tree["child1"],
        author_name="worker-a",
        text_body="m",
    )
    _close_room(repo, room_id)
    assert repo.unread_count(room_id, caller_instance_id=tree["owner"]) == 1


def test_unread_count_fails_closed_for_non_member(repo, room_id):
    with pytest.raises(RoomNotFoundError):
        repo.unread_count(room_id, caller_instance_id="outsider")


# ---------------------------------------------------------------------------
# event-kind registry discipline (P1 emits ONLY room_purged)
# ---------------------------------------------------------------------------


def test_p1_emits_only_room_purged_kind(repo, engine, room_id):
    """The full P1 surface runs; the ONLY room_* event kind written is
    room_purged (registry-before-emission — the rest are P3+)."""
    repo.append_message(
        room_id=room_id,
        author_instance_id="x",
        author_name="x",
        text_body="t",
    )
    repo.advance_read_cursor(room_id, "x", 1)
    repo.derive_undelivered_tagged()
    with pytest.raises(RoomNotFoundError):
        repo.list_messages("absent", caller_instance_id="x")
    repo.purge_room(room_id, reason="test")
    with Session(engine) as s:
        kinds = {
            row
            for row in s.exec(
                select(Event.kind).where(Event.kind.startswith("room_"))
            ).all()
        }  # SQLModel exec squashes single-column selects to scalars
    assert kinds == {"room_purged"}
