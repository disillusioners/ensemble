"""Unit tests for the ReportInjection repository.

Validates the exactly-once delivery semantics that fix the
parent-waits-for-child deadlock: a child completion report is
delivered by EXACTLY ONE of two paths — the live agent-node drain
(``claim_for_injection`` → ``INJECTED``) or the fallback
``PROCESS_REPORT`` task (``claim_for_task_delivery`` →
``TASK_DELIVERED``) — never both, never neither.

All tests run against the in-memory SQLite ``engine`` fixture (see
``tests/repositories/conftest.py``), which creates the
``report_injections`` and ``message_queue`` tables via
``SQLModel.metadata.create_all``.
"""

from __future__ import annotations

import pytest

from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection import (
    ReportInjection,
    ReportInjectionRepository,
    ReportInjectionState,
)
from sqlmodel import Session


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def repo(engine) -> ReportInjectionRepository:
    """A ReportInjectionRepository bound to the test engine."""
    return ReportInjectionRepository(engine)


def _enqueue(repo, engine, parent="parent-1", child="child-1", msg="msg-1",
             report_msg="rmsg-1", content="report body"):
    """Helper: enqueue one PENDING report + its companion message_queue row."""
    # Companion completion_report row in message_queue (status=ready),
    # mirroring what child_reports creates in production.
    with Session(engine) as session:
        session.add(MessageQueue(
            message_id=report_msg,
            instance_id=parent,
            content=content,
            source=f"internal_report:{child}:{msg}",
            type=MessageType.COMPLETION_REPORT.value,
            status=MessageStatus.READY.value,
        ))
        session.commit()
    return repo.enqueue(
        parent_instance_id=parent,
        child_instance_id=child,
        child_message_id=msg,
        report_message_id=report_msg,
        content=content,
    )


# =============================================================================
# claim_for_injection — agent-node drain
# =============================================================================


class TestClaimForInjection:
    """The live agent-node drain path."""

    def test_drain_returns_content_and_marks_injected(self, repo, engine):
        _enqueue(repo, engine, content="hello report")
        drained = repo.claim_for_injection("parent-1")

        assert len(drained) == 1
        assert drained[0]["content"] == "hello report"
        assert drained[0]["report_message_id"] == "rmsg-1"
        assert repo.count_pending_for_parent("parent-1") == 0

    def test_drain_marks_companion_message_completed(self, repo, engine):
        _enqueue(repo, engine, report_msg="rmsg-1")
        repo.claim_for_injection("parent-1")

        from sqlmodel import select as sm_select
        with Session(engine) as session:
            row = session.exec(
                sm_select(MessageQueue).where(MessageQueue.message_id == "rmsg-1")
            ).first()
        assert row is not None
        assert row.status == MessageStatus.COMPLETED.value

    def test_drain_is_idempotent(self, repo, engine):
        """A second drain returns nothing (rows already INJECTED)."""
        _enqueue(repo, engine)
        assert len(repo.claim_for_injection("parent-1")) == 1
        assert repo.claim_for_injection("parent-1") == []

    def test_drain_returns_empty_for_unknown_parent(self, repo, engine):
        assert repo.claim_for_injection("no-such-parent") == []

    def test_drain_is_scoped_to_parent(self, repo, engine):
        """Drain for one parent must not consume another parent's reports."""
        _enqueue(repo, engine, parent="parent-1", report_msg="r-1", content="A")
        _enqueue(repo, engine, parent="parent-2", report_msg="r-2", content="B")

        drained = repo.claim_for_injection("parent-1")
        assert len(drained) == 1
        assert drained[0]["content"] == "A"
        # parent-2's report is untouched
        assert repo.count_pending_for_parent("parent-2") == 1


class TestQueueingMultipleReports:
    """The deadlock scenario from production: multiple workers complete
    near-simultaneously while the parent holds its turn open. All their
    reports must queue and drain together (single-slot replace would
    have lost all but the last)."""

    def test_multiple_reports_for_same_parent_all_drained(self, repo, engine):
        _enqueue(repo, engine, child="w-1", msg="m-1", report_msg="r-1", content="worker 1 report")
        _enqueue(repo, engine, child="w-2", msg="m-2", report_msg="r-2", content="worker 2 report")

        assert repo.count_pending_for_parent("parent-1") == 2

        drained = repo.claim_for_injection("parent-1")
        contents = [d["content"] for d in drained]

        # BOTH reports delivered — this is the core fix.
        assert set(contents) == {"worker 1 report", "worker 2 report"}
        assert repo.count_pending_for_parent("parent-1") == 0


# =============================================================================
# claim_for_task_delivery — fallback PROCESS_REPORT task
# =============================================================================


class TestClaimForTaskDelivery:
    """The fallback task delivery path (tri-state TaskDeliveryClaim)."""

    def test_task_claim_claimed_when_pending(self, repo, engine):
        _enqueue(repo, engine, report_msg="rmsg-1")
        claim = repo.claim_for_task_delivery("rmsg-1")

        assert claim.status == "claimed"
        assert claim.row is not None
        assert claim.row.state == ReportInjectionState.TASK_DELIVERED.value

    def test_task_claim_already_delivered_after_first_claim(self, repo, engine):
        """Exactly-once: a second task claim is 'already_delivered' (skip)."""
        _enqueue(repo, engine, report_msg="rmsg-1")
        assert repo.claim_for_task_delivery("rmsg-1").status == "claimed"
        second = repo.claim_for_task_delivery("rmsg-1")
        assert second.status == "already_delivered"
        assert second.row is None

    def test_task_claim_missing_when_no_row_exists(self, repo, engine):
        """CRITICAL (review B2): a missing row is 'missing', NOT
        'already_delivered' — the caller must proceed (not skip) so the
        report is not silently lost. Covers PROCESS_REPORT tasks from
        older code / paths that did not enqueue a report_injections row."""
        claim = repo.claim_for_task_delivery("never-enqueued")
        assert claim.status == "missing"
        assert claim.row is None

    def test_task_claim_missing_after_drain_is_actually_delivered(self, repo, engine):
        """After the live drain delivers (INJECTED), a task claim must read
        'already_delivered' (the row exists and is terminal), NOT 'missing'."""
        _enqueue(repo, engine, report_msg="rmsg-1")
        repo.claim_for_injection("parent-1")  # drain → INJECTED
        claim = repo.claim_for_task_delivery("rmsg-1")
        assert claim.status == "already_delivered"


# =============================================================================
# Exactly-once across the two delivery paths
# =============================================================================


class TestExactlyOnceAcrossPaths:
    """The critical invariant: a report is delivered by exactly one path."""

    def test_drain_then_task_loses(self, repo, engine):
        """If the live turn drains first, the fallback task skips."""
        _enqueue(repo, engine, report_msg="rmsg-1")

        assert len(repo.claim_for_injection("parent-1")) == 1
        # Fallback task now finds the row terminal → 'already_delivered' → skip.
        assert repo.claim_for_task_delivery("rmsg-1").status == "already_delivered"

    def test_task_then_drain_loses(self, repo, engine):
        """If the fallback task delivers first, a later drain skips it."""
        _enqueue(repo, engine, report_msg="rmsg-1")

        assert repo.claim_for_task_delivery("rmsg-1").status == "claimed"
        # A subsequent live-turn drain finds nothing PENDING for this report.
        assert repo.claim_for_injection("parent-1") == []
