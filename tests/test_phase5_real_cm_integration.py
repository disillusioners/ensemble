"""Phase 5 integration tests: real-CM integration with the ``waiting_for`` SQL column.

Phase 5 of the CorrelationManager (CM) migration verifies that the SQL
``waiting_for`` column and the CM's in-memory pending count stay in
sync through the full ``send_message`` -> child completion -> daemon
restart cycle.

Context (carried over from Phase 4):
  * ``waiting_for`` is REBUILD-ONLY: writes continue (increment at
    ``send_message``, decrement at child completion/error) so the
    column stays consistent as a rebuild cache, but it is NEVER read
    for cascade control flow.
  * Control-flow reads are replaced by ``cm.is_complete()`` and
    ``cm.get_pending_count()``.
  * ``rebuild_from_db()`` is the cold-start safety net that reconstructs
    the CM's in-memory ``_pending`` map from the SQL column and the
    message queue.

These tests exercise the REAL SQLite engine, the REAL
``SQLModelInstanceRepository`` / ``SQLModelMessageQueueRepository``, and
the REAL ``CorrelationManager``. No mocks are used for the SQL path.
The only SQL the tests issue directly are the ``waiting_for`` increment
/ decrement ``UPDATE`` statements that mirror the production side
effects of ``send_message`` and ``_update_parent_on_child_complete``.

Run with::

    pytest tests/test_phase5_real_cm_integration.py -v
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Importing the model classes registers them with SQLModel.metadata so
# `SQLModel.metadata.create_all` creates the corresponding tables.
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance, InstanceHierarchy
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.services.correlation_manager import (
    CorrelationManager,
    notify_corr_register,
    notify_corr_resolve,
    set_correlation_manager,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Shared fixtures — real in-memory SQLite (mirrors test_correlation_shadow.py)
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_cm_singleton():
    """Ensure each test starts and ends with the CM singleton cleared.

    The module-level ``_correlation_manager`` global in
    ``daemon.services.correlation_manager`` persists across tests;
    without this fixture, state leaks between tests.
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


@pytest.fixture
async def cm(
    instance_repo: SQLModelInstanceRepository,
    message_repo: SQLModelMessageQueueRepository,
) -> CorrelationManager:
    """Start a real ``CorrelationManager`` and register it as the singleton.

    The singleton wiring lets the production hook helpers
    (``notify_corr_register`` / ``notify_corr_resolve``) dispatch to
    this instance. The manager is stopped and the singleton cleared on
    teardown.
    """
    manager = CorrelationManager(
        instance_repository=instance_repo,
        message_queue_repository=message_repo,
    )
    await manager.start()
    set_correlation_manager(manager)
    try:
        yield manager
    finally:
        await manager.stop()
        set_correlation_manager(None)


# =============================================================================
# SQL + row helpers (copied verbatim from test_correlation_shadow.py and
# test_phase4_deprecation.py so the tests exercise the EXACT UPDATE that
# send_message and child_reports use in production)
# =============================================================================


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

    Uses a unique ``agent_dir`` per call so the PRIMARY KEY / NOT NULL
    constraints are satisfied without interfering between tests.
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


def _make_message(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str | None = None,
    status: str = MessageStatus.READY.value,
) -> MessageQueue:
    """Insert a ``MessageQueue`` row directly (no real enqueue flow).

    Used by the rebuild tests to seed the queue with EXPLICIT
    ``message_id`` values so the rebuilt CM correlation keys can be
    asserted.
    """
    with Session(engine) as session:
        row = MessageQueue(
            message_id=message_id or f"msg-{uuid.uuid4().hex[:8]}",
            instance_id=instance_id,
            content="phase5 test message",
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
# Test 1: Increment path — mirror the ``send_message`` SQL + CM hook
# =============================================================================


class TestIncrementPathRoundTrip:
    """``send_message`` writes the rebuild cache AND registers with the CM.

    Verifies that the two side effects (SQL UPDATE + CM hook) keep the
    SQL column and the CM in-memory state in agreement, and that no
    completion callback fires on a pure increment.
    """

    @pytest.mark.asyncio
    async def test_send_message_increments_db_and_registers_cm_pending(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        cm: CorrelationManager,
    ) -> None:
        """Mirror the ``send_message`` production code path.

        Production ``send_message`` issues two side effects atomically
        with enqueuing the child message:
          1. SQL ``UPDATE instances SET waiting_for = +1`` (rebuild cache)
          2. ``notify_corr_register(parent, child, message_id)`` (CM hook)

        This test verifies that after mirroring those two side effects:
          * DB ``waiting_for == 1``
          * ``cm.get_pending_count(parent) == 1``
          * ``cm.is_complete(parent)`` is False
          * The correlation key ``f"{child_id}:{msg_id}"`` is in
            ``cm._pending[parent]``
          * ``cm._pending[parent].had_error`` is False
          * No completion callback has fired yet
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=0)
        _make_instance(engine, child_id, parent_id=parent_id)

        # Inject a completion callback that records calls so we can
        # assert it has NOT fired after a pure increment.
        completion_calls: list[tuple[str, str]] = []

        async def on_complete(pid: str, terminal_status: str) -> None:
            completion_calls.append((pid, terminal_status))

        cm._completion_callback = on_complete

        msg_id = f"msg-{uuid.uuid4().hex[:8]}"

        # ── Step 1: send_message SQL side effect (rebuild cache write) ──
        new_val = _increment_waiting_for(engine, parent_id)
        assert new_val == 1, (
            f"Increment SQL must yield 1; got {new_val}. This UPDATE is "
            f"the exact statement send_message issues against waiting_for."
        )

        # ── Step 2: send_message CM hook (in-memory pending entry) ──
        await notify_corr_register(
            parent_id=parent_id,
            child_id=child_id,
            message_id=msg_id,
        )

        # ── Assertions: DB column and CM in-memory state agree ──
        assert _read_waiting_for(instance_repo, parent_id) == 1, (
            "DB waiting_for must be 1 after the send_message increment."
        )
        assert cm.get_pending_count(parent_id) == 1, (
            "CM pending count must be 1 after notify_corr_register."
        )
        assert cm.is_complete(parent_id) is False, (
            "CM must report NOT complete while one correlation is pending."
        )

        # The correlation key is the (child_id, message_id) pair.
        assert parent_id in cm._pending, "CM must track the parent after register."
        pc = cm._pending[parent_id]
        correlation_key = f"{child_id}:{msg_id}"
        assert correlation_key in pc.pending, (
            f"Correlation key {correlation_key!r} must be in the pending "
            f"set; got keys: {list(pc.pending.keys())}"
        )
        assert pc.had_error is False, (
            "A fresh register must NOT set had_error."
        )

        # No completion yet — the parent still has outstanding work.
        assert completion_calls == [], (
            "Completion callback must NOT fire on a pure increment "
            "(parent still has pending correlations)."
        )


# =============================================================================
# Test 2: Decrement path — mirror child completion SQL + CM hook
# =============================================================================


class TestDecrementPathRoundTrip:
    """Child completion decrements the rebuild cache AND resolves the CM entry.

    Verifies the SQL UPDATE and the CM hook together transition the
    parent from "has pending" to "complete", fire the completion
    callback exactly once, and clean up BOTH ``_pending`` and ``_locks``.
    """

    @pytest.mark.asyncio
    async def test_child_completion_decrements_db_and_fires_completion_callback(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        cm: CorrelationManager,
    ) -> None:
        """Mirror the child-completion production code path.

        Pre-condition (mirrors a PRIOR ``send_message``): parent already
        has ``waiting_for == 1`` (set by ``_make_instance``) and the CM
        already has one pending correlation registered.

        Production ``_update_parent_on_child_complete`` then issues:
          1. SQL ``UPDATE instances SET waiting_for = -1`` (clamped at 0)
          2. ``notify_corr_resolve(parent, child, message_id, "responded")``
             (CM hook from child_reports)

        This test verifies that after mirroring those two side effects:
          * DB ``waiting_for == 0``
          * ``cm.is_complete(parent)`` is True
          * ``cm.get_pending_count(parent) == 0``
          * ``parent_id not in cm._pending`` (entry cleaned up)
          * ``parent_id not in cm._locks`` (S3 fix: lock pruned)
          * The completion callback fired EXACTLY ONCE with
            ``terminal_status == "completed"``
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        # Pre-populate DB: waiting_for=1 represents a prior send_message.
        _make_instance(engine, parent_id, waiting_for=1)
        _make_instance(engine, child_id, parent_id=parent_id)

        # Inject a completion callback that records (parent_id, terminal_status).
        completion_calls: list[tuple[str, str]] = []

        async def on_complete(pid: str, terminal_status: str) -> None:
            completion_calls.append((pid, terminal_status))

        cm._completion_callback = on_complete

        msg_id = f"msg-{uuid.uuid4().hex[:8]}"
        # Mirror the PRIOR send_message's CM hook so the pending entry
        # exists before we resolve it. (DB is already at 1 from the
        # pre-population, so we do NOT increment again — that would
        # desync the two sides.)
        await notify_corr_register(
            parent_id=parent_id,
            child_id=child_id,
            message_id=msg_id,
        )
        # Sanity: both sides agree at 1 before the decrement.
        assert cm.get_pending_count(parent_id) == 1
        assert _read_waiting_for(instance_repo, parent_id) == 1

        # ── Step 1: child_reports SQL side effect (rebuild cache write) ──
        new_val = _decrement_waiting_for(engine, parent_id)
        assert new_val == 0, (
            f"Decrement SQL must yield 0 (clamped); got {new_val}. This "
            f"UPDATE is the exact statement _update_parent_on_child_complete "
            f"issues against waiting_for."
        )

        # ── Step 2: child_reports CM hook (in-memory resolve) ──
        await notify_corr_resolve(
            parent_id=parent_id,
            child_id=child_id,
            message_id=msg_id,
            status="responded",
        )

        # ── Assertions: DB column and CM in-memory state agree ──
        assert _read_waiting_for(instance_repo, parent_id) == 0, (
            "DB waiting_for must be 0 after the child completion decrement."
        )
        assert cm.is_complete(parent_id) is True, (
            "CM must report complete after the last correlation resolves."
        )
        assert cm.get_pending_count(parent_id) == 0, (
            "CM pending count must be 0 after resolution."
        )
        assert parent_id not in cm._pending, (
            "CM must drop the _pending entry on completion "
            "(no unbounded growth)."
        )
        # S3 fix: the per-parent lock MUST also be pruned on completion.
        assert parent_id not in cm._locks, (
            f"Phase 4 / S3 violation: _locks still contains "
            f"{parent_id[:8]}... after completion. The lock must be "
            f"popped alongside _pending to prevent unbounded growth."
        )
        # Callback fired EXACTLY ONCE with terminal_status="completed".
        assert completion_calls == [(parent_id, "completed")], (
            f"Completion callback must fire once with terminal_status="
            f"'completed'; got {completion_calls!r}"
        )


# =============================================================================
# Test 3: Rebuild-after-restart — cold-start reconstruction from the SQL column
# =============================================================================


class TestRebuildAfterRestart:
    """Cold-start rebuild: CM reconstructs in-memory state from the DB.

    These are the critical crash-recovery tests. If the daemon is
    restarted while a parent still has pending children, the next CM
    must reconstruct its ``_pending`` map from the persisted SQL state
    (``waiting_for > 0`` parents + their children's pending messages).
    """

    @pytest.mark.asyncio
    async def test_rebuild_reconstructs_cm_state_from_persisted_db(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """``rebuild_from_db()`` (triggered by ``start()``) must reconstruct
        the CM's in-memory ``_pending`` map from the persisted SQL state.

        Setup: parent with ``waiting_for=2`` and two ``MessageQueue``
        rows for the child (one READY, one PROCESSING), each with an
        EXPLICIT ``message_id`` so we can assert the rebuilt correlation
        keys use the REAL message IDs (proving the C1 fix: no
        placeholder keys).

        A FRESH CM is created (NOT the ``cm`` fixture) so we exercise
        the true cold-start path — the CM has no in-memory state before
        ``start()`` runs ``rebuild_from_db()``.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=2)
        _make_instance(engine, child_id, parent_id=parent_id)

        # Two pending messages with EXPLICIT, known message_ids.
        msg_id_1 = f"msg-{uuid.uuid4().hex[:8]}"
        msg_id_2 = f"msg-{uuid.uuid4().hex[:8]}"
        _make_message(
            engine,
            instance_id=child_id,
            message_id=msg_id_1,
            status=MessageStatus.READY.value,
        )
        _make_message(
            engine,
            instance_id=child_id,
            message_id=msg_id_2,
            status=MessageStatus.PROCESSING.value,
        )

        # Fresh CM — no prior in-memory state. start() triggers rebuild.
        fresh_cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
        )
        await fresh_cm.start()
        try:
            # Parent must be tracked after rebuild.
            assert parent_id in fresh_cm._pending, (
                f"rebuild_from_db() did NOT track parent {parent_id[:8]}... "
                f"with waiting_for=2. _pending keys: "
                f"{[k[:8] for k in fresh_cm._pending.keys()]}"
            )
            # Pending count must match DB waiting_for.
            assert fresh_cm.get_pending_count(parent_id) == 2, (
                f"Rebuilt pending count must be 2 (matches waiting_for=2); "
                f"got {fresh_cm.get_pending_count(parent_id)}"
            )
            # Both REAL correlation keys must be present — NOT placeholders.
            # This proves the C1 fix: rebuild uses
            # get_pending_for_instances which returns real
            # (child_id, message_id) tuples.
            pending_keys = set(fresh_cm._pending[parent_id].pending.keys())
            expected_key_1 = f"{child_id}:{msg_id_1}"
            expected_key_2 = f"{child_id}:{msg_id_2}"
            assert expected_key_1 in pending_keys, (
                f"Rebuilt pending must include real correlation key "
                f"{expected_key_1!r}; got keys: {sorted(pending_keys)}"
            )
            assert expected_key_2 in pending_keys, (
                f"Rebuilt pending must include real correlation key "
                f"{expected_key_2!r}; got keys: {sorted(pending_keys)}"
            )
        finally:
            await fresh_cm.stop()

    @pytest.mark.asyncio
    async def test_rebuild_simulates_daemon_restart_cycle(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """Full daemon-restart cycle: CM #1 dies, CM #2 rebuilds from scratch.

        Steps:
          1. Pre-populate DB (parent waiting_for=2, child, 2 pending msgs).
          2. Create CM #1, start it (rebuild runs) — verify state.
          3. Stop CM #1, clear singleton, DISCARD the in-memory CM object
             (simulates daemon death — all in-memory state is gone).
          4. Create CM #2, start it (rebuild runs AGAIN from scratch).
          5. Assert CM #2 also reconstructs the parent with 2 pending
             correlations and the same real correlation keys.

        This proves ``rebuild_from_db`` is idempotent across restarts
        and does NOT depend on any in-memory state from the previous CM.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=2)
        _make_instance(engine, child_id, parent_id=parent_id)

        msg_id_1 = f"msg-{uuid.uuid4().hex[:8]}"
        msg_id_2 = f"msg-{uuid.uuid4().hex[:8]}"
        _make_message(
            engine,
            instance_id=child_id,
            message_id=msg_id_1,
            status=MessageStatus.READY.value,
        )
        _make_message(
            engine,
            instance_id=child_id,
            message_id=msg_id_2,
            status=MessageStatus.PROCESSING.value,
        )

        expected_key_1 = f"{child_id}:{msg_id_1}"
        expected_key_2 = f"{child_id}:{msg_id_2}"

        # ── CM #1: first start after the DB was populated ──
        cm1 = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
        )
        await cm1.start()
        try:
            assert cm1.get_pending_count(parent_id) == 2, (
                "CM #1 must rebuild 2 pending correlations from the DB."
            )
            assert expected_key_1 in cm1._pending[parent_id].pending
            assert expected_key_2 in cm1._pending[parent_id].pending
        finally:
            await cm1.stop()
        # Clear singleton + discard the object — simulate daemon death.
        set_correlation_manager(None)
        del cm1

        # ── CM #2: cold restart — rebuild runs again from scratch ──
        cm2 = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
        )
        await cm2.start()
        try:
            assert parent_id in cm2._pending, (
                f"CM #2 must re-track parent {parent_id[:8]}... after "
                f"the simulated restart. _pending keys: "
                f"{[k[:8] for k in cm2._pending.keys()]}"
            )
            assert cm2.get_pending_count(parent_id) == 2, (
                f"CM #2 must rebuild 2 pending correlations (same as CM #1); "
                f"got {cm2.get_pending_count(parent_id)}"
            )
            pending_keys = set(cm2._pending[parent_id].pending.keys())
            assert expected_key_1 in pending_keys, (
                f"CM #2 must rebuild the real correlation key "
                f"{expected_key_1!r}; got keys: {sorted(pending_keys)}"
            )
            assert expected_key_2 in pending_keys, (
                f"CM #2 must rebuild the real correlation key "
                f"{expected_key_2!r}; got keys: {sorted(pending_keys)}"
            )
        finally:
            await cm2.stop()


