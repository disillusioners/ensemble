"""Comprehensive tests for JobStateMachine.

This module tests the formal state machine for job lifecycle transitions.
"""

import pytest

from daemon.services.job_state_machine import (
    JobStateMachine,
    InvalidTransitionError,
    TRANSITIONS,
    job_state_machine,
)


class TestStateMachineCanTransition:
    """Tests for can_transition() method."""

    def test_can_transition_none_to_pending(self):
        """Test None -> PENDING is valid (job creation)."""
        sm = JobStateMachine()
        assert sm.can_transition(None, "pending") is True

    def test_can_transition_pending_to_processing(self):
        """Test PENDING -> PROCESSING is valid (job start)."""
        sm = JobStateMachine()
        assert sm.can_transition("pending", "processing") is True

    def test_can_transition_pending_to_cancelled(self):
        """Test PENDING -> CANCELLED is valid (job cancel)."""
        sm = JobStateMachine()
        assert sm.can_transition("pending", "cancelled") is True

    def test_can_transition_processing_to_completed(self):
        """Test PROCESSING -> COMPLETED is valid (job complete)."""
        sm = JobStateMachine()
        assert sm.can_transition("processing", "completed") is True

    def test_can_transition_processing_to_failed(self):
        """Test PROCESSING -> FAILED is valid (job fail)."""
        sm = JobStateMachine()
        assert sm.can_transition("processing", "failed") is True

    def test_can_transition_processing_to_cancelled(self):
        """Test PROCESSING -> CANCELLED is valid (job abort)."""
        sm = JobStateMachine()
        assert sm.can_transition("processing", "cancelled") is True

    def test_can_transition_failed_to_pending(self):
        """Test FAILED -> PENDING is valid (job retry)."""
        sm = JobStateMachine()
        assert sm.can_transition("failed", "pending") is True

    def test_can_transition_failed_to_dead_letter(self):
        """Test FAILED -> DEAD_LETTER is valid."""
        sm = JobStateMachine()
        assert sm.can_transition("failed", "dead_letter") is True

    def test_can_transition_dead_letter_to_pending(self):
        """Test DEAD_LETTER -> PENDING is valid (job replay)."""
        sm = JobStateMachine()
        assert sm.can_transition("dead_letter", "pending") is True


class TestStateMachineInvalidTransitions:
    """Tests for invalid transitions returning False."""

    def test_cannot_transition_completed_to_pending(self):
        """Test COMPLETED -> PENDING is invalid."""
        sm = JobStateMachine()
        assert sm.can_transition("completed", "pending") is False

    def test_cannot_transition_completed_to_processing(self):
        """Test COMPLETED -> PROCESSING is VALID as the orphan-race re-arm
        transition (added 2026-06-20). The JobFeedbackObserver
        ``_finalize_job`` post-commit re-check transitions a just-committed
        COMPLETED job back to PROCESSING when a concurrent
        ``register_message_send`` was in-flight during finalization. Without
        this transition the late child would be silently orphaned.
        """
        sm = JobStateMachine()
        assert sm.can_transition("completed", "processing") is True
        assert sm.get_transition_name("completed", "processing") == "rearm_after_complete"

    def test_cannot_transition_pending_to_completed(self):
        """Test PENDING -> COMPLETED is invalid."""
        sm = JobStateMachine()
        assert sm.can_transition("pending", "completed") is False

    def test_cannot_transition_pending_to_failed(self):
        """Test PENDING -> FAILED is invalid."""
        sm = JobStateMachine()
        assert sm.can_transition("pending", "failed") is False

    def test_cannot_transition_processing_to_pending(self):
        """Test PROCESSING -> PENDING is invalid (but 'requeue' is allowed for MESSAGE jobs)."""
        sm = JobStateMachine()
        assert sm.can_transition("processing", "pending") is True

    def test_cannot_transition_cancelled_to_pending(self):
        """Test CANCELLED -> PENDING is invalid."""
        sm = JobStateMachine()
        assert sm.can_transition("cancelled", "pending") is False

    def test_cannot_transition_cancelled_to_processing(self):
        """Test CANCELLED -> PROCESSING is invalid."""
        sm = JobStateMachine()
        assert sm.can_transition("cancelled", "processing") is False

    def test_cannot_transition_dead_letter_to_completed(self):
        """Test DEAD_LETTER -> COMPLETED is invalid."""
        sm = JobStateMachine()
        assert sm.can_transition("dead_letter", "completed") is False

    def test_cannot_transition_dead_letter_to_failed(self):
        """Test DEAD_LETTER -> FAILED is invalid."""
        sm = JobStateMachine()
        assert sm.can_transition("dead_letter", "failed") is False


