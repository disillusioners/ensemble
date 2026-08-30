"""S6 — pure-hang WC-wake acceptance test, ALL THREE wake surfaces (T10.3).

wc-wake-report-integrity (phase1-plan §6-T10.3; blueprint park/wake
corollary). This is the feature's acceptance test: a parked
WAITING_CHILDREN (WC) parent with a single HUNG child must be woken by
exactly one message sent through each public lane, and the wake must
produce a REAL engine turn that consumes the message — followed by the
child's later report delivering. A pure-hang shape: no sibling
termination, no external mid-test unblock — the wake message alone
must do the job.

Surfaces (parametrized over all three):

  (a) HTTP ``POST /instances/{id}/messages`` — the real endpoint
      coroutine (``daemon/routers/messages.send_message``) with a real
      app-state handle.
  (b) agent-tool ``send_message`` — the real tool closure built by the
      production ``create_instance_tools`` factory.
  (c) ``job_inject`` → ``enqueue_message`` — the real tool closure
      (LOCKED C1-D3 Option A lane; T7→T10 dependency).

Real engine (what is NOT mocked):

  * real ``InstanceManager`` over a real in-memory SQLite engine —
    real repositories, real ``claim_pending_task``, real status flips;
  * real ``WorkerPool`` (1 worker) — the real enqueue → claim →
    ``ProcessMessageProcessor`` → ``MessageProcessingPipeline`` dispatch
    chain, exactly the chain the D13/T6b choke-point analysis pinned;
  * real ``build_instance_graph`` — the production graph assembly
    (agent node, streaming loop, pairing guard, D1 seam, D2 drain all
    live) with the LLM bound to a SCRIPTED fake (a pure-hang test
    needs no model). The fake records every message list it is
    invoked with — the consumption evidence.
  * real report-delivery contract for the "child's later report"
    step: a ``MessageQueue`` row with
    ``source=internal_report:{child}:{message_id}`` via
    ``manager.enqueue_message`` (the durable wake contract
    ``child_reports`` itself writes at ``child_reports.py:2744``; the
    report-build/adjudication chain is out of scope here — S6 proves
    DELIVERY + wake + consumption).

Flag state: ``ENSEMBLE_WC_WAKE_ENQUEUE=1`` (C1-Q2 flag-ON semantics —
the two-turn world). The WC parking mechanism itself is out of scope
(plan §2): the parent is seeded WC, the child is seeded hung.

Schema note: MigrationRunner is no-op'd — the pre-existing SQLite
migration family 20260714_000001 (QUARANTINE.md 2026-08-26) is
orthogonal to this component.
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Response
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select
from starlette.requests import Request

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.task.models import Task, TaskStatus

_WAKE_TOKEN = "WAKE-PURE-HANG"
_REPORT_TOKEN = "CHILD-REPORT-PURE-HANG"


# ---------------------------------------------------------------------------
# Restore REAL langgraph (undo the root conftest unit-test mocks)
#
# Same recipe as tests/integration/test_compaction_e2e.py::
# ``restore_langgraph_modules`` — the root conftest installs MagicMock
# langgraph modules into sys.modules before any daemon import; this
# harness needs the REAL graph engine (that is the point of S6), so the
# mocks are purged and the cached daemon modules re-imported. The
# checkpoint.memory mock entry is ALSO purged (the compaction exemplar
# omits it) because the D1 seam reads checkpoint state via
# ``graph.aget_state`` — a real checkpointer is load-bearing here.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def restore_langgraph_modules():
    from types import ModuleType  # noqa: F401

    original_modules: dict[str, object] = {}
    mock_keys = [
        "langgraph",
        "langgraph.graph",
        "langgraph.graph.state",
        "langgraph.prebuilt",
        "langgraph.constants",
        "langgraph.checkpoint",
        "langgraph.checkpoint.memory",
        "langgraph.checkpoint.sqlite",
        "langgraph.checkpoint.sqlite.aio",
    ]
    for key in mock_keys:
        if key in sys.modules:
            original_modules[key] = sys.modules[key]
    for key in mock_keys:
        sys.modules.pop(key, None)

    for mod_name in [
        "daemon.compaction",
        "daemon.graph",
        "daemon.manager",
        "daemon.persistence",
        "daemon.services.instance_messaging",
        "daemon.services.worker_pool",
        "daemon.services.task_processor",
        "daemon.services.message_processing_pipeline",
        "daemon.services.execution_gate",
    ]:
        sys.modules.pop(mod_name, None)

    yield

    for key in mock_keys:
        if key in original_modules:
            sys.modules[key] = original_modules[key]


@pytest.fixture(autouse=True)
def _reset_wc_wake_enqueue_flag_cache():
    """Reset the WC-wake kill-switch cache around EVERY test in this module.

    W1 completion (2026-08-30 pre-flip batch — 5th flag setter):
    the ``ENSEMBLE_WC_WAKE_ENQUEUE=1`` tests in this integration
    module set the env and call
    ``_reset_wc_wake_enqueue_for_tests()`` — but monkeypatch only
    restores the ENV at teardown; the resolver's module-global cache
    stays ``True`` and leaks into later flag-implicit tests (both
    the cross-file-order and subset-by-name vectors reproduce
    ``assert 200 == 202`` on the legacy 202 expectation). Clear the
    cache BEFORE and AFTER every test so each test resolves the flag
    from the ambient env.

    Integration-module caveat: the autouse ``restore_langgraph_modules``
    fixture above pops ``daemon.services.instance_messaging`` from
    ``sys.modules`` so the real graph engine loads with real langgraph.
    This reset fixture re-imports the module fresh, getting a module
    whose global starts ``None`` (matching the other three unit-module
    fixtures — same pattern, same outcome). Module-scoped on purpose —
    a suite-global autouse in ``tests/conftest.py`` would mask
    intentional flag-state tests and add overhead everywhere.
    """
    from daemon.services.instance_messaging import (
        _reset_wc_wake_enqueue_for_tests,
    )

    _reset_wc_wake_enqueue_for_tests()
    yield
    _reset_wc_wake_enqueue_for_tests()



# ---------------------------------------------------------------------------
# Scripted LLM — the consumption evidence
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    """Fake bound LLM: records every invocation, echoes the last human turn.

    The recording is the S6 consumption evidence: a wake message only
    counts as consumed when the REAL graph's agent node hands it to the
    model.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def bind_tools(self, _tools):  # noqa: D102 - mirrors ChatOpenAI surface
        return self

    def invoke(self, messages, **_kwargs):  # noqa: D102
        contents = [
            m if isinstance(m, str) else str(getattr(m, "content", ""))
            for m in messages
        ]
        self.calls.append(contents)
        # Small delay makes the post-enqueue RUNNING window observable.
        time.sleep(0.15)
        last = contents[-1] if contents else ""
        return AIMessage(content=f"consumed:{last[-80:]}")


