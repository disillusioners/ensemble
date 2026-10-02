"""Scheduled tasks end-to-end integration tests (phase-5 §Task 3).

Excluded by default; run via ``pytest -m integration`` or the
``scheduled_tasks_acceptance`` pack (tests/packs/scheduled_tasks_acceptance.sh).

What is REAL here (the phase-5 verification tier):
  * a real ``InstanceManager`` built via the chat-source harness
    (``wire_manager_only`` — no LLM, no worker pool: this surface creates
    JobItem/Task/MessageQueue rows and never executes a graph turn);
  * the REAL shared ``scheduling_service`` mounted off the manager
    (create/cancel against the real ``source_configs`` table);
  * the REAL ``SourceRegistry`` adapter build/start/cancel path;
  * the REAL inline message-Job dispatch:
    ``SchedulerAdapter._route_via_job_queue`` → ``InstanceMapper`` →
    ``InstanceManager.enqueue_message_job`` →
    ``InstanceMessagingService.enqueue_message_job`` →
    ``JobQueueService.enqueue`` — JobItem + Task + MessageQueue rows on
    the harness SQLite engine.

STUB SEAM (one, documented): ``manager.spawn_instance_with_mcp`` is
stubbed to return a pre-seeded Instance row id. One-shot schedules are
FORCED to ``new_instance`` (adapter ``__init__``), so the mapper takes the
force-new path and would otherwise drive the full graph-stack spawn. The
stub is the only non-production line between the schedule row and the
JobItem mirror; the single-uuid contract under test lives entirely
downstream of it.

SKELETON ADAPTATIONS vs the frozen phase5-plan §Task 3 skeleton:
  * ``await build_live_pool_manager()`` → the LANDED harness API is the
    SYNC context manager ``wire_manager_only(engine)`` (no pool needed —
    nothing in this surface claims a worker thread);
  * ``manager.job_queue_service.repository.get_by_source("scheduler")``
    (Risk-7) — VERIFIED ABSENT: symbol grep over ``daemon/repositories/``
    finds ``get_by_work_id`` (``task/repository.py:410``) but NO
    ``get_by_source`` anywhere. Adapted to the real API: JobItems are read
    via ``JobRepository.list(job_types=["message"])`` + ``source``
    equality; the Task side via ``TaskRepository.get_by_work_id``.
  * ``manager._source_repository.list_schedule_executions(id, limit=100)``
    — verified REAL (``source/repository.py:644``).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock

import pytest

from tests.integration.chat_source_harness import (
    build_chat_source_engine,
    wire_manager_only,
)

pytestmark = pytest.mark.integration

FUTURE_RUN_AT = "2030-01-15T09:00:00+00:00"


async def _await_until(predicate, timeout: float = 10.0, interval: float = 0.02) -> bool:
    """ASYNC variant of the harness ``wait_until``.

    The harness helper is a sync busy-loop (``time.sleep``) — correct for
    the thread-based chat-source tests, but in an ``async def`` test it
    BLOCKS the event loop, so any task it waits on (e.g. the supervisor
    task running ``adapter.start()``) can never advance. This variant
    awaits between predicate checks.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())


# ---------------------------------------------------------------------------
# Wiring helpers (mirror tests/integration/test_chat_source_live_injection_e2e.py)
# ---------------------------------------------------------------------------


def _wire_job_stack(engine, manager):
    """Surgically wire the job stack onto the harness manager.

    ``wire_manager_only`` skips the heavy ``initialize()`` lifespan, so
    ``manager._job_queue_service`` is None — the durable dispatch path
    would crash inside ``_job_queue_service.enqueue(...)`` after the
    Task/MessageQueue rows are written. Mirrors the production
    api.py wiring order (repos → lock manager → service → set_event_loop
    → set_job_queue_service).
    """
    from daemon.repositories import (
        create_job_queue_repository,
        create_job_repository,
    )
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.services.job_lock_manager import JobLockManager
    from daemon.services.job_queue_mgmt_service import JobQueueMgmtService
    from daemon.services.job_queue_service import JobQueueService

    job_repo = create_job_repository(engine=engine, create_tables=True)
    queue_repo = create_job_queue_repository(engine=engine, create_tables=True)
    lock_repo = LockRepository(engine=engine)
    service = JobQueueService(
        repository=job_repo,
        lock_manager=JobLockManager(lock_repo=lock_repo),
        queue_repo=queue_repo,
        instance_manager=manager,
    )
    service.set_event_loop(asyncio.get_running_loop())
    manager.set_job_queue_service(service)

    mgmt = JobQueueMgmtService(
        queue_repo=queue_repo,
        job_repo=job_repo,
        task_repo=getattr(manager, "_task_repo", None),
    )
    system_project_id = manager._project_repository.ensure_system_default_project()
    return job_repo, queue_repo, mgmt, system_project_id


