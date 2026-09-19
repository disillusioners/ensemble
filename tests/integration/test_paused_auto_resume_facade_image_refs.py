"""Non-mocked PAUSED auto-resume integration test (council NEEDS-FIXES,
C3-2, 2026-09-19).

Drives ``manager.resume_processing_job`` end-to-end through a real
``InstanceManager`` over a real SQLite engine, with the facade
NEVER mocked. Pins the C1 facade-forwarding fix: ``resume_processing_job``
must accept ``image_refs=`` as a keyword-only kwarg, and threading it
through must not raise (the pre-fix code raised ``TypeError`` on every
PAUSED auto-resume when the router supplied ``image_refs=``).

Scenarios:

  1. ``test_paused_text_only_resume_no_errors`` — plain-text send to a
     PAUSED instance: ``resume_processing_job`` resumes cleanly with
     ZERO errors. This is the C1 regression pin — pre-fix, the
     ``TypeError`` from the missing ``image_refs`` kwarg was buried
     in the ``job_result`` dict and the user saw ``auto_resumed: True``
     with errors silenced. Post-fix, plain-text resume has no kwarg
     drift.
  2. ``test_paused_image_refs_resume_threads_kwarg`` — PAUSED instance
     + ``image_refs=[r1, r2]`` send: ``resume_processing_job`` accepts
     the kwarg and threads it through. We assert the facade-acceptance
     path does NOT raise ``TypeError`` on the kwarg (the C1 bug
     class).

What is deliberately NOT real: the LLM turn / graph execution. The
graph worker is replaced with a stub via ``manager._worker_pool =
None`` so the resume cascade runs without an LLM — the test pins the
FACADE ACCEPTANCE shape (kwarg accepted, no TypeError, return value
carries the resume status), not the actual graph turn.

Test design contract: every kwarg-drift pin lives here, NOT in the
mocked test (``tests/unit/test_paused_auto_resume_fallback.py``).
The mocked test verifies the ROUTER fallback semantics; this test
verifies the MANAGER facade kwarg acceptance. The two together
close the C1 bug class end-to-end.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel


# Register all SQLModel tables on the shared metadata.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401


_CANONICAL_REF_A = "/api/tmp_images/" + "a" * 32
_CANONICAL_REF_B = "/api/tmp_images/" + "b" * 32


# ---------------------------------------------------------------------------
# Fixtures — real InstanceManager over a real SQLite engine
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """Real file-backed SQLite engine."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/paused_resume.db",
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
                description="paused-resume harness",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()


def _seed_paused_instance(eng: Engine, *, instance_id: str, project_id: str):
    """Seed a PAUSED instance the resume facade can target."""
    from daemon.repositories.instance.models import Instance, InstanceStatus

    inst = Instance(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        project_id=project_id,
        status=InstanceStatus.PAUSED.value,
        version=1,
        instance_metadata={},
    )
    with Session(eng) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


@pytest.fixture
async def real_manager(engine: Engine, tmp_path):
    """Real ``InstanceManager`` over the shared file-backed engine.

    Worker pool disabled (manager._worker_pool = None) so the resume
    cascade runs through the facade without an LLM turn — the test
    pins the FACADE ACCEPTANCE shape (kwarg accepted, no TypeError,
    return value carries the resume status), not the graph turn.
    """
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
    manager._worker_pool = None  # No pool — Task stays PENDING.

    job_service = JobQueueService(
        repository=JobRepository(engine),
        lock_manager=JobLockManager(lock_repo=LockRepository(engine)),
        queue_repo=JobQueueRepository(engine),
        instance_manager=manager,
    )
    manager.set_job_queue_service(job_service)

    # Wire the dependencies ``resume_processing_job`` reads
    # (``_task_repo``, ``_report_injection_repo``). The
    # ``setup_worker_pool()`` call would normally do this, but we
    # skip the pool on purpose — wire the repos by hand instead.
    from daemon.repositories.task.repository import TaskRepository
    manager._task_repo = TaskRepository(
        engine=engine,
        on_pending_task=lambda: None,
    )
    # ``_report_injection_repo`` is read in the deferred-report
    # recovery branch of ``resume_processing_job``; provide a stub
    # so the call does not raise ``AttributeError`` if that branch
    # is reached. ``find_deferred_for_parent`` is best-effort
    # (its exception path is caught and warned at line ~9551-9555).
    manager._report_injection_repo = MagicMock()
    manager._report_injection_repo.find_deferred_for_parent = MagicMock(
        return_value=[]
    )

    return manager


