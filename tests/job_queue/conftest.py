"""Pytest configuration and fixtures for job queue tests."""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.job_queue import JobRepository, JobQueueRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.lock_repository import LockRepository
# Importing the Instance model registers it on ``SQLModel.metadata``
# so the ``instances`` table is created by ``create_all`` in the
# session-scoped engine fixture. Tests that exercise
# ``JobRepository.find_orphan_active_jobs`` (the orphan reaper, Phase 2
# of System Cleanup) correlate against ``instances`` — they would
# fail at compile time against an engine where the table is absent.
from daemon.repositories.instance.models import Instance  # noqa: F401
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService
from daemon.services import project_normalizer
from daemon.services.dependency_bus import set_dependency_bus
from daemon import constants

# ── Shared Test Constants ────────────────────────────────────────────────────────

TEST_SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


# ── Autouse Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def setup_system_default_project():
    """Set SYSTEM_DEFAULT_PROJECT_ID for tests that call enqueue() which normalizes project_id.

    project_normalizer uses daemon.constants.SYSTEM_DEFAULT_PROJECT_ID via module attribute access,
    so we only need to update the constants module binding.
    """
    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    constants.SYSTEM_DEFAULT_PROJECT_ID = TEST_SYSTEM_PROJECT_ID

    yield

    constants.SYSTEM_DEFAULT_PROJECT_ID = original


@pytest.fixture(autouse=True)
def _truncate_tables(engine):
    """Clear all tables around each test (function-scoped).

    Belt-and-braces isolation: truncate both BEFORE yield (to clear any
    data left over from session-scoped engine setup or earlier fixtures
    that populate data) and AFTER yield (to leave a clean slate for the
    next test). Mirrors the PostgreSQL ``_pg_truncate_tables`` pattern in
    ``tests/postgres/conftest.py``, adapted to ``DELETE FROM`` for SQLite
    compatibility.
    """
    def _truncate():
        from sqlmodel import Session, text
        from sqlalchemy import inspect
        # Only truncate tables that actually exist in the engine. Some
        # models (e.g. ``opencode_sessions``) get registered into
        # ``SQLModel.metadata`` AFTER the session-scoped ``engine``
        # fixture ran ``create_all`` — they are imported lazily inside
        # test bodies. Iterating ``metadata.tables`` blindly would issue
        # ``DELETE FROM`` against non-existent tables and raise
        # ``OperationalError: no such table`` at teardown.
        existing_tables = set(inspect(engine).get_table_names())
        with Session(engine) as session:
            for table in SQLModel.metadata.tables:
                if table not in existing_tables:
                    continue
                session.exec(text(f'DELETE FROM "{table}"'))
            session.commit()

    _truncate()
    yield
    _truncate()


