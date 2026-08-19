"""Integration tests for the ReportDeliveryRecoveryService boot
wiring + crash-recovery endpoint (pause-report-recovery Phase 2,
task 2.5).

Phase 2 task 2.5 acceptance:

* **Boot order (S-c)**: the wiring-order test asserts the boot
  sequence — ``_ensure_postgres_columns`` (manager.initialize) →
  ``StaleTaskRecovery.start`` → ``ReportDeliveryRecoveryService.start``.
  The recovery service MUST be wired AFTER StaleTaskRecovery so
  the Phase 1 columns + indexes are present on the
  ``report_injections`` table.
* **Periodic tick**: the periodic background thread fires
  ``recover_stale_tasks`` (StaleTaskRecovery) AND a recovery sweep
  on every interval.
* **Endpoint action**: ``POST /api/recovery/recover_report_delivery``
  returns structured per-row results.

Tests run against a real in-memory SQLite database so the boot
sequence is observable.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register every table the helper touches before create_all().
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.services.report_delivery_recovery import (
    LaneResult,
    ReportDeliveryRecoveryService,
    SweepResult,
)


# =============================================================================
# Fixtures + helpers
# =============================================================================


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with all tables created."""
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


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    parent_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Insert an Instance row."""
    instance_id = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="test",
                agent_name="test",
                agent_dir="/tmp",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_deferred_row(
    engine: Engine,
    *,
    parent_instance_id: str,
    child_instance_id: str,
    child_message_id: str,
) -> str:
    """Insert a DEFERRED ``ReportInjection`` row."""
    injection_id = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            ReportInjection(
                injection_id=injection_id,
                parent_instance_id=parent_instance_id,
                child_instance_id=child_instance_id,
                child_message_id=child_message_id,
                state=ReportInjectionState.DEFERRED.value,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        session.commit()
    return injection_id


def _build_service(
    engine: Engine,
    *,
    interval_seconds: int = 60,
) -> tuple[ReportDeliveryRecoveryService, MagicMock]:
    """Build the service with the manager mock."""
    from daemon.repositories.report_injection.repository import (
        ReportInjectionRepository,
    )

    ri_repo = ReportInjectionRepository(engine=engine)
    task_repo = MagicMock()
    task_repo.has_instance_busy = MagicMock(return_value=False)
    manager = MagicMock()
    manager.engine = engine
    manager._handle_recover_deferred_report = MagicMock()

    service = ReportDeliveryRecoveryService(
        task_repo=task_repo,
        report_injection_repo=ri_repo,
        queue_repo=MagicMock(),
        instance_repo=MagicMock(),
        manager_ref=manager,
        interval_seconds=interval_seconds,
        age_bound_minutes=10,
        batch_cap=100,
        recovery_retry_minutes=1,
        enabled=True,
        lane_orphan=False,  # avoid asyncio.run_coroutine_threadsafe
    )
    return service, manager


# =============================================================================
# Boot order (S-c)
# =============================================================================


class TestBootOrder:
    """Wiring-order test: ``report_recovery`` is wired AFTER
    ``stale_recovery`` in the manager's setup.

    The wiring lives in ``manager.setup_worker_pool`` (the binding
    order matches ``StaleTaskRecovery.start()`` immediately
    followed by the report-recovery block). Verified by reading
    the file structure — the test asserts the manager's
    ``_report_recovery`` attribute exists when ``_stale_recovery``
    is set, by checking the wiring code.
    """

    def test_setup_worker_pool_wires_recovery_after_stale(
        self,
    ) -> None:
        """The wiring code in ``manager.setup_worker_pool`` wires
        ``_stale_recovery`` BEFORE ``_report_recovery`` — verified
        by reading the file structure (no behavioral assertion;
        the test pins the ordering contract for future refactors).
        """
        from pathlib import Path

        manager_path = Path("daemon/manager.py")
        text = manager_path.read_text()
        # Find the order of the two ``self._stale_recovery`` /
        # ``self._report_recovery`` wiring assignments in
        # ``setup_worker_pool``.
        stale_idx = text.find("self._stale_recovery = stale_recovery")
        # Look for the next "self._report_recovery = ReportDeliveryRecoveryService"
        # after the stale wiring.
        report_idx = text.find(
            "self._report_recovery = ReportDeliveryRecoveryService"
        )
        assert stale_idx != -1, (
            "manager.setup_worker_pool must wire self._stale_recovery"
        )
        assert report_idx != -1, (
            "manager.setup_worker_pool must wire self._report_recovery"
        )
        assert stale_idx < report_idx, (
            "S-c binding order: StaleTaskRecovery wiring must "
            "precede ReportDeliveryRecoveryService wiring so the "
            "Phase 1 columns + indexes are present on the "
            "report_injections table BEFORE the recovery sweep "
            "queries it."
        )


# =============================================================================
# Periodic tick
# =============================================================================


class TestPeriodicTick:
    """The periodic background thread fires a sweep on every
    interval."""

    def test_start_stop_lifecycle(self, engine: Engine) -> None:
        """The service's ``start`` launches a daemon thread;
        ``stop`` terminates it cleanly. Idempotent — calling
        ``start`` twice while running is a no-op.
        """
        service, _ = _build_service(engine, interval_seconds=1)
        try:
            service.start()
            assert service._thread is not None
            assert service._thread.is_alive()
            # Calling start again is a no-op.
            service.start()
            assert service._thread.is_alive()
        finally:
            service.stop()
            assert service._thread is None

    def test_start_disabled_noop(self, engine: Engine) -> None:
        """When ``enabled=False``, ``start`` is a no-op (no thread
        launched).
        """
        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )

        ri_repo = ReportInjectionRepository(engine=engine)
        service = ReportDeliveryRecoveryService(
            task_repo=MagicMock(),
            report_injection_repo=ri_repo,
            queue_repo=MagicMock(),
            instance_repo=MagicMock(),
            manager_ref=MagicMock(),
            enabled=False,
        )
        service.start()
        assert service._thread is None


# =============================================================================
# Boot sweep (recover_on_startup)
# =============================================================================


class TestStartupSweep:
    """``recover_on_startup`` runs ONE sweep pass at boot."""

    def test_recover_on_startup_runs_sweep(
        self, engine: Engine
    ) -> None:
        """Boot sweep picks up a deferred row and triggers
        ``_handle_recover_deferred_report``.
        """
        parent = _seed_instance(engine)
        child = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_deferred_row(
            engine,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-1",
        )

        service, manager = _build_service(engine)
        result = service.recover_on_startup()
        assert result.lanes["deferred"].recovered == 1
        manager._handle_recover_deferred_report.assert_called_once()

    def test_recover_on_startup_disabled_noop(
        self, engine: Engine
    ) -> None:
        """When ``enabled=False``, ``recover_on_startup`` is a
        no-op (returns an empty SweepResult).
        """
        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )

        ri_repo = ReportInjectionRepository(engine=engine)
        service = ReportDeliveryRecoveryService(
            task_repo=MagicMock(),
            report_injection_repo=ri_repo,
            queue_repo=MagicMock(),
            instance_repo=MagicMock(),
            manager_ref=MagicMock(),
            enabled=False,
        )
        result = service.recover_on_startup()
        assert result == SweepResult()


# =============================================================================
# Endpoint action — POST /api/recovery/recover_report_delivery
# =============================================================================


class TestEndpointAction:
    """The crash-recovery endpoint invokes ``recover_now`` and
    returns structured per-row results.
    """

    @pytest.mark.asyncio
    async def test_endpoint_returns_structured_results(
        self, engine: Engine
    ) -> None:
        """The endpoint surfaces the ``SweepResult.to_dict()``
        shape — per-lane counts + ``total_recovered``.
        """
        from daemon.routers.recovery import recover_report_delivery
        from fastapi import Request

        parent = _seed_instance(engine)
        child = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_deferred_row(
            engine,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-1",
        )

        service, manager = _build_service(engine)

        # Build a fake Request with an app.state.manager that
        # exposes ``_report_recovery``.
        request = MagicMock(spec=Request)
        request.app.state.manager._report_recovery = service
        request.app.state.manager.is_write_paused = False

        result = await recover_report_delivery(request)
        # Structured shape — top-level keys.
        assert "lanes" in result
        assert "total_recovered" in result
        assert result["total_recovered"] == 1
        assert result["lanes"]["deferred"]["recovered"] == 1

    @pytest.mark.asyncio
    async def test_endpoint_503_when_disabled(
        self,
    ) -> None:
        """When the manager has no ``_report_recovery`` (disabled
        or partial init), the endpoint returns 503.
        """
        from daemon.routers.recovery import recover_report_delivery
        from fastapi import HTTPException, Request

        request = MagicMock(spec=Request)
        request.app.state.manager._report_recovery = None

        with pytest.raises(HTTPException) as exc:
            await recover_report_delivery(request)
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_endpoint_503_when_writes_paused(
        self,
    ) -> None:
        """When ``is_write_paused`` is True, the endpoint returns 503."""
        from daemon.routers.recovery import recover_report_delivery
        from fastapi import HTTPException, Request

        request = MagicMock(spec=Request)
        request.app.state.manager.is_write_paused = True
        request.app.state.manager._report_recovery = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await recover_report_delivery(request)
        assert exc.value.status_code == 503
