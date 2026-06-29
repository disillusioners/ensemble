"""Comprehensive tests for JobStateMachine (Phase 5 admission vocabulary).

This module tests the formal state machine for job lifecycle transitions.
Phase 5 rewrote the state machine against the 4-value ``AdmissionState``
vocabulary (``queued`` / ``active`` / ``done`` / ``dead``) instead of
the legacy 7-value ``JobStatus`` enum. ``VALID_TRANSITIONS`` is now a
``set[tuple[str, str]]`` keyed on admission values; transition names
(``"start"`` / ``"retry"`` / ...) are no longer surfaced by the API
because the consumer (``_finalize_terminal``) selects the variant via
the ``Decision`` enum, not the from/to pair.
"""

import pytest

from daemon.services.job_state_machine import (
    JobStateMachine,
    InvalidTransitionError,
    VALID_TRANSITIONS,
    job_state_machine,
)


class TestStateMachineCanTransition:
    """Tests for can_transition() method on the admission vocabulary."""

    def test_can_transition_queued_to_active(self):
        """Queued -> Active is valid (job start)."""
        sm = JobStateMachine()
        assert sm.can_transition("queued", "active") is True

    def test_can_transition_queued_to_done(self):
        """Queued -> Done is valid (cancel-from-pending)."""
        sm = JobStateMachine()
        assert sm.can_transition("queued", "done") is True

    def test_can_transition_active_to_done(self):
        """Active -> Done is valid (complete / fail / abort)."""
        sm = JobStateMachine()
        assert sm.can_transition("active", "done") is True

    def test_can_transition_active_to_queued(self):
        """Active -> Queued is valid (retry)."""
        sm = JobStateMachine()
        assert sm.can_transition("active", "queued") is True

    def test_can_transition_active_to_dead(self):
        """Active -> Dead is valid (DEAD_LETTER decision)."""
        sm = JobStateMachine()
        assert sm.can_transition("active", "dead") is True

    def test_can_transition_done_to_queued(self):
        """Done -> Queued is valid (replay from done)."""
        sm = JobStateMachine()
        assert sm.can_transition("done", "queued") is True

    def test_can_transition_dead_to_queued(self):
        """Dead -> Queued is valid (replay from DLQ)."""
        sm = JobStateMachine()
        assert sm.can_transition("dead", "queued") is True

    def test_can_transition_done_to_active(self):
        """Done -> Active is valid (orphan-race post-commit re-arm)."""
        sm = JobStateMachine()
        assert sm.can_transition("done", "active") is True

    def test_can_transition_same_state_active(self):
        """Same-state Active -> Active is a valid no-op (pause/resume reconcile)."""
        sm = JobStateMachine()
        assert sm.can_transition("active", "active") is True

    def test_can_transition_same_state_done(self):
        """Same-state Done -> Done is a valid no-op (idempotent finalize retries)."""
        sm = JobStateMachine()
        assert sm.can_transition("done", "done") is True

    def test_cannot_transition_queued_to_dead(self):
        """Queued -> Dead is invalid (must go through Active)."""
        sm = JobStateMachine()
        assert sm.can_transition("queued", "dead") is False

    def test_cannot_transition_dead_to_active(self):
        """Dead -> Active is invalid (only Dead -> Queued is allowed)."""
        sm = JobStateMachine()
        assert sm.can_transition("dead", "active") is False

    def test_cannot_transition_dead_to_done(self):
        """Dead -> Done is invalid."""
        sm = JobStateMachine()
        assert sm.can_transition("dead", "done") is False

    def test_cannot_transition_done_to_dead(self):
        """Done -> Dead is invalid (only Active -> Dead is allowed)."""
        sm = JobStateMachine()
        assert sm.can_transition("done", "dead") is False


class TestStateMachineValidateTransition:
    """Tests for validate_transition() method."""

    def test_validate_transition_valid(self):
        """Test validate_transition doesn't raise for valid transitions."""
        sm = JobStateMachine()
        # Should not raise
        sm.validate_transition("queued", "active")
        sm.validate_transition("active", "done")
        sm.validate_transition("active", "queued")
        sm.validate_transition("active", "dead")
        sm.validate_transition("done", "queued")
        sm.validate_transition("dead", "queued")

    def test_validate_transition_same_state_no_op(self):
        """Test validate_transition treats same-state transitions as no-ops."""
        sm = JobStateMachine()
        # Should not raise — same-state is implicit no-op.
        sm.validate_transition("active", "active")
        sm.validate_transition("done", "done")

    def test_validate_transition_invalid_raises(self):
        """Test validate_transition raises InvalidTransitionError for invalid transitions."""
        sm = JobStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.validate_transition("queued", "dead")

    def test_validate_transition_dead_to_active_raises(self):
        """Test Dead -> Active raises InvalidTransitionError."""
        sm = JobStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.validate_transition("dead", "active")


