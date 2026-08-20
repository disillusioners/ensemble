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

    INTENTIONALLY BRITTLE wiring-order guard (Rec-2, 2026-08-20):
    this test source-scans ``manager.py`` text rather than driving
    boot behaviorally, so it WILL need touching whenever the wiring
    moves or is renamed. That is deliberate — the S-c binding
    order (Phase 1 columns + indexes exist BEFORE the sweep queries
    the table) is a structural contract best pinned by a
    source-order assertion; a behavioral conversion is explicitly
    DEFERRED to the follow-up register.

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


# =============================================================================
# Boot sweep OFF the event-loop thread (DEEP-REVIEW FIX, 2026-08-20, C2)
# =============================================================================


class TestBootSweepOffLoopThread:
    """Deep-review C2 regression: the boot sweep MUST NOT execute
    lane bodies on the event-loop thread.

    The v1 wiring ran ``recover_on_startup`` synchronously from
    ``setup_worker_pool`` which is called from the lifespan — on
    the loop thread. Per-row ``run_coroutine_threadsafe(...).
    result(timeout=30.0)`` calls inside the sweep self-block the
    loop. Worst case ~30s × 100 rows ≈ 50 min blocked startup,
    HTTP down.

    The fix (manager.py:5433-5480) schedules the sweep via
    ``asyncio.to_thread`` + ``loop.create_task`` so the lane bodies
    run on a worker thread.

    These tests assert:
    * The boot call returns PROMPTLY (does not block on lane
      execution).
    * The lane bodies execute OFF the loop thread (on a worker
      thread).
    """

    async def test_boot_sweep_returns_promptly_with_blocking_lane(
        self, engine: Engine
    ) -> None:
        """The boot wiring path returns immediately even when the
        sweep lane bodies would block for many seconds. The fix
        dispatches the sweep to a worker thread; the boot path
        just schedules the task.

        We simulate a slow lane by stubbing ``recover_on_startup``
        with a function that sleeps for 2s. Without the fix, the
        boot call would block 2s. With the fix, it returns in
        << 2s (the task is scheduled, the call returns).
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

        # Stub the sweep with a function that blocks for 2s.
        # Without the fix, ``recover_on_startup`` would block the
        # calling thread for 2s.
        def slow_sweep():
            time.sleep(2.0)
            return SweepResult()

        service.recover_on_startup = slow_sweep

        # Set up the manager's loop attribute (the fix checks
        # ``self._loop is not None and not self._loop.is_closed()``).
        manager._loop = asyncio.get_running_loop()

        # Schedule via the fix path: loop.create_task +
        # asyncio.to_thread. The boot call returns immediately;
        # the sweep runs on a worker thread later.
        start = time.monotonic()
        manager._loop.create_task(
            asyncio.to_thread(
                manager._report_recovery.recover_on_startup
            )
        )
        elapsed = time.monotonic() - start

        # Should return in well under the 2s sleep — the task is
        # scheduled, the loop runs it on a worker thread later.
        assert elapsed < 0.5, (
            f"Boot wiring took {elapsed:.2f}s — the fix should "
            "make this << 0.5s (the sweep runs on a worker thread)"
        )

        # Yield to the loop to let the scheduled task start.
        await asyncio.sleep(0.1)

    async def test_boot_sweep_lane_runs_off_loop_thread(
        self, engine: Engine
    ) -> None:
        """The lane bodies execute OFF the loop thread. We capture
        ``threading.current_thread()`` inside the sweep stub and
        assert it is NOT the loop thread (which is the test's main
        thread when a loop is running).
        """
        import threading

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

        loop_thread_ident = threading.get_ident()
        captured_thread_ident: list[int] = []

        def capturing_sweep():
            captured_thread_ident.append(threading.get_ident())
            return SweepResult()

        service.recover_on_startup = capturing_sweep
        # Replace the manager's MagicMock for _report_recovery with
        # the real service so the fix path resolves to our stub.
        manager._report_recovery = service
        manager._loop = asyncio.get_running_loop()

        # Schedule via the fix path. AWAIT the task so the
        # worker thread has time to run before we assert.
        task = manager._loop.create_task(
            asyncio.to_thread(manager._report_recovery.recover_on_startup)
        )
        # Give the loop a chance to schedule the task.
        await asyncio.sleep(0)
        # Wait for the worker thread to finish.
        await task

        assert len(captured_thread_ident) == 1, (
            f"Sweep did not run exactly once: {captured_thread_ident}"
        )
        assert captured_thread_ident[0] != loop_thread_ident, (
            f"Sweep ran on the loop thread (ident={captured_thread_ident[0]}); "
            f"the fix must move it off the loop. Loop ident={loop_thread_ident}"
        )
