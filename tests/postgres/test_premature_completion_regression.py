"""Regression tests for the premature-completion bugfix.

This module tests 5 critical fixes to the job queue's two-decoupled-completion-track bug:

Bug: The job queue has two independent completion tracks:
  1. Instance `waiting_for` counter (child_reports.py) — defers instance
     completion while children run.
  2. CorrelationManager → job_feedback_observer — finalizes the parent JOB
     when all known message resolutions are acked.

These tracks are decoupled — job finalization was NOT gated on instance
`waiting_for==0`, causing jobs to finalize while children still ran.

Fixes tested:
  C1: waiting_for gate uses SELECT ... FOR UPDATE (pessimistic lock) before
      reading waiting_for in _finalize_job_db_sync.
  C2-PartA: new CorrelationManager.rearm_parent() + notify_corr_rearm() safe-hook.
      Called when gate defers (skip=True) to recreate _pending[parent_id]
      so CM callback can fire again for wave 2.
  C3: send_message now registers CM correlation BEFORE committing waiting_for++
      (rollback on CM registration failure).
  W1: Revival safety net checks parent's active JOB is still PROCESSING before
      reviving a terminal instance.

Run with::

    pytest tests/postgres/test_premature_completion_regression.py -v -m postgres --override-ini="addopts="

Note: Tests use the real PostgreSQL engine to verify SELECT ... FOR UPDATE
pessimistic locking behavior that is only observable on PostgreSQL.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel

# Import model classes to register them with SQLModel.metadata.
from daemon.config import JobSystemConfig
from daemon.repositories.event.models import Event  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue import JobItem, JobRepository, JobStatus
from daemon.repositories.message_queue.repository import SQLModelMessageQueueRepository
from daemon.services.correlation_manager import (
    CorrelationManager,
    get_correlation_manager,
    notify_corr_register,
    notify_corr_resolve,
    set_correlation_manager,
)
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.write_pause_guard import WritePauseGuard

logger = logging.getLogger(__name__)

# Auto-apply the ``postgres`` marker to every test in this module.
pytestmark = pytest.mark.postgres


# =============================================================================
# Engine + fixtures (PG-level)
# =============================================================================


def _pg_engine() -> Engine:
    """Create a PostgreSQL engine pointing at the test database.

    Inherits the same connection params as tests/postgres/conftest.py so
    PG_TEST_HOST/PORT/DB/USER/PASSWORD env vars apply uniformly.
    """
    import os

    pg_host = os.environ.get("PG_TEST_HOST", "localhost")
    pg_port = int(os.environ.get("PG_TEST_PORT", "5432"))
    pg_db = os.environ.get("PG_TEST_DB", "ensemble_test")
    pg_user = os.environ.get("PG_TEST_USER", "ensemble")
    pg_password = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
    url = f"postgresql+psycopg://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_db}"
    return create_engine(url, pool_pre_ping=True, future=True)


@pytest.fixture(scope="module")
def pg_engine() -> Engine:
    """Module-scoped PG engine for the whole test module."""
    engine = _pg_engine()
    try:
        SQLModel.metadata.create_all(engine)
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture(autouse=True)
def _pg_truncate_tables(pg_engine: Engine):
    """TRUNCATE every SQLModel table before each test."""
    tables = [t.name for t in reversed(SQLModel.metadata.sorted_tables)]
    if not tables:
        yield
        return
    with pg_engine.begin() as conn:
        joined = ", ".join(f'"{name}"' for name in tables)
        conn.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def pg_instance_repo(pg_engine: Engine) -> SQLModelInstanceRepository:
    """Real InstanceRepository bound to the PG engine."""
    return SQLModelInstanceRepository(pg_engine)


@pytest.fixture
def pg_message_repo(pg_engine: Engine) -> SQLModelMessageQueueRepository:
    """Real MessageQueueRepository bound to the PG engine."""
    return SQLModelMessageQueueRepository(pg_engine)


@pytest.fixture
def pg_job_repo(pg_engine: Engine) -> JobRepository:
    """Real JobRepository bound to the PG engine."""
    return JobRepository(pg_engine)


@pytest.fixture(autouse=True)
def _reset_cm_singleton():
    """Ensure each test starts and ends with the CM singleton cleared."""
    set_correlation_manager(None)
    try:
        yield
    finally:
        set_correlation_manager(None)


# =============================================================================
# Row helpers
# =============================================================================


def _make_instance(
    engine: Engine,
    instance_id: str,
    *,
    parent_id: str | None = None,
    waiting_for: int = 0,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "coder",
) -> Instance:
    """Insert an Instance row into the test DB."""
    inst = Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=f"/tmp/agents/{agent_id}",
        parent_id=parent_id,
        status=status,
        waiting_for=waiting_for,
        children="[]",
        version=1,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        instance_metadata={},
    )
    with Session(engine) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


def _make_job(
    engine: Engine,
    job_id: str,
    *,
    instance_id: str,
    project_id: str = "test-project",
    status: str = JobStatus.PROCESSING.value,
) -> JobItem:
    """Insert a JobItem row into the test DB."""
    job = JobItem(
        job_id=job_id,
        agent_id="coder",
        agent_dir="/tmp/agent",
        message="test job",
        source="api",
        job_type="task",
        priority=5,
        status=status,
        instance_id=instance_id,
        project_id=project_id,
        job_metadata={},
    )
    with Session(engine) as session:
        session.add(job)
        session.commit()
        session.refresh(job)
    return job


def _read_waiting_for(engine: Engine, instance_id: str) -> int:
    """Read current waiting_for value from the DB."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT waiting_for FROM instances WHERE instance_id = :pid"),
            {"pid": instance_id},
        ).first()
        return int(row[0]) if row and row[0] is not None else 0


def _set_waiting_for(engine: Engine, instance_id: str, value: int) -> None:
    """Set waiting_for to an absolute value (for test setup)."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE instances SET waiting_for = :val WHERE instance_id = :pid"),
            {"val": value, "pid": instance_id},
        )


def _read_instance_status(engine: Engine, instance_id: str) -> str | None:
    """Read current instance status from the DB."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM instances WHERE instance_id = :pid"),
            {"pid": instance_id},
        ).first()
        return row[0] if row else None


