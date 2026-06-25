"""Tests for ``JobFeedbackObserver._finalize_instance`` (Phase 3 fix).

The ``_finalize_instance`` method (in ``daemon/services/job_feedback_observer.py``)
owns the FULL instance terminal transition in the CM-callback path:

  1. ``instance.status`` → COMPLETED / ERROR (idempotency guarded).
  2. SSE ``status_change`` broadcast via ``instance_manager._live_hub``.
  3. ``CompletionRegistry.complete()`` signal (unblocks waiters).
  4. Lifecycle event published via ``events_service``.

Phase 3 regression context: before this method existed, the CM callback chain
only transitioned the JOB. Legacy inline paths in ``child_reports.py`` /
``error_reporting.py`` set the instance status, but those paths early-return
when CM is active. So instances were left stuck at ``RUNNING`` while their
jobs showed ``COMPLETED`` — breaking ``invoke_agent_and_wait()`` callers,
orphan-job detection, and the SSE lifecycle stream. ``_finalize_instance``
restores symmetry between the CM-active and CM-disabled paths.

This file covers the eight required scenarios:

  1. RUNNING → COMPLETED status transition.
  2. RUNNING → ERROR status transition.
  3. SSE ``status_change`` is emitted with correct args.
  4. ``CompletionRegistry.complete()`` is signaled on both paths.
  5. Lifecycle event is published.
  6. Idempotency: already-terminal instance is a no-op.
  7. Phase 3 regression case: instance stuck at RUNNING gets transitioned.
  8. Error isolation: SSE failure does not block other side-effects.

Run with::

    pytest tests/test_finalize_instance.py -v --tb=short
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Importing the model classes is what registers them with SQLModel.metadata.
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.write_pause_guard import WritePauseGuard


# ─── Shared fixtures & helpers ────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (mirrors test_observer_correlation.py).

    Used so the DB transition inside ``_finalize_instance`` is exercised
    end-to-end (the method writes via ``Session(engine)`` + ``session.commit()``).
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


def seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "developer",
    parent_id: str | None = None,
    version: int = 1,
) -> str:
    """Insert an Instance row into the in-memory DB. Returns the instance_id."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir="/tmp/agent",
            parent_id=parent_id,
            status=status,
            version=version,
        )
        s.add(inst)
        s.commit()
    return iid


def get_instance(engine: Engine, instance_id: str) -> Instance | None:
    """Re-read an Instance from the DB (post-commit, detached)."""
    with Session(engine) as s:
        return s.get(Instance, instance_id)


@contextmanager
def patched_completion_registry():
    """Patch ``daemon.services.completion_registry.get_completion_registry``.

    Yields a mock whose ``.complete(...)`` is a ``MagicMock`` (the real
    ``complete`` is synchronous). The patch is active for the duration of
    the ``with`` block and removed on exit.

    ``_finalize_instance`` imports ``get_completion_registry`` lazily inside
    the function body, so the patched name must be visible at the
    ``daemon.services.completion_registry`` module attribute — the local
    import re-binds on every call.
    """
    mock_registry = MagicMock(name="CompletionRegistry")
    mock_registry.complete = MagicMock(return_value=True)

    with patch(
        "daemon.services.completion_registry.get_completion_registry",
        return_value=mock_registry,
    ) as patched:
        yield mock_registry, patched


def make_observer(
    engine: Engine,
    *,
    write_guard: WritePauseGuard | None = None,
    live_hub: Any | None = None,
    events_service: Any | None = None,
    get_last_message_returns: str | None = "agent response",
) -> tuple[JobFeedbackObserver, dict[str, Any]]:
    """Build a ``JobFeedbackObserver`` with a real engine + mocked side deps.

    Returns ``(observer, mocks)`` where ``mocks`` is a dict of named mocks
    the tests can assert against.

    Notes:
      * ``write_guard`` defaults to a real ``WritePauseGuard`` — the unit
        under test (``_finalize_instance``) calls ``write_enter`` /
        ``write_exit`` on it via ``WriteGuardSession.__enter__`` /
        ``__exit__``. A real guard exercises that path end-to-end.
      * The instance_manager mock has ``is_write_paused = False`` set
        explicitly (see the WritePauseGuard gotcha in the task spec —
        MagicMock auto-attributes are truthy, which would short-circuit).
      * The CompletionRegistry singleton is patched separately by
        ``patched_completion_registry`` so per-test isolation works.
    """
    guard = write_guard or WritePauseGuard()

    mock_manager = MagicMock(name="InstanceManager")
    mock_manager.engine = engine
    mock_manager.write_guard = guard
    # CRITICAL: explicit False. MagicMock auto-attrs are truthy and would
    # be interpreted as "writes paused" elsewhere. ``_finalize_instance``
    # does not read this directly, but the surrounding test wiring does.
    mock_manager.is_write_paused = False

    # Live hub — mocked, so we can assert on stream_status_change calls.
    hub = live_hub if live_hub is not None else MagicMock(name="LiveHub")
    hub.stream_status_change = AsyncMock()
    mock_manager._live_hub = hub

    # Events service — mocked, with an async method we can spy on.
    events = (
        events_service if events_service is not None else MagicMock(name="Events")
    )
    events._publish_instance_lifecycle_event = AsyncMock()
    mock_manager._events_service = events

    # Last assistant message — AsyncMock; tests can override per-call.
    mock_manager._get_last_assistant_message_raw = AsyncMock(
        return_value=get_last_message_returns
    )

    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=MagicMock(),
        job_repo=MagicMock(),
        lock_repo=MagicMock(),
        project_repo=MagicMock(),
        instance_manager=mock_manager,
    )

    return observer, {
        "instance_manager": mock_manager,
        "live_hub": hub,
        "events_service": events,
        "write_guard": guard,
    }


# ─── Test 1: Status transition — Instance RUNNING → COMPLETED ────────────────


class TestStatusTransitionToCompleted:
    """``_finalize_instance(id, "completed")`` flips instance.status to COMPLETED."""

    @pytest.mark.asyncio
    async def test_running_instance_transitions_to_completed(self, engine):
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        observer, mocks = make_observer(engine)

        with patched_completion_registry():
            await observer._finalize_instance(instance_id, "completed")

        inst = get_instance(engine, instance_id)
        assert inst is not None
        assert inst.status == InstanceStatus.COMPLETED.value
        # Version was bumped from 1 → 2.
        assert inst.version == 2
        # updated_at and last_activity_at were refreshed.
        assert inst.updated_at is not None
        assert inst.last_activity_at is not None


# ─── Test 2: Status transition — Instance RUNNING → ERROR ─────────────────────


class TestStatusTransitionToError:
    """``_finalize_instance(id, "error")`` flips instance.status to ERROR."""

    @pytest.mark.asyncio
    async def test_running_instance_transitions_to_error(self, engine):
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        observer, mocks = make_observer(engine)

        with patched_completion_registry():
            await observer._finalize_instance(instance_id, "error")

        inst = get_instance(engine, instance_id)
        assert inst is not None
        assert inst.status == InstanceStatus.ERROR.value
        assert inst.version == 2


# ─── Test 3: SSE event ───────────────────────────────────────────────────────


class TestSseStatusChangeEmitted:
    """``_finalize_instance`` emits an SSE ``status_change`` event with correct args."""

    @pytest.mark.asyncio
    async def test_completed_path_emits_status_change_completed(self, engine):
        instance_id = seed_instance(
            engine, status=InstanceStatus.RUNNING.value, agent_id="writer"
        )
        observer, mocks = make_observer(engine)

        with patched_completion_registry():
            await observer._finalize_instance(instance_id, "completed")

        mocks["live_hub"].stream_status_change.assert_awaited_once()
        call = mocks["live_hub"].stream_status_change.call_args
        # stream_status_change(instance_id, status, agent_id=...)
        assert call.args[0] == instance_id
        assert call.args[1] == "completed"
        assert call.kwargs.get("agent_id") == "writer"

    @pytest.mark.asyncio
    async def test_error_path_emits_status_change_error(self, engine):
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        observer, mocks = make_observer(engine)

        with patched_completion_registry():
            await observer._finalize_instance(instance_id, "error")

        call = mocks["live_hub"].stream_status_change.call_args
        assert call.args[0] == instance_id
        assert call.args[1] == "error"


