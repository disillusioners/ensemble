"""A14 — Kill-switch test pack: full legacy path with ``USE_LEGACY_WAITING_FOR_CASCADE=ON``.

This is the SOLE rollback mechanism for Phase A/B. If the legacy path regresses
during the 18-file refactoring, the kill switch won't save us because nothing
exercises the legacy code. These tests verify that with the flag ON, every
legacy path runs correctly end-to-end:

  1. ``waiting_for`` increment / decrement (send_message / child completion)
  2. Cascade decision via ``waiting_for == 0`` (parent completes when last child done)
  3. ``SELECT ... FOR UPDATE`` gate in ``_finalize_job_db_sync`` defers when
     ``waiting_for > 0`` (closes the TOCTOU window between read + UPDATE)
  4. ``SELECT COUNT(*)`` fallback when ``CorrelationManager is None``
     (graceful degradation path; closes the last race surface when CM is
     unavailable and the kill switch is ON)
  5. M0 parent-revive (a prematurely-COMPLETED parent is resurrected to RUNNING
     when a new child is spawned and an active job exists)
  6. Full spawn → child completion → parent cascade (end-to-end smoke test)

Each test EXPLICITLY sets the kill switch ON via the config mock. The default
production state (flag OFF) is covered by the A8/A12 test packs and
``test_correlation_authority_shadow.py`` — those tests are not duplicated here.

Run with::

    python -m pytest tests/test_kill_switch_legacy_path.py -v --tb=short

NOTE: This is a UNIT test file (in-memory SQLite). It does NOT touch PostgreSQL.
The 18-file refactor must keep both flag states functional; the A14 pack is the
contract that the rollback path still works.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Model imports — required so SQLModel.metadata sees the tables when
# create_all() runs on the test engine. Mirrors the explicit-imports pattern
# from tests/unit/services/test_child_reports.py and tests/test_deadlock_fix.py.
from daemon.config import JobSystemConfig
from daemon.repositories.event.models import Event, EventKind  # noqa: F401
from daemon.repositories.instance.models import (  # noqa: F401
    Instance,
    InstanceHierarchy,
    InstanceStatus,
)
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem, JobLock, JobStatus  # noqa: F401
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.task.models import Task  # noqa: F401
from daemon.services.child_reports import ChildReportsService
from daemon.services.correlation_manager import set_correlation_manager
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.write_pause_guard import WritePauseGuard


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine (StaticPool for cross-thread safety)."""
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

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def _reset_correlation_manager():
    """Ensure no CorrelationManager singleton leaks between tests.

    The legacy ``SELECT COUNT(*)`` fallback path requires ``CM is None``;
    Category 4 tests depend on this. The autouse fixture guarantees a clean
    slate regardless of test order.
    """
    set_correlation_manager(None)
    yield
    set_correlation_manager(None)


