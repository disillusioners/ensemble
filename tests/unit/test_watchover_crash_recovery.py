"""Focused tests for Watchover Phase 5 crash recovery and suspension wiring."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.stale_task_recovery import StaleTaskRecovery


def _stale_recovery(*, manager=None, grace_seconds: int = 60) -> StaleTaskRecovery:
    task_repo = MagicMock()
    task_repo.find_cancellable_tasks.return_value = []
    task_repo.find_stale_running_tasks.return_value = []
    task_repo.find_orphaned_cancelled_tasks.return_value = []
    return StaleTaskRecovery(
        task_repository=task_repo,
        message_repository=MagicMock(),
        instance_manager=manager,
        watchover_terminate_grace_seconds=grace_seconds,
    )


def test_restore_registers_graph_before_watchover_recovery_reader():
    source = inspect.getsource(InstanceLifecycleService._restore_instance)
    register_at = source.index("self._manager.instances[instance_id] =")
    recover_at = source.index("await self._recover_watchover_pending_termination")
    assert register_at < recover_at


@pytest.mark.asyncio
async def test_restore_recovery_triggers_watchover_termination_and_clears_marker():
    stale_row = SimpleNamespace(
        instance_metadata={"watchover_pending_termination": True}
    )
    repo = MagicMock()
    repo.get.return_value = stale_row
    manager = MagicMock()
    manager._instance_repository = repo
    manager.terminate_instance = AsyncMock(return_value=True)
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    service._manager = manager

    await service._recover_watchover_pending_termination("iid", stale_row)

    manager.terminate_instance.assert_awaited_once_with(
        "iid", terminal_reason="watchover_terminated"
    )
    repo.set_metadata_many.assert_called_once_with(
        "iid",
        {
            "watchover_pending_termination": False,
            "watchover_pending_termination_at": None,
        },
    )


@pytest.mark.asyncio
async def test_restore_recovery_failure_is_non_fatal_and_preserves_marker():
    stale_row = SimpleNamespace(
        instance_metadata={"watchover_pending_termination": True}
    )
    manager = MagicMock()
    manager.instances = {"iid": ("graph", "agents/coder")}
    manager._instance_repository = MagicMock()

    async def _fail_after_cleanup(instance_id, *, terminal_reason):
        manager.instances.pop(instance_id, None)
        raise RuntimeError("cascade failed")

    manager.terminate_instance = AsyncMock(side_effect=_fail_after_cleanup)
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    service._manager = manager

    await service._recover_watchover_pending_termination("iid", stale_row)

    manager._instance_repository.set_metadata_many.assert_not_called()
    assert manager.instances["iid"] == ("graph", "agents/coder")


def test_stale_marker_sweep_only_schedules_old_alive_instances():
    now = datetime.now(timezone.utc)
    old = (now - timedelta(seconds=120)).isoformat()
    recent = (now - timedelta(seconds=10)).isoformat()
    rows = [
        SimpleNamespace(
            instance_id="old-alive",
            status=InstanceStatus.RUNNING.value,
            instance_metadata={"watchover_pending_termination_at": old},
            updated_at=old,
        ),
        SimpleNamespace(
            instance_id="recent-alive",
            status=InstanceStatus.RUNNING.value,
            instance_metadata={"watchover_pending_termination_at": recent},
            updated_at=recent,
        ),
        SimpleNamespace(
            instance_id="old-terminal",
            status=InstanceStatus.TERMINATED.value,
            instance_metadata={"watchover_pending_termination_at": old},
            updated_at=old,
        ),
    ]
    manager = MagicMock()
    manager._instance_repository.find_instances_with_metadata_key.return_value = rows
    manager.terminate_instance = AsyncMock(return_value=True)
    recovery = _stale_recovery(manager=manager)

    def _close_and_accept(coro):
        coro.close()
        return True

    with patch(
        "daemon.services.main_loop_bridge.MainLoopBridge.run_async_no_wait",
        side_effect=_close_and_accept,
    ) as schedule:
        swept = recovery._sweep_watchover_terminate_markers()

    assert swept == 1
    manager._instance_repository.find_instances_with_metadata_key.assert_called_once_with(
        "watchover_pending_termination", True
    )
    manager.terminate_instance.assert_called_once_with(
        "old-alive", terminal_reason="watchover_terminated"
    )
    schedule.assert_called_once()


def test_periodic_and_startup_recovery_both_run_watchover_sweep():
    recovery = _stale_recovery()
    recovery._sweep_watchover_terminate_markers = MagicMock(return_value=0)

    assert recovery.recover_stale_tasks() == 0
    assert recovery.recover_on_startup() == 0
    assert recovery._sweep_watchover_terminate_markers.call_count == 2


def test_instance_repository_finds_boolean_metadata_value_on_sqlite():
    engine = create_engine("sqlite:///:memory:")
    Instance.__table__.create(engine)
    repo = SQLModelInstanceRepository(engine)
    repo.create(
        instance_id="pending",
        agent_id="coder",
        agent_dir="agents/coder",
        metadata={"watchover_pending_termination": True},
    )
    repo.create(
        instance_id="not-pending",
        agent_id="coder",
        agent_dir="agents/coder",
        metadata={"watchover_pending_termination": False},
    )

    matches = repo.find_instances_with_metadata_key(
        "watchover_pending_termination", True
    )

    assert [row.instance_id for row in matches] == ["pending"]


@pytest.mark.asyncio
async def test_lifecycle_pause_threads_reason_to_db_boundary():
    manager = MagicMock()
    manager._instance_repository.get_tree_root_id.return_value = "iid"
    manager._instance_repository.get_tree_ids.return_value = ["iid"]
    manager._instance_repository.get.return_value = SimpleNamespace(
        status=InstanceStatus.RUNNING.value,
        agent_id="coder",
    )
    manager._request_registry = MagicMock()
    manager._graph_tasks = {}
    manager._gii_throttle = {}
    manager._loop_breaker_state = {}
    manager.release_context_usage_cache = MagicMock()
    manager.clear_injection = MagicMock(return_value=None)
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager.engine = MagicMock()
    manager.write_guard = MagicMock()
    service = InstanceLifecycleService(manager, MagicMock())
    db_result = SimpleNamespace(
        updated_ids=["iid"],
        agent_ids_by_instance={"iid": "coder"},
    )

    with patch(
        "daemon.services.instance_lifecycle.asyncio.to_thread",
        new=AsyncMock(return_value=db_result),
    ) as to_thread:
        result = await service.pause_instance_cascade(
            "iid", suspension_reason="watchover_setup"
        )

    assert result["paused_ids"] == ["iid"]
    assert to_thread.await_args.kwargs["suspension_reason"] == "watchover_setup"


@pytest.mark.asyncio
async def test_manager_pause_facade_threads_suspension_reason():
    from daemon.manager import InstanceManager

    manager = InstanceManager.__new__(InstanceManager)
    manager._lifecycle_service = MagicMock()
    manager._lifecycle_service.pause_instance_cascade = AsyncMock(
        return_value={"paused_ids": ["iid"], "skipped_ids": []}
    )

    result = await manager.pause_instance_cascade(
        "iid", suspension_reason="watchover_setup"
    )

    assert result["paused_ids"] == ["iid"]
    manager._lifecycle_service.pause_instance_cascade.assert_awaited_once_with(
        "iid", suspension_reason="watchover_setup"
    )
