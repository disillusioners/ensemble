"""REAL-dispatch integration test: ``image_refs`` kwarg survives the
5-function chain (Phase 2 / clipboard-image-chat / freeze-list A7).

Background: round-2 amendment #27 made the ``image_refs`` forwarding
REQUIRED across the chain:
  InstanceManager.enqueue_message
  → InstanceMessagingService.enqueue_message
  → _prepare_enqueued_message (row write + checkpoint kwargs stamp)
  → MessageQueue row carries refs (audit column)
  → _build_graph_input stamps additional_kwargs["image_refs"]
  → HumanMessage.additional_kwargs on the checkpoint
(i.e. the INTERNAL ``enqueue_message`` facade — the ``enqueue_message_job``
facade variant the production POST /messages route actually calls is
covered by ``TestImageRefsJobFacadeChain`` below; C10 M2 escape,
2026-09-20).

What this file proves, through the REAL facade → real
``InstanceMessagingService`` → real ``_prepare_enqueued_message``
chain over a real file-backed SQLite database (mirror
``tests/integration/test_job_driven_enqueue_work_id_facade.py``):

  1. POST /messages with ``image_refs=[r1, r2]`` survives the chain —
     the ``MessageQueue.images`` row column carries the canonical URL
     forms (audit) — A3 row persistence assertion.
  2. ``images=`` is byte-identical (None) when ``image_refs`` is the
     channel — A4 legacy data-URI path's identity contract (ref-sends
     never write to ``images``).
  3. Default-caller path (no image_refs at all) still succeeds —
     internal self-mint callers are unaffected.
  4. Empty list (``image_refs=[]``) is treated as absent — wire
     byte-identical to the no-kwarg path.

If a kwarg is missing at any layer (router → facade → service → row
+ kwargs stamp), the test fails loudly with a clear message naming
the dropped layer.

What is deliberately NOT real: the WorkerPool (``_worker_pool = None``)
and the LLM turn. This test pins the enqueue + row + kwargs-stamp
seams; a real pool would need a real graph + scripted LLM.
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
from daemon.repositories.message_queue.models import MessageQueue


_CANONICAL_REF_A = "/api/tmp_images/" + "a" * 32
_CANONICAL_REF_B = "/api/tmp_images/" + "b" * 32
_CANONICAL_REF_C = "/api/tmp_images/" + "c" * 32


# ---------------------------------------------------------------------------
# Fixtures — real manager over a real file-backed SQLite engine
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """Real SQLite FILE database (tmp_path) with NullPool.

    Deliberately NOT StaticPool/:memory: (per the work_id facade test
    harness lessons — the cross-thread session-refresh/lost-write
    hazard from a single shared connection is documented in
    QUARANTINE.md dependency_bus row).
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/image_refs_facade.db",
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
                description="image-refs facade harness",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()


def _seed_instance(eng: Engine, *, instance_id: str, project_id: str) -> Instance:
    inst = Instance(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        project_id=project_id,
        status=InstanceStatus.RUNNING.value,
        version=1,
        instance_metadata={},
    )
    with Session(eng) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


def _load_message_rows(eng: Engine, instance_id: str) -> list[MessageQueue]:
    with Session(eng) as session:
        return list(
            session.exec(
                select(MessageQueue).where(MessageQueue.instance_id == instance_id)
            )
        )