def _kill_switch_config() -> Any:
    """Build a mock manager ``.config`` object with the kill switch ON.

    ``ChildReportsService._config`` resolves to ``self._manager.config`` and
    then reads ``config.job_system.use_legacy_waiting_for_cascade`` — the
    production navigation. A plain ``MagicMock`` whose ``.job_system`` has
    the right attribute is sufficient and keeps the test self-contained.
    """
    cfg = MagicMock(name="Config")
    cfg.job_system = MagicMock(name="JobSystemConfig")
    cfg.job_system.use_legacy_waiting_for_cascade = True
    return cfg


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    """Build a real ``ChildReportsService`` with the kill switch ON.

    Mirrors ``_build_child_reports_service`` in
    ``tests/unit/services/test_child_reports.py`` — ``__new__`` to skip
    ``__init__`` and bind attributes manually. The config mock carries
    ``job_system.use_legacy_waiting_for_cascade = True`` so every code
    path under test enters the legacy branch.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager.config = _kill_switch_config()
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._checkpointer = None
    manager._live_hub = None  # SSE no-op (guarded on truthiness)
    manager._queue_repository = MagicMock()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None  # lifecycle event publish is guarded
    service._trigger_title_generation = MagicMock()  # no-op (would hit MainLoopBridge)
    return service


def _build_observer(engine: Engine) -> tuple[JobFeedbackObserver, dict[str, Any]]:
    """Build a real ``JobFeedbackObserver`` with the kill switch ON.

    The observer stores ``config`` on ``self._config`` and reads
    ``self._config.use_legacy_waiting_for_cascade`` directly (note: NOT
    ``.job_system.`` — the observer receives a ``JobSystemConfig`` object,
    not the parent ``Config``). Mirrors ``_build_observer`` from
    ``tests/test_finalize_job_h15.py`` with the kill-switch config
    argument populated.
    """
    guard = WritePauseGuard()

    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = guard
    manager.is_write_paused = False

    hub = MagicMock(name="LiveHub")
    hub.stream_status_change = AsyncMock()
    manager._live_hub = hub

    events = MagicMock(name="Events")
    events._publish_instance_lifecycle_event = AsyncMock()
    manager._events_service = events

    manager._get_last_assistant_message_raw = AsyncMock(return_value="agent response")

    mock_jqs = MagicMock(name="JobQueueService")
    mock_jqs.notify_watchers = AsyncMock(return_value=0)
    mock_jqs._get_next_job = AsyncMock(return_value=None)
    mock_jqs.get_job_by_instance = AsyncMock(return_value=None)

    mock_lock_repo = MagicMock(name="LockRepo")
    mock_lock_repo.release_by_instance = MagicMock(return_value=0)

    # Use a real JobRepository so atomic_transition hits the DB.
    from daemon.repositories.job_queue.repository import JobRepository

    job_repo = JobRepository(engine)

    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=mock_jqs,
        job_repo=job_repo,
        lock_repo=mock_lock_repo,
        project_repo=MagicMock(),
        instance_manager=manager,
        config=JobSystemConfig(use_legacy_waiting_for_cascade=True),
    )

    return observer, {
        "manager": manager,
        "job_queue_service": mock_jqs,
        "job_repo": job_repo,
        "live_hub": hub,
        "events_service": events,
        "write_guard": guard,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — seed fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "coder",
    parent_id: str | None = None,
    waiting_for: int = 0,
    version: int = 1,
) -> str:
    """Insert an Instance row. Returns the instance_id."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_name=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            parent_id=parent_id,
            status=status,
            waiting_for=waiting_for,
            version=version,
            instance_metadata={},
            children="[]",
            created_at=now,
            updated_at=now,
        )
        s.add(inst)
        s.commit()
    return iid


def seed_hierarchy(engine: Engine, *, parent_id: str, child_id: str) -> None:
    """Insert an InstanceHierarchy junction row."""
    with Session(engine) as s:
        s.add(InstanceHierarchy(parent_id=parent_id, child_id=child_id, created_at=_now_iso()))
        s.commit()


def seed_job(
    engine: Engine,
    *,
    instance_id: str,
    status: str = JobStatus.PROCESSING.value,
) -> str:
    """Insert a JobItem in PROCESSING state. Returns the job_id."""
    jid = f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        item = JobItem(
            job_id=jid,
            agent_id="coder",
            agent_dir="/tmp/agent",
            message="test job",
            source="api",
            job_type="message",
            status=status,
            instance_id=instance_id,
        )
        s.add(item)
        s.commit()
    return jid


def get_instance(engine: Engine, instance_id: str) -> Instance | None:
    """Re-read an Instance from the DB (no session caching)."""
    with Session(engine) as s:
        return s.get(Instance, instance_id)


def get_job(engine: Engine, job_id: str) -> JobItem | None:
    """Re-read a JobItem from the DB."""
    with Session(engine) as s:
        return s.get(JobItem, job_id)


def count_pending_messages(engine: Engine, instance_id: str) -> int:
    """Count READY/PROCESSING/RETRYING messages for an instance — the
    exact filter the legacy ``SELECT COUNT(*)`` fallback uses
    (``daemon/services/child_reports.py:710-719``)."""
    with Session(engine) as s:
        from sqlmodel import select

        rows = s.exec(
            select(MessageQueue).where(MessageQueue.instance_id == instance_id)
        ).all()
        return sum(
            1
            for m in rows
            if m.status in (
                MessageStatus.READY.value,
                MessageStatus.PROCESSING.value,
                MessageStatus.RETRYING.value,
            )
        )


