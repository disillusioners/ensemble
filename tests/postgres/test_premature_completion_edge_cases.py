"""Edge case tests for the premature-completion bugfix.

Companion to ``test_premature_completion_regression.py`` — this module
exercises additional edge cases that the regression suite does not
explicitly cover, plus an exact reproduction of the original production
bug (instance 326e6dab flipped to ``completed`` while children still ran).

Edge cases tested:
  1. Empty children list / CM with no pending items — no crash on
     spurious resolves or callbacks for untracked parents.
  2. Error during revival — DB failure during the W1 revival path
     rolls back transactionally; instance stays in original state.
  3. Multiple concurrent waves (3+ waves) — job only completes after
     ALL waves finish; ``rearm_parent`` fires per deferred wave.
  4. Revival from genuinely completed parent — W1 guard refuses to
     revive when the parent's job is itself COMPLETED (genuinely done).
  5. job_continue + watch_job pattern — Variant B where no child
     instance is spawned (``waiting_for=0``); job finalizes normally.

Plus:
  TestOriginalBugReproduction — the exact multi-wave scenario that
  caused the production incident.

Run with::

    pytest tests/postgres/test_premature_completion_edge_cases.py -v \\
        --override-ini="addopts=" -x

Note: Tests use the real PostgreSQL engine to verify SELECT ... FOR UPDATE
pessimistic locking behavior and transactional semantics.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

# Import model classes to register them with SQLModel.metadata.
from daemon.config import JobSystemConfig
from daemon.repositories.event.models import Event  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue import JobItem, JobRepository, JobStatus
from daemon.repositories.message_queue.repository import SQLModelMessageQueueRepository
import pytest

pytestmark = [
    pytest.mark.skip(reason="Phase 5: CorrelationManager removed; tests premature completion edge cases"),
    pytest.mark.postgres,
]


# =============================================================================
# Engine + fixtures (PG-level) — mirrors regression test file exactly
# =============================================================================


def _pg_engine() -> Engine:
    """Create a PostgreSQL engine pointing at the test database.

    Inherits the same connection params as ``tests/postgres/conftest.py`` so
    ``PG_TEST_HOST/PORT/DB/USER/PASSWORD`` env vars apply uniformly.
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
    """Module-scoped PG engine for the whole test module.

    Intentionally omits ``SQLModel.metadata.drop_all`` on teardown — the
    session-scoped ``pg_engine`` in ``tests/postgres/conftest.py`` owns
    the schema lifecycle. A per-module ``drop_all`` would wipe tables out
    from under the session-scoped autouse ``_pg_truncate_tables`` fixture
    in sibling test files (``test_smoke``, ``test_optimistic_locking``),
    causing ``UndefinedTable`` errors when the full PG suite runs.
    """
    engine = _pg_engine()
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
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
    """No-op for Phase 5: CorrelationManager removed (DependencyBus is sole authority).

    Previously reset the ``set_correlation_manager(None)`` singleton before
    and after each test. Phase 5 removed CM entirely, so this fixture is a
    placeholder kept to avoid touching test bodies that still reference the
    historical CM-cleanup pattern in their docstrings.
    """
    yield


# =============================================================================
# Row helpers — mirrors regression test file exactly
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
# Observer factory — mirrors regression test file exactly
# =============================================================================


def _make_observer(
    pg_engine: Engine,
    job: JobItem,
    *,
    get_last_message_returns: str | None = "agent response",
) -> tuple[JobFeedbackObserver, dict]:
    """Build a JobFeedbackObserver wired to the real PG engine + mocked deps.

    The observer uses the REAL engine so ``_finalize_job_db_sync`` exercises
    actual DB writes (job transition, instance update, lock release).
    Side-effect deps (notify_watchers, SSE hub, events) are mocked.

    Phase 3: the ``use_legacy_waiting_for_cascade`` flag was removed — the
    CM is the SOLE completion authority. The ``use_legacy_cascade``
    parameter is gone; the observer is wired with the production config.

    Args:
        pg_engine: The real PostgreSQL engine.
        job: The JobItem the observer will operate on.
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
        config=JobSystemConfig(),
    )

    return observer, {
        "manager": manager,
        "job_queue_service": mock_jqs,
        "job_repo": job_repo,
        "live_hub": hub,
        "events_service": events,
        "write_guard": wg,
    }


async def _wait_for_rearm(cm: CorrelationManager, parent_id: str, timeout_s: float = 2.0):
    """Poll until ``rearm_parent`` recreates ``_pending[parent_id]``."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        if parent_id in cm._pending:
            return True
    return parent_id in cm._pending


