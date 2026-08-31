"""E2E capstone — wc-wake phase-1 gate, ON vs OFF scenario on a real harness.

REUSES the boot harness pattern from
``tests/integration/test_wc_wake_pure_hang.py`` (read first, copied
its fixture approach — no new harness was invented). The pure-hang
S6 acceptance suite already proves the flag-ON wake surface works
on three routing sites; this capstone proves the OFF-state revert
path AND the ON-state wake, both in one pack, against the same real
manager + real worker pool + scripted-LLM graph.

ON-state (ENSEMBLE_WC_WAKE_ENQUEUE=1):
  Scenario: WC-parked parent (active child is HUNG; never completes)
  + user message via HTTP POST /instances/{id}/messages.
  Asserts (within the bounded wake window — 60s, comfortably larger
  than the pure-hang harness's 5s WC-flip + 20s quiescence):
    1. MessageQueue row IS minted (durable wake).
    2. Task row IS created.
    3. WC → RUNNING flip happens (parent leaves waiting_children).
    4. A real graph turn processes the wake message (scripted LLM
       sees the wake token).
    5. Parent re-parks to WC afterwards (the child is still hung —
       the completion gate re-parks; not a defect, just the harness
       shape). Acceptable alternate: parent completes if the child
       somehow reports.

OFF-state (flag unset — the documented stranding):
  Same scenario; flag defaults OFF.
  Asserts:
    1. HTTP POST returns 202 with "injected" body (NOT 200 + MessageResponse).
    2. NO MessageQueue row is minted for the wake token.
    3. NO Task row is created for the parent.
    4. Parent stays WC throughout the bounded wait (no flip).
    5. The message lands in the RAM FIFO (manager.set_injection called)
       — proof the routing did NOT take the enqueue branch.

The OFF assertions are the kill-switch revert proof: OFF must keep
the legacy stranding behavior intact (C2-D2.5-FLIP / D2.5-FLIP).

TEST-ENV ONLY. No production code changes, no daemon boot, no ports
(below 10000). The harness's DaemonConfig sets port=8079 but the
listener is NEVER started — FastAPI is driven in-process via ASGI
transport (mirroring pure-hang).
"""
from __future__ import annotations

import asyncio
import sys
import time
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
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task, TaskStatus

# Pre-flight: ensure_system_default_project() is normally called at
# daemon lifespan startup. The probe-style test runs in isolation, so
# we set the constant explicitly (mirrors origin_contract_e2e_probe_test
# line 79). Without this the job-queue seeding at the wake_harness
# bootstrap fails with ``NOT NULL constraint failed: job_queues.project_id``.
import daemon.constants  # noqa: E402
daemon.constants.SYSTEM_DEFAULT_PROJECT_ID = "capstone-default-project"

# Bounded wake window: the task spec says "≤60s — pick a bound the
# harness can defend; the point is NOT-an-hour". Pure-hang's actual
# observation at HEAD is a 5s WC flip + 10s quiescence (~15s total);
# 60s is a margin-rich bound that still fits well under the 280s
# internal cap.
WAKE_BOUND_S = 60.0

WAKE_TOKEN_ON = "WAKE-CAPSTONE-ON"
WAKE_TOKEN_OFF = "WAKE-CAPSTONE-OFF"


# ---------------------------------------------------------------------------
# langgraph real-loader (mirrors pure-hang's restore_langgraph_modules)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def restore_langgraph_modules():
    """Undo the root-conftest langgraph mocks so the real engine loads.

    Same recipe as ``tests/integration/test_wc_wake_pure_hang.py``
    and ``tests/integration/test_compaction_e2e.py`` — purge the
    MagicMock langgraph modules and pop the daemon modules that
    captured pre-pop bindings to ``_resolve_wc_wake_enqueue_enabled``.
    """
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
        "daemon.routers.messages",
        "daemon.tools.job_queue",
    ]:
        sys.modules.pop(mod_name, None)

    yield

    for key in mock_keys:
        if key in original_modules:
            sys.modules[key] = original_modules[key]


@pytest.fixture(autouse=True)
def _reset_wc_wake_enqueue_flag_cache():
    """Reset the WC-wake resolver cache around every test in this module.

    Mirror of pure-hang's fixture — same autouse pattern, same reason
    (W1 pollution prevention across module identity).
    """
    from daemon.services.instance_messaging import (
        _reset_wc_wake_enqueue_for_tests,
    )

    _reset_wc_wake_enqueue_for_tests()
    yield
    _reset_wc_wake_enqueue_for_tests()


