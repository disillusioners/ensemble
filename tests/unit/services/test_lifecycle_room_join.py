"""Lifecycle-level coverage of the REAL room-join seam (P2 OPT-1).

Every tool-level test in ``test_spawn_room_join.py`` drives a replica of
the lifecycle's DB half (the sim). This file exercises the ACTUAL
``InstanceLifecycleService._spawn_instance_db_sync`` path on real
file-backed SQLite — the seam where the latent ImportError lived
(``_raise_typed_integrity`` resolved through ``RoomRepository``) and
where the one-tx contract (membership row + instance JSONB keys,
commit/rollback together) either holds or doesn't.

Coverage:
* happy path — membership row + BOTH JSONB keys (``join_room``,
  ``member_name``) committed WITH the instance row, and
  ``_SpawnResult`` carries them back;
* tier-4 suffix on the real seam — the FINAL suffixed name lands in
  BOTH the member row and the instance JSONB (no silent rename);
* forced collision — ``MemberNameCollisionError`` (dual-family) raised
  from the REAL commit, with ZERO orphan instance/membership rows
  (whole-spawn rollback proven on the real seam, not a replica).
"""

from __future__ import annotations

import uuid
from typing import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.event.models  # noqa: F401 — register event
import daemon.repositories.instance.models  # noqa: F401 — register instances
from daemon.repositories.instance.models import Instance
from daemon.repositories.room import RoomRepository
from daemon.repositories.room import membership as room_membership
from daemon.repositories.room.membership import RoomJoinSpec
from daemon.repositories.room.models import ChatRoomMember
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.write_pause_guard import WritePauseGuard


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = create_engine(
        f"sqlite:///{tmp_path}/lifecycle_room_join.db",
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


CALLER = "11111111-1111-1111-1111-111111111111"
TREE_ROOT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def room_id(engine: Engine) -> str:
    repo = RoomRepository(engine)
    return repo.create_room(
        name="alpha",
        tree_root_id=TREE_ROOT,
        owner_instance_id=CALLER,
        owner_member_name="owner",
    )["room"]["room_id"]


def _count_instances(engine: Engine, iid: str) -> int:
    with Session(engine) as s:
        return len(
            s.exec(
                __import__("sqlmodel").select(Instance).where(
                    Instance.instance_id == iid
                )
            ).all()
        )


def _count_members(engine: Engine, iid: str) -> int:
    with Session(engine) as s:
        return len(
            s.exec(
                __import__("sqlmodel").select(ChatRoomMember).where(
                    ChatRoomMember.instance_id == iid
                )
            ).all()
        )


class TestLifecycleRoomJoin:
    def test_happy_path_commits_member_row_and_both_jsonb_keys(
        self, engine, room_id
    ):
        """The REAL seam: membership row + instance JSONB (join_room +
        member_name) commit together, and _SpawnResult carries them
        back (no replica)."""
        lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
        instance_id = str(uuid.uuid4())
        spec = RoomJoinSpec(
            room_id=room_id,
            base_name="scout",
            role="child",
            agent_id="capture",
            tier="parent_provided",
        )
        result = lifecycle._spawn_instance_db_sync(
            engine,
            WritePauseGuard(),
            instance_id=instance_id,
            resolved_agent_id="capture",
            resolved_agent_dir="/agents/capture",
            version_tag=None,
            agent_name="capture",
            parent_id=CALLER,
            project_id=None,
            instance_metadata={},
            room_join=spec,
        )
        assert result.room_id == room_id
        assert result.member_name == "scout"

        with Session(engine) as s:
            inst = s.get(Instance, instance_id)
            assert inst is not None
            assert inst.instance_metadata == {
                "join_room": room_id,
                "member_name": "scout",
            }
            member = s.get(ChatRoomMember, (room_id, instance_id))
            assert member is not None
            assert member.member_name == "scout"
            assert member.role == "child"
            assert member.state == "active"

    def test_tier4_suffix_lands_in_both_member_row_and_jsonb(
        self, engine, room_id
    ):
        """A colliding base name: the tier-4 registry produces
        'scout-1' and that FINAL name lands in BOTH the membership
        row and the instance JSONB (no silent rename on the real
        seam)."""
        # Pre-register 'scout' under a DIFFERENT instance.
        with Session(engine) as s:
            s.add(
                Instance(
                    instance_id="taken-1",
                    agent_id="capture",
                    agent_dir="/agents/capture",
                    parent_id=CALLER,
                    status="idle",
                )
            )
            s.commit()
        repo = RoomRepository(engine)
        repo.join_room(room_id=room_id, instance_id="taken-1", base_name="scout")

        lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
        instance_id = str(uuid.uuid4())
        spec = RoomJoinSpec(
            room_id=room_id,
            base_name="scout",
            role="child",
            agent_id="capture",
            tier="parent_provided",
        )
        result = lifecycle._spawn_instance_db_sync(
            engine,
            WritePauseGuard(),
            instance_id=instance_id,
            resolved_agent_id="capture",
            resolved_agent_dir="/agents/capture",
            version_tag=None,
            agent_name="capture",
            parent_id=CALLER,
            project_id=None,
            instance_metadata={},
            room_join=spec,
        )
        assert result.member_name == "scout-1"
        with Session(engine) as s:
            inst = s.get(Instance, instance_id)
            assert inst.instance_metadata["member_name"] == "scout-1"
            member = s.get(ChatRoomMember, (room_id, instance_id))
            assert member is not None
            assert member.member_name == "scout-1"

    def test_forced_collision_raises_typed_and_rolls_back_whole_spawn(
        self, engine, room_id
    ):
        """Forced UNIQUE(room_id, member_name) fire on the REAL commit:
        MemberNameCollisionError (dual-family) raised; ZERO orphan
        instance/membership rows — the whole spawn rolls back."""
        from daemon.repositories.room.repository import MemberNameCollisionError

        # Pre-register 'scout' under a DIFFERENT instance.
        with Session(engine) as s:
            s.add(
                Instance(
                    instance_id="taken-2",
                    agent_id="capture",
                    agent_dir="/agents/capture",
                    parent_id=CALLER,
                    status="idle",
                )
            )
            s.commit()
        repo = RoomRepository(engine)
        repo.join_room(room_id=room_id, instance_id="taken-2", base_name="scout")

        from sqlalchemy import text as _text

        def _scalar(stmt, **kw):
            with engine.connect() as conn:
                return conn.execute(_text(stmt).bindparams(**kw)).scalar()

        before_instance_count = _scalar("SELECT COUNT(*) FROM instances")
        before_member_count = _scalar(
            "SELECT COUNT(*) FROM chatroom_members WHERE room_id = :r", r=room_id
        )

        # Force the registry to claim 'scout' (already taken) so the
        # INSERT violates UNIQUE — simulating a racer past the lock.
        instance_id = str(uuid.uuid4())
        spec = RoomJoinSpec(
            room_id=room_id,
            base_name="scout",
            role="child",
            agent_id="capture",
            tier="parent_provided",
        )
        lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
        with patch.object(
            room_membership, "resolve_member_name", lambda taken, base: base
        ):
            with pytest.raises(MemberNameCollisionError):
                lifecycle._spawn_instance_db_sync(
                    engine,
                    WritePauseGuard(),
                    instance_id=instance_id,
                    resolved_agent_id="capture",
                    resolved_agent_dir="/agents/capture",
                    version_tag=None,
                    agent_name="capture",
                    parent_id=CALLER,
                    project_id=None,
                    instance_metadata={},
                    room_join=spec,
                )

        # Whole-spawn rollback: no instance row, no membership row.
        assert _count_instances(engine, instance_id) == 0
        assert _count_members(engine, instance_id) == 0
        # Pre-existing rows untouched (the holder survives).
        after_instance_count = _scalar("SELECT COUNT(*) FROM instances")
        after_member_count = _scalar(
            "SELECT COUNT(*) FROM chatroom_members WHERE room_id = :r", r=room_id
        )
        assert after_instance_count == before_instance_count
        assert after_member_count == before_member_count
        # The holder is intact.
        holder = repo.get_member(room_id, "taken-2")
        assert holder is not None and holder["member_name"] == "scout"
