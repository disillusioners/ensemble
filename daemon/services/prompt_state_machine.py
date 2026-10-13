"""Service-side canonical surface for the prompt-version approval state machine.

agent-prompt-immutability Phase B (T1).

The repository at :mod:`daemon.repositories.prompt_version.repository`
owns the **state-machine data** (``VALID_TRANSITIONS``,
``InvalidTransitionError``, ``PROMPT_STATES``). Phase B's code that
sits OUTSIDE the repository — the service layer's thin async facade,
the inner_soul rewrite, the agent_mother rewire, the future REST
routers — imports these names from THIS module rather than reaching
into the repository directly. Two reasons:

1. **Single import surface.** Route every downstream call to a
   service-layer path so the repository's contract surface stays
   narrow. ``VALID_TRANSITIONS`` is the A1 frozen set (see
   ``phase-contract.md``); we re-export it here unchanged.
2. **Helper predicates.** ``is_terminal(state)`` and
   ``can_transition(from, to)`` are pure-Python helpers the service
   layer needs at multiple call sites (gate checks, error mapping,
   precondition assertions). The repository stays focused on the
   SQL-bearing machinery; the helpers live here.

Mirrors the role of :mod:`daemon.services.job_state_machine`
(`daemon/services/job_state_machine.py:84-110`) — same shape: a
:class:`JobStateMachine` (here a module-level :class:`PromptStateMachine`
singleton) with ``can_transition`` / ``validate_transition`` methods
plus ``InvalidTransitionError(ValueError)``. The prompt-version case
is simpler than the job-queue case (no same-state no-op, no paused
slot — the admission column is the state; pause/resume are instance
concerns, not row concerns).

Conventions
-----------

* The repository's :data:`VALID_TRANSITIONS` is the **single source
  of truth**. ``prompt_state_machine.VALID_TRANSITIONS`` is a
  re-export, not a duplicate — if the two ever disagree, the
  repository wins and the test that exercises both surfaces should
  fail loudly (see ``tests/test_prompt_approval_service.py``).
* ``is_terminal(state)`` answers "can anything leave this state?".
  Per A1: ``approved`` and ``rejected`` are terminal;
  ``archived`` is also terminal (no transition leaves it).
  ``draft`` and ``pending_approval`` are not.
* ``can_transition(from_state, to_state)`` is a pure
  membership check, matching :meth:`JobStateMachine.can_transition`
  (no same-state no-op here — ``(X, X)`` is *not* enumerated;
  callers that want a same-state idempotent transition route
  through :meth:`PromptStateMachine.validate_transition` and the
  repo will reject via ``InvalidTransitionError`` the same way it
  rejects any other invalid pair).
"""

from __future__ import annotations

import logging
from typing import FrozenSet, Optional, Tuple

# Re-exports from the repository (A1 FROZEN). The repository owns the
# data; this module owns the import surface + helper predicates.
from daemon.repositories.prompt_version.repository import (
    PROMPT_STATES,
    InvalidTransitionError,
    VALID_TRANSITIONS,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Terminal state set (D1 / phase-contract.md A1)
# ─────────────────────────────────────────────────────────────────────────────


# A1 FROZEN. ``approved`` and ``rejected`` are terminal — the only way
# out is a NEW row at ``max(version)+1`` for the key (same-row re-open
# would break forensic identity, per decisions.md D1). ``archived`` is
# also terminal — a draft superseded by a fresh pending row never
# transitions back; the lineage stays intact.
TERMINAL_STATES: FrozenSet[str] = frozenset({
    "approved",
    "rejected",
    "archived",
})


# ─────────────────────────────────────────────────────────────────────────────
# PromptStateMachine
# ─────────────────────────────────────────────────────────────────────────────


class PromptStateMachine:
    """Formal state machine for prompt-version approval transitions.

    Mirrors :class:`daemon.services.job_state_machine.JobStateMachine`
    in shape but stays smaller: no same-state no-op, no paused slot,
    no Decision enum (the admission column on jobs has more
    transitions than the approval workflow here does). The
    predicates are:

    * :meth:`can_transition` — pure membership check on
      :data:`VALID_TRANSITIONS`.
    * :meth:`validate_transition` — raises :class:`InvalidTransitionError`
      when the pair is not in :data:`VALID_TRANSITIONS`.
    * :meth:`is_terminal` — convenience predicate (sets membership).

    Singleton instance :data:`prompt_state_machine` is the canonical
    handle for downstream code; instantiating extra copies is harmless
    but signals confusion.
    """

    @staticmethod
    def can_transition(from_state: Optional[str], to_state: str) -> bool:
        """Check if a transition is valid.

        Membership check on :data:`VALID_TRANSITIONS`. Differs from
        :meth:`JobStateMachine.can_transition` in two ways:

        * NO same-state no-op. ``(draft, draft)`` is NOT in
          :data:`VALID_TRANSITIONS`; callers that want a same-state
          idempotent transition should route through the repo's
          ``atomic_transition`` and accept the resulting
          ``InvalidTransitionError`` (which is the same error type
          the repo raises for any other invalid pair — uniform error
          surface, no second-class call paths).
        * ``None`` (a fresh row's "before" state) is rejected. Row
          creation is an INSERT into ``state='draft'`` /
          ``state='pending_approval'``; it never goes through the
          state machine. Matches :meth:`JobStateMachine.can_transition`
          semantics.

        Args:
            from_state: Current state (``None`` always rejected — row
                creation is an INSERT, never a transition).
            to_state: Target state.

        Returns:
            ``True`` iff ``(from_state, to_state)`` is in
            :data:`VALID_TRANSITIONS`.
        """
        if from_state is None:
            return False
        return (from_state, to_state) in VALID_TRANSITIONS

    @staticmethod
    def validate_transition(
        from_state: Optional[str],
        to_state: str,
        version_id: str = "",
    ) -> None:
        """Validate transition; raise ``InvalidTransitionError`` on failure.

        Args:
            from_state: Current state.
            to_state: Target state.
            version_id: The PromptVersion primary key for error
                messages (callers that have it pass it; the underlying
                repo will still reject with the same error type).

        Raises:
            InvalidTransitionError: If the transition is not allowed.
        """
        if not PromptStateMachine.can_transition(from_state, to_state):
            raise InvalidTransitionError(
                version_id=version_id,
                from_state=from_state,
                to_state=to_state,
            )

    @staticmethod
    def is_terminal(state: str) -> bool:
        """Return ``True`` iff ``state`` has no outgoing transitions.

        Args:
            state: A :data:`PROMPT_STATES` member.

        Returns:
            ``True`` for ``approved``, ``rejected``, ``archived``;
            ``False`` for ``draft``, ``pending_approval``.

        Raises:
            ValueError: If ``state`` is not in :data:`PROMPT_STATES`
                (mirrors the project's "validators return clean
                errors, callers catch" convention).
        """
        if state not in PROMPT_STATES:
            raise ValueError(
                f"is_terminal: state must be one of {sorted(PROMPT_STATES)}, "
                f"got {state!r}"
            )
        return state in TERMINAL_STATES


# Singleton instance for convenience (mirrors ``job_state_machine`` at
# ``daemon/services/job_state_machine.py:142``).
prompt_state_machine = PromptStateMachine()


__all__ = [
    # Re-exports from the repository (A1 frozen contract).
    "PROMPT_STATES",
    "VALID_TRANSITIONS",
    "InvalidTransitionError",
    # Module-level constants.
    "TERMINAL_STATES",
    # The state machine itself.
    "PromptStateMachine",
    "prompt_state_machine",
]