def _scheduler_jobitems(job_repo):
    """All message-JobItems sourced from the scheduler (Risk-7 adaptation —
    no ``get_by_source`` exists on the repository; ``list(job_types=[...])``
    + source equality is the real API)."""
    jobs, _total = job_repo.list(job_types=["message"], limit=1000)
    return [j for j in jobs if j.source == "scheduler"]


async def _provision_queues(mgmt, system_project_id):
    from daemon.services.job_queue_mgmt_service import JobQueueMgmtService

    assert isinstance(mgmt, JobQueueMgmtService)
    await mgmt.auto_provision_system_queues(system_project_id)


def _seed_instance(engine, instance_id: str, agent_id: str, project_id: str) -> None:
    from sqlmodel import Session

    from daemon.repositories.instance.models import Instance

    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir=f"/agents/{agent_id}",
                status="running",
                project_id=project_id,
            )
        )
        s.commit()


# ---------------------------------------------------------------------------
# Task 3.1 — happy path + D4 single-uuid contract
# ---------------------------------------------------------------------------


async def test_one_shot_schedule_creates_job_item(tmp_path):
    """schedule → dispatch → JobItem created with the D4 single-uuid contract.

    The load-bearing assertion is IDENTITY: ``JobItem.job_id == Task.work_id``
    (one UUID minted in ``enqueue_message_job`` and bound as the Task's
    work_id with ``work_id_required=True``) — asserted as equality AND as a
    single shared value across exactly one JobItem and one Task row.
    """
    engine = build_chat_source_engine(str(tmp_path / "e2e-happy.db"))
    with wire_manager_only(engine) as manager:
        job_repo, _queue_repo, mgmt, system_project_id = _wire_job_stack(engine, manager)
        await _provision_queues(mgmt, system_project_id)

        seeded_instance = f"inst-{uuid.uuid4().hex[:12]}"
        _seed_instance(engine, seeded_instance, "ari", system_project_id)
        # One-shot schedules force new_instance → the mapper takes the
        # spawn path. Stub ONLY the graph-stack spawn (see module docstring);
        # the mapping row + everything downstream is real.
        manager.spawn_instance_with_mcp = AsyncMock(return_value=seeded_instance)

        svc = manager.scheduling_service
        created = await svc.create_schedule(
            {
                "label": "int-happy-1",
                "agent": "ari",
                "message": "fire the scheduled task",
                "project_id": system_project_id,
                "when": FUTURE_RUN_AT,
                "timezone": "UTC",
                "recurrence": "once",
                "priority": 5,
                "instance_mode": "new_instance",
            },
            caller_instance_id="integration-test",
            caller_agent_id="tester",
        )
        # LANDED repo: source_id is a MINTED UUID; the label lives in the
        # name column (create passes no explicit source_id).
        assert created.label == "int-happy-1"
        uuid.UUID(created.source_id)  # minted uuid, not the label
        assert created.next_run_at_utc is not None

        try:
            # The create flow started a live adapter (best-effort start).
            # start_adapter is a supervisor-task launch — wait for the
            # adapter's own start() to complete (semaphore created) before
            # driving the trigger path.
            await _await_until(
                lambda: (
                    (a := manager.source_registry.get(created.source_id)) is not None
                    and a._execution_semaphore is not None
                ),
                timeout=10,
            )
            adapter = manager.source_registry.get(created.source_id)
            assert adapter is not None, "created one-shot schedule must have a live adapter"
            # Drive the REAL scheduled-trigger path (semaphore → _execute_run
            # → _route_via_job_queue) without waiting out the wall clock.
            await adapter._emit_scheduled_message()

            await _await_until(
                lambda: len(_scheduler_jobitems(job_repo)) > 0,
                timeout=10,
            )
            items = _scheduler_jobitems(job_repo)
            assert len(items) == 1, f"expected exactly ONE JobItem (D4), got {len(items)}"
            item = items[0]
            assert item.job_type == "message"
            assert item.source == "scheduler"
            assert item.instance_id == seeded_instance

            # ── THE single-uuid contract (assert identity, not presence) ──
            from daemon.repositories.task.repository import TaskRepository

            task_repo = TaskRepository(engine=engine)
            task = task_repo.get_by_work_id(item.job_id)
            assert task is not None, "Task row must exist for the dispatched job"
            assert task.work_id == item.job_id, (
                f"single-uuid contract violated: Task.work_id={task.work_id!r} "
                f"!= JobItem.job_id={item.job_id!r}"
            )
            # ONE uuid end-to-end: the shared value is a real uuid and the
            # Task lookup by that uuid resolves to exactly the same row.
            uuid.UUID(item.job_id)  # raises if the mirror id is not a uuid
            assert task_repo.get_by_work_id(item.job_id).work_id == item.job_id
        finally:
            await manager.source_registry.stop_all()