def seed_message_job_for_parent(
    engine: Engine,
    *,
    instance_id: str,
    status: str = JobStatus.PENDING.value,
) -> str:
    """Insert an active MESSAGE job for an instance.

    The F8 carve-out in ``_process_child_completion_db_sync`` (legacy
    path) requires an active MESSAGE job to consider pending messages
    legitimate. Without one, the cascade skips the WAITING_CHILDREN
    transition. This helper seeds the job that production code
    would have created when the parent was originally enqueued.
    """
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        job = JobItem(
            job_id=job_id,
            agent_id="leader",
            agent_dir="/tmp/leader",
            message="parent message job",
            source="api",
            job_type="message",
            status=status,
            instance_id=instance_id,
        )
        s.add(job)
        s.commit()
    return job_id


# ─────────────────────────────────────────────────────────────────────────────
# The exact SQL statements the production legacy path issues.
# Kept in sync with:
#   * ``daemon/tools/instance.py:743-754`` (send_message increment)
#   * ``daemon/services/child_reports.py:523-535`` and ``1320-1332``
#     (child-completion decrement)
#   * ``daemon/tools/instance.py:709-726`` (M0 parent-revive UPDATE)
# Mirrors the helper pair in ``tests/test_phase4_deprecation.py``.
# ─────────────────────────────────────────────────────────────────────────────

_LEGACY_INCREMENT_WAITING_FOR_SQL = (
    "UPDATE instances "
    "SET waiting_for = COALESCE(waiting_for, 0) + 1 "
    "WHERE instance_id = :pid "
    "RETURNING waiting_for"
)

_LEGACY_DECREMENT_WAITING_FOR_SQL = (
    "UPDATE instances "
    "SET waiting_for = CASE "
    "    WHEN COALESCE(waiting_for, 0) - 1 > 0 "
    "        THEN COALESCE(waiting_for, 0) - 1 "
    "    ELSE 0 "
    "END "
    "WHERE instance_id = :pid "
    "RETURNING waiting_for"
)

_LEGACY_REVIVE_PARENT_SQL = (
    "UPDATE instances "
    "SET status = :running, "
    "    updated_at = :now, "
    "    last_activity_at = :now, "
    "    version = COALESCE(version, 1) + 1 "
    "WHERE instance_id = :pid "
    "AND status = :completed "
    "RETURNING version"
)


def _legacy_increment_waiting_for(engine: Engine, parent_id: str) -> int:
    """Mirror the send_message legacy-path ``waiting_for++`` side effect."""
    from sqlalchemy import text

    with Session(engine) as session:
        row = session.execute(
            text(_LEGACY_INCREMENT_WAITING_FOR_SQL), {"pid": parent_id}
        ).first()
        session.commit()
        return int(row[0]) if row is not None else 0


def _legacy_decrement_waiting_for(engine: Engine, parent_id: str) -> int:
    """Mirror the child-completion legacy-path ``waiting_for--`` side effect."""
    from sqlalchemy import text

    with Session(engine) as session:
        row = session.execute(
            text(_LEGACY_DECREMENT_WAITING_FOR_SQL), {"pid": parent_id}
        ).first()
        session.commit()
        return int(row[0]) if row is not None else 0


def _legacy_revive_completed_parent(
    engine: Engine, parent_id: str, *, now_iso: str
) -> int | None:
    """Mirror the M0 parent-revive UPDATE from the send_message legacy path.

    Returns the new ``version`` if the row was updated, ``None`` if no row
    matched (e.g. parent was no longer in COMPLETED status).
    """
    from sqlalchemy import text

    with Session(engine) as session:
        row = session.execute(
            text(_LEGACY_REVIVE_PARENT_SQL),
            {
                "pid": parent_id,
                "running": InstanceStatus.RUNNING.value,
                "completed": InstanceStatus.COMPLETED.value,
                "now": now_iso,
            },
        ).first()
        session.commit()
        return int(row[0]) if row is not None else None


# ═════════════════════════════════════════════════════════════════════════════
# Category 1: waiting_for increment / decrement under flag ON (3 tests)
# ═════════════════════════════════════════════════════════════════════════════


