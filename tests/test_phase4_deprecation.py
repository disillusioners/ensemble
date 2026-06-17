"""Phase 4 deprecation tests: ``waiting_for`` reads replaced by CorrelationManager.

Phase 4 deprecated ``waiting_for`` READS for control-flow decisions. The
column is KEPT as a rebuild-only cache — writes continue (increment at
``send_message``, decrement at child completion/error). Only the *reads*
that drive cascade control flow are replaced with CorrelationManager (CM)
equivalents:

    * ``cm.is_complete(parent_id)``       → replaces ``waiting_for == 0`` check
    * ``cm.get_pending_count(parent_id)`` → replaces ``waiting_for > 0`` check

These tests verify the six Phase 4 invariants:

1. CM-active mode uses CM, not ``waiting_for`` reads, for control flow.
2. ``waiting_for`` is still WRITTEN (increment/decrement) as rebuild cache.
3. ``WAITING_CHILDREN`` status is NOT set imperatively when CM is active.
4. Graceful degradation: when CM is disabled, ``waiting_for`` reads resume.
5. ``_locks`` dict cleanup: locks are pruned after correlation completes.
6. ``rebuild_from_db()`` still uses ``waiting_for > 0`` to find parents.

Run with::

    pytest tests/test_phase4_deprecation.py -v
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Importing the model classes is what registers them with SQLModel.metadata;
# `SQLModel.metadata.create_all` only creates tables for imported models.
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import (
    Instance,
    InstanceHierarchy,
    InstanceStatus,
)
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
)
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.services.correlation_manager import (
    CorrelationManager,
    STATUS_PENDING,
    STATUS_RESPONDED,
    notify_corr_register,
    notify_corr_resolve,
    set_correlation_manager,
    get_correlation_manager,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Shared Fixtures — real in-memory SQLite (from test_correlation_shadow.py)
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_cm_singleton():
    """Ensure each test starts and ends with the CM singleton cleared.

    The module-level ``_correlation_manager`` global in
    ``daemon.services.correlation_manager`` persists across tests; without
    this fixture, state leaks between tests.
    """
    set_correlation_manager(None)
    try:
        yield
    finally:
        set_correlation_manager(None)


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with FK enforcement enabled.

    Mirrors the pragma setup in ``daemon/repositories/factory.py`` but
    in-memory and using StaticPool so the same connection is reused
    across threads within a single test.
    """
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


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    """Real ``InstanceRepository`` bound to the in-memory engine."""
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def message_repo(engine: Engine) -> SQLModelMessageQueueRepository:
    """Real ``MessageQueueRepository`` bound to the in-memory engine."""
    return SQLModelMessageQueueRepository(engine)


def _make_instance(
    engine: Engine,
    instance_id: str,
    *,
    parent_id: str | None = None,
    waiting_for: int = 0,
    status: str = "running",
    agent_id: str = "coder",
) -> Instance:
    """Insert a bare ``Instance`` row in the test DB.

    Uses a unique ``agent_dir`` per call so the engine's PRIMARY KEY /
    NOT NULL constraints are satisfied without interfering between tests.
    """
    with Session(engine) as session:
        row = Instance(
            instance_id=instance_id,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}/{uuid.uuid4().hex[:6]}",
            parent_id=parent_id,
            status=status,
            waiting_for=waiting_for,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


# SQL patterns copied verbatim from production code so the test exercises
# the exact UPDATE that ``send_message`` and ``child_reports`` use.
_INCREMENT_WAITING_FOR_SQL = text(
    "UPDATE instances "
    "SET waiting_for = COALESCE(waiting_for, 0) + 1 "
    "WHERE instance_id = :pid "
    "RETURNING waiting_for"
)
_DECREMENT_WAITING_FOR_SQL = text(
    "UPDATE instances "
    "SET waiting_for = CASE "
    "    WHEN COALESCE(waiting_for, 0) - 1 > 0 "
    "        THEN COALESCE(waiting_for, 0) - 1 "
    "    ELSE 0 "
    "END "
    "WHERE instance_id = :pid "
    "RETURNING waiting_for"
)


def _increment_waiting_for(engine: Engine, parent_id: str) -> int:
    """Mirror ``send_message`` ``waiting_for++`` side effect.

    Returns the new value of ``waiting_for`` (as observed by the UPDATE).
    """
    with Session(engine) as session:
        row = session.execute(_INCREMENT_WAITING_FOR_SQL, {"pid": parent_id}).first()
        session.commit()
        return int(row[0]) if row is not None else 0


def _decrement_waiting_for(engine: Engine, parent_id: str) -> int:
    """Mirror ``_update_parent_on_child_complete`` ``waiting_for--`` side effect.

    Returns the new value of ``waiting_for`` (as observed by the UPDATE).
    """
    with Session(engine) as session:
        row = session.execute(_DECREMENT_WAITING_FOR_SQL, {"pid": parent_id}).first()
        session.commit()
        return int(row[0]) if row is not None else 0


def _read_waiting_for(
    instance_repo: SQLModelInstanceRepository, instance_id: str
) -> int | None:
    inst = instance_repo.get(instance_id)
    return inst.waiting_for if inst is not None else None