class TestInvalidTransitionError:
    """Tests for InvalidTransitionError exception (Phase 5: from_state/to_state attrs)."""

    def test_error_attributes(self):
        """Test InvalidTransitionError has from_state/to_state attributes (not from_status/to_status)."""
        error = InvalidTransitionError(
            job_id="job-123",
            from_state="queued",
            to_state="done",
        )
        assert error.job_id == "job-123"
        assert error.from_state == "queued"
        assert error.to_state == "done"

    def test_error_message(self):
        """Test InvalidTransitionError has correct message text."""
        error = InvalidTransitionError(
            job_id="job-456",
            from_state="active",
            to_state="queued",
        )
        assert "job-456" in str(error)
        assert "active" in str(error)
        assert "queued" in str(error)
        assert "Invalid transition" in str(error)

    def test_error_with_none_from_state(self):
        """Test InvalidTransitionError accepts a None from_state."""
        error = InvalidTransitionError(
            job_id="job-789",
            from_state=None,
            to_state="done",
        )
        assert error.job_id == "job-789"
        assert error.from_state is None
        assert error.to_state == "done"

    def test_error_inherits_from_value_error(self):
        """Test InvalidTransitionError inherits from ValueError.

        Phase 5 design choice: lets callers catch both with a single
        ``except ValueError`` clause. Existing ``except InvalidTransitionError``
        and ``isinstance`` checks keep working.
        """
        error = InvalidTransitionError(
            job_id="job-001",
            from_state="queued",
            to_state="dead",
        )
        assert isinstance(error, ValueError)


class TestValidTransitionsConstant:
    """Tests for VALID_TRANSITIONS set."""

    def test_valid_transitions_has_eight_entries(self):
        """VALID_TRANSITIONS contains exactly 8 entries under the admission vocabulary.

        History:
          * 15 — pre-Phase-5 (legacy JobStatus 7-state machine, named dict).
          * 8 — Phase 5 collapsed the named dict onto a set-of-tuples and
            merged the terminal ``done`` family onto ``active -> done``.
            Pausing (``processing -> paused``) is an Instance concern and
            never moves the admission column, so the corresponding set
            entries were dropped.
        """
        assert len(VALID_TRANSITIONS) == 8

    def test_valid_transitions_contains_start(self):
        """VALID_TRANSITIONS contains the queued -> active start transition."""
        assert ("queued", "active") in VALID_TRANSITIONS

    def test_valid_transitions_contains_cancel_pending(self):
        """VALID_TRANSITIONS contains queued -> done (cancel-from-pending)."""
        assert ("queued", "done") in VALID_TRANSITIONS

    def test_valid_transitions_contains_complete_fail_abort(self):
        """VALID_TRANSITIONS contains active -> done (terminal boundary)."""
        assert ("active", "done") in VALID_TRANSITIONS

    def test_valid_transitions_contains_retry(self):
        """VALID_TRANSITIONS contains active -> queued (retry)."""
        assert ("active", "queued") in VALID_TRANSITIONS

    def test_valid_transitions_contains_dead_letter(self):
        """VALID_TRANSITIONS contains active -> dead (DEAD_LETTER decision)."""
        assert ("active", "dead") in VALID_TRANSITIONS

    def test_valid_transitions_contains_replay_from_done(self):
        """VALID_TRANSITIONS contains done -> queued (replay from done)."""
        assert ("done", "queued") in VALID_TRANSITIONS

    def test_valid_transitions_contains_replay_from_dlq(self):
        """VALID_TRANSITIONS contains dead -> queued (replay from DLQ)."""
        assert ("dead", "queued") in VALID_TRANSITIONS

    def test_valid_transitions_contains_orphan_rearm(self):
        """VALID_TRANSITIONS contains the orphan-race re-arm: done -> active.

        Used by ``JobFeedbackObserver._finalize_job`` post-commit when a
        concurrent ``register_message_send`` was in-flight during
        finalization. Without this transition the late child would be
        silently orphaned.
        """
        assert ("done", "active") in VALID_TRANSITIONS

    def test_valid_transitions_does_not_contain_none_create(self):
        """VALID_TRANSITIONS does NOT contain (None, queued).

        The create path is an INSERT into a fresh row, not a state
        transition. Callers that previously passed ``from_status=None``
        are moved to the INSERT path; the set-based API raises
        ``InvalidTransitionError`` for ``None`` (matches the pre-Phase-5
        behavior of "INSERTs don't go through the state machine").
        """
        assert (None, "queued") not in VALID_TRANSITIONS


class TestJobStateMachineSingleton:
    """Tests for the job_state_machine singleton."""

    def test_singleton_exists(self):
        """Test job_state_machine singleton exists."""
        assert job_state_machine is not None

    def test_singleton_is_job_state_machine_instance(self):
        """Test job_state_machine is a JobStateMachine instance."""
        assert isinstance(job_state_machine, JobStateMachine)

    def test_singleton_can_transition_valid(self):
        """Test singleton can_transition accepts valid transitions."""
        assert job_state_machine.can_transition("queued", "active") is True
        assert job_state_machine.can_transition("active", "done") is True

    def test_singleton_can_transition_invalid(self):
        """Test singleton can_transition rejects invalid transitions."""
        assert job_state_machine.can_transition("queued", "dead") is False
        assert job_state_machine.can_transition("dead", "active") is False

    def test_singleton_validate_transition_does_not_raise(self):
        """Test singleton validate_transition does not raise for valid transitions."""
        # Should not raise
        job_state_machine.validate_transition("queued", "active")
        job_state_machine.validate_transition("active", "done")
        job_state_machine.validate_transition("done", "queued")

    def test_singleton_validate_transition_raises(self):
        """Test singleton validate_transition raises for invalid transitions."""
        with pytest.raises(InvalidTransitionError):
            job_state_machine.validate_transition("queued", "dead")