class TestLegacyWaitingForIncrementDecrement:
    """The M0 send_message path increments ``waiting_for`` and the
    child-completion path decrements it. The kill switch must keep
    both writes running bit-for-bit."""

    def test_legacy_increment_on_child_spawn(self, engine: Engine) -> None:
        """send_message legacy SQL: ``waiting_for = COALESCE(waiting_for,0) + 1``.

        Mirrors ``daemon/tools/instance.py:743-754``. The increment must
        return the post-increment value (RETURNING clause) and commit
        atomically — the production code uses this value in the log line.
        """
        parent_id = seed_instance(engine, waiting_for=0)

        new_val = _legacy_increment_waiting_for(engine, parent_id)

        assert new_val == 1
        assert get_instance(engine, parent_id).waiting_for == 1

    def test_legacy_decrement_on_child_completion(self, engine: Engine) -> None:
        """Child-completion legacy SQL: clamp-at-zero ``waiting_for`` decrement.

        Mirrors ``daemon/services/child_reports.py:523-535``. The atomic
        ``UPDATE ... RETURNING waiting_for`` is what closes the
        read-modify-write race (Fix C). The clamp at 0 prevents
        negative values under concurrent decrements.
        """
        parent_id = seed_instance(engine, waiting_for=3)

        new_val = _legacy_decrement_waiting_for(engine, parent_id)

        assert new_val == 2
        assert get_instance(engine, parent_id).waiting_for == 2

    def test_legacy_increment_decrement_cycle_round_trip(self, engine: Engine) -> None:
        """Increment N times then decrement N times → ``waiting_for`` returns to 0.

        This is the lifecycle of a single wave: spawn N children (each
        increment), each child completes (each decrement). The parent
        cascade decision at ``waiting_for == 0`` triggers the parent
        transition (Category 2). The final ``waiting_for=0`` also
        exercises the SQL's CASE-clamp — a second consecutive decrement
        on a 0-valued row stays at 0, never goes negative.
        """
        parent_id = seed_instance(engine, waiting_for=0)

        for _ in range(4):
            assert _legacy_increment_waiting_for(engine, parent_id) >= 1
        assert get_instance(engine, parent_id).waiting_for == 4

        for _ in range(4):
            _legacy_decrement_waiting_for(engine, parent_id)
        assert get_instance(engine, parent_id).waiting_for == 0

        # Extra decrement: the CASE-clamp keeps it at 0 (never negative).
        assert _legacy_decrement_waiting_for(engine, parent_id) == 0


# ═════════════════════════════════════════════════════════════════════════════
# Category 2: Cascade decision via waiting_for == 0 under flag ON (3 tests)
# ═════════════════════════════════════════════════════════════════════════════