# ---------------------------------------------------------------------------
# Fixtures — real manager, real worker pool, real graph, hung child
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """Real SQLite FILE database (tmp_path) with NullPool.

    Deliberately NOT StaticPool/:memory: — the harness runs REAL worker
    threads + loop-side sessions against ONE database, and StaticPool's
    single shared connection trips the documented cross-thread
    session-refresh/lost-write hazard (QUARANTINE.md dependency_bus
    row). A file DB with per-checkout connections + WAL mirrors
    production's concurrency shape.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/s6.db",
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


def _seed_system_default_project(engine: Engine) -> None:
    """Seed the system-default project row the spawn path validates."""
    from daemon import constants
    from daemon.repositories.project.models import Project  # noqa: F401
    from daemon.repositories.project.models import ProjectStatus

    pid = constants.SYSTEM_DEFAULT_PROJECT_ID
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Project(
                project_id=pid,
                name="_system_default",
                project_type="system",
                status=ProjectStatus.ACTIVE.value,
                description="S6 harness",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()


def _read_status(engine: Engine, instance_id: str) -> str | None:
    with Session(engine) as s:
        inst = s.get(Instance, instance_id)
        return inst.status if inst else None


def _set_status(engine: Engine, instance_id: str, status: str) -> None:
    with Session(engine) as s:
        inst = s.get(Instance, instance_id)
        assert inst is not None
        inst.status = status
        s.add(inst)
        s.commit()


def _active_task_count(engine: Engine, instance_id: str) -> int:
    with Session(engine) as s:
        rows = s.exec(
            select(Task).where(
                Task.instance_id == instance_id,
                Task.status.in_(
                    [TaskStatus.PENDING.value, TaskStatus.RUNNING.value]
                ),
            )
        ).all()
        return len(rows)


async def _wait_for_quiescent(engine: Engine, instance_id: str, timeout: float = 20.0):
    """Poll until the instance has no PENDING/RUNNING task left."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _active_task_count(engine, instance_id) == 0:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"instance {instance_id} still has active tasks after {timeout}s"
    )


