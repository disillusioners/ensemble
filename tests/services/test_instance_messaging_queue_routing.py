"""Tests for the optional ``queue_id`` parameter on
:meth:`InstanceMessagingService.enqueue_message_job`.

The HTTP message-sending API now accepts an optional ``queue_id`` so the
caller can route the JobItem mirror to a specific JobQueue instead of
the hardcoded ``system_parallel_queue``. The resolution logic lives in
``enqueue_message_job`` (the service method the HTTP route forwards to
in the NORMAL / IDLE branch) and threads the resolved
``queue_id_for_job`` into ``JobRepository.create(queue_id=...)``.

This file exercises the four contract scenarios:

1. ``queue_id=None`` (omitted) — legacy default
   ``system_parallel_queue`` is used. Validates backward compatibility.
2. ``queue_id=<valid id in project>`` — that queue is used as the
   JobItem's ``queue_id``.
3. ``queue_id=<id from different project>`` — falls back to default,
   WARNING logged, no exception raised.
4. ``queue_id=<nonexistent id>`` — falls back to default, WARNING logged,
   no exception raised.

The repository call that stamps the queue_id onto the JobItem mirror
(``JobRepository.create``) is what we observe — the assertion captures
``queue_id=...`` from the kwargs / args and verifies the resolved value.
We do NOT exercise a real DB because the resolution logic is purely a
function of the queue repository's metadata lookups; the downstream
``create`` is exercised by the existing ``tests/job_queue/`` suite.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.manager import InstanceManager
from daemon.services.instance_messaging import (
    InstanceMessagingService,
)


# ============================================================
# Helpers
# ============================================================


_PROJECT_ID = "proj-test-001"
_OTHER_PROJECT_ID = "proj-other-002"
_DEFAULT_QUEUE_ID = "default-queue-id-0001"
_VALID_QUEUE_ID = "valid-queue-id-0002"
_OTHER_PROJECT_QUEUE_ID = "other-project-queue-id-0003"
_NONEXISTENT_QUEUE_ID = "nonexistent-queue-id-9999"


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
) -> MagicMock:
    """Build a manager mock with the queue + job repositories wired
    to the supplied stubs.

    The instance repository lookup that
    :meth:`enqueue_message_job` performs to discover the instance's
    ``project_id`` is mocked to return a ``SimpleNamespace`` carrying
    the requested project id. The full ``_prepare_enqueued_message``
    prelude is monkeypatched in the test body to skip DB writes.
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
    # resolution code can pull the queue metadata and the JobItem
    # insert through their own paths.
    manager._job_queue_service = MagicMock()
    manager._job_queue_service._queue_repo = queue_repo
    manager._job_queue_service._repository = job_repo
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = MagicMock()
    manager._worker_pool = MagicMock()
    manager._worker_pool.notify_work = MagicMock()
    return manager


def _make_service(manager: MagicMock) -> InstanceMessagingService:
    """Build an :class:`InstanceMessagingService` around ``manager``,
    with the prelude and side-effect helpers stubbed so the body of
    :meth:`enqueue_message_job` runs end-to-end without touching the
    DB.

    The single observable side effect we want to capture is the
    ``queue_id`` kwarg passed to ``JobRepository.create``; the prelude
    + SSE + title-generation helpers are irrelevant to the queue
    resolution contract under test.
    """
    svc = InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )

    # Stub the prelude so the test does not require a real DB. The
    # prelude writes MessageQueue + Task rows in a single transaction;
    # the resolution logic lives AFTER the prelude, so stubbing it
    # here does not affect the queue_id routing path.
    #
    # The shape must match :class:`_PreparedEnqueueContext` (NamedTuple
    # in daemon/services/instance_messaging.py) — the resolution code
    # later reads ``ctx.message_id`` and ``ctx.task_id`` / ``ctx.work_id``
    # downstream for the JobItem linkage, so all fields must be
    # present even when the test only cares about the queue_id.
    from daemon.services.instance_messaging import _PreparedEnqueueContext

    prelude_ctx = _PreparedEnqueueContext(
        message_id="msg-stub",
        msg_type="human",
        status_changed_to_running=False,
        is_idle_to_running=False,
        instance_agent_id="leader",
        previous_status=None,
        task_id=None,
        work_id="work-id-stub",
        is_deferred=False,
    )
    svc._prepare_enqueued_message = MagicMock(return_value=prelude_ctx)
    svc._maybe_trigger_title_generation = MagicMock()
    return svc


@asynccontextmanager
async def _invoke_enqueue(
    *,
    queue_id: str | None,
    queue_repo: MagicMock,
    job_repo: MagicMock,
    project_id: str = _PROJECT_ID,
):
    """Drive :meth:`enqueue_message_job` once with the supplied
    ``queue_id`` and ``queue_repo`` / ``job_repo`` stubs, yielding
    the mocks so the test body can assert on captured calls.

    The ``project_id`` defaults to the same project the instance is
    mocked to belong to, so the same project mocks the resolved
    ``requested.project_id`` is compared against.
    """
    manager = _make_manager(
        queue_repo=queue_repo,
        job_repo=job_repo,
        project_id=project_id,
    )
    svc = _make_service(manager)

    with patch("daemon.registry.get_registry") as mock_get_registry:
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

    yield job_repo, queue_repo


def _captured_queue_id(job_repo: MagicMock) -> str | None:
    """Return the ``queue_id`` kwarg captured by the most recent
    ``JobRepository.create`` call.

    The resolution code threads the resolved id into
    ``job_repo.create(..., queue_id=<resolved>, ...)`` at line ~1546
    of daemon/services/instance_messaging.py. Pull it out of the
    captured kwargs so the caller can assert on the resolved value.
    """
    assert job_repo.create.called, "JobRepository.create was not invoked"
    _, kwargs = job_repo.create.call_args
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
        self, caplog: pytest.LogCaptureFixture
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
            ) as (captured_job_repo, captured_queue_repo):
                resolved = _captured_queue_id(captured_job_repo)

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
        self, caplog: pytest.LogCaptureFixture
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
            ) as (captured_job_repo, captured_queue_repo):
                resolved = _captured_queue_id(captured_job_repo)

        assert resolved == _DEFAULT_QUEUE_ID
        captured_queue_repo.get_by_name.assert_called_once_with(
            _PROJECT_ID, "system_parallel_queue"
        )
        captured_queue_repo.get.assert_not_called()
        assert not any(
            record.levelno >= logging.WARNING for record in caplog.records
        )

    async def test_valid_queue_id_in_project_is_used(
        self, caplog: pytest.LogCaptureFixture
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
            ) as (captured_job_repo, captured_queue_repo):
                resolved = _captured_queue_id(captured_job_repo)

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
        self, caplog: pytest.LogCaptureFixture
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
            ) as (captured_job_repo, captured_queue_repo):
                resolved = _captured_queue_id(captured_job_repo)

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
        self, caplog: pytest.LogCaptureFixture
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
            ) as (captured_job_repo, captured_queue_repo):
                resolved = _captured_queue_id(captured_job_repo)

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
        self, caplog: pytest.LogCaptureFixture
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
            ) as (captured_job_repo, captured_queue_repo):
                resolved = _captured_queue_id(captured_job_repo)

        # The default queue still wins — the repo error is folded
        # into the same fallback path and the ``get_by_name`` lookup
        # is still attempted after the ``get`` error.
        assert resolved == _DEFAULT_QUEUE_ID
        captured_queue_repo.get.assert_called_once_with(_VALID_QUEUE_ID)
        captured_queue_repo.get_by_name.assert_called_once_with(
            _PROJECT_ID, "system_parallel_queue"
        )

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