# ─── Test 4: CompletionRegistry signal ───────────────────────────────────────


class TestCompletionRegistrySignal:
    """``_finalize_instance`` signals ``CompletionRegistry.complete()`` correctly.

    The completed path passes the last assistant message as ``result`` with
    ``is_error=False`` (default). The error path passes the error string with
    ``is_error=True``.
    """

    @pytest.mark.asyncio
    async def test_completed_path_signals_completion_without_error(self, engine):
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        observer, _ = make_observer(
            engine, get_last_message_returns="done"
        )

        with patched_completion_registry() as (mock_registry, _patched):
            await observer._finalize_instance(instance_id, "completed")

        mock_registry.complete.assert_called_once()
        call = mock_registry.complete.call_args
        assert call.args[0] == instance_id
        # result=last message, is_error defaults to False
        assert call.kwargs.get("result") == "done"
        assert call.kwargs.get("is_error", False) is False

    @pytest.mark.asyncio
    async def test_error_path_signals_completion_with_error_flag(self, engine):
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        observer, _ = make_observer(engine)

        with patched_completion_registry() as (mock_registry, _patched):
            await observer._finalize_instance(
                instance_id, "error", error="boom"
            )

        mock_registry.complete.assert_called_once()
        call = mock_registry.complete.call_args
        assert call.args[0] == instance_id
        # error path wraps the error in "Agent error: <msg>".
        assert call.kwargs.get("result") == "Agent error: boom"
        assert call.kwargs.get("is_error") is True

    @pytest.mark.asyncio
    async def test_error_path_uses_unknown_when_no_error_string(self, engine):
        """No error string → ``"Unknown error"`` fallback in the signal."""
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        observer, _ = make_observer(engine)

        with patched_completion_registry() as (mock_registry, _patched):
            await observer._finalize_instance(instance_id, "error")

        call = mock_registry.complete.call_args
        assert call.kwargs.get("result") == "Agent error: Unknown error"
        assert call.kwargs.get("is_error") is True


# ─── Test 5: Lifecycle event published ───────────────────────────────────────


class TestLifecycleEventPublished:
    """``_finalize_instance`` publishes a lifecycle event via the events service."""

    @pytest.mark.asyncio
    async def test_lifecycle_event_published_with_correct_args(self, engine):
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        instance_id = seed_instance(
            engine,
            status=InstanceStatus.RUNNING.value,
            parent_id=parent_id,
        )
        observer, mocks = make_observer(engine)

        with patched_completion_registry():
            await observer._finalize_instance(
                instance_id, "completed", error=None
            )

        mocks[
            "events_service"
        ]._publish_instance_lifecycle_event.assert_awaited_once()
        call = mocks[
            "events_service"
        ]._publish_instance_lifecycle_event.call_args
        assert call.kwargs.get("instance_id") == instance_id
        assert call.kwargs.get("status") == "completed"
        assert call.kwargs.get("parent_id") == parent_id

    @pytest.mark.asyncio
    async def test_lifecycle_event_for_root_instance_has_no_parent(self, engine):
        """Root instance (parent_id=None) publishes lifecycle event with parent_id=None."""
        instance_id = seed_instance(
            engine, status=InstanceStatus.RUNNING.value, parent_id=None
        )
        observer, mocks = make_observer(engine)

        with patched_completion_registry():
            await observer._finalize_instance(instance_id, "completed")

        call = mocks[
            "events_service"
        ]._publish_instance_lifecycle_event.call_args
        assert call.kwargs.get("parent_id") is None


# ─── Test 6: Idempotency — already-terminal instance ─────────────────────────