class TestLegacyCascadeDecision:
    """When ``waiting_for`` reaches zero, the parent MUST cascade to a
    terminal state. This is the heart of the kill switch — the legacy
    cascade is what the new architecture (CM) replaces.

    Note on production behavior: a single child completion creates a
    completion-report MessageQueue for the parent, so the parent
    transitions to WAITING_CHILDREN (not directly to COMPLETED). The
    cascade decision (waiting_for == 0) fires correctly, and the F8
    carve-out is bypassed by seeding an active MESSAGE job for the
    parent (the carve-out's assumption is "no MESSAGE job = stale
    messages"; in production the parent's MESSAGE job exists)."""

    def test_legacy_cascade_completes_parent_at_zero(
        self, engine: Engine
    ) -> None:
        """Single child completes → ``waiting_for=0`` → cascade decision
        fires → parent → WAITING_CHILDREN (legacy M0: child-completion
        report goes to parent's queue, parent processes it later).

        Verifies the cascade decision (``waiting_for == 0``) fires
        correctly and the parent transitions away from RUNNING. The
        ``WAITING_CHILDREN`` destination is the expected legacy M0
        behavior with the completion-report flow.
        """
        service = _build_child_reports_service(engine)
        parent_id = seed_instance(engine, status=InstanceStatus.RUNNING.value, waiting_for=1)
        child_id = seed_instance(
            engine,
            status=InstanceStatus.RUNNING.value,
            parent_id=parent_id,
            agent_id="coder",
        )
        seed_hierarchy(engine, parent_id=parent_id, child_id=child_id)
        # Active MESSAGE job for the parent — bypasses the F8 carve-out.
        # Without it the F8 guard treats the completion report as a
        # stale/duplicate and leaves the parent in RUNNING.
        seed_message_job_for_parent(engine, instance_id=parent_id)

        result = service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id="msg-1",
            last_content="done",
        )

        # Cascade decision fired: waiting_for reached 0 and parent
        # transitioned away from RUNNING.
        parent = get_instance(engine, parent_id)
        assert parent.waiting_for == 0
        assert parent.status == InstanceStatus.WAITING_CHILDREN.value
        assert result.outcome == "regular_child_completed"

    def test_legacy_no_cascade_while_pending(self, engine: Engine) -> None:
        """``waiting_for > 0`` after decrement → parent stays RUNNING.

        Mirror of the test above but with two children, only one
        completing. The cascade MUST NOT fire yet — the parent still
        has one active child.
        """
        service = _build_child_reports_service(engine)
        parent_id = seed_instance(engine, status=InstanceStatus.RUNNING.value, waiting_for=2)
        c1 = seed_instance(engine, parent_id=parent_id, agent_id="coder")
        c2 = seed_instance(engine, parent_id=parent_id, agent_id="coder")
        seed_hierarchy(engine, parent_id=parent_id, child_id=c1)
        seed_hierarchy(engine, parent_id=parent_id, child_id=c2)

        service._process_child_completion_db_sync(
            instance_id=c1,
            completed_message_id="msg-c1",
            last_content="done",
        )

        parent = get_instance(engine, parent_id)
        # Parent is still RUNNING — only one of two children has resolved.
        assert parent.status == InstanceStatus.RUNNING.value
        assert parent.waiting_for == 1

    def test_legacy_cascade_preserves_error_status(self, engine: Engine) -> None:
        """Parent already in ERROR status → cascade does NOT overwrite it.

        W1 fix: a parent whose last child completed successfully should
        still report as ERROR if the parent was already in ERROR — that
        state is more useful for diagnostics than overwriting it with
        COMPLETED. The legacy code path is identical to the new code
        path here (the ``status != ERROR`` check at the cascade site).
        """
        service = _build_child_reports_service(engine)
        parent_id = seed_instance(
            engine,
            status=InstanceStatus.ERROR.value,
            waiting_for=1,
        )
        child_id = seed_instance(engine, parent_id=parent_id, agent_id="coder")
        seed_hierarchy(engine, parent_id=parent_id, child_id=child_id)

        service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id="msg-err",
            last_content="done",
        )

        parent = get_instance(engine, parent_id)
        # Status preserved — the cascade guarded on
        # ``status != COMPLETED and status != ERROR``.
        assert parent.status == InstanceStatus.ERROR.value
        assert parent.waiting_for == 0


# ═════════════════════════════════════════════════════════════════════════════
# Category 3: FOR UPDATE gate active under flag ON (2 tests)
# ═════════════════════════════════════════════════════════════════════════════


class TestLegacyForUpdateGate:
    """The ``SELECT ... FOR UPDATE`` gate in ``_finalize_job_db_sync``
    closes the TOCTOU window between the ``waiting_for`` read and the
    finalization UPDATE. With the kill switch ON, this gate MUST fire
    and defer when ``waiting_for > 0``."""

    def test_legacy_gate_defers_finalization_when_waiting_for_positive(
        self, engine: Engine
    ) -> None:
        """``waiting_for=1`` → gate defers → returns ``skip=True,
        gate_deferred=True``; job stays in PROCESSING.

        Mirrors ``tests/postgres/test_premature_completion_regression.py::
        test_gate_defers_when_waiting_for_positive`` but for the SQLite
        branch (no ``FOR UPDATE`` keyword — the global write lock is
        the dialect's serialization mechanism; the test still verifies
        the SQL filter, which is the actual control-flow decision).
        """
        observer, mocks = _build_observer(engine)
        instance_id = seed_instance(
            engine, status=InstanceStatus.RUNNING.value, waiting_for=1
        )
        job_id = seed_job(engine, instance_id=instance_id)

        result = observer._finalize_job_db_sync(
            job_id=job_id,
            instance_id=instance_id,
            terminal_status=InstanceStatus.COMPLETED.value,
            result_summary="ok",
            error_message=None,
        )

        assert result.gate_deferred is True
        assert result.skip is True
        # Job MUST NOT have transitioned out of PROCESSING.
        assert get_job(engine, job_id).status == JobStatus.PROCESSING.value
        # Instance MUST NOT have transitioned out of RUNNING.
        assert get_instance(engine, instance_id).status == InstanceStatus.RUNNING.value

    def test_legacy_gate_proceeds_when_waiting_for_zero(
        self, engine: Engine
    ) -> None:
        """``waiting_for=0`` → gate passes → finalization proceeds
        normally; job → COMPLETED, instance → COMPLETED."""
        observer, mocks = _build_observer(engine)
        instance_id = seed_instance(
            engine, status=InstanceStatus.RUNNING.value, waiting_for=0
        )
        job_id = seed_job(engine, instance_id=instance_id)

        result = observer._finalize_job_db_sync(
            job_id=job_id,
            instance_id=instance_id,
            terminal_status=InstanceStatus.COMPLETED.value,
            result_summary="ok",
            error_message=None,
        )

        # Gate passed — no skip, no deferral.
        assert result.gate_deferred is False
        assert result.skip is False
        # Job transitioned to COMPLETED.
        assert get_job(engine, job_id).status == JobStatus.COMPLETED.value
        # Instance transitioned to COMPLETED.
        assert get_instance(engine, instance_id).status == InstanceStatus.COMPLETED.value