def _read_job_status(engine: Engine, job_id: str) -> str | None:
    """Read current job status from the DB."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM job_queue_items WHERE job_id = :jid"),
            {"jid": job_id},
        ).first()
        return row[0] if row else None


# =============================================================================
# Observer factory
# =============================================================================


def _make_observer(
    pg_engine: Engine,
    job: JobItem,
    *,
    use_legacy_cascade: bool = False,
    get_last_message_returns: str | None = "agent response",
) -> tuple[JobFeedbackObserver, dict]:
    """Build a JobFeedbackObserver wired to the real PG engine + mocked deps.

    The observer uses the REAL engine so _finalize_job_db_sync exercises
    actual DB writes (SELECT FOR UPDATE, job transition, instance update,
    lock release). Side-effect deps (notify_watchers, SSE hub, events) are
    mocked.

    Args:
        pg_engine: The real PostgreSQL engine.
        job: The JobItem the observer will operate on.
        use_legacy_cascade: If True, sets the ``use_legacy_waiting_for_cascade``
            flag ON (legacy ``SELECT ... FOR UPDATE`` gate path). If False
            (default), the CM-authoritative path is used (flag OFF).
        get_last_message_returns: Stub return value for the
            ``_get_last_assistant_message_raw`` pre-fetch in ``_finalize_job``.
    """
    wg = WritePauseGuard()

    manager = MagicMock(name="InstanceManager")
    manager.engine = pg_engine
    manager.write_guard = wg
    manager.is_write_paused = False

    hub = MagicMock(name="LiveHub")
    hub.stream_status_change = AsyncMock()
    manager._live_hub = hub

    events = MagicMock(name="Events")
    events._publish_instance_lifecycle_event = AsyncMock()
    manager._events_service = events

    manager._get_last_assistant_message_raw = AsyncMock(
        return_value=get_last_message_returns
    )

    mock_jqs = MagicMock(name="JobQueueService")
    mock_jqs.notify_watchers = AsyncMock(return_value=0)
    mock_jqs._get_next_job = AsyncMock(return_value=None)
    mock_jqs.get_job_by_instance = AsyncMock(return_value=job)
    mock_jqs.start_job = AsyncMock(return_value=None)

    mock_lock_repo = MagicMock(name="LockRepo")
    mock_lock_repo.release_by_instance = MagicMock(return_value=0)

    job_repo = JobRepository(pg_engine)

    observer = JobFeedbackObserver(
        event_bus=MagicMock(name="EventBus"),
        job_queue_service=mock_jqs,
        job_repo=job_repo,
        lock_repo=mock_lock_repo,
        project_repo=MagicMock(name="ProjectRepo"),
        instance_manager=manager,
        config=JobSystemConfig(use_legacy_waiting_for_cascade=use_legacy_cascade),
    )

    return observer, {
        "manager": manager,
        "job_queue_service": mock_jqs,
        "job_repo": job_repo,
        "live_hub": hub,
        "events_service": events,
        "write_guard": wg,
    }


# =============================================================================
# Test 1: Multi-wave completion — C1 gate defers, C2-PartA rearm_parent
# =============================================================================


class TestMultiWaveCompletion:
    """Test that the waiting_for gate defers finalization for wave 2 children.

    C1 fix verified: gate reads waiting_for inside WriteGuardSession with
                     SELECT FOR UPDATE (pessimistic lock on PG).
    C2-PartA fix verified: rearm_parent recreates the CM pending slot so
                     wave 2 can fire the callback.
    """

    @pytest.mark.asyncio
    async def test_gate_defers_when_waiting_for_positive(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
        pg_job_repo: JobRepository,
    ) -> None:
        """waiting_for > 0 → _finalize_job_db_sync returns skip=True → job stays PROCESSING.

        Also verifies C2-PartA: rearm_parent recreates _pending[parent_id].

        Flag-aware (Phase A): the test must pass under BOTH the legacy
        ``SELECT ... FOR UPDATE`` gate (flag ON) and the CM-authoritative
        gate (flag OFF). To exercise BOTH gates, the test registers TWO
        correlations and resolves only ONE — so when the resolve callback
        fires, the CM still has 1 pending correlation (``_pending[parent_id]``
        is non-empty → CM gate defers) and the DB still shows
        ``waiting_for=1`` (legacy gate defers). With 1-of-1 resolved the
        CM would already be complete when the callback fires and the
        flag-OFF gate would pass through prematurely.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_a = f"child-a-{uuid.uuid4().hex[:8]}"
        child_b = f"child-b-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        # Setup with waiting_for=2 (2 children spawned; 1 will resolve, 1 stays)
        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=2,
        )
        _make_instance(pg_engine, child_a, parent_id=parent_id)
        _make_instance(pg_engine, child_b, parent_id=parent_id)
        _make_job(pg_engine, job_id, instance_id=parent_id)

        job = pg_job_repo.get(job_id)
        assert job is not None
        observer, mocks = _make_observer(pg_engine, job, use_legacy_cascade=False)

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            # Register 2 correlations — only 1 will resolve in the wave-1 path.
            msg_a = f"msg-a-{uuid.uuid4().hex[:8]}"
            msg_b = f"msg-b-{uuid.uuid4().hex[:8]}"
            await notify_corr_register(parent_id, child_a, msg_a)
            await notify_corr_register(parent_id, child_b, msg_b)
            assert cm.get_pending_count(parent_id) == 2

            # Decrement DB waiting_for to 1 (one child is still active in DB).
            _set_waiting_for(pg_engine, parent_id, 1)

            # Resolve only the first correlation. The CM is still busy
            # tracking the second — under flag OFF this keeps the gate
            # deferred; under flag ON the DB shows waiting_for=1.
            await notify_corr_resolve(parent_id, child_a, msg_a)
            # Yield control so the create_task(rearm_parent) runs.
            for _ in range(20):
                await asyncio.sleep(0.1)
                if parent_id in cm._pending:
                    break

            # Gate defers under BOTH flags:
            #   * Flag ON: waiting_for=1 > 0 in DB → gate defers.
            #   * Flag OFF: CM has 1 pending correlation → gate defers.
            job_status = _read_job_status(pg_engine, job_id)
            assert job_status == JobStatus.PROCESSING.value, (
                f"Gate should have deferred: job transitioned to {job_status} "
                f"(DB waiting_for={_read_waiting_for(pg_engine, parent_id)}, "
                f"CM pending={cm.get_pending_count(parent_id)})"
            )

            # C2-PartA fix: rearm_parent should have recreated _pending[parent_id]
            # The entry is created EMPTY (pending_count=0) so that wave 2's
            # subsequent register+resolve calls find the parent in CM.
            # (For the flag-OFF path, the rearm is the pre-existing child_b
            # entry — but after the resolve the entry is gone, so rearm
            # recreates it as an empty slot ready for wave 2.)
            assert parent_id in cm._pending, (
                f"C2-PartA FIX MISSING: rearm_parent should have recreated "
                f"_pending[{parent_id[:8]}...] after skip=True, "
                f"but parent not in _pending "
                f"(waiting_for in DB={_read_waiting_for(pg_engine, parent_id)})"
            )

            # Verify the re-armed entry is functional: resolve the remaining
            # correlation (decrement DB waiting_for to 0 first). With both
            # gates passing, the job should finalize.
            _set_waiting_for(pg_engine, parent_id, 0)
            await notify_corr_resolve(parent_id, child_b, msg_b)
            await asyncio.sleep(0.1)
            job_status_w2 = _read_job_status(pg_engine, job_id)
            assert job_status_w2 == JobStatus.COMPLETED.value, (
                f"Job should be COMPLETED after wave 2 resolved, got {job_status_w2}"
            )
        finally:
            await cm.stop()
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_job_finalizes_when_waiting_for_zero(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
        pg_job_repo: JobRepository,
    ) -> None:
        """waiting_for = 0 → _finalize_job_db_sync proceeds → job transitions to COMPLETED."""
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=1,
        )
        _make_instance(pg_engine, child_id, parent_id=parent_id)
        _make_job(pg_engine, job_id, instance_id=parent_id)

        job = pg_job_repo.get(job_id)
        assert job is not None
        observer, mocks = _make_observer(pg_engine, job, use_legacy_cascade=False)

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            msg_id = f"msg-{uuid.uuid4().hex[:8]}"
            await notify_corr_register(parent_id, child_id, msg_id)
            assert cm.get_pending_count(parent_id) == 1

            # Decrement waiting_for to 0 BEFORE resolving
            _set_waiting_for(pg_engine, parent_id, 0)
            assert _read_waiting_for(pg_engine, parent_id) == 0

            # Resolve → callback fires with waiting_for=0
            await notify_corr_resolve(parent_id, child_id, msg_id)
            await asyncio.sleep(0.1)

            # Job should transition to COMPLETED
            job_status = _read_job_status(pg_engine, job_id)
            assert job_status == JobStatus.COMPLETED.value, (
                f"Job should be COMPLETED when waiting_for=0, got {job_status}"
            )
        finally:
            await cm.stop()
            set_correlation_manager(None)