class TestStateMachineGetTransitionName:
    """Tests for get_transition_name() method."""

    def test_get_transition_name_create(self):
        """Test get_transition_name for creation."""
        sm = JobStateMachine()
        assert sm.get_transition_name(None, "pending") == "create"

    def test_get_transition_name_start(self):
        """Test get_transition_name for job start."""
        sm = JobStateMachine()
        assert sm.get_transition_name("pending", "processing") == "start"

    def test_get_transition_name_cancel(self):
        """Test get_transition_name for cancel from pending."""
        sm = JobStateMachine()
        assert sm.get_transition_name("pending", "cancelled") == "cancel"

    def test_get_transition_name_complete(self):
        """Test get_transition_name for completion."""
        sm = JobStateMachine()
        assert sm.get_transition_name("processing", "completed") == "complete"

    def test_get_transition_name_fail(self):
        """Test get_transition_name for failure."""
        sm = JobStateMachine()
        assert sm.get_transition_name("processing", "failed") == "fail"

    def test_get_transition_name_abort(self):
        """Test get_transition_name for abort (processing cancel)."""
        sm = JobStateMachine()
        assert sm.get_transition_name("processing", "cancelled") == "abort"

    def test_get_transition_name_retry(self):
        """Test get_transition_name for retry."""
        sm = JobStateMachine()
        assert sm.get_transition_name("failed", "pending") == "retry"

    def test_get_transition_name_dead_letter(self):
        """Test get_transition_name for dead letter."""
        sm = JobStateMachine()
        assert sm.get_transition_name("failed", "dead_letter") == "dead_letter"

    def test_get_transition_name_replay(self):
        """Test get_transition_name for replay."""
        sm = JobStateMachine()
        assert sm.get_transition_name("dead_letter", "pending") == "replay"

    def test_get_transition_name_invalid_returns_none(self):
        """Test get_transition_name returns None for invalid transition."""
        sm = JobStateMachine()
        assert sm.get_transition_name("completed", "pending") is None


class TestStateMachineGetValidTransitions:
    """Tests for get_valid_transitions() method."""

    def test_get_valid_transitions_from_none(self):
        """Test valid transitions from None (new job)."""
        sm = JobStateMachine()
        result = sm.get_valid_transitions(None)
        assert ("pending", "create") in result
        assert len(result) == 1

    def test_get_valid_transitions_from_pending(self):
        """Test valid transitions from PENDING state."""
        sm = JobStateMachine()
        result = sm.get_valid_transitions("pending")
        targets = {target for target, name in result}
        assert "processing" in targets  # start
        assert "cancelled" in targets   # cancel
        assert len(result) == 2

    def test_get_valid_transitions_from_processing(self):
        """Test valid transitions from PROCESSING state."""
        sm = JobStateMachine()
        result = sm.get_valid_transitions("processing")
        targets = {target for target, name in result}
        assert "completed" in targets   # complete
        assert "failed" in targets      # fail
        assert "cancelled" in targets  # abort
        assert "pending" in targets  # requeue
        assert len(result) == 4

    def test_get_valid_transitions_from_failed(self):
        """Test valid transitions from FAILED state."""
        sm = JobStateMachine()
        result = sm.get_valid_transitions("failed")
        targets = {target for target, name in result}
        assert "pending" in targets       # retry
        assert "dead_letter" in targets   # dead_letter
        assert "cancelled" in targets    # cancel_after_fail
        assert len(result) == 3

    def test_get_valid_transitions_from_dead_letter(self):
        """Test valid transitions from DEAD_LETTER state."""
        sm = JobStateMachine()
        result = sm.get_valid_transitions("dead_letter")
        assert ("pending", "replay") in result
        assert len(result) == 1

    def test_get_valid_transitions_from_completed(self):
        """Test valid transitions from COMPLETED state.

        As of 2026-06-20 the only valid transition is the orphan-race
        re-arm (COMPLETED → PROCESSING via ``rearm_after_complete``).
        """
        sm = JobStateMachine()
        result = sm.get_valid_transitions("completed")
        assert result == [("processing", "rearm_after_complete")]

    def test_get_valid_transitions_from_cancelled(self):
        """Test valid transitions from CANCELLED state (none)."""
        sm = JobStateMachine()
        result = sm.get_valid_transitions("cancelled")
        assert result == []


class TestStateMachineValidateTransition:
    """Tests for validate_transition() method."""

    def test_validate_transition_valid(self):
        """Test validate_transition doesn't raise for valid transitions."""
        sm = JobStateMachine()
        # Should not raise
        sm.validate_transition(None, "pending")
        sm.validate_transition("pending", "processing")
        sm.validate_transition("processing", "completed")
        sm.validate_transition("processing", "failed")
        sm.validate_transition("processing", "cancelled")

    def test_validate_transition_invalid(self):
        """Test validate_transition raises InvalidTransitionError for invalid transitions."""
        sm = JobStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.validate_transition("completed", "pending")


class TestInvalidTransitionError:
    """Tests for InvalidTransitionError exception."""

    def test_error_attributes(self):
        """Test InvalidTransitionError has correct attributes."""
        error = InvalidTransitionError(
            job_id="job-123",
            from_status="pending",
            to_status="completed"
        )
        assert error.job_id == "job-123"
        assert error.from_status == "pending"
        assert error.to_status == "completed"

    def test_error_message(self):
        """Test InvalidTransitionError has correct message."""
        error = InvalidTransitionError(
            job_id="job-456",
            from_status="processing",
            to_status="pending"
        )
        assert "job-456" in str(error)
        assert "processing" in str(error)
        assert "pending" in str(error)
        assert "Invalid transition" in str(error)

    def test_error_with_none_from_status(self):
        """Test InvalidTransitionError with None from_status."""
        error = InvalidTransitionError(
            job_id="job-789",
            from_status=None,
            to_status="completed"
        )
        assert error.job_id == "job-789"
        assert error.from_status is None
        assert error.to_status == "completed"


