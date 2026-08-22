"""Regression tests for the question-pause completion status guard.

The question() tool pauses an instance before returning control to the user.
A stale child-completion callback may still run after that transition, so the
completion path must treat ``PAUSED`` as an idempotent no-op. These tests call
the synchronous DB path directly against a real in-memory SQLite database so
the status transition (or lack of one) is observable and deterministic.

It also exercises ``MessageProcessingPipeline._is_instance_paused`` — the
fresh-DB re-check that the pipeline runs before delegating to
``_check_child_completion``. The helper is mocked here (no real DB) because
its entire contract is ``self._manager._instance_repository.get(id)``.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Import all tables used by the completion service before create_all().
from daemon.repositories.dependency_bus.models import DependencyWatcher  # noqa: F401
from daemon.repositories.event.models import Event  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobItem  # noqa: F401
from daemon.repositories.message_queue.models import MessageQueue  # noqa: F401
from daemon.repositories.report_injection.models import ReportInjection  # noqa: F401
from daemon.repositories.task.models import Task  # noqa: F401
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.message_processing_pipeline import MessageProcessingPipeline
from daemon.write_pause_guard import WritePauseGuard


@pytest.fixture
def engine() -> Engine:
    """Create an isolated in-memory SQLite engine for each test."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def dependency_bus() -> MagicMock:
    """Install a zero-pending-child bus for the root completion path."""
    bus = MagicMock(name="DependencyBus")
    bus.count_pending_for_target_sync.return_value = 0
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    """Build the real completion service with only its DB dependencies."""
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service


def _seed_root_instance(engine: Engine, *, status: str) -> str:
    """Insert a root instance in the requested lifecycle state."""
    instance_id = f"question-pause-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="leader",
                agent_name="leader",
                agent_dir="/tmp/leader",
                parent_id=None,
                status=status,
                paused_at="2026-07-22T00:00:00+00:00"
                if status == InstanceStatus.PAUSED.value
                else None,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


class TestQuestionPauseCompletionGuard:
    """Completion callbacks must respect a question() pause transition."""

    def test_paused_root_stays_paused_when_completion_runs(
        self, engine: Engine, dependency_bus: MagicMock
    ) -> None:
        """A stale completion report cannot turn PAUSED into COMPLETED.

        Phase 1 (pause-report-recovery Variant B fix 2): PAUSED is
        now a separate guard outcome (``deferred_pause``) instead of
        ``idempotency_skip``. The instance stays PAUSED; the DEFERRED
        marker is NOT written for a ROOT instance (parent_id is
        None → no delivery obligation).
        """
        service = _build_child_reports_service(engine)
        instance_id = _seed_root_instance(
            engine, status=InstanceStatus.PAUSED.value
        )

        result = service._process_child_completion_db_sync(
            instance_id=instance_id,
            completed_message_id="stale-completion-message",
            last_content="stale completion",
        )

        # Phase 1: PAUSED status → deferred_pause outcome (NOT
        # idempotency_skip — that branch is COMPLETED/ERROR only).
        assert result.outcome == "deferred_pause"
        dependency_bus.count_pending_for_target_sync.assert_not_called()
        with Session(engine) as session:
            instance = session.get(Instance, instance_id)
            assert instance is not None
            assert instance.status == InstanceStatus.PAUSED.value
            # No DEFERRED marker for a root (parent_id is None).
            from sqlmodel import select as sm_select
            from daemon.repositories.report_injection.models import (
                ReportInjection,
                ReportInjectionState,
            )
            markers = list(
                session.exec(
                    sm_select(ReportInjection).where(
                        ReportInjection.child_instance_id == instance_id
                    ).where(
                        ReportInjection.state
                        == ReportInjectionState.DEFERRED.value
                    )
                ).all()
            )
            assert markers == [], (
                "Root instance (parent_id None) must not have a "
                "DEFERRED marker — no delivery obligation"
            )

    @pytest.mark.parametrize(
        "initial_status",
        [InstanceStatus.RUNNING.value, InstanceStatus.IDLE.value],
    )
    def test_non_paused_root_still_completes_normally(
        self,
        engine: Engine,
        initial_status: str,
    ) -> None:
        """Running and idle roots still take the normal COMPLETED path."""
        service = _build_child_reports_service(engine)
        instance_id = _seed_root_instance(engine, status=initial_status)

        result = service._process_child_completion_db_sync(
            instance_id=instance_id,
            completed_message_id="completion-message",
            last_content="normal completion",
        )

        assert result.outcome == "root_completed"
        with Session(engine) as session:
            instance = session.get(Instance, instance_id)
            assert instance is not None
            assert instance.status == InstanceStatus.COMPLETED.value


def _build_pipeline(repo: MagicMock | None) -> MessageProcessingPipeline:
    """Build a MessageProcessingPipeline with only ``_manager`` wired.

    ``__init__`` is bypassed because the test only exercises
    ``_is_instance_paused``, which reads ``self._manager._instance_repository``
    via ``getattr`` and falls through when it is missing.
    """
    pipeline = MessageProcessingPipeline.__new__(MessageProcessingPipeline)
    manager = MagicMock(name="InstanceManager")
    manager._instance_repository = repo
    pipeline._manager = manager
    return pipeline


class TestIsInstancePaused:
    """``MessageProcessingPipeline._is_instance_paused`` contract.

    The pipeline calls ``self._manager._instance_repository.get(id)`` off
    the event loop (``asyncio.to_thread``) and compares ``status`` to
    ``InstanceStatus.PAUSED.value``. Failures of any kind must return
    ``False`` so the message path keeps flowing.
    """

    @pytest.mark.asyncio
    async def test_returns_true_when_db_status_is_paused(self) -> None:
        """A row with status=PAUSED must report True."""
        instance = MagicMock()
        instance.status = InstanceStatus.PAUSED.value
        repo = MagicMock()
        repo.get = MagicMock(return_value=instance)
        pipeline = _build_pipeline(repo)

        result = await pipeline._is_instance_paused("inst-1")

        assert result is True
        repo.get.assert_called_once_with("inst-1")

    @pytest.mark.asyncio
    async def test_returns_false_when_db_status_is_running(self) -> None:
        """A row with status=RUNNING must report False."""
        instance = MagicMock()
        instance.status = InstanceStatus.RUNNING.value
        repo = MagicMock()
        repo.get = MagicMock(return_value=instance)
        pipeline = _build_pipeline(repo)

        result = await pipeline._is_instance_paused("inst-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_instance_not_found(self) -> None:
        """Missing row (repo.get returns None) is fail-open False."""
        repo = MagicMock()
        repo.get = MagicMock(return_value=None)
        pipeline = _build_pipeline(repo)

        result = await pipeline._is_instance_paused("missing-id")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_db_query_raises(self) -> None:
        """Any DB read error must not crash — return False (fail-open)."""
        repo = MagicMock()
        repo.get = MagicMock(side_effect=RuntimeError("db exploded"))
        pipeline = _build_pipeline(repo)

        result = await pipeline._is_instance_paused("inst-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_repository_missing(self) -> None:
        """No repository attribute on manager → fail-open False."""
        pipeline = MessageProcessingPipeline.__new__(MessageProcessingPipeline)
        manager = MagicMock(spec=[])  # no attributes at all
        pipeline._manager = manager

        result = await pipeline._is_instance_paused("inst-1")

        assert result is False