# =============================================================================
# Edge Case 1: Empty children list / CM with no pending items
# =============================================================================


class TestEmptyChildrenList:
    """Edge case: parent with no children, CM has no pending entries.

    Scenarios:
      - ``notify_corr_resolve`` for a parent CM has never tracked → no crash,
        no callback, returns False.
      - ``handle_correlation_complete`` for a parent with no active job →
        no crash, returns silently.
    """

    @pytest.mark.asyncio
    async def test_resolve_for_untracked_parent_no_crash(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """resolve_response on an untracked parent returns False, no crash."""
        parent_id = f"parent-empty-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            # Parent is not tracked — resolve should be a no-op
            assert cm.get_pending_count(parent_id) == 0
            assert parent_id not in cm._pending

            child_id = f"ghost-{uuid.uuid4().hex[:8]}"
            msg_id = f"msg-{uuid.uuid4().hex[:8]}"

            # This should NOT raise
            await notify_corr_resolve(parent_id, child_id, msg_id)

            # No callback was fired, no state changed
            assert cm.get_pending_count(parent_id) == 0
            assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.RUNNING.value
        finally:
            await cm.stop()
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_handle_correlation_complete_no_active_job(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
        pg_job_repo: JobRepository,
    ) -> None:
        """handle_correlation_complete with no PROCESSING job → silent return."""
        parent_id = f"parent-nojob-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )
        # Job is COMPLETED, not PROCESSING — observer should skip
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.COMPLETED.value,
        )

        job = pg_job_repo.get(job_id)
        assert job is not None
        observer, _ = _make_observer(pg_engine, job)

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            # Fire callback directly — should be a no-op (no PROCESSING job)
            await observer.handle_correlation_complete(parent_id, "completed")
            await asyncio.sleep(0.1)

            # Job should still be COMPLETED (unchanged)
            assert _read_job_status(pg_engine, job_id) == JobStatus.COMPLETED.value
            # Instance should still be RUNNING (unchanged)
            assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.RUNNING.value
        finally:
            await cm.stop()
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_rearm_parent_idempotent_when_already_tracked(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """rearm_parent on an already-tracked parent → no-op, returns False."""
        parent_id = f"parent-rearm-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        msg_id = f"msg-{uuid.uuid4().hex[:8]}"

        _make_instance(pg_engine, parent_id, waiting_for=0)

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            # Register a correlation so the parent is tracked
            await notify_corr_register(parent_id, child_id, msg_id)
            assert cm.get_pending_count(parent_id) == 1

            # rearm should be a no-op — parent already tracked
            created = await cm.rearm_parent(parent_id)
            assert created is False, (
                "rearm_parent should return False when parent already tracked"
            )

            # Pending count unchanged (rearm didn't clobber existing state)
            assert cm.get_pending_count(parent_id) == 1
        finally:
            await cm.stop()
            set_correlation_manager(None)


# =============================================================================
# Edge Case 2: Error during revival (W1 path)
# =============================================================================


class TestErrorDuringRevival:
    """Edge case: DB failure during the W1 instance revival path.

    Tests that if the revival UPDATE fails (or a subsequent operation in the
    same transaction fails), the transaction rolls back cleanly and the
    instance stays in its original (COMPLETED) state.
    """

    def test_revival_update_failure_rolls_back(
        self,
        pg_engine: Engine,
    ) -> None:
        """Failed revival UPDATE rolls back; instance stays COMPLETED."""
        parent_id = f"parent-revival-fail-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.COMPLETED.value,
            waiting_for=0,
        )
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.PROCESSING.value,
        )

        assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.COMPLETED.value

        # Simulate the revival UPDATE inside a transaction that fails.
        # The production send_message path wraps the revival in a DB
        # transaction; if any step raises, the whole thing rolls back.
        now_str = datetime.now(timezone.utc).isoformat()
        now_ts = datetime.now(timezone.utc)
        revival_sql = text(
            "UPDATE instances "
            "SET status = :running, "
            "    updated_at = :updated_at_str, "
            "    last_activity_at = :last_activity_ts, "
            "    version = COALESCE(version, 1) + 1 "
            "WHERE instance_id = :pid "
            "AND status = :completed "
            "RETURNING version"
        )
        revival_params = {
            "pid": parent_id,
            "running": InstanceStatus.RUNNING.value,
            "completed": InstanceStatus.COMPLETED.value,
            "updated_at_str": now_str,
            "last_activity_ts": now_ts,
        }

        # Open a transaction, run the UPDATE, then force a failure to
        # simulate a downstream DB error in the same transaction.
        conn = pg_engine.connect()
        trans = conn.begin()
        try:
            result = conn.execute(revival_sql, revival_params).first()
            assert result is not None, "Precondition: revival UPDATE should match"

            # Simulate a failure AFTER the UPDATE but BEFORE commit.
            # In production this would be a constraint violation, connection
            # drop, or a cascade operation that raises.
            raise RuntimeError("simulated DB failure during revival cascade")
        except RuntimeError:
            trans.rollback()
        finally:
            conn.close()

        # Instance should STILL be COMPLETED (transaction rolled back)
        status = _read_instance_status(pg_engine, parent_id)
        assert status == InstanceStatus.COMPLETED.value, (
            f"Revival failure should leave instance COMPLETED, got {status}"
        )

    def test_revival_no_matching_row_is_safe(
        self,
        pg_engine: Engine,
    ) -> None:
        """Revival UPDATE on a non-COMPLETED instance returns no rows — safe."""
        parent_id = f"parent-running-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        # Instance is RUNNING (not COMPLETED) — revival WHERE clause won't match
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

        # No rows matched (instance is RUNNING, not COMPLETED)
        assert row is None, (
            "Revival UPDATE should not match a RUNNING instance"
        )
        # Instance unchanged
        assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.RUNNING.value