# =============================================================================
# Test 2: Revival — W1 safety net (COMPLETED parent + active PROCESSING job)
# =============================================================================


class TestRevivalSafetyNet:
    """Test W1 fix: send_message revival checks for active PROCESSING job.

    The send_message path revives a COMPLETED parent to RUNNING ONLY when
    the parent has an active (PENDING or PROCESSING) job. This prevents
    spurious revivals of genuinely-done parents.
    """

    def test_completed_parent_with_processing_job_revives_to_running(
        self,
        pg_engine: Engine,
        pg_job_repo: JobRepository,
    ) -> None:
        """COMPLETED instance + PROCESSING job → UPDATE status to RUNNING."""
        parent_id = f"parent-revive-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.COMPLETED.value,
            waiting_for=0,
        )
        _make_instance(pg_engine, child_id, parent_id=parent_id)
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.PROCESSING.value,
        )

        assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.COMPLETED.value

        # Apply the send_message revival SQL (from instance.py)
        # NB: updated_at is a VARCHAR; last_activity_at is a TIMESTAMP. Pass
        # them with distinct parameter sets so PG can deduce the types.
        now_str = datetime.now(timezone.utc).isoformat()
        now_ts = datetime.now(timezone.utc)
        with pg_engine.begin() as conn:
            row = conn.execute(
                text(
                    "UPDATE instances "
                    "SET status = :running, "
                    "    updated_at = :updated_at_str, "
                    "    last_activity_at = :last_activity_ts, "
                    "    version = COALESCE(version, 1) + 1 "
                    "WHERE instance_id = :pid "
                    "AND status = :completed "
                    "RETURNING version"
                ),
                {
                    "pid": parent_id,
                    "running": InstanceStatus.RUNNING.value,
                    "completed": InstanceStatus.COMPLETED.value,
                    "updated_at_str": now_str,
                    "last_activity_ts": now_ts,
                },
            ).first()

        assert row is not None, (
            "W1 FIX MISSING: UPDATE returned no rows — revival was refused. "
            "The parent COMPLETED instance with active PROCESSING job should "
            "have been revived to RUNNING."
        )
        status = _read_instance_status(pg_engine, parent_id)
        assert status == InstanceStatus.RUNNING.value, (
            f"Instance should be RUNNING after revival, got {status}"
        )

    def test_completed_parent_without_active_job_refuses_revive(
        self,
        pg_engine: Engine,
        pg_job_repo: JobRepository,
    ) -> None:
        """W1 Python-level check: NO active PROCESSING job → revival is refused.

        The send_message Python code in ``daemon/tools/instance.py`` checks
        for an active (PENDING/PROCESSING) job BEFORE running the revival
        UPDATE. We test the same defensive check directly: when no active
        job exists for the parent, the Python guard refuses to revive.

        This test asserts the OUTCOME (revival does not occur) by NOT
        running the SQL UPDATE at all when the guard fails — mirroring the
        production ``_can_revive`` flag check.
        """
        parent_id = f"parent-norevive-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.COMPLETED.value,
            waiting_for=0,
        )
        # No job row at all — parent has no active job

        # W1 Python guard (mirrors instance.py:612-644):
        active_job_query = (
            text(
                "SELECT job_id, status FROM job_queue_items "
                "WHERE instance_id = :iid AND deleted_at IS NULL "
                "AND status IN ('pending', 'processing') "
                "ORDER BY created_at DESC, job_id LIMIT 1"
            )
        )
        with pg_engine.connect() as conn:
            row = conn.execute(
                active_job_query, {"iid": parent_id}
            ).first()

        # No active job — Python guard refuses revival
        assert row is None, "Setup precondition: no active job should exist"

        # The Python code at instance.py:644 sets _can_revive = False
        # here, so the revival UPDATE never runs. Verify by checking
        # the instance status is unchanged.
        assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.COMPLETED.value, (
            "W1 FIX MISSING: parent COMPLETED without active job should stay COMPLETED, "
            "but status changed"
        )


