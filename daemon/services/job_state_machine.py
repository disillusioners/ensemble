"""Formal state machine for job lifecycle transitions."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Status string constants (avoid importing JobStatus to prevent circular import)
_STATUS_PENDING = "pending"
_STATUS_PROCESSING = "processing"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"
_STATUS_CANCELLED = "cancelled"
_STATUS_DEAD_LETTER = "dead_letter"

# State transition table: (from_state, to_state) -> transition_name
# Using string literals directly to avoid circular imports
TRANSITIONS: Dict[Tuple[Optional[str], str], str] = {
    (None, _STATUS_PENDING): "create",
    (_STATUS_PENDING, _STATUS_PROCESSING): "start",
    (_STATUS_PENDING, _STATUS_CANCELLED): "cancel",
    (_STATUS_PROCESSING, _STATUS_COMPLETED): "complete",
    (_STATUS_PROCESSING, _STATUS_FAILED): "fail",
    (_STATUS_PROCESSING, _STATUS_CANCELLED): "abort",
    (_STATUS_FAILED, _STATUS_PENDING): "retry",
    (_STATUS_FAILED, _STATUS_DEAD_LETTER): "dead_letter",
    (_STATUS_DEAD_LETTER, _STATUS_PENDING): "replay",
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, job_id: str, from_status: Optional[str], to_status: str) -> None:
        self.job_id = job_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Invalid transition for job {job_id}: {from_status} → {to_status}"
        )


class JobStateMachine:
    """Formal state machine for job lifecycle transitions."""

    def can_transition(self, from_status: Optional[str], to_status: str) -> bool:
        """Check if a transition is valid.

        Args:
            from_status: Current state (None for new jobs).
            to_status: Target state.

        Returns:
            True if the transition is allowed.
        """
        return (from_status, to_status) in TRANSITIONS

    def get_transition_name(
        self, from_status: Optional[str], to_status: str
    ) -> Optional[str]:
        """Get the name of a transition.

        Args:
            from_status: Current state (None for new jobs).
            to_status: Target state.

        Returns:
            Transition name (e.g., 'start', 'complete') or None if invalid.
        """
        return TRANSITIONS.get((from_status, to_status))

    def get_valid_transitions(
        self, from_status: Optional[str]
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
        self, from_status: Optional[str], to_status: str
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