# ---------------------------------------------------------------------------
# Task 3.2 — cancel prevents dispatch (D5 terminal)
# ---------------------------------------------------------------------------


async def test_cancelled_schedule_never_dispatches(tmp_path):
    """D5: cancel is terminal — the adapter is evicted (nothing left to
    trigger), no JobItem lands, history stays queryable, and the §5.3
    ``last_execution_id`` echo is present on the cancel response."""
    engine = build_chat_source_engine(str(tmp_path / "e2e-cancel.db"))
    with wire_manager_only(engine) as manager:
        job_repo, _queue_repo, mgmt, system_project_id = _wire_job_stack(engine, manager)
        await _provision_queues(mgmt, system_project_id)
        _seed_instance(engine, f"inst-{uuid.uuid4().hex[:12]}", "ari", system_project_id)
        manager.spawn_instance_with_mcp = AsyncMock(return_value="inst-unused")

        svc = manager.scheduling_service
        created = await svc.create_schedule(
            {
                "label": "int-cancel-1",
                "agent": "ari",
                "message": "should never fire",
                "project_id": system_project_id,
                "when": FUTURE_RUN_AT,
                "timezone": "UTC",
                "recurrence": "once",
            },
            caller_instance_id="integration-test",
            caller_agent_id="tester",
        )
        assert manager.source_registry.get(created.source_id) is not None

        cancel = await svc.cancel_schedule(created.source_id)
        assert cancel.status == "cancelled"
        assert cancel.cancelled_at
        assert cancel.last_execution_id is None  # no executions yet — echo is None

        # Manual-trigger attempt: the adapter is EVICTED (§5.2 evict-before-
        # stop) — there is no adapter left to trigger.
        assert manager.source_registry.get(created.source_id) is None, (
            "cancelled schedule must be evicted from the registry"
        )

        # Nothing dispatches.
        await asyncio.sleep(0.3)
        assert _scheduler_jobitems(job_repo) == []

        # Row is terminal cancelled (NOT deleted).
        row = manager._source_repository.get_source_config(created.source_id)
        assert row is not None
        assert row.status == "cancelled"
        assert row.enabled is False

        # History queryable (preserved — possibly empty for a fresh schedule).
        history = manager._source_repository.list_schedule_executions(
            created.source_id, limit=100
        )
        assert isinstance(history, list)

        # A second cancel is refused (terminal): ValueError → caller maps 409.
        with pytest.raises(ValueError, match="already cancelled"):
            await svc.cancel_schedule(created.source_id)
        await manager.source_registry.stop_all()


# ---------------------------------------------------------------------------
# Task 3.2b — stop does not clobber cancelled (architecture §3.3)
# ---------------------------------------------------------------------------


async def test_stop_adapter_does_not_clobber_cancelled(tmp_path):
    """A ``stop_adapter`` landing on a CANCELLED row must NOT resurrect it to
    STOPPED (the clobber guard) — and must still evict the stale adapter."""
    engine = build_chat_source_engine(str(tmp_path / "e2e-clobber.db"))
    with wire_manager_only(engine) as manager:
        _job_repo, _queue_repo, mgmt, system_project_id = _wire_job_stack(engine, manager)
        await _provision_queues(mgmt, system_project_id)

        svc = manager.scheduling_service
        created = await svc.create_schedule(
            {
                "label": "int-clobber-1",
                "agent": "ari",
                "message": "m",
                "project_id": system_project_id,
                "when": FUTURE_RUN_AT,
                "timezone": "UTC",
                "recurrence": "once",
            },
            caller_instance_id="integration-test",
            caller_agent_id="tester",
        )
        await svc.cancel_schedule(created.source_id)
        assert (
            manager._source_repository.get_source_config(created.source_id).status
            == "cancelled"
        )

        # Re-materialize a live adapter for the cancelled row (the race the
        # guard exists for: an adapter reaching stop AFTER the cancel landed).
        registry = manager.source_registry
        row = manager._source_repository.get_source_config(created.source_id)
        stale_adapter = await registry._create_adapter_from_config(row)
        registry.register(stale_adapter)

        ok = await registry.stop_adapter(created.source_id)  # persist_status=True
        assert ok is True

        # THE guard: status stayed cancelled — NOT resurrected to stopped.
        assert (
            manager._source_repository.get_source_config(created.source_id).status
            == "cancelled"
        )
        # And the stale adapter is still evicted.
        assert registry.get(created.source_id) is None
        await manager.source_registry.stop_all()


