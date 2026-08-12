"""Phase 2.5 / Task 2.5.11 — ``job_continue`` concurrency gate.

Verifies the post-D13 ``job_continue`` tool rejects concurrent calls
against the same instance using the canonical
``TaskRepository.has_instance_busy(instance_id)`` gate (Task 2.5.8
+ Bug-1 fix 2026-08-12).

Pre-D13 the tool rejected by querying
``JobRepository.find_processing_message_jobs_by_instance`` — a
DB-level concurrency gate over MESSAGE ``JobItem`` rows. After D13
no MESSAGE ``JobItem`` rows are created, so the gate moved onto the
``task`` table: any PENDING, RUNNING, or PAUSED ``task`` row for
the instance counts as live work and the tool refuses to enqueue
a follow-up message.

The test surface uses ``create_job_tools`` against a fake
``JobQueueService`` + mock ``InstanceManager`` so the gate logic is
exercised end-to-end without spinning up a real engine.

Behaviour contract (Task 2.5.8 + Bug-1 fix 2026-08-12):

  * ``has_instance_busy(instance_id) is True`` → tool returns
    ``{"error": "Instance ... has a task still in flight — wait for it
    to complete first"}`` and ``enqueue_message`` is NOT called.
  * ``has_instance_busy(instance_id) is False`` → tool proceeds to
    ``enqueue_message`` and returns the new ``message_id`` / ``job_id``.
  * PAUSED tasks ARE INCLUDED by ``has_instance_busy`` — a paused
    instance has live work (the Task still holds the per-instance
    serialization slot) and a ``job_continue`` against a paused
    instance must be rejected. (The pre-fix ``has_inflight_task``
    gate excluded PAUSED — that was a concurrency leak: a paused
    instance was treated as "not busy" and a follow-up enqueue
    would race the resume on the langgraph ``thread_id``.)

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
    svc = AsyncMock()
    # Legacy kill-switch path (pre-resolver) — tests exercise the
    # get_job branch, not the get_work resolver path.
    svc.use_virtual_job_resolver = False
    return svc


@pytest.fixture
def queue_mgmt_service():
    return AsyncMock()


@pytest.fixture
def dead_letter_service():
    return MagicMock()


@pytest.fixture
def task_repo():
    """``TaskRepository`` mock with the gate primitive.

    ``has_instance_busy(instance_id)`` is the canonical
    DB-level concurrency gate (Task 2.5.8 + Bug-1 fix
    2026-08-12). The default ``False`` keeps happy-path tests
    passing; tests exercising the rejection path override the
    return value explicitly.

    Note: the pre-fix ``has_inflight_task`` was kept on the mock
    for backwards-compat with any straggling call sites (the
    production method is still defined; only the gate-consumer
    has moved to ``has_instance_busy``). The default return is
    ``False`` for both so the happy-path is unchanged.
    """
    repo = MagicMock()
    repo.has_instance_busy = MagicMock(return_value=False)
    # Kept on the mock in case a future straggling
    # ``has_inflight_task`` call site needs a default; the gate
    # in ``job_continue`` no longer consults this attribute.
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
    # Phase 5 cutover: ``job_continue`` enqueues via ``enqueue_message_job``
    # (the inline message-Job path — see daemon/tools/job_queue.py:749).
    # Tests must mock this attribute; ``enqueue_message`` is the legacy
    # internal-only path and is NOT called by ``job_continue``.
    manager.enqueue_message_job = AsyncMock()
    # Kept as a defensive belt-and-suspenders mock so any straggling
    # ``enqueue_message`` call site (regressions, future code that
    # forgets to migrate) does not accidentally pass through the real
    # implementation. Production does not exercise this attribute.
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
    """Build a MagicMock standing in for a JobItem returned by ``job_service.get_work`` / ``job_service.get_job``.

    Production ``job_continue`` calls ``await job_service.get_work(old_job_id)``
    first (the kind-agnostic resolver — see daemon/tools/job_queue.py:651),
    then routes by ``record.kind``. When ``kind == "job"`` it also calls
    ``get_job`` for the soft-delete column. To keep both code paths happy
    with a single fixture, the returned mock carries:
      * ``.status = "completed"``  — terminal-state check needs a real string,
        not an AsyncMock (the bug this test was written to catch).
      * ``.kind = "job"``          — routes into the ``enqueue_message_job``
        enqueue path.
      * ``.instance_id``           — used by the gate's instance_status
        pre-check and the ``has_inflight_task`` keying.
      * ``.deleted_at = None``     — soft-delete guard skips.
      * ``.admission_state = "done"`` — same value ``_make_old_job`` exposed
        pre-D13, kept for backwards-compat with any code that reads it.
    """
    old_job = MagicMock()
    # NOTE: we must set status via a direct assignment that is NOT
    # itself a MagicMock — the job_continue path calls get_work which
    # returns a WorkRecord whose .status is read directly.
    type(old_job).status = property(lambda self: "completed")
    type(old_job).kind = property(lambda self: "job")
    type(old_job).admission_state = property(lambda self: "done")
    old_job.instance_id = instance_id
    old_job.deleted_at = None
    return old_job


def _make_instance(*, status: str = "running"):
    instance = MagicMock()
    instance.status = status
    return instance


# ─── Task 2.5.11: ``job_continue`` concurrency gate ──────────────────────────


class TestJobContinueConcurrencyGate:
    """The ``has_instance_busy`` gate rejects concurrent ``job_continue`` calls.

    The classic race the gate prevents: two concurrent agent turns
    attempting to enqueue follow-up work on the same instance. Pre-D13
    the gate was the ``find_processing_message_jobs_by_instance``
    check (a list of PROCESSING MESSAGE ``JobItem`` rows). Post-D13
    it's ``has_instance_busy`` (True iff any PENDING, RUNNING, or
    PAUSED ``task`` row exists for the instance — the canonical
    "is this instance busy?" predicate; Bug-1 fix 2026-08-12).
    """

    @pytest.mark.asyncio
    async def test_rejects_when_task_repo_reports_inflight(
        self, job_service, mock_manager, tools, task_repo
    ):
        """Gate fires: ``has_instance_busy`` returns True → tool rejects.

        The error message is the contract Task 2.5.8 specifies — the
        pre-D13 wording ("has a job still processing") is gone. The
        new wording mentions tasks, not jobs, because there is no
        ``JobItem`` to collide with any more.
        """
        job_service.get_job.return_value = _make_old_job()
        # Production calls ``get_work`` first (kind-agnostic resolver); mock
        # both so the test stays green if the resolver path regresses.
        job_service.get_work.return_value = _make_old_job()
        mock_manager._instance_repository.get.return_value = _make_instance(
            status="running"
        )
        # Gate: a Task is already driving this instance.
        task_repo.has_instance_busy = MagicMock(return_value=True)

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
        mock_manager.enqueue_message_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_proceeds_when_no_task_in_flight(
        self, job_service, mock_manager, tools, task_repo
    ):
        """Happy path: gate is closed → tool enqueues via ``enqueue_message``.

        Mirrors the happy-path baseline in
        ``tests/test_job_queue_tools.py::test_job_continue_happy_path``
        — kept here so the new gate wiring is verified by the gate
        contract: ``has_instance_busy`` is called exactly once with
        the ``instance_id`` from the old job, and when it returns
        False, the tool enqueues normally.
        """
        from daemon.manager import AsyncMessageResult

        old_job = _make_old_job(instance_id="inst-1")
        job_service.get_job.return_value = old_job
        job_service.get_work.return_value = old_job
        mock_manager._instance_repository.get.return_value = _make_instance(
            status="running"
        )
        # Gate: no live Task is driving this instance.
        task_repo.has_instance_busy = MagicMock(return_value=False)
        mock_manager.enqueue_message_job.return_value = AsyncMessageResult(
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
        mock_manager.enqueue_message_job.assert_awaited_once()
        # The gate must have been consulted with the right instance_id.
        # NOTE: ``has_instance_busy`` is invoked via ``asyncio.to_thread``
        # so the mock's call counter (not await counter) is what
        # advances — ``assert_called_with`` is the right assertion.
        task_repo.has_instance_busy.assert_called_with("inst-1")

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

        The test mocks ``has_instance_busy`` to return False the FIRST
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
        job_service.get_work.return_value = old_job
        mock_manager._instance_repository.get.return_value = _make_instance(
            status="running"
        )

        # Gate: first call passes, subsequent calls fail.
        call_log: list[str] = []
        def _gate_side_effect(instance_id: str) -> bool:
            call_log.append(instance_id)
            return len(call_log) > 1  # True after the first call
        task_repo.has_instance_busy = MagicMock(side_effect=_gate_side_effect)

        # The first enqueue resolves normally; the second never
        # enqueues (gate rejects before we reach it).
        mock_manager.enqueue_message_job.return_value = AsyncMessageResult(
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
        assert mock_manager.enqueue_message_job.await_count == 1, (
            "the rejected concurrent call must NOT enqueue; only the "
            "passing call should reach enqueue_message_job"
        )

        # Both calls consulted the gate.
        assert len(call_log) == 2
        assert call_log == ["inst-1", "inst-1"]

    @pytest.mark.asyncio
    async def test_paused_tasks_now_block_gate(
        self, job_service, mock_manager, tools, task_repo
    ):
        """PAUSED tasks MUST count as in-flight — the Bug-1 fix (2026-08-12).

        Sister invariant to ``TaskRepository.has_instance_busy``'s
        docstring: ``has_instance_busy`` INCLUDES PAUSED because
        paused tasks are live work the instance still owns — a
        ``job_continue`` call against an instance whose only task
        is PAUSED is REJECTED. The pre-fix
        ``has_inflight_task`` gate excluded PAUSED and that was
        a concurrency leak: a paused instance was treated as
        "not busy" and a follow-up enqueue would race the resume
        on the langgraph ``thread_id``.

        The companion primitive
        ``TaskRepository.find_paused_or_running_by_instance`` ALSO
        includes PAUSED — for the same reason, paused work is
        live work.

        NOTE: the tool's instance-status pre-check
        (``InstanceStatus.PAUSED``) returns an error before the
        ``has_instance_busy`` gate fires — so the
        gate-must-fire assertion requires a RUNNING instance
        (the instance must pass the status pre-check for the
        gate to even be consulted). The instance is RUNNING
        here, but the underlying Task is PAUSED — simulating
        a freshly-paused Task on an instance whose pause
        hadn't yet propagated to the instance-status table.
        """
        from daemon.manager import AsyncMessageResult

        # Use a RUNNING instance (not paused) — the paused
        # instance-status pre-check would otherwise short-circuit
        # before the gate fires. The Task is PAUSED — the gate
        # must now reject (the Bug-1 fix behaviour).
        old_job = _make_old_job(instance_id="inst-1")
        job_service.get_job.return_value = old_job
        job_service.get_work.return_value = old_job
        mock_manager._instance_repository.get.return_value = _make_instance(
            status="running"
        )
        # The Task is PAUSED — ``has_instance_busy`` INCLUDES
        # PAUSED so the gate returns True (the tool is rejected).
        # This is the new Bug-1-fix behaviour; the pre-fix
        # ``has_inflight_task`` returned False and the test
        # asserted the happy path.
        task_repo.has_instance_busy = MagicMock(return_value=True)
        mock_manager.enqueue_message_job.return_value = AsyncMessageResult(
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

        # Tool was rejected by the gate. (The instance is RUNNING so
        # the status pre-check passes — the gate must fire here.)
        # NOTE: ``has_instance_busy`` is invoked via
        # ``asyncio.to_thread`` so we assert ``assert_called_once``
        # (not ``assert_awaited_once``).
        task_repo.has_instance_busy.assert_called_once()
        assert "error" in result, (
            f"gate MUST fire when the only Task is PAUSED "
            f"(Bug-1 fix); got {result!r}"
        )
        assert "has a task still in flight" in result["error"]
        assert "inst-1" in result["error"]
        # The gate's True return value short-circuited the enqueue.
        mock_manager.enqueue_message_job.assert_not_awaited()
        # The gate returned True — tool was rejected.
        task_repo.has_instance_busy.assert_called_with("inst-1")