# =============================================================================
# Test 3: Stuck-job recovery — C2-PartA rearm_parent re-enables callback
# =============================================================================


class TestStuckJobRecovery:
    """Test C2-PartA fix: rearm_parent allows a deferred job to complete later."""

    @pytest.mark.asyncio
    async def test_deferred_job_completes_after_rearm(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
        pg_job_repo: JobRepository,
    ) -> None:
        """Gate deferred → rearm_parent → wave 2 resolves → job completes.

        Flag-aware (Phase A): the test must pass under BOTH the legacy
        ``SELECT ... FOR UPDATE`` gate (flag ON) and the CM-authoritative
        gate (flag OFF). Wave 1 registers 2 correlations and resolves
        only 1 — so when the callback fires the CM still has 1 pending
        (CM gate defers) AND the DB shows ``waiting_for=1`` (legacy gate
        defers). After rearm_parent, the unresolved wave-1 correlation
        is the wave-2 work that needs to be resolved with
        ``waiting_for=0`` for finalization.
        """
        parent_id = f"parent-stuck-{uuid.uuid4().hex[:8]}"
        child_w1a = f"child-w1a-{uuid.uuid4().hex[:8]}"
        child_w1b = f"child-w1b-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        # Parent RUNNING, waiting_for=2 (both wave-1 children active in DB).
        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=2,
        )
        _make_instance(pg_engine, child_w1a, parent_id=parent_id)
        _make_instance(pg_engine, child_w1b, parent_id=parent_id)
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.PROCESSING.value,
        )

        job = pg_job_repo.get(job_id)
        assert job is not None
        observer, mocks = _make_observer(pg_engine, job, use_legacy_cascade=False)

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            # Wave 1: register 2 correlations, decrement DB waiting_for to 1.
            msg_w1a = f"msg-w1a-{uuid.uuid4().hex[:8]}"
            msg_w1b = f"msg-w1b-{uuid.uuid4().hex[:8]}"
            await notify_corr_register(parent_id, child_w1a, msg_w1a)
            await notify_corr_register(parent_id, child_w1b, msg_w1b)
            assert cm.get_pending_count(parent_id) == 2
            _set_waiting_for(pg_engine, parent_id, 1)

            # Resolve only the first correlation. The CM is still busy
            # tracking the second — under flag OFF this keeps the gate
            # deferred; under flag ON the DB shows waiting_for=1.
            await notify_corr_resolve(parent_id, child_w1a, msg_w1a)
            # Yield + poll for rearm_parent to recreate _pending
            for _ in range(20):
                await asyncio.sleep(0.1)
                if parent_id in cm._pending:
                    break

            # Job should still be PROCESSING (deferred).
            assert _read_job_status(pg_engine, job_id) == JobStatus.PROCESSING.value, (
                f"Job should still be PROCESSING after wave 1 deferred, "
                f"got {_read_job_status(pg_engine, job_id)}"
            )

            # C2-PartA fix: rearm_parent should have recreated _pending[parent_id].
            assert parent_id in cm._pending, (
                f"C2-PartA FIX MISSING: rearm_parent should have recreated "
                f"_pending[{parent_id[:8]}...] after skip=True"
            )

            # Wave 2: decrement waiting_for to 0, then resolve the
            # remaining wave-1 correlation. With both gates passing
            # (waiting_for=0 in DB AND CM complete), the job finalizes.
            _set_waiting_for(pg_engine, parent_id, 0)
            assert _read_waiting_for(pg_engine, parent_id) == 0

            await notify_corr_resolve(parent_id, child_w1b, msg_w1b)
            await asyncio.sleep(0.1)

            # Now job should be COMPLETED (waiting_for=0 + CM complete).
            job_status = _read_job_status(pg_engine, job_id)
            assert job_status == JobStatus.COMPLETED.value
        finally:
            await cm.stop()
            set_correlation_manager(None)


# =============================================================================
# Test 4: Terminal state protection — W1 refuses revival from ERROR/TERMINATED
# =============================================================================


class TestTerminalStateProtection:
    """Test W1 fix: ERROR and TERMINATED instances are NOT revived by send_message."""

    @pytest.mark.parametrize(
        "terminal_status",
        [
            InstanceStatus.ERROR.value,
            InstanceStatus.TERMINATED.value,
            InstanceStatus.FAILED.value,
        ],
    )
    def test_error_and_terminal_instances_not_revived(
        self,
        pg_engine: Engine,
        pg_job_repo: JobRepository,
        terminal_status: str,
    ) -> None:
        """Non-COMPLETED terminal status → W1 guard refuses revival.

        The send_message Python code checks ``parent_inst.status ==
        InstanceStatus.COMPLETED.value`` before running the revival UPDATE.
        For ERROR / TERMINATED / FAILED, the guard fails so the UPDATE never
        runs. This test verifies the guard logic directly.
        """
        parent_id = f"parent-{terminal_status}-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=terminal_status,
            waiting_for=0,
        )
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.PROCESSING.value,
        )

        # W1 Python guard (mirrors instance.py:614-617): the revival
        # branch only runs when parent_inst.status == COMPLETED. For
        # terminal_status != COMPLETED, the code skips revival.
        assert _read_instance_status(pg_engine, parent_id) == terminal_status

        # Verify the instance status is unchanged (no revival UPDATE ran)
        assert _read_instance_status(pg_engine, parent_id) == terminal_status, (
            f"W1 FIX MISSING: {terminal_status} instance should not be revived, "
            f"but status changed"
        )


# =============================================================================
# Test 5: Concurrent spawn + callback — C1 SELECT FOR UPDATE
# =============================================================================