@pytest.fixture(scope="session")
def engine():
    """Create in-memory SQLite engine for testing (session-scoped).

    Session-scoped to avoid re-creating 27+ tables for every test.
    Uses StaticPool to reuse the same connection across threads.
    Required because asyncio.to_thread() runs workers in different threads,
    and SQLite in-memory databases are per-thread by default.
    Tables are created once at session start; _truncate_tables clears data
    between tests for isolation.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repository(engine):
    """Create JobRepository instance with fresh database."""
    repo = JobRepository(engine)
    yield repo
    # Clean up after test (use hard_delete since tests may create jobs in various states)
    repo.hard_delete_terminal()
    repo.hard_delete_by_project("test-project")


@pytest.fixture
def lock_repo(engine):
    """Create LockRepository instance with fresh database."""
    return LockRepository(engine)


@pytest.fixture
def lock_manager(lock_repo):
    """Create fresh JobLockManager instance with lock_repo."""
    manager = JobLockManager(lock_repo=lock_repo)
    yield manager
    # Clean up locks using lock_repo directly (clear() raises NotImplementedError)
    all_locks = lock_repo.get_all_locks()
    for lock in all_locks:
        lock_repo.release(lock.lock_id)


# ── File-backed SQLite fixtures for concurrent tests (F11) ─────────────────
#
# These fixtures back the engine with a file on disk rather than the
# ``:memory:`` URL + StaticPool used by the regular fixtures above. They
# are REQUIRED for tests that exercise the new atomic slot-claim contract
# in ``LockRepository.try_acquire_slot`` (C5) under multi-thread fan-out:
#
#   * StaticPool shares a single connection across threads, so SQLite's
#     per-connection locking serialises the cursor access and we never
#     observe a real race. The race we want to test is the DB-level UNIQUE
#     conflict on ``uq_job_locks_slot`` — which only fires when two
#     connections race for the same slot.
#   * File-backed SQLite (default QueuePool) hands each thread its own
#     connection, exposing the real cross-connection UNIQUE conflict
#     path that the production code uses.
#
# Both fixtures are file-scoped per-test (tmp_path is function-scoped by
# pytest default), so concurrent tests do not pollute each other.

@pytest.fixture
def concurrent_lock_repo(tmp_path):
    """LockRepository backed by a file on disk (default QueuePool).

    Use this in tests that exercise concurrent ``try_acquire_slot``
    against the same (project_id, queue_id, lock_slot) triple. The
    file-backed engine hands each thread its own SQLite connection,
    which is necessary to observe the cross-connection UNIQUE
    conflict that makes the slot-claim invariant visible.
    """
    db_path = tmp_path / "job_locks_concurrent.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield LockRepository(eng)
    finally:
        eng.dispose()


@pytest.fixture
def concurrent_lock_manager(concurrent_lock_repo):
    """JobLockManager backed by a file-backed ``LockRepository`` (F11).

    Pair this with ``concurrent_lock_repo`` (or use it directly) in
    concurrent tests. Switches from ``:memory:`` + StaticPool to a
    file-backed SQLite with the default QueuePool, so multi-thread
    acquires each get their own connection and the DB-level UNIQUE
    conflict path is actually exercised.
    """
    return JobLockManager(lock_repo=concurrent_lock_repo)


@pytest.fixture
def concurrent_engine(tmp_path):
    """SQLAlchemy ``Engine`` backed by a file on disk (default QueuePool).

    Use this for tests that need a ``JobRepository`` exercising concurrent
    ``atomic_transition`` / ``atomic_retry`` / ``start_job`` writes
    against the same row. The file-backed engine hands each thread its
    own SQLite connection, which is necessary to observe the
    cross-connection UPDATE / SQL-guard race that the production code
    defends against.

    StaticPool (the default for the in-memory ``engine`` fixture) shares
    a single connection across threads and SQLite's per-connection
    parameter binding is not safe under concurrent statements —
    concurrent threads trip ``InterfaceError('bad parameter or other
    API misuse')`` instead of producing a clean ``ValueError`` /
    ``InvalidTransitionError`` for the loser.
    """
    db_path = tmp_path / "job_repository_concurrent.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def concurrent_repository(concurrent_engine):
    """JobRepository backed by a file-backed SQLite engine.

    Pair this with ``concurrent_engine`` (or use it directly) in
    concurrent tests that exercise ``atomic_transition``,
    ``atomic_retry``, or ``start_job``. Each thread gets its own
    SQLite connection, so the SQL-level guard produces a clean
    ``ValueError`` / ``InvalidTransitionError`` for the losing
    writer — exactly the contract these tests assert on.
    """
    return JobRepository(concurrent_engine)


@pytest.fixture
def queue_repository(engine):
    """Create JobQueueRepository instance with fresh database."""
    repo = JobQueueRepository(engine)
    yield repo


@pytest.fixture
def queue_repository_with_system_queues(engine):
    """Create JobQueueRepository with system queues pre-provisioned."""
    repo = JobQueueRepository(engine)
    # Pre-provision system queues for test-project
    repo.create(
        project_id="test-project",
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    repo.create(
        project_id="test-project",
        queue_name="system_parallel_queue",
        queue_type="parallel",
        concurrency_limit=3,
        is_system=True,
    )
    repo.create(
        project_id="test-project",
        queue_name="system_kb_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
        description="System FIFO queue for Knowledge Base import jobs",
    )
    # Also set up for project-1 and project-2 used in some tests
    repo.create(
        project_id="project-1",
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    repo.create(
        project_id="project-1",
        queue_name="system_parallel_queue",
        queue_type="parallel",
        concurrency_limit=3,
        is_system=True,
    )
    repo.create(
        project_id="project-1",
        queue_name="system_kb_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
        description="System FIFO queue for Knowledge Base import jobs",
    )
    repo.create(
        project_id="project-2",
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    repo.create(
        project_id="project-2",
        queue_name="system_parallel_queue",
        queue_type="parallel",
        concurrency_limit=3,
        is_system=True,
    )
    repo.create(
        project_id="project-2",
        queue_name="system_kb_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
        description="System FIFO queue for Knowledge Base import jobs",
    )
    # Also set up for the test system project ID (used when normalize_project_id() is called with None)
    repo.create(
        project_id=TEST_SYSTEM_PROJECT_ID,
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    yield repo


@pytest.fixture
def job_queue_service(repository, lock_manager, queue_repository_with_system_queues):
    """Create JobQueueService with system queues pre-provisioned.
    
    This fixture sets up system queues for test-project, project-1, and project-2
    so that tests with project_id can properly route jobs to their queues.
    """
    return JobQueueService(repository, lock_manager, queue_repository_with_system_queues)


@pytest.fixture
def sample_job_data():
    """Sample job creation data for repository tests."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "Test job message",
        "source": "api",
        "project_id": "test-project",
        "priority": 5,
        "job_metadata": {"test": True},
    }


