"""Regression pins for the E7 "no checkpoints rows" leg of the
never-dispatched-husk detector (D1) and the binding cross-check (D2).

E7 is the THIRD evidence leg: a row that has ANY row in the
``checkpoints`` table for its thread has executed a turn and is
therefore NOT a never-dispatched husk, even when every other evidence
leg (E1 no task rows, E2 no message_queue rows, E3 no job_queue_items
rows, non-terminal, spawn-stamp-only activity, age > threshold, no
children, no question pack, no pause marker) is satisfied. This closes
the [168 h, infinity) residual band where the other evidence has aged
out but a checkpoint row still proves a turn ran.

Graceful degradation: the ``checkpoints`` table is absent in the SQLite
unit-test fixtures (SQLModel.metadata does not create it). When the
table is absent the leg cannot be evaluated; the detector FAILS CLOSED
(nothing is flagged) and the payload reports ``evidence_legs ==
"degraded"``. A missing proof never produces a flag.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.tools import instance_read as ir
from tests.helpers.nestops_pg_fixtures import call, install_fake_home


@pytest.fixture(autouse=True)
def _fake_home_fence(monkeypatch, tmp_path):
    install_fake_home(monkeypatch, tmp_path)


def _aged(days: int = 30) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _instance(instance_id, *, created_at=None, last_activity_at=None, status="idle", version=1):
    if created_at is None:
        created_at = _aged()
    return Instance(
        instance_id=instance_id,
        agent_id="worker",
        agent_dir="/tmp/agent",
        status=status,
        version=version,
        created_at=created_at.isoformat(),
        updated_at=created_at.isoformat(),
        last_activity_at=last_activity_at,
        metadata={},
    )


class _FakeTaskRepo:
    def __init__(self):
        self._tasks = {}

    def get_by_instance(self, instance_id):
        return self._tasks.get(instance_id, [])


class _FakeQuestionManager:
    def __init__(self):
        self._packs = {}

    def get_question_pack(self, instance_id):
        return self._packs.get(instance_id)


class _RepoBackedManager:
    def __init__(self, engine):
        self.engine = engine
        self._instance_repository = SQLModelInstanceRepository(engine)
        self._task_repo = _FakeTaskRepo()
        self._question_manager = _FakeQuestionManager()

    async def get_instance(self, instance_id):
        return object()

    async def ensure_mcp_preloaded(self, instance_id):
        return []

    def find_near_instance(self, instance_id, max_distance=7):
        return []

    def list_instances(self, limit=10, offset=0, status=None, **kw):
        instances, total, truncated = self._instance_repository.list(
            limit=limit, offset=offset, status=status,
            exclude_kb=kw.get("exclude_kb", True),
        )
        result = []
        for inst in instances:
            info = inst.to_dict()
            info["children"] = self._instance_repository.list_child_ids_permanent(
                inst.instance_id
            )
            result.append(info)
        return result, total, truncated


def _read_tools(manager):
    return ir.create_instance_read_tools(manager, current_instance_id="caller", agent_id="maintenancer")


def _flagged_ids(payload):
    for d in payload["detectors"]:
        if d["detector"] == "never-dispatched-husk":
            return {r["instance_id"] for r in d["instances"]}
    raise AssertionError("husk detector missing")


def _husk_row(payload):
    for d in payload["detectors"]:
        if d["detector"] == "never-dispatched-husk":
            return d
    raise AssertionError("husk detector missing")


@pytest.mark.asyncio
async def test_e7_row_with_checkpoint_rows_is_not_flagged():
    """P1: a row that HAS checkpoint rows is NOT flagged even when every
    other evidence leg is satisfied. The checkpoints table is present in
    this fixture (created explicitly) so E7 can be evaluated."""
    tmpdir = tempfile.mkdtemp(prefix="husk-e7-ckpt-")
    engine = create_engine(f"sqlite:///{tmpdir}/fleet.db")
    SQLModel.metadata.create_all(engine)
    # Create the checkpoints table (LangGraph schema, thread_id = instance_id).
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE checkpoints ("
            "thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', "
            "checkpoint_id TEXT NOT NULL, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))"
        ))
        conn.commit()

    created = _aged(30)
    row = _instance("husk-with-ckpt-1", created_at=created, last_activity_at=None)
    ids = []
    with Session(engine) as session:
        session.add(row)
        session.commit()
    ids.append("husk-with-ckpt-1")
    # Insert a checkpoint row for this thread — proof a turn ran.
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id) "
            "VALUES ('husk-with-ckpt-1', '', 'cp-1')"
        ))
        conn.commit()

    manager = _RepoBackedManager(engine)
    payload = await call(_read_tools(manager), "instance_health")

    assert "husk-with-ckpt-1" not in _flagged_ids(payload)
    # Table present => E7 evaluates => not degraded (other probes healthy).
    assert payload["evidence_legs"] == "ok"
    engine.dispose()


@pytest.mark.asyncio
async def test_e7_graceful_degradation_table_absent():
    """P2: with the checkpoints table absent, nothing is flagged and the
    payload shows the leg is unavailable (evidence_legs == 'degraded')."""
    tmpdir = tempfile.mkdtemp(prefix="husk-e7-degrade-")
    engine = create_engine(f"sqlite:///{tmpdir}/fleet.db")
    SQLModel.metadata.create_all(engine)
    # No checkpoints table created.

    created = _aged(30)
    with Session(engine) as session:
        session.add(_instance("husk-no-table-1", created_at=created, last_activity_at=None))
        session.commit()

    manager = _RepoBackedManager(engine)
    payload = await call(_read_tools(manager), "instance_health")

    assert "husk-no-table-1" not in _flagged_ids(payload)
    assert payload["evidence_legs"] == "degraded"
    assert _husk_row(payload)["evidence_legs"] == "degraded"
    engine.dispose()


@pytest.mark.asyncio
async def test_e7_row_without_checkpoint_rows_can_still_be_flagged():
    """Companion: when the checkpoints table is present but a row has NO
    checkpoint rows, and every other leg is satisfied, it IS flagged."""
    tmpdir = tempfile.mkdtemp(prefix="husk-e7-nockpt-")
    engine = create_engine(f"sqlite:///{tmpdir}/fleet.db")
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE checkpoints ("
            "thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', "
            "checkpoint_id TEXT NOT NULL, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))"
        ))
        conn.commit()

    created = _aged(30)
    with Session(engine) as session:
        session.add(_instance("husk-no-ckpt-2", created_at=created, last_activity_at=None))
        session.commit()

    manager = _RepoBackedManager(engine)
    payload = await call(_read_tools(manager), "instance_health")

    assert "husk-no-ckpt-2" in _flagged_ids(payload)
    assert payload["evidence_legs"] == "ok"
    engine.dispose()
