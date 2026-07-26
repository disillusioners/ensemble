"""Tests for the optional ``queue_id`` parameter on
:meth:`InstanceMessagingService.enqueue_message_job`.

The HTTP message-sending API now accepts an optional ``queue_id`` so the
caller can route the JobItem mirror to a specific JobQueue instead of
the hardcoded ``system_parallel_queue``. The resolution logic lives in
``enqueue_message_job`` (the service method the HTTP route forwards to
in the NORMAL / IDLE branch) and threads the resolved
``queue_id_for_job`` into ``JobQueueService.enqueue(queue_id=...)``.

This file exercises the four contract scenarios:

1. ``queue_id=None`` (omitted) — legacy default
   ``system_parallel_queue`` is used. Validates backward compatibility.
2. ``queue_id=<valid id in project>`` — that queue is used as the
   JobItem's ``queue_id``.
3. ``queue_id=<id from different project>`` — falls back to default,
   WARNING logged, no exception raised.
4. ``queue_id=<nonexistent id>`` — falls back to default, WARNING logged,
   no exception raised.

The observable side effect we capture is the ``queue_id`` kwarg passed
to ``JobQueueService.enqueue`` (Phase 5 / Option B cutover: the
JobItem is now created via the queue path, not via
``JobRepository.create`` directly). The async entry point is
``manager._job_queue_service.enqueue`` (an AsyncMock in tests).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.manager import InstanceManager
from daemon.services.instance_messaging import (
    InstanceMessagingService,
)
from daemon.services.project_normalizer import normalize_project_id


# ============================================================
# Helpers
# ============================================================


_PROJECT_ID = "proj-test-001"
_OTHER_PROJECT_ID = "proj-other-002"
_DEFAULT_QUEUE_ID = "default-queue-id-0001"
_VALID_QUEUE_ID = "valid-queue-id-0002"
_OTHER_PROJECT_QUEUE_ID = "other-project-queue-id-0003"
_NONEXISTENT_QUEUE_ID = "nonexistent-queue-id-9999"


# ── Engine fixture (Option B synchronous Task contract) ─────────
# The new ``enqueue_message_job`` writes Task + MessageQueue rows
# synchronously via ``_prepare_enqueued_message``; that helper needs
# a real SQLAlchemy engine. The queue-routing tests are unit-level
# (no DB) so we provision a per-test in-memory SQLite with the
# minimal table set required.
from sqlmodel import SQLModel, create_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
def routing_engine():
    """In-memory SQLite engine with Instance + Task + MessageQueue
    + JobItem tables. The Option B synchronous Task contract
    requires ``_prepare_enqueued_message`` to write to a real DB;
    the routing tests arrange a minimal engine so that call can
    succeed while the actual queue + job repos remain mocked.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Importing the models registers them on SQLModel.metadata.
    from daemon.repositories.event.models import Event  # noqa: F401
    from daemon.repositories.instance.models import (
        Instance,  # noqa: F401
        InstanceStatus,  # noqa: F401
    )
    from daemon.repositories.job_queue.models import (  # noqa: F401
        AdmissionState,
        JobItem,
    )
    from daemon.repositories.message_queue.models import (  # noqa: F401
        MessageQueue,
        MessageStatus,
        MessageType,
    )
    from daemon.repositories.task.models import (  # noqa: F401
        Task,
        TaskStatus,
        TaskType,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _make_queue(
    queue_id: str,
    project_id: str,
    *,
    name: str = "test-queue",
) -> SimpleNamespace:
    """Build a JobQueue-shaped object with the attributes the
    resolution code reads.

    Mirrors the public attribute surface of
    :class:`daemon.repositories.job_queue.models.JobQueue` — only
    ``queue_id`` and ``project_id`` are read by the resolved code.
    """
    return SimpleNamespace(
        queue_id=queue_id,
        project_id=project_id,
        queue_name=name,
        queue_name_lower=name.lower(),
    )


def _build_queue_repo(
    *,
    by_id: dict[str, SimpleNamespace] | None = None,
    default_queue: SimpleNamespace | None = None,
    raise_on_get: Exception | None = None,
    raise_on_get_by_name: Exception | None = None,
) -> MagicMock:
    """Build a mock of :class:`JobQueueRepository` simulating the
    metadata lookups the resolution code performs.

    * ``get(queue_id)`` — id lookup, returns the matching
      :class:`SimpleNamespace` or ``None`` for unknown ids.
    * ``get_by_name(project_id, "system_parallel_queue")`` — default
      fallback lookup, returns the configured default queue.

    Either lookup can be made to raise to exercise the ``except``
    branch — but the four scenarios in this file never do, because the
    spec forbids 4xx responses and the graceful-degradation contract is
    covered by the WARNING + fallback path.
    """
    repo = MagicMock()

    def _get(queue_id: str):
        if raise_on_get is not None:
            raise raise_on_get
        return by_id.get(queue_id) if by_id else None

    def _get_by_name(project_id: str, queue_name: str):
        if raise_on_get_by_name is not None:
            raise raise_on_get_by_name
        assert queue_name == "system_parallel_queue", (
            "test bug: only default queue should be resolved by name"
        )
        return default_queue

    repo.get = MagicMock(side_effect=_get)
    repo.get_by_name = MagicMock(side_effect=_get_by_name)
    return repo


def _make_manager(
    *,
    queue_repo: MagicMock,
    job_repo: MagicMock,
    project_id: str = _PROJECT_ID,
    engine: Any = None,
) -> MagicMock:
    """Build a manager mock with the queue + job repositories wired
    to the supplied stubs.

    Phase 5 (Option B) cutover: ``enqueue_message_job`` no longer
    calls ``job_repo.create(...)`` directly. It routes through
    ``manager._job_queue_service.enqueue(...)`` (an ``AsyncMock``).
    The queue_repo and job_repo mocks are still wired in case the
    resolution code touches them, but the primary observable side
    effect is the ``queue_id`` kwarg passed to ``enqueue``.

    Option B (synchronous Task contract): ``enqueue_message_job``
    ALSO calls ``_prepare_enqueued_message(work_id=job_id)`` to
    create the ``MessageQueue`` + ``Task`` rows synchronously. That
    call needs a real engine (writes to ``message_queue`` /
    ``task`` / ``instance`` tables) — if the caller does not supply
    one, ``engine=None`` and the test should arrange a real engine
    via the ``engine`` fixture (the conftest provides one).
    """
    instance_meta = SimpleNamespace(
        instance_id="inst-1",
        agent_id="leader",
        project_id=project_id,
        instance_metadata={"project_id": project_id},
    )

    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=instance_meta)
    # ``InstanceMessagingService._job_repository`` is a property that
    # resolves to ``manager._job_queue_service._repository`` (NOT
    # ``manager._job_repository``). Both ``_queue_repo`` and
    # ``_repository`` live on the same ``_job_queue_service`` mock
    # under distinct attribute names — keep them separate so the
    # resolution code can pull the queue metadata through its own
    # path.
    manager._job_queue_service = MagicMock()
    manager._job_queue_service._queue_repo = queue_repo
    manager._job_queue_service._repository = job_repo
    # ``enqueue`` is the new dispatch entry point (Option B). It is
    # async — must be an AsyncMock so the `await` inside
    # ``enqueue_message_job`` consumes it correctly.
    fake_job = MagicMock()
    fake_job.job_id = "job-stub"
    fake_job.job_type = "message"
    fake_job.instance_id = "inst-1"
    manager._job_queue_service.enqueue = AsyncMock(return_value=fake_job)
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._worker_pool = MagicMock()
    manager._worker_pool.notify_work = MagicMock()
    # Engine + write_guard — needed by ``_prepare_enqueued_message``
    # (synchronous Task contract). If the caller does not supply an
    # engine, we attribute-error inside ``_prepare_enqueued_message``;
    # the test must arrange one or patch the helper.
    manager.engine = engine
    manager.write_guard = MagicMock()
    return manager


def _make_service(manager: MagicMock) -> InstanceMessagingService:
    """Build an :class:`InstanceMessagingService` around ``manager``.

    Option B: ``enqueue_message_job`` no longer calls
    ``_prepare_enqueued_message``; the Task + MessageQueue rows are
    created at dispatch time inside the JobProcessor message branch.
    The only observable side effect we capture is the ``queue_id``
    kwarg passed to ``JobQueueService.enqueue``.
    """
    return InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )


@asynccontextmanager
async def _invoke_enqueue(
    *,
    queue_id: str | None,
    queue_repo: MagicMock,
    job_repo: MagicMock,
    project_id: str = _PROJECT_ID,
    engine: Any = None,
):
    """Drive :meth:`enqueue_message_job` once with the supplied
    ``queue_id`` and ``queue_repo`` / ``job_repo`` stubs, yielding
    the mocks so the test body can assert on captured calls.

    The ``project_id`` defaults to the same project the instance is
    mocked to belong to, so the same project mocks the resolved
    ``requested.project_id`` is compared against.

    Option B cutover: the observable side effect is the ``queue_id``
    kwarg passed to ``JobQueueService.enqueue`` (not
    ``JobRepository.create``).

    Option B (synchronous Task contract): ``enqueue_message_job``
    ALSO calls ``_prepare_enqueued_message(work_id=job_id)`` to
    create the ``MessageQueue`` + ``Task`` rows synchronously. That
    call needs a real engine and a seeded ``Instance`` row in the
    real DB. The ``engine`` fixture provides the engine; the helper
    seeds the instance automatically.
    """
    # Seed the instance in the real DB so
    # ``_prepare_enqueued_message`` can flip the status / write
    # the Task + MessageQueue rows. If no engine is supplied, the
    # helper skips the seed (the test must arrange it itself).
    if engine is not None:
        from sqlmodel import Session
        from daemon.repositories.instance.models import (
            Instance,
            InstanceStatus,
        )
        with Session(engine) as session:
            session.add(Instance(
                instance_id="inst-1",
                agent_id="leader",
                agent_dir="/path/to/leader",
                project_id=project_id,
                status=InstanceStatus.IDLE.value,
                instance_metadata={"project_id": project_id},
            ))
            session.commit()

    manager = _make_manager(
        queue_repo=queue_repo,
        job_repo=job_repo,
        project_id=project_id,
        engine=engine,
    )
    svc = _make_service(manager)

    with patch(
        "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
    ), patch("daemon.registry.get_registry") as mock_get_registry:
        registry = MagicMock()
        registry.get_resolved = MagicMock(
            return_value=SimpleNamespace(
                id="leader",
                path="/path/to/leader",
            )
        )
        mock_get_registry.return_value = registry

        await svc.enqueue_message_job(
            instance_id="inst-1",
            message="hello",
            source="api",
            queue_id=queue_id,
        )

    yield manager._job_queue_service, queue_repo


def _captured_queue_id(job_queue_service: MagicMock) -> str | None:
    """Return the ``queue_id`` kwarg captured by the most recent
    ``JobQueueService.enqueue`` call.

    Option B: the resolution code threads the resolved id into
    ``job_queue_service.enqueue(..., queue_id=<resolved>, ...)`` at
    ``daemon/services/instance_messaging.py:1484`` (the enqueue-call
    site). Pull it out of the captured kwargs so the caller can assert
    on the resolved value.
    """
    assert job_queue_service.enqueue.called, (
        "JobQueueService.enqueue was not invoked"
    )
    _, kwargs = job_queue_service.enqueue.call_args
    return kwargs.get("queue_id")


# ============================================================
# Tests
# ============================================================


@pytest.mark.asyncio
class TestEnqueueMessageJobQueueIdResolution:
    """Verify the four contract scenarios for the optional
    ``queue_id`` parameter on ``enqueue_message_job``.
    """

    async def test_queue_id_none_uses_default_queue(
        self, caplog: pytest.LogCaptureFixture, routing_engine
    ) -> None:
        """``queue_id=None`` (omitted) — backward-compatible default
        resolution. The code resolves
        ``system_parallel_queue`` by name for the instance's
        project and stamps that id onto the JobItem mirror.

        Verifies:

        * ``queue_repo.get_by_name`` is called with the project_id +
          ``system_parallel_queue`` name.
        * ``queue_repo.get`` is NOT consulted (no caller-supplied id
          to validate).
        * The resolved default queue_id flows through to
          ``JobRepository.create(queue_id=...)``.
        * No WARNING is logged (default path is not a fallback).
        """
        default_queue = _make_queue(_DEFAULT_QUEUE_ID, _PROJECT_ID, name="system_parallel_queue")
        queue_repo = _build_queue_repo(default_queue=default_queue)
        job_repo = MagicMock()

        with caplog.at_level(logging.WARNING, logger="daemon.services.instance_messaging"):
            async with _invoke_enqueue(
                queue_id=None,
                queue_repo=queue_repo,
                job_repo=job_repo,
                engine=routing_engine,
            ) as (captured_job_queue_service, captured_queue_repo):
                resolved = _captured_queue_id(captured_job_queue_service)

        assert resolved == _DEFAULT_QUEUE_ID
        # Default resolution path: by-name lookup was used.
        captured_queue_repo.get_by_name.assert_called_once_with(
            _PROJECT_ID, "system_parallel_queue"
        )
        # No caller-supplied id → no need to validate against ``get``.
        captured_queue_repo.get.assert_not_called()
        # No fallback happened → no WARNING.
        assert not any(
            record.levelno >= logging.WARNING for record in caplog.records
        ), f"Unexpected WARNING: {[r.getMessage() for r in caplog.records]}"

    async def test_queue_id_empty_string_uses_default_queue(
        self, caplog: pytest.LogCaptureFixture, routing_engine
    ) -> None:
        """``queue_id=""`` (empty string, treated like None) — the
        truthiness check on ``queue_id.strip()`` collapses whitespace
        and empty ids onto the default path. Same assertion matrix as
        the ``None`` case.
        """
        default_queue = _make_queue(_DEFAULT_QUEUE_ID, _PROJECT_ID, name="system_parallel_queue")
        queue_repo = _build_queue_repo(default_queue=default_queue)
        job_repo = MagicMock()

        with caplog.at_level(logging.WARNING, logger="daemon.services.instance_messaging"):
            async with _invoke_enqueue(
                queue_id="",
                queue_repo=queue_repo,
                job_repo=job_repo,
                engine=routing_engine,
            ) as (captured_job_queue_service, captured_queue_repo):
                resolved = _captured_queue_id(captured_job_queue_service)

        assert resolved == _DEFAULT_QUEUE_ID
        captured_queue_repo.get_by_name.assert_called_once_with(
            _PROJECT_ID, "system_parallel_queue"
        )
        captured_queue_repo.get.assert_not_called()
        assert not any(
            record.levelno >= logging.WARNING for record in caplog.records
        )

    async def test_valid_queue_id_in_project_is_used(
        self, caplog: pytest.LogCaptureFixture, routing_engine
    ) -> None:
        """``queue_id=<valid id in project>`` — happy path. The
        supplied id is validated via ``queue_repo.get`` and the
        resolved queue's id is stamped onto the JobItem mirror.

        Verifies:

        * ``queue_repo.get`` is consulted with the supplied id.
        * ``queue_repo.get_by_name`` is NOT called — the caller-supplied
          id won, so the default fallback is skipped.
        * The resolved queue_id (the same id the caller supplied —
          ``get`` returns the row directly) is at the create site.
        * No WARNING logged.
        """
        valid_queue = _make_queue(_VALID_QUEUE_ID, _PROJECT_ID, name="custom-queue")
        default_queue = _make_queue(_DEFAULT_QUEUE_ID, _PROJECT_ID, name="system_parallel_queue")
        queue_repo = _build_queue_repo(
            by_id={_VALID_QUEUE_ID: valid_queue},
            default_queue=default_queue,
        )
        job_repo = MagicMock()

        with caplog.at_level(logging.WARNING, logger="daemon.services.instance_messaging"):
            async with _invoke_enqueue(
                queue_id=_VALID_QUEUE_ID,
                queue_repo=queue_repo,
                job_repo=job_repo,
                engine=routing_engine,
            ) as (captured_job_queue_service, captured_queue_repo):
                resolved = _captured_queue_id(captured_job_queue_service)

        # The caller-supplied id is the one that flowed through.
        assert resolved == _VALID_QUEUE_ID
        # Validation path: ``get`` was consulted.
        captured_queue_repo.get.assert_called_once_with(_VALID_QUEUE_ID)
        # Winner-takes-all: default fallback is NOT executed.
        captured_queue_repo.get_by_name.assert_not_called()
        # No fallback → no WARNING.
        assert not any(
            record.levelno >= logging.WARNING for record in caplog.records
        )

    async def test_queue_id_from_other_project_falls_back_to_default(
        self, caplog: pytest.LogCaptureFixture, routing_engine
    ) -> None:
        """``queue_id=<id from different project>`` — graceful
        degradation. The queue exists but its ``project_id`` does not
        match the instance's project, so it is rejected with a WARNING
        and the default ``system_parallel_queue`` is used.

        Verifies:

        * ``queue_repo.get`` is called with the supplied id.
        * ``queue_repo.get_by_name`` is called as the fallback.
        * The JobItem's queue_id is the resolved DEFAULT queue_id, NOT
          the supplied id.
        * A WARNING is logged mentioning the mismatch.
        """
        other_project_queue = _make_queue(
            _OTHER_PROJECT_QUEUE_ID, _OTHER_PROJECT_ID, name="other-queue"
        )
        default_queue = _make_queue(_DEFAULT_QUEUE_ID, _PROJECT_ID, name="system_parallel_queue")
        queue_repo = _build_queue_repo(
            by_id={_OTHER_PROJECT_QUEUE_ID: other_project_queue},
            default_queue=default_queue,
        )
        job_repo = MagicMock()

        with caplog.at_level(logging.WARNING, logger="daemon.services.instance_messaging"):
            async with _invoke_enqueue(
                queue_id=_OTHER_PROJECT_QUEUE_ID,
                queue_repo=queue_repo,
                job_repo=job_repo,
                engine=routing_engine,
            ) as (captured_job_queue_service, captured_queue_repo):
                resolved = _captured_queue_id(captured_job_queue_service)

        assert resolved == _DEFAULT_QUEUE_ID
        # Validation tried first; default fallback attempted second.
        captured_queue_repo.get.assert_called_once_with(_OTHER_PROJECT_QUEUE_ID)
        captured_queue_repo.get_by_name.assert_called_once_with(
            _PROJECT_ID, "system_parallel_queue"
        )
        # WARNING is the operator-visible signal that a fallback happened.
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "Expected a WARNING for wrong-project fallback"
        assert any(
            "wrong_project" in r.getMessage() for r in warnings
        ), f"WARNING did not mention wrong_project: {[r.getMessage() for r in warnings]}"
        assert any(
            _OTHER_PROJECT_QUEUE_ID in r.getMessage() for r in warnings
        ), f"WARNING did not mention the rejected id: {[r.getMessage() for r in warnings]}"

    async def test_nonexistent_queue_id_falls_back_to_default(
        self, caplog: pytest.LogCaptureFixture, routing_engine
    ) -> None:
        """``queue_id=<nonexistent id>`` — graceful degradation. The
        supplied id is not found in the repository (``get`` returns
        ``None``), so it is rejected with a WARNING and the default
        ``system_parallel_queue`` is used.

        Verifies:

        * ``queue_repo.get`` is called with the supplied id.
        * ``queue_repo.get_by_name`` is called as the fallback.
        * The JobItem's queue_id is the resolved DEFAULT queue_id.
        * A WARNING is logged mentioning ``not_found``.
        """
        default_queue = _make_queue(_DEFAULT_QUEUE_ID, _PROJECT_ID, name="system_parallel_queue")
        queue_repo = _build_queue_repo(
            by_id={},  # no rows — any id returns None
            default_queue=default_queue,
        )
        job_repo = MagicMock()

        with caplog.at_level(logging.WARNING, logger="daemon.services.instance_messaging"):
            async with _invoke_enqueue(
                queue_id=_NONEXISTENT_QUEUE_ID,
                queue_repo=queue_repo,
                job_repo=job_repo,
                engine=routing_engine,
            ) as (captured_job_queue_service, captured_queue_repo):
                resolved = _captured_queue_id(captured_job_queue_service)

        assert resolved == _DEFAULT_QUEUE_ID
        captured_queue_repo.get.assert_called_once_with(_NONEXISTENT_QUEUE_ID)
        captured_queue_repo.get_by_name.assert_called_once_with(
            _PROJECT_ID, "system_parallel_queue"
        )
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "Expected a WARNING for not-found fallback"
        assert any(
            "not_found" in r.getMessage() for r in warnings
        ), f"WARNING did not mention not_found: {[r.getMessage() for r in warnings]}"
        assert any(
            _NONEXISTENT_QUEUE_ID in r.getMessage() for r in warnings
        ), f"WARNING did not mention the rejected id: {[r.getMessage() for r in warnings]}"

    async def test_repo_get_error_falls_back_to_default(
        self, caplog: pytest.LogCaptureFixture, routing_engine
    ) -> None:
        """``queue_repo.get`` raises — the resolution code folds the
        exception into the same fallback path and falls through to
        the default queue resolution. The default wins. Verifies
        the contract does not raise any exception that the HTTP
        route would have to handle, and that a transient
        ``get`` failure does NOT skip the ``get_by_name`` fallback.
        """
        default_queue = _make_queue(_DEFAULT_QUEUE_ID, _PROJECT_ID, name="system_parallel_queue")
        queue_repo = _build_queue_repo(
            default_queue=default_queue,
            raise_on_get=RuntimeError("simulated DB outage"),
        )
        job_repo = MagicMock()

        with caplog.at_level(logging.WARNING, logger="daemon.services.instance_messaging"):
            # No exception should escape — the spec is explicit.
            async with _invoke_enqueue(
                queue_id=_VALID_QUEUE_ID,
                queue_repo=queue_repo,
                job_repo=job_repo,
                engine=routing_engine,
            ) as (captured_job_queue_service, captured_queue_repo):
                resolved = _captured_queue_id(captured_job_queue_service)

        # The default queue still wins — the repo error is folded
        # into the same fallback path and the ``get_by_name`` lookup
        # is still attempted after the ``get`` error.
        assert resolved == _DEFAULT_QUEUE_ID
        captured_queue_repo.get.assert_called_once_with(_VALID_QUEUE_ID)
        captured_queue_repo.get_by_name.assert_called_once_with(
            _PROJECT_ID, "system_parallel_queue"
        )

    async def test_project_less_instance_triggers_dispatch_bus_notify_all(
        self, routing_engine
    ) -> None:
        """A project-less instance wakes all dispatch waiters as a fallback."""
        from sqlmodel import Session
        from daemon.repositories.instance.models import Instance, InstanceStatus

        queue_repo = _build_queue_repo()
        job_repo = MagicMock()
        with Session(routing_engine) as session:
            session.add(Instance(
                instance_id="inst-1",
                agent_id="leader",
                agent_dir="/path/to/leader",
                project_id=None,
                status=InstanceStatus.IDLE.value,
                instance_metadata={"project_id": None},
            ))
            session.commit()

        manager = _make_manager(
            queue_repo=queue_repo,
            job_repo=job_repo,
            project_id=None,
            engine=routing_engine,
        )
        dispatch_bus = MagicMock()
        manager._job_queue_service._dispatch_bus = dispatch_bus
        fake_job = MagicMock()
        manager._job_queue_service.enqueue = AsyncMock(return_value=fake_job)
        svc = _make_service(manager)

        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(id="leader", path="/path/to/leader")
            )
            mock_get_registry.return_value = registry

            await svc.enqueue_message_job(instance_id="inst-1", message="hello")

        manager._job_queue_service.enqueue.assert_awaited_once()
        _, kwargs = manager._job_queue_service.enqueue.call_args
        assert kwargs["project_id"] == normalize_project_id(None)
        dispatch_bus.notify_all.assert_called_once()
        dispatch_bus.notify_new_job.assert_not_called()

    async def test_manager_wrapper_forwards_queue_id(self) -> None:
        manager = object.__new__(InstanceManager)
        manager._messaging_service = MagicMock()
        manager._messaging_service.enqueue_message_job = AsyncMock()

        await manager.enqueue_message_job(
            instance_id="inst-1",
            message="hello",
            queue_id="queue-abc",
        )

        manager._messaging_service.enqueue_message_job.assert_awaited_once_with(
            instance_id="inst-1",
            message="hello",
            source="api",
            priority=1,
            images=None,
            metadata=None,
            is_deferred=False,
            is_background=False,
            queue_id="queue-abc",
        )


# ============================================================
# W2: project-less instance dispatch_bus fallback
# ============================================================
#
# The W2 fix in ``daemon/services/instance_messaging.py`` widens the
# wakeup path for instances whose raw ``project_id`` is ``None``. The
# code tracks ``raw_project_was_none`` BEFORE normalization at
# ``instance_messaging.py:1383`` (init) / ``:1391`` (clear) and, when
# the flag stays True, falls back to
# ``dispatch_bus.notify_all()`` at ``instance_messaging.py:1553-1561``
# — because ``notify_new_job(None)`` silently early-returns on a
# project-less JobItem. This test class guards that contract:
# a project-less instance MUST wake every dispatcher waiter even when
# the underlying ``notify_new_job`` call would have been a no-op.


@pytest.mark.asyncio
class TestEnqueueMessageJobProjectlessFallback:
    """Regression test for the W2 dispatch-bus fallback.

    The test seeds a project-less instance, drives
    :meth:`InstanceMessagingService.enqueue_message_job`, and asserts
    that:

    * ``manager._job_queue_service.enqueue`` is awaited exactly once
      with ``project_id`` normalized to the system default (i.e. the
      call still completes through the queue path).
    * ``dispatch_bus.notify_all()`` is called exactly once — the W2
      fallback fires because ``raw_project_was_none`` is True.
    * ``dispatch_bus.notify_new_job`` is NOT called — that path is
      owned by ``enqueue`` internally, NOT by the W2 branch.
    """

    async def test_project_less_instance_triggers_dispatch_bus_notify_all(
        self, routing_engine
    ) -> None:
        """A project-less instance wakes all dispatch waiters as a fallback.

        Setup mirrors :class:`TestEnqueueMessageJobQueueIdResolution`:

        * ``routing_engine`` (SQLite in-memory) is required because
          ``_prepare_enqueued_message`` writes MessageQueue + Task rows
          synchronously during ``enqueue_message_job``.
        * The Instance row is seeded with ``project_id=None`` so the
          production code's ``raw_project_was_none`` flag stays True.
        * ``manager._instance_repository.get`` returns a SimpleNamespace
          mirroring the seeded Instance.
        * ``manager._job_queue_service._dispatch_bus`` is a MagicMock so
          we can assert the ``notify_all`` / ``notify_new_job`` calls.
        * ``manager._job_queue_service.enqueue`` is an AsyncMock returning
          a fake JobItem carrying ``job_id``, ``job_type="message"``,
          and ``instance_id`` attributes (the production code does not
          read these off the returned JobItem, but matching the real
          surface makes the test fail loudly if the contract drifts).

        Assertions:

        * ``enqueue`` awaited exactly once.
        * ``enqueue`` received ``project_id=normalize_project_id(None)``
          — i.e. the system default project id.
        * ``dispatch_bus.notify_all`` called exactly once (W2 fallback
          fires).
        * ``dispatch_bus.notify_new_job`` was NOT called (owned by
          ``enqueue`` internally, not by the W2 branch — verifying this
          keeps the fallback's responsibility narrowly scoped).
        """
        from sqlmodel import Session
        from daemon.repositories.instance.models import (
            Instance,
            InstanceStatus,
        )

        queue_repo = _build_queue_repo()
        job_repo = MagicMock()

        # Seed the project-less Instance row so
        # ``_prepare_enqueued_message`` (called synchronously inside
        # ``enqueue_message_job``) finds it and can update status /
        # last_activity_at without raising. ``project_id=None`` is the
        # W2 trigger condition.
        with Session(routing_engine) as session:
            session.add(Instance(
                instance_id="inst-projectless",
                agent_id="leader",
                agent_dir="/path/to/leader",
                project_id=None,
                status=InstanceStatus.IDLE.value,
                instance_metadata={"project_id": None},
            ))
            session.commit()

        # ``_make_manager(..., project_id=None)`` builds the
        # SimpleNamespace that ``manager._instance_repository.get``
        # returns — matching ``project_id=None`` here keeps the mock
        # consistent with the seeded DB row above.
        manager = _make_manager(
            queue_repo=queue_repo,
            job_repo=job_repo,
            project_id=None,
            engine=routing_engine,
        )

        # Wire the dispatch bus mock onto the job queue service. The
        # W2 fallback reaches for ``_dispatch_bus`` via getattr so the
        # MagicMock here is exactly what the production code reads.
        dispatch_bus = MagicMock()
        manager._job_queue_service._dispatch_bus = dispatch_bus

        # Replace the default ``enqueue`` AsyncMock from ``_make_manager``
        # with one that returns a fake JobItem carrying the attributes
        # the production JobItem would expose (``job_id``, ``job_type``,
        # ``instance_id``). The production code does not currently read
        # these off the return value, but matching the real surface
        # keeps the test honest if the contract ever changes.
        fake_job = MagicMock()
        fake_job.job_id = "job-projectless-stub"
        fake_job.job_type = "message"
        fake_job.instance_id = "inst-projectless"
        manager._job_queue_service.enqueue = AsyncMock(return_value=fake_job)

        svc = _make_service(manager)

        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(
                    id="leader",
                    path="/path/to/leader",
                )
            )
            mock_get_registry.return_value = registry

            await svc.enqueue_message_job(
                instance_id="inst-projectless",
                message="hello",
                source="api",
            )

        # 1. ``enqueue`` was awaited exactly once.
        manager._job_queue_service.enqueue.assert_awaited_once()

        # 2. ``enqueue`` received ``project_id`` normalized to the
        # system default — proving the project-less instance still
        # completes through the queue path (just with a synthetic
        # project id).
        _, kwargs = manager._job_queue_service.enqueue.call_args
        assert kwargs["project_id"] == normalize_project_id(None), (
            f"Expected project_id to be normalized to system default "
            f"({normalize_project_id(None)!r}), got {kwargs.get('project_id')!r}"
        )

        # 3. The W2 fallback fired — ``notify_all`` was called once so
        # every dispatcher waiter wakes up even though
        # ``notify_new_job(None)`` would have early-returned.
        dispatch_bus.notify_all.assert_called_once()

        # 4. ``notify_new_job`` is NOT called by the W2 branch — it is
        # owned by ``JobQueueService.enqueue`` internally. Asserting
        # ``assert_not_called`` here locks the responsibility split:
        # the W2 fallback is a wake-all safety net, NOT a duplicate
        # notify path.
        dispatch_bus.notify_new_job.assert_not_called()


# ============================================================
# C1: HTTP router wiring verification
# ============================================================
#
# The four scenarios above exercise the service layer directly. They
# prove that ``enqueue_message_job`` resolves the right ``queue_id`` for
# the JobItem mirror, but they do NOT prove that the FastAPI route at
# ``daemon/routers/messages.py`` actually forwards the
# ``message.queue_id`` field from the request body into the service
# call. A typo in the route (``queue_id=message.id``) or a missing
# Pydantic field on ``MessageCreate`` would silently break the feature
# for the entire HTTP surface while the service-layer tests still pass.
#
# This test follows the established router-test pattern from
# ``tests/unit/routers/test_message_status_endpoint.py`` (FastAPI app +
# router-include + middleware-injected manager + ``TestClient``) — no
# real DB, no real manager, no real LLM. The single point under test
# is the one-line wiring at
# ``daemon/routers/messages.py`` ~line 321:
#     ``queue_id=message.queue_id``
# which must thread the caller-supplied queue_id from the JSON body
# into the service call.


_SENTINEL_QUEUE_ID = "queue-test-123"
_SENTINEL_INSTANCE_ID = "inst-idle-001"
_SENTINEL_MESSAGE_ID = "msg-router-001"


def _make_idle_manager(*, enqueue_side_effect: AsyncMock) -> MagicMock:
    """Build a minimal manager mock that drives ``send_message`` down
    the NORMAL (IDLE / terminal) branch.

    Required surface for the route:

    * ``is_write_paused`` — a property returning ``False`` so the
      503 guard is skipped.
    * ``get_instance_info(instance_id)`` — returns a dict with a
      ``status`` key that is NOT in ``_INJECTION_ELIGIBLE_STATUSES``
      and NOT ``"paused"`` so the route falls through to the NORMAL
      branch and calls ``enqueue_message_job``.
    * ``enqueue_message_job`` — ``AsyncMock`` the test asserts on.
    * ``config.llm.model_vision`` — only read when the body has
      ``images``; harmless to leave as a plain attribute since the
      request body sends no images.
    """
    manager = MagicMock()

    # is_write_paused is a property on the real manager; setting it as
    # a plain attribute on the MagicMock is sufficient because the
    # route reads it via simple attribute access (no descriptor).
    manager.is_write_paused = False

    # IDLE branch — anything that is not RUNNING / WAITING_CHILDREN /
    # PAUSED falls through to the NORMAL path which is the only path
    # that forwards ``queue_id``.
    manager.get_instance_info = MagicMock(
        return_value={"status": "idle", "instance_id": _SENTINEL_INSTANCE_ID}
    )

    # The single observable side effect under test.
    manager.enqueue_message_job = enqueue_side_effect

    return manager


@pytest.fixture
def router_client_with_manager():
    """Provide a TestClient + manager-slot for the messages router.

    Mirrors the ``client_with_manager`` fixture in
    ``tests/unit/routers/test_message_status_endpoint.py`` — the same
    FastAPI + middleware pattern the project already uses for router
    tests. The middleware reads ``state["manager"]`` and writes it
    onto ``request.app.state.manager`` so the route's
    ``_get_manager(request)`` helper can find it.
    """
    from daemon.routers.messages import router

    app = FastAPI()
    app.include_router(router)
    state: dict = {"manager": None}

    @app.middleware("http")
    async def _inject_manager(request, call_next):
        request.app.state.manager = state["manager"]
        return await call_next(request)

    client = TestClient(app)
    return client, state


class TestMessageRouteQueueIdForwarding:
    """HTTP router wiring verification for the optional ``queue_id``
    field on ``POST /instances/{instance_id}/messages``.

    The service-layer tests above already cover the resolution
    contract. This class proves the FastAPI route at
    ``daemon/routers/messages.py`` actually forwards the
    ``message.queue_id`` field into the manager call. Without this
    test the ``queue_id`` parameter could be silently dropped at the
    router boundary and the production HTTP surface would lose the
    feature while service tests still pass.
    """

    def test_router_forwards_queue_id_to_enqueue_message_job(
        self, router_client_with_manager
    ) -> None:
        """``POST /instances/{instance_id}/messages`` with a body
        containing ``queue_id`` must invoke
        ``manager.enqueue_message_job`` with the same ``queue_id``
        value.

        Test mechanics:

        * The instance is set to IDLE so the request flows through the
          NORMAL branch (the only branch that calls
          ``enqueue_message_job``).
        * ``manager.enqueue_message_job`` is patched as an AsyncMock
          that returns a stub ``AsyncMessageResult`` carrying a
          ``message_id`` so the route's response-shape code does not
          blow up.
        * The HTTP response is asserted to be 200 — the NORMAL
          branch's success code. (The 202 status is reserved for the
          INJECTION branch used by RUNNING / WAITING_CHILDREN; an
          IDLE instance cannot trigger it.)
        * The AsyncMock is asserted to have been awaited exactly once
          with ``queue_id=<the sentinel value>`` — the wiring
          contract under test.
        """
        client, state = router_client_with_manager

        # The route reads ``result.message_id`` and ``result.job_id``
        # from the AsyncMessageResult returned by enqueue_message_job,
        # so the stub must expose those two attributes.
        stub_result = SimpleNamespace(
            message_id=_SENTINEL_MESSAGE_ID,
            job_id="job-router-001",
        )
        enqueue_mock = AsyncMock(return_value=stub_result)
        state["manager"] = _make_idle_manager(enqueue_side_effect=enqueue_mock)

        resp = client.post(
            f"/instances/{_SENTINEL_INSTANCE_ID}/messages",
            json={"content": "hi", "queue_id": _SENTINEL_QUEUE_ID},
        )

        # The NORMAL branch returns 200 (the 202 path is the
        # INJECTION branch, which only fires for RUNNING /
        # WAITING_CHILDREN — not reachable from an IDLE instance).
        assert resp.status_code == 200, resp.text

        # The wiring under test: the HTTP body's ``queue_id`` flowed
        # through to the service call as the ``queue_id`` kwarg.
        enqueue_mock.assert_awaited_once()
        _, kwargs = enqueue_mock.call_args
        assert kwargs.get("queue_id") == _SENTINEL_QUEUE_ID, (
            f"Router did not forward queue_id to enqueue_message_job: "
            f"expected {_SENTINEL_QUEUE_ID!r}, got kwargs={kwargs!r}"
        )
        # And the message body itself was forwarded as ``message``.
        assert kwargs.get("message") == "hi"
        assert kwargs.get("instance_id") == _SENTINEL_INSTANCE_ID

        # The response carries the message_id from the service stub
        # so downstream callers can correlate the queued job.
        body = resp.json()
        assert body["message_id"] == _SENTINEL_MESSAGE_ID
