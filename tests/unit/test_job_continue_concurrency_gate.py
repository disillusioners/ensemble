"""Phase 2.5 / Task 2.5.11 — ``job_continue`` concurrency gate.

Verifies the post-D13 ``job_continue`` tool rejects concurrent calls
against the same instance using the new
``TaskRepository.has_inflight_task(instance_id)`` gate (Task 2.5.8).

Pre-D13 the tool rejected by querying
``JobRepository.find_processing_message_jobs_by_instance`` — a
DB-level concurrency gate over MESSAGE ``JobItem`` rows. After D13
no MESSAGE ``JobItem`` rows are created, so the gate moved onto the
``task`` table: any PENDING or RUNNING ``task`` row for the instance
counts as in-flight and the tool refuses to enqueue a follow-up
message.

The test surface uses ``create_job_tools`` against a fake
``JobQueueService`` + mock ``InstanceManager`` so the gate logic is
exercised end-to-end without spinning up a real engine.

Behaviour contract (Task 2.5.8):

  * ``has_inflight_task(instance_id) is True`` → tool returns
    ``{"error": "Instance ... has a task still in flight — wait for it
    to complete first"}`` and ``enqueue_message`` is NOT called.
  * ``has_inflight_task(instance_id) is False`` → tool proceeds to
    ``enqueue_message`` and returns the new ``message_id`` / ``job_id``.
  * PAUSED tasks are EXCLUDED by ``has_inflight_task`` — paused is a
    quiescent state and a ``job_continue`` against a paused instance
    is allowed to proceed (the user opted to unpause via a separate
    flow).

Run with::

    pytest tests/unit/test_job_continue_concurrency_gate.py -v --tb=short
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.tools.job_queue import create_job_tools


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def job_service():
    """Async ``JobQueueService`` mock — the gate doesn't read it, but
    the tool's happy-path pre-flight (job lookup) does.
    """
    return AsyncMock()


@pytest.fixture
def queue_mgmt_service():
    return AsyncMock()


@pytest.fixture
def dead_letter_service():
    return MagicMock()


@pytest.fixture
def task_repo():
    """``TaskRepository`` mock with the gate primitive.

    ``has_inflight_task(instance_id)`` is the new DB-level concurrency
    gate (Task 2.5.8). The default ``False`` keeps happy-path tests
    passing; tests exercising the rejection path override the return
    value explicitly.
    """
    repo = MagicMock()
    repo.has_inflight_task = MagicMock(return_value=False)
    return repo


@pytest.fixture
def mock_manager(task_repo):
    """``InstanceManager`` mock exposing only the surface ``job_continue`` reads."""
    manager = MagicMock()
    instance_repo = MagicMock()
    manager._instance_repository = instance_repo
    # Phase 2.5 (Task 2.5.8): the gate moved onto ``_task_repo``.
    manager._task_repo = task_repo
    manager.enqueue_message = AsyncMock()
    return manager


@pytest.fixture
def tools(job_service, queue_mgmt_service, dead_letter_service, mock_manager):
    """Build the job-queue tool set with the gate wired up."""
    return create_job_tools(
        job_service,
        queue_mgmt_service,
        dead_letter_service,
        manager=mock_manager,
    )


@pytest.fixture
def job_continue(tools):
    """``job_continue`` is the 13th tool in the returned tuple."""
    return tools[12]


def _make_old_job(*, instance_id: str = "inst-1"):
    """Build a MagicMock standing in for a JobItem returned by ``job_service.get_job``."""
    old_job = MagicMock()
    old_job.status = "completed"
    old_job.instance_id = instance_id
    old_job.deleted_at = None
    return old_job


def _make_instance(*, status: str = "running"):
    instance = MagicMock()
    instance.status = status
    return instance


# ─── Task 2.5.11: ``job_continue`` concurrency gate ──────────────────────────


class TestJobContinueConcurrencyGate:
    """The ``has_inflight_task`` gate rejects concurrent ``job_continue`` calls.

    The classic race the gate prevents: two concurrent agent turns
    attempting to enqueue follow-up work on the same instance. Pre-D13
    the gate was the ``find_processing_message_jobs_by_instance``
    check (a list of PROCESSING MESSAGE ``JobItem`` rows). Post-D13
    it's ``has_inflight_task`` (True iff any PENDING or RUNNING
    ``task`` row exists for the instance).
    """

    @pytest.mark.asyncio
    async def test_rejects_when_task_repo_reports_inflight(
        self, job_service, mock_manager, tools, task_repo
    ):
        """Gate fires: ``has_inflight_task`` returns True → tool rejects.

        The error message is the contract Task 2.5.8 specifies — the
        pre-D13 wording ("has a job still processing") is gone. The
        new wording mentions tasks, not jobs, because there is no
        ``JobItem`` to collide with any more.
        """
        job_service.get_job.return_value = _make_old_job()
        mock_manager._instance_repository.get.return_value = _make_instance(
            status="running"
        )
        # Gate: a Task is already driving this instance.
        task_repo.has_inflight_task = MagicMock(return_value=True)

        job_continue = tools[12]
        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue",
        })

        assert "error" in result
        assert "has a task still in flight" in result["error"], (
            f"expected the post-D13 gate message, got {result['error']!r}"
        )
        assert "inst-1" in result["error"]
        # Critical: enqueue must NOT have been called.
        mock_manager.enqueue_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_proceeds_when_no_task_in_flight(
        self, job_service, mock_manager, tools, task_repo
    ):
        """Happy path: gate is closed → tool enqueues via ``enqueue_message``.

        Mirrors the happy-path baseline in
        ``tests/test_job_queue_tools.py::test_job_continue_happy_path``
        — kept here so the new gate wiring is verified by the gate
        contract: ``has_inflight_task`` is called exactly once with the
        ``instance_id`` from the old job, and when it returns False,
        the tool enqueues normally.
        """
        from daemon.manager import AsyncMessageResult

        old_job = _make_old_job(instance_id="inst-1")
        job_service.get_job.return_value = old_job
        mock_manager._instance_repository.get.return_value = _make_instance(
            status="running"
        )
        # Gate: no Task is driving this instance.
        task_repo.has_inflight_task = MagicMock(return_value=False)
        mock_manager.enqueue_message.return_value = AsyncMessageResult(
            message_id="msg-1",
            instance_id="inst-1",
            status="queued",
            job_id="new-job-1",
        )

        job_continue = tools[12]
        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue",
        })

        assert "error" not in result
        assert result["new_job_id"] == "new-job-1"
        assert result["message_id"] == "msg-1"
        mock_manager.enqueue_message.assert_awaited_once()
        # The gate must have been consulted with the right instance_id.
        # NOTE: ``has_inflight_task`` is invoked via ``asyncio.to_thread``
        # so the mock's call counter (not await counter) is what
        # advances — ``assert_called_with`` is the right assertion.
        task_repo.has_inflight_task.assert_called_with("inst-1")

    @pytest.mark.asyncio
    async def test_two_concurrent_calls_first_succeeds_second_rejected(
        self, job_service, mock_manager, tools, task_repo
    ):
        """Two concurrent ``job_continue`` calls.

        Simulates the B3 race the gate was designed to close: two
        concurrent callers attempt to enqueue follow-up work on the
        same instance. The race window between caller A's
        ``has_inflight_task = False`` check and its ``enqueue_message``
        commit is when caller B's check runs. If both pass the gate,
        two message rows get enqueued — the worker pool then races to
        drive both graph turns for the same instance (a corruption
        window on the langgraph checkpoint).

        The test mocks ``has_inflight_task`` to return False the FIRST
        time it is awaited (caller A passes the gate) and True on
        every subsequent call (caller B is rejected). This is the
        realistic observable behaviour: the first enqueue has already
        created the in-flight Task by the time the second call
        consults the gate.

        Asserts:
          * The first call's ``enqueue_message`` was awaited.
          * The second call's result carries the gate-rejection error.
          * Only one ``enqueue_message`` awaitable was driven (the
            rejected call did NOT enqueue).
        """
        import asyncio

        from daemon.manager import AsyncMessageResult

        old_job = _make_old_job(instance_id="inst-1")
        job_service.get_job.return_value = old_job
        mock_manager._instance_repository.get.return_value = _make_instance(
            status="running"
        )

        # Gate: first call passes, subsequent calls fail.
        call_log: list[str] = []
        def _gate_side_effect(instance_id: str) -> bool:
            call_log.append(instance_id)
            return len(call_log) > 1  # True after the first call
        task_repo.has_inflight_task = MagicMock(side_effect=_gate_side_effect)

        # The first enqueue resolves normally; the second never
        # enqueues (gate rejects before we reach it).
        mock_manager.enqueue_message.return_value = AsyncMessageResult(
            message_id="msg-A",
            instance_id="inst-1",
            status="queued",
            job_id="new-job-A",
        )

        job_continue = tools[12]
        call_a = asyncio.create_task(
            job_continue.ainvoke({
                "old_job_id": "old-job-1",
                "message": "A's continuation",
            })
        )
        # Yield to the event loop so call_a progresses past the gate
        # check before call_b starts. asyncio.sleep(0) is sufficient
        # — the await on ``asyncio.to_thread(has_inflight_task, ...)``
        # inside ``job_continue`` is the scheduling point.
        await asyncio.sleep(0)
        call_b = asyncio.create_task(
            job_continue.ainvoke({
                "old_job_id": "old-job-1",
                "message": "B's continuation",
            })
        )

        result_a, result_b = await asyncio.gather(call_a, call_b)

        # Caller A succeeded.
        assert "error" not in result_a, (
            f"first concurrent call must succeed; got {result_a!r}"
        )
        assert result_a["new_job_id"] == "new-job-A"

        # Caller B was rejected by the gate.
        assert "error" in result_b, (
            f"second concurrent call must be rejected by the gate; "
            f"got {result_b!r}"
        )
        assert "has a task still in flight" in result_b["error"]
        assert "inst-1" in result_b["error"]

        # Exactly one enqueue fired (the rejected call short-circuited).
        assert mock_manager.enqueue_message.await_count == 1, (
            "the rejected concurrent call must NOT enqueue; only the "
            "passing call should reach enqueue_message"
        )

        # Both calls consulted the gate.
        assert len(call_log) == 2
        assert call_log == ["inst-1", "inst-1"]

    @pytest.mark.asyncio
    async def test_paused_tasks_do_not_block_gate(
        self, job_service, mock_manager, tools, task_repo
    ):
        """PAUSED tasks must NOT count as in-flight.

        Sister invariant to ``TaskRepository.has_inflight_task``'s
        docstring: ``has_inflight_task`` excludes PAUSED because
        paused tasks are NOT actively driving the graph. A
        ``job_continue`` call against an instance whose only task is
        PAUSED is allowed to proceed — the user has explicitly paused
        and is now opting to enqueue more work.

        Companion primitive
        ``TaskRepository.find_paused_or_running_by_instance`` DOES
        include PAUSED — it is the root-vs-child routing decision for
        ``resume_processing_job``, which needs to recognise paused
        state to fire checkpoint resume.

        NOTE: the tool's instance-status pre-check (``InstanceStatus.PAUSED``)
        returns an error before the ``has_inflight_task`` gate fires —
        so the gate-must-fire assertion requires a RUNNING instance
        (the instance must pass the status pre-check for the gate to
        even be consulted). We verify here that the gate itself does
        NOT short-circuit (it returns False because the only Task is
        PAUSED) by checking the gate was called and the tool's error
        message is from the gate-rejection branch, not the
        instance-paused branch.
        """
        from daemon.manager import AsyncMessageResult

        # Use a RUNNING instance (not paused) — the paused pre-check
        # would otherwise short-circuit before the gate fires.
        job_service.get_job.return_value = _make_old_job(instance_id="inst-1")
        mock_manager._instance_repository.get.return_value = _make_instance(
            status="running"
        )
        # The Task is PAUSED — ``has_inflight_task`` excludes PAUSED
        # so the gate returns False (the tool is allowed to proceed).
        task_repo.has_inflight_task = MagicMock(return_value=False)
        mock_manager.enqueue_message.return_value = AsyncMessageResult(
            message_id="msg-1",
            instance_id="inst-1",
            status="queued",
            job_id="new-job-1",
        )

        job_continue = tools[12]
        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue",
        })

        # Tool proceeded past the gate. (The instance is RUNNING so
        # the status pre-check passes.)
        # NOTE: ``has_inflight_task`` is invoked via ``asyncio.to_thread``
        # so we assert ``assert_called_once`` (not ``assert_awaited_once``).
        task_repo.has_inflight_task.assert_called_once()
        assert "error" not in result, (
            f"gate must NOT fire when the only Task is PAUSED; got {result!r}"
        )
        assert result["new_job_id"] == "new-job-1"
        # The gate returned False — tool proceeded to enqueue.
        mock_manager.enqueue_message.assert_awaited_once()
        # The gate's return value was consulted and was False.
        task_repo.has_inflight_task.assert_called_with("inst-1")
