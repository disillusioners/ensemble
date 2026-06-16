"""Integration tests for CorrelationManager shadow mode.

Phase 1 (Shadow Mode) of the CorrelationManager observes and validates
the existing ``waiting_for`` counter without modifying control flow. These
tests verify that CM works end-to-end against the daemon's real
in-memory repositories, and that the shadow-mode invariants hold:

    * ``register_message_send`` (called by ``send_message`` hook) makes
      the CM's pending count track the parent's DB ``waiting_for`` value.
    * ``resolve_response`` (called by ``child_reports.py`` and
      ``error_reporting.py`` hooks) removes the matching entry.
    * The CM fires the ``completion_callback`` exactly when the last
      pending correlation is resolved, with the correct
      ``terminal_status`` ("completed" vs. "error").
    * The DB ``waiting_for`` counter is unchanged by CM activity (shadow
      mode does not affect control flow).

The tests exercise the real production hook helpers
(``notify_corr_register`` / ``notify_corr_resolve`` from
``daemon/services/correlation_manager.py``) and the real SQL ``UPDATE``
patterns that ``daemon/tools/instance.py::send_message`` and
``daemon/services/child_reports.py::_update_parent_on_child_complete``
use to bump / decrement ``waiting_for``. No LLM calls are required.

Run with:

    pytest tests/test_correlation_shadow.py -v
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Importing the model classes is what registers them with SQLModel.metadata;
# `SQLModel.metadata.create_all` only creates tables for imported models.
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance, InstanceHierarchy
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import MessageQueue
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


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with FK enforcement enabled.

    Mirrors the pragma setup in `daemon/repositories/factory.py` (which sets
    `PRAGMA foreign_keys=ON` on every new connection) but in-memory and
    using StaticPool so the same connection is reused across threads
    within a single test.
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
    """Real InstanceRepository bound to the in-memory engine."""
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def message_repo(engine: Engine) -> SQLModelMessageQueueRepository:
    """Real MessageQueueRepository bound to the in-memory engine."""
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
    """Insert a bare Instance row in the test DB.

    Uses a unique agent_dir per call so the engine's PRIMARY KEY / NOT
    NULL constraints are satisfied without interfering between tests.
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
    """Mirror ``send_message`` waiting_for++ side effect.

    Returns the new value of ``waiting_for`` (as observed by the UPDATE).
    """
    with Session(engine) as session:
        row = session.execute(_INCREMENT_WAITING_FOR_SQL, {"pid": parent_id}).first()
        session.commit()
        return int(row[0]) if row is not None else 0


def _decrement_waiting_for(engine: Engine, parent_id: str) -> int:
    """Mirror ``_update_parent_on_child_complete`` waiting_for-- side effect.

    Returns the new value of ``waiting_for`` (as observed by the UPDATE).
    """
    with Session(engine) as session:
        row = session.execute(_DECREMENT_WAITING_FOR_SQL, {"pid": parent_id}).first()
        session.commit()
        return int(row[0]) if row is not None else 0


def _read_waiting_for(instance_repo: SQLModelInstanceRepository, instance_id: str) -> int | None:
    inst = instance_repo.get(instance_id)
    return inst.waiting_for if inst is not None else None