def _log_saw(llm: _ScriptedLLM, token: str) -> bool:
    return any(token in content for call in llm.calls for content in call)


@pytest.fixture
async def wake_harness(engine: Engine):
    """Real manager + real worker pool + scripted-LLM graph + hung child.

    Shape: parent spawned real, parked WC; ONE child spawned real,
    left RUNNING with a never-completing carrier Task (the pure hang).

    Engine injection: ``create_engine_from_config`` is patched at the
    manager module level so EVERY engine the manager creates (main +
    checkpointer) is the fixture's shared StaticPool engine — the test,
    the manager, and the worker threads all see one database, mirroring
    production's one-shared-engine philosophy.
    """
    import daemon.manager as daemon_manager_module
    from daemon.config import (
        AgentsConfig,
        Config,
        DaemonConfig,
        LLMConfig,
        LimitsConfig,
        PersistenceConfig,
    )
    from daemon.manager import InstanceManager

    _seed_system_default_project(engine)

    config = Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4",
            temperature=0.7,
        ),
        limits=LimitsConfig(
            max_children_per_instance=3,
            instance_timeout_minutes=60,
        ),
        persistence=PersistenceConfig(
            db_path=":memory:",
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
            max_instance_history=300,
        ),
        daemon=DaemonConfig(host="127.0.0.1", port=8079),
        agents=AgentsConfig(directory="./agents"),
    )

    llm = _ScriptedLLM()
    real_build = daemon_manager_module.build_instance_graph

    def _build_with_scripted_llm(*args, **kwargs):
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=llm):
            return real_build(*args, **kwargs)

    with (
        patch(
            "daemon.migrations.runner.MigrationRunner.run_pending_migrations",
            return_value=[],
        ),
        patch(
            "daemon.manager.create_engine_from_config", return_value=engine
        ),
        patch(
            "daemon.manager.build_instance_graph", new=_build_with_scripted_llm
        ),
    ):
        manager = InstanceManager(config)
        manager._loop = asyncio.get_running_loop()

        # Real in-memory checkpointer: production creates the adapter
        # in initialize(); the D1 seam reads checkpoint state via
        # ``graph.aget_state`` every enqueued turn, so the graph must
        # be compiled WITH a checkpointer. The adapter wraps the raw
        # saver exactly as production does (SqliteCheckpointerAdapter
        # is saver-agnostic — it only exposes ``.raw_saver``).
        from langgraph.checkpoint.memory import MemorySaver

        from daemon.checkpoint_adapter import SqliteCheckpointerAdapter

        manager._checkpointer = SqliteCheckpointerAdapter(MemorySaver())
        # The title-generation path would attempt a real OpenAI call
        # (fire-and-forget 401 noise) — stub it; titles are out of S6
        # scope.
        manager._generate_and_broadcast_title = AsyncMock()

        # ``setup_worker_pool`` wires the task repo into the maintenance
        # service, which production constructs during ``initialize()``.
        # Construct it here (no background start — the harness never
        # calls start()).
        from daemon.services.maintenance import MaintenanceService

        manager._maintenance_service = MaintenanceService()
        manager._maintenance_service.set_request_registry({})

        manager.setup_worker_pool(num_workers=1)

        # Real job-service stack (mirrors the api.py lifespan ordering:
        # setup_worker_pool first, then the JobQueueService + work
        # resolver). Needed by the job_inject surface; harmless for the
        # other two.
        from daemon.repositories.factory import create_job_repository
        from daemon.repositories.job_queue.lock_repository import LockRepository
        from daemon.repositories.job_queue.queue_repository import (
            JobQueueRepository,
        )
        from daemon.services.job_lock_manager import JobLockManager
        from daemon.services.job_queue_service import JobQueueService
        from daemon.services.work_resolver import WorkResolverService

        job_repo = create_job_repository(engine=engine, create_tables=True)
        job_service = JobQueueService(
            repository=job_repo,
            lock_manager=JobLockManager(lock_repo=LockRepository(engine)),
            queue_repo=JobQueueRepository(engine),
            instance_manager=manager,
        )
        manager.set_job_queue_service(job_service)
        manager._maintenance_service.set_job_queue_service(job_service)
        work_resolver = WorkResolverService(
            task_repo=manager._task_repo,
            job_repo=job_repo,
            instance_repo=manager._instance_repository,
        )
        manager._work_resolver = work_resolver
        job_service.set_work_resolver(work_resolver)

        # Provision the REAL system queues for the system-default project
        # (production: api.py lifespan → auto-provision). The HTTP lane's
        # ``enqueue_message_job`` resolves ``system_parallel_queue``.
        # Seeded SYNCHRONOUSLY in the fixture thread — the mgmt-service
        # path hops threads via asyncio.to_thread, which trips the
        # documented StaticPool cross-thread refresh race
        # (QUARANTINE.md 2026-08-29 dependency_bus row, same class).
        from daemon.services.job_queue_mgmt_service import (
            RESERVED_QUEUE_NAMES,
        )
        from daemon.repositories.job_queue.models import JobQueue, QueueType

        now_iso = datetime.now(timezone.utc).isoformat()
        with Session(engine) as s:
            for name in sorted(RESERVED_QUEUE_NAMES):
                s.add(
                    JobQueue(
                        project_id=constants_project_id(),
                        queue_name=name,
                        queue_name_lower=name.lower(),
                        queue_type=QueueType.FIFO.value,
                        concurrency_limit=5,
                        is_system=True,
                        created_at=now_iso,
                        updated_at=now_iso,
                    )
                )
            s.commit()

        # Real dispatch bus + JobProcessor (api.py lifespan ordering).
        # The HTTP lane's message JobItem mirror starts admission_state
        # ='queued'; the queue-awareness guard in claim_pending_task
        # (2026-07-26 FIFO concurrency fix) refuses the linked Task
        # until the JobProcessor admits the job (queued -> active) and
        # wakes the pool — the harness runs that production chain for
        # real. (The JobFeedbackObserver mirror terminalization is out
        # of S6 scope: S6 asserts WC flip + Task completion +
        # consumption, not mirror terminalization.)
        from daemon.services.dispatch_event_bus import DispatchEventBus
        from daemon.services.job_processor import JobProcessor

        dispatch_event_bus = DispatchEventBus()
        dispatch_event_bus.set_event_loop(asyncio.get_running_loop())
        job_service.set_dispatch_bus(dispatch_event_bus)
        job_processor = JobProcessor(
            queue_service=job_service,
            instance_manager=manager,
            project_repo=manager._project_repository,
            queue_repo=JobQueueRepository(engine),
            poll_interval=1.0,
            dispatch_bus=dispatch_event_bus,
            event_dispatch_enabled=True,
        )
        await job_processor.start()

        yield manager, engine, llm

        if manager._worker_pool is not None:
            manager._worker_pool.stop(timeout=5)
        try:
            await job_processor.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Parked-parent + hung-child setup (shared by the three surfaces)