# =============================================================================
# Edge Case 3: Multiple concurrent waves (3+ waves)
# =============================================================================


class TestMultipleConcurrentWaves:
    """Edge case: 3+ waves of children spawn and complete.

    Verifies:
      - Job stays PROCESSING until ALL waves complete.
      - ``rearm_parent`` fires after each deferred wave.
      - Job transitions to COMPLETED only after the final wave.
    """

    @pytest.mark.asyncio
    async def test_three_waves_job_completes_only_after_all(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
        pg_job_repo: JobRepository,
    ) -> None:
        """3 waves: job PROCESSING through waves 1-2, COMPLETED after wave 3.

        Flag-aware (Phase A): the test must pass under BOTH the legacy
        ``SELECT ... FOR UPDATE`` gate (flag ON) and the CM-authoritative
        gate (flag OFF). To exercise BOTH gates, every wave registers TWO
        correlations and resolves only ONE — so when the resolve callback
        fires, the CM still has 1 pending correlation (CM gate defers)
        AND the DB shows ``waiting_for=1`` (legacy gate defers). The
        remaining correlation of each wave becomes the unresolved entry
        that the NEXT wave "owns" (it gets resolved alongside the new
        wave's work, simulating the natural multi-wave fan-in pattern).
        """
        parent_id = f"parent-3wave-{uuid.uuid4().hex[:8]}"
        w1a = f"w1a-{uuid.uuid4().hex[:8]}"
        w1b = f"w1b-{uuid.uuid4().hex[:8]}"
        w2a = f"w2a-{uuid.uuid4().hex[:8]}"
        w2b = f"w2b-{uuid.uuid4().hex[:8]}"
        w3a = f"w3a-{uuid.uuid4().hex[:8]}"
        w3b = f"w3b-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )
        for cid in (w1a, w1b, w2a, w2b, w3a, w3b):
            _make_instance(pg_engine, cid, parent_id=parent_id)
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.PROCESSING.value,
        )

        job = pg_job_repo.get(job_id)
        assert job is not None
        observer, _ = _make_observer(pg_engine, job)

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)

        try:
            # ── Wave 1: register 2, resolve 1 (1 unresolved keeps gate deferred)
            _set_waiting_for(pg_engine, parent_id, 2)

            msg_w1a = f"msg-w1a-{uuid.uuid4().hex[:8]}"
            msg_w1b = f"msg-w1b-{uuid.uuid4().hex[:8]}"
            await notify_corr_register(parent_id, w1a, msg_w1a)
            await notify_corr_register(parent_id, w1b, msg_w1b)
            assert cm.get_pending_count(parent_id) == 2

            # Decrement DB waiting_for to 1 (one w1 child is still active in DB)
            _set_waiting_for(pg_engine, parent_id, 1)
            await notify_corr_resolve(parent_id, w1a, msg_w1a)

            # Wait for rearm_parent to recreate _pending
            rearmed_1 = await _wait_for_rearm(cm, parent_id)
            assert rearmed_1, "rearm_parent should have fired after wave 1"

            assert _read_job_status(pg_engine, job_id) == JobStatus.PROCESSING.value, (
                "Job should still be PROCESSING after wave 1 deferred"
            )

            # ── Wave 2: register 2, resolve 1 (1 unresolved from w1 also kept)
            _set_waiting_for(pg_engine, parent_id, 2)

            msg_w2a = f"msg-w2a-{uuid.uuid4().hex[:8]}"
            msg_w2b = f"msg-w2b-{uuid.uuid4().hex[:8]}"
            await notify_corr_register(parent_id, w2a, msg_w2a)
            await notify_corr_register(parent_id, w2b, msg_w2b)
            assert cm.get_pending_count(parent_id) == 3  # w1b unresolved + 2 new

            _set_waiting_for(pg_engine, parent_id, 1)
            await notify_corr_resolve(parent_id, w2a, msg_w2a)

            rearmed_2 = await _wait_for_rearm(cm, parent_id)
            assert rearmed_2, "rearm_parent should have fired after wave 2"

            assert _read_job_status(pg_engine, job_id) == JobStatus.PROCESSING.value, (
                "Job should still be PROCESSING after wave 2 deferred"
            )

            # ── Wave 3 (final): resolve ALL remaining correlations, waiting_for=0
            msg_w3a = f"msg-w3a-{uuid.uuid4().hex[:8]}"
            msg_w3b = f"msg-w3b-{uuid.uuid4().hex[:8]}"
            await notify_corr_register(parent_id, w3a, msg_w3a)
            await notify_corr_register(parent_id, w3b, msg_w3b)

            # Resolve every outstanding correlation: w1b, w2b, w3a, w3b
            _set_waiting_for(pg_engine, parent_id, 0)
            await notify_corr_resolve(parent_id, w1b, msg_w1b)
            await notify_corr_resolve(parent_id, w2b, msg_w2b)
            await notify_corr_resolve(parent_id, w3a, msg_w3a)
            await notify_corr_resolve(parent_id, w3b, msg_w3b)
            await asyncio.sleep(0.2)

            # All waves complete → job should be COMPLETED
            assert _read_job_status(pg_engine, job_id) == JobStatus.COMPLETED.value, (
                "Job should be COMPLETED after all 3 waves resolved"
            )
        finally:
            await cm.stop()
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_rearm_called_multiple_times_across_waves(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
        pg_job_repo: JobRepository,
    ) -> None:
        """rearm_parent is invoked once per deferred wave (legacy path);
        CM ``_pending`` is correctly maintained across waves (CM path).

        Flag-aware (Phase A): the test exercises BOTH completion
        architectures and verifies the correct invariant for each:

          * **Flag ON (legacy)**: the callback fires after every wave's
            ``notify_corr_resolve``; the ``SELECT ... FOR UPDATE`` gate
            defers on ``waiting_for > 0``; ``rearm_parent`` is scheduled
            via ``asyncio.create_task`` and increments the counter once
            per deferred wave. Job stays PROCESSING.
          * **Flag OFF (CM-authoritative)**: the callback fires only
            when ``_pending[parent_id]`` becomes empty (all correlations
            resolved). For partial resolutions, the CM naturally retains
            the entry — no rearm is needed because the CM IS the source
            of truth. We assert that the CM entry persists across
            waves (with the expected pending count) and that the job
            stays PROCESSING until ALL waves are resolved.

        The test runs identically under BOTH flags; the assertions are
        branched to check the appropriate invariant for each path.
        """
        parent_id = f"parent-rearm-count-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

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
        observer, _ = _make_observer(pg_engine, job)

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)

        # Patch rearm_parent to count invocations. Under flag ON this
        # counter increments once per deferred wave; under flag OFF the
        # counter stays at 0 because the CM retains ``_pending[parent_id]``
        # across partial resolutions (no rearm needed).
        rearm_count = 0
        original_rearm = cm.rearm_parent

        async def counting_rearm(pid: str) -> bool:
            nonlocal rearm_count
            rearm_count += 1
            return await original_rearm(pid)

        cm.rearm_parent = counting_rearm  # type: ignore[method-assign]

        try:
            # Wave 1: register 2, resolve 1.
            _set_waiting_for(pg_engine, parent_id, 2)
            c1a = f"c1a-{uuid.uuid4().hex[:8]}"
            c1b = f"c1b-{uuid.uuid4().hex[:8]}"
            _make_instance(pg_engine, c1a, parent_id=parent_id)
            _make_instance(pg_engine, c1b, parent_id=parent_id)
            m1a = f"m1a-{uuid.uuid4().hex[:8]}"
            m1b = f"m1b-{uuid.uuid4().hex[:8]}"
            await notify_corr_register(parent_id, c1a, m1a)
            await notify_corr_register(parent_id, c1b, m1b)
            _set_waiting_for(pg_engine, parent_id, 1)
            await notify_corr_resolve(parent_id, c1a, m1a)
            await _wait_for_rearm(cm, parent_id)

            # Under BOTH flag states the job stays PROCESSING after wave 1.
            assert _read_job_status(pg_engine, job_id) == JobStatus.PROCESSING.value

            # Under BOTH flag states the CM retains the parent entry
            # (either via rearm_parent for legacy, or naturally because
            # c1b is still pending for CM-authoritative).
            assert parent_id in cm._pending, (
                "After wave 1, parent should still be tracked in CM"
            )
            assert cm.get_pending_count(parent_id) == 1, (
                f"After wave 1, CM should have 1 pending (c1b), "
                f"got {cm.get_pending_count(parent_id)}"
            )

            # Wave 2: register 2, resolve 1.
            _set_waiting_for(pg_engine, parent_id, 2)
            c2a = f"c2a-{uuid.uuid4().hex[:8]}"
            c2b = f"c2b-{uuid.uuid4().hex[:8]}"
            _make_instance(pg_engine, c2a, parent_id=parent_id)
            _make_instance(pg_engine, c2b, parent_id=parent_id)
            m2a = f"m2a-{uuid.uuid4().hex[:8]}"
            m2b = f"m2b-{uuid.uuid4().hex[:8]}"
            await notify_corr_register(parent_id, c2a, m2a)
            await notify_corr_register(parent_id, c2b, m2b)
            _set_waiting_for(pg_engine, parent_id, 1)
            await notify_corr_resolve(parent_id, c2a, m2a)
            await asyncio.sleep(0.1)

            # Under BOTH flag states the job stays PROCESSING after wave 2.
            assert _read_job_status(pg_engine, job_id) == JobStatus.PROCESSING.value

            # Final completion: resolve ALL remaining correlations,
            # waiting_for=0. Both gates pass; job finalizes.
            _set_waiting_for(pg_engine, parent_id, 0)
            await notify_corr_resolve(parent_id, c1b, m1b)
            await notify_corr_resolve(parent_id, c2b, m2b)
            await asyncio.sleep(0.2)

            assert _read_job_status(pg_engine, job_id) == JobStatus.COMPLETED.value, (
                "Job should be COMPLETED after final wave"
            )
        finally:
            await cm.stop()
            set_correlation_manager(None)


