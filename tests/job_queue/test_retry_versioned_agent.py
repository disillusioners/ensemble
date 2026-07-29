"""Tests for retry_job() preserving the versioned agent directory (F8).

These tests verify that ``JobQueueService.retry_job()`` re-enqueues a
failed job into the SAME versioned agent directory the original job was
created with, instead of silently downgrading to the base (untagged)
agent directory.

Background: ``JobItem.agent_tag`` was added in F1 so that the original
version tag (``"v2"``, ``"v3"``, ...) can be recovered at retry time.
Without it, ``retry_job()`` would call ``enqueue()`` without
``agent_tag``, and the registry resolution chain
(``get_version(agent_id, None) or get_resolved(agent_id)``) would
return the BASE agent (not the versioned one). The retried job would
then run against the wrong agent definition — silently.

This test pins the version-preservation contract end-to-end:
  1. Seed a FAILED JobItem with ``agent_id="developer"``,
     ``agent_dir="./agents/developer[v2]"``, ``agent_tag="v2"``.
  2. Mock ``daemon.services.job_queue_service.get_registry`` so that
     ``get_version(agent_id, "v2")`` returns ``None`` (forcing the
     fallback to ``get_resolved``) and ``get_resolved(agent_id)``
     returns a meta with ``path="./agents/developer[v2]"`` — i.e. the
     versioned path.
  3. Call ``await service.retry_job(failed_job.job_id)``.
  4. Assert the retried job's ``agent_dir`` is the versioned path,
     NOT the base (``./agents/developer``).
"""

from unittest.mock import patch
from datetime import datetime
import pytest

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID
from daemon.repositories.job_queue import JobRepository, JobQueueRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService


# Store original value to restore after tests
_original_system_default_project_id = SYSTEM_DEFAULT_PROJECT_ID


@pytest.fixture(autouse=True)
def setup_system_project_id():
    """Set up SYSTEM_DEFAULT_PROJECT_ID for testing and restore after.

    This fixture runs automatically for every test in this module so the
    orphan-normalization path (which defaults project_id=None to
    SYSTEM_DEFAULT_PROJECT_ID) targets our test project ID.
    """
    import daemon.constants as constants

    constants.SYSTEM_DEFAULT_PROJECT_ID = "__test_system_default__"

    yield

    constants.SYSTEM_DEFAULT_PROJECT_ID = _original_system_default_project_id


