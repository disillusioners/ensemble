"""A15 — In-flight crash-recovery tests (PostgreSQL).

Crash-recovery tests that verify ``CorrelationManager.rebuild_from_db()``
correctly reconstructs the ``_pending`` state when the daemon restarts
with mid-flight parents.

Scenario
--------
A parent instance has spawned children that are still running. The daemon
restarts (crash or deploy). The ``CorrelationManager`` is the SOLE
completion authority; the DB column ``waiting_for`` is the **rebuild
cache** (per ADR-011): on startup, ``rebuild_from_db()`` uses it as the
seed query to find parents that need state reconstruction.

This test pack verifies:

  1. Restart with mid-flight parents → both correlations restored.
  2. Restart then resolve → pending count decreases, completion fires.
  3. Restart with mixed state (some completed, some pending) → only
     pending entries are in ``_pending``.
  4. Concurrent ``register_message_send`` during rebuild is preserved
     (A0a MERGE semantics — top-level clear + per-parent MERGE).
  5. Restart with various wait-count scenarios → ``rebuild_from_db()``
     correctly reconstructs CM state from the ``waiting_for`` rebuild
     cache.

These tests use the **real PostgreSQL engine** (not mocks) so the actual
``get_all_with_waiting_for()``, ``get_children()``, and
``get_pending_for_instances()`` queries are exercised — including the
JOIN logic, JSONB / VARCHAR column reads, and PG-specific SQL. The
side-effect deps (EventBus, completion callback, completion registry)
are mocked.

Run with::

    uv run pytest tests/postgres/test_inflight_flag_flip.py -v \\
        --override-ini="addopts=" -m postgres

Notes
-----
* Uses module-scoped PG engine + autouse TRUNCATE for isolation.
* ``rebuild_from_db()`` always reads ``waiting_for`` as the rebuild
  cache (regardless of any feature flag state, since Phase 3 removed
  the ``USE_LEGACY_WAITING_FOR_CASCADE`` flag entirely).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

# Register SQLModel table classes before SQLModel.metadata.create_all.
from daemon.repositories.instance.models import Instance, InstanceStatus  # noqa: F401
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.message_queue.repository import SQLModelMessageQueueRepository
import pytest

pytestmark = pytest.mark.skip(reason="Phase 5: CorrelationManager removed; tests CM crash recovery")

# CM-era imports removed in Phase 5 (CorrelationManager → DependencyBus).
# Tests in this module are skipped via ``pytestmark`` above.

logger = logging.getLogger(__name__)


# =============================================================================
# Engine + fixtures
# =============================================================================


def _pg_engine() -> Engine:
    """Create a PostgreSQL engine pointing at the test database.

    Inherits the same env-var overrides as tests/postgres/conftest.py.
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
    """Module-scoped PG engine — create_all on setup.

    The session-scoped ``pg_engine`` in ``tests/postgres/conftest.py``
    owns the schema lifecycle and runs ``drop_all`` at session teardown,
    so this fixture deliberately avoids ``drop_all`` here. A per-module
    ``drop_all`` would wipe tables out from under the session-scoped
    autouse ``_pg_truncate_tables`` fixture in sibling test files,
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
    return SQLModelInstanceRepository(pg_engine)


@pytest.fixture
def pg_message_repo(pg_engine: Engine) -> SQLModelMessageQueueRepository:
    return SQLModelMessageQueueRepository(pg_engine)


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


def _make_message(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str | None = None,
    status: str = MessageStatus.READY.value,
    content: str = "hello",
    msg_type: str = MessageType.AGENT.value,
) -> MessageQueue:
    """Insert a MessageQueue row into the test DB."""
    msg = MessageQueue(
        message_id=message_id or str(uuid.uuid4()),
        instance_id=instance_id,
        content=content,
        type=msg_type,
        status=status,
    )
    with Session(engine) as session:
        session.add(msg)
        session.commit()
        session.refresh(msg)
    return msg


def _read_waiting_for(engine: Engine, instance_id: str) -> int:
    """Read current ``waiting_for`` value from the DB."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT waiting_for FROM instances WHERE instance_id = :pid"),
            {"pid": instance_id},
        ).first()
        return int(row[0]) if row and row[0] is not None else 0


def _count_pending_messages(engine: Engine, instance_id: str) -> int:
    """Count READY/PROCESSING/RETRYING messages for an instance (mirrors
    ``rebuild_from_db``'s view via ``get_pending_for_instances``)."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM message_queue "
                "WHERE instance_id = :iid "
                "AND status IN ('ready', 'processing', 'retrying')"
            ),
            {"iid": instance_id},
        ).first()
        return int(row[0]) if row else 0


# =============================================================================
# Test 1 — Restart with mid-flight parents
# =============================================================================


class TestRestartWithMidFlightParents:
    """``rebuild_from_db()`` reconstructs both correlations for a parent
    that has two children with pending messages."""

    @pytest.mark.asyncio
    async def test_rebuild_restores_two_pending_correlations(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """Parent spawned 2 children, both with READY messages. After restart
        (simulated by a fresh CM + ``rebuild_from_db()``), both correlations
        must be present in ``_pending``.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_a = f"child-a-{uuid.uuid4().hex[:8]}"
        child_b = f"child-b-{uuid.uuid4().hex[:8]}"
        msg_a = f"msg-a-{uuid.uuid4().hex[:8]}"
        msg_b = f"msg-b-{uuid.uuid4().hex[:8]}"

        # Pre-crash DB state: parent has waiting_for=2 (rebuild cache).
        _make_instance(pg_engine, parent_id, waiting_for=2)
        _make_instance(pg_engine, child_a, parent_id=parent_id)
        _make_instance(pg_engine, child_b, parent_id=parent_id)
        _make_message(pg_engine, instance_id=child_a, message_id=msg_a)
        _make_message(pg_engine, instance_id=child_b, message_id=msg_b)

        # Daemon restart: fresh CM with NO in-memory state.
        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=None,
        )
        await cm.start()

        try:
            # Both correlations must be reconstructed from DB.
            assert parent_id in cm._pending, (
                f"A15 MISSING: parent not in _pending after rebuild — "
                f"rebuild_from_db() did not reconstruct mid-flight state. "
                f"tracked={list(cm._pending.keys())}"
            )
            assert cm.get_pending_count(parent_id) == 2, (
                f"A15 MISSING: expected 2 pending correlations, got "
                f"{cm.get_pending_count(parent_id)}"
            )

            # Both correlation_keys (f"{child_id}:{message_id}") must be present.
            parent_state = cm._pending[parent_id]
            assert f"{child_a}:{msg_a}" in parent_state.pending, (
                f"A15 MISSING: correlation key for child_a not rebuilt. "
                f"keys={list(parent_state.pending.keys())}"
            )
            assert f"{child_b}:{msg_b}" in parent_state.pending, (
                f"A15 MISSING: correlation key for child_b not rebuilt. "
                f"keys={list(parent_state.pending.keys())}"
            )
            assert cm.is_complete(parent_id) is False
        finally:
            await cm.stop()


# =============================================================================
# Test 2 — Restart then resolve
# =============================================================================