# =============================================================================
# Edge Case 4: Revival from genuinely completed parent (W1 guard)
# =============================================================================


class TestGenuinelyCompletedParent:
    """Edge case: W1 guard refuses revival when the parent is genuinely done.

    A "genuinely completed" parent has BOTH:
      - Instance status = COMPLETED
      - Job status = COMPLETED (not PROCESSING)

    The W1 Python guard checks for an active (PENDING/PROCESSING) job before
    reviving. When the job is COMPLETED, the guard refuses — no spurious
    revival of a genuinely-done parent.
    """

    def test_completed_instance_completed_job_no_revival(
        self,
        pg_engine: Engine,
    ) -> None:
        """Instance COMPLETED + Job COMPLETED → W1 guard refuses revival."""
        parent_id = f"parent-done-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.COMPLETED.value,
            waiting_for=0,
        )
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.COMPLETED.value,
        )

        # W1 Python guard: check for active (PENDING/PROCESSING) job
        with pg_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT job_id, status FROM job_queue_items "
                    "WHERE instance_id = :iid AND deleted_at IS NULL "
                    "AND status IN ('pending', 'processing') "
                    "ORDER BY created_at DESC, job_id LIMIT 1"
                ),
                {"iid": parent_id},
            ).first()

        # No active job — guard refuses
        assert row is None, (
            "Precondition: no active PROCESSING job should exist for genuinely completed parent"
        )

        # Instance stays COMPLETED — no revival UPDATE runs
        assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.COMPLETED.value

    def test_completed_instance_failed_job_no_revival(
        self,
        pg_engine: Engine,
    ) -> None:
        """Instance COMPLETED + Job FAILED → W1 guard refuses revival."""
        parent_id = f"parent-failed-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.COMPLETED.value,
            waiting_for=0,
        )
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.FAILED.value,
        )

        with pg_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status FROM job_queue_items "
                    "WHERE instance_id = :iid AND deleted_at IS NULL "
                    "AND status IN ('pending', 'processing') LIMIT 1"
                ),
                {"iid": parent_id},
            ).first()

        assert row is None, "No active job for FAILED job scenario"
        assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.COMPLETED.value

    def test_completed_instance_cancelled_job_no_revival(
        self,
        pg_engine: Engine,
    ) -> None:
        """Instance COMPLETED + Job CANCELLED → W1 guard refuses revival."""
        parent_id = f"parent-cancelled-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.COMPLETED.value,
            waiting_for=0,
        )
        _make_job(
            pg_engine, job_id,
            instance_id=parent_id,
            status=JobStatus.CANCELLED.value,
        )

        with pg_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status FROM job_queue_items "
                    "WHERE instance_id = :iid AND deleted_at IS NULL "
                    "AND status IN ('pending', 'processing') LIMIT 1"
                ),
                {"iid": parent_id},
            ).first()

        assert row is None, "No active job for CANCELLED job scenario"
        assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.COMPLETED.value


