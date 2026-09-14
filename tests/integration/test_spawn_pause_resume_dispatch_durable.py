"""INTEGRATION — original wedge scenario, end-to-end on the fixed tree.

Reproduces the incident 2026-09-14 shape against a REAL
``InstanceManager`` over a real file-backed SQLite engine (harness
mirrors ``tests/integration/test_job_driven_enqueue_work_id_facade.py``):

  1. Leader + two spawn-created children that are NEVER dispatched
     (idle, version=1, zero task/message/checkpoint rows) + one
     dispatched child (queued work) as the control.
  2. ``ask_questions``-style pause cascade over the whole tree.
  3. Answer-style resume cascade + the router's per-resumed-id
     ``resume_processing_job`` pattern.
  4. Leader dispatches real work to the children via the agent-tool
     ``send_message`` closure.

Assertions (DEFECT A + DEFECT B, integrated):

  * Children are SKIPPED by the pause cascade (stay ``idle``) — the
    ``running``+``internal_child_noop`` limbo never forms.
  * The leader's dispatches MATERIALIZED DURABLY: ``task`` rows
    (PENDING) + ``message_queue`` rows exist for both children, and
    ``manager._pending_injections`` is EMPTY — nothing is stranded in
    the in-memory injection lane.
  * The durable task is CLAIMABLE by the worker-pool claim gate
    (``claim_pending_task``) — i.e. a graph run starts from it.
  * Regression pin: a genuinely-running target (live graph task in
    ``_graph_tasks``) still receives the in-memory injection.

No worker pool is wired (``_worker_pool = None``) so Task rows stay
PENDING for deterministic DB inspection — the claim gate is exercised
directly, which is the seam a real pool would drive.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task, TaskStatus


# ---------------------------------------------------------------------------
# Fixtures — real manager over a real file-backed SQLite engine
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = create_engine(
        f"sqlite:///{tmp_path}/spawn_pause_resume_dispatch.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_system_default_project(eng: Engine) -> None:
    from daemon import constants
    from daemon.repositories.project.models import Project, ProjectStatus

    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(eng) as s:
        s.add(
            Project(
                project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
                name="_system_default",
                project_type="system",
                status=ProjectStatus.ACTIVE.value,
                description="graphless dispatch integration harness",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()


def _seed_instance(
    eng: Engine,
    *,
    instance_id: str,
    parent_id: str | None,
    status: str,
) -> Instance:
    inst = Instance(
        instance_id=instance_id,
        agent_id="worker",
        agent_dir="/agents/worker",
        project_id=None,
        parent_id=parent_id,
        status=status,
        version=1,
        instance_metadata={},
    )
    with Session(eng) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


@pytest.fixture
async def manager(engine: Engine, tmp_path):
    """Real ``InstanceManager`` (no worker pool — Tasks stay PENDING for
    DB inspection; the claim gate is exercised directly)."""
    _seed_system_default_project(engine)

    from daemon.config import (
        AgentsConfig,
        Config,
        DaemonConfig,
        LLMConfig,
        LimitsConfig,
        PersistenceConfig,
    )
    from daemon.manager import InstanceManager

    config = Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4",
            temperature=0.7,
        ),
        limits=LimitsConfig(
            max_children_per_instance=5,
            instance_timeout_minutes=60,
        ),
        persistence=PersistenceConfig(
            db_path=str(tmp_path / "config-unused.db"),
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
            max_instance_history=300,
        ),
        daemon=DaemonConfig(host="127.0.0.1", port=8079),
        agents=AgentsConfig(directory="./agents"),
    )

    with (
        patch(
            "daemon.migrations.runner.MigrationRunner.run_pending_migrations",
            return_value=[],
        ),
        patch(
            "daemon.manager.create_engine_from_config", return_value=engine
        ),
    ):
        mgr = InstanceManager(config)

    mgr._loop = asyncio.get_running_loop()
    mgr._worker_pool = None
    # ``setup_worker_pool()`` normally wires ``_task_repo`` (the pause /
    # resume cascades read it via the lifecycle service property). With
    # no worker pool, wire the repo directly — identical to the
    # production constructor call minus the pool notify callback.
    from daemon.repositories.task.repository import TaskRepository

    mgr._task_repo = TaskRepository(engine=engine, on_pending_task=None)
    return mgr


def _build_send_message_tool(mgr):
    """Real agent-tool ``send_message`` closure over the real manager.

    Mirrors the routing suites' tool-builder (the heavy factory helpers
    are patched out; ``_check_team_membership`` is bypassed — team
    wiring is not this incident's surface).
    """
    from tests.helpers.send_message_fixtures import (
        get_send_message_tool,
        patch_heavy_helpers,
    )

    patches = patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tool = get_send_message_tool(mgr)
    finally:
        for p in reversed(patches):
            p.stop()

    async def _send(target_id: str, message: str) -> str:
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            return await tool.coroutine(target_id, message)

    return _send


# ---------------------------------------------------------------------------
# The original scenario, end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_pause_resume_then_dispatch_materializes_durably(
    manager, engine
):
    # ── 1. Leader spawns two children WITHOUT dispatch ──────────────────
    _seed_instance(
        engine, instance_id="leader-1", parent_id=None,
        status=InstanceStatus.RUNNING.value,
    )
    _seed_instance(
        engine, instance_id="child-a", parent_id="leader-1",
        status=InstanceStatus.IDLE.value,
    )
    _seed_instance(
        engine, instance_id="child-b", parent_id="leader-1",
        status=InstanceStatus.IDLE.value,
    )
    # Control: a child that HAS queued work (dispatched, not started).
    _seed_instance(
        engine, instance_id="child-work", parent_id="leader-1",
        status=InstanceStatus.IDLE.value,
    )
    with Session(engine) as s:
        s.add(
            MessageQueue(
                message_id="mq-control",
                instance_id="child-work",
                content="real queued task",
                role="user",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        s.add(
            Task(instance_id="child-work", task_type="PROCESS_MESSAGE")
        )
        s.commit()

    send = _build_send_message_tool(manager)

    # ── 2. ask_questions pause cascade (whole tree) ─────────────────────
    pause_result = await manager.pause_instance_cascade("leader-1")

    assert set(pause_result["paused_ids"]) == {"leader-1", "child-work"}
    assert set(pause_result["skipped_ids"]) >= {"child-a", "child-b"}
    with Session(engine) as s:
        for cid in ("child-a", "child-b"):
            row = s.get(Instance, cid)
            assert row.status == InstanceStatus.IDLE.value, (
                "never-dispatched children must NOT be paused — the "
                "limbo is prevented at pause time"
            )

    # ── 3. Answer resume cascade + router per-resumed-id pattern ────────
    resume_result = await manager.resume_instance_cascade("leader-1")

    # Router contract (routers/instances.py:828-870): resume_processing_job
    # is invoked ONLY for resumed ids — the ghosts were never resumed.
    assert "child-a" not in resume_result["resumed_ids"]
    assert "child-b" not in resume_result["resumed_ids"]
    for resumed_id in resume_result["resumed_ids"]:
        # The router's actual downstream call; for the leader (no
        # suspension handle, silent=False for target) it lands on
        # invalid_or_missing_handle → None → router falls through. For
        # child-work (real paused turn) it re-arms durably. Both are
        # pre-existing correct behaviors; here we pin they RUN at all.
        await manager.resume_processing_job(
            resumed_id,
            message="resume" if resumed_id != "leader-1" else "continue",
            silent=resumed_id != "leader-1",
        )

    # ── 4. Leader dispatches real work via agent-tool send_message ──────
    result_a = await send("child-a", "do the thing, part A")
    result_b = await send("child-b", "do the thing, part B")

    assert "Message queued and sent to child-a" in result_a
    assert "Message queued and sent to child-b" in result_b

    # DURABLE materialization: task + message rows exist for both.
    with Session(engine) as s:
        task_rows = s.exec(
            select(Task).where(
                Task.instance_id.in_(["child-a", "child-b"])
            )
        ).all()
        msg_rows = s.exec(
            select(MessageQueue).where(
                MessageQueue.instance_id.in_(["child-a", "child-b"])
            )
        ).all()

    assert {t.instance_id for t in task_rows} == {"child-a", "child-b"}
    assert all(t.status == TaskStatus.PENDING.value for t in task_rows)
    assert {m.instance_id for m in msg_rows} == {"child-a", "child-b"}

    # NOTHING is stranded in the in-memory injection lane.
    assert not manager._pending_injections.get("child-a")
    assert not manager._pending_injections.get("child-b")

    # The durable tasks are CLAIMABLE → a graph run starts from them
    # (this is the seam the worker pool would drive). The claim gate is
    # FIFO-within-tier, so drain until exhausted and assert BOTH
    # children's tasks get claimed.
    claimed_instance_ids: set[str] = set()
    while True:
        claimed = manager._task_repo.claim_pending_task(worker_id="w-1")
        if claimed is None:
            break
        assert claimed.status == TaskStatus.RUNNING.value
        claimed_instance_ids.add(claimed.instance_id)

    assert {"child-a", "child-b"} <= claimed_instance_ids, (
        f"both children's durable tasks must be claimable; got "
        f"{sorted(claimed_instance_ids)}"
    )


@pytest.mark.asyncio
async def test_live_running_target_still_receives_injection(manager, engine):
    """Regression pin at integration level: a genuinely-running target
    (live graph task registered in ``_graph_tasks``) still takes the
    in-memory injection lane — the guard must not overcorrect."""
    _seed_instance(
        engine, instance_id="live-child", parent_id="leader-live",
        status=InstanceStatus.RUNNING.value,
    )
    send = _build_send_message_tool(manager)

    async def _never_ends():
        await asyncio.Event().wait()

    stub_task = asyncio.create_task(_never_ends())
    manager._graph_tasks["live-child"] = stub_task
    try:
        result = await send("live-child", "mid-turn input")

        assert "Message injected into running target" in result
        assert manager._pending_injections.get("live-child"), (
            "live-graph target must receive the in-memory injection"
        )
        with Session(engine) as s:
            rows = s.exec(
                select(Task).where(Task.instance_id == "live-child")
            ).all()
        assert rows == [], "injection must not create durable task rows"
    finally:
        stub_task.cancel()
        try:
            await stub_task
        except asyncio.CancelledError:
            pass