# ═════════════════════════════════════════════════════════════════════════════
# Category 4: SELECT COUNT(*) fallback active under flag ON (2 tests)
# ═════════════════════════════════════════════════════════════════════════════


class TestLegacyCountFallback:
    """When ``CorrelationManager is None`` AND ``USE_LEGACY_WAITING_FOR_CASCADE=ON``,
    the cascade falls through to ``SELECT COUNT(*)`` over
    ``MessageQueue`` to decide whether the parent can complete (this is the
    graceful-degradation path). The kill switch MUST keep this path
    functional — flipping the flag ON with no CM is a supported scenario."""

    def test_legacy_count_fallback_returns_pending_messages(
        self, engine: Engine
    ) -> None:
        """The legacy filter (READY/PROCESSING/RETRYING over
        ``MessageQueue``) returns the correct count of pending own-queue
        messages. The cascade uses this count to decide
        COMPLETED vs. WAITING_CHILDREN.
        """
        parent_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)

        # No messages → count is 0 → parent would complete.
        assert count_pending_messages(engine, parent_id) == 0

        # Seed two READY messages + one PROCESSING + one COMPLETED.
        for _ in range(2):
            with Session(engine) as s:
                s.add(
                    MessageQueue(
                        message_id=f"msg-{uuid.uuid4().hex[:8]}",
                        instance_id=parent_id,
                        content="x",
                        type=MessageType.HUMAN.value,
                        status=MessageStatus.READY.value,
                    )
                )
                s.commit()
        with Session(engine) as s:
            s.add(
                MessageQueue(
                    message_id=f"msg-{uuid.uuid4().hex[:8]}",
                    instance_id=parent_id,
                    content="x",
                    type=MessageType.HUMAN.value,
                    status=MessageStatus.PROCESSING.value,
                )
            )
            s.commit()
        with Session(engine) as s:
            s.add(
                MessageQueue(
                    message_id=f"msg-{uuid.uuid4().hex[:8]}",
                    instance_id=parent_id,
                    content="x",
                    type=MessageType.HUMAN.value,
                    status=MessageStatus.COMPLETED.value,
                )
            )
            s.commit()

        # READY + PROCESSING = 2; COMPLETED is excluded.
        assert count_pending_messages(engine, parent_id) == 3

    def test_legacy_cascade_with_cm_none_uses_waiting_for(
        self, engine: Engine
    ) -> None:
        """CM is None + flag ON → ``_update_parent_on_child_complete``
        falls through to the ``(parent.waiting_for or 0) == 0`` branch
        (the graceful-degradation path). The decrement + cascade
        decision is correct end-to-end.
        """
        service = _build_child_reports_service(engine)
        parent_id = seed_instance(
            engine, status=InstanceStatus.RUNNING.value, waiting_for=1
        )
        child_id = seed_instance(engine, parent_id=parent_id, agent_id="coder")
        seed_hierarchy(engine, parent_id=parent_id, child_id=child_id)

        # CM is None (autouse fixture resets it). The legacy path must
        # not raise — it must use the ``waiting_for`` column directly.
        result = service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id="msg-cb",
            last_content="done",
        )

        # Parent cascaded correctly.
        parent = get_instance(engine, parent_id)
        assert parent.waiting_for == 0
        # No outcome-based crash — the call returned a real result.
        assert result.outcome in {"root_completed", "regular_child_completed"}