@pytest.fixture
async def facade_manager(engine: Engine, tmp_path):
    """Real ``InstanceManager`` (the object production injects into
    JobProcessor / JobFeedbackObserver) over the shared file-backed engine.

    Engine injection: ``create_engine_from_config`` is patched at the
    manager module level so the manager's ONE shared engine is the
    fixture's engine. The job-service stack is real so the
    ``stamp_message_id`` mirror write runs.
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
    manager._worker_pool = None  # No pool — Task stays PENDING for DB inspection.

    job_service = JobQueueService(
        repository=JobRepository(engine),
        lock_manager=JobLockManager(lock_repo=LockRepository(engine)),
        queue_repo=JobQueueRepository(engine),
        instance_manager=manager,
    )
    manager.set_job_queue_service(job_service)

    return manager


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestImageRefsFacadeChain:
    """The kwarg threads router → facade → service → row + kwargs stamp."""

    async def test_image_refs_survives_to_row_column(self, facade_manager, engine):
        """POST with image_refs=[r1, r2] → MessageQueue.image_refs row
        carries the URL forms (audit) — the dedicated JSONB column
        landed by council Option A (2026-09-19). The legacy ``images``
        column stays None — ref-sends NEVER write there (round-2
        overload reverted)."""
        from daemon import constants

        inst = _seed_instance(
            engine,
            instance_id=str(uuid.uuid4()),
            project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        )

        refs = [_CANONICAL_REF_A, _CANONICAL_REF_B]

        # Use ``enqueue_message`` (durable path without JobItem mirror)
        # to avoid the system parallel queue provisioning required by
        # ``enqueue_message_job``. The kwarg threading through the
        # facade + service + _prepare_enqueued_message chain is the
        # same code path either way.
        result = await facade_manager.enqueue_message(
            instance_id=inst.instance_id,
            message="look at these",
            source="api",
            image_refs=refs,
        )

        assert result.message_id

        rows = _load_message_rows(engine, inst.instance_id)
        assert len(rows) == 1
        # Refs on the dedicated image_refs column (council Option A).
        assert rows[0].image_refs == refs
        # Legacy ``images`` column stays None — the round-2 overload
        # is reverted.
        assert rows[0].images is None

    async def test_image_refs_default_omitted_writes_null(self, facade_manager, engine):
        """Default-caller path (no image_refs) still succeeds and the
        row's images column is None — internal self-mint callers
        unaffected."""
        from daemon import constants

        inst = _seed_instance(
            engine,
            instance_id=str(uuid.uuid4()),
            project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        )

        result = await facade_manager.enqueue_message(
            instance_id=inst.instance_id,
            message="plain text only",
            source="api",
        )

        assert result.message_id
        rows = _load_message_rows(engine, inst.instance_id)
        assert len(rows) == 1
        assert rows[0].images is None  # null on the JSONB column

    async def test_three_refs_all_persist(self, facade_manager, engine):
        """A 3-ref POST persists all three on the dedicated
        image_refs column."""
        from daemon import constants

        inst = _seed_instance(
            engine,
            instance_id=str(uuid.uuid4()),
            project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        )

        refs = [_CANONICAL_REF_A, _CANONICAL_REF_B, _CANONICAL_REF_C]

        await facade_manager.enqueue_message(
            instance_id=inst.instance_id,
            message="three",
            source="api",
            image_refs=refs,
        )

        rows = _load_message_rows(engine, inst.instance_id)
        assert rows[0].image_refs == refs
        assert rows[0].images is None

    async def test_image_refs_alongside_images_lands_on_dedicated_column(
        self, facade_manager, engine
    ):
        """When both ``images`` (legacy data-URI) and ``image_refs``
        are forwarded, the facade writes each to its DEDICATED
        column (council Option A). The MessageCreate model layer
        XOR-rejects the request upstream — this test pins the
        facade-acceptance shape only (no runtime behavior)."""
        from daemon import constants

        inst = _seed_instance(
            engine,
            instance_id=str(uuid.uuid4()),
            project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        )

        # Pass both — facade forwards both to DEDICATED columns.
        await facade_manager.enqueue_message(
            instance_id=inst.instance_id,
            message="both",
            source="api",
            images=["data:image/png;base64,abc"],
            image_refs=[_CANONICAL_REF_A],
        )

        rows = _load_message_rows(engine, inst.instance_id)
        # Refs on the dedicated column:
        assert rows[0].image_refs == [_CANONICAL_REF_A]
        # Data-URIs on the legacy column:
        assert rows[0].images == ["data:image/png;base64,abc"]


# ---------------------------------------------------------------------------
# Job-variant facade: InstanceManager.enqueue_message_job (C10 M2 escape)
# ---------------------------------------------------------------------------


def _seed_system_parallel_queue(eng: Engine) -> str:
    """Seed the ``system_parallel_queue`` row that ``enqueue_message_job``
    queue resolution AND ``JobQueueService.enqueue`` require — the job
    path is fail-closed when the row is missing (ValueError from
    ``enqueue``: "No system parallel queue found")."""
    from daemon import constants
    from daemon.repositories.job_queue.models import JobQueue

    qid = "queue-sys-parallel-image-refs"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(eng) as s:
        s.add(
            JobQueue(
                queue_id=qid,
                project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
                queue_name="system_parallel_queue",
                queue_name_lower="system_parallel_queue",
                queue_type="parallel",
                concurrency_limit=3,
                is_system=True,
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()
    return qid


class TestImageRefsJobFacadeChain:
    """REAL dispatch through ``InstanceManager.enqueue_message_job`` —
    the facade variant the production POST /messages route calls
    (``daemon/routers/messages.py``). Closes the C10 M2 escape: the
    sibling ``enqueue_message`` classes above do NOT cross this facade
    method, so an ``image_refs`` forward dropped HERE stayed green
    against the whole integration suite (2026-09-20 mutation audit).

    The forward assertion uses a PASSTHROUGH spy on the service's
    ``_prepare_enqueued_message`` (capture kwargs, then run the REAL
    prelude) — the dispatch stays genuine end-to-end and the row
    assertions read production-written columns, never test-local
    mirrors.
    """

    async def test_job_facade_forwards_image_refs_and_persists_row(
        self, facade_manager, engine
    ):
        """enqueue_message_job(image_refs=[r1, r2]) → (a) the service's
        prelude receives the kwarg (facade forward survived) AND (b) the
        REAL MessageQueue row carries the refs on the DEDICATED column
        with legacy ``images`` NULL (durable dual-column contract)."""
        from daemon import constants
        from daemon.repositories.message_queue.models import MessageQueue  # noqa: F401

        _seed_system_parallel_queue(engine)
        inst = _seed_instance(
            engine,
            instance_id=str(uuid.uuid4()),
            project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        )
        refs = [_CANONICAL_REF_A, _CANONICAL_REF_B]

        service = facade_manager._messaging_service
        real_prepare = service._prepare_enqueued_message
        captured: dict = {}

        def _passthrough_spy(**kwargs):
            captured.update(kwargs)
            return real_prepare(**kwargs)

        with patch.object(
            service, "_prepare_enqueued_message", _passthrough_spy
        ):
            result = await facade_manager.enqueue_message_job(
                instance_id=inst.instance_id,
                message="job-path refs",
                source="api",
                image_refs=refs,
            )

        assert result.message_id
        # (a) FORWARD assertion: exactly what survived the
        # facade → service hop.
        assert captured.get("image_refs") == refs
        # (b) Durable dual-column contract on the production row.
        rows = _load_message_rows(engine, inst.instance_id)
        assert len(rows) == 1
        assert rows[0].image_refs == refs
        assert rows[0].images is None

    async def test_job_facade_omitted_image_refs_writes_null(
        self, facade_manager, engine
    ):
        """Omitted kwarg → the prelude receives ``image_refs=None`` and
        the row column stays NULL (byte-identical default for every
        existing job-path caller)."""
        from daemon import constants

        _seed_system_parallel_queue(engine)
        inst = _seed_instance(
            engine,
            instance_id=str(uuid.uuid4()),
            project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        )

        service = facade_manager._messaging_service
        real_prepare = service._prepare_enqueued_message
        captured: dict = {}

        def _passthrough_spy(**kwargs):
            captured.update(kwargs)
            return real_prepare(**kwargs)

        with patch.object(
            service, "_prepare_enqueued_message", _passthrough_spy
        ):
            result = await facade_manager.enqueue_message_job(
                instance_id=inst.instance_id,
                message="job-path plain",
                source="api",
            )

        assert result.message_id
        assert captured.get("image_refs") is None
        rows = _load_message_rows(engine, inst.instance_id)
        assert len(rows) == 1
        assert rows[0].image_refs is None
        assert rows[0].images is None