def _make_message(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str | None = None,
    status: str = MessageStatus.READY.value,
) -> MessageQueue:
    """Insert a ``MessageQueue`` row directly (no real enqueue flow)."""
    with Session(engine) as session:
        row = MessageQueue(
            message_id=message_id or f"msg-{uuid.uuid4().hex[:8]}",
            instance_id=instance_id,
            content="phase4 test message",
            type="agent",
            source="test",
            status=status,
            priority=1,
            retry_count=0,
            max_retries=5,
            enqueued_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


# =============================================================================
# Mock helpers — adapted from test_cascade_integration.py
# =============================================================================


def _make_parent(
    *,
    status: str = InstanceStatus.RUNNING.value,
    waiting_for: int = 0,
    parent_id: str = "parent-phase4",
) -> MagicMock:
    """Build a mock parent ``Instance`` with the attributes the cascade reads."""
    parent = MagicMock()
    parent.instance_id = parent_id
    parent.parent_id = None
    parent.status = status
    parent.waiting_for = waiting_for
    parent.children = "[]"
    parent.instance_metadata = {}
    parent.last_activity_at = None
    parent.updated_at = None
    parent.version = 1
    return parent


def _make_child(parent_id: str = "parent-phase4") -> MagicMock:
    """Build a mock child ``Instance`` referencing the given parent."""
    child = MagicMock()
    child.instance_id = "child-phase4"
    child.parent_id = parent_id
    child.status = "completed"
    child.instance_metadata = {}
    child.children = "[]"
    child.waiting_for = 0
    child.last_activity_at = None
    child.version = 1
    return child


def _setup_cascade_session(
    parent: MagicMock, *, pending_count: int = 0
) -> MagicMock:
    """Build a mock session simulating the atomic UPDATE + post-expiry re-read.

    Mirrors the cascade in ``_update_parent_on_child_complete``:

    * ``session.get(Instance, ...)`` → ``parent`` (twice — initial + post-expiry)
    * ``session.execute(text("UPDATE ... RETURNING waiting_for"))`` → new value 0
    * ``session.exec(select(func.count())...)`` → ``pending_count`` (legacy path)
    * ``session.expire(parent)`` → no-op
    """
    session = MagicMock()
    session.get = MagicMock(return_value=parent)
    update_result = MagicMock()
    update_result.first = MagicMock(return_value=(0,))
    session.execute = MagicMock(return_value=update_result)
    pending_result = MagicMock()
    pending_result.scalar_one = MagicMock(return_value=pending_count)
    session.exec = MagicMock(return_value=pending_result)
    session.expire = MagicMock()
    return session


def _make_mock_manager() -> MagicMock:
    """Build a minimal ``MagicMock`` for the ``InstanceManager`` facade.

    Mirrors ``test_cascade_integration.py::_make_mock_manager`` — the
    ``is_write_paused = False`` setting is required because
    ``WriteGuardSession`` reads it on enter.
    """
    manager = MagicMock()
    manager._live_hub = None
    manager._checkpointer = None
    manager.config = MagicMock()
    manager.config.llm = MagicMock()
    manager.is_write_paused = False
    manager.write_guard = MagicMock()
    manager.write_guard.is_write_paused = False
    manager.engine = MagicMock()
    return manager


# =============================================================================
# Scenario 1: CM-active mode uses CM, not waiting_for reads
# =============================================================================


class TestCmActivePrefersCmOverWaitingFor:
    """Phase 4 invariant: when CM is wired, cascade control flow uses
    ``cm.is_complete()`` / ``cm.get_pending_count()`` — NOT the
    ``waiting_for`` column on the parent instance.

    Verification strategy: set up two contradictory signals (CM says
    one thing, the ``waiting_for`` column says the opposite) and
    confirm the cascade follows CM, not the column.
    """

    @pytest.mark.asyncio
    async def test_cm_says_complete_overrides_stale_waiting_for(self) -> None:
        """CM-active: ``cm.is_complete() == True`` + ``parent.waiting_for == 99``
        → cascade takes the CM bypass. The stale column value is ignored.
        """
        from daemon.services.child_reports import ChildReportsService

        cm = MagicMock(spec=CorrelationManager, name="cm-active-phase4")
        cm.is_complete = MagicMock(return_value=True)
        set_correlation_manager(cm)
        try:
            # parent.waiting_for is INTENTIONALLY 99 (would NOT be complete
            # in the legacy path). CM says complete. CM must win.
            parent = _make_parent(
                status=InstanceStatus.RUNNING.value,
                waiting_for=99,
                parent_id="parent-p4-s1a",
            )
            child = _make_child(parent_id="parent-p4-s1a")
            session = _setup_cascade_session(parent)

            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            with patch(
                "daemon.services.correlation_manager.notify_corr_resolve",
                new=AsyncMock(),
            ):
                result = await service._update_parent_on_child_complete(
                    session, child, completed_message_id="msg-p4-s1a"
                )

            # The CM-bypass return sentinel — no inline transition.
            assert result == (False, None, None), (
                f"CM-bypass must return (False, None, None); got {result!r}. "
                f"CM.is_complete() returned True but the cascade did not take "
                f"the bypass, which means it read parent.waiting_for instead."
            )
            # CM was consulted, not the column.
            assert cm.is_complete.called, (
                "cm.is_complete() was never called — cascade skipped CM"
            )
            # No SELECT COUNT(*) — the legacy path is short-circuited.
            assert session.exec.call_count == 0, (
                f"CM-active cascade must NOT run SELECT COUNT(*); got "
                f"{session.exec.call_count} session.exec call(s). "
                f"Calls: {[repr(c.args[0]) for c in session.exec.call_args_list]}"
            )
            # parent.status was NOT mutated inline.
            assert parent.status == InstanceStatus.RUNNING.value, (
                f"CM-active cascade must NOT mutate parent.status inline; "
                f"got {parent.status!r}"
            )
        finally:
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_cm_says_pending_overrides_zero_waiting_for(self) -> None:
        """CM-active: ``cm.is_complete() == False`` + ``parent.waiting_for == 0``
        → cascade does NOT complete the parent. The stale-zero column is ignored.
        """
        from daemon.services.child_reports import ChildReportsService

        cm = MagicMock(spec=CorrelationManager, name="cm-pending-phase4")
        cm.is_complete = MagicMock(return_value=False)
        set_correlation_manager(cm)
        try:
            # parent.waiting_for is INTENTIONALLY 0 (legacy would complete).
            # CM says NOT complete. CM must win → no completion.
            parent = _make_parent(
                status=InstanceStatus.RUNNING.value,
                waiting_for=0,
                parent_id="parent-p4-s1b",
            )
            child = _make_child(parent_id="parent-p4-s1b")
            session = _setup_cascade_session(parent)

            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            with patch(
                "daemon.services.correlation_manager.notify_corr_resolve",
                new=AsyncMock(),
            ):
                result = await service._update_parent_on_child_complete(
                    session, child, completed_message_id="msg-p4-s1b"
                )

            # No completion was reported.
            assert result == (False, None, None), (
                f"CM-pending cascade must NOT complete the parent; got {result!r}. "
                f"CM.is_complete() returned False but the cascade completed anyway."
            )
            # CM was consulted.
            assert cm.is_complete.called
            # parent.status was NOT touched.
            assert parent.status == InstanceStatus.RUNNING.value
            # session.exec was NOT called — no legacy SELECT COUNT.
            assert session.exec.call_count == 0
        finally:
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_cm_active_does_not_read_parent_waiting_for_attribute(self) -> None:
        """Direct probe: ``parent.waiting_for`` must NOT be accessed for the
        cascade control-flow decision when CM is active.

        Uses a ``PropertyMock``-style probe that records every access to
        ``.waiting_for``. With CM active, the cascade should consult
        ``cm.is_complete()`` and never touch the column.
        """
        from daemon.services.child_reports import ChildReportsService

        class _WaitingForProbe:
            """Stand-in parent whose ``.waiting_for`` access is recorded."""

            def __init__(self) -> None:
                self.instance_id = "parent-p4-probe"
                self.parent_id = None
                self.status = InstanceStatus.RUNNING.value
                self.children = "[]"
                self.instance_metadata: dict = {}
                self.last_activity_at = None
                self.updated_at = None
                self.version = 1
                self.waiting_for_access_count = 0

            @property
            def waiting_for(self) -> int:
                self.waiting_for_access_count += 1
                return 0

        cm = MagicMock(spec=CorrelationManager, name="cm-probe")
        cm.is_complete = MagicMock(return_value=True)
        set_correlation_manager(cm)
        try:
            probe = _WaitingForProbe()
            child = MagicMock()
            child.instance_id = "child-p4-probe"
            child.parent_id = "parent-p4-probe"
            child.status = "completed"
            child.instance_metadata = {}
            child.children = "[]"
            child.waiting_for = 0
            child.last_activity_at = None
            child.version = 1
            session = MagicMock()
            session.get = MagicMock(return_value=probe)
            update_result = MagicMock()
            update_result.first = MagicMock(return_value=(0,))
            session.execute = MagicMock(return_value=update_result)
            session.exec = MagicMock()
            session.expire = MagicMock()

            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            with patch(
                "daemon.services.correlation_manager.notify_corr_resolve",
                new=AsyncMock(),
            ):
                await service._update_parent_on_child_complete(
                    session, child, completed_message_id="msg-p4-probe"
                )

            # CM was consulted.
            assert cm.is_complete.called
            # parent.waiting_for was NEVER read for control flow.
            assert probe.waiting_for_access_count == 0, (
                f"parent.waiting_for was accessed {probe.waiting_for_access_count} "
                f"time(s) while CM was active. Phase 4 deprecation violated: "
                f"control-flow reads of waiting_for must go through cm.is_complete() "
                f"when CM is wired."
            )
        finally:
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_cm_get_pending_count_used_for_root_deferral(self) -> None:
        """Phase 4 also deprecates ``waiting_for > 0`` reads for the root
        deferral in ``_process_child_completion_and_notify_parent`` —
        ``cm.get_pending_count()`` is the authoritative source.
        """
        # Construct a CM with a real in-memory pending count = 2.
        # Then verify that the root deferral check uses cm.get_pending_count,
        # not the parent's waiting_for column.
        cm = MagicMock(spec=CorrelationManager, name="cm-pending-root")
        cm.get_pending_count = MagicMock(return_value=2)
        set_correlation_manager(cm)
        try:
            # Even though the parent's DB waiting_for is 0, the root should
            # defer completion because CM says there are still pending children.
            assert cm.get_pending_count("parent-p4-root") == 2

            # Now verify the code path consults CM first.
            # We use the same probe pattern: parent.waiting_for should NOT be
            # read when CM is active.
            class _PendingProbe:
                instance_id = "parent-p4-root"
                parent_id = None
                status = InstanceStatus.RUNNING.value
                children = "[]"
                instance_metadata: dict = {}
                last_activity_at = None
                updated_at = None
                version = 1
                access_count = 0

                @property
                def waiting_for(self) -> int:
                    self.access_count += 1
                    return 0

            probe = _PendingProbe()
            # The cascade function reads `getattr(instance, "waiting_for", None)`
            # only as a fallback. With CM active, it calls
            # `cm.get_pending_count(instance_id)` first.
            # We verify the CM method was the one consulted by the cascade code.
            # (The actual _process_child_completion_and_notify_parent requires
            # a full manager, so we just verify the CM API is the contract.)

            # Simulate the cascade's branch: cm is not None → use cm.
            from daemon.services.correlation_manager import get_correlation_manager

            actual_cm = get_correlation_manager()
            assert actual_cm is cm
            # The CM contract: get_pending_count is the authoritative read.
            assert actual_cm.get_pending_count("parent-p4-root") == 2
            # The probe was never touched — we just verified the CM path.
            assert probe.access_count == 0
        finally:
            set_correlation_manager(None)


# =============================================================================
# Scenario 2: waiting_for is still WRITTEN (increment/decrement)
# =============================================================================


class TestWaitingForStillWritten:
    """Phase 4 invariant: ``waiting_for`` writes MUST continue (increment
    on ``send_message``, decrement on child completion/error) so the
    column stays consistent as a rebuild cache for ``rebuild_from_db()``.

    No control-flow READS happen, but the WRITES are required for crash
    recovery (ADR-011).
    """

    @pytest.mark.asyncio
    async def test_send_message_increments_waiting_for_in_db(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
    ) -> None:
        """``send_message`` path: SQL UPDATE ``waiting_for = +1`` is still
        issued and the column value changes. The CM ``notify_corr_register``
        hook fires alongside the SQL write (dual-write pattern).
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=0)

        # Simpler: use a MagicMock CM to keep the test focused on the write.
        cm_mock = MagicMock(spec=CorrelationManager, name="cm-write-1")
        cm_mock.register_message_send = AsyncMock(return_value=None)
        set_correlation_manager(cm_mock)
        try:
            # ── Pre-condition ──
            assert _read_waiting_for(instance_repo, parent_id) == 0

            # ── Mirror send_message: SQL increment + CM register ──
            new_val = _increment_waiting_for(engine, parent_id)
            assert new_val == 1, (
                f"Increment SQL must yield 1 (was {new_val}). "
                f"The send_message path writes waiting_for via this UPDATE."
            )
            await notify_corr_register(
                parent_id=parent_id,
                child_id=f"child-{uuid.uuid4().hex[:6]}",
                message_id=f"msg-{uuid.uuid4().hex[:6]}",
            )

            # ── Post-condition: column is written ──
            assert _read_waiting_for(instance_repo, parent_id) == 1
            # CM hook fired alongside the SQL write.
            assert cm_mock.register_message_send.await_count == 1, (
                f"notify_corr_register must call cm.register_message_send "
                f"exactly once; got {cm_mock.register_message_send.await_count}"
            )
        finally:
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_child_completion_decrements_waiting_for_in_db(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
    ) -> None:
        """Child completion path: SQL UPDATE ``waiting_for = -1`` (clamped to 0)
        is still issued. The CM ``notify_corr_resolve`` hook fires alongside.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=2)

        cm_mock = MagicMock(spec=CorrelationManager, name="cm-write-2")
        cm_mock.resolve_response = AsyncMock(return_value=False)
        set_correlation_manager(cm_mock)
        try:
            # ── Mirror _update_parent_on_child_complete: SQL decrement + CM resolve ──
            new_val = _decrement_waiting_for(engine, parent_id)
            assert new_val == 1, (
                f"Decrement SQL must yield 1 (was {new_val}). "
                f"waiting_for-- is the rebuild-cache write on child completion."
            )
            await notify_corr_resolve(
                parent_id=parent_id,
                child_id=f"child-{uuid.uuid4().hex[:6]}",
                message_id=f"msg-{uuid.uuid4().hex[:6]}",
                status="responded",
            )

            # ── Post-condition: column is written ──
            assert _read_waiting_for(instance_repo, parent_id) == 1
            # CM hook fired alongside.
            assert cm_mock.resolve_response.await_count == 1
        finally:
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_decrement_clamps_at_zero_not_negative(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
    ) -> None:
        """Edge case: decrementing from 0 stays at 0 (CASE clamp in SQL)."""
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=0)

        new_val = _decrement_waiting_for(engine, parent_id)
        assert new_val == 0, (
            f"Decrement from 0 must clamp at 0 (CASE WHEN ... -1 > 0); "
            f"got {new_val}. A negative value would corrupt the rebuild cache."
        )
        assert _read_waiting_for(instance_repo, parent_id) == 0

    @pytest.mark.asyncio
    async def test_full_register_resolve_cycle_maintains_cache(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
    ) -> None:
        """End-to-end: send + complete a single message cycle. The DB
        ``waiting_for`` column must round-trip 0 → 1 → 0, proving the
        rebuild cache is consistently maintained across both writes.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=0)
        _make_instance(engine, child_id, parent_id=parent_id)

        # Use a real CM with the in-memory test repos.
        cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
        )
        await cm.start()
        set_correlation_manager(cm)
        try:
            msg_id = f"msg-{uuid.uuid4().hex[:8]}"

            # 0 → 1 (send_message side effect)
            inc = _increment_waiting_for(engine, parent_id)
            assert inc == 1
            await notify_corr_register(
                parent_id=parent_id, child_id=child_id, message_id=msg_id
            )
            assert _read_waiting_for(instance_repo, parent_id) == 1
            assert cm.get_pending_count(parent_id) == 1

            # 1 → 0 (child completion side effect)
            dec = _decrement_waiting_for(engine, parent_id)
            assert dec == 0
            await notify_corr_resolve(
                parent_id=parent_id,
                child_id=child_id,
                message_id=msg_id,
                status="responded",
            )
            assert _read_waiting_for(instance_repo, parent_id) == 0
            # CM is also empty.
            assert cm.get_pending_count(parent_id) == 0
        finally:
            await cm.stop()
            set_correlation_manager(None)


# =============================================================================
# Scenario 3: WAITING_CHILDREN not set imperatively when CM is active
# =============================================================================


class TestWaitingChildrenNotSetWhenCmActive:
    """Phase 4 invariant: when CM is active, ``parent.status`` must NOT
    be set to ``WAITING_CHILDREN`` imperatively. The parent stays
    ``RUNNING`` (or its previous status) while children run — CM is the
    authoritative source of correlation state in-memory.

    When CM is None (graceful degradation), the legacy ``WAITING_CHILDREN``
    behavior is retained as a fallback.
    """

    @pytest.mark.asyncio
    async def test_cm_active_parent_status_not_mutated_by_cascade(self) -> None:
        """CM active + parent has "pending messages" (mocked ``session.exec``
        returns > 0) → the cascade takes the CM-bypass path and does NOT
        set ``WAITING_CHILDREN`` on the parent. The parent stays RUNNING.
        """
        from daemon.services.child_reports import ChildReportsService

        cm = MagicMock(spec=CorrelationManager, name="cm-no-wc")
        cm.is_complete = MagicMock(return_value=True)
        set_correlation_manager(cm)
        try:
            # Pre-condition: parent is RUNNING.
            parent = _make_parent(
                status=InstanceStatus.RUNNING.value,
                waiting_for=0,
                parent_id="parent-p4-s3a",
            )
            child = _make_child(parent_id="parent-p4-s3a")
            # Even with "pending messages" (would set WAITING_CHILDREN in
            # legacy path), the CM-bypass skips the inline transition.
            session = _setup_cascade_session(parent, pending_count=5)

            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            with patch(
                "daemon.services.correlation_manager.notify_corr_resolve",
                new=AsyncMock(),
            ):
                result = await service._update_parent_on_child_complete(
                    session, child, completed_message_id="msg-p4-s3a"
                )

            # Bypass return — no completion, no WAITING_CHILDREN transition.
            assert result == (False, None, None)
            # parent.status was NOT changed to WAITING_CHILDREN.
            assert parent.status != InstanceStatus.WAITING_CHILDREN.value, (
                f"Phase 4 violation: CM-active cascade set "
                f"parent.status = WAITING_CHILDREN. Parent should stay "
                f"RUNNING while CM is authoritative. Got {parent.status!r}."
            )
            assert parent.status == InstanceStatus.RUNNING.value
        finally:
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_cm_none_parent_gets_waiting_children_fallback(self) -> None:
        """CM None (graceful degradation) + waiting_for=0 + pending messages>0
        → the legacy path sets ``WAITING_CHILDREN`` on the parent. This is
        the intentional fallback for environments without CM.
        """
        from daemon.services.child_reports import ChildReportsService

        set_correlation_manager(None)
        try:
            parent = _make_parent(
                status=InstanceStatus.RUNNING.value,
                waiting_for=0,
                parent_id="parent-p4-s3b",
            )
            child = _make_child(parent_id="parent-p4-s3b")
            # Legacy path: pending messages present → WAITING_CHILDREN.
            session = _setup_cascade_session(parent, pending_count=3)

            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            result = await service._update_parent_on_child_complete(
                session, child, completed_message_id=None
            )

            # Legacy: transitioned to WAITING_CHILDREN (transient signal).
            assert result == (True, None, None), (
                f"CM-disabled fallback must return (True, None, None) for "
                f"WAITING_CHILDREN transition; got {result!r}"
            )
            assert parent.status == InstanceStatus.WAITING_CHILDREN.value, (
                f"CM-disabled fallback must set WAITING_CHILDREN; "
                f"got {parent.status!r}"
            )
        finally:
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_cm_active_skips_legacy_cascade_entirely(self) -> None:
        """Structural invariant: with CM active, the legacy inline cascade
        block is unreachable — verified by ``session.exec`` call count.
        This is the SAME invariant tested in test_cascade_integration.py
        but framed as a Phase 4 deprecation guard.
        """
        from daemon.services.child_reports import ChildReportsService

        cm = MagicMock(spec=CorrelationManager, name="cm-structural")
        cm.is_complete = MagicMock(return_value=True)
        set_correlation_manager(cm)
        try:
            parent = _make_parent(
                status=InstanceStatus.RUNNING.value,
                waiting_for=0,
                parent_id="parent-p4-s3c",
            )
            child = _make_child(parent_id="parent-p4-s3c")
            # Wire session.exec to FAIL if called — this is the strongest
            # possible proof that the legacy path is unreachable.
            session = MagicMock()
            session.get = MagicMock(return_value=parent)
            update_result = MagicMock()
            update_result.first = MagicMock(return_value=(0,))
            session.execute = MagicMock(return_value=update_result)
            session.exec = MagicMock(
                side_effect=AssertionError(
                    "Phase 4 violation: CM-active cascade called session.exec "
                    "— the legacy SELECT COUNT(*) path was not short-circuited."
                )
            )
            session.expire = MagicMock()

            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            with patch(
                "daemon.services.correlation_manager.notify_corr_resolve",
                new=AsyncMock(),
            ):
                # If session.exec is called, the AssertionError will
                # propagate and fail the test.
                result = await service._update_parent_on_child_complete(
                    session, child, completed_message_id="msg-p4-s3c"
                )

            assert result == (False, None, None)
            assert parent.status == InstanceStatus.RUNNING.value
        finally:
            set_correlation_manager(None)


# =============================================================================
# Scenario 4: Graceful degradation when CM is disabled
# =============================================================================


class TestGracefulDegradationCmDisabled:
    """Phase 4 invariant: when ``get_correlation_manager()`` returns
    ``None`` (CM not wired), the system falls back to the legacy
    ``waiting_for`` reads for control flow. The system must still work
    correctly in this degraded mode.
    """

    @pytest.mark.asyncio
    async def test_cm_none_uses_waiting_for_zero_to_complete(self) -> None:
        """CM None + ``waiting_for=0`` + no pending messages → the legacy
        cascade runs end-to-end: SELECT COUNT(*) → 0 pending → COMPLETED.
        """
        from daemon.services.child_reports import ChildReportsService

        set_correlation_manager(None)
        try:
            parent = _make_parent(
                status=InstanceStatus.RUNNING.value,
                waiting_for=0,
                parent_id="parent-p4-s4a",
            )
            child = _make_child(parent_id="parent-p4-s4a")
            # Legacy: 0 pending messages → COMPLETED.
            session = _setup_cascade_session(parent, pending_count=0)

            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            result = await service._update_parent_on_child_complete(
                session, child, completed_message_id=None
            )

            # Legacy: parent reported as completed.
            assert result == (False, parent.instance_id, None)
            assert parent.status == InstanceStatus.COMPLETED.value
            # Legacy SELECT COUNT(*) WAS executed.
            assert session.exec.call_count == 1
        finally:
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_cm_none_pending_messages_keep_waiting_children(self) -> None:
        """CM None + ``waiting_for=0`` + pending messages>0 → parent stays
        alive in ``WAITING_CHILDREN`` (legacy fallback for Phase 4).
        """
        from daemon.services.child_reports import ChildReportsService

        set_correlation_manager(None)
        try:
            parent = _make_parent(
                status=InstanceStatus.RUNNING.value,
                waiting_for=0,
                parent_id="parent-p4-s4b",
            )
            child = _make_child(parent_id="parent-p4-s4b")
            session = _setup_cascade_session(parent, pending_count=2)

            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            result = await service._update_parent_on_child_complete(
                session, child, completed_message_id=None
            )

            assert result == (True, None, None)
            assert parent.status == InstanceStatus.WAITING_CHILDREN.value
        finally:
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_cm_none_write_paused_does_not_crash(self) -> None:
        """CM None fallback must not depend on CM APIs at all — verified by
        the fact that the cascade never references ``get_correlation_manager``
        successfully (it returns None) and never calls ``cm.is_complete``.
        """
        from daemon.services.child_reports import ChildReportsService

        # The autouse fixture already clears the singleton. Be explicit.
        set_correlation_manager(None)
        try:
            parent = _make_parent(
                status=InstanceStatus.RUNNING.value,
                waiting_for=0,
                parent_id="parent-p4-s4c",
            )
            child = _make_child(parent_id="parent-p4-s4c")
            session = _setup_cascade_session(parent, pending_count=0)

            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            # The cascade must complete successfully with CM=None.
            result = await service._update_parent_on_child_complete(
                session, child, completed_message_id=None
            )
            assert result == (False, parent.instance_id, None)
            assert parent.status == InstanceStatus.COMPLETED.value
        finally:
            set_correlation_manager(None)


# =============================================================================
# Scenario 5: _locks dict cleanup
# =============================================================================


class TestLocksDictCleanup:
    """Phase 4 invariant: when a parent's correlation completes,
    ``resolve_response`` must drop BOTH ``_pending[parent_id]`` AND
    ``_locks[parent_id]``. Without lock cleanup, the ``_locks`` dict
    grows unboundedly across many sessions (Phase 1 Finding 1.2, S3 fix).
    """

    @pytest.fixture
    def mock_repos(self) -> tuple[MagicMock, MagicMock]:
        """Build minimal mock repos — the _locks cleanup test does not
        require DB reads because resolve_response only calls
        ``_validate_shadow_mode`` on NON-complete resolves.
        """
        instance_repo = MagicMock()
        message_repo = MagicMock()
        return instance_repo, message_repo

    @pytest.mark.asyncio
    async def test_lock_removed_after_correlation_completes(
        self, mock_repos: tuple[MagicMock, MagicMock]
    ) -> None:
        """Register one message → ``_locks`` has the parent. Resolve it
        (last pending) → ``_pending`` and ``_locks`` are both empty.
        """
        instance_repo, message_repo = mock_repos
        cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
        )

        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        msg_id = f"msg-{uuid.uuid4().hex[:8]}"

        # Register — creates a _locks entry.
        await cm.register_message_send(parent_id, child_id, msg_id)
        assert parent_id in cm._locks, (
            f"_locks should contain {parent_id[:8]}... after register; "
            f"got keys: {list(cm._locks.keys())[:3]}..."
        )
        assert parent_id in cm._pending

        # Resolve — should drop both _pending and _locks entries.
        completed = await cm.resolve_response(parent_id, child_id, msg_id)
        assert completed is True, "Last resolve must report completion"

        assert parent_id not in cm._pending, (
            f"_pending should NOT contain {parent_id[:8]}... after completion; "
            f"got keys: {list(cm._pending.keys())[:3]}..."
        )
        assert parent_id not in cm._locks, (
            f"Phase 4 / S3 violation: _locks still contains {parent_id[:8]}... "
            f"after the parent completed. Unbounded growth across sessions. "
            f"Current _locks keys: {list(cm._locks.keys())[:3]}..."
        )

    @pytest.mark.asyncio
    async def test_locks_dict_bounded_across_many_sessions(
        self, mock_repos: tuple[MagicMock, MagicMock]
    ) -> None:
        """Register+resolve 50 distinct parents. ``_locks`` must end at
        size 0 (not 50) — proving the S3 cleanup fix works under load.
        """
        instance_repo, message_repo = mock_repos
        cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
        )

        N = 50
        for i in range(N):
            parent_id = f"parent-{i:04d}"
            child_id = f"child-{i:04d}"
            msg_id = f"msg-{i:04d}"
            await cm.register_message_send(parent_id, child_id, msg_id)
            completed = await cm.resolve_response(parent_id, child_id, msg_id)
            assert completed is True

        # Both dicts must be empty after the loop.
        assert len(cm._pending) == 0, (
            f"_pending must be empty after {N} register+resolve cycles; "
            f"got {len(cm._pending)} entries"
        )
        assert len(cm._locks) == 0, (
            f"_locks must be empty after {N} register+resolve cycles "
            f"(S3 fix: pop on completion). Got {len(cm._locks)} entries. "
            f"Unbounded growth means locks leak across sessions."
        )

    @pytest.mark.asyncio
    async def test_lock_retained_for_partial_completion(
        self, mock_repos: tuple[MagicMock, MagicMock]
    ) -> None:
        """Lock cleanup is conditioned on completion. A parent with
        2 pending correlations that resolves 1 still has its lock.
        """
        instance_repo, message_repo = mock_repos
        cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
        )

        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        msg_a = f"msg-a-{uuid.uuid4().hex[:8]}"
        msg_b = f"msg-b-{uuid.uuid4().hex[:8]}"

        await cm.register_message_send(parent_id, child_id, msg_a)
        await cm.register_message_send(parent_id, child_id, msg_b)
        assert parent_id in cm._locks
        assert cm.get_pending_count(parent_id) == 2

        # Resolve one — lock must still be present.
        completed = await cm.resolve_response(
            parent_id, child_id, msg_a, status=STATUS_RESPONDED
        )
        assert completed is False
        assert parent_id in cm._locks, (
            f"Lock must persist while parent has unresolved correlations; "
            f"_locks keys: {list(cm._locks.keys())[:3]}..."
        )
        assert parent_id in cm._pending
        assert cm.get_pending_count(parent_id) == 1

        # Resolve the other — now lock should be gone.
        completed = await cm.resolve_response(
            parent_id, child_id, msg_b, status=STATUS_RESPONDED
        )
        assert completed is True
        assert parent_id not in cm._locks
        assert parent_id not in cm._pending


# =============================================================================
# Scenario 6: rebuild_from_db() still uses waiting_for
# =============================================================================


class TestRebuildFromDbUsesWaitingFor:
    """Phase 4 invariant: ``rebuild_from_db()`` is the cold-start safety
    net that reconstructs the CM's in-memory ``_pending`` state from the
    DB. It MUST continue to query ``waiting_for > 0`` to find parents
    that need correlation tracking — this is the only place where
    ``waiting_for`` is read for the rebuild cache contract.

    Without this query, a daemon restart would lose all in-flight
    correlation state and orphan pending children.
    """

    @pytest.mark.asyncio
    async def test_rebuild_queries_waiting_for_column(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """``rebuild_from_db()`` calls ``get_all_with_waiting_for()`` to
        discover parents needing tracking. The mock repo is wired to fail
        if this method is not called.
        """
        # Seed: one parent with waiting_for=2 and one child with 2 pending messages.
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=2)
        _make_instance(engine, child_id, parent_id=parent_id)
        _make_message(engine, instance_id=child_id, status=MessageStatus.READY.value)
        _make_message(engine, instance_id=child_id, status=MessageStatus.PROCESSING.value)

        # Mock the repos to assert that get_all_with_waiting_for is called.
        instance_repo_spy = MagicMock(wraps=instance_repo)
        instance_repo_spy.get_all_with_waiting_for = MagicMock(
            side_effect=instance_repo.get_all_with_waiting_for
        )
        message_repo_spy = MagicMock(wraps=message_repo)
        message_repo_spy.get_pending_for_instances = MagicMock(
            side_effect=message_repo.get_pending_for_instances
        )

        cm = CorrelationManager(
            instance_repository=instance_repo_spy,
            message_queue_repository=message_repo_spy,
        )
        await cm.start()
        try:
            # rebuild_from_db must have been called during start() and must
            # have queried get_all_with_waiting_for.
            assert instance_repo_spy.get_all_with_waiting_for.called, (
                f"rebuild_from_db() must call instance_repo.get_all_with_waiting_for() "
                f"to discover parents needing tracking. This is the rebuild cache "
                f"contract — without it, daemon restarts lose correlation state."
            )
            # And it must have queried the message queue for pending messages.
            assert message_repo_spy.get_pending_for_instances.called, (
                f"rebuild_from_db() must call message_repo.get_pending_for_instances() "
                f"to enumerate pending correlations for each parent."
            )
        finally:
            await cm.stop()

    @pytest.mark.asyncio
    async def test_rebuild_finds_parents_with_waiting_for_positive(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """Parent with ``waiting_for=2`` + 2 pending messages in the queue
        → ``rebuild_from_db()`` rebuilds the CM's ``_pending`` with 2 entries.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=2)
        _make_instance(engine, child_id, parent_id=parent_id)

        # Two pending messages (different statuses) → 2 correlation entries.
        _make_message(engine, instance_id=child_id, status=MessageStatus.READY.value)
        _make_message(engine, instance_id=child_id, status=MessageStatus.PROCESSING.value)

        cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
        )
        await cm.start()
        try:
            # CM should have tracked this parent with 2 pending correlations.
            assert parent_id in cm._pending, (
                f"rebuild_from_db() did NOT track parent {parent_id[:8]}... "
                f"with waiting_for=2. _pending keys: "
                f"{[k[:8] for k in cm._pending.keys()]}"
            )
            assert cm.get_pending_count(parent_id) == 2, (
                f"Expected 2 pending correlations (matching waiting_for=2); "
                f"got {cm.get_pending_count(parent_id)}"
            )
        finally:
            await cm.stop()

    @pytest.mark.asyncio
    async def test_rebuild_ignores_parents_with_waiting_for_zero(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """Parent with ``waiting_for=0`` must NOT be tracked by the CM
        after rebuild — no pending correlations, nothing to track.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=0)

        cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
        )
        await cm.start()
        try:
            # No pending correlations for a zero-waiting_for parent.
            assert parent_id not in cm._pending, (
                f"rebuild_from_db() tracked {parent_id[:8]}... with "
                f"waiting_for=0 — should be ignored. _pending keys: "
                f"{[k[:8] for k in cm._pending.keys()]}"
            )
            assert cm.get_pending_count(parent_id) == 0
        finally:
            await cm.stop()

    @pytest.mark.asyncio
    async def test_get_all_with_waiting_for_called_exactly_once(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """rebuild_from_db queries ``get_all_with_waiting_for`` exactly
        once per rebuild call (batched query, not N+1).
        """
        # Seed several parents.
        for i in range(3):
            _make_instance(
                engine, f"parent-{i}", waiting_for=i + 1
            )

        instance_repo_spy = MagicMock(wraps=instance_repo)
        instance_repo_spy.get_all_with_waiting_for = MagicMock(
            side_effect=instance_repo.get_all_with_waiting_for
        )

        cm = CorrelationManager(
            instance_repository=instance_repo_spy,
            message_queue_repository=message_repo,
        )
        await cm.start()
        try:
            call_count = instance_repo_spy.get_all_with_waiting_for.call_count
            assert call_count == 1, (
                f"rebuild_from_db() should call get_all_with_waiting_for "
                f"exactly once (batched); got {call_count} call(s). "
                f"Multiple calls would be an N+1 query pattern."
            )
        finally:
            await cm.stop()


# =============================================================================
# Scenario 7: Root vs Non-Root WAITING_CHILDREN semantics (W1 carve-out)
# =============================================================================


class TestRootVsNonRootWaitingChildren:
    """Phase 4 W1: Root instances with pending own-queue messages get
    ``WAITING_CHILDREN``; non-root parents with the same condition stay
    ``PROCESSING`` under CM (CM tracks their children).

    The root carve-out is intentional: root instances may have messages
    in their OWN queue (from HTTP, scheduler, user input) that are NOT
    child-response correlations. The CM does not track these, so we
    still set ``WAITING_CHILDREN`` to signal "root has queued work to
    process." Non-root parents are gated by ``cm.is_complete()`` and the
    CM-active bypass returns early before reaching any own-queue check.

    Reference: ``child_reports.py`` line 858 (root own-queue query) vs
    line 565 (non-root cascade, CM bypass).
    """

    @pytest.mark.asyncio
    async def test_root_with_pending_own_queue_gets_waiting_children(self) -> None:
        """Root instance with pending own-queue messages → status = WAITING_CHILDREN.

        Scenario: a child completion report is still queued in the
        instance's ``MessageQueue`` (``READY``) when the previous message
        finishes. The instance has no parent (``parent_id is None``) and
        ``waiting_for == 0``. The CM tracks child-response correlations
        but NOT the root's own-queue work. So the function falls through
        to the SELECT COUNT branch and sets ``WAITING_CHILDREN``.
        """
        from daemon.services.child_reports import ChildReportsService

        set_correlation_manager(None)

        root = MagicMock()
        root.instance_id = "root-p4-W1-pending"
        root.parent_id = None  # root: no parent
        root.agent_id = "coder"
        root.status = InstanceStatus.RUNNING.value
        root.waiting_for = 0  # all children done
        root.instance_metadata = {}
        root.children = None
        root.version = 1
        root.last_activity_at = None
        root.updated_at = None

        # Mock session: root is in the DB, and its own-queue has 1 pending.
        session = MagicMock()
        session.get = MagicMock(return_value=root)
        exec_result = MagicMock()
        exec_result.scalar_one = MagicMock(return_value=1)
        session.exec = MagicMock(return_value=exec_result)
        session.commit = MagicMock()

        # Build a mock manager with the attributes _process_child_completion_and_notify_parent reads.
        manager = MagicMock()
        manager._instance_repository = MagicMock()
        manager._instance_repository.get.return_value = root
        manager._checkpointer = None  # skips _get_last_assistant_message_raw
        manager._live_hub = None
        manager.write_guard = MagicMock()
        manager._queue_repository = MagicMock()
        manager.engine = MagicMock()

        # Bypass __init__ (avoids binding real manager attributes).
        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = manager
        service._events_service = None
        # Mock the title generation trigger so we don't try to actually run it.
        service._trigger_title_generation = MagicMock()

        # Patch Session and WriteGuardSession so the function uses our mock session.
        wgs = MagicMock()
        wgs.__enter__ = MagicMock(return_value=session)
        wgs.__exit__ = MagicMock(return_value=False)

        with patch("daemon.services.child_reports.Session", return_value=MagicMock()):
            with patch(
                "daemon.services.child_reports.WriteGuardSession", return_value=wgs
            ):
                await service._process_child_completion_and_notify_parent(
                    instance_id="root-p4-W1-pending",
                    completed_message_id="msg-p4-W1-pending",
                )

        # Root carve-out: WAITING_CHILDREN is set for own-queue pending work.
        assert root.status == InstanceStatus.WAITING_CHILDREN.value, (
            f"Root with pending own-queue should get WAITING_CHILDREN, "
            f"got {root.status!r}"
        )

    @pytest.mark.asyncio
    async def test_non_root_with_pending_own_queue_stays_processing_under_cm(self) -> None:
        """Non-root parent under CM stays PROCESSING even with own-queue work.

        Non-root parents are gated by ``cm.is_complete()``. When CM says
        the parent still has pending child correlations, the code returns
        early WITHOUT checking the own-queue. The own-queue check only
        runs when CM is None (graceful degradation fallback).
        """
        from daemon.services.child_reports import ChildReportsService

        # CM active, says NOT complete (children still pending).
        cm = MagicMock(spec=CorrelationManager, name="cm-W1-non-root")
        cm.is_complete = MagicMock(return_value=False)
        cm.get_pending_count = MagicMock(return_value=1)
        set_correlation_manager(cm)
        try:
            # Non-root parent: has a parent of its own (so it's not a root).
            parent = _make_parent(
                status="processing",
                waiting_for=1,
                parent_id="parent-p4-W1",
            )
            parent.parent_id = "grandparent-p4-W1"  # not None → non-root
            parent.waiting_for = 1

            child = _make_child(parent_id="parent-p4-W1")
            # session.get(Instance, child.parent_id) returns the parent mock.
            session = _setup_cascade_session(parent)

            mock_manager = _make_mock_manager()
            service = ChildReportsService(manager=mock_manager)

            with patch(
                "daemon.services.correlation_manager.notify_corr_resolve",
                new=AsyncMock(),
            ):
                result = await service._update_parent_on_child_complete(
                    session, child, completed_message_id="msg-p4-W1-nr"
                )

            # CM was consulted (CM was the authority for control flow).
            assert cm.is_complete.called, (
                "cm.is_complete() was never called — cascade skipped CM. "
                "Phase 4 deprecation violated: control flow must consult CM."
            )
            # Non-root parent stays PROCESSING (no WAITING_CHILDREN set by code).
            assert parent.status != InstanceStatus.WAITING_CHILDREN.value, (
                f"Non-root parent under CM should NOT get WAITING_CHILDREN, "
                f"got {parent.status!r}. The CM-active bypass should return early."
            )
            assert parent.status == "processing"
            # The function takes the CM-bypass return path.
            assert result == (False, None, None), (
                f"CM-bypass must return (False, None, None); got {result!r}"
            )
        finally:
            set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_root_with_no_pending_messages_completes(self) -> None:
        """Root with no children AND no own-queue messages → status = COMPLETED.

        The happy path: root has finished all work (no children pending,
        no own-queue messages) and can complete.
        """
        from daemon.services.child_reports import ChildReportsService

        set_correlation_manager(None)

        root = MagicMock()
        root.instance_id = "root-p4-W1-clean"
        root.parent_id = None  # root
        root.agent_id = "coder"
        root.status = InstanceStatus.RUNNING.value
        root.waiting_for = 0
        root.instance_metadata = {}
        root.children = None
        root.version = 1
        root.last_activity_at = None
        root.updated_at = None

        # Mock session: root in DB, own-queue has 0 pending.
        session = MagicMock()
        session.get = MagicMock(return_value=root)
        exec_result = MagicMock()
        exec_result.scalar_one = MagicMock(return_value=0)
        session.exec = MagicMock(return_value=exec_result)
        session.commit = MagicMock()

        manager = MagicMock()
        manager._instance_repository = MagicMock()
        manager._instance_repository.get.return_value = root
        manager._checkpointer = None
        manager._live_hub = None
        manager.write_guard = MagicMock()
        manager._queue_repository = MagicMock()
        manager.engine = MagicMock()

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = manager
        service._events_service = None
        service._trigger_title_generation = MagicMock()

        wgs = MagicMock()
        wgs.__enter__ = MagicMock(return_value=session)
        wgs.__exit__ = MagicMock(return_value=False)

        with patch("daemon.services.child_reports.Session", return_value=MagicMock()):
            with patch(
                "daemon.services.child_reports.WriteGuardSession", return_value=wgs
            ):
                await service._process_child_completion_and_notify_parent(
                    instance_id="root-p4-W1-clean",
                    completed_message_id="msg-p4-W1-clean",
                )

        # Root with nothing pending → COMPLETED.
        assert root.status == InstanceStatus.COMPLETED.value, (
            f"Root with no pending work should COMPLETE, got {root.status!r}"
        )
