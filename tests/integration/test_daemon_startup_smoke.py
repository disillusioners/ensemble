#!/usr/bin/env python
"""Startup smoke test for the C1 bugfix.

Verifies the daemon's startup sequence completes without the
``AttributeError: 'InstanceManager' object has no attribute '_task_repo'``
crash that was caused by ``set_task_repository()`` being called from
``initialize()`` before ``self._task_repo`` was assigned in
``setup_worker_pool()``.

The test:
  1. Creates an ``InstanceManager`` from the real ``config.yaml``.
  2. Awaits ``manager.initialize()`` — pre-fix this raised
     ``AttributeError`` on ``self._task_repo`` because
     ``MaintenanceService.set_task_repository(self._task_repo)`` was
     called before ``self._task_repo`` was assigned.
  3. Calls ``manager.setup_worker_pool()`` — post-fix this is where the
     wiring happens, immediately after ``self._task_repo = task_repo``.
  4. Verifies ``self._maintenance_service._task_repository`` is wired.

No messages are sent, no workers are started; the test exits as soon as
the wiring path completes successfully.
"""

import pytest

from daemon.manager import InstanceManager


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_startup_wires_task_repository_without_attribute_error(integration_config):
    """Verify initialize() + setup_worker_pool() sequence completes."""
    manager = InstanceManager(integration_config)

    # Pre-fix: this raised AttributeError on self._task_repo because
    # MaintenanceService.set_task_repository(self._task_repo) was called
    # in initialize() before _task_repo was assigned.
    await manager.initialize()

    try:
        # Post-fix: setup_worker_pool() assigns self._task_repo and then
        # calls set_task_repository(self._task_repo) immediately after.
        # NOTE: WorkerPool with num_workers=0 still spawns the
        # heartbeat / coordinator threads that stop on shutdown.
        manager.setup_worker_pool(num_workers=0)

        # Verify the wiring actually happened — _task_repo must exist
        # and the maintenance service must hold the same reference.
        assert manager._task_repo is not None, (
            "setup_worker_pool() must assign self._task_repo"
        )
        assert (
            manager._maintenance_service._task_repository is manager._task_repo
        ), (
            "MaintenanceService._task_repository must be wired to "
            "manager._task_repo by setup_worker_pool()"
        )
    finally:
        # shutdown_worker_pool() stops the worker pool threads and the
        # stale-task recovery thread; without this the test process hangs.
        try:
            manager.shutdown_worker_pool()
        except Exception:
            pass