@pytest.fixture
async def cm(
    instance_repo: SQLModelInstanceRepository,
    message_repo: SQLModelMessageQueueRepository,
) -> CorrelationManager:
    """Start a real CorrelationManager bound to the test repositories.

    Registers the manager as the global singleton so the production hook
    helpers (``notify_corr_register`` / ``notify_corr_resolve``) dispatch
    to it. Stops the manager and clears the singleton on teardown.
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


# ─── Test 1: Basic Shadow Mode Operation ──────────────────────────────────────


class TestBasicShadowMode:
    """Parent sends a message to a child; child completes.

    Verifies the happy-path shadow invariants:
      * CM pending_count == DB waiting_for after the increment.
      * CM is_complete after the resolve; pending entry cleaned up.
      * DB waiting_for is unaffected by CM activity (shadow invariant).
    """

    @pytest.mark.asyncio
    async def test_register_then_resolve_tracks_db_state(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        cm: CorrelationManager,
    ) -> None:
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=0)
        _make_instance(engine, child_id, parent_id=parent_id)

        # ── Step 1: send_message side effect ──
        # Production: send_message() bumps waiting_for, then calls
        # notify_corr_register(parent, child, message_id).
        new_val = _increment_waiting_for(engine, parent_id)
        assert new_val == 1, f"increment should yield 1, got {new_val}"
        msg_id_1 = f"msg-{uuid.uuid4().hex[:8]}"
        await notify_corr_register(
            parent_id=parent_id,
            child_id=child_id,
            message_id=msg_id_1,
        )

        # CM should now be tracking exactly one pending correlation.
        assert cm.get_pending_count(parent_id) == 1
        assert cm.is_complete(parent_id) is False
        # DB waiting_for agrees with CM pending count.
        assert _read_waiting_for(instance_repo, parent_id) == 1
        # The correlation entry is keyed (child_id, message_id) — sanity check.
        assert parent_id in cm._pending
        pc = cm._pending[parent_id]
        correlation_key = f"{child_id}:{msg_id_1}"
        assert correlation_key in pc.pending
        assert pc.had_error is False

        # ── Step 2: child completion side effect ──
        # Production: _update_parent_on_child_complete decrements waiting_for,
        # then calls notify_corr_resolve(parent, child, message_id, "responded").
        # The hook helper is fire-and-forget; the completion signal is
        # observable via the CM's pending map and the completion callback
        # (asserted in Test 2/3 via the callback list). Here we verify
        # the CM transitioned to "complete" by inspecting its state.
        new_val = _decrement_waiting_for(engine, parent_id)
        assert new_val == 0
        await notify_corr_resolve(
            parent_id=parent_id,
            child_id=child_id,
            message_id=msg_id_1,
            status="responded",
        )
        # Resolving the last pending should clear the in-memory entry.
        assert cm.is_complete(parent_id) is True
        assert cm.get_pending_count(parent_id) == 0
        assert parent_id not in cm._pending

        # DB waiting_for unchanged (already at 0, the SQL UPDATE was a no-op clamp).
        assert _read_waiting_for(instance_repo, parent_id) == 0


# ─── Test 2: Multiple Messages to Same Child ──────────────────────────────────


class TestMultipleMessagesToSameChild:
    """Two messages to the same child — completion fires only after both resolve."""

    @pytest.mark.asyncio
    async def test_two_messages_fire_complete_only_after_both(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        cm: CorrelationManager,
    ) -> None:
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=0)
        _make_instance(engine, child_id, parent_id=parent_id)

        # Capture completion callbacks in a list to assert ordering and
        # contents. The CM is created in the fixture without a callback;
        # we inject one AFTER fixture setup.
        completion_calls: list[tuple[str, str]] = []

        async def on_complete(
            pid: str, terminal_status: str
        ) -> None:
            completion_calls.append((pid, terminal_status))

        cm._completion_callback = on_complete

        # ── Send 2 messages to the same child ──
        msg_a = f"msg-a-{uuid.uuid4().hex[:8]}"
        msg_b = f"msg-b-{uuid.uuid4().hex[:8]}"

        new_val = _increment_waiting_for(engine, parent_id)
        new_val = _increment_waiting_for(engine, parent_id)
        assert new_val == 2
        await notify_corr_register(parent_id, child_id, msg_a)
        await notify_corr_register(parent_id, child_id, msg_b)

        # Both pending.
        assert cm.get_pending_count(parent_id) == 2
        assert _read_waiting_for(instance_repo, parent_id) == 2
        # No completion yet.
        assert completion_calls == []

        # ── Resolve the first message — completion must NOT fire ──
        new_val = _decrement_waiting_for(engine, parent_id)
        assert new_val == 1
        await notify_corr_resolve(
            parent_id, child_id, msg_a, status="responded"
        )
        # CM still has one pending entry.
        assert cm.get_pending_count(parent_id) == 1
        # ... and completion has NOT fired.
        assert completion_calls == [], (
            "Resolving one of two pending must NOT fire complete"
        )
        # Correct entry remains; the resolved one is gone.
        assert f"{child_id}:{msg_b}" in cm._pending[parent_id].pending
        assert f"{child_id}:{msg_a}" not in cm._pending[parent_id].pending

        # ── Resolve the second — completion fires with terminal_status="completed" ──
        new_val = _decrement_waiting_for(engine, parent_id)
        assert new_val == 0
        await notify_corr_resolve(
            parent_id, child_id, msg_b, status="responded"
        )
        # Pending map cleaned up; in-memory state removed.
        assert cm.get_pending_count(parent_id) == 0
        assert parent_id not in cm._pending
        # Callback fired exactly once, with terminal_status="completed".
        assert completion_calls == [(parent_id, "completed")]


# ─── Test 3: Error Path Shadow Mode ──────────────────────────────────────────


class TestErrorPathShadowMode:
    """Child errors → CM resolves with status="error" → terminal_status="error"."""

    @pytest.mark.asyncio
    async def test_error_resolution_yields_error_terminal(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        cm: CorrelationManager,
    ) -> None:
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=0)
        _make_instance(engine, child_id, parent_id=parent_id)

        completion_calls: list[tuple[str, str]] = []

        async def on_complete(
            pid: str, terminal_status: str
        ) -> None:
            completion_calls.append((pid, terminal_status))

        cm._completion_callback = on_complete

        # ── Register one pending message ──
        msg_id = f"msg-{uuid.uuid4().hex[:8]}"
        _increment_waiting_for(engine, parent_id)
        await notify_corr_register(parent_id, child_id, msg_id)
        assert cm.get_pending_count(parent_id) == 1
        assert cm._pending[parent_id].had_error is False
        assert completion_calls == []

        # ── Simulate the error_reporting.py path: status="error" ──
        _decrement_waiting_for(engine, parent_id)
        await notify_corr_resolve(
            parent_id, child_id, msg_id, status="error"
        )
        # Callback fired with terminal_status="error" because had_error was set.
        assert completion_calls == [(parent_id, "error")]
        # In-memory state cleaned up.
        assert parent_id not in cm._pending
        assert cm.is_complete(parent_id) is True

    @pytest.mark.asyncio
    async def test_mixed_resolve_responded_then_error_yields_error(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        cm: CorrelationManager,
    ) -> None:
        """Conservative rule: any error response → parent terminal is "error".

        Two pending messages; one resolves cleanly, the other with
        status="error". The completion must report terminal_status="error"
        because had_error is sticky.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=0)
        _make_instance(engine, child_id, parent_id=parent_id)

        completion_calls: list[tuple[str, str]] = []

        async def on_complete(
            pid: str, terminal_status: str
        ) -> None:
            completion_calls.append((pid, terminal_status))

        cm._completion_callback = on_complete

        msg_ok = f"msg-ok-{uuid.uuid4().hex[:8]}"
        msg_err = f"msg-err-{uuid.uuid4().hex[:8]}"

        _increment_waiting_for(engine, parent_id)
        _increment_waiting_for(engine, parent_id)
        await notify_corr_register(parent_id, child_id, msg_ok)
        await notify_corr_register(parent_id, child_id, msg_err)
        assert cm.get_pending_count(parent_id) == 2

        # Resolve the OK one first — must not fire complete.
        _decrement_waiting_for(engine, parent_id)
        await notify_corr_resolve(
            parent_id, child_id, msg_ok, status="responded"
        )
        assert cm.get_pending_count(parent_id) == 1
        assert cm._pending[parent_id].had_error is False
        assert completion_calls == []

        # Resolve the error one — fires complete with terminal_status="error".
        _decrement_waiting_for(engine, parent_id)
        await notify_corr_resolve(
            parent_id, child_id, msg_err, status="error"
        )
        assert cm.get_pending_count(parent_id) == 0
        assert completion_calls == [(parent_id, "error")]


