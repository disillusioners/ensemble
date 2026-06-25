"""Formal state machine for job lifecycle transitions."""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# Status string constants (avoid importing JobStatus to prevent circular import)
_STATUS_PENDING = "pending"
_STATUS_PROCESSING = "processing"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"
_STATUS_CANCELLED = "cancelled"
_STATUS_DEAD_LETTER = "dead_letter"
_STATUS_PAUSED = "paused"

# State transition table: (from_state, to_state) -> transition_name
# Using string literals directly to avoid circular imports
TRANSITIONS: Dict[Tuple[str | None, str], str] = {
    (None, _STATUS_PENDING): "create",
    (_STATUS_PENDING, _STATUS_PROCESSING): "start",
    (_STATUS_PENDING, _STATUS_CANCELLED): "cancel",
    (_STATUS_PROCESSING, _STATUS_COMPLETED): "complete",
    (_STATUS_PROCESSING, _STATUS_FAILED): "fail",
    (_STATUS_PROCESSING, _STATUS_CANCELLED): "abort",
    (_STATUS_PROCESSING, _STATUS_PENDING): "requeue",
    (_STATUS_FAILED, _STATUS_PENDING): "retry",
    (_STATUS_FAILED, _STATUS_DEAD_LETTER): "dead_letter",
    (_STATUS_FAILED, _STATUS_CANCELLED): "cancel_after_fail",
    (_STATUS_DEAD_LETTER, _STATUS_PENDING): "replay",
    # Pause/resume transitions (Phase 1 of pause/resume redesign, 2026-06-25):
    # Allow a running job to be suspended (PROCESSING→PAUSED), resumed back to
    # PROCESSING (PAUSED→PROCESSING), or terminated while paused
    # (PAUSED→CANCELLED). JobStatus.PAUSED enum is added in a parallel task.
    (_STATUS_PROCESSING, _STATUS_PAUSED): "pause",
    (_STATUS_PAUSED, _STATUS_PROCESSING): "resume",
    (_STATUS_PAUSED, _STATUS_CANCELLED): "cancel_after_pause",
    # Orphan-race re-arm (2026-06-20): after a job is committed to COMPLETED
    # the post-commit re-check in JobFeedbackObserver._finalize_job may detect
    # a concurrent ``register_message_send`` that bumped the CM generation
    # counter during finalization. The job must be transitioned back to
    # PROCESSING so the late child's eventual resolve can find a PROCESSING
    # job (otherwise ``_get_processing_job_for_instance`` returns None and
    # the child is silently orphaned). This transition is the only legal
    # way to un-stick a finalized job whose CM had a late register.
    (_STATUS_COMPLETED, _STATUS_PROCESSING): "rearm_after_complete",
}


class InvalidTransitionError(ValueError):
    """Raised when an invalid state transition is attempted.

    Inheriting from ``ValueError`` (instead of plain ``Exception``) lets
    callers catch both ``InvalidTransitionError`` and other value-style
    validation errors with a single ``except ValueError`` clause. Existing
    ``except InvalidTransitionError`` and ``isinstance(e, InvalidTransitionError)``
    checks keep working because the subclass relationship is preserved.
    """

    def __init__(self, job_id: str, from_status: str | None, to_status: str) -> None:
        self.job_id = job_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Invalid transition for job {job_id}: {from_status} → {to_status}"
        )


class JobStateMachine:
    """Formal state machine for job lifecycle transitions."""

    def can_transition(self, from_status: str | None, to_status: str) -> bool:
        """Check if a transition is valid.

        Args:
            from_status: Current state (None for new jobs).
            to_status: Target state.

        Returns:
            True if the transition is allowed.
        """
        return (from_status, to_status) in TRANSITIONS

    def get_transition_name(
        self, from_status: str | None, to_status: str
    ) -> str | None:
        """Get the name of a transition.

        Args:
            from_status: Current state (None for new jobs).
            to_status: Target state.

        Returns:
            Transition name (e.g., 'start', 'complete') or None if invalid.
        """
        return TRANSITIONS.get((from_status, to_status))

    def get_valid_transitions(
        self, from_status: str | None
    ) -> List[tuple[str, str]]:
        """Get all valid target states from a given state.

        Args:
            from_status: Current state (None for new jobs).

        Returns:
            List of (target_state, transition_name) tuples.
        """
        return [
            (to_state, name)
            for (f_state, to_state), name in TRANSITIONS.items()
            if f_state == from_status
        ]

    def validate_transition(
        self, from_status: str | None, to_status: str
    ) -> None:
        """Validate transition, raising InvalidTransitionError if invalid.

        Args:
            from_status: Current state (None for new jobs).
            to_status: Target state.

        Raises:
            InvalidTransitionError: If the transition is not allowed.
        """
        if not self.can_transition(from_status, to_status):
            raise InvalidTransitionError(
                job_id="",  # Job ID should be provided by caller
                from_status=from_status,
                to_status=to_status,
            )


# Singleton instance for convenience
job_state_machine = JobStateMachine()