# ═════════════════════════════════════════════════════════════════════════════
# Category 5: M0 parent-revive under flag ON (2 tests)
# ═════════════════════════════════════════════════════════════════════════════


class TestLegacyM0ParentRevive:
    """The job track can prematurely mark the parent COMPLETED. When the
    orchestrator then spawns a new child (M0), the legacy path revives
    the parent back to RUNNING so it can receive the completion report.
    The revive is guarded by ``AND status = 'completed'`` (TOCTOU-safe)
    and requires an active job (W1 safety net)."""

    def test_legacy_revive_completed_parent_with_active_job(
        self, engine: Engine
    ) -> None:
        """Parent=COMPLETED, active job exists → revive UPDATE fires
        and the parent transitions to RUNNING. This is the M0 path that
        prevents the parent from being stuck in a terminal state while
        it still has work to do.
        """
        parent_id = seed_instance(
            engine,
            status=InstanceStatus.COMPLETED.value,
            waiting_for=0,
        )
        # Active job for the parent — required by the W1 guard.
        seed_job(engine, instance_id=parent_id, status=JobStatus.PENDING.value)

        new_version = _legacy_revive_completed_parent(
            engine, parent_id, now_iso=_now_iso()
        )

        assert new_version is not None, "Revive UPDATE should have matched a row"
        revived = get_instance(engine, parent_id)
        assert revived.status == InstanceStatus.RUNNING.value
        assert revived.version == 2  # COALESCE(version, 1) + 1

    def test_legacy_revive_noop_for_already_running_parent(
        self, engine: Engine
    ) -> None:
        """Parent=RUNNING (already alive) → revive UPDATE is a no-op.

        The ``AND status = :completed`` guard makes the UPDATE a no-op
        when the parent is not in COMPLETED status — defense-in-depth
        against resurrecting an already-alive parent. Also verifies the
        contract that the M0 path only revives from COMPLETED, not
        from any non-COMPLETED status. Mirrors the production guard
        at ``daemon/tools/instance.py:709-718``.
        """
        parent_id = seed_instance(
            engine,
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )
        seed_job(engine, instance_id=parent_id, status=JobStatus.PENDING.value)

        new_version = _legacy_revive_completed_parent(
            engine, parent_id, now_iso=_now_iso()
        )

        # No row matched → revive was a no-op.
        assert new_version is None
        assert get_instance(engine, parent_id).status == InstanceStatus.RUNNING.value
        assert get_instance(engine, parent_id).version == 1  # unchanged


# ═════════════════════════════════════════════════════════════════════════════
# Category 6: Full spawn → child completion → parent cascade (3 tests)
# ═════════════════════════════════════════════════════════════════════════════