# ---------------------------------------------------------------------------


async def _park_parent_with_hung_child(manager, engine: Engine) -> tuple[str, str]:
    """Spawn a real parent + ONE hung child; park the parent WC.

    Returns ``(parent_id, child_id)``. The child carries a RUNNING
    carrier Task that never completes — the pure-hang shape. No sibling
    termination ever fires in this test.
    """
    parent_id, _ = manager.spawn_instance(agent_id="developer")
    assert parent_id

    child_id, _ = manager.spawn_instance(agent_id="developer", parent_id=parent_id)
    assert child_id

    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        # Hung child: the REAL spawned child row stays RUNNING and gets
        # a RUNNING carrier Task that never completes (the pure hang).
        s.add(
            Task(
                task_type="process_message",
                instance_id=child_id,
                status=TaskStatus.RUNNING.value,
                worker_id="hung-carrier",
                started_at=now,
            )
        )
        child = s.get(Instance, child_id)
        child.status = InstanceStatus.RUNNING.value
        s.add(child)
        s.commit()

    # Park the parent (the WC parking mechanism itself is out of scope —
    # plan §2; the seeded state IS the parked state the lanes must treat
    # correctly).
    _set_status(engine, parent_id, InstanceStatus.WAITING_CHILDREN.value)
    return parent_id, child_id


