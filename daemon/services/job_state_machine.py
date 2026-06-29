"""Formal state machine for job admission-state transitions (Phase 5).

Replaces the legacy ``JobStatus``-keyed TRANSITIONS dict with a
``VALID_TRANSITIONS`` set-of-tuples keyed on ``AdmissionState``
values. The 7-value enum and the per-transition name strings
(``"start"``/``"complete"``/...) collapsed under the queue-proxy
model:

    * complete / fail / cancel / abort all map to ``active → done``
      — the consumer of the boundary (``_finalize_terminal``) now
      selects the variant via the ``Decision`` enum (``NO_RETRY`` /
      ``RETRY`` / ``DEAD_LETTER``), not the from/to state pair.
    * ``(X, X)`` "self loop" entries (active → active for the
      pause/no-op reconcile path, done → done for idempotent finalize
      retries) are treated as implicitly valid no-ops by
      :meth:`JobStateMachine.can_transition` / ``validate_transition``;
      pause/resume are Instance concerns and never move the admission
      column. The set-based membership check on
      :data:`VALID_TRANSITIONS` keeps its strict semantics — same-state
      entries are NOT enumerated there because they are categorically
      no-ops, not state transitions.
    * the ``(None, queued)`` "create" entry is omitted — the create
      path is an INSERT into a fresh row, not a state transition.
      Callers that previously passed ``from_status=None`` are moved
      to the INSERT path; the set-based API raises
      ``InvalidTransitionError`` for ``None`` (matches the pre-Phase-5
      behavior of "INSERTs don't go through the state machine").

Per-call reason names (``start``/``complete``/``retry``/...) were
used only for logging in :func:`repository.atomic_transition`'s
``logger.info("... | %s -> %s (%s) | ...")`` call. With the
queue-proxy model the ``(from, to)`` pair uniquely identifies the
transition, so the name is redundant — the log call has been
updated to drop the name and emit ``(from, to)`` directly.
"""

from __future__ import annotations

import logging
from typing import Set, Tuple

from daemon.repositories.job_queue.models import AdmissionState

logger = logging.getLogger(__name__)


# Phase 5: transitions on the 4-value admission vocabulary.
# 8 entries cover every transition exercised by ``_finalize_terminal``,
# ``cancel_job``, ``JobRetryEngine.maybe_retry`` (RETRY → ``active →
# queued``), the DLQ replay path (``done → queued`` / ``dead →
# queued``), and the orphan-race post-commit re-arm in
# ``JobFeedbackObserver`` (``done → active``).
VALID_TRANSITIONS: Set[Tuple[str, str]] = {
    (AdmissionState.QUEUED.value, AdmissionState.ACTIVE.value),   # start
    (AdmissionState.QUEUED.value, AdmissionState.DONE.value),     # cancel pending
    (AdmissionState.ACTIVE.value, AdmissionState.DONE.value),     # complete / fail / cancel / abort (NO_RETRY)
    (AdmissionState.ACTIVE.value, AdmissionState.QUEUED.value),   # retry (RETRY)
    (AdmissionState.ACTIVE.value, AdmissionState.DEAD.value),     # dead-letter (DEAD_LETTER)
    (AdmissionState.DONE.value, AdmissionState.QUEUED.value),     # replay from done
    (AdmissionState.DEAD.value, AdmissionState.QUEUED.value),     # replay from DLQ
    (AdmissionState.DONE.value, AdmissionState.ACTIVE.value),     # orphan-race post-commit re-arm
}


class InvalidTransitionError(ValueError):
    """Raised when an invalid admission-state transition is attempted.

    Inheriting from ``ValueError`` (instead of plain ``Exception``) lets
    callers catch both ``InvalidTransitionError`` and other value-style
    validation errors with a single ``except ValueError`` clause. Existing
    ``except InvalidTransitionError`` and ``isinstance(e, InvalidTransitionError)``
    checks keep working because the subclass relationship is preserved.
    """

    def __init__(self, job_id: str, from_state: str | None, to_state: str) -> None:
        self.job_id = job_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Invalid transition for job {job_id}: {from_state} \u2192 {to_state}"
        )


class JobStateMachine:
    """Formal state machine for job admission-state transitions."""

    def can_transition(self, from_state: str | None, to_state: str) -> bool:
        """Check if a transition is valid.

        Same-state transitions (``from_state == to_state``) are
        treated as implicit no-ops and return ``True``: pause/resume
        are Instance concerns that don't move the admission column
        (e.g. ``JobRecoveryService`` reconciles ``PROCESSING → PAUSED``
        by issuing ``atomic_transition(active, active)``, a no-op on
        the admission column). Returning ``True`` here is the least
        invasive way to keep those callers working without populating
        :data:`VALID_TRANSITIONS` with self-loops.

        Args:
            from_state: Current admission state (``None`` is never valid
                under the queue-proxy model — creation is an INSERT).
            to_state: Target admission state.

        Returns:
            True iff ``from_state == to_state`` (no-op) OR
            ``(from_state, to_state)`` is in :data:`VALID_TRANSITIONS`.
        """
        if from_state == to_state:
            return True  # no-op; pause/resume are Instance concerns
        return (from_state, to_state) in VALID_TRANSITIONS

    def validate_transition(
        self, from_state: str | None, to_state: str, job_id: str = ""
    ) -> None:
        """Validate transition, raising InvalidTransitionError if invalid.

        Same-state transitions are treated as implicit no-ops and
        pass validation without raising. See
        :meth:`can_transition` for the full rationale.

        Args:
            from_state: Current admission state.
            to_state: Target admission state.
            job_id: Job ID for the error message (optional — call sites
                that don't have it can pass an empty string; the
                underlying UPDATE will reject with the same error).

        Raises:
            InvalidTransitionError: If the transition is not allowed.
        """
        if from_state == to_state:
            return  # no-op; pause/resume are Instance concerns
        if not self.can_transition(from_state, to_state):
            raise InvalidTransitionError(
                job_id=job_id,
                from_state=from_state,
                to_state=to_state,
            )


# Singleton instance for convenience
job_state_machine = JobStateMachine()