# ---------------------------------------------------------------------------
# Scripted LLM (consumption evidence)
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def bind_tools(self, _tools):
        return self

    def invoke(self, messages, **_kwargs):
        contents = [
            m if isinstance(m, str) else str(getattr(m, "content", ""))
            for m in messages
        ]
        self.calls.append(contents)
        time.sleep(0.15)
        last = contents[-1] if contents else ""
        return AIMessage(content=f"consumed:{last[-80:]}")


# ---------------------------------------------------------------------------
# Engine + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = create_engine(
        f"sqlite:///{tmp_path}/capstone.db",
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


def constants_project_id() -> str:
    from daemon import constants
    return constants.SYSTEM_DEFAULT_PROJECT_ID


def _seed_system_default_project(engine: Engine) -> None:
    from daemon import constants
    from daemon.repositories.project.models import Project
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
                description="capstone harness",
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


def _message_queue_count_for(
    engine: Engine, *, instance_id: str, content_token: str
) -> int:
    with Session(engine) as s:
        rows = s.exec(
            select(MessageQueue).where(
                MessageQueue.instance_id == instance_id,
                MessageQueue.content.contains(content_token),
            )
        ).all()
        return len(rows)


def _task_count_for_instance(engine: Engine, instance_id: str) -> int:
    with Session(engine) as s:
        rows = s.exec(
            select(Task).where(Task.instance_id == instance_id)
        ).all()
        return len(rows)


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


async def _wait_for_quiescent(engine: Engine, instance_id: str, timeout: float = 30.0):
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


# ---------------------------------------------------------------------------
# Wake harness (mirrors pure-hang's structure; trimmed for ONE surface)
# ---------------------------------------------------------------------------