class TestTransitionsConstant:
    """Tests for TRANSITIONS constant."""

    def test_transitions_has_twelve_entries(self):
        """Test TRANSITIONS dict has 12 entries (added rearm_after_complete for
        the orphan-race fix in 2026-06-20: COMPLETED → PROCESSING is now a
        legal transition for jobs that need to be re-armed when a late
        ``register_message_send`` was in-flight during finalization).
        """
        assert len(TRANSITIONS) == 12

    def test_transitions_contains_create(self):
        """Test TRANSITIONS contains create transition."""
        assert (None, "pending") in TRANSITIONS
        assert TRANSITIONS[(None, "pending")] == "create"

    def test_transitions_contains_start(self):
        """Test TRANSITIONS contains start transition."""
        assert ("pending", "processing") in TRANSITIONS
        assert TRANSITIONS[("pending", "processing")] == "start"

    def test_transitions_contains_cancel(self):
        """Test TRANSITIONS contains cancel transition."""
        assert ("pending", "cancelled") in TRANSITIONS
        assert TRANSITIONS[("pending", "cancelled")] == "cancel"

    def test_transitions_contains_complete(self):
        """Test TRANSITIONS contains complete transition."""
        assert ("processing", "completed") in TRANSITIONS
        assert TRANSITIONS[("processing", "completed")] == "complete"

    def test_transitions_contains_fail(self):
        """Test TRANSITIONS contains fail transition."""
        assert ("processing", "failed") in TRANSITIONS
        assert TRANSITIONS[("processing", "failed")] == "fail"

    def test_transitions_contains_abort(self):
        """Test TRANSITIONS contains abort transition."""
        assert ("processing", "cancelled") in TRANSITIONS
        assert TRANSITIONS[("processing", "cancelled")] == "abort"

    def test_transitions_contains_retry(self):
        """Test TRANSITIONS contains retry transition."""
        assert ("failed", "pending") in TRANSITIONS
        assert TRANSITIONS[("failed", "pending")] == "retry"

    def test_transitions_contains_dead_letter(self):
        """Test TRANSITIONS contains dead_letter transition."""
        assert ("failed", "dead_letter") in TRANSITIONS
        assert TRANSITIONS[("failed", "dead_letter")] == "dead_letter"

    def test_transitions_contains_replay(self):
        """Test TRANSITIONS contains replay transition."""
        assert ("dead_letter", "pending") in TRANSITIONS
        assert TRANSITIONS[("dead_letter", "pending")] == "replay"

    def test_transitions_contains_rearm_after_complete(self):
        """Test TRANSITIONS contains the orphan-race re-arm transition
        (COMPLETED → PROCESSING). This is the legal transition that
        ``JobFeedbackObserver._finalize_job`` uses to re-arm a job whose
        CM had a late ``register_message_send`` during finalization —
        without it, the late child is silently orphaned.
        """
        assert ("completed", "processing") in TRANSITIONS
        assert TRANSITIONS[("completed", "processing")] == "rearm_after_complete"

    def test_can_transition_rearm_after_complete(self):
        """Test ``can_transition`` accepts the re-arm transition."""
        sm = JobStateMachine()
        assert sm.can_transition("completed", "processing") is True
        assert sm.get_transition_name("completed", "processing") == "rearm_after_complete"

    def test_validate_transition_rearm_after_complete(self):
        """Test ``validate_transition`` does not raise for the re-arm."""
        sm = JobStateMachine()
        # Should not raise — the re-arm is a legal transition.
        sm.validate_transition("completed", "processing")


class TestJobStateMachineSingleton:
    """Tests for the job_state_machine singleton."""

    def test_singleton_exists(self):
        """Test job_state_machine singleton exists."""
        assert job_state_machine is not None

    def test_singleton_is_job_state_machine_instance(self):
        """Test job_state_machine is a JobStateMachine instance."""
        assert isinstance(job_state_machine, JobStateMachine)

    def test_singleton_can_transition(self):
        """Test singleton can_transition works."""
        assert job_state_machine.can_transition(None, "pending") is True
        assert job_state_machine.can_transition("pending", "processing") is True
        assert job_state_machine.can_transition("completed", "pending") is False

    def test_singleton_get_transition_name(self):
        """Test singleton get_transition_name works."""
        assert job_state_machine.get_transition_name(None, "pending") == "create"
        assert job_state_machine.get_transition_name("pending", "processing") == "start"

    def test_singleton_validate_transition(self):
        """Test singleton validate_transition works."""
        # Should not raise
        job_state_machine.validate_transition(None, "pending")
        job_state_machine.validate_transition("pending", "processing")

        # Should raise
        with pytest.raises(InvalidTransitionError):
            job_state_machine.validate_transition("completed", "pending")
