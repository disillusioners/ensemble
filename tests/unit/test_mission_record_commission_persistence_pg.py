"""PG-backed regression test — the send_message commission must be DURABLE.

Defect class (mission-records PR1 release gate 2026-10-25, item 🔴1)
----------------------------------------------------------------------
``daemon/tools/instance.py::_register_child_completion_watcher`` opened a
``WriteGuardSession`` and minted BOTH the ``mission_records`` row and the
``mission_commissions`` row inside it (``session=session`` — the
"commission rides the watcher's SAME transaction" contract). The block
contained NO commit, and ``WriteGuardSession.__exit__`` closes the
session, which ROLLS BACK the still-open transaction. The success log
fired anyway, so the log reported a commission id that never became
durable: a live PostgreSQL E2E showed ``mission_commissions = 0`` rows
behind a logged ``COMMISSION lane=send_message … commission=…``.

Why THIS test is PostgreSQL-backed (SQLite is NOT coverage here)
------------------------------------------------------------------
pysqlite's SAVEPOINT RELEASE commits the enclosing transaction when
there is no outer BEGIN — the very same code path leaves the rows
VISIBLE on SQLite, so the defect is structurally invisible to the
SQLite unit suite (that is exactly why the suite stayed green while
production lost the rows). Only a real PostgreSQL session — where the
transaction survives until an explicit COMMIT — reproduces the class.
``test_sqlite_masks_the_commission_rollback_class`` documents that
dialect artifact explicitly.

The test drives the REAL production helper
(:func:`daemon.tools.instance._register_child_completion_watcher`) over
a REAL PostgreSQL session — not a replica of its session lifecycle — so
removing the durability boundary in the production code turns this test
RED again.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, select

# Model imports register the tables on ``SQLModel.metadata`` BEFORE
# ``create_all`` (tests/postgres/conftest.py convention).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.models import Instance
from daemon.repositories.mission_record.models import (
    MissionCommission,
    MissionRecord,
)
from daemon.repositories.task.models import Task
from daemon.repositories.task.repository import TaskRepository
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordMode,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)
from daemon.tools.instance import _register_child_completion_watcher
from daemon.write_pause_guard import WritePauseGuard

# ── PG scratch coordinates (PG_TEST_* ONLY — never ambient POSTGRES_*) ──
# The ambient shell carries ``POSTGRES_DB=ensemble_prod`` (env-poison
# class): reading it would point this fixture at a live database, so
# only the ``PG_TEST_*`` family is consulted here (same contract as
# ``tests/test_prompt_version_resolution.py``).
_PG_HOST = os.environ.get("PG_TEST_HOST", "127.0.0.1")
_PG_PORT = os.environ.get("PG_TEST_PORT", "5432")
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "")
_PG_MAINT_DB = os.environ.get("PG_TEST_MAINT_DB", "postgres")
_PG_SCRATCH_PREFIX = "ensemble_mrpr1_commission_"

#: Live databases this fixture must NEVER touch (hard abort, not skip).
_PG_FORBIDDEN_DBS = frozenset({"ensemble_prod", "ensemble_dev"})


def _pg_admin_engine():
    """Maintenance engine (AUTOCOMMIT) for CREATE/DROP DATABASE.

    Returns ``(engine, None)`` on success or ``(None, skip_reason)`` when
    PostgreSQL is unreachable (the caller skips loudly — a PG skip must
    never be mistaken for a green run).
    """
    if _PG_MAINT_DB in _PG_FORBIDDEN_DBS:
        pytest.exit(
            f"HARD ABORT: commission-persistence fixture resolved "
            f"maintenance DB {_PG_MAINT_DB!r} — refusing to point the "
            f"test at a live database.",
            returncode=1,
        )
    url = (
        f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
        f"@{_PG_HOST}:{_PG_PORT}/{_PG_MAINT_DB}"
    )
    engine = create_engine(url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — unreachable ⇒ clean skip
        engine.dispose()
        return None, f"PostgreSQL unreachable at {url}: {exc}"
    return engine, None


@pytest.fixture
def pg_scratch_engine() -> Iterator[tuple[Engine, str]]:
    """A DEDICATED scratch PostgreSQL database, created and dropped.

    Never writes into ``ensemble_dev`` / ``ensemble_prod`` — the scratch
    database is created from a maintenance connection and dropped at
    teardown.
    """
    admin, skip_reason = _pg_admin_engine()
    if admin is None:
        pytest.skip(skip_reason)

    scratch_db = f"{_PG_SCRATCH_PREFIX}{uuid.uuid4().hex[:8]}"
    assert scratch_db not in _PG_FORBIDDEN_DBS
    engine: Engine | None = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch_db}"'))
        scratch_url = (
            f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
            f"@{_PG_HOST}:{_PG_PORT}/{scratch_db}"
        )
        engine = create_engine(scratch_url, pool_pre_ping=True, future=True)
        SQLModel.metadata.create_all(engine)
        yield engine, scratch_db
    finally:
        if engine is not None:
            engine.dispose()
        try:
            with admin.connect() as conn:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :db AND pid <> pg_backend_pid()"
                    ),
                    {"db": scratch_db},
                )
                conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_db}"'))
        except Exception:  # pragma: no cover — best-effort cleanup
            pass
        admin.dispose()


class _StubManager:
    """Minimal ``InstanceManager`` stand-in for the watcher helper.

    The helper only touches ``_task_repo``, ``engine``, ``write_guard``
    and ``mission_record_service``.
    """

    def __init__(
        self,
        engine: Engine,
        task_repo: TaskRepository,
        service: MissionRecordService,
    ) -> None:
        self.engine = engine
        self._task_repo = task_repo
        self.mission_record_service = service
        self.write_guard = WritePauseGuard()


@pytest.fixture
def mission_records_dry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the mode ladder to ``dry`` (mints write) for the test."""
    monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, MissionRecordMode.DRY)
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