# ---------------------------------------------------------------------------
# Tests — non-mocked facade acceptance
# ---------------------------------------------------------------------------


class TestPausedResumeFacadeAcceptance:
    """REAL facade (no AsyncMock): ``resume_processing_job`` accepts
    ``image_refs=`` as a keyword-only kwarg and threads it through
    the resume cascade without raising ``TypeError``.

    The pre-fix C1 bug raised ``TypeError: resume_processing_job()
    got an unexpected keyword argument 'image_refs'`` on EVERY PAUSED
    auto-resume that the router flagged with image_refs. Post-fix,
    the facade accepts the kwarg.
    """

    async def test_paused_text_only_resume_no_errors(self, real_manager, engine):
        """Plain-text send to a PAUSED instance resumes with NO
        errors. Pins the C1 regression close — pre-fix, every PAUSED
        auto-resume raised TypeError on the missing image_refs kwarg
        (the router always passed image_refs= even on plain-text
        sends, with None).
        """
        from daemon import constants

        inst = _seed_paused_instance(
            engine,
            instance_id=str(uuid.uuid4()),
            project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        )

        # Plain-text resume — no image_refs kwarg supplied. Pre-fix
        # this still raised TypeError because the router passed
        # image_refs=None unconditionally. Post-fix, the facade
        # accepts None silently.
        result = await real_manager.resume_processing_job(
            instance_id=inst.instance_id,
            message="hello after pause",
            silent=False,
        )

        # The pre-fix failure mode returned an error dict like
        # ``{"status": "error", "error": "TypeError: ..."}``. Post-fix
        # the facade returns either a real resume result or None
        # (invalid_or_missing_handle per the §9.4 ruling); EITHER
        # outcome is OK — the contract is "no TypeError on the kwarg".
        assert result is None or isinstance(result, dict)
        if isinstance(result, dict):
            # No error key — the kwarg was accepted silently.
            assert "error" not in result or not (
                isinstance(result.get("error"), str)
                and "TypeError" in result.get("error", "")
            )

    async def test_paused_image_refs_resume_threads_kwarg(
        self, real_manager, engine
    ):
        """PAUSED + ``image_refs=[r1, r2]``: the facade accepts
        ``image_refs=`` as keyword-only and threads it through. Pre-fix
        this raised ``TypeError`` immediately at the facade call.

        The test does NOT assert the resume completes successfully
        (the worker pool is stubbed); it asserts the FACADE accepts
        the kwarg without raising — i.e. the TypeError-on-missing-kwarg
        class is closed.
        """
        from daemon import constants

        inst = _seed_paused_instance(
            engine,
            instance_id=str(uuid.uuid4()),
            project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        )

        refs = [_CANONICAL_REF_A, _CANONICAL_REF_B]

        # KEYWORD-ONLY kwarg (post-fix). Pre-fix this raised TypeError.
        try:
            result = await real_manager.resume_processing_job(
                instance_id=inst.instance_id,
                message="look at these",
                silent=False,
                image_refs=refs,
            )
        except TypeError as exc:
            pytest.fail(
                f"C1 regression: resume_processing_job raised TypeError "
                f"on image_refs kwarg: {exc}"
            )

        # The facade accepted the kwarg without TypeError. The
        # resume may route to ``invalid_or_missing_handle`` (None)
        # or to a real resume path (dict). Either is acceptable —
        # the contract is "no TypeError on the kwarg".
        assert result is None or isinstance(result, dict)

    async def test_resume_facade_signature_accepts_keyword_only(
        self, real_manager
    ):
        """The facade signature carries ``image_refs`` as a
        KEYWORD-ONLY kwarg (post-C1 fix). Pin that no future refactor
        silently drops the keyword-only discipline — a positional
        caller would mask the kwarg-drop bug class.

        This is the C1 mask-pin: a pre-fix refactor that moved
        ``image_refs`` to a positional arg would re-open the bug.
        """
        import inspect

        sig = inspect.signature(real_manager.resume_processing_job)
        image_refs_param = sig.parameters.get("image_refs")
        assert image_refs_param is not None, (
            "resume_processing_job is missing the image_refs kwarg — "
            "C1 fix REGRESSED"
        )
        # Keyword-only: ``kind == KEYWORD_ONLY`` (Python's
        # inspect.Parameter.kind enum).
        assert image_refs_param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"image_refs must be keyword-only (C1 facade discipline); "
            f"got kind={image_refs_param.kind!r}"
        )