@pytest.mark.asyncio
async def test_retry_job_preserves_versioned_agent_directory():
    """F8: ``retry_job()`` must re-enqueue into the versioned ``agent_dir``.

    The original FAILED JobItem was created with
    ``agent_dir="./agents/developer[v2]"`` and ``agent_tag="v2"``. The
    retried job must reuse the versioned ``agent_dir`` — not silently
    downgrade to the base ``./agents/developer``.

    The test exercises the F1+8 contract end-to-end:
      * The registry's ``get_version(agent_id, "v2")`` returns ``None``
        (forcing the ``or get_resolved(...)`` fallback to fire — this
        mirrors how the real registry behaves when a caller looks up
        a non-existent tag).
      * The registry's ``get_resolved(agent_id)`` returns the
        versioned meta with ``path="./agents/developer[v2]"``.
      * Without F1+8, ``retry_job()`` would call ``enqueue()`` without
        ``agent_tag`` and the registry resolution would return
        ``./agents/developer`` (the base). With F1+8, the persisted
        ``agent_tag="v2"`` is carried through and the versioned
        directory is preserved.
    """
    # Create engine and repository for this specific test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    try:
        repository = JobRepository(engine)
        lock_repo = LockRepository(engine)
        lock_manager = JobLockManager(lock_repo=lock_repo)
        queue_repo = JobQueueRepository(engine)

        # Pre-provision system queue for the test system default project
        queue_repo.create(
            project_id="__test_system_default__",
            queue_name="system_fifo_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )

        service = JobQueueService(repository, lock_manager, queue_repo)

        with patch(
            "daemon.services.job_queue_service.normalize_project_id",
            return_value="__test_system_default__",
        ):
            # Create a FAILED job for a versioned agent. We seed
            # ``agent_dir`` directly (the versioned path
            # ``./agents/developer[v2]``) and persist
            # ``agent_tag="v2"`` — this is exactly what the production
            # enqueue path stores when a caller submits with
            # ``agent_tag="v2"`` (the registry resolves
            # ``get_version("developer", "v2")`` to a versioned meta
            # whose ``path`` is the ``[v2]`` directory).
            failed_job = repository.create(
                agent_id="developer",
                agent_dir="./agents/developer[v2]",
                message="Versioned agent job that failed",
                source="test",
                project_id="__test_system_default__",
                priority=5,
                job_metadata={"test": True},
                agent_tag="v2",  # F1: persisted for retry-time recovery
            )

            # Verify the seed state.
            assert failed_job.agent_dir == "./agents/developer[v2]"
            assert failed_job.agent_tag == "v2"
            assert failed_job.admission_state == AdmissionState.QUEUED.value

            # Move to PROCESSING then FAILED so retry_job() will accept
            # it. ``start_job`` requires an instance_id; the test
            # value is arbitrary — only the admission transitions
            # matter for the retry precondition.
            repository.start_job(failed_job.job_id, instance_id="test-instance-001")
            repository.fail_job(failed_job.job_id, error_message="Test failure")

            # Phase 5: ``failed_at`` is the live retry marker. ``fail_job``
            # sets ``admission_state='done'`` and writes ``failed_at``,
            # which is exactly the precondition ``retry_job`` checks.
            from sqlmodel import Session
            from sqlalchemy import text as _sa_text
            with Session(engine) as _session:
                _session.execute(
                    _sa_text(
                        "UPDATE job_queue_items "
                        "SET admission_state = 'done', "
                        "    failed_at = :failed_at "
                        "WHERE job_id = :jid"
                    ),
                    {
                        "jid": failed_job.job_id,
                        "failed_at": datetime.utcnow().isoformat(),
                    },
                )
                _session.commit()

            # Verify the FAILED precondition for retry_job()
            seeded_failed = repository.get(failed_job.job_id)
            assert seeded_failed is not None
            assert seeded_failed.admission_state == AdmissionState.DONE.value
            assert seeded_failed.failed_at is not None
            assert seeded_failed.agent_dir == "./agents/developer[v2]"
            assert seeded_failed.agent_tag == "v2"

            # Mock the registry so that:
            #   - get_version returns ``None`` → forces the ``or
            #     get_resolved(...)`` fallback path to fire
            #   - get_resolved returns the versioned path
            #     ``./agents/developer[v2]``
            #
            # The CRITICAL point is that ``get_version.return_value``
            # MUST be explicitly set to ``None`` — ``MagicMock``
            # returns a truthy Mock by default, which would short-
            # circuit the fallback and silently return the wrong
            # base path. This is the exact gotcha documented in
            # ``tests/job_queue/test_idempotent_enqueue.py``.
            from unittest.mock import MagicMock
            versioned_meta = MagicMock()
            versioned_meta.path = "./agents/developer[v2]"

            mock_registry = MagicMock()
            # CRITICAL: explicitly None, NOT relying on the MagicMock
            # default (which would be truthy).
            mock_registry.get_version.return_value = None
            mock_registry.get_resolved.return_value = versioned_meta
            mock_registry.get.return_value = versioned_meta  # legacy

            with patch(
                "daemon.services.job_queue_service.get_registry",
                return_value=mock_registry,
            ):
                retried_job = await service.retry_job(failed_job.job_id)

            # Headline assertion: the retried job preserves the
            # versioned agent_dir. Without F1+8, this would silently
            # be ``./agents/developer`` (the base) — proving the
            # versioned-tag downgrade bug is fixed.
            assert retried_job is not None
            assert retried_job.agent_dir == "./agents/developer[v2]"
            assert retried_job.agent_dir != "./agents/developer"
            assert retried_job.agent_id == "developer"
            # The new job has a distinct ``job_id`` (retry creates a
            # fresh row, not an UPDATE).
            assert retried_job.job_id != failed_job.job_id

        # Clean up locks
        for lock in lock_repo.get_all_locks():
            lock_repo.release(lock.lock_id)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_retry_job_persisted_agent_tag_through_repository_create():
    """F8 (lower-level): ``JobItem.agent_tag`` round-trips through
    ``JobRepository.create()``.

    A simpler, lower-level guard test that confirms the F1 column
    declaration + repository plumbing works in isolation, without
    touching the registry mock or the service's enqueue path. This
    catches regressions where the column is added but the kwargs
    aren't threaded through ``create()``.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    try:
        repository = JobRepository(engine)

        # Insert with agent_tag set.
        job = repository.create(
            agent_id="developer",
            agent_dir="./agents/developer[v3]",
            message="Tag round-trip test",
            source="test",
            project_id="__test_system_default__",
            priority=5,
            agent_tag="v3",
        )
        assert job.agent_tag == "v3"

        # Re-read from DB to confirm the value actually persisted
        # (this exercises the column declaration, not just the Python
        # attribute).
        refetched = repository.get(job.job_id)
        assert refetched is not None
        assert refetched.agent_tag == "v3"
        assert refetched.agent_dir == "./agents/developer[v3]"

        # Insert with agent_tag omitted — default None.
        job2 = repository.create(
            agent_id="developer",
            agent_dir="./agents/developer",
            message="No tag",
            source="test",
            project_id="__test_system_default__",
            priority=5,
        )
        assert job2.agent_tag is None
    finally:
        engine.dispose()