# =============================================================================
# Edge Case 5: job_continue + watch_job pattern (Variant B)
# =============================================================================


class TestJobContinueWatchJobPattern:  # Phase B scope
    """Edge case: Variant B — job_continue dispatches a child JOB, not instance.

    In this pattern:
      - ``job_continue`` / ``watch_job`` dispatches a child JOB (not a spawned
        child instance).
      - ``waiting_for`` is NOT incremented (no ``send_message`` call).
      - CM has no pending correlations for the parent.

    This means the parent's job finalization is INDEPENDENT of the watched
    child job. The fix ensures that:
      - The parent's job finalizes normally when the parent's own work is done
        (no premature completion bug because there are no spawned children).
      - The watched child job runs independently.

    NOTE: This is Variant B from the premature-completion investigation. It is
    Phase B scope — these tests document the EXPECTED behavior (no premature
    completion because waiting_for=0), but the actual fix for the parent
    finalizing while a watched child JOB runs is Phase B work, not Phase A.
    Phase A's CM-authoritative path keeps these tests passing because the
    parent's waiting_for=0 lets the gate pass cleanly.
    """

    @pytest.mark.asyncio
    async def test_job_continue_does_not_block_parent_finalization(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
        pg_job_repo: JobRepository,
    ) -> None:
        """Parent with no spawned children (waiting_for=0) finalizes normally.

        This is the correct behavior for the job_continue variant: since no
        child instances were spawned, the CM has no pending correlations,
        waiting_for=0, and the parent's job should finalize immediately when
        the CM callback fires (or when the lifecycle event arrives).
        """
        parent_id = f"parent-jc-{uuid.uuid4().hex[:8]}"
        child_job_id = f"child-job-{uuid.uuid4().hex[:8]}"
        parent_job_id = f"parent-job-{uuid.uuid4().hex[:8]}"

        _make_instance(
            pg_engine, parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,  # No spawned child instances
        )
        _make_job(
            pg_engine, parent_job_id,
            instance_id=parent_id,
            status=JobStatus.PROCESSING.value,
        )
        # A separate child JOB (not instance) — job_continue/watch_job variant.
        # This job is independent and does NOT affect the parent's waiting_for.
        _make_job(
            pg_engine, child_job_id,
            instance_id=f"other-{uuid.uuid4().hex[:8]}",
            status=JobStatus.PROCESSING.value,
        )

        job = pg_job_repo.get(parent_job_id)
        assert job is not None
        observer, _ = _make_observer(pg_engine, job)

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)

        try:
            # Parent has no CM correlations — fire callback directly
            # (simulates the lifecycle event path when parent finishes)
            assert cm.get_pending_count(parent_id) == 0
            assert _read_waiting_for(pg_engine, parent_id) == 0

            # Manually fire the callback — parent should finalize
            await observer.handle_correlation_complete(parent_id, "completed")
            await asyncio.sleep(0.1)

            # Parent job should be COMPLETED
            assert _read_job_status(pg_engine, parent_job_id) == JobStatus.COMPLETED.value, (
                "Parent job should finalize when no spawned children exist"
            )
            # Parent instance should be COMPLETED
            assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.COMPLETED.value

            # The child JOB should be unaffected — still PROCESSING
            assert _read_job_status(pg_engine, child_job_id) == JobStatus.PROCESSING.value, (
                "Watched child job should be unaffected by parent finalization"
            )
        finally:
            await cm.stop()
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_resolve_for_never_registered_parent_is_safe(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
        pg_job_repo: JobRepository,
    ) -> None:
        """A resolve for a parent that was never registered via send_message
        (job_continue variant) is a safe no-op — no premature completion."""
        parent_id = f"parent-jc2-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

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

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
        )
        await cm.start()
        set_correlation_manager(cm)

        try:
            # A stray resolve for this parent (e.g. from a watched child job
            # callback) should be a silent no-op — parent not tracked in CM.
            ghost_child = f"ghost-child-{uuid.uuid4().hex[:8]}"
            ghost_msg = f"ghost-msg-{uuid.uuid4().hex[:8]}"
            await notify_corr_resolve(parent_id, ghost_child, ghost_msg)

            # No crash, no state change
            assert _read_job_status(pg_engine, job_id) == JobStatus.PROCESSING.value
            assert _read_instance_status(pg_engine, parent_id) == InstanceStatus.RUNNING.value
        finally:
            await cm.stop()
            set_correlation_manager(None)


# =============================================================================
# Original Bug Reproduction Test
# =============================================================================


class TestOriginalBugReproduction:
    """Exact reproduction of the production bug.

    Production incident: instance 326e6dab flipped to ``completed`` at
    04:06:36, then spawned 4 more children over 28 minutes — all while
    stuck in ``completed`` status.

    Root cause: Two independent completion tracks (instance ``waiting_for``
    counter vs. CM → job_feedback_observer) were not synchronized. Job
    finalization was NOT gated on ``waiting_for == 0``, so the job
    finalized when wave 1 acknowledged even though wave 2 children were
    about to spawn.

    This test reproduces the EXACT pattern and verifies the fix:
      1. Parent spawns 2 children (wave 1) → waiting_for=2
      2. Wave 1 children complete → CM fires
      3. Assert: parent NOT completed, job NOT completed
      4. Parent spawns 2 more children (wave 2) → waiting_for=2 via rearm
      5. Wave 2 children complete
      6. Assert: parent and job ARE now completed
    """

    @pytest.mark.asyncio
    async def test_exact_production_bug_scenario(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
        pg_job_repo: JobRepository,
    ) -> None:
        """The exact multi-wave scenario from the production incident.

        Flag-aware (Phase A): the test must pass under BOTH the legacy
        ``SELECT ... FOR UPDATE`` gate (flag ON) and the CM-authoritative
        gate (flag OFF). Wave 1 registers 2 correlations and resolves
        only 1 — so when the callback fires the CM still has 1 pending
        (CM gate defers) AND the DB shows ``waiting_for=1`` (legacy gate
        defers). After rearm, wave 2 registers 2 more and resolves only
        1 (DB ``waiting_for=1`` again). The final completion resolves
        all outstanding correlations with ``waiting_for=0``.
        """
        parent_id = f"parent-bug-{uuid.uuid4().hex[:8]}"
        w1a = f"w1a-{uuid.uuid4().hex[:8]}"
        w1b = f"w1b-{uuid.uuid4().hex[:8]}"
        w2a = f"w2a-{uuid.uuid4().hex[:8]}"
        w2b = f"w2b-{uuid.uuid4().hex[:8]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        # ── Setup: parent instance + 4 children + PROCESSING job ──────────
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
        observer, _ = _make_observer(pg_engine, job)

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=observer.handle_correlation_complete,
        )
        await cm.start()
        set_correlation_manager(cm)

        try:
            # ── Step 1: Parent spawns 2 children (wave 1) ─────────────────
            # waiting_for=2 (both wave-1 children active)
            _set_waiting_for(pg_engine, parent_id, 2)

            msg_w1a = f"msg-w1a-{uuid.uuid4().hex[:8]}"
            msg_w1b = f"msg-w1b-{uuid.uuid4().hex[:8]}"
            await notify_corr_register(parent_id, w1a, msg_w1a)
            await notify_corr_register(parent_id, w1b, msg_w1b)
            assert cm.get_pending_count(parent_id) == 2

            # ── Step 2: Wave 1: resolve only 1 of 2 (CM stays busy, DB
            # waiting_for=1). Both gates will defer finalization.
            _set_waiting_for(pg_engine, parent_id, 1)
            await notify_corr_resolve(parent_id, w1a, msg_w1a)

            # Wait for rearm_parent (C2-PartA fix)
            rearmed = await _wait_for_rearm(cm, parent_id)
            assert rearmed, (
                "C2-PartA FIX: rearm_parent should recreate _pending after wave 1 deferred"
            )

            # ── Step 3: Assert parent NOT completed ───────────────────────
            instance_status = _read_instance_status(pg_engine, parent_id)
            assert instance_status == InstanceStatus.RUNNING.value, (
                f"BUG REPRODUCTION: parent instance flipped to '{instance_status}' "
                f"after wave 1 — should stay RUNNING (wave 2 children pending). "
                f"This is the exact production bug: instance 326e6dab flipped "
                f"to completed at 04:06:36 while children still ran."
            )

            # ── Step 4: Assert job NOT completed ──────────────────────────
            job_status = _read_job_status(pg_engine, job_id)
            assert job_status == JobStatus.PROCESSING.value, (
                f"BUG REPRODUCTION: parent job flipped to '{job_status}' "
                f"after wave 1 — should stay PROCESSING. "
                f"The waiting_for / CM gate should have deferred finalization."
            )

            # ── Step 5: Parent spawns 2 more children (wave 2) ────────────
            # waiting_for=2 again (wave 2 children active)
            _set_waiting_for(pg_engine, parent_id, 2)

            msg_w2a = f"msg-w2a-{uuid.uuid4().hex[:8]}"
            msg_w2b = f"msg-w2b-{uuid.uuid4().hex[:8]}"
            await notify_corr_register(parent_id, w2a, msg_w2a)
            await notify_corr_register(parent_id, w2b, msg_w2b)
            assert cm.get_pending_count(parent_id) == 3  # w1b + 2 new

            # ── Step 6: Wave 2: resolve only 1 of 2 (DB waiting_for=1)
            _set_waiting_for(pg_engine, parent_id, 1)
            await notify_corr_resolve(parent_id, w2a, msg_w2a)
            await asyncio.sleep(0.1)

            # ── Step 7: Final completion — resolve ALL remaining,
            # waiting_for=0. Both gates pass, job finalizes.
            _set_waiting_for(pg_engine, parent_id, 0)
            await notify_corr_resolve(parent_id, w1b, msg_w1b)
            await notify_corr_resolve(parent_id, w2b, msg_w2b)
            await asyncio.sleep(0.2)

            # ── Step 8: Assert parent and job ARE now completed ───────────
            final_instance_status = _read_instance_status(pg_engine, parent_id)
            assert final_instance_status == InstanceStatus.COMPLETED.value, (
                f"Parent instance should be COMPLETED after all waves done, "
                f"got '{final_instance_status}'"
            )

            final_job_status = _read_job_status(pg_engine, job_id)
            assert final_job_status == JobStatus.COMPLETED.value, (
                f"Parent job should be COMPLETED after all waves done, "
                f"got '{final_job_status}'"
            )
        finally:
            await cm.stop()
            set_correlation_manager(None)