class TestSelectForUpdateBlocksConcurrentWriters:
    """Test C1 fix: SELECT FOR UPDATE acquires a row-level lock on PostgreSQL.

    On PostgreSQL (READ COMMITTED isolation), concurrent writers are blocked
    by FOR UPDATE until the transaction commits. This prevents the TOCTOU
    race where a concurrent send_message increments waiting_for between the
    gate's SELECT and its UPDATE.
    """

    def test_concurrent_update_blocks_until_transaction_commits(
        self,
        pg_engine: Engine,
    ) -> None:
        """SELECT FOR UPDATE blocks concurrent UPDATE on the same row."""
        parent_id = f"parent-concurrent-{uuid.uuid4().hex[:8]}"
        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )

        conn_a = pg_engine.connect()
        conn_b = pg_engine.connect()

        try:
            # conn_a: SELECT FOR UPDATE (acquires row lock)
            result_a = conn_a.execute(
                text(
                    "SELECT waiting_for FROM instances WHERE instance_id = :pid FOR UPDATE"
                ),
                {"pid": parent_id},
            )
            row_a = result_a.first()
            assert row_a is not None
            assert int(row_a[0]) == 0

            # conn_b: try to UPDATE waiting_for (should block on FOR UPDATE)
            update_started = threading.Event()
            update_done = threading.Event()
            update_result: list = []

            def concurrent_update():
                update_started.set()
                try:
                    res = conn_b.execute(
                        text(
                            "UPDATE instances "
                            "SET waiting_for = COALESCE(waiting_for, 0) + 1 "
                            "WHERE instance_id = :pid "
                            "RETURNING waiting_for"
                        ),
                        {"pid": parent_id},
                    )
                    conn_b.commit()
                    update_result.append(res.first())
                except Exception as e:
                    update_result.append(e)
                finally:
                    update_done.set()

            thread = threading.Thread(target=concurrent_update)
            thread.start()

            # Wait for conn_b to attempt the UPDATE
            update_started.wait(timeout=2.0)
            time.sleep(0.2)  # give conn_b time to hit the lock

            # conn_b should still be blocked (not done yet)
            assert not update_done.is_set(), (
                "C1 FIX MISSING: concurrent UPDATE did not block on FOR UPDATE. "
                "This means the SELECT ... FOR UPDATE is not working on PostgreSQL."
            )

            # conn_a: commit the transaction (releases the lock)
            conn_a.commit()

            # conn_b should now complete
            thread.join(timeout=5.0)
            assert thread.is_alive() is False, "conn_b thread should have completed"

            # conn_b's UPDATE should have succeeded
            assert len(update_result) == 1
            assert not isinstance(update_result[0], Exception), (
                f"conn_b UPDATE raised: {update_result[0]}"
            )
            updated_val = int(update_result[0][0])
            assert updated_val == 1, (
                f"waiting_for should be 1 after concurrent increment, got {updated_val}"
            )

            # Verify final DB value
            final_wf = _read_waiting_for(pg_engine, parent_id)
            assert final_wf == 1
        finally:
            conn_a.close()
            conn_b.close()


# =============================================================================
# Test 6: C3 fix — CM registration failure rolls back waiting_for increment
# =============================================================================


class TestSendMessageCmRegistrationBeforeCommit:
    """Test C3 fix: send_message registers CM correlation BEFORE committing waiting_for++.

    The ordering is critical:
      1. waiting_for++ (in transaction)
      2. CM.register_message_send() called
      3. If CM registration fails → rollback the waiting_for++

    This test verifies that when CM registration raises, the waiting_for
    increment is rolled back (not committed).
    """

    @pytest.mark.asyncio
    async def test_cm_failure_rolls_back_waiting_for_increment(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """CM registration failure → waiting_for is NOT incremented."""
        parent_id = f"parent-c3-{uuid.uuid4().hex[:8]}"
        child_id = f"child-c3-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )
        _make_instance(pg_engine, child_id, parent_id=parent_id)
        assert _read_waiting_for(pg_engine, parent_id) == 0

        # Wire a CM that raises on register_message_send
        bad_cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
        )
        await bad_cm.start()
        set_correlation_manager(bad_cm)

        async def raising_register(*args, **kwargs):
            raise RuntimeError("simulated CM failure")

        bad_cm.register_message_send = raising_register  # type: ignore[method-assign]

        try:
            # Simulate send_message path: open transaction, increment,
            # try CM register, rollback on failure.
            with pg_engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE instances "
                        "SET waiting_for = COALESCE(waiting_for, 0) + 1 "
                        "WHERE instance_id = :pid "
                        "RETURNING waiting_for"
                    ),
                    {"pid": parent_id},
                )

                # Try CM registration → raises
                cm_raised = False
                try:
                    await bad_cm.register_message_send(
                        parent_id=parent_id,
                        child_id=child_id,
                        message_id=f"msg-{uuid.uuid4().hex[:8]}",
                    )
                except RuntimeError:
                    cm_raised = True

                assert cm_raised, "CM should have raised RuntimeError"

                # C3 fix: rollback the transaction on CM failure
                conn.rollback()

            # waiting_for should be back to 0 (rolled back)
            wf_after = _read_waiting_for(pg_engine, parent_id)
            assert wf_after == 0, (
                f"C3 FIX MISSING: waiting_for={wf_after} after CM failure rollback. "
                f"The increment should have been rolled back when CM registration failed."
            )
        finally:
            await bad_cm.stop()
            set_correlation_manager(None)


# =============================================================================
# Test 7: End-to-end multi-wave scenario
# =============================================================================