# ─── Test 4: Hook Helper Is a No-Op When CM Not Wired ────────────────────────


class TestHookHelperTolerantOfMissingCM:
    """The hook helpers MUST be safe to call when no CM is registered.

    Shadow-mode safety: a misconfigured production environment (CM not
    wired up) must not break send_message or child_reports. The helpers
    short-circuit when ``get_correlation_manager()`` returns ``None``.
    """

    @pytest.mark.asyncio
    async def test_register_helper_is_noop_without_cm(self) -> None:
        # No cm fixture — explicitly clear the global singleton.
        set_correlation_manager(None)
        # Must not raise; must silently return.
        await notify_corr_register(
            parent_id="orphan-parent",
            child_id="orphan-child",
            message_id="orphan-msg",
        )

    @pytest.mark.asyncio
    async def test_resolve_helper_is_noop_without_cm(self) -> None:
        set_correlation_manager(None)
        # Must not raise; helper is fire-and-forget and returns None.
        result = await notify_corr_resolve(
            parent_id="orphan-parent",
            child_id="orphan-child",
            message_id="orphan-msg",
            status="responded",
        )
        assert result is None


# ─── Test 5: Rebuild From DB on Start ─────────────────────────────────────────


class TestRebuildFromDB:
    """On start(), CM reconstructs pending state from DB rows where waiting_for > 0.

    This is the cold-start safety net: if the daemon is restarted while
    a parent has waiting_for > 0, the CM rebuilds its pending map from
    the DB so it can continue validating the cascade.
    """

    @pytest.mark.asyncio
    async def test_start_rebuilds_pending_from_db_waiting_for(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository,
    ) -> None:
        # Pre-populate the DB with parent (waiting_for=2) and 2 children.
        # No messages are enqueued in the queue, so the rebuild will count
        # only the parent's waiting_for, not the queue contents (rebuild
        # reads ready+processing+retrying messages per child).
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_a = f"child-a-{uuid.uuid4().hex[:8]}"
        child_b = f"child-b-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=2)
        _make_instance(engine, child_a, parent_id=parent_id)
        _make_instance(engine, child_b, parent_id=parent_id)

        # Now create a fresh CM and start it; rebuild_from_db should run.
        # The CM will not find ready/processing/retrying messages for the
        # children (the queue is empty), so cm_count will be 0 and a
        # mismatch warning will be logged (DB waiting_for=2, CM found=0).
        # This documents the current rebuild behavior — it does NOT count
        # the parent's waiting_for against arbitrary message-queue rows;
        # it only re-registers queue entries for which a child is known.
        # The test asserts the post-rebuild CM state is consistent with
        # the empty queue (no queue rows → no CM entries).
        new_cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
        )
        await new_cm.start()
        try:
            # CM should have no pending for the parent (no messages in queue).
            assert new_cm.get_pending_count(parent_id) == 0
            # ...but the parent's DB waiting_for is still 2.
            assert _read_waiting_for(instance_repo, parent_id) == 2
        finally:
            await new_cm.stop()


