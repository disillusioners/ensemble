"""REAL-dispatch integration test: job-driven enqueue THROUGH the
``InstanceManager`` facade (blocker C1 acceptance, 2026-09-01).

The blocker: Fix A (constitution Phase 0) gave the service-layer
``InstanceMessagingService.enqueue_message`` a fail-closed
``work_id_required`` guard raising :class:`LinkageContractError` — but the
production wiring (``api.py``) injects the ``InstanceManager`` FACADE into
``JobProcessor`` / ``JobFeedbackObserver``, and the facade declared no
``work_id_required`` kwarg. All four job-driven dispatch sites died with
``TypeError`` at bind time, BEFORE the contract was reachable; the
``except Exception`` handlers degraded that into complete_job(FAILED) →
retry → dead-letter (the observer additionally M10-terminated the fresh
instance per attempt).

What this file proves, through the REAL facade → real
``InstanceMessagingService`` → real ``_prepare_enqueued_message`` chain
over a real file-backed SQLite database:

  1. A job-driven dispatch (``work_id_required=True``) with ``work_id``
     OMITTED raises :class:`LinkageContractError` — NOT ``TypeError``.
     (``pytest.raises(LinkageContractError)`` fails loudly if a
     ``TypeError`` propagates instead — the pre-fix failure mode.)
  2. A job-driven dispatch WITH ``work_id=job_id`` succeeds and the
     written ``Task`` row carries ``work_id == job_id`` — the
     ``Task.work_id == JobItem.job_id`` linkage contract.
  3. The default (no kwarg) internal path — agent-to-agent send_message,
     cascade-resume, child reports — still self-mints and succeeds, so
     the facade change broke no internal caller.

What is deliberately NOT real: the WorkerPool (``_worker_pool = None`` —
task CLAIM/consumption and the LLM turn are out of scope; this test pins
the enqueue seam, and a real pool would need a real graph + scripted LLM).
The job-service stack (``JobRepository`` / locks / queues) IS real so the
Option B mirror-stamp runs against a real seeded ``JobItem``.

Harness notes (repo lesson, QUARANTINE.md dependency_bus row): file-backed
SQLite via ``tmp_path`` with ``NullPool`` + WAL pragmas — NOT
``StaticPool``/``:memory:``, whose single shared connection trips the
documented cross-thread lost-write corruption. Same recipe as
``tests/integration/test_wc_wake_pure_hang.py``. ``MigrationRunner`` is
no-op'd — the quarantined pre-existing SQLite migration family
(20260714_000001) is orthogonal to this component.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.instance.models  # noqa: F401 (register tables)
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.task.models import Task
from daemon.services.messaging_types import LinkageContractError

_DISPATCH_SOURCE = "job:processor"


# ---------------------------------------------------------------------------
# Fixtures — real manager over a real file-backed SQLite engine
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """Real SQLite FILE database (tmp_path) with NullPool.

    Deliberately NOT StaticPool/:memory: — the harness mixes test-side
    sessions with manager/worker-side sessions against ONE database;
    StaticPool's single shared connection trips the documented
    cross-thread session-refresh/lost-write hazard (QUARANTINE.md
    dependency_bus row). A file DB with per-checkout connections + WAL
    mirrors production's concurrency shape.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/job_dispatch_facade.db",
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
    """Seed the system-default project row the manager paths validate."""
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
                description="job-driven facade dispatch harness",
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
    project_id: str,
    status: str = InstanceStatus.RUNNING.value,
) -> Instance:
    inst = Instance(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        project_id=project_id,
        status=status,
        version=1,
        instance_metadata={},
    )
    with Session(eng) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


