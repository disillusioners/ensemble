"""Shared room-matrix harness for the group-chat P2 test packs.

Dedupe of the copy-pasted P2 harness blocks (mechanical fold pass,
2026-11-15): the real file-backed SQLite engine with the
per-connection PRAGMAs (``foreign_keys=ON`` / WAL / ``busy_timeout`` —
the F9-parity P1 shape, NOT ``StaticPool``/``:memory:``), the seeding
helpers, the generic row counter, the room-birth helper, and the
spawn-sim builder replicating the lifecycle's ONE-transaction join.

Consumers: ``tests/unit/repositories/test_room_membership.py``,
``tests/unit/tools/test_spawn_room_join.py``,
``tests/unit/services/test_lifecycle_room_join.py``.

Style follows the existing ``tests/helpers`` convention (plain
functions; the pytest fixtures stay in the test files and delegate
here — see ``checkpoint_prune_pg.py`` for the sibling pattern).
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.event.models  # noqa: F401 — register event
import daemon.repositories.instance.models  # noqa: F401 — register instances
from daemon.repositories.instance.models import Instance
from daemon.repositories.room import membership as room_membership
from daemon.repositories.room.models import ChatRoomMember
from daemon.repositories.room.repository import RoomRepository

__all__ = [
    "make_room_engine",
    "seed_instance",
    "join_member",
    "count_rows",
    "make_room",
    "_make_spawn_sim",
]


def make_room_engine(tmp_path, name: str) -> Engine:
    """Real file-backed SQLite engine under ``tmp_path`` with the
    per-connection PRAGMAs (``foreign_keys=ON``, WAL, 10s busy
    timeout) and all room/instance/event models registered via
    ``create_all``. The caller owns disposal (the ``yield`` +
    ``dispose`` fixture pattern in the test files)."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/{name}",
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
    return eng


def seed_instance(
    eng: Engine, instance_id: str, parent_id: str | None = None
) -> None:
    """Insert one Instance row (agent ``ari``, idle)."""
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


def join_member(
    eng: Engine, room_id: str, instance_id: str, member_name: str
) -> None:
    """Direct membership-row insert (bypasses the registry — seeding)."""
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


def count_rows(eng: Engine, model: Any, **filters: Any) -> int:
    """``len(SELECT * WHERE **filters)`` — the generic row counter."""
    with Session(eng) as s:
        stmt = select(model)
        for attr, value in filters.items():
            stmt = stmt.where(getattr(model, attr) == value)
        return len(s.exec(stmt).all())


def make_room(
    repo: RoomRepository,
    *,
    tree_root: str,
    owner: str,
    name: str = "alpha",
    owner_member_name: str = "owner",
) -> str:
    """Room birth through the repository (the owner name rides the
    naming registry) → the new room_id."""
    return repo.create_room(
        name=name,
        tree_root_id=tree_root,
        owner_instance_id=owner,
        owner_member_name=owner_member_name,
    )["room"]["room_id"]


def _make_spawn_sim(
    engine: Engine,
    calls: list[dict],
    *,
    commit_backstop: bool = False,
) -> Callable[..., tuple[str, None]]:
    """Stub ``manager.spawn_instance`` replicating the lifecycle's
    ONE-transaction join: membership row (suffix registry inside the
    tx) + Instance row with the JSONB join keys, committed together.
    Returns the instance id (the tool reads back the member row).

    ``commit_backstop=True`` adds the step-4 commit classifier
    (``IntegrityError → RoomRepository._raise_typed_integrity``), so a
    UNIQUE(room_id, member_name) fire at commit surfaces exactly as it
    does in production — typed ``MemberNameCollisionError`` raised OUT
    of ``manager.spawn_instance``. A spec-less (legacy-path) call to
    the backstop variant asserts — it has no join to classify.
    """

    def _sim(**kwargs) -> tuple[str, None]:
        calls.append(kwargs)
        iid = kwargs["instance_id"]
        spec = kwargs.get("room_join")
        if spec is None:
            assert not commit_backstop, (
                "commit-backstop sim requires a room_join spec"
            )
            return iid, None
        with Session(engine) as s:
            row = room_membership.join_room_in_session(
                s,
                room_id=spec.room_id,
                instance_id=iid,
                base_name=spec.base_name,
                role=spec.role,
                agent_id=spec.agent_id,
            )
            final_name = row.member_name
            s.add(
                Instance(
                    instance_id=iid,
                    agent_id=kwargs.get("agent_id") or "capture",
                    agent_dir="/agents/capture",
                    parent_id=kwargs.get("parent_id"),
                    status="idle",
                    instance_metadata={
                        "join_room": spec.room_id,
                        "member_name": final_name,
                    },
                )
            )
            try:
                s.commit()
            except IntegrityError as exc:
                if commit_backstop:
                    RoomRepository._raise_typed_integrity(
                        exc, spec.room_id, spec.base_name
                    )
                raise
        return iid, None

    return _sim