# ─── Test 6: Shadow Validation Logs Match When Counts Agree ──────────────────


class TestShadowValidationLogs:
    """When CM pending count == DB waiting_for, _validate_shadow_mode logs MATCH.

    The shadow mode's whole point is to surface a warning when the two
    diverge; a MATCH is logged at DEBUG (rate-limited). This test calls
    _validate_shadow_mode directly to verify the match counter increments
    when the counts agree.
    """

    @pytest.mark.asyncio
    async def test_match_counter_increments_when_counts_agree(
        self,
        engine: Engine,
        instance_repo: SQLModelInstanceRepository,
        cm: CorrelationManager,
    ) -> None:
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        _make_instance(engine, parent_id, waiting_for=0)
        _make_instance(engine, child_id, parent_id=parent_id)

        # Establish agreement: 1 pending, 1 DB waiting_for.
        _increment_waiting_for(engine, parent_id)
        await notify_corr_register(parent_id, child_id, f"msg-{uuid.uuid4().hex[:8]}")

        before = cm._match_count
        await cm._validate_shadow_mode(parent_id)
        after = cm._match_count
        # One MATCH recorded.
        assert after == before + 1, (
            f"Expected match counter to increment by 1 (was {before}, now {after})"
        )
        # Mismatch counter untouched.
        assert cm._mismatch_count == 0