def _seed_message_job(
    eng: Engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str,
) -> JobItem:
    """Insert the driving ``JobItem(job_type='message', ...)`` (ACTIVE —
    the state a JobItem is in while its dispatch is in flight)."""
    with Session(eng) as session:
        job = JobItem(
            job_id=job_id,
            agent_id="developer",
            agent_dir="/agents/developer",
            message="job-driven dispatch payload",
            source="api",
            project_id=project_id,
            queue_id=None,
            priority=1,
            admission_state=AdmissionState.ACTIVE.value,
            job_type="message",
            instance_id=instance_id,
            job_metadata={},
            max_retries=0,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
    return job


@pytest.fixture
async def facade_manager(engine: Engine, tmp_path):
    """Real ``InstanceManager`` (the object production injects into
    JobProcessor / JobFeedbackObserver via ``api.py``) over the shared
    file-backed engine.

    Engine injection: ``create_engine_from_config`` is patched at the
    manager module level so the manager's ONE shared engine is the
    fixture's engine — the test, the manager, and any service threads
    all see one database, mirroring production's one-shared-engine
    philosophy.
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
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.repositories.job_queue.queue_repository import (
        JobQueueRepository,
    )
    from daemon.repositories.job_queue.repository import JobRepository
    from daemon.services.job_lock_manager import JobLockManager
    from daemon.services.job_queue_service import JobQueueService

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
        manager = InstanceManager(config)

    manager._loop = asyncio.get_running_loop()
    # No worker pool: the Task must stay PENDING for DB inspection. Task
    # claim/consumption is out of scope — this test pins the enqueue seam.
    manager._worker_pool = None

    # Real job-service stack (mirrors the api.py lifespan ordering) so
    # the Option B ``stamp_message_id`` mirror write runs for real.
    job_service = JobQueueService(
        repository=JobRepository(engine),
        lock_manager=JobLockManager(lock_repo=LockRepository(engine)),
        queue_repo=JobQueueRepository(engine),
        instance_manager=manager,
    )
    manager.set_job_queue_service(job_service)

    return manager


def _load_tasks(eng: Engine, instance_id: str) -> list[Task]:
    with Session(eng) as session:
        return list(
            session.exec(select(Task).where(Task.instance_id == instance_id))
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestJobDrivenDispatchThroughFacade:
    """The facade must carry ``work_id_required`` to the service guard."""

    async def test_omitted_work_id_raises_linkage_contract_error(
        self, facade_manager, engine
    ):
        """Job-driven dispatch with ``work_id`` OMITTED must raise
        :class:`LinkageContractError` — NOT ``TypeError``.

        Pre-fix, the facade had no ``work_id_required`` kwarg, so this
        exact call died with ``TypeError`` at bind time and the service
        fail-closed guard was unreachable.
        """
        from daemon import constants

        inst = _seed_instance(
            engine,
            instance_id=str(uuid.uuid4()),
            project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        )

        with pytest.raises(LinkageContractError) as excinfo:
            await facade_manager.enqueue_message(
                instance_id=inst.instance_id,
                message="job-driven dispatch payload",
                source=_DISPATCH_SOURCE,
                work_id_required=True,  # work_id deliberately omitted
            )

        # The guard fired from the service prelude, before any DB write.
        assert "_prepare_enqueued_message" in str(excinfo.value)
        # No Task row may have been minted by the failed dispatch.
        assert _load_tasks(engine, inst.instance_id) == []

    async def test_work_id_present_succeeds_and_links_task(
        self, facade_manager, engine
    ):
        """Job-driven dispatch WITH ``work_id=job_id`` must succeed and
        the written Task row must carry ``work_id == job_id``."""
        from daemon import constants

        project_id = constants.SYSTEM_DEFAULT_PROJECT_ID
        inst = _seed_instance(
            engine, instance_id=str(uuid.uuid4()), project_id=project_id
        )
        job_id = str(uuid.uuid4())
        _seed_message_job(
            engine, job_id=job_id, instance_id=inst.instance_id,
            project_id=project_id,
        )

        result = await facade_manager.enqueue_message(
            instance_id=inst.instance_id,
            message="job-driven dispatch payload",
            source=_DISPATCH_SOURCE,
            work_id=job_id,
            work_id_required=True,
        )

        assert result.job_id == job_id
        tasks = _load_tasks(engine, inst.instance_id)
        assert len(tasks) == 1
        assert tasks[0].work_id == job_id

    async def test_default_path_still_self_mints(self, facade_manager, engine):
        """Internal callers (no kwarg at all) must be unaffected: the
        dispatch succeeds and the Task self-mints a fresh UUID handle."""
        from daemon import constants

        inst = _seed_instance(
            engine,
            instance_id=str(uuid.uuid4()),
            project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        )

        result = await facade_manager.enqueue_message(
            instance_id=inst.instance_id,
            message="internal nudge",
            source="internal_agent:parent-1",
        )

        assert result.job_id  # fresh UUID minted, not None
        tasks = _load_tasks(engine, inst.instance_id)
        assert len(tasks) == 1
        assert tasks[0].work_id == result.job_id