def constants_project_id() -> str:
    from daemon import constants

    return constants.SYSTEM_DEFAULT_PROJECT_ID


async def _assert_wake_consumed_and_report_delivers(
    manager, engine: Engine, llm: _ScriptedLLM, parent_id: str, child_id: str
):
    """Shared wake + report-delivery assertions (pure-hang corollary).

    1. WC → RUNNING flip: the parked parent must have left
       ``waiting_children`` (the enqueue transaction's flip; nothing in
       a plain message turn ever re-parks it).
    2. Real-engine turn consumes the message: the scripted LLM's call
       log must contain the wake token (only the REAL agent node hands
       messages to the model).
    3. The child's later report delivers: a durable
       ``internal_report:{child}:{message_id}`` row via
       ``manager.enqueue_message`` → real worker turn → the report
       token reaches the model.
    """
    # (1) WC → RUNNING flip: the flip commits inside the enqueue
    # transaction, so the parent leaves waiting_children as soon as the
    # surface call returns. The parked parent may legitimately RE-PARK
    # to waiting_children after the wake turn completes (the child is
    # still hung — the completion gate re-parks), so the flip is caught
    # as a RUNNING sighting in the wake window, not as an end state.
    flip_seen = False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not flip_seen:
        if _read_status(engine, parent_id) == InstanceStatus.RUNNING.value:
            flip_seen = True
            break
        time.sleep(0.02)
    assert flip_seen, (
        "S6: the parked parent must FLIP to RUNNING when the wake "
        "message is accepted (WC -> RUNNING in the enqueue transaction)"
    )

    # (2) the wake turn: real WorkerPool claims the Task the wake
    # created and drives the real pipeline. Wait for quiescence.
    await _wait_for_quiescent(engine, parent_id)
    assert _log_saw(llm, _WAKE_TOKEN), (
        "S6: the real-engine turn must CONSUME the wake message — the "
        f"scripted LLM never saw it. Calls: {llm.calls}"
    )

    # (3) — the child's later report delivers through the real durable
    # report-delivery contract and is consumed by a real parent turn.
    completed_message_id = str(uuid.uuid4())
    result = await manager.enqueue_message(
        parent_id,
        _REPORT_TOKEN,
        source=f"internal_report:{child_id}:{completed_message_id}",
    )
    assert result.message_id
    await _wait_for_quiescent(engine, parent_id)
    assert _log_saw(llm, _REPORT_TOKEN), (
        "S6: the child's later report must DELIVER — the scripted LLM "
        f"never saw it. Calls: {llm.calls}"
    )


# ---------------------------------------------------------------------------
# (a) HTTP POST /instances/{id}/messages
# ---------------------------------------------------------------------------


async def test_http_post_messages_wakes_parked_wc_parent(wake_harness, monkeypatch):
    """Surface (a): HTTP POST /messages — WC → 200 durable wake → real turn."""
    from daemon.routers.messages import MessageCreate, send_message as http_send_message

    monkeypatch.setenv("ENSEMBLE_WC_WAKE_ENQUEUE", "1")
    from daemon.services.instance_messaging import _reset_wc_wake_enqueue_for_tests

    _reset_wc_wake_enqueue_for_tests()

    manager, engine, llm = wake_harness
    parent_id, child_id = await _park_parent_with_hung_child(manager, engine)
    assert _read_status(engine, parent_id) == InstanceStatus.WAITING_CHILDREN.value

    app = FastAPI()
    app.state.manager = manager
    request = Request(
        {
            "type": "http",
            "app": app,
            "method": "POST",
            "path": f"/instances/{parent_id}/messages",
            "headers": [],
            "query_string": b"",
        }
    )

    body = await http_send_message(
        instance_id=parent_id,
        message=MessageCreate(content=_WAKE_TOKEN),
        request=request,
        response=Response(),
    )

    # D4 contract: WC gets the MessageResponse shape with a real
    # message_id (not the legacy 202-injected shape, which had neither
    # message_id nor job_id and carried status="injected" +
    # pending_count). NOTE: ``queued`` reflects real queue saturation
    # (MessageQueueRow capacity), NOT the routing decision — the mocked
    # unit fixtures pin it True; against the real queue here it is the
    # unsaturated default.
    assert "status" not in body, body
    assert "pending_count" not in body, body
    assert body["message_id"]
    assert body["job_id"]
    assert body["role"] == "assistant"

    await _assert_wake_consumed_and_report_delivers(
        manager, engine, llm, parent_id, child_id
    )