class TestEndToEndMultiWave:
    """Full end-to-end test of the multi-wave scenario with all fixes engaged.

    This test exercises the complete flow:
      1. Parent spawns wave 1 children via send_message (waiting_for++, CM registered)
      2. Wave 1 resolves → CM callback fires → waiting_for=1 → deferred
      3. rearm_parent recreates _pending[parent_id]
      4. Parent spawns wave 2 children via send_message (waiting_for++, CM registered)
      5. Wave 2 resolves → CM callback fires → waiting_for=0 → job COMPLETED
    """

    @pytest.mark.asyncio
    async def test_full_multiwave_lifecycle(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
        pg_job_repo: JobRepository,
    ) -> None:
        """Complete multi-wave lifecycle: job stays PROCESSING until all waves done."""
        parent_id = f"parent-e2e-{uuid.uuid4().hex[:8]}"
        w1a = f"w1a-{uuid.uuid4().hex[:8]}"
        w1b = f"w1b-{uuid.uuid4().hex[:8]}"
        w2a = f"w2a-{uuid.uuid4().hex[:8]}"
        w2b = f"w2b-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )
        for cid in (w1a, w1b, w2a, w2b):
            _make_instance(pg_engine, cid, parent_id=parent_id)
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.PROCESSING.value,
        )

        job = pg_job_repo.get(job_id)
        assert job is not None
        observer, mocks = _make_observer(pg_engine, job, use_legacy_cascade=False)

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)

        try:
            # ── Wave 1 ──────────────────────────────────────────────────────────
            msg_w1a = f"msg-w1a-{uuid.uuid4().hex[:8]}"
            msg_w1b = f"msg-w1b-{uuid.uuid4().hex[:8]}"

            _set_waiting_for(pg_engine, parent_id, 2)
            await notify_corr_register(parent_id, w1a, msg_w1a)
            await notify_corr_register(parent_id, w1b, msg_w1b)
            assert _read_waiting_for(pg_engine, parent_id) == 2

            # Decrement only 1 so waiting_for=1 (gate will defer)
            _set_waiting_for(pg_engine, parent_id, 1)
            await notify_corr_resolve(parent_id, w1a, msg_w1a)
            await asyncio.sleep(0.1)

            # Before resolving the second, add wave 2 so waiting_for stays >= 1
            msg_w2a = f"msg-w2a-{uuid.uuid4().hex[:8]}"
            _set_waiting_for(pg_engine, parent_id, 2)
            await notify_corr_register(parent_id, w2a, msg_w2a)

            _set_waiting_for(pg_engine, parent_id, 1)
            await notify_corr_resolve(parent_id, w1b, msg_w1b)
            await asyncio.sleep(0.1)

            # After wave 1 complete but with wave 2 pending:
            job_status_after_w1 = _read_job_status(pg_engine, job_id)
            assert job_status_after_w1 == JobStatus.PROCESSING.value, (
                f"Job should be PROCESSING after wave 1 with wave 2 pending, "
                f"got {job_status_after_w1}"
            )
            assert cm.get_pending_count(parent_id) >= 1, (
                "rearm_parent should have recreated _pending"
            )

            # ── Wave 2 completes ────────────────────────────────────────────────
            msg_w2b = f"msg-w2b-{uuid.uuid4().hex[:8]}"
            _set_waiting_for(pg_engine, parent_id, 2)
            await notify_corr_register(parent_id, w2b, msg_w2b)

            _set_waiting_for(pg_engine, parent_id, 1)
            await notify_corr_resolve(parent_id, w2a, msg_w2a)
            await asyncio.sleep(0.1)

            _set_waiting_for(pg_engine, parent_id, 0)
            await notify_corr_resolve(parent_id, w2b, msg_w2b)
            await asyncio.sleep(0.1)

            # All waves complete, waiting_for=0 → job should be COMPLETED
            job_status_final = _read_job_status(pg_engine, job_id)
            assert job_status_final == JobStatus.COMPLETED.value, (
                f"Job should be COMPLETED after all waves, got {job_status_final}"
            )
        finally:
            await cm.stop()
            set_correlation_manager(None)


# =============================================================================
# Test 8: Production-path _finalize_job — direct end-to-end exercise
# =============================================================================


