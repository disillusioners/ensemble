"""Phase 4 Skill Evolution — periodic metric-scan handler tests.

Covers Task 7a of the Phase 4 plan. ``InstanceManager._run_skill_metric_scan``
is the maintenance-loop entry point that drives the Tier 1 trigger
engine and enqueues downstream analysis/evolution jobs.

Tests exercise the handler end-to-end against a real engine + the
Phase 4 repos so the enqueue path (``system_parallel_queue`` only)
and the ``job_type`` / ``job_metadata`` mapping are pinned.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, select

from daemon.config import Config, SkillEvolutionConfig
from daemon.manager import InstanceManager
from daemon.repositories.job_queue import (
    AdmissionState,
    JobItem,
    JobQueue,
    JobQueueRepository,
    QueueType,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.project.models import Project
from daemon.repositories.skill.models import (  # noqa: F401
    Skill,
)
from daemon.repositories.skill.repository import (
    SkillRepository,
    SkillTriggerRepository,
)


# ─── Engine + DB fixtures ──────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite + StaticPool + FK enforcement (all tables)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Import every model module so SQLModel.metadata has the full
    # schema before ``create_all`` runs.
    import daemon.repositories.job_queue.models  # noqa: F401
    import daemon.repositories.project.models  # noqa: F401
    import daemon.repositories.skill.models  # noqa: F401

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def project_id() -> str:
    return "test-project"


def _make_queue(engine, *, project_id, queue_name, queue_id=None):
    queue_id = queue_id or f"q-{uuid.uuid4().hex[:12]}"
    with Session(engine) as s:
        s.add(
            JobQueue(
                queue_id=queue_id,
                project_id=project_id,
                queue_name=queue_name,
                queue_name_lower=queue_name,
                queue_type=QueueType.PARALLEL.value,
                concurrency_limit=5,
                is_system=True,
            )
        )
        s.commit()
    return queue_id


def _make_project(engine, *, project_id, status="active"):
    with Session(engine) as s:
        s.add(
            Project(
                project_id=project_id,
                name=f"project-{project_id}",
                status=status,
                main_directory=f"/tmp/{project_id}",
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        s.commit()


# ─── Helper: build a manager with stubbed services + real repos ────────────


def _make_manager_with_services(
    engine: Engine,
    project_id: str,
    *,
    trigger_engine: Any = None,
    metrics_service: Any = None,
):
    """Build a manager-shaped object exposing the attributes the
    scan handler reaches into.

    We don't run ``InstanceManager.__init__`` (it would set up a
    second engine + dozens of services we don't care about). Instead
    we build a lightweight shim that mirrors the handler's access
    pattern (``self._skill_trigger_engine``, ``self._project_repository``,
    ``self._job_queue_service``).
    """
    queue_repo = JobQueueRepository(engine)
    job_repo = JobRepository(engine)
    trigger_repo = SkillTriggerRepository(engine)
    skill_repo = SkillRepository(engine)

    class _ProjectRepoStub:
        def __init__(self):
            self._projects: list[Any] = []

        def list_projects(self, *, status=None, limit=100, **kwargs):
            if status is None:
                return list(self._projects)
            return [
                p
                for p in self._projects
                if getattr(p, "status", None) == status
            ]

        def set_projects(self, projects):
            self._projects = projects

    class _StubManager:
        """Bare-minimum manager shim exposing only the attributes
        ``_run_skill_metric_scan`` reaches into."""

        _skill_trigger_engine: Any
        _skill_metrics_service: Any
        _engine: Engine
        _project_repository: Any
        _job_queue_service: Any

        async def _run_skill_metric_scan(self) -> None:
            # Bound at runtime via attach_to_instance below.
            raise NotImplementedError

    class _JobQueueServiceShim:
        def __init__(self):
            self._queue_repo = queue_repo
            self._repository = job_repo

    manager = _StubManager()
    manager._skill_trigger_engine = trigger_engine
    manager._skill_metrics_service = metrics_service
    manager._engine = engine
    manager._project_repository = _ProjectRepoStub()
    manager._job_queue_service = _JobQueueServiceShim()
    # Bind the production method onto the shim so the handler runs
    # against the real ``InstanceManager._run_skill_metric_scan``
    # implementation, but with this stub's wiring.
    manager._run_skill_metric_scan = (
        InstanceManager._run_skill_metric_scan.__get__(manager)
    )

    return manager, queue_repo, job_repo, trigger_repo, skill_repo


# ─── Tests ─────────────────────────────────────────────────────────────────


class TestSkillMetricScanSoftFail:
    """The handler short-circuits gracefully on missing wiring."""

    @pytest.mark.asyncio
    async def test_skips_when_trigger_engine_missing(
        self, engine, project_id
    ):
        manager, *_ = _make_manager_with_services(
            engine, project_id, trigger_engine=None
        )
        # Should NOT raise.
        await manager._run_skill_metric_scan()

    @pytest.mark.asyncio
    async def test_skips_when_project_repo_missing(
        self, engine, project_id
    ):
        trigger = AsyncMock()
        manager, *_ = _make_manager_with_services(
            engine, project_id, trigger_engine=trigger
        )
        manager._project_repository = None
        # Should NOT raise.
        await manager._run_skill_metric_scan()
        trigger.evaluate_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_job_queue_service_missing(
        self, engine, project_id
    ):
        trigger = AsyncMock()
        manager, *_ = _make_manager_with_services(
            engine, project_id, trigger_engine=trigger
        )
        manager._job_queue_service = None
        await manager._run_skill_metric_scan()
        trigger.evaluate_all.assert_not_awaited()


class TestSkillMetricScanEnqueue:
    """Flagged skills produce the correct downstream job."""

    @pytest.mark.asyncio
    async def test_enqueues_skill_analysis_on_system_parallel_queue(
        self, engine, project_id
    ):
        _make_project(engine, project_id=project_id)
        parallel_queue_id = _make_queue(
            engine,
            project_id=project_id,
            queue_name="system_parallel_queue",
        )

        trigger = MagicMock()
        trigger.evaluate_all = AsyncMock(
            return_value=[
                {
                    "skill_id": "skill-analyze-1",
                    "skill_name": "AnalyzeMe",
                    "trigger_name": "low_completion_rate",
                    "trigger_action": "analyze",
                    "reason": "completion_rate=0.20 < 0.3",
                    "stats": {"completion_rate": 0.2},
                }
            ]
        )

        manager, queue_repo, job_repo, _, _ = (
            _make_manager_with_services(
                engine, project_id, trigger_engine=trigger
            )
        )
        # Inject the project so list_projects returns it.
        manager._project_repository.set_projects(
            [MagicMock(project_id=project_id, status="active")]
        )

        await manager._run_skill_metric_scan()

        jobs, total = job_repo.list(project_id=project_id, limit=10)
        assert total == 1
        job = jobs[0]
        assert job.job_type == "skill_analysis"
        assert job.queue_id == parallel_queue_id
        assert job.source == "skill_metric_scan"
        assert job.agent_id == "skill-evolution"
        assert job.job_metadata["skill_id"] == "skill-analyze-1"
        assert job.job_metadata["trigger_name"] == "low_completion_rate"
        assert "evolution_type" not in job.job_metadata

    @pytest.mark.asyncio
    async def test_enqueues_skill_evolution_with_fix_type(
        self, engine, project_id
    ):
        _make_project(engine, project_id=project_id)
        _make_queue(
            engine,
            project_id=project_id,
            queue_name="system_parallel_queue",
        )

        trigger = MagicMock()
        trigger.evaluate_all = AsyncMock(
            return_value=[
                {
                    "skill_id": "skill-evolve-1",
                    "skill_name": "EvolveMe",
                    "trigger_name": "consecutive_failures",
                    "trigger_action": "evolve_fix",
                    "reason": "consecutive_failures=5 >= 3",
                    "stats": {"consecutive_failures": 5},
                }
            ]
        )

        manager, _, job_repo, _, _ = _make_manager_with_services(
            engine, project_id, trigger_engine=trigger
        )
        manager._project_repository.set_projects(
            [MagicMock(project_id=project_id, status="active")]
        )

        await manager._run_skill_metric_scan()

        jobs, total = job_repo.list(project_id=project_id, limit=10)
        assert total == 1
        job = jobs[0]
        assert job.job_type == "skill_evolution"
        assert job.job_metadata["skill_id"] == "skill-evolve-1"
        assert job.job_metadata["evolution_type"] == "FIX"

    @pytest.mark.asyncio
    async def test_no_flagged_skills_no_jobs(
        self, engine, project_id
    ):
        _make_project(engine, project_id=project_id)
        _make_queue(
            engine,
            project_id=project_id,
            queue_name="system_parallel_queue",
        )

        trigger = MagicMock()
        trigger.evaluate_all = AsyncMock(return_value=[])

        manager, _, job_repo, _, _ = _make_manager_with_services(
            engine, project_id, trigger_engine=trigger
        )
        manager._project_repository.set_projects(
            [MagicMock(project_id=project_id, status="active")]
            )

        await manager._run_skill_metric_scan()

        _, total = job_repo.list(project_id=project_id, limit=10)
        assert total == 0

    @pytest.mark.asyncio
    async def test_skips_project_without_parallel_queue(
        self, engine, project_id
    ):
        """Project exists but has no system_parallel_queue → no jobs."""
        _make_project(engine, project_id=project_id)
        # NOTE: do NOT create the system_parallel_queue.

        trigger = MagicMock()
        trigger.evaluate_all = AsyncMock(
            return_value=[
                {
                    "skill_id": "skill-x",
                    "skill_name": "X",
                    "trigger_name": "low_completion_rate",
                    "trigger_action": "analyze",
                    "reason": "r",
                    "stats": {},
                }
            ]
        )

        manager, _, job_repo, _, _ = _make_manager_with_services(
            engine, project_id, trigger_engine=trigger
        )
        manager._project_repository.set_projects(
            [MagicMock(project_id=project_id, status="active")]
            )

        await manager._run_skill_metric_scan()

        _, total = job_repo.list(project_id=project_id, limit=10)
        assert total == 0

    @pytest.mark.asyncio
    async def test_one_failed_project_does_not_block_others(
        self, engine
    ):
        """A trigger failure on one project does not prevent jobs
        for another project."""
        project_a = "proj-a"
        project_b = "proj-b"
        _make_project(engine, project_id=project_a)
        _make_project(engine, project_id=project_b)
        _make_queue(
            engine,
            project_id=project_a,
            queue_name="system_parallel_queue",
        )
        _make_queue(
            engine,
            project_id=project_b,
            queue_name="system_parallel_queue",
        )

        async def _evaluate(project_id_arg):
            if project_id_arg == project_a:
                raise RuntimeError("simulated engine failure")
            return [
                {
                    "skill_id": "skill-b",
                    "skill_name": "B",
                    "trigger_name": "task_count_scan",
                    "trigger_action": "analyze",
                    "reason": "ok",
                    "stats": {},
                }
            ]

        trigger = MagicMock()
        trigger.evaluate_all = AsyncMock(side_effect=_evaluate)

        manager, _, job_repo, _, _ = _make_manager_with_services(
            engine, project_a, trigger_engine=trigger
        )
        manager._project_repository.set_projects(
            [
                MagicMock(project_id=project_a, status="active"),
                MagicMock(project_id=project_b, status="active"),
            ]
        )

        await manager._run_skill_metric_scan()

        # Project A's failure is logged + skipped; project B's flag
        # still produced a job.
        _, total_b = job_repo.list(project_id=project_b, limit=10)
        _, total_a = job_repo.list(project_id=project_a, limit=10)
        assert total_b == 1
        assert total_a == 0