# =============================================================================
# Test 4: Multiple messages round-trip — partial then full resolution
# =============================================================================


class TestMultiMessageRoundTrip:
    """Multi-message round-trip: partial resolution keeps state, final resolves all.

    Verifies that the CM keeps accurate pending state through partial
    resolutions and ONLY fires the completion callback when the LAST
    correlation resolves. The DB ``waiting_for`` and CM pending count
    must stay in lockstep through every step.
    """

    @pytest.mark.asyncio
    async def test_partial_resolution_keeps_state_then_full_completion_fires_callback(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        cm: CorrelationManager,
    ) -> None:
        """Three messages to three children: resolve 2, then resolve the last.

        Production mirror: each ``send_message`` increments waiting_for
        and calls ``notify_corr_register``; each child completion
        decrements waiting_for and calls ``notify_corr_resolve``.

        Assertions:
          * After 3 registers: DB waiting_for=3, CM pending=3, no callback.
          * After 2 resolves:  DB waiting_for=1, CM pending=1, no callback.
          * After the last:    DB waiting_for=0, CM pending=0, complete,
            _pending + _locks cleaned up, callback fired once with
            terminal_status="completed".
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=0)
        # Three distinct children.
        child_a = f"child-a-{uuid.uuid4().hex[:8]}"
        child_b = f"child-b-{uuid.uuid4().hex[:8]}"
        child_c = f"child-c-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, child_a, parent_id=parent_id)
        _make_instance(engine, child_b, parent_id=parent_id)
        _make_instance(engine, child_c, parent_id=parent_id)

        completion_calls: list[tuple[str, str]] = []

        async def on_complete(pid: str, terminal_status: str) -> None:
            completion_calls.append((pid, terminal_status))

        cm._completion_callback = on_complete

        msg_a = f"msg-a-{uuid.uuid4().hex[:8]}"
        msg_b = f"msg-b-{uuid.uuid4().hex[:8]}"
        msg_c = f"msg-c-{uuid.uuid4().hex[:8]}"

        # ── Step 1: register 3 messages (send_message x3) ──
        for _child_id, _msg_id in (
            (child_a, msg_a),
            (child_b, msg_b),
            (child_c, msg_c),
        ):
            _increment_waiting_for(engine, parent_id)
            await notify_corr_register(
                parent_id=parent_id,
                child_id=_child_id,
                message_id=_msg_id,
            )

        assert _read_waiting_for(instance_repo, parent_id) == 3, (
            "DB waiting_for must be 3 after three send_message increments."
        )
        assert cm.get_pending_count(parent_id) == 3, (
            "CM pending count must be 3 after three registers."
        )
        assert completion_calls == [], (
            "No completion callback must fire while correlations are pending."
        )

        # ── Step 2: resolve 2 of 3 (child completion x2) ──
        for _child_id, _msg_id in (
            (child_a, msg_a),
            (child_b, msg_b),
        ):
            _decrement_waiting_for(engine, parent_id)
            await notify_corr_resolve(
                parent_id=parent_id,
                child_id=_child_id,
                message_id=_msg_id,
                status="responded",
            )

        assert _read_waiting_for(instance_repo, parent_id) == 1, (
            "DB waiting_for must be 1 after two decrements."
        )
        assert cm.get_pending_count(parent_id) == 1, (
            "CM pending count must be 1 after two resolves."
        )
        assert completion_calls == [], (
            "Completion must NOT fire while one correlation is still pending."
        )

        # ── Step 3: resolve the LAST one ──
        _decrement_waiting_for(engine, parent_id)
        await notify_corr_resolve(
            parent_id=parent_id,
            child_id=child_c,
            message_id=msg_c,
            status="responded",
        )

        assert _read_waiting_for(instance_repo, parent_id) == 0, (
            "DB waiting_for must be 0 after the final decrement."
        )
        assert cm.get_pending_count(parent_id) == 0, (
            "CM pending count must be 0 after the final resolve."
        )
        assert cm.is_complete(parent_id) is True, (
            "CM must report complete after the last correlation resolves."
        )
        assert parent_id not in cm._pending, (
            "CM must drop the _pending entry on completion."
        )
        assert parent_id not in cm._locks, (
            f"Phase 4 / S3 violation: _locks still contains "
            f"{parent_id[:8]}... after the final completion."
        )
        assert completion_calls == [(parent_id, "completed")], (
            f"Completion callback must fire exactly once with "
            f"terminal_status='completed'; got {completion_calls!r}"
        )