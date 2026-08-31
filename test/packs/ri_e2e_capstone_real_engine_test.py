"""E2E capstone — report-integrity (b) gate on a REAL ENGINE (gate scope 8).

Reuses the boot harness pattern from
``test/packs/wc_wake_e2e_capstone_test.py`` (P1, committed) — no new
harness was invented. The P1 capstone proved the WC-wake ON/OFF
surface on a real engine; this capstone proves the ri (b)
NOTICE-INJECTION surface on a real engine.

BAR ACHIEVED: ``real-manager + live completion entry``. We boot the
real ``InstanceManager`` on a file-backed WAL SQLite database, wire
the real ``WorkerPool(1)`` + ``JobProcessor`` + ``JobQueueService``
+ ``WorkResolverService``, and drive the live completion entry on
real durable rows:

* ``service._process_child_completion_db_sync(child_id, ...)`` —
  the sync stamp path; commits the child terminal stamp.
* ``service._dispatch_post_commit_side_effects(child_result, ...)`` —
  the async post-commit dispatch (fire the bus hook, etc).
* ``service._process_child_completion_db_sync(parent_id, ...)`` —
  the sync stamp path; commits the parent's root_completed stamp
  with ``b_violation_report`` attached (same-tx evaluation).
* ``service._dispatch_post_commit_side_effects(parent_result, ...)`` —
  the async post-commit dispatch that calls
  ``enforce_declared_waiting_violations`` (D2.reconciler-bridge
  site). When the (b) flag is ON, this writes a real
  ``MessageQueue`` + ``Task`` row pair via ``manager.enqueue_message``.

We do NOT drive a graph turn for the parent processing the notice
— the assertion is the durable ``MessageQueue`` row, not its
consumption. The scripted LLM stub is in place if the WorkerPool
picks up the Task row before we query.

The P1 harness fixture is the engine bootstrap; this pack adds the
scenario seeder and the live completion entry driver on top.

SCENARIO (43070f6f-class silent-death replay — same shape the
hermetic ri_incident_repro_integration_test covers, but on the real
engine):

  1. Real parent + child instances (manager.spawn_instance).
  2. Parent stamped WAITING_CHILDREN (declared-waiting shape).
  3. Child stamped COMPLETED with a junk opener (zero tool calls).
  4. PENDING ``report_injections`` row staged for parent→child
     (the exact shape the (b) predicate reads).
  5. Drive the regular completion path (child → regular_child_completed).
  6. Drive the parent completion stamp (parent → root_completed).
  7. Drive the post-commit dispatch (fires (b) enforcement).

CASE OFF (ship default, flag unset):
  * The [ReportIntegrityGuard] declared-waiting violation WARNING
    fires (guard SAW it, log-only).
  * Parent reaches COMPLETED (documented log-only semantics).
  * ZERO ``MessageQueue`` rows with source
    ``system:report-integrity-guard``; ``_B_NOTICE_LEDGER`` empty.

CASE ON (monkeypatch.setenv flag "1" per-test, fresh resolver cache
— the resolver caches at first read; W1-pollution lesson):
  * Parent completes normally (fail-OPEN, D2.6).
  * An adjudication notice lands in the REAL ``MessageQueue``:
    source ``system:report-integrity-guard``, metadata
    ``report_integrity_notice true``, body cites the child id +
    ``[REPORT SANITY: ...]`` marker, NOT inside ``[SYSTEM NOTE]`` frame.
  * ``_B_NOTICE_LEDGER`` carries the parent (episode recorded).

The (c) marker rides the junk report envelope (always-on instrument,
independent of flag).

TEST-ENV ONLY. No production code changes, no daemon boot, no
sockets (<10000). The harness's DaemonConfig sets port=8079 but the
listener is NEVER started — FastAPI is not driven. The
WorkerPool(1) is started but only consumes notice Tasks created by
the ON-state enqueue (the assertion queries MessageQueue BEFORE
the worker can pick up).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)

# Pre-flight: ensure_system_default_project() is normally called at
# daemon lifespan startup. Mirror the wake-capstone recipe — set the
# constant explicitly so job-queue seeding at bootstrap finds a
# project. Without this the ``system_parallel_queue`` seeding in the
# harness raises ``NOT NULL constraint failed:
# job_queues.project_id``.
import daemon.constants  # noqa: E402
daemon.constants.SYSTEM_DEFAULT_PROJECT_ID = "ri-e2e-capstone-default-project"


PARENT_AGENT_ID = "leader"
CHILD_AGENT_ID = "worker"
JUNK_OPENER_HISTORY: list[dict] = [
    {"role": "user", "content": "Investigate the flaky queue test"},
    {
        "role": "assistant",
        "content": "I'll take a look at this now.",
        "tool_calls": [],
    },
]


# ---------------------------------------------------------------------------
# langgraph real-loader (mirrors wake-capstone's recipe)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def restore_langgraph_modules():
    """Undo the root-conftest langgraph mocks so the real engine loads.

    Same recipe as ``wc_wake_e2e_capstone_test.py`` and
    ``tests/integration/test_wc_wake_pure_hang.py`` — purge the
    MagicMock langgraph modules and pop the daemon modules that
    captured pre-pop bindings.
    """
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
def _reset_b_guard_resolver():
    """Reset the (b) guard resolver cache + ledger around every test.

    The resolver caches ``_B_GUARD_ENABLED`` at first read; the ledger
    is a module-level dict. Mirror the wc-wake fixture pattern —
    autouse reset around every test so OFF→ON transitions within a
    session observe the fresh env (W1-pollution lesson, applied to
    the ri guard).
    """
    import daemon.services.report_integrity_guard as rig

    rig._B_GUARD_ENABLED = None
    rig._B_NOTICE_LEDGER = {}
    rig._B_GUARD_BOOT_LOG_EMITTED = False
    yield
    rig._B_GUARD_ENABLED = None
    rig._B_NOTICE_LEDGER = {}
    rig._B_GUARD_BOOT_LOG_EMITTED = False


# ---------------------------------------------------------------------------
# Scripted LLM (consumption evidence — present but unused in Bar 2)
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    """Returns ``AIMessage`` so the graph's ``agent_node`` does not
    crash on ``response.content`` when the WorkerPool picks up the
    notice task. Mirrors the wake-capstone pattern with an AIMessage
    return shape.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def bind_tools(self, _tools):
        return self

    def invoke(self, messages, **_kwargs):
        from langchain_core.messages import AIMessage

        contents = [
            m if isinstance(m, str) else str(getattr(m, "content", ""))
            for m in messages
        ]
        self.calls.append(contents)
        last = contents[-1] if contents else ""
        return AIMessage(content=f"consumed:{last[-80:]}")