async def test_send_message_commission_rows_persist_after_block_exit(
    pg_scratch_engine, mission_records_dry
):
    """The commission AND the mission row survive the watcher block.

    RED against the unfixed code (rows rolled back by the session exit),
    GREEN once the durability boundary commits the block's transaction.
    """
    engine, scratch_db = pg_scratch_engine
    assert scratch_db.startswith(_PG_SCRATCH_PREFIX)

    parent_id = f"mrpr1-parent-{uuid.uuid4().hex[:8]}"
    child_id = f"mrpr1-child-{uuid.uuid4().hex[:8]}"
    message_id = f"mrpr1-msg-{uuid.uuid4().hex[:8]}"

    with Session(engine) as seed:
        seed.add(
            Instance(
                instance_id=parent_id,
                agent_id="agent-x",
                agent_dir="/tmp/agent-x",
                agent_name="x",
                status="idle",
            )
        )
        seed.add(
            Instance(
                instance_id=child_id,
                agent_id="agent-x",
                agent_dir="/tmp/agent-x",
                agent_name="x",
                status="idle",
                parent_id=parent_id,
            )
        )
        task = Task(instance_id=child_id, message_id=message_id)
        seed.add(task)
        seed.commit()

    service = MissionRecordService(engine)
    # The REAL task repository (its ``get_by_message`` opens its own
    # session — the production shape).
    manager = _StubManager(engine, TaskRepository(engine), service)

    bus = DependencyBus(DependencyWatcherRepository(engine))
    await bus.start()
    set_dependency_bus(bus)
    try:
        result = await _register_child_completion_watcher(
            manager, parent_id, child_id, message_id
        )
    finally:
        set_dependency_bus(None)
        await bus.stop()

    # The block ran to completion (no watcher error).
    assert result is None

    # ── the durability assertions (read through a FRESH session) ──────────
    with Session(engine) as verify:
        commissions = verify.exec(
            select(MissionCommission).where(
                MissionCommission.commissioner_instance_id == parent_id,
                MissionCommission.worker_instance_id == child_id,
                MissionCommission.lane == "send_message",
            )
        ).all()
        missions = verify.exec(
            select(MissionRecord).where(
                MissionRecord.anchor_instance_id == parent_id
            )
        ).all()

    assert len(commissions) == 1, (
        "the send_message commission row was rolled back — the mission "
        f"record and commission are NOT durable (scratch={scratch_db})"
    )
    assert len(missions) == 1, (
        "the mission_records row was rolled back — the mission is NOT "
        f"durable (scratch={scratch_db})"
    )
    assert commissions[0].mission_id == missions[0].mission_id
    assert commissions[0].state == "outstanding"


def test_sqlite_masks_the_commission_rollback_class(tmp_path):
    """SQLite CANNOT cover the defect class — it commits on savepoint RELEASE.

    On pysqlite a SAVEPOINT RELEASE commits the enclosing transaction
    when there is no outer BEGIN, so the rows land even though the
    production block never commits. This test documents the dialect
    artifact so nobody mistakes a green SQLite run for coverage of the
    durability boundary.
    """
    from daemon.write_pause_guard import WriteGuardSession

    engine = create_engine(f"sqlite:///{tmp_path}/mask.db", future=True)
    SQLModel.metadata.create_all(engine)
    service = MissionRecordService(engine)
    parent_id = "mask-parent"
    child_id = "mask-child"
    with Session(engine) as seed:
        seed.add(
            Instance(
                instance_id=parent_id,
                agent_id="agent-x",
                agent_dir="/tmp/agent-x",
                agent_name="x",
                status="idle",
            )
        )
        seed.add(
            Instance(
                instance_id=child_id,
                agent_id="agent-x",
                agent_dir="/tmp/agent-x",
                agent_name="x",
                status="idle",
                parent_id=parent_id,
            )
        )
        seed.commit()

    from daemon.services.mission_record_service import (
        mint_commission_fail_open,
        resolve_or_mint_fail_open,
    )

    _reset_mission_records_mode_for_tests()
    try:
        with WriteGuardSession(Session(engine), WritePauseGuard()) as session:
            mission_id = resolve_or_mint_fail_open(
                service, parent_id, "send_message", session=session
            )
            commission_id = mint_commission_fail_open(
                service,
                mission_id,
                commissioner_instance_id=parent_id,
                worker_instance_id=child_id,
                lane="send_message",
                session=session,
            )
    finally:
        _reset_mission_records_mode_for_tests()

    with Session(engine) as verify:
        rows = verify.exec(
            select(MissionCommission).where(
                MissionCommission.commission_id == commission_id
            )
        ).all()
    assert len(rows) == 1, (
        "SQLite no longer masks the class — if this starts failing, the "
        "dialect artifact changed and the PG test above is the only "
        "remaining coverage for the durability boundary"
    )
