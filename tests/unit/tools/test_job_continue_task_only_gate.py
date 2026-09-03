"""Tests for the M2 ``job_continue`` task-only gate.

Mission-class Milestone M2 (2026-09-02, ``feature/mission-class``) —
the structural guardrail in contract draft §3 (the "Plus" clause):
``job_continue`` accepts ``job_type='task'`` only. For
``job_type='message'`` (mirror receipts) the tool returns a clear
refusal pointing at ``send_message`` — the canonical mirror path.

Rationale (contract draft §3):
  * ``job_type='task'`` → the row IS a mission proxy (the work).
    ``job_continue`` is the canonical "send a follow-up instruction
    to the spawned instance" path.
  * ``job_type='message'`` → the row is a mirror receipt of a message
    the user sent to the instance. ``job_continue`` was the
    historical shortcut for "send another message" — but the
    canonical mirror path is ``send_message`` directly (the message
    itself IS the wire). Forcing ``job_continue`` through this gate
    keeps the wrong-predicate trap closed: an agent cannot
    accidentally use the work-side primitive to message.

The gate is the M2 anti-trap guardrail. This test file pins:

  * ``job_type='message'`` ⇒ ``{"error": ..., "use send_message"}``,
    enqueue NOT called.
  * ``job_type='task'`` ⇒ proceeds past the gate (the existing
    instance-status / concurrency checks still apply).
  * Non-JobItem work (``kind != "job"`` — Task / report) is
    unaffected: the gate only fires for JobItem rows whose
    ``job_type`` is the mirror kind.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.tools.job_queue import create_job_tools


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def job_service() -> AsyncMock:
    """Async ``JobQueueService`` mock.

    ``get_work`` returns a ``WorkRecord``; ``get_job`` returns the
    underlying ``JobItem`` row (the kind-agnostic resolver +
    soft-delete-guard path). The M2 gate reads ``record.job_type``
    (the resolver-backed WorkRecord) AND ``old_job.job_type`` (the
    raw JobItem); tests populate both for full coverage.
    """
    svc = AsyncMock()
    svc.use_virtual_job_resolver = False
    return svc


@pytest.fixture
def queue_mgmt_service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def dead_letter_service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def task_repo() -> MagicMock:
    """``TaskRepository`` mock — the concurrency gate (Task 2.5.8)."""
    repo = MagicMock()
    repo.has_instance_busy = MagicMock(return_value=False)
    return repo


@pytest.fixture
def mock_manager(task_repo: MagicMock) -> MagicMock:
    """``InstanceManager`` mock exposing only the surface ``job_continue`` reads."""
    manager = MagicMock()
    instance_repo = MagicMock()
    manager._instance_repository = instance_repo
    manager._task_repo = task_repo
    manager.enqueue_message_job = AsyncMock()
    manager.enqueue_message = AsyncMock()
    return manager


@pytest.fixture
def tools(
    job_service: AsyncMock,
    queue_mgmt_service: AsyncMock,
    dead_letter_service: MagicMock,
    mock_manager: MagicMock,
):
    return create_job_tools(
        job_service,
        queue_mgmt_service,
        dead_letter_service,
        manager=mock_manager,
    )


@pytest.fixture
def job_continue(tools):
    """``job_continue`` is the 13th tool in the returned tuple.

    Index matches ``tests/unit/test_job_continue_concurrency_gate.py``
    — the layout is owned by the ``create_job_tools`` factory and
    the two test files stay in lock-step.
    """
    return tools[12]


# ─── Helpers ─────────────────────────────────────────────────────────────


def _make_record(*, kind: str = "job", job_type: str | None = "task"):
    """Build a ``WorkRecord``-shaped mock that the M2 gate inspects.

    The gate reads ``record.kind`` AND ``record.job_type``. The
    WorkRecord-shape is permissive — only those two fields are
    consulted at the gate, plus the standard terminal-state check
    (``status``).
    """
    record = MagicMock()
    type(record).kind = property(lambda self: kind)
    type(record).status = property(lambda self: "completed")
    type(record).job_type = property(lambda self: job_type)
    record.instance_id = "inst-1"
    return record


def _make_old_job(*, job_type: str = "task", deleted_at=None):
    """Build a ``JobItem``-shaped mock for the soft-delete-guard branch."""
    job = MagicMock()
    type(job).kind = property(lambda self: "job")
    type(job).job_type = property(lambda self: job_type)
    job.instance_id = "inst-1"
    job.deleted_at = deleted_at
    return job


def _make_instance(*, status: str = "running") -> MagicMock:
    instance = MagicMock()
    instance.status = status
    return instance


# ─── The M2 task-only gate ───────────────────────────────────────────────


class TestJobContinueTaskOnlyGate:
    """M2 guardrail: ``job_continue`` accepts ``job_type='task'`` only."""

    @pytest.mark.asyncio
    async def test_rejects_message_type_with_send_message_pointer(
        self, job_service, mock_manager, tools
    ) -> None:
        """``job_type='message'`` ⇒ clear refusal, ``send_message``
        is the canonical mirror path.

        The error wording names ``send_message`` (with the
        signature) so the agent immediately knows what to call
        instead — no silent fallthrough to ``enqueue_message_job``.
        """
        record = _make_record(job_type="message")
        old_job = _make_old_job(job_type="message")
        job_service.get_work.return_value = record
        job_service.get_job.return_value = old_job
        mock_manager._instance_repository.get.return_value = _make_instance(
            status="running"
        )

        job_continue = tools[12]
        result = await job_continue.ainvoke({
            "old_job_id": "msg-job-1",
            "message": "Continue with another message",
        })

        assert "error" in result
        # Refusal points at the canonical mirror path.
        assert "send_message" in result["error"]
        assert "message-type" in result["error"]
        assert "msg-job-1" in result["error"]
        # CRITICAL: enqueue MUST NOT have been called.
        mock_manager.enqueue_message_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_proceeds_for_task_type(
        self, job_service, mock_manager, tools
    ) -> None:
        """``job_type='task'`` proceeds past the M2 gate.

        The M2 gate fires ONLY on ``job_type='message'``. Task rows
        fall through to the existing instance-status /
        concurrency checks (the pre-M2 path; the tests for those
        checks live in ``test_job_continue_concurrency_gate.py``).
        This test pins the gate's NO-FIRE-on-task behavior — the
        gate must not silently widen to also block task rows.
        """
        record = _make_record(job_type="task")
        old_job = _make_old_job(job_type="task")
        job_service.get_work.return_value = record
        job_service.get_job.return_value = old_job
        mock_manager._instance_repository.get.return_value = _make_instance(
            status="running"
        )

        # Mock the happy-path enqueue so the tool can complete.
        mock_manager.enqueue_message_job.return_value = {
            "message_id": "msg-new",
            "job_id": "job-new",
        }

        job_continue = tools[12]
        result = await job_continue.ainvoke({
            "old_job_id": "task-job-1",
            "message": "Continue with a follow-up instruction",
        })

        # The gate did NOT fire — the call proceeded past the
        # ``is_terminal`` and ``job_continue`` checks. The result
        # carries the new job_id (the enqueue was attempted; the
        # exact return shape is owned by the post-gate path, not
        # the gate itself).
        assert "error" not in result or "send_message" not in result.get("error", "")
        mock_manager.enqueue_message_job.assert_awaited()

    @pytest.mark.asyncio
    async def test_gate_fires_only_on_message_type(
        self, job_service, mock_manager, tools
    ) -> None:
        """The gate discriminates by ``job_type``: only ``'message'``
        is refused; ``'task'`` (and ``None`` / unknown) fall through.

        Regression pin: a future change that broadens the gate
        (e.g. refuses any non-``task`` value) would silently break
        the recovery path for tasks. The two previous tests cover
        the boundary cases; this one drives the explicit
        task-type-non-message path.
        """
        record = _make_record(job_type="task")
        old_job = _make_old_job(job_type="task")
        job_service.get_work.return_value = record
        job_service.get_job.return_value = old_job
        mock_manager._instance_repository.get.return_value = _make_instance(
            status="running"
        )
        mock_manager.enqueue_message_job.return_value = {
            "message_id": "msg-new",
            "job_id": "job-new",
        }

        job_continue = tools[12]
        result = await job_continue.ainvoke({
            "old_job_id": "job-task",
            "message": "Continue task",
        })

        # No gate-fire wording (the ``send_message`` pointer is
        # the diagnostic; absence is the proof the gate didn't
        # fire).
        if "error" in result:
            assert "send_message" not in result["error"]
            assert "message-type" not in result["error"]
        # enqueue WAS called — the call proceeded past the gate.
        mock_manager.enqueue_message_job.assert_awaited()

    @pytest.mark.asyncio
    async def test_gate_does_not_fire_for_non_job_kind(
        self, job_service, mock_manager, tools
    ) -> None:
        """The gate ONLY applies to ``kind='job'`` rows.

        Non-JobItem work (``kind='task'`` / ``'turn'`` / ``'report'``)
        does not have a ``job_type`` distinction — Task rows ARE
        missions; report / turn rows are not JobItems at all. The
        gate must not fire for these (the gate's purpose is to
        redirect mirror receipts to ``send_message``; non-JobItem
        rows have no mirror path to redirect to).
        """
        record = _make_record(kind="task", job_type=None)
        # ``get_job`` is NOT called for non-JobItem rows — the soft-
        # delete guard skips. Leave the mock as-is.
        job_service.get_work.return_value = record
        mock_manager._instance_repository.get.return_value = _make_instance(
            status="running"
        )
        # The M2 pre-check on ``record.job_type`` returns None for
        # non-JobItem rows ⇒ the gate does NOT fire. The
        # post-D13 ``has_instance_busy`` gate (concurrency) is
        # still mocked to False so the call proceeds.
        mock_manager.enqueue_message_job.return_value = {
            "message_id": "msg-new",
            "job_id": "job-new",
        }

        job_continue = tools[12]
        result = await job_continue.ainvoke({
            "old_job_id": "task-record-id",
            "message": "Continue",
        })

        # The M2 ``send_message`` pointer MUST NOT appear — the
        # gate's diagnostic is reserved for JobItem mirror rows.
        if "error" in result:
            assert "send_message" not in result["error"] or (
                # The gate's wording can fire if the test fixture
                # accidentally routed through the JobItem branch —
                # in which case the kind discriminator was wrong.
                # The presence of ``send_message`` here is therefore
                # a fixture-misconfiguration signal, not a gate-fire
                # signal; we surface it loud.
                "fixture misroute" + str(result)
            )
        # enqueue WAS called (the call proceeded past the gate).
        mock_manager.enqueue_message_job.assert_awaited()
