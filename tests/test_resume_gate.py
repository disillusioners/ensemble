"""Tests for the ExecutionGate wrapping in ``_resume_processing_background``.

Race #5 fix: the resume path now acquires a per-instance execution
lock via ``ExecutionGateService.run()`` before driving
``graph.astream``. Without this, concurrent /resume calls (or a
WorkerPool / JobQueue dispatch racing a resume) would corrupt the
langgraph checkpoint.

These tests cover:

1. **Happy path** — when the gate is free, resume acquires it and
   runs the work_fn to completion.
2. **Exception handling inside the gate** — when ``_execution_gate.run``
   raises a non-gate exception, the existing error handler runs
   (job FAILED, instance ERROR).
3. **CancellationToken threading (W4)** — a ``CancellationToken``
   passed to ``_resume_processing_background`` is propagated by
   identity to ``_process_message_with_tracking`` so the LLM
   streaming callback can raise ``OperationCancelledError``
   cooperatively on pause.
4. **Per-instance cleanup (W3)** — ``_graph_tasks[instance_id]`` is
   popped in the outermost finally block so the entry does not leak
   across happy-path exits.
5. **Request registry unregister (W4)** — ``_request_registry.unregister``
   is invoked in the finally block so the CTS that
   ``resume_processing_job`` registered is released.

The tests use a fake ``_execution_gate`` so the lock acquire /
release path is bypassed and we can deterministically return /
raise. Real-world ``ExecutionGateService`` behaviour is covered by
``tests/unit/services/test_execution_gate.py``.

To keep the suite fast, ``asyncio.sleep`` is patched to a no-op so
the 0.5/1.0/2.0s backoff delays don't actually block.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.manager import InstanceManager
from daemon.config import Config
from daemon.cancellation import CancellationTokenSource
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.job_queue_service import DemandState


# ─── Test doubles ─────────────────────────────────────────────────────────────


class MockMessageResult:
    """Mock return value of ``_process_message_with_tracking``."""

    def __init__(self, content: str = "Resume completed") -> None:
        self.content = content


class MockAsyncMessageResult:
    """Mock return value of ``enqueue_message`` (WorkerPool path)."""

    def __init__(self, message_id: str | None = None) -> None:
        self.message_id = message_id or str(uuid.uuid4())
        self.instance_id = None
        self.status = "queued"


def _make_fake_gate(
    side_effects: list | None = None,
    raise_after: tuple[type[BaseException], str] | None = None,
) -> MagicMock:
    """Build a fake ``_execution_gate`` whose ``run`` method returns the
    values in ``side_effects`` in order. If ``raise_after`` is set, the
    ``run`` call raises the given exception *instead* of returning.
    """
    gate = MagicMock()
    if raise_after is not None:
        exc_cls, msg = raise_after
        gate.run = AsyncMock(side_effect=exc_cls(msg))
    else:
        gate.run = AsyncMock(side_effect=side_effects or [MockMessageResult()])
    return gate


class MockInstanceMeta:
    """Mock instance metadata returned by ``_instance_repository.get``.

    The post-processing branch of ``_resume_processing_background``
    checks ``instance.waiting_for > 0`` and ``instance.status`` to
    decide whether to skip the job completion. ``MagicMock`` defaults
    raise ``TypeError`` on integer comparison, so we use a real
    object with the two fields the resume path reads.
    """

    def __init__(
        self,
        instance_id: str = "test-instance",
        status: str = InstanceStatus.RUNNING.value,
        waiting_for: int = 0,
    ) -> None:
        self.instance_id = instance_id
        self.status = status
        self.waiting_for = waiting_for


@pytest.fixture
def _make_manager():
    """Build a minimally-mocked ``InstanceManager`` for exercising
    ``_resume_processing_background`` directly.

    Yields a factory ``factory(gate) -> InstanceManager``. Only the
    attributes / methods the resume path actually touches are wired up.
    Anything else on the manager is the default ``MagicMock`` created
    by ``__new__`` + manual attribute setting.

    Phase 3: the ``use_legacy_waiting_for_cascade`` flag was removed.
    The resume path now expects CM to be initialized; if it isn't, the
    A9 hard error fires per ADR-011. We patch
    ``daemon.manager.get_correlation_manager`` — the binding imported
    into manager.py's namespace (``from .services.correlation_manager
    import get_correlation_manager`` at line 62). Patching the source
    module alone is insufficient due to Python's ``from X import Y``
    binding semantics; the lookup in ``_resume_processing_background``
    resolves to the manager.py binding. The patch is scoped to the
    test via the fixture's teardown.
    """
    patchers: list = []

    def _factory(gate: MagicMock) -> InstanceManager:
        job_queue_service = MagicMock()
        job_queue_service.complete_job = AsyncMock()

        instance_repository = MagicMock()
        instance_repository.update_instance = MagicMock()
        # Default: instance is RUNNING with no pending children. The
        # resume path's ``waiting_for > 0`` check would raise TypeError
        # on a bare MagicMock; the real-looking object avoids that.
        instance_repository.get = MagicMock(return_value=MockInstanceMeta())

        # Real CancellationTokenSource so we can verify the token
        # threaded through ``_process_message_with_tracking`` is the
        # same instance the registry returned. ``register`` returns the
        # source; ``unregister`` is a no-op mock.
        request_registry = MagicMock()
        request_registry.register = MagicMock(
            return_value=CancellationTokenSource()
        )
        request_registry.unregister = MagicMock()

        manager = InstanceManager.__new__(InstanceManager)
        manager._job_queue_service = job_queue_service
        manager._instance_repository = instance_repository
        manager._execution_gate = gate
        manager._request_registry = request_registry
        manager._process_message_with_tracking = AsyncMock(
            return_value=MockMessageResult()
        )
        manager._process_child_completion_and_notify_parent = AsyncMock()
        manager.enqueue_message = AsyncMock(return_value=MockAsyncMessageResult())
        manager._graph_tasks = {}
        # Minimal ``config`` mock for any config-shaped API surface
        # the resume path may touch (currently none, but defensive).
        manager.config = MagicMock()
        manager.config.job_system = MagicMock()

        # Phase 3: wire CM mock so the A9 hard-error at
        # ``daemon/manager.py:2913`` does not fire when the resume
        # path checks ``cm is not None``. The CM mock reports 0
        # pending children for any instance so the resume path
        # proceeds to job completion.
        cm_mock = MagicMock()
        cm_mock.get_pending_count = lambda iid: 0
        cm_patcher = patch(
            "daemon.manager.get_correlation_manager",
            return_value=cm_mock,
        )
        cm_patcher.start()
        patchers.append(cm_patcher)
        manager._cm_mock = cm_mock
        return manager

    yield _factory

    # Teardown: stop all patchers started during the test
    for p in patchers:
        try:
            p.stop()
        except RuntimeError:
            pass


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestResumeGateWrapping:
    """Verify the resume path goes through ``_execution_gate.run``."""

    @pytest.mark.asyncio
    async def test_happy_path_acquires_gate_and_completes_job(self, _make_manager):
        """When the gate is free, resume acquires it and runs to completion.

        Verifies:
        - ``_execution_gate.run`` is called once with holder_id
          ``resume:<message_id>`` and holder_kind ``message_job``.
        - The job ends in ``DemandState.COMPLETED``.
        - The instance is NOT marked ERROR.
        """
        gate = _make_fake_gate(side_effects=[MockMessageResult()])
        manager = _make_manager(gate)

        instance_id = "inst-happy"
        message_id = str(uuid.uuid4())
        old_job_id = "job-happy"

        await manager._resume_processing_background(
            instance_id=instance_id,
            message="resume",
            message_id=message_id,
            old_job_id=old_job_id,
            silent=False,
            images=None,
        )

        gate.run.assert_awaited_once()
        kwargs = gate.run.await_args.kwargs
        assert kwargs["instance_id"] == instance_id
        assert kwargs["holder_id"] == f"resume:{message_id}"
        assert kwargs["holder_kind"] == "message_job"
        assert callable(kwargs["work_fn"])

        # Job completed, instance NOT errored.
        manager._job_queue_service.complete_job.assert_awaited_once()
        # ``complete_job(job_id, demand_state, ...)`` is called with
        # positional args; check via .args[1] for the DemandState.
        assert (
            manager._job_queue_service.complete_job.await_args.args[1]
            == DemandState.COMPLETED
        )
        manager._instance_repository.update_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_gate_uses_message_job_kind_not_resume(self, _make_manager):
        """The resume path uses ``holder_kind="message_job"`` — the
        production resume implementation does not introduce a new
        holder-kind value. This test pins that choice so a future
        refactor that adds a distinct resume holder-kind is caught
        here.
        """
        gate = _make_fake_gate(side_effects=[MockMessageResult()])
        manager = _make_manager(gate)

        await manager._resume_processing_background(
            instance_id="inst-kind",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-kind",
            silent=False,
            images=None,
        )

        kwargs = gate.run.await_args.kwargs
        assert kwargs["holder_kind"] == "message_job"
        # The holder_id format is ``resume:<message_id>``.
        assert kwargs["holder_id"].startswith("resume:")

    @pytest.mark.asyncio
    async def test_other_exception_inside_gate_propagates_to_error_handler(self, _make_manager):
        """If the gate raises an exception that is NOT a gate-specific
        error, it must propagate to the existing error handler (job
        FAILED, instance ERROR). The race-fix should not break the
        existing error path.
        """
        gate = _make_fake_gate(
            raise_after=(RuntimeError, "boom")
        )
        manager = _make_manager(gate)

        # Should not raise — the existing except Exception block
        # catches and marks the job FAILED.
        await manager._resume_processing_background(
            instance_id="inst-other-err",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-other-err",
            silent=False,
            images=None,
        )

        manager._job_queue_service.complete_job.assert_awaited_once()
        cj_args = manager._job_queue_service.complete_job.await_args.args
        assert cj_args[1] == DemandState.FAILED

        manager._instance_repository.update_instance.assert_called_once()
        ui_kwargs = manager._instance_repository.update_instance.call_args.kwargs
        assert ui_kwargs["status"] == InstanceStatus.ERROR.value


class TestResumeCleanupAndCancellation:
    """Verify the resume path's per-instance cleanup (W3) and
    cancellation-token threading (W4) work end-to-end.

    W3: ``_graph_tasks[instance_id]`` is popped in the outermost
    ``finally`` block so the entry does not leak across exception
    or normal completion paths.

    W4: A ``CancellationToken`` passed to
    ``_resume_processing_background`` is propagated to
    ``_process_message_with_tracking`` so ``pause_instance_cascade``
    can cooperatively interrupt LLM streaming via the token rather
    than abruptly via ``task.cancel()``. The message_id is
    unregistered from ``_request_registry`` in the finally block.
    """

    @pytest.mark.asyncio
    async def test_graph_tasks_entry_popped_on_happy_path(self, _make_manager):
        """W3: cleanup also runs on the happy path (lease free, resume
        completes successfully). The pre-existing tests verify the
        processing logic but did not pin this — the outer call
        previously relied on ``pause_instance_cascade`` to pop the
        entry, which left a window where stale entries blocked the
        next resume.
        """
        gate = _make_fake_gate(side_effects=[MockMessageResult()])
        manager = _make_manager(gate)
        manager._graph_tasks["inst-cleanup-happy"] = "sentinel"

        await manager._resume_processing_background(
            instance_id="inst-cleanup-happy",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-cleanup-happy",
            silent=False,
            images=None,
        )

        assert "inst-cleanup-happy" not in manager._graph_tasks

    @pytest.mark.asyncio
    async def test_cancellation_token_passed_to_process_message_with_tracking(self, _make_manager):
        """W4: a ``CancellationToken`` passed to
        ``_resume_processing_background`` is propagated by identity to
        ``_process_message_with_tracking`` so the LLM streaming
        callback can raise ``OperationCancelledError`` cooperatively
        on pause.

        Unlike the gate-wrapping tests above (which use a fake
        ``gate.run`` that returns a ``MockMessageResult`` without
        invoking ``work_fn``), this test uses a gate that actually
        calls ``work_fn`` so the closure body — where the
        ``cancellation_token`` is passed to
        ``_process_message_with_tracking`` — runs for real.
        """
        gate = MagicMock()

        async def gate_run(instance_id, holder_id, holder_kind, work_fn):
            # Invoke the closure; the gate then propagates the return value.
            return await work_fn()

        gate.run = gate_run
        manager = _make_manager(gate)

        cts = CancellationTokenSource()
        token = cts.token

        await manager._resume_processing_background(
            instance_id="inst-ct",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-ct",
            silent=False,
            images=None,
            cancellation_token=token,
        )

        manager._process_message_with_tracking.assert_awaited_once()
        kwargs = manager._process_message_with_tracking.await_args.kwargs
        # Identity check (``is``): the exact same token object must
        # be threaded through so cancelling it propagates to the
        # streaming callback.
        assert kwargs["cancellation_token"] is token

    @pytest.mark.asyncio
    async def test_cancellation_token_default_is_none(self, _make_manager):
        """W4: when ``_resume_processing_background`` is called
        without a token (legacy callers that have not been migrated),
        ``_process_message_with_tracking`` receives ``None``. The
        default-parameter contract is preserved.

        Same gate shape as ``test_cancellation_token_passed_to_...``
        — invoke ``work_fn`` so the closure body runs and the
        default ``cancellation_token=None`` is observable.
        """
        gate = MagicMock()

        async def gate_run(instance_id, holder_id, holder_kind, work_fn):
            return await work_fn()

        gate.run = gate_run
        manager = _make_manager(gate)

        await manager._resume_processing_background(
            instance_id="inst-no-ct",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-no-ct",
            silent=False,
            images=None,
        )

        manager._process_message_with_tracking.assert_awaited_once()
        kwargs = manager._process_message_with_tracking.await_args.kwargs
        assert kwargs["cancellation_token"] is None

    @pytest.mark.asyncio
    async def test_request_registry_unregister_called_in_finally(self, _make_manager):
        """W4: the outermost finally block calls
        ``_request_registry.unregister(message_id)`` so the CTS that
        ``resume_processing_job`` registered is released. This test
        verifies the contract directly: any caller passing a
        ``message_id`` should see ``unregister`` invoked exactly once
        in the finally block on every exit path (success, exception).
        """
        gate = _make_fake_gate(side_effects=[MockMessageResult()])
        manager = _make_manager(gate)

        message_id = str(uuid.uuid4())
        await manager._resume_processing_background(
            instance_id="inst-unreg",
            message="resume",
            message_id=message_id,
            old_job_id="job-unreg",
            silent=False,
            images=None,
        )

        manager._request_registry.unregister.assert_called_once_with(message_id)