@pytest.fixture
def sample_job_data_service():
    """Sample job creation data for service tests."""
    return {
        "agent_id": "developer",  # Use existing agent
        "message": "Test job message",
        "source": "api",
        "project_id": "test-project",
        "priority": 5,
        "metadata": {"test": True},
    }


@pytest.fixture
def sample_job_data_no_project():
    """Sample job creation data without project_id for repository."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "Test job without project",
        "source": "api",
        "project_id": None,
        "priority": 5,
        "job_metadata": None,
    }


@pytest.fixture
def sample_job_data_no_project_service():
    """Sample job creation data without project_id for service."""
    return {
        "agent_id": "developer",  # Use existing agent
        "message": "Test job without project",
        "source": "api",
        "project_id": None,
        "priority": 5,
        "metadata": None,
    }


@pytest.fixture
def sample_job_data_service_no_project(sample_job_data_no_project_service):
    """Alias for sample_job_data_no_project_service (for backward compatibility)."""
    return sample_job_data_no_project_service


@pytest.fixture
def high_priority_job_data():
    """High priority job data for repository ordering tests."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "High priority job",
        "source": "api",
        "project_id": "test-project",
        "priority": 10,  # Highest priority
        "job_metadata": None,
    }


@pytest.fixture
def high_priority_job_data_service():
    """High priority job data for service ordering tests."""
    return {
        "agent_id": "developer",  # Use existing agent
        "message": "High priority job",
        "source": "api",
        "project_id": "test-project",
        "priority": 10,  # Highest priority
        "metadata": None,
    }