class TestIdempotency:
    """An instance already in a terminal status is a no-op.

    Covers ``COMPLETED``, ``ERROR``, ``TERMINATED``, ``FAILED`` — all in
    ``_TERMINAL_INSTANCE_STATUSES``. The method returns early BEFORE any
    side effect, so SSE / CompletionRegistry / lifecycle event are not
    double-fired.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "terminal_status",
        [
            InstanceStatus.COMPLETED.value,
            InstanceStatus.ERROR.value,
            InstanceStatus.TERMINATED.value,
            InstanceStatus.FAILED.value,
        ],
    )
    async def test_already_terminal_instance_is_noop(
        self, engine, terminal_status
    ):
        instance_id = seed_instance(
            engine,
            status=InstanceStatus.RUNNING.value,
            version=1,
        )
        # Manually set the instance to a terminal status.
        with Session(engine) as s:
            inst = s.get(Instance, instance_id)
            inst.status = terminal_status
            s.commit()

        observer, mocks = make_observer(engine)

        with patched_completion_registry() as (mock_registry, _patched):
            await observer._finalize_instance(instance_id, "completed")

        # No side effects fired.
        mocks["live_hub"].stream_status_change.assert_not_called()
        mock_registry.complete.assert_not_called()
        mocks[
            "events_service"
        ]._publish_instance_lifecycle_event.assert_not_called()

        # Version was NOT bumped (no write happened).
        inst = get_instance(engine, instance_id)
        assert inst.version == 1


# ─── Test 7: Phase 3 regression case — instance stuck at RUNNING ─────────────


class TestPhase3Regression:
    """The original bug: instance left at RUNNING while job is COMPLETED.

    Before Phase 3, the CM callback path only transitioned the JOB; the
    instance was never updated. This test pins that regression: starting
    from ``status=RUNNING`` (the buggy stuck state), after
    ``_finalize_instance`` the instance is COMPLETED and all the side
    effects that the bug suppressed (SSE, CompletionRegistry) now fire.
    """

    @pytest.mark.asyncio
    async def test_stuck_running_instance_is_rescued(self, engine):
        # Seed the buggy state: instance stuck at RUNNING.
        instance_id = seed_instance(
            engine,
            status=InstanceStatus.RUNNING.value,
            version=1,
        )
        # Sanity check — we ARE in the buggy stuck state.
        inst_before = get_instance(engine, instance_id)
        assert inst_before.status == InstanceStatus.RUNNING.value

        observer, mocks = make_observer(engine)

        with patched_completion_registry() as (mock_registry, _patched):
            await observer._finalize_instance(instance_id, "completed")

        # The fix: instance flips to COMPLETED.
        inst_after = get_instance(engine, instance_id)
        assert inst_after.status == InstanceStatus.COMPLETED.value
        assert inst_after.version == 2  # bumped

        # The fix: SSE fires (was NOT happening pre-fix).
        mocks["live_hub"].stream_status_change.assert_awaited_once()
        # The fix: CompletionRegistry signaled (was NOT happening pre-fix).
        mock_registry.complete.assert_called_once()


# ─── Test 8: Error isolation — SSE fails, other side-effects still fire ─────


class TestErrorIsolation:
    """A failure in one side effect must not block the others.

    ``_finalize_instance`` wraps each of Steps 2/3/4 in its own
    ``try / except`` block. The DB transition (Step 1) commits BEFORE
    any of the side effects, so a downstream failure leaves the DB
    consistent and lets the other side effects still fire.
    """

    @pytest.mark.asyncio
    async def test_sse_failure_does_not_block_other_side_effects(self, engine):
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        observer, mocks = make_observer(engine)

        # Make SSE raise. The other side effects must still run.
        mocks["live_hub"].stream_status_change.side_effect = RuntimeError(
            "SSE boom"
        )

        with patched_completion_registry() as (mock_registry, _patched):
            # Must not raise.
            await observer._finalize_instance(instance_id, "completed")

        # DB transition still happened (Step 1 runs before SSE).
        inst = get_instance(engine, instance_id)
        assert inst.status == InstanceStatus.COMPLETED.value
        assert inst.version == 2

        # SSE was attempted (and raised)…
        mocks["live_hub"].stream_status_change.assert_awaited_once()

        # …but CompletionRegistry still signaled.
        mock_registry.complete.assert_called_once()
        assert mock_registry.complete.call_args.args[0] == instance_id

        # …and the lifecycle event was still published.
        mocks[
            "events_service"
        ]._publish_instance_lifecycle_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_completion_registry_failure_does_not_block_lifecycle(
        self, engine
    ):
        """A failure in CompletionRegistry.complete() must not block the
        lifecycle event publish.
        """
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        observer, mocks = make_observer(engine)

        with patched_completion_registry() as (mock_registry, _patched):
            # Make CompletionRegistry.complete() raise.
            mock_registry.complete.side_effect = RuntimeError("registry boom")

            await observer._finalize_instance(instance_id, "completed")

        # SSE and lifecycle event still fired.
        mocks["live_hub"].stream_status_change.assert_awaited_once()
        mocks[
            "events_service"
        ]._publish_instance_lifecycle_event.assert_awaited_once()
        # DB transition still happened.
        inst = get_instance(engine, instance_id)
        assert inst.status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_lifecycle_event_failure_does_not_block_sse(self, engine):
        """A failure in the lifecycle event publish must not block the SSE emit.

        Step 2 (SSE) runs before Step 4 (lifecycle), so an SSE failure can
        also mask a later step — but a lifecycle failure alone (with SSE
        succeeding) is the symmetric case this test pins.
        """
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        observer, mocks = make_observer(engine)

        mocks[
            "events_service"
        ]._publish_instance_lifecycle_event.side_effect = RuntimeError(
            "events boom"
        )

        with patched_completion_registry():
            await observer._finalize_instance(instance_id, "completed")

        # SSE fired before the failure point.
        mocks["live_hub"].stream_status_change.assert_awaited_once()
        # Lifecycle event was attempted (and raised)…
        mocks[
            "events_service"
        ]._publish_instance_lifecycle_event.assert_awaited_once()


# ─── Test 9 (bonus): unknown terminal_status is a no-op ─────────────────────


class TestUnknownTerminalStatus:
    """Defensive: ``terminal_status`` outside the known set is a no-op."""

    @pytest.mark.asyncio
    async def test_unknown_terminal_status_logs_and_returns(self, engine):
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        observer, mocks = make_observer(engine)

        with patched_completion_registry() as (mock_registry, _patched):
            # Must not raise.
            await observer._finalize_instance(instance_id, "frobnicated")

        # No side effects.
        mocks["live_hub"].stream_status_change.assert_not_called()
        mock_registry.complete.assert_not_called()
        mocks[
            "events_service"
        ]._publish_instance_lifecycle_event.assert_not_called()

        # Instance status unchanged.
        inst = get_instance(engine, instance_id)
        assert inst.status == InstanceStatus.RUNNING.value
        assert inst.version == 1


# ─── Test 10 (bonus): DB transition failure re-raises to caller ─────────────


class TestDbTransitionFailureReraises:
    """If the DB write itself fails, the exception propagates to the caller.

    The caller (``_finalize_job``) wraps the call in its own ``try/except``
    and logs at WARNING — but the failure is observable. This is by
    design: the orphan-detector / recovery sweep is the safety net.
    """

    @pytest.mark.asyncio
    async def test_session_commit_failure_propagates(self, engine):
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        observer, mocks = make_observer(engine)

        # Patch ``Session`` so its ``get`` returns a real instance BUT
        # ``commit`` raises. This simulates a DB write failure inside the
        # WriteGuardSession block.
        original_get = Session.get

        class _ExplodingSession:
            def __init__(self, real_session):
                self._real = real_session

            def get(self, *args, **kwargs):
                return self._real.get(*args, **kwargs)

            def commit(self):
                raise RuntimeError("DB write boom")

            def __getattr__(self, name):
                return getattr(self._real, name)

        real_session = Session(engine)
        exploding = _ExplodingSession(real_session)

        # Patch ``Session`` at the import site used by the production code
        # (``daemon.services.job_feedback_observer``) so the call inside
        # ``_finalize_instance`` resolves to the exploding proxy. Patching
        # ``sqlmodel.Session`` directly is not enough — the production
        # module binds ``Session`` at import time.
        with (
            patch(
                "daemon.services.job_feedback_observer.Session",
                return_value=exploding,
            ),
            patched_completion_registry() as (mock_registry, _patched),
        ):
            with pytest.raises(RuntimeError, match="DB write boom"):
                await observer._finalize_instance(instance_id, "completed")

        # The downstream side effects never ran.
        mocks["live_hub"].stream_status_change.assert_not_called()
        mock_registry.complete.assert_not_called()
        mocks[
            "events_service"
        ]._publish_instance_lifecycle_event.assert_not_called()