# ---------------------------------------------------------------------------
# (b) agent-tool send_message
# ---------------------------------------------------------------------------


async def test_agent_tool_send_message_wakes_parked_wc_parent(
    wake_harness, monkeypatch
):
    """Surface (b): the real agent-tool closure — WC → durable wake turn."""
    from tests.helpers.send_message_fixtures import (
        get_send_message_tool,
        patch_heavy_helpers,
    )

    monkeypatch.setenv("ENSEMBLE_WC_WAKE_ENQUEUE", "1")
    from daemon.services.instance_messaging import _reset_wc_wake_enqueue_for_tests

    _reset_wc_wake_enqueue_for_tests()

    manager, engine, llm = wake_harness
    parent_id, child_id = await _park_parent_with_hung_child(manager, engine)
    assert _read_status(engine, parent_id) == InstanceStatus.WAITING_CHILDREN.value

    patches = patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        send_tool = get_send_message_tool(manager)
        with patch(
            "daemon.tools.instance._check_team_membership", return_value=None
        ):
            result = await send_tool.coroutine(parent_id, _WAKE_TOKEN)
    finally:
        for p in reversed(patches):
            p.stop()

    # Enqueue-parity text (durable wake; the W3 stranding caveat is
    # injection-branch-only and must NOT appear for WC under flag ON).
    assert "Message queued and sent" in result
    assert "pause-loss parity" not in result

    await _assert_wake_consumed_and_report_delivers(
        manager, engine, llm, parent_id, child_id
    )


# ---------------------------------------------------------------------------
# (c) job_inject → enqueue_message (LOCKED C1-D3 Option A; T7→T10)
# ---------------------------------------------------------------------------


async def test_job_inject_wakes_parked_wc_parent(wake_harness, monkeypatch):
    """Surface (c): job_inject against a WC target routes to
    ``enqueue_message`` (Option A) — durable wake + real turn."""
    from daemon.tools.job_queue import create_job_tools

    monkeypatch.setenv("ENSEMBLE_WC_WAKE_ENQUEUE", "1")
    from daemon.services.instance_messaging import _reset_wc_wake_enqueue_for_tests

    _reset_wc_wake_enqueue_for_tests()

    manager, engine, llm = wake_harness
    parent_id, child_id = await _park_parent_with_hung_child(manager, engine)
    assert _read_status(engine, parent_id) == InstanceStatus.WAITING_CHILDREN.value

    # A real JobItem bound to the parked parent (job_inject resolves the
    # target instance through it). admission_state='active' with NO
    # backing in-flight Task — mirrors a live job whose instance parked.
    job_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=job_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="original job",
                source="api",
                project_id=constants_project_id(),
                instance_id=parent_id,
                admission_state="active",
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()

    job_service = manager._job_queue_service
    assert job_service is not None, (
        "S6 harness: setup_worker_pool must wire the real JobQueueService"
    )

    tools = create_job_tools(
        job_service,
        queue_mgmt_service=None,
        dead_letter_service=None,
        current_instance_id="",
        agent_id="developer",
        manager=manager,
    )
    job_inject = next(
        (t for t in tools if getattr(t, "name", None) == "job_inject"), None
    )
    assert job_inject is not None, "job_inject tool missing from create_job_tools"

    result = await job_inject.coroutine(job_id=job_id, message=_WAKE_TOKEN)

    # Option A contract: WC → enqueued (durable), not injected.
    # NOTE: ``queued`` mirrors AsyncMessageResult.queued (queue-saturation
    # semantics), NOT the routing decision — the routing evidence is
    # status="enqueued" + a durable message_id.
    assert result.get("status") == "enqueued", result
    assert result.get("error") is None
    assert result.get("message_id")

    await _assert_wake_consumed_and_report_delivers(
        manager, engine, llm, parent_id, child_id
    )