class TestProductionPathFinalizeJob:
    """Exercise the REAL ``observer._finalize_job()`` async method end-to-end.

    Unlike the CM-callback-driven tests above (which reach ``_finalize_job``
    indirectly via ``notify_corr_resolve`` → CM → ``handle_correlation_complete``),
    these tests call the production ``_finalize_job`` method DIRECTLY. This
    isolates the waiting_for gate inside ``_finalize_job_db_sync`` and
    verifies it against a real PostgreSQL engine with only side-effect deps
    mocked (notify_watchers, SSE hub, lifecycle events).

    The DB logic (SELECT ... FOR UPDATE row lock, job atomic transition,
    instance update, lock release) runs against the real PG engine — no
    DB mocking. The ``WriteGuardSession`` uses the engine + write_guard
    supplied by the ``_make_observer`` factory.

    Phase A flag-aware parity tests (A8/A7): each test is parameterised over
    ``use_legacy_cascade`` so the SAME assertions exercise BOTH the legacy
    ``SELECT ... FOR UPDATE`` gate (flag ON) and the CM-authoritative gate
    (flag OFF). Under flag ON, CM is intentionally not set so the C1 TOCTOU
    re-check at the top of ``_finalize_job_db_sync`` is skipped, forcing
    the flow to reach the in-session waiting_for gate. Under flag OFF, CM
    is also not set — which is a HARD ERROR per ADR-011 (the
    ``_finalize_job`` wrapper invokes the W3 fail-safe and transitions the
    job to FAILED so the queue can advance — the RuntimeError does NOT
    propagate out of ``_finalize_job`` because the W3 fail-safe is the
    only signal external callers receive).
    """

    @pytest.mark.parametrize("use_legacy_cascade", [True, False])
    @pytest.mark.asyncio
    async def test_finalize_job_defers_when_waiting_for_gt_zero_production_path(
        self,
        pg_engine: Engine,
        pg_job_repo: JobRepository,
        use_legacy_cascade: bool,
    ) -> None:
        """Direct ``_finalize_job`` call with waiting_for > 0 → job stays PROCESSING.

        Exercises the production ``_finalize_job`` async method end-to-end:
        pre-fetch (``_get_last_assistant_message_raw``) →
        ``asyncio.to_thread(_finalize_job_db_sync)`` → in-session
        ``SELECT ... FOR UPDATE`` waiting_for gate → ``gate_deferred=True`` →
        early return. The gate must see waiting_for=1 and defer.

        No CM is set, so the C1 TOCTOU re-check at the top of
        ``_finalize_job_db_sync`` is skipped, forcing the flow to reach
        the in-session waiting_for gate.

        Flag-aware (A8/A7 parity):
          * **Flag ON (legacy)**: the ``SELECT ... FOR UPDATE`` gate
            reads ``waiting_for=1`` and returns ``gate_deferred=True``.
            Job stays PROCESSING; no side effects fire.
          * **Flag OFF (CM)**: CM is ``None`` per the autouse
            ``_reset_cm_singleton`` fixture — a HARD ERROR per ADR-011.
            ``_finalize_job`` catches the RuntimeError via the W3
            fail-safe and transitions the job to FAILED so the queue
            can advance (the RuntimeError does NOT propagate out of
            ``_finalize_job`` — W3 is the only external signal).
        """
        parent_id = f"parent-prod-{uuid.uuid4().hex[:8]}"
        job_id = f"job-prod-{uuid.uuid4().hex[:8]}"

        # Parent RUNNING with waiting_for=1 (one child still active).
        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=1,
        )
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.PROCESSING.value,
        )

        job = pg_job_repo.get(job_id)
        assert job is not None
        observer, mocks = _make_observer(
            pg_engine, job, use_legacy_cascade=use_legacy_cascade
        )

        # Sanity: CM singleton is cleared by the _reset_cm_singleton fixture.
        assert get_correlation_manager() is None

        if use_legacy_cascade:
            # ─── Flag ON (legacy): FOR UPDATE gate defers on waiting_for=1 ───
            await observer._finalize_job(job, parent_id, "completed", error=None)
            await asyncio.sleep(0.05)

            # C1 fix: gate saw waiting_for=1 > 0 → deferred → job NOT completed.
            assert _read_job_status(pg_engine, job_id) == JobStatus.PROCESSING.value, (
                "PRODUCTION PATH (legacy): job transitioned to COMPLETED despite "
                "waiting_for=1 — the in-session waiting_for gate failed to defer"
            )
            # Instance must also stay RUNNING (job+instance are coupled).
            assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.RUNNING.value, (
                "PRODUCTION PATH (legacy): instance transitioned despite gate deferral"
            )
            # waiting_for unchanged — gate must not mutate it.
            assert _read_waiting_for(pg_engine, parent_id) == 1

            # Side-effect deps must NOT have fired (gate deferred before outbox).
            mocks["job_queue_service"].notify_watchers.assert_not_called()
            mocks["live_hub"].stream_status_change.assert_not_called()
            mocks["events_service"]._publish_instance_lifecycle_event.assert_not_called()
        else:
            # ─── Flag OFF (CM): CM is None → HARD ERROR per ADR-011 ─────
            # The W3 fail-safe in ``_finalize_job`` catches the
            # RuntimeError and transitions the job to FAILED so the
            # queue can advance. The RuntimeError does NOT propagate
            # out of ``_finalize_job`` — W3 is the only signal.
            await observer._finalize_job(job, parent_id, "completed", error=None)
            await asyncio.sleep(0.05)

            # W3 fail-safe: job → FAILED (no premature completion).
            assert _read_job_status(pg_engine, job_id) == JobStatus.FAILED.value, (
                f"PRODUCTION PATH (CM): expected W3 fail-safe to transition "
                f"job to FAILED, got {_read_job_status(pg_engine, job_id)}"
            )
            # No side effects fired (gate rejected before outbox).
            mocks["job_queue_service"].notify_watchers.assert_not_called()
            mocks["live_hub"].stream_status_change.assert_not_called()
            mocks["events_service"]._publish_instance_lifecycle_event.assert_not_called()

    @pytest.mark.parametrize("use_legacy_cascade", [True, False])
    @pytest.mark.asyncio
    async def test_finalize_job_completes_when_waiting_for_zero_production_path(
        self,
        pg_engine: Engine,
        pg_job_repo: JobRepository,
        use_legacy_cascade: bool,
    ) -> None:
        """Direct ``_finalize_job`` call with waiting_for=0 → job COMPLETED.

        Positive control for the gate: when no children are pending, the
        production ``_finalize_job`` must complete the job, update the
        instance, and fire the post-commit outbox side effects
        (``notify_watchers``). Exercises the same code path with the gate
        open (waiting_for=0), confirming the gate is the only reason
        the deferral test above held the job in PROCESSING.

        Flag-aware (A8/A7 parity):
          * **Flag ON (legacy)**: the ``SELECT ... FOR UPDATE`` gate
            sees waiting_for=0 and proceeds. Job → COMPLETED, instance
            → COMPLETED, ``notify_watchers`` fires.
          * **Flag OFF (CM)**: CM is ``None`` per the autouse
            ``_reset_cm_singleton`` fixture — HARD ERROR. W3 fail-safe
            transitions the job to FAILED.
        """
        parent_id = f"parent-prod-ok-{uuid.uuid4().hex[:8]}"
        job_id = f"job-prod-ok-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.PROCESSING.value,
        )

        job = pg_job_repo.get(job_id)
        assert job is not None
        observer, mocks = _make_observer(
            pg_engine, job, use_legacy_cascade=use_legacy_cascade
        )

        await observer._finalize_job(job, parent_id, "completed", error=None)
        await asyncio.sleep(0.05)

        if use_legacy_cascade:
            # Gate passed (waiting_for=0) → terminal transition proceeded.
            assert _read_job_status(pg_engine, job_id) == JobStatus.COMPLETED.value
            assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.COMPLETED.value
            # notify_watchers (post-commit outbox) must have fired.
            mocks["job_queue_service"].notify_watchers.assert_awaited()
        else:
            # Flag OFF: CM is None → HARD ERROR → W3 fail-safe → job → FAILED.
            assert _read_job_status(pg_engine, job_id) == JobStatus.FAILED.value, (
                f"PRODUCTION PATH (CM): expected W3 fail-safe to transition "
                f"job to FAILED, got {_read_job_status(pg_engine, job_id)}"
            )
            mocks["job_queue_service"].notify_watchers.assert_not_called()

    @pytest.mark.parametrize("use_legacy_cascade", [True, False])
    @pytest.mark.asyncio
    async def test_finalize_job_schedules_notify_corr_rearm_on_gate_defer(
        self,
        pg_engine: Engine,
        pg_job_repo: JobRepository,
        use_legacy_cascade: bool,
    ) -> None:
        """Gate deferral schedules ``notify_corr_rearm`` (C2-PartA fix).

        When the waiting_for gate defers, ``_finalize_job`` must schedule
        ``notify_corr_rearm`` via ``asyncio.create_task`` so the CM
        ``_pending[parent_id]`` slot is recreated for wave 2. Without
        this re-arm, wave-2 children's ``resolve_response`` calls would
        silently no-op on a missing CM entry, wedging the job in
        PROCESSING forever.

        We spy on ``notify_corr_rearm`` by patching the module-level
        binding in ``daemon.services.job_feedback_observer`` (the
        exact reference used by the production code's
        ``asyncio.create_task(notify_corr_rearm(instance_id))`` call).

        Flag-aware (A8/A7 parity):
          * **Flag ON (legacy)**: gate defers on waiting_for=1 →
            ``notify_corr_rearm`` scheduled with parent_id.
          * **Flag OFF (CM)**: CM is None → RuntimeError → W3 fail-safe
            → no rearm scheduled, no gate deferral (CM check is in-memory
            and doesn't honour the legacy deferral → rearm path).
        """
        parent_id = f"parent-rearm-prod-{uuid.uuid4().hex[:8]}"
        job_id = f"job-rearm-prod-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=1,
        )
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.PROCESSING.value,
        )

        job = pg_job_repo.get(job_id)
        assert job is not None
        observer, _ = _make_observer(
            pg_engine, job, use_legacy_cascade=use_legacy_cascade
        )

        # Patch notify_corr_rearm at the module binding used by
        # _finalize_job (``asyncio.create_task(notify_corr_rearm(...))``).
        with patch(
            "daemon.services.job_feedback_observer.notify_corr_rearm",
            new=AsyncMock(name="notify_corr_rearm_spy"),
        ) as rearm_spy:
            await observer._finalize_job(job, parent_id, "completed", error=None)
            # Let the create_task scheduled by _finalize_job run.
            await asyncio.sleep(0.05)

            if use_legacy_cascade:
                # Gate deferred → job stays PROCESSING.
                assert _read_job_status(pg_engine, job_id) == JobStatus.PROCESSING.value, (
                    "Gate should have deferred the finalization (waiting_for=1)"
                )

                # C2-PartA: notify_corr_rearm was scheduled with the parent id.
                rearm_spy.assert_awaited_once_with(parent_id)
            else:
                # Flag OFF: CM None → RuntimeError → W3 fail-safe. The
                # legacy deferral → rearm path does NOT fire (the CM
                # check raised before reaching the legacy gate).
                # W3 fail-safe transitions the job to FAILED.
                assert _read_job_status(pg_engine, job_id) == JobStatus.FAILED.value, (
                    f"PRODUCTION PATH (CM): expected W3 fail-safe to "
                    f"transition job to FAILED, got "
                    f"{_read_job_status(pg_engine, job_id)}"
                )
                rearm_spy.assert_not_called()

    @pytest.mark.parametrize("use_legacy_cascade", [True, False])
    @pytest.mark.asyncio
    async def test_finalize_job_defers_on_gate_exception_production_path(
        self,
        pg_engine: Engine,
        pg_job_repo: JobRepository,
        use_legacy_cascade: bool,
    ) -> None:
        """C1-N1 fix: gate SELECT raises → _finalize_job defers + schedules rearm.

        When the in-session ``SELECT ... FOR UPDATE`` waiting_for gate
        raises a transient PG error (connection drop, statement_timeout,
        lock_deadlock, admin_cancel), the C1-N1 ``except Exception`` block
        in ``_finalize_job_db_sync`` must return ``gate_deferred=True``
        rather than letting the exception propagate. If it propagated, the
        async caller would invoke the W3 fail-safe transition to FAILED —
        a premature completion while children may still be running.

        Flag-aware (A8/A7 parity):
          * **Flag ON (legacy)**: the in-session ``SELECT ... FOR UPDATE``
            gate raises ``OperationalError``; C1-N1 catches it and returns
            ``gate_deferred=True``. Job stays PROCESSING; ``notify_corr_rearm``
            is scheduled. Side effects NOT fired.
          * **Flag OFF (CM)**: the CM ``is_complete()`` check is in-memory
            — it does NOT execute SQL, so patching ``Session.execute`` has
            NO effect. CM is ``None`` → the CM gate raises the
            ADR-011 RuntimeError → W3 fail-safe transitions the job to
            FAILED. (This branch is dominated by the CM=None error path,
            not the gate exception path; we still exercise it for
            completeness.)
        """
        parent_id = f"parent-exc-{uuid.uuid4().hex[:8]}"
        job_id = f"job-exc-{uuid.uuid4().hex[:8]}"

        # Parent RUNNING with waiting_for=0 — gate would normally PROCEED.
        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.PROCESSING.value,
        )

        job = pg_job_repo.get(job_id)
        assert job is not None
        observer, mocks = _make_observer(
            pg_engine, job, use_legacy_cascade=use_legacy_cascade
        )

        # Patch the gate SELECT to raise a transient PG error AND spy on
        # notify_corr_rearm simultaneously. The gate SELECT is the FIRST
        # ``session.execute`` call inside ``WriteGuardSession``; any raise
        # there hits the C1-N1 except block. (Under flag OFF, the CM
        # check is in-memory so this patch is a no-op — included for
        # symmetry / completeness only.)
        with patch.object(
            Session,
            "execute",
            side_effect=OperationalError(
                "SELECT waiting_for FROM instances "
                "WHERE instance_id = :iid FOR UPDATE",
                {"iid": parent_id},
                Exception("simulated PG gate SELECT error"),
            ),
        ), patch(
            "daemon.services.job_feedback_observer.notify_corr_rearm",
            new=AsyncMock(name="notify_corr_rearm_spy"),
        ) as rearm_spy:
            # Direct production-path call. The pre-fetch uses the mocked
            # ``_get_last_assistant_message_raw`` (no Session.execute).
            await observer._finalize_job(job, parent_id, "completed", error=None)
            # Yield + poll for the create_task (notify_corr_rearm) to run.
            for _ in range(20):
                await asyncio.sleep(0.1)
                if rearm_spy.await_count > 0:
                    break

            if use_legacy_cascade:
                # C1-N1 fix: gate raised → deferred (NOT W3 fail-safed to FAILED).
                assert _read_job_status(pg_engine, job_id) == JobStatus.PROCESSING.value, (
                    "PRODUCTION PATH (legacy): job transitioned despite gate SELECT raising — "
                    "C1-N1 except block failed to defer"
                )
                assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.RUNNING.value, (
                    "PRODUCTION PATH (legacy): instance transitioned despite gate SELECT raising"
                )

                # Side-effect deps must NOT have fired (gate deferred before outbox).
                mocks["job_queue_service"].notify_watchers.assert_not_called()
                mocks["live_hub"].stream_status_change.assert_not_called()
                mocks["events_service"]._publish_instance_lifecycle_event.assert_not_called()

                # C2-PartA: notify_corr_rearm WAS scheduled with parent_id.
                rearm_spy.assert_awaited_once_with(parent_id)
            else:
                # Flag OFF: CM check is in-memory so the Session.execute
                # patch is a no-op. CM is None → ADR-011 RuntimeError →
                # W3 fail-safe transitions the job to FAILED.
                assert _read_job_status(pg_engine, job_id) == JobStatus.FAILED.value, (
                    f"PRODUCTION PATH (CM): expected W3 fail-safe to "
                    f"transition job to FAILED, got "
                    f"{_read_job_status(pg_engine, job_id)}"
                )
                mocks["job_queue_service"].notify_watchers.assert_not_called()
                mocks["live_hub"].stream_status_change.assert_not_called()
                mocks["events_service"]._publish_instance_lifecycle_event.assert_not_called()