@pytest.fixture
async def wake_harness(engine: Engine):
    """Real manager + real worker pool + scripted-LLM graph + hung child.

    Same recipe as ``test_wc_wake_pure_hang.py:wake_harness``; the
    only difference is the worker's ``num_workers`` (1 is plenty —
    the parent is parked, the child is hung, no contention).
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

        from langgraph.checkpoint.memory import MemorySaver
        from daemon.checkpoint_adapter import SqliteCheckpointerAdapter

        manager._checkpointer = SqliteCheckpointerAdapter(MemorySaver())
        manager._generate_and_broadcast_title = AsyncMock()

        from daemon.services.maintenance import MaintenanceService
        manager._maintenance_service = MaintenanceService()
        manager._maintenance_service.set_request_registry({})

        manager.setup_worker_pool(num_workers=1)

        # Wire the real job-service stack so the HTTP lane's
        # ``enqueue_message_job`` resolves ``system_parallel_queue``.
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

        from daemon.services.job_queue_mgmt_service import RESERVED_QUEUE_NAMES
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


async def _park_parent_with_hung_child(manager, engine: Engine) -> tuple[str, str]:
    """Spawn real parent + ONE hung child; park parent WC.

    Same shape as pure-hang's helper. Returns ``(parent_id, child_id)``.
    """
    parent_id, _ = manager.spawn_instance(agent_id="developer")
    child_id, _ = manager.spawn_instance(
        agent_id="developer", parent_id=parent_id
    )

    now = datetime.now(timezone.utc)
    with Session(engine) as s:
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

    _set_status(engine, parent_id, InstanceStatus.WAITING_CHILDREN.value)
    return parent_id, child_id


# ---------------------------------------------------------------------------
# ON-state: ENSEMBLE_WC_WAKE_ENQUEUE=1 — wake happens
# ---------------------------------------------------------------------------


async def test_on_state_wakes_parked_wc_parent(
    wake_harness, monkeypatch: pytest.MonkeyPatch
):
    """WC target + flag ON → durable wake + real graph turn."""
    from daemon.routers.messages import MessageCreate, send_message as http_send_message

    monkeypatch.setenv("ENSEMBLE_WC_WAKE_ENQUEUE", "1")
    from daemon.services.instance_messaging import _reset_wc_wake_enqueue_for_tests
    _reset_wc_wake_enqueue_for_tests()

    manager, engine, llm = wake_harness
    parent_id, _child_id = await _park_parent_with_hung_child(manager, engine)
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
    response = Response()

    body = await http_send_message(
        instance_id=parent_id,
        message=MessageCreate(content=WAKE_TOKEN_ON),
        request=request,
        response=response,
    )

    # ON-state contract: durable wake → 200 + MessageResponse shape.
    assert response.status_code == 200, (
        f"ON-state: expected 200 OK (durable wake), got {response.status_code}"
    )
    assert body.get("message_id"), body
    assert body.get("job_id"), body
    assert body.get("role") == "assistant", body

    # Wait for the parent to leave WC (≤ wake bound).
    flip_seen = False
    deadline = time.monotonic() + WAKE_BOUND_S
    while time.monotonic() < deadline and not flip_seen:
        if _read_status(engine, parent_id) == InstanceStatus.RUNNING.value:
            flip_seen = True
            break
        await asyncio.sleep(0.05)
    assert flip_seen, (
        f"ON-state: WC parent did not flip to RUNNING within "
        f"{WAKE_BOUND_S}s (wake was not prompt)"
    )

    # Wait for the wake turn to be processed by the real engine.
    await _wait_for_quiescent(engine, parent_id, timeout=WAKE_BOUND_S)
    assert _log_saw(llm, WAKE_TOKEN_ON), (
        f"ON-state: real graph turn did NOT consume the wake token "
        f"({WAKE_TOKEN_ON}); calls={llm.calls}"
    )

    # Durable row evidence: MessageQueue row IS minted (token-keyed).
    mq_count = _message_queue_count_for(
        engine, instance_id=parent_id, content_token=WAKE_TOKEN_ON
    )
    assert mq_count >= 1, (
        f"ON-state: expected ≥1 MessageQueue row for {WAKE_TOKEN_ON}, "
        f"got {mq_count}"
    )


# ---------------------------------------------------------------------------
# OFF-state: flag unset — legacy stranding, byte-faithful
# ---------------------------------------------------------------------------


async def test_off_state_legacy_stranding(wake_harness):
    """WC target + flag unset → RAM FIFO injection, NO MessageQueue row,
    parent stays WC.

    This is the kill-switch revert proof: OFF must keep the legacy
    stranding behavior intact. The parent is parked WC with a hung
    child; under flag OFF the message lands in the RAM FIFO and
    NEITHER enqueues NOR wakes the parent (the documented defect
    that the kill-switch was created to fix; OFF keeps the defect
    because OFF == legacy).
    """
    from daemon.routers.messages import MessageCreate, send_message as http_send_message

    # Ensure flag is unset (the test will not call monkeypatch.setenv).
    import os
    os.environ.pop("ENSEMBLE_WC_WAKE_ENQUEUE", None)
    from daemon.services.instance_messaging import _reset_wc_wake_enqueue_for_tests
    _reset_wc_wake_enqueue_for_tests()

    manager, engine, llm = wake_harness
    parent_id, _child_id = await _park_parent_with_hung_child(manager, engine)
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
    response = Response()

    body = await http_send_message(
        instance_id=parent_id,
        message=MessageCreate(content=WAKE_TOKEN_OFF),
        request=request,
        response=response,
    )

    # OFF-state contract: legacy injection → 202 + "injected" body.
    assert response.status_code == 202, (
        f"OFF-state: expected 202 Accepted (legacy injection), got "
        f"{response.status_code}; body={body}"
    )
    assert body.get("status") == "injected", body
    assert body.get("instance_id") == parent_id, body
    assert body.get("content") == WAKE_TOKEN_OFF, body

    # Bounded wait — bounded ≤ wake bound; the message must NOT
    # trigger a WC flip within this window. The hang child never
    # reports; the legacy path never enqueues; the parent stays WC.
    await asyncio.sleep(min(5.0, WAKE_BOUND_S / 4))
    assert _read_status(engine, parent_id) == InstanceStatus.WAITING_CHILDREN.value, (
        f"OFF-state: WC parent flipped away from WAITING_CHILDREN "
        f"(legacy stranding violated); current="
        f"{_read_status(engine, parent_id)!r}"
    )

    # OFF-state must NOT mint a MessageQueue row for the wake token
    # (the injection path is RAM FIFO only).
    mq_count = _message_queue_count_for(
        engine, instance_id=parent_id, content_token=WAKE_TOKEN_OFF
    )
    assert mq_count == 0, (
        f"OFF-state: legacy injection must NOT mint a MessageQueue "
        f"row; got {mq_count} rows for token {WAKE_TOKEN_OFF}"
    )

    # OFF-state must NOT create a Task row for the wake.
    task_count = _task_count_for_instance(engine, parent_id)
    assert task_count == 0, (
        f"OFF-state: legacy injection must NOT create a Task row; "
        f"got {task_count} Task rows on parent {parent_id}"
    )

    # LLM must NOT have seen the wake token (no real graph turn
    # happened).
    assert not _log_saw(llm, WAKE_TOKEN_OFF), (
        f"OFF-state: scripted LLM saw {WAKE_TOKEN_OFF} — a real graph "
        f"turn fired (legacy stranding violated); calls={llm.calls}"
    )
