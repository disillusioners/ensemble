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
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.task.models import Task
from tests.helpers.checkpoints_fixture import (
    create_checkpoints_table,
    insert_checkpoint_row,
)
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
    create_checkpoints_table(engine)

    created = _aged(30)
    row = _instance("husk-with-ckpt-1", created_at=created, last_activity_at=None)
    with Session(engine) as session:
        session.add(row)
        session.commit()
    # Insert a checkpoint row for this thread — proof a turn ran.
    insert_checkpoint_row(engine, "husk-with-ckpt-1", "cp-1")

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
async def test_composed_residual_row_that_satisfies_every_leg_is_still_flagged():
    """THE COMPOSED RESIDUAL — pinned as the CURRENT, DISCLOSED behaviour.

    A row that satisfies EVERY leg INCLUDING zero checkpoints is still
    flagged. This models the shape the E7 "does NOT cover" list
    describes: an instance that DID execute a turn, then

      * its task rows were boot-wiped (``queue_discard_on_startup`` /
        boot recovery deletes durable rows),
      * its ``message_queue`` rows aged out (``cleanup_old`` > 24 h),
      * its ``checkpoints`` rows were retention-swept (> 168 h TTL),
      * it has been silent > 24 h, with no descendants, no JobItem and
        no dependency watcher.

    Observationally that row is INDISTINGUISHABLE from a genuine
    never-dispatched husk: zero rows in every evidence table. The
    detector therefore flags it — the current, disclosed behaviour. This
    pin exists so the NEXT lane sees the boundary pinned rather than
    discovered: E7 closes the band only WHILE the checkpoint rows
    survive; once they are retention-swept the row re-enters the class.
    """
    tmpdir = tempfile.mkdtemp(prefix="husk-e7-residual-")
    engine = create_engine(f"sqlite:///{tmpdir}/fleet.db")
    SQLModel.metadata.create_all(engine)
    create_checkpoints_table(engine)

    created = _aged(30)  # > 168 h — the checkpoints TTL has elapsed.
    with Session(engine) as session:
        session.add(
            _instance("composed-residual-1", created_at=created, last_activity_at=None)
        )
        session.commit()

    # The row DID execute a turn: build the evidence a turn leaves, then
    # remove it exactly as the production retention/boot paths do.
    with Session(engine) as session:
        session.add(Task(instance_id="composed-residual-1", task_type="PROCESS_MESSAGE"))
        session.add(
            JobItem(
                agent_id="worker",
                agent_dir="/tmp/agents/worker",
                message="dispatch",
                instance_id="composed-residual-1",
                job_type="task",
                admission_state="done",
            )
        )
        session.commit()
    insert_checkpoint_row(engine, "composed-residual-1", "cp-residual-1")
    # Boot-wipe of task/job rows + checkpoint retention sweep (> 168 h).
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM task WHERE instance_id = 'composed-residual-1'"))
        conn.execute(
            text("DELETE FROM job_queue_items WHERE instance_id = 'composed-residual-1'")
        )
        conn.execute(text("DELETE FROM checkpoints WHERE thread_id = 'composed-residual-1'"))
        conn.commit()

    # The residue: zero task, zero message_queue, zero job_item, zero
    # checkpoint rows, no descendants, no watcher — every leg satisfied.
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT count(*) FROM checkpoints WHERE thread_id = 'composed-residual-1'"
        )).scalar() == 0
        assert conn.execute(text(
            "SELECT count(*) FROM task WHERE instance_id = 'composed-residual-1'"
        )).scalar() == 0
        assert conn.execute(text(
            "SELECT count(*) FROM job_queue_items WHERE instance_id = 'composed-residual-1'"
        )).scalar() == 0

    manager = _RepoBackedManager(engine)
    payload = await call(_read_tools(manager), "instance_health")

    # CURRENT, DISCLOSED behaviour: the row is flagged. E7 cannot tell a
    # retention-swept executed row from a genuine never-dispatched husk.
    assert "composed-residual-1" in _flagged_ids(payload)
    assert payload["evidence_legs"] == "ok"
    engine.dispose()