class TestRestartThenResolve:
    """After restart, resolving children one at a time must decrease the
    pending count and fire the completion callback when the last one
    resolves."""

    @pytest.mark.asyncio
    async def test_rebuild_then_resolve_each_decrements_and_fires_callback(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """Restart with 2 pending correlations. Resolve one: count → 1.
        Resolve the other: count → 0 and completion_callback fires once.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_a = f"child-a-{uuid.uuid4().hex[:8]}"
        child_b = f"child-b-{uuid.uuid4().hex[:8]}"
        msg_a = f"msg-a-{uuid.uuid4().hex[:8]}"
        msg_b = f"msg-b-{uuid.uuid4().hex[:8]}"

        _make_instance(pg_engine, parent_id, waiting_for=2)
        _make_instance(pg_engine, child_a, parent_id=parent_id)
        _make_instance(pg_engine, child_b, parent_id=parent_id)
        _make_message(pg_engine, instance_id=child_a, message_id=msg_a)
        _make_message(pg_engine, instance_id=child_b, message_id=msg_b)

        callback = AsyncMock(name="completion_callback")
        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=callback,
        )
        await cm.start()

        try:
            # Sanity: 2 pending after rebuild.
            assert cm.get_pending_count(parent_id) == 2
            assert callback.await_count == 0

            # Resolve the first child via the rebuilt correlation key.
            await cm.resolve_response(parent_id, child_a, msg_a)
            assert cm.get_pending_count(parent_id) == 1, (
                f"Pending count should be 1 after first resolve, "
                f"got {cm.get_pending_count(parent_id)}"
            )
            assert callback.await_count == 0, (
                "Callback fired prematurely — should only fire when ALL "
                "correlations are resolved."
            )

            # Resolve the second child.
            await cm.resolve_response(parent_id, child_b, msg_b)
            assert cm.get_pending_count(parent_id) == 0
            # Parent slot cleaned up after completion.
            assert parent_id not in cm._pending, (
                f"Parent should be removed from _pending after completion, "
                f"but still present. _pending={list(cm._pending.keys())}"
            )

            # Callback fired exactly once with terminal_status="completed".
            assert callback.await_count == 1, (
                f"Callback should have fired exactly once, "
                f"got {callback.await_count}"
            )
            callback.assert_awaited_once_with(parent_id, "completed")
        finally:
            await cm.stop()


# =============================================================================
# Test 3 — Restart with mixed state (some completed, some pending)
# =============================================================================


class TestRestartWithMixedState:
    """Restart with a parent that has 3 children: 1 message COMPLETED,
    2 still READY. Only the 2 pending should appear in ``_pending``."""

    @pytest.mark.asyncio
    async def test_rebuild_skips_completed_messages(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """Parent has 3 children; child_a message is COMPLETED, child_b
        and child_c messages are READY. After rebuild: only the 2 READY
        correlations are in ``_pending``. The COMPLETED one is excluded
        because ``get_pending_for_instances`` filters on status IN
        (READY, PROCESSING, RETRYING).
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_a = f"child-a-{uuid.uuid4().hex[:8]}"
        child_b = f"child-b-{uuid.uuid4().hex[:8]}"
        child_c = f"child-c-{uuid.uuid4().hex[:8]}"
        msg_done = f"msg-done-{uuid.uuid4().hex[:8]}"
        msg_b = f"msg-b-{uuid.uuid4().hex[:8]}"
        msg_c = f"msg-c-{uuid.uuid4().hex[:8]}"

        # Pre-crash DB: 3 children, but only 2 still pending in DB.
        # waiting_for=2 reflects actual in-flight (completed one already
        # decremented; see test_premature_completion_regression for the
        # exact pattern that produces this state).
        _make_instance(pg_engine, parent_id, waiting_for=2)
        _make_instance(pg_engine, child_a, parent_id=parent_id)
        _make_instance(pg_engine, child_b, parent_id=parent_id)
        _make_instance(pg_engine, child_c, parent_id=parent_id)
        # Completed message: NOT in the rebuild set.
        _make_message(
            pg_engine,
            instance_id=child_a,
            message_id=msg_done,
            status=MessageStatus.COMPLETED.value,
        )
        # Still-pending messages: included.
        _make_message(
            pg_engine,
            instance_id=child_b,
            message_id=msg_b,
            status=MessageStatus.READY.value,
        )
        _make_message(
            pg_engine,
            instance_id=child_c,
            message_id=msg_c,
            status=MessageStatus.PROCESSING.value,
        )

        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=None,
        )
        await cm.start()

        try:
            # The COMPLETED message must NOT be in _pending (rebuild filter).
            assert cm.get_pending_count(parent_id) == 2, (
                f"A15 MISSING: expected 2 pending correlations (READY + "
                f"PROCESSING), got {cm.get_pending_count(parent_id)}. "
                f"COMPLETED messages must be filtered out by "
                f"get_pending_for_instances()."
            )

            parent_state = cm._pending[parent_id]
            assert f"{child_a}:{msg_done}" not in parent_state.pending, (
                "A15 MISSING: COMPLETED message must NOT be in rebuilt "
                "_pending (rebuild filter excludes COMPLETED/FAILED)."
            )
            assert f"{child_b}:{msg_b}" in parent_state.pending
            assert f"{child_c}:{msg_c}" in parent_state.pending

            # The PROCESSING message is included (still active).
            assert (
                _count_pending_messages(pg_engine, child_b)
                + _count_pending_messages(pg_engine, child_c)
                == 2
            )
        finally:
            await cm.stop()


