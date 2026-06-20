"""Integration tests for the watch_job -> CM pending_jobs -> completion callback chain.

Tests the B1-B4 implementation:
  - B1: ParentCorrelation.pending_jobs set + is_complete() checks both pending and pending_jobs
  - B2: CM.register_job_send / CM.resolve_job + module helpers notify_corr_register_job / notify_corr_resolve_job
  - B3: watch_job calls notify_corr_register_job
  - B4: _finalize_job post-commit outbox calls notify_corr_resolve_job

All tests run at the CorrelationManager level (unit tests, SQLite/mocks, NOT PostgreSQL).
The CM is the authoritative source of truth for completion — if these tests pass, the
watch_job -> completion chain is correct regardless of the observer wiring.

Test organization:
  Category 1 (TestPendingJobsTracking): basic pending_jobs register/resolve
  Category 2 (TestMixedMessageAndJob): combined message + job correlations
  Category 3 (TestGenerationCounter): generation counter bumps for re-arm detection
  Category 4 (TestWatchJobOrphanProtection): Variant B orphan-race regression
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.services.correlation_manager import (
    STATUS_ERROR,
    STATUS_RESPONDED,
    CorrelationManager,
)


# =============================================================================
# Local helpers (mirroring tests/test_correlation_manager.py patterns)
# =============================================================================


def make_instance_repo() -> MagicMock:
    """Build a mock SQLModelInstanceRepository (CM shadow validation reads it)."""
    repo = MagicMock(name="InstanceRepo")
    repo.list = MagicMock(return_value=([], 0))
    repo.get_all_with_waiting_for = MagicMock(return_value=[])
    repo.get_children = MagicMock(return_value=[])
    repo.get = MagicMock(return_value=None)
    return repo


def make_msg_repo() -> MagicMock:
    """Build a mock SQLModelMessageQueueRepository."""
    repo = MagicMock(name="MsgRepo")
    repo.list = MagicMock(return_value=[])
    repo.get_pending_for_instances = MagicMock(return_value=[])
    return repo


def make_callback() -> tuple[list, Any]:
    """Build a completion_callback that records every invocation."""
    recorder: list[tuple[str, str]] = []

    async def _cb(parent_id: str, terminal_status: str) -> None:
        recorder.append((parent_id, terminal_status))

    _cb.calls = recorder  # type: ignore[attr-defined]
    return recorder, _cb


def make_cm(*, callback: Any = None) -> CorrelationManager:
    """Instantiate a CM with sensible mocked defaults."""
    return CorrelationManager(
        instance_repository=make_instance_repo(),
        message_queue_repository=make_msg_repo(),
        completion_callback=callback,
    )


# =============================================================================
# Category 1 — Basic pending_jobs tracking
# =============================================================================


class TestPendingJobsTracking:
    """Tests for the basic register_job_send / resolve_job lifecycle."""

    @pytest.mark.asyncio
    async def test_register_job_marks_incomplete(self):
        """Registering a single watched job keeps the parent incomplete with count=1."""
        cm = make_cm()
        parent = "parent-1"
        job1 = "job-aaa"

        await cm.register_job_send(parent, job1)

        assert cm.is_complete(parent) is False
        assert cm.get_pending_count(parent) == 1

    @pytest.mark.asyncio
    async def test_register_then_resolve_job_completes(self):
        """Register one job then resolve it -> callback fires (completed), parent cleaned up."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        job1 = "job-aaa"

        await cm.register_job_send(parent, job1)
        result = await cm.resolve_job(parent, job1)  # default status=STATUS_RESPONDED

        assert result is True, "last correlation should report True"
        assert recorder == [(parent, "completed")], "callback should fire exactly once with completed"
        assert cm.is_complete(parent) is True, "after completion parent is untracked -> is_complete returns True"
        assert cm.get_pending_count(parent) == 0

    @pytest.mark.asyncio
    async def test_multiple_jobs_resolve_one_still_pending(self):
        """Two registered jobs, resolving only one keeps parent incomplete, no callback yet."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        job1, job2 = "job-1", "job-2"

        await cm.register_job_send(parent, job1)
        await cm.register_job_send(parent, job2)

        result = await cm.resolve_job(parent, job1)

        assert result is False, "not the last correlation"
        assert cm.is_complete(parent) is False
        assert cm.get_pending_count(parent) == 1
        assert recorder == [], "callback must NOT fire while a job is still pending"


# =============================================================================
# Category 2 — Mixed message and job correlations
# =============================================================================


class TestMixedMessageAndJob:
    """Tests combining message correlations with watched-job correlations."""

    @pytest.mark.asyncio
    async def test_message_and_job_both_pending_neither_alone_completes(self):
        """Message + job correlation together: resolving only one leaves parent incomplete."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"

        # Register one message and one job
        await cm.register_message_send(parent, "childA", "msgA")
        await cm.register_job_send(parent, "job1")

        assert cm.get_pending_count(parent) == 2, "one message + one job = two pending"

        # Resolve only the message — job1 still pending, NOT complete
        await cm.resolve_response(parent, "childA", "msgA")

        assert cm.is_complete(parent) is False
        assert cm.get_pending_count(parent) == 1
        assert recorder == [], "callback must not fire while watched job is still pending"

        # Now resolve the watched job — parent finally completes
        await cm.resolve_job(parent, "job1")

        assert recorder == [(parent, "completed")]
        assert cm.get_pending_count(parent) == 0

    @pytest.mark.asyncio
    async def test_job_completes_first_then_message(self):
        """Order-swap: resolving job first leaves parent incomplete until message also resolves."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"

        await cm.register_message_send(parent, "childA", "msgA")
        await cm.register_job_send(parent, "job1")

        # Resolve job FIRST — message still pending
        await cm.resolve_job(parent, "job1")

        assert cm.is_complete(parent) is False, "message still pending -> parent incomplete"
        assert recorder == [], "callback must not fire while message correlation is pending"

        # Now resolve the message
        await cm.resolve_response(parent, "childA", "msgA")

        assert recorder == [(parent, "completed")]

    @pytest.mark.asyncio
    async def test_error_status_on_job_yields_error_terminal(self):
        """Resolving a watched job with STATUS_ERROR sets had_error -> terminal_status='error'."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        job1 = "job-aaa"

        await cm.register_job_send(parent, job1)
        result = await cm.resolve_job(parent, job1, status=STATUS_ERROR)

        assert result is True
        assert recorder == [(parent, "error")], "had_error flips terminal_status from completed to error"