# ---------------------------------------------------------------------------
# Task 3.2c — cancelled rows never boot-start (architecture §3.4)
# ---------------------------------------------------------------------------


async def test_cancelled_never_boot_starts(tmp_path):
    """Boot filter: ``start_all`` must skip CANCELLED rows — no adapter is
    created for them (registry.py boot filter, ``status in {STOPPED, CANCELLED}``)."""
    engine = build_chat_source_engine(str(tmp_path / "e2e-boot.db"))
    with wire_manager_only(engine) as manager:
        _job_repo, _queue_repo, mgmt, system_project_id = _wire_job_stack(engine, manager)
        await _provision_queues(mgmt, system_project_id)

        svc = manager.scheduling_service
        created = await svc.create_schedule(
            {
                "label": "int-boot-1",
                "agent": "ari",
                "message": "m",
                "project_id": system_project_id,
                "when": FUTURE_RUN_AT,
                "timezone": "UTC",
                "recurrence": "once",
            },
            caller_instance_id="integration-test",
            caller_agent_id="tester",
        )
        # Drop the create-time adapter, then cancel.
        await manager.source_registry.stop_adapter(created.source_id)
        await svc.cancel_schedule(created.source_id)

        # Simulate boot: load all rows from the DB. The only row is the
        # cancelled one — it must be filtered out.
        await manager.source_registry.start_all()
        try:
            assert manager.source_registry.get(created.source_id) is None, (
                "boot must not create an adapter for a cancelled row"
            )
        finally:
            # Cancels any pending autostart tasks scheduled by start_all.
            await manager.source_registry.stop_all()


# ---------------------------------------------------------------------------
# Task 3.3 — catch-up beyond lateness cap (D3, integration side)
# ---------------------------------------------------------------------------


async def test_one_shot_beyond_cap_no_dispatch_with_reason(tmp_path, monkeypatch):
    """D3: a one-shot past-due beyond the cap records a SKIPPED row with the
    lateness reason, dispatches NO JobItem, and stays ARMED (never disabled,
    never cancelled)."""
    monkeypatch.setenv("ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS", "60")

    engine = build_chat_source_engine(str(tmp_path / "e2e-catchup.db"))
    with wire_manager_only(engine) as manager:
        job_repo, _queue_repo, mgmt, system_project_id = _wire_job_stack(engine, manager)
        await _provision_queues(mgmt, system_project_id)

        svc = manager.scheduling_service
        past_run_at = (datetime.now(dt_timezone.utc) - timedelta(hours=2)).isoformat()
        created = await svc.create_schedule(
            {
                "label": "int-catchup-1",
                "agent": "ari",
                "message": "too late to fire",
                "project_id": system_project_id,
                "when": past_run_at,
                "timezone": "UTC",
                "recurrence": "once",
            },
            caller_instance_id="integration-test",
            caller_agent_id="tester",
        )

        try:
            # The create-time start runs the loop; the first iteration hits
            # the cap and records the skip. No manual trigger — the loop owns it.
            await _await_until(
                lambda: len(
                    manager._source_repository.list_schedule_executions(created.source_id)
                )
                >= 1,
                timeout=10,
            )
            history = manager._source_repository.list_schedule_executions(
                created.source_id, limit=10
            )
            assert len(history) >= 1
            skipped = [e for e in history if e.status == "skipped"]
            assert skipped, f"expected a SKIPPED row, got {[e.status for e in history]}"
            assert any(
                "past_lateness_cap" in (e.error_message or "") for e in skipped
            ), f"expected the lateness-cap reason marker, got {[e.error_message for e in skipped]}"

            # NO JobItem dispatched.
            await asyncio.sleep(0.3)
            assert _scheduler_jobitems(job_repo) == []

            # The schedule stays ARMED (ADR-003): enabled, not cancelled.
            row = manager._source_repository.get_source_config(created.source_id)
            assert row.enabled is True
            assert row.status != "cancelled"
        finally:
            # Stop the retry loop (it re-records skips every ERROR_RETRY_S).
            await manager.source_registry.stop_adapter(created.source_id)
            await manager.source_registry.stop_all()