# =============================================================================
# Test 4 — Concurrent register during rebuild (A0a MERGE fix)
# =============================================================================


class TestConcurrentRegisterDuringRebuild:
    """A ``register_message_send`` arriving while ``rebuild_from_db()`` is
    running must NOT be lost. The A0a MERGE semantics preserve it:

      - Top-level clear (``self._pending = {}``) wipes stale entries.
      - Per-parent rebuild write MERGES (does not replace) the existing
        ``ParentCorrelation.pending`` dict, so a concurrent register's
        entry survives the rebuild.

    This test simulates a slow ``get_all_with_waiting_for`` query so the
    concurrent register has a chance to land between the top-level clear
    and the per-parent rebuild write.
    """

    @pytest.mark.asyncio
    async def test_register_during_rebuild_is_preserved(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """Concurrent register survives rebuild → final count = DB + concurrent."""
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_db = f"child-db-{uuid.uuid4().hex[:8]}"
        child_concurrent = f"child-concurrent-{uuid.uuid4().hex[:8]}"
        msg_db = f"msg-db-{uuid.uuid4().hex[:8]}"
        msg_concurrent = f"msg-concurrent-{uuid.uuid4().hex[:8]}"

        # Pre-crash DB: parent waiting_for=1, 1 child with 1 pending message.
        _make_instance(pg_engine, parent_id, waiting_for=1)
        _make_instance(pg_engine, child_db, parent_id=parent_id)
        _make_message(
            pg_engine, instance_id=child_db, message_id=msg_db,
            status=MessageStatus.READY.value,
        )

        # Make the parent query slow so the concurrent register has a
        # chance to land between the top-level clear and the per-parent
        # rebuild write.
        original_get_all = pg_instance_repo.get_all_with_waiting_for
        call_count = {"n": 0}

        def slow_get_all_with_waiting_for():
            call_count["n"] += 1
            # Sleep only on the first call (during rebuild). Subsequent
            # calls (if any) pass through.
            import time as _t
            if call_count["n"] == 1:
                _t.sleep(0.2)
            return original_get_all()

        with patch.object(
            pg_instance_repo,
            "get_all_with_waiting_for",
            side_effect=slow_get_all_with_waiting_for,
        ):
            cm = CorrelationManager(
                instance_repository=pg_instance_repo,
                message_queue_repository=pg_message_repo,
                completion_callback=None,
            )
            await cm.start()

            # While the rebuild's get_all_with_waiting_for is sleeping,
            # a concurrent register_message_send lands. By the time
            # rebuild reads `_pending[parent_id]` in the per-parent
            # write block, the slot is already populated with the
            # concurrent entry — the MERGE semantics preserve it.
            concurrent_register_task = asyncio.create_task(
                cm.register_message_send(parent_id, child_concurrent, msg_concurrent)
            )
            await concurrent_register_task

            try:
                # The DB-backed entry must be present.
                parent_state = cm._pending[parent_id]
                assert f"{child_db}:{msg_db}" in parent_state.pending, (
                    f"A0a MISSING: DB-backed correlation key "
                    f"{child_db}:{msg_db} not rebuilt. keys="
                    f"{list(parent_state.pending.keys())}"
                )
                # The concurrent entry must ALSO be present (MERGE preserved it).
                assert f"{child_concurrent}:{msg_concurrent}" in parent_state.pending, (
                    f"A0a MISSING: concurrent register_message_send was "
                    f"lost during rebuild. The MERGE semantics should have "
                    f"preserved it. keys={list(parent_state.pending.keys())}"
                )
                # Total: 1 (DB) + 1 (concurrent) = 2.
                assert cm.get_pending_count(parent_id) == 2, (
                    f"A0a MISSING: expected 2 pending (1 DB + 1 concurrent), "
                    f"got {cm.get_pending_count(parent_id)}"
                )
                assert cm.is_complete(parent_id) is False
            finally:
                await cm.stop()


# =============================================================================
# Test 5 — Flag-flip mid-flight (rebuild reads waiting_for cache)
# =============================================================================


class TestFlagFlipMidFlightRebuild:
    """The "in-flight during migration" scenario:

      * Historically, ``send_message`` wrote to ``waiting_for`` (this is
        the pre-Phase-3 behavior — see git history).
      * Phase 3 removed the ``USE_LEGACY_WAITING_FOR_CASCADE`` flag
        entirely. The daemon's production read path now goes through
        the CM (``cm.get_pending_count``), not the SQL counter. The
        column is RETAINED as the rebuild cache per ADR-011.
      * ``rebuild_from_db()`` must read ``waiting_for > 0`` parents and
        reconstruct CM state correctly.

    The point: ``waiting_for`` written under the old architecture is
    sufficient for rebuild to recover state under the new architecture.
    """

    @pytest.mark.asyncio
    async def test_rebuild_reads_waiting_for_cache_after_flag_flip(
        self,
        pg_engine: Engine,
        pg_instance_repo: SQLModelInstanceRepository,
        pg_message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        """Pre-flip state: parent with 3 in-flight children, waiting_for=3.

        Simulates a daemon restart AFTER the flag has flipped from ON
        to OFF. The CM is empty. ``rebuild_from_db()`` must reconstruct
        the 3 pending correlations from ``waiting_for=3`` and the
        READY messages.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        children = [f"child-{i}-{uuid.uuid4().hex[:8]}" for i in range(3)]
        msgs = [f"msg-{i}-{uuid.uuid4().hex[:8]}" for i in range(3)]

        # Pre-flip DB state — written by legacy path. waiting_for=3
        # reflects 3 in-flight children.
        _make_instance(pg_engine, parent_id, waiting_for=3)
        for cid in children:
            _make_instance(pg_engine, cid, parent_id=parent_id)
        for cid, mid in zip(children, msgs):
            _make_message(
                pg_engine,
                instance_id=cid,
                message_id=mid,
                status=MessageStatus.READY.value,
            )

        # Verify precondition: DB cache matches the in-flight count.
        assert _read_waiting_for(pg_engine, parent_id) == 3
        total_pending = sum(_count_pending_messages(pg_engine, c) for c in children)
        assert total_pending == 3, (
            f"Pre-flip DB state should have 3 pending messages, "
            f"got {total_pending}"
        )

        # Post-flip daemon restart: fresh CM. completion_callback is
        # AsyncMock so we can observe the rebuild triggering it (it
        # won't here, since all 3 are still pending — but it's there
        # for symmetry with the production wiring).
        callback = AsyncMock(name="completion_callback")
        cm = CorrelationManager(
            instance_repository=pg_instance_repo,
            message_queue_repository=pg_message_repo,
            completion_callback=callback,
        )
        await cm.start()

        try:
            # Rebuild must read the waiting_for cache and find the parent.
            assert parent_id in cm._pending, (
                f"A15 MISSING: parent not in _pending after rebuild from "
                f"waiting_for cache. _pending={list(cm._pending.keys())}"
            )

            # All 3 correlations must be reconstructed.
            assert cm.get_pending_count(parent_id) == 3, (
                f"A15 MISSING: expected 3 pending correlations from "
                f"waiting_for cache, got {cm.get_pending_count(parent_id)}. "
                f"rebuild_from_db() must read waiting_for as rebuild cache."
            )
            assert cm.is_complete(parent_id) is False

            parent_state = cm._pending[parent_id]
            for cid, mid in zip(children, msgs):
                assert f"{cid}:{mid}" in parent_state.pending, (
                    f"A15 MISSING: correlation key {cid}:{mid} not rebuilt "
                    f"from waiting_for cache. keys="
                    f"{list(parent_state.pending.keys())}"
                )

            # callback must NOT have fired (3 still pending).
            callback.assert_not_called()

            # Now resolve all 3 — rebuild must have produced a state
            # that the production resolve_response can complete cleanly.
            for cid, mid in zip(children, msgs):
                await cm.resolve_response(parent_id, cid, mid)

            # All resolved → callback fires once.
            assert parent_id not in cm._pending, (
                "Parent should be cleared after all correlations resolved "
                "post-rebuild."
            )
            callback.assert_awaited_once_with(parent_id, "completed")
        finally:
            await cm.stop()