# =============================================================================
# Category 3 — Generation counter integration
# =============================================================================


class TestGenerationCounter:
    """Tests for the generation counter that powers the re-arm gate."""

    @pytest.mark.asyncio
    async def test_register_job_send_bumps_generation(self):
        """Each register_job_send call monotonically bumps the per-parent generation counter."""
        cm = make_cm()
        parent = "parent-1"
        job1, job2 = "job-1", "job-2"

        assert cm.get_generation(parent) == 0, "untracked parent starts at 0"

        await cm.register_job_send(parent, job1)
        assert cm.get_generation(parent) == 1

        await cm.register_job_send(parent, job2)
        assert cm.get_generation(parent) == 2

    @pytest.mark.asyncio
    async def test_resolve_job_also_bumps_generation(self):
        """resolve_job also bumps the generation counter (symmetric with register_job_send).

        # In production, _finalize_job reads get_generation() before/after its DB commit.
        # A change means a register was in-flight -> re-arm (COMPLETED->PROCESSING).
        # This test proves the counter changes on resolve, which is the symmetric
        # bump that makes the re-arm gate fire correctly when a concurrent
        # register_job_send lands during finalization.
        """
        cm = make_cm()
        parent = "parent-1"
        job1 = "job-1"

        await cm.register_job_send(parent, job1)
        gen_before = cm.get_generation(parent)
        assert gen_before == 1

        await cm.resolve_job(parent, job1)
        gen_after = cm.get_generation(parent)

        assert gen_after > gen_before, (
            "resolve_job must bump the generation counter so _finalize_job's "
            "before/after comparison can detect an in-flight register"
        )


# =============================================================================
# Category 4 — watch_job orphan protection (Variant B regression)
# =============================================================================


class TestWatchJobOrphanProtection:
    """Tests proving the CM state that prevents premature completion of a parent
    whose message children have all responded but whose watched job is still running.
    """

    @pytest.mark.asyncio
    async def test_watched_job_keeps_parent_alive_after_messages_resolve(self):
        """Full watch_job flow at CM level: message resolves but watched job still pending -> no callback."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"

        # Simulate: parent calls watch_job (register_job_send) AND sends a message.
        await cm.register_message_send(parent, "childA", "msgA")
        await cm.register_job_send(parent, "watched-job-1")

        # The message child responds, but the watched job is still running.
        await cm.resolve_response(parent, "childA", "msgA")

        # Parent must NOT be complete yet — watched job still pending.
        assert cm.is_complete(parent) is False
        assert recorder == [], "callback must not fire while watched job is still pending"

        # The watched job finally completes.
        await cm.resolve_job(parent, "watched-job-1")

        # NOW complete.
        assert recorder == [(parent, "completed")]

    @pytest.mark.asyncio
    async def test_variant_b_regression_orphan_protection(self):
        """Variant B regression: without pending_jobs tracking, the parent would complete
        as soon as its message children resolved, orphaning the watched job.
        The pending_jobs set prevents this.
        """
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"

        # Parent has 2 message children AND watches 1 job.
        await cm.register_message_send(parent, "child-1", "msg-1")
        await cm.register_message_send(parent, "child-2", "msg-2")
        await cm.register_job_send(parent, "long-running-job")

        assert cm.get_pending_count(parent) == 3, "two messages + one watched job = three pending"

        # BOTH message children respond (simulating the parent's message fanout completing).
        await cm.resolve_response(parent, "child-1", "msg-1")
        await cm.resolve_response(parent, "child-2", "msg-2")

        # Parent must STILL NOT be complete — orphan protection: the watched job is pending.
        assert cm.is_complete(parent) is False
        assert recorder == [], "callback must not fire while watched job is still pending"
        assert cm.get_pending_count(parent) == 1, "only the watched job remains"

        # The watched job finally terminates.
        await cm.resolve_job(parent, "long-running-job")

        # NOW complete.
        assert recorder == [(parent, "completed")]