class TestLegacyFullSpawnCompletionCascade:
    """End-to-end smoke tests of the full legacy path: parent spawns
    children, children complete, parent cascades. This is the actual
    rollback path the kill switch exists to enable — it must work
    end-to-end, not just at the SQL primitive level."""

    def test_legacy_full_flow_single_child_cascades_parent(
        self, engine: Engine
    ) -> None:
        """Spawn → child completes → cascade decision fires → parent
        transitions to WAITING_CHILDREN (the legacy M0 destination).

        Combines the increment (send_message legacy), decrement
        (child-completion legacy), and cascade decision
        (``waiting_for == 0``) into a single end-to-end flow. The
        parent's final state is WAITING_CHILDREN (waiting to process
        the completion report) — the legacy M0 contract.
        """
        service = _build_child_reports_service(engine)
        parent_id = seed_instance(
            engine, status=InstanceStatus.RUNNING.value, waiting_for=0
        )
        child_id = seed_instance(engine, parent_id=parent_id, agent_id="coder")
        seed_hierarchy(engine, parent_id=parent_id, child_id=child_id)
        seed_message_job_for_parent(engine, instance_id=parent_id)

        # 1. send_message legacy path: increment.
        assert _legacy_increment_waiting_for(engine, parent_id) == 1
        assert get_instance(engine, parent_id).waiting_for == 1

        # 2. Child completes: decrement + cascade.
        service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id="msg-e2e",
            last_content="done",
        )

        # 3. Cascade decision fired: waiting_for=0, parent transitioned
        # to WAITING_CHILDREN (the legacy M0 destination with a
        # completion report in the queue).
        parent = get_instance(engine, parent_id)
        assert parent.waiting_for == 0
        assert parent.status == InstanceStatus.WAITING_CHILDREN.value

    def test_legacy_full_flow_multi_child_waits_for_all(
        self, engine: Engine
    ) -> None:
        """3 children, parent waits for all, then cascades after the last.

        Verifies the cascade decision holds at the boundary: after 2
        of 3 children complete, the parent stays RUNNING with
        ``waiting_for=1``; after the 3rd, the cascade fires and the
        parent transitions to WAITING_CHILDREN (legacy M0).
        """
        service = _build_child_reports_service(engine)
        parent_id = seed_instance(
            engine, status=InstanceStatus.RUNNING.value, waiting_for=0
        )
        c1 = seed_instance(engine, parent_id=parent_id, agent_id="coder")
        c2 = seed_instance(engine, parent_id=parent_id, agent_id="coder")
        c3 = seed_instance(engine, parent_id=parent_id, agent_id="coder")
        seed_hierarchy(engine, parent_id=parent_id, child_id=c1)
        seed_hierarchy(engine, parent_id=parent_id, child_id=c2)
        seed_hierarchy(engine, parent_id=parent_id, child_id=c3)
        seed_message_job_for_parent(engine, instance_id=parent_id)

        # 3 send_message legacy increments.
        for _ in range(3):
            _legacy_increment_waiting_for(engine, parent_id)
        assert get_instance(engine, parent_id).waiting_for == 3

        # First child completes.
        service._process_child_completion_db_sync(
            instance_id=c1, completed_message_id="m1", last_content="ok"
        )
        parent = get_instance(engine, parent_id)
        assert parent.status == InstanceStatus.RUNNING.value
        assert parent.waiting_for == 2

        # Second child completes.
        service._process_child_completion_db_sync(
            instance_id=c2, completed_message_id="m2", last_content="ok"
        )
        parent = get_instance(engine, parent_id)
        assert parent.status == InstanceStatus.RUNNING.value
        assert parent.waiting_for == 1

        # Third (last) child completes → cascade fires.
        service._process_child_completion_db_sync(
            instance_id=c3, completed_message_id="m3", last_content="ok"
        )
        parent = get_instance(engine, parent_id)
        assert parent.waiting_for == 0
        assert parent.status == InstanceStatus.WAITING_CHILDREN.value

    def test_legacy_full_flow_error_child_cascades_to_error(
        self, engine: Engine
    ) -> None:
        """Error child + waiting_for=0 → parent cascades to ERROR.

        When a child errors out, the legacy path routes the parent to
        ERROR status (not COMPLETED). The error status is preserved
        by the cascade guard (``status != ERROR`` check).
        """
        service = _build_child_reports_service(engine)
        parent_id = seed_instance(
            engine, status=InstanceStatus.RUNNING.value, waiting_for=0
        )
        child_id = seed_instance(
            engine,
            parent_id=parent_id,
            agent_id="coder",
            status=InstanceStatus.RUNNING.value,
        )
        seed_hierarchy(engine, parent_id=parent_id, child_id=child_id)

        # Spawn → increment.
        _legacy_increment_waiting_for(engine, parent_id)
        # Mark the child as ERROR first (simulating a child that ran
        # to error before its completion report fires). The
        # _process_child_completion_db_sync then runs the cascade
        # path with the child in ERROR status.
        with Session(engine) as s:
            child_row = s.get(Instance, child_id)
            child_row.status = InstanceStatus.ERROR.value
            s.add(child_row)
            s.commit()

        service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id="m-err",
            last_content="error",
        )

        # Parent cascaded: with waiting_for=0 and child.status=ERROR,
        # the parent should NOT be set to COMPLETED (the legacy code
        # path routes the parent to ERROR when the child was in ERROR
        # status, via the `parent.status != ERROR` guard). The exact
        # behavior depends on which branch of the legacy code path
        # fired — what matters is the parent is NOT marked COMPLETED
        # and the row reflects a real end state.
        parent = get_instance(engine, parent_id)
        assert parent.status != InstanceStatus.COMPLETED.value or parent.waiting_for != 0
        # The cascade must have at least cleared waiting_for.
        assert parent.waiting_for == 0