# ---------------------------------------------------------------------------
# Engine + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed WAL SQLite engine (mirrors wake-capstone)."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/ri_capstone.db",
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
    from daemon.repositories.project.models import Project, ProjectStatus

    pid = constants_project_id()
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Project(
                project_id=pid,
                name="_system_default",
                project_type="system",
                status=ProjectStatus.ACTIVE.value,
                description="ri capstone harness",
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
    engine: Engine,
    *,
    instance_id: str,
    source: str,
) -> int:
    with Session(engine) as s:
        rows = s.exec(
            select(MessageQueue).where(
                MessageQueue.instance_id == instance_id,
                MessageQueue.source == source,
            )
        ).all()
        return len(rows)


def _message_queue_rows_for(
    engine: Engine,
    *,
    instance_id: str,
    source: str,
) -> list[MessageQueue]:
    with Session(engine) as s:
        rows = s.exec(
            select(MessageQueue).where(
                MessageQueue.instance_id == instance_id,
                MessageQueue.source == source,
            )
        ).all()
        return list(rows)


# ---------------------------------------------------------------------------
# Real-engine harness (mirrors wc_wake_e2e_capstone_test.py:wake_harness)
# ---------------------------------------------------------------------------


@pytest.fixture
async def ri_harness(engine: Engine):
    """Real manager + real worker pool + real job processor on WAL SQLite.

    Same bootstrap recipe as ``wc_wake_e2e_capstone_test.py:wake_harness``
    — the only differences are the script path + project id. The
    harness yields ``(manager, engine, llm)``; teardown stops the
    worker pool + job processor cleanly.
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

    # Force the (b) enforcement dual-read gate to the "env ON + config
    # true" shape so the cap-driven ``enforce_declared_waiting_violations``
    # passes the gate when ``WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED=1``.
    # Without this the harness's default ``Config.report_integrity``
    # section (``b_terminal_waiting_guard_enabled=False``) defeats the
    # env flip — the dual-read gate ANDs both, so the env ON alone is
    # not enough. The cap is test-environment-only; production never
    # mutates this config.
    if hasattr(config, "report_integrity") and config.report_integrity is not None:
        config.report_integrity.b_terminal_waiting_guard_enabled = True

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

        # Wire the DependencyBus singleton — A8 is a HARD error in
        # ``_process_child_completion_db_sync`` if the bus singleton
        # is None. The bus needs a DependencyWatcherRepository bound
        # to our engine; ``start()`` is NOT required for the sync
        # ``count_pending_for_target_sync`` read used by the stamp
        # gate (we never emit terminal events in this test path).
        from daemon.repositories.dependency_bus.repository import (
            DependencyWatcherRepository,
        )
        from daemon.services.dependency_bus import (
            DependencyBus,
            set_dependency_bus,
        )

        bus = DependencyBus(DependencyWatcherRepository(engine))
        set_dependency_bus(bus)

        yield manager, engine, llm

        set_dependency_bus(None)
        if manager._worker_pool is not None:
            manager._worker_pool.stop(timeout=5)
        try:
            await job_processor.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Scenario seeder — real instances + the declared-waiting shape
# ---------------------------------------------------------------------------


async def _seed_declared_waiting_shape(
    manager, engine: Engine
) -> tuple[str, str]:
    """Spawn real parent + child; stage the declared-waiting shape.

    Steps (mirrors the hermetic ri_incident_repro scaffold):
      1. Spawn parent (root) via real manager.spawn_instance.
      2. Spawn child via real manager.spawn_instance(parent_id=...).
      3. Stamp parent to WAITING_CHILDREN (declared-waiting display).
      4. Leave child RUNNING (the live completion path will
         transition it to COMPLETED — pre-stamping COMPLETED would
         hit the idempotency short-circuit and the stamp would
         outcome=idempotency_skip, not regular_child_completed).
      5. Stage the PENDING ``report_injections`` row explicitly —
         this is the durable obligation the (b) predicate reads.
         In a live run the regular completion path writes this row
         itself; here we stage it deterministically so the test
         shape is exact and hermetic.

    Returns ``(parent_id, child_id)``.
    """
    parent_id, _ = manager.spawn_instance(agent_id=PARENT_AGENT_ID)
    child_id, _ = manager.spawn_instance(
        agent_id=CHILD_AGENT_ID, parent_id=parent_id
    )

    _set_status(engine, parent_id, InstanceStatus.WAITING_CHILDREN.value)
    # NOTE: child stays RUNNING — the regular completion path will
    # transition it to COMPLETED. Pre-stamping COMPLETED would trip
    # the idempotency short-circuit and the stamp would outcome as
    # ``idempotency_skip``, not ``regular_child_completed``.

    # Stage PENDING report_injections row (the durable obligation the
    # (b) predicate reads). Bypassing the live completion path's
    # report-row INSERT keeps the test deterministic — we are seeding
    # the durable shape the predicate consumes, not the path that
    # produces it.
    report_repo = ReportInjectionRepository(engine)
    with Session(engine) as session:
        report_repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=f"msg-child-{child_id[:8]}",
            report_message_id=f"rmsg-{child_id[:8]}",
            content="junk opener body (seeded)",
        )

    return parent_id, child_id


# ---------------------------------------------------------------------------
# Live completion entry driver (the Bar 2 spine)
# ---------------------------------------------------------------------------


async def _drive_live_completion(
    manager, engine: Engine, parent_id: str, child_id: str
) -> str:
    """Drive the live completion entry on real durable rows.

    Mirrors ``tests/integration/test_report_integrity_repro.py`` but
    uses the REAL ``ChildReportsService`` from the real
    ``InstanceManager`` and the REAL ``manager.enqueue_message``
    path (the assertion target for the ON case).

    Returns the parent report content (junk opener with (c) marker).
    """
    service = manager._child_reports_service

    # Patch the checkpoint fetch so ``_get_last_assistant_message``
    # returns the junk opener (zero tool-call evidence). The (c)
    # marker rides the report envelope via the Wave-1 instrument.
    with patch(
        "daemon.services.child_reports.get_instance_messages",
        new=AsyncMock(return_value=JUNK_OPENER_HISTORY),
    ):
        report_content = await service._get_last_assistant_message(
            child_id, CHILD_AGENT_ID
        )
    assert report_content is not None
    from daemon.constants import REPORT_SANITY_MARKER as SANITY_MARKER
    assert SANITY_MARKER in report_content, (
        "(c) marker must ride the terminal report envelope "
        "(D2.9 — always-on observability, independent of flag)"
    )

    # The child's regular completion stamp — commits the
    # child→COMPLETED transition + inserts the report_injections row
    # + enqueues the PROCESS_REPORT Task for the parent (this is the
    # path that creates the durable obligation in a live run; we
    # ALSO staged it above for determinism, so the INSERT here is
    # absorbed by the W6 IntegrityError savepoint — both paths land
    # the predicate-readable shape).
    #
    # The sync DB half runs on a worker thread via ``asyncio.to_thread``
    # (mirrors the production ``_process_child_completion_and_notify_parent``
    # pattern — ``child_reports.py:1884``). Calling the sync method
    # directly from the event loop risks blocking the loop on SQLite
    # write contention when the WorkerPool is alive.
    completed_message_id = f"msg-{child_id[:8]}"
    child_result = await asyncio.to_thread(
        service._process_child_completion_db_sync,
        child_id,
        completed_message_id,
        report_content,
    )
    assert child_result.outcome == "regular_child_completed", (
        f"expected regular_child_completed, got {child_result.outcome!r}"
    )

    # The child's async post-commit dispatch — fires the bus hook
    # and the lifecycle event. The bus singleton may be None in the
    # real-engine harness (it is not auto-started); the dispatch
    # path tolerates that with a WARNING and continues. We still
    # need the call to drive the parent-stamp path's normal side
    # effects (lifecycle event, SSE no-op).
    await service._dispatch_post_commit_side_effects(
        child_result, report_content, completed_message_id
    )

    # Drain the parent's pending MessageQueue rows — the live
    # ``regular_child_completed`` path enqueues a PROCESS_REPORT
    # notification for the parent, which leaves the parent in
    # WAITING_CHILDREN (root_waiting_children outcome) when stamped
    # directly. To reach ``root_completed`` (the outcome the (b)
    # enforcement site ``child_reports.py:3746`` lives in), we must
    # drain the pending messages first — this is the post-consume
    # shape in a live run where the WorkerPool has already drained
    # the notification task. We use a direct DELETE on the durable
    # rows to mirror the consumed-state; we do NOT need to invoke
    # the messaging service for this — the stamp's gate is the
    # durable row count, and the test's assertion target is the
    # (b) enforcement path, not the message-processing path.
    from sqlalchemy import delete as sa_delete
    from daemon.repositories.message_queue.models import MessageQueue as _MQ
    from daemon.repositories.task.models import Task as _Task

    with Session(engine) as session:
        session.execute(
            sa_delete(_MQ).where(_MQ.instance_id == parent_id)
        )
        session.execute(
            sa_delete(_Task).where(_Task.instance_id == parent_id)
        )
        session.commit()

    # The parent's root completion stamp — the (b) predicate runs
    # same-tx and the b_violation_report rides the result. Parent
    # becomes COMPLETED here.
    parent_result = await asyncio.to_thread(
        service._process_child_completion_db_sync,
        parent_id,
        "msg-parent-wrap-up",
        "parent wrap-up text",
    )
    assert parent_result.outcome == "root_completed", (
        f"expected root_completed, got {parent_result.outcome!r}"
    )
    assert parent_result.b_violation_report is not None
    assert parent_result.b_violation_report.is_violation is True
    assert parent_result.b_violation_report.count >= 1

    # The parent's async post-commit dispatch — fires the bus hook
    # AND (when the (b) flag is ON) the enforce_declared_waiting_violations
    # action, which calls ``manager.enqueue_message`` for the parent.
    await service._dispatch_post_commit_side_effects(
        parent_result, "parent wrap-up text", "msg-parent-wrap-up"
    )

    return report_content


# ---------------------------------------------------------------------------
# CASE OFF — ship default (flag unset)
# ---------------------------------------------------------------------------


async def test_off_state_log_only_byte_parity(
    ri_harness, monkeypatch: pytest.MonkeyPatch, caplog
):
    """(b) flag unset on the real engine → log-only byte-parity.

    The (b) guard SAW the declared-waiting violation (the
    [ReportIntegrityGuard] WARNING fires inside the stamp), the
    parent reached COMPLETED through the live path (fail-OPEN), and
    ZERO notice rows were minted — ``MessageQueue`` carries no row
    with source ``system:report-integrity-guard`` and
    ``_B_NOTICE_LEDGER`` is empty.
    """
    import daemon.services.report_integrity_guard as rig
    from daemon.constants import REPORT_SANITY_MARKER as SANITY_MARKER

    # Ensure flag is unset (the autouse fixture already cleared the
    # cache; this is the explicit env reset for safety).
    monkeypatch.delenv(
        "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED",
        raising=False,
    )
    rig._B_GUARD_ENABLED = None
    rig._B_NOTICE_LEDGER = {}
    assert rig.resolve_report_integrity_b_guard_enabled() is False

    manager, engine, _llm = ri_harness
    parent_id, child_id = await _seed_declared_waiting_shape(manager, engine)

    caplog.set_level(logging.DEBUG, logger="daemon.services.report_integrity_guard")
    await _drive_live_completion(manager, engine, parent_id, child_id)

    # Parent COMPLETED via the live path.
    assert _read_status(engine, parent_id) == InstanceStatus.COMPLETED.value, (
        f"OFF-state: parent must reach COMPLETED through the live "
        f"stamp path; got {_read_status(engine, parent_id)!r}"
    )

    # Guard SAW the declared-waiting violation (stage-ii log only).
    guard_warnings = [
        r
        for r in caplog.records
        if "declared-waiting violation" in r.getMessage()
    ]
    assert len(guard_warnings) >= 1, (
        f"OFF-state: [ReportIntegrityGuard] declared-waiting "
        f"violation WARNING must fire at least once; got logs="
        f"{[r.getMessage() for r in caplog.records]}"
    )

    # NO notice row minted (the OFF contract).
    notice_rows = _message_queue_count_for(
        engine,
        instance_id=parent_id,
        source="system:report-integrity-guard",
    )
    assert notice_rows == 0, (
        f"OFF-state: zero MessageQueue rows with source "
        f"system:report-integrity-guard expected; got {notice_rows}"
    )

    # Ledger empty (episode never opened — flag OFF short-circuits
    # before the dedupe write).
    assert parent_id not in rig._B_NOTICE_LEDGER, (
        f"OFF-state: _B_NOTICE_LEDGER must NOT record the parent "
        f"(flag OFF short-circuits before the dedupe write); "
        f"ledger keys={list(rig._B_NOTICE_LEDGER)!r}"
    )

    # (c) marker rode the report envelope — independent of flag.
    # Sanity-check the marker constant matches what the report
    # content carried (the live ``_get_last_assistant_message`` path
    # asserted this above; we re-verify the literal so the report
    # cites the exact marker the ON case will assert on too).
    assert SANITY_MARKER.startswith("[REPORT SANITY:")


# ---------------------------------------------------------------------------
# CASE ON — flag "1" — adjudication notice enqueued through real path
# ---------------------------------------------------------------------------


async def test_on_state_real_message_queue_row(
    ri_harness, monkeypatch: pytest.MonkeyPatch
):
    """(b) flag ON on the real engine → real ``MessageQueue`` notice row.

    The parent's stamp proceeded (fail-OPEN, D2.6) and the
    enforcement action enqueued ONE adjudication notice through
    ``manager.enqueue_message`` — a REAL durable ``MessageQueue`` row
    with source ``system:report-integrity-guard``, priority 0,
    metadata ``report_integrity_notice true``, body naming the
    terminal child + the (c) marker citation, NOT inside the
    ``[SYSTEM NOTE]`` frame.
    """
    import daemon.services.report_integrity_guard as rig
    from daemon.constants import REPORT_SANITY_MARKER as SANITY_MARKER

    # Flip the kill-switch BEFORE driving the completion path so the
    # resolver cache and the dual-read gate both read ON.
    monkeypatch.setenv(
        "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED", "1"
    )
    rig._B_GUARD_ENABLED = None
    rig._B_NOTICE_LEDGER = {}
    assert rig.resolve_report_integrity_b_guard_enabled() is True

    # The dual-read gate reads ``manager.config.report_integrity``
    # (env ON + config section absent ⇒ True, env ON + explicit
    # config ``false`` ⇒ False). The harness's Config has no
    # ``report_integrity`` section ⇒ True. Sanity-check:
    from daemon.services.report_integrity_guard import (
        is_report_integrity_b_enforcement_active,
    )
    assert is_report_integrity_b_enforcement_active(manager=None) is True

    manager, engine, _llm = ri_harness
    parent_id, child_id = await _seed_declared_waiting_shape(manager, engine)

    # Direct debug: verify enforcement gate with the actual manager.
    assert is_report_integrity_b_enforcement_active(manager=manager) is True, (
        "ON-state: dual-read gate must pass (env ON + config true); "
        "the harness's Config.report_integrity.b_terminal_waiting_guard_enabled "
        "is overridden to True in the harness bootstrap"
    )

    await _drive_live_completion(manager, engine, parent_id, child_id)

    # Brief settle window for the post-commit enqueue to land in
    # the durable ``MessageQueue`` row (the enqueue is awaited inside
    # the enforcement action — the row is committed BEFORE this
    # point — but a 100ms sleep is the documented hygiene margin for
    # the WAL writer on macOS, mirroring wake-capstone's pattern).
    await asyncio.sleep(0.1)

    # Parent transitioned through the live path: the (b) enforcement
    # notice was enqueued through ``manager.enqueue_message`` for an
    # instance that was stamped COMPLETED by the stamp; the messaging
    # service's "Auto-resume IDLE / WAITING_CHILDREN / COMPLETED
    # instances to RUNNING" seam (per the system-context instance
    # reuse + revive semantics) revives the parent to RUNNING so
    # the WorkerPool can deliver the notice. Either COMPLETED or
    # RUNNING is acceptable evidence that the parent completed the
    # stamp (the revive is the documented post-completion seam).
    parent_status = _read_status(engine, parent_id)
    assert parent_status in (
        InstanceStatus.COMPLETED.value,
        InstanceStatus.RUNNING.value,
    ), (
        f"ON-state: parent must transition through the live stamp "
        f"path (COMPLETED → RUNNING via the notice-enqueue revive "
        f"is expected); got {parent_status!r}"
    )

    # The adjudication notice landed in the REAL ``MessageQueue``.
    rows = _message_queue_rows_for(
        engine,
        instance_id=parent_id,
        source="system:report-integrity-guard",
    )
    assert len(rows) == 1, (
        f"ON-state: exactly ONE MessageQueue row with source "
        f"system:report-integrity-guard expected; got {len(rows)} "
        f"rows for parent={parent_id}"
    )

    notice_row = rows[0]
    # Source pin (RESERVED_SOURCE_PREFIXES + system:* dispatch-source
    # guard — the (b) guard is the only authorized writer).
    assert notice_row.source == "system:report-integrity-guard"

    # Metadata pin — the (b) guard stamps this flag for downstream
    # consumers (the parent-side LLM, the watchdog, the SSE
    # dispatcher). The MessageQueue model renames the Python attribute
    # to ``message_metadata`` (DB column ``metadata``) to avoid the
    # SQLAlchemy ``MetaData`` shadow — read via the renamed attr.
    metadata = notice_row.message_metadata or {}
    assert metadata.get("report_integrity_notice") is True, (
        f"ON-state: notice metadata must carry "
        f"report_integrity_notice=true; got metadata={metadata!r}"
    )

    # Body content — names the terminal child + cites the (c) marker
    # + NOT inside the [SYSTEM NOTE] frame (C2-D2.2).
    body = notice_row.content
    assert child_id in body, (
        f"ON-state: notice body must name the terminal child; "
        f"child_id={child_id!r} not in body={body[:200]!r}"
    )
    assert SANITY_MARKER in body, (
        f"ON-state: notice body must cite the (c) marker "
        f"(D2.9); marker={SANITY_MARKER!r} not in body={body[:200]!r}"
    )
    assert "[SYSTEM NOTE]" not in body, (
        f"ON-state: notice must NOT be inside the [SYSTEM NOTE] "
        f"frame (C2-D2.2); got body={body[:200]!r}"
    )
    assert "Report-integrity notice" in body, (
        f"ON-state: notice body must carry the Report-integrity "
        f"notice header; got body={body[:200]!r}"
    )

    # Episode recorded in the dedupe ledger.
    assert parent_id in rig._B_NOTICE_LEDGER, (
        f"ON-state: _B_NOTICE_LEDGER must record the parent "
        f"episode; ledger keys={list(rig._B_NOTICE_LEDGER)!r}"
    )

    # Priority pin — the (b) guard passes priority=0 (system lane).
    assert notice_row.priority == 0, (
        f"ON-state: notice priority must be 0 (system lane); "
        f"got {notice_row.priority!r}"
    )
