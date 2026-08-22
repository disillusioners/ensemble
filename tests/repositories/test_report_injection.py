"""Unit tests for the ReportInjection repository.

Validates the exactly-once delivery semantics that fix the
parent-waits-for-child deadlock: a child completion report is
delivered by EXACTLY ONE of two paths — the live agent-node drain
(``claim_for_injection`` → ``INJECTED``) or the fallback
``PROCESS_REPORT`` task (``claim_for_task_delivery`` →
``TASK_DELIVERED``) — never both, never neither.

Phase 1 (pause-report-recovery) adds the DEFERRED marker tests:
``ensure_deferred`` write-once gate, ``find_deferred_for_parent``,
``transition_deferred_to_pending``, ``count_pending_for_parent``
semantics broadening (PENDING ∪ DEFERRED), and the C1
case-lockstep IntegrityError assertion.

All tests run against the in-memory SQLite ``engine`` fixture (see
``tests/repositories/conftest.py``), which creates the
``report_injections`` and ``message_queue`` tables via
``SQLModel.metadata.create_all``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from daemon.constants import (
    DEFERRED_REASON_PAUSE_TOCTOU,
    DEFERRED_REASON_PENDING_MESSAGES,
)
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


# =============================================================================
# Phase 1 — DEFERRED marker lifecycle
# =============================================================================


class TestDeferredEnumAndStateShape:
    """Phase 1: ReportInjectionState exposes DEFERRED; rows may be
    written with NULL ``report_message_id`` and NULL ``content``
    (the pre-artifact Site-1 shape)."""

    def test_deferred_is_uppercase_storage_literal(self):
        """C1 case-lockstep: enum value must be uppercase 'DEFERRED'."""
        assert ReportInjectionState.DEFERRED.value == "DEFERRED"

    def test_pending_uppercase_storage_literal(self):
        """C1 case-lockstep: PENDING must stay uppercase."""
        assert ReportInjectionState.PENDING.value == "PENDING"

    def test_injected_uppercase_storage_literal(self):
        """C1 case-lockstep: INJECTED must stay uppercase."""
        assert ReportInjectionState.INJECTED.value == "INJECTED"

    def test_task_delivered_uppercase_storage_literal(self):
        """C1 case-lockstep: TASK_DELIVERED must stay uppercase."""
        assert (
            ReportInjectionState.TASK_DELIVERED.value == "TASK_DELIVERED"
        )

    def test_pending_row_with_null_report_message_id_accepted(self, repo, engine):
        """C4: NULL report_message_id is permitted (Phase 1 marker
        shape). The pre-artifact Site-1 marker has no artifact yet;
        Phase 2 reconciliation handles ``report_message_id IS NULL``."""
        with Session(engine) as session:
            row = ReportInjection(
                parent_instance_id="parent-x",
                child_instance_id="child-x",
                child_message_id="msg-x",
                report_message_id=None,  # ← C4: nullable
                content=None,  # ← pre-artifact shape
                state=ReportInjectionState.DEFERRED.value,
                deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            assert row.report_message_id is None
            assert row.content is None
            assert row.state == ReportInjectionState.DEFERRED.value


class TestEnsureDeferredWriteOnceGate:
    """Phase 1 (Task 1.2): ``ensure_deferred`` is the write-once
    gate on the obligation triple. The partial unique index
    ``uq_report_injections_oblig_triple``
    (``WHERE state IN ('PENDING','DEFERRED')``) rejects concurrent
    duplicates; ``ensure_deferred`` absorbs the IntegrityError (W6)
    and returns None (no-op)."""

    def test_ensure_deferred_first_call_writes_row(
        self, repo, engine
    ) -> None:
        row = repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        assert row is not None
        assert row.state == ReportInjectionState.DEFERRED.value
        assert row.deferred_reason == DEFERRED_REASON_PAUSE_TOCTOU
        assert row.report_message_id is None
        assert row.content is None
        assert row.recovery_attempted_at is None

    def test_ensure_deferred_twice_returns_no_op(
        self, repo, engine
    ) -> None:
        """Two ``ensure_deferred`` calls for the same triple must
        produce ONE row (the partial unique index absorbs the
        duplicate)."""
        first = repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        second = repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        # First call inserts; second is a no-op (returns None, no
        # new row).
        assert first is not None
        assert second is None
        # Verify only one row exists.
        with Session(engine) as session:
            from sqlmodel import select as sm_select
            rows = list(
                session.exec(
                    sm_select(ReportInjection).where(
                        ReportInjection.parent_instance_id == "parent-1"
                    )
                ).all()
            )
            assert len(rows) == 1

    def test_ensure_deferred_updates_existing_reason_when_different(
        self, repo, engine
    ) -> None:
        """If a DEFERRED/PENDING row exists for the same triple and
        the new reason differs, ``ensure_deferred`` updates the
        reason in-place (no duplication, never escalates state)."""
        first = repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        assert first is not None
        assert first.deferred_reason == DEFERRED_REASON_PAUSE_TOCTOU
        # Second call with a DIFFERENT reason → reason updated,
        # state unchanged, returned row reflects the update.
        updated = repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PENDING_MESSAGES,
        )
        assert updated is not None
        assert updated.deferred_reason == DEFERRED_REASON_PENDING_MESSAGES
        # State must NOT escalate — still DEFERRED.
        assert updated.state == ReportInjectionState.DEFERRED.value

    def test_ensure_deferred_after_terminal_allowed(
        self, repo, engine
    ) -> None:
        """After the live drain delivers a PENDING row (terminal
        INJECTED), a NEW ``ensure_deferred`` for the same triple is
        allowed — terminal rows are out of the index predicate, so a
        fresh non-terminal obligation (re-spawn scenario) can be
        recorded."""
        _enqueue(repo, engine)  # PENDING row
        repo.claim_for_injection("parent-1")  # → INJECTED (terminal)
        new_marker = repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        assert new_marker is not None
        assert new_marker.state == ReportInjectionState.DEFERRED.value

    def test_duplicate_non_terminal_triple_raises_integrity_error(
        self, repo, engine
    ) -> None:
        """C1 ASSERTION: a duplicate non-terminal triple raises
        ``sqlalchemy.exc.IntegrityError``. This is the storage-layer
        gate the partial unique index enforces; ``ensure_deferred``
        catches it (W6) but the bare INSERT path lets it propagate."""
        repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        # Direct INSERT (bypassing ``ensure_deferred``'s W6 absorption)
        # must raise IntegrityError.
        with Session(engine) as session:
            dup = ReportInjection(
                parent_instance_id="parent-1",
                child_instance_id="child-1",
                child_message_id="msg-1",
                state=ReportInjectionState.DEFERRED.value,
            )
            session.add(dup)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()


class TestFindDeferredForParent:
    """Phase 1: ``find_deferred_for_parent`` lists DEFERRED markers
    awaiting recovery for a parent (oldest first)."""

    def test_returns_only_deferred_for_target_parent(self, repo, engine):
        repo.ensure_deferred(
            parent_instance_id="parent-A",
            child_instance_id="child-A1",
            child_message_id="m-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        repo.ensure_deferred(
            parent_instance_id="parent-B",
            child_instance_id="child-B1",
            child_message_id="m-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        rows = repo.find_deferred_for_parent("parent-A")
        assert len(rows) == 1
        assert rows[0].parent_instance_id == "parent-A"

    def test_empty_when_no_deferred_markers(self, repo, engine):
        assert repo.find_deferred_for_parent("nobody") == []

    def test_ordered_by_created_at_ascending(self, repo, engine):
        # Insert three markers for the same parent (different
        # children — different triples so the index does not block).
        first = repo.ensure_deferred(
            parent_instance_id="parent-A",
            child_instance_id="child-1",
            child_message_id="m-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        second = repo.ensure_deferred(
            parent_instance_id="parent-A",
            child_instance_id="child-2",
            child_message_id="m-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        third = repo.ensure_deferred(
            parent_instance_id="parent-A",
            child_instance_id="child-3",
            child_message_id="m-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        rows = repo.find_deferred_for_parent("parent-A")
        assert [r.injection_id for r in rows] == [
            first.injection_id,
            second.injection_id,
            third.injection_id,
        ]

    def test_excludes_pending_and_terminal(self, repo, engine):
        """``find_deferred_for_parent`` returns ONLY DEFERRED rows;
        PENDING and terminal rows (INJECTED / TASK_DELIVERED) are
        not DEFERRED obligations awaiting recovery."""
        repo.ensure_deferred(
            parent_instance_id="parent-A",
            child_instance_id="child-1",
            child_message_id="m-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        # PENDING row (different triple)
        repo.enqueue(
            parent_instance_id="parent-A",
            child_instance_id="child-2",
            child_message_id="m-1",
            report_message_id="rmsg-2",
            content="content",
        )
        # Terminal row (different triple)
        repo.enqueue(
            parent_instance_id="parent-A",
            child_instance_id="child-3",
            child_message_id="m-1",
            report_message_id="rmsg-3",
            content="content",
        )
        repo.claim_for_injection("parent-A")  # drains PENDING + terminal

        rows = repo.find_deferred_for_parent("parent-A")
        assert len(rows) == 1
        assert rows[0].state == ReportInjectionState.DEFERRED.value


class TestTransitionDeferredToPending:
    """Phase 1: ``transition_deferred_to_pending`` atomically
    transitions DEFERRED → PENDING (guarded UPDATE) and stamps
    ``recovery_attempted_at``."""

    def test_transitions_deferred_to_pending_and_stamps(
        self, repo, engine
    ) -> None:
        marker = repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        assert marker.recovery_attempted_at is None

        result = repo.transition_deferred_to_pending(marker.injection_id)
        assert result is True

        # Verify the row is now PENDING with a recovery_attempted_at
        # stamp.
        with Session(engine) as session:
            from sqlmodel import select as sm_select
            row = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.injection_id == marker.injection_id
                )
            ).first()
        assert row is not None
        assert row.state == ReportInjectionState.PENDING.value
        assert row.recovery_attempted_at is not None

    def test_transition_returns_false_when_not_deferred(
        self, repo, engine
    ) -> None:
        """A non-DEFERRED row cannot transition (rowcount=0)."""
        _enqueue(repo, engine)  # PENDING row
        result = repo.transition_deferred_to_pending(
            # Find the row we just enqueued.
            _row_id_for(repo, "parent-1")
        )
        assert result is False

    def test_transition_returns_false_when_missing(
        self, repo, engine
    ) -> None:
        """No row for the injection_id → rowcount=0 → False."""
        result = repo.transition_deferred_to_pending("nonexistent-id")
        assert result is False

    def test_transition_twice_only_first_succeeds(
        self, repo, engine
    ) -> None:
        """Two concurrent recoveries: only the first wins
        (rowcount=0 for the second)."""
        marker = repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        first = repo.transition_deferred_to_pending(marker.injection_id)
        second = repo.transition_deferred_to_pending(marker.injection_id)
        assert first is True
        assert second is False


class TestClaimMethodsIgnoreDeferred:
    """Phase 1: the two delivery claim paths (``claim_for_injection``
    and ``claim_for_task_delivery``) must NOT see DEFERRED rows —
    their ``WHERE state='PENDING'`` guards make this true by
    construction. A DEFERRED row stays invisible until
    ``transition_deferred_to_pending`` recovers it."""

    def test_claim_for_injection_ignores_deferred(self, repo, engine):
        repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        # Live drain finds nothing — DEFERRED is not PENDING.
        drained = repo.claim_for_injection("parent-1")
        assert drained == []
        # And the DEFERRED row is still DEFERRED (untouched).
        rows = repo.find_deferred_for_parent("parent-1")
        assert len(rows) == 1

    def test_claim_for_task_delivery_ignores_deferred(
        self, repo, engine
    ) -> None:
        """A DEFERRED row has no ``report_message_id``, so the task
        claim's ``WHERE report_message_id == ...`` lookup naturally
        misses it (the row is invisible to the fallback path)."""
        repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        # No row to claim for any report_message_id (the marker has
        # NULL report_message_id; the fallback task claim looks up by
        # a specific report_message_id which the marker never has).
        claim = repo.claim_for_task_delivery("any-rmsg")
        assert claim.status == "missing"


class TestCountPendingUnionDeferred:
    """Phase 1: ``count_pending_for_parent`` is broadened from
    PENDING-only to ``PENDING ∪ DEFERRED`` — a DEFERRED marker is
    an outstanding delivery obligation."""

    def test_counts_deferred_rows_as_outstanding(self, repo, engine):
        repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        # The marker counts even though it is DEFERRED (not PENDING).
        assert repo.count_pending_for_parent("parent-1") == 1

    def test_counts_pending_and_deferred_separately(self, repo, engine):
        # One DEFERRED marker.
        repo.ensure_deferred(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            child_message_id="msg-1",
            deferred_reason=DEFERRED_REASON_PAUSE_TOCTOU,
        )
        # One PENDING row (different triple).
        repo.enqueue(
            parent_instance_id="parent-1",
            child_instance_id="child-2",
            child_message_id="msg-2",
            report_message_id="rmsg-2",
            content="content",
        )
        # Count is the union: 1 (DEFERRED) + 1 (PENDING) = 2.
        assert repo.count_pending_for_parent("parent-1") == 2

    def test_terminal_rows_not_counted(self, repo, engine):
        _enqueue(repo, engine)
        repo.claim_for_injection("parent-1")  # → INJECTED
        # Terminal rows are not outstanding obligations.
        assert repo.count_pending_for_parent("parent-1") == 0


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _row_id_for(repo, parent_instance_id: str) -> str:
    """Return the ``injection_id`` of the first PENDING row for a
    parent (helper for the not-DEFERRED transition test)."""
    # Reach into the repo's engine — tests-only helper.
    with Session(repo.engine) as session:
        from sqlmodel import select as sm_select

        row = session.exec(
            sm_select(ReportInjection).where(
                ReportInjection.parent_instance_id == parent_instance_id
            )
        ).first()
    assert row is not None
    return row.injection_id