@pytest.fixture
def low_priority_job_data():
    """Low priority job data for repository ordering tests."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "Low priority job",
        "source": "api",
        "project_id": "test-project",
        "priority": 1,  # Lowest priority
        "job_metadata": None,
    }


@pytest.fixture
def low_priority_job_data_service():
    """Low priority job data for service ordering tests."""
    return {
        "agent_id": "developer",  # Use existing agent
        "message": "Low priority job",
        "source": "api",
        "project_id": "test-project",
        "priority": 1,  # Lowest priority
        "metadata": None,
    }


# ── ADR-011 DependencyBus fixture ──────────────────────────────────────────────


def _make_test_dependency_bus():
    """Build a Mock DependencyBus singleton for unit tests.

    The production bus tracks pending watchers via DB-backed state
    (Phase 2+: the bus is the SOLE completion authority per
    ``child_reports.py:1081-1090`` and the gate at
    ``job_feedback_observer.py:3558-3564`` requires the singleton).
    Unit tests that don't exercise the bus's DB-backed behavior need
    a stand-in that:

    - Returns 0 for ``count_pending_for_target[_sync]`` (no pending
      watchers), so the cascade lane ``is_parent_complete`` check at
      ``child_reports.py:1090`` is satisfied.
    - Returns False/None for ``had_parent_error`` /
      ``parent_error_message`` so ``_resolve_finalize_status`` does
      not override the default status.
    - Returns ``[]`` from ``emit_terminal`` /
      ``emit_terminal_for_child_instance`` so no follow-up tasks are
      enqueued.
    - Returns an ``asyncio.Lock`` from ``_get_parent_lock`` so the
      ``async with await bus._get_parent_lock(...)`` block at
      ``child_reports.py:2139`` doesn't deadlock.

    The fixture resets the singleton on teardown so a failed test
    cannot leak the mock into the next test (the production invariant
    is "bus initialized at startup"; tests start fresh).
    """
    bus = MagicMock()

    async def _lock_factory(parent_id):
        return asyncio.Lock()

    # Sync helpers used inside DB sync threads
    bus.count_pending_for_target_sync = MagicMock(return_value=0)
    bus.count_pending_for_target = AsyncMock(return_value=0)

    # Parent-error override consulted by ``_resolve_finalize_status``
    bus.had_parent_error = MagicMock(return_value=False)
    bus.parent_error_message = MagicMock(return_value=None)

    # Orphan-race generation re-arm (job_feedback_observer.py:1684):
    # ``post_gen > pre_gen`` triggers a re-arm so late children's
    # resolves find a PROCESSING job. The mock returns 0 / 0 by
    # default so the re-arm is a no-op for tests that don't drive the
    # orphan-race path.
    bus.get_generation = MagicMock(return_value=0)

    # Terminal hooks — no-op (no follow-ups)
    bus.emit_terminal = AsyncMock(return_value=[])
    bus.emit_terminal_for_child_instance = AsyncMock(return_value=[])

    # Per-parent async lock — real lock (the wrapping
    # ``async with await bus._get_parent_lock(...)`` pattern needs a
    # real async context manager; AsyncMock doesn't natively support
    # ``async with`` on its return value).
    bus._get_parent_lock = _lock_factory

    # No-op mark-enqueued side effects
    bus.mark_enqueued_by_source_target = AsyncMock(return_value=None)

    return bus


@pytest.fixture(autouse=True)
def dependency_bus():
    """Provide a mock DependencyBus via ``set_dependency_bus`` for tests.

    Acceptance tests under ``tests/job_queue/`` exercise production code
    that hard-requires the bus singleton (ADR-011):
    ``_finalize_job_db_sync`` raises ``RuntimeError: DependencyBus is None ...``
    at ``job_feedback_observer.py:3560`` and ``child_reports.py:1107`` if
    the singleton is unset. This fixture installs a mock bus so the gate
    code paths exercise their real decision logic (not the None-error
    short-circuit) — preserving production semantics in tests.

    The bus's ``count_pending_for_target`` returns 0 so the cascade
    lane's ``is_parent_complete`` check at ``child_reports.py:1090`` is
    satisfied and tests focus on the freshness/gate semantics (the
    decision this acceptance set is meant to verify).
    """
    bus = _make_test_dependency_bus()
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


# ── Acceptance-set helpers ─────────────────────────────────────────────────────


@pytest.fixture
def observer_manager_setup(engine):
    """Configure a Mock ``InstanceManager`` with the real engine + write_guard.

    Acceptance tests under ``tests/job_queue/`` construct their manager
    as ``MagicMock()`` (per the round-1 fix pattern), but production
    code in ``JobFeedbackObserver._finalize_job_db_sync`` accesses
    ``self._instance_manager.engine`` and ``self._instance_manager.write_guard``
    to open the in-session ``WriteGuardSession`` that performs the
    terminal JobItem UPDATE. With a bare ``MagicMock`` for the manager,
    the WriteGuardSession wraps a Mock engine/guard and the in-session
    UPDATE becomes a no-op — the test then observes the JobItem stuck
    in ``ACTIVE`` instead of ``DONE``.

    This fixture pre-configures the engine + write_guard on a fresh
    ``MagicMock`` manager, and disables the report-repair branch (so a
    Mock ``config.report_repair`` does not blow up at the integer /
    MagicMock comparison in ``_get_last_assistant_message_raw``).

    Returns a callable ``(manager_mock=None) -> manager_mock`` that
    applies the configuration in place. Tests that already have a
    manager (e.g., the ``observer_env`` fixture) call this helper to
    patch the manager in place.
    """
    from daemon.write_pause_guard import WritePauseGuard

    guard = WritePauseGuard()

    def _setup(manager):
        # Real engine — required for WriteGuardSession to do real SQL.
        manager.engine = engine
        manager.write_guard = guard
        # Disable report-repair so the report_repair_cfg.size_ratio_threshold
        # path at child_reports.py:1726 short-circuits before any
        # comparison (MagicMock-vs-int would raise TypeError).
        manager.config = MagicMock()
        manager.config.report_repair = MagicMock()
        manager.config.report_repair.enabled = False
        manager.config.report_repair.repair_excluded_agents = []
        manager.config.report_repair.size_ratio_threshold = 5.0
        manager.config.report_repair.lookback_messages = 3
        return manager

    return _setup
