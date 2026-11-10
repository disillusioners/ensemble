"""The SHARED skill-lane refusal envelope (M11).

WHY THIS MODULE EXISTS

Three refusals in the skill lane were shaped three different ways:

* a REPOSITORY-level refusal class carried a **class attribute**
  ``reason`` and no ``detail``. That class was DELETED by the metrics
  lane after it proved ZERO production callers — the live dual-column
  writer is E4's ``daemon/services/skill_deactivation.py::write_skill_pair``
  (and the sanctioned ``SkillRepository.deactivate``), so the orphaned
  second writer and its two value-object types are gone;
* ``PairResolutionRefused`` (``daemon/services/skill_deactivation.py``)
  carried an **instance attribute** ``reason`` and a ``detail``;
* ``SkillFixBurstCapRefused`` (``daemon/services/skill_job_dispatcher.py``)
  carried **no token at all** — the REST route hard-coded the literal
  ``"fix_burst_cap"`` at the raise site.

A caller therefore had to know WHICH refusal it held to read the reason:
``e.reason`` on one, ``e.reason`` (instance) on another, and "look at
the router" on the third. Two of them even named the SAME refusal with
two different literal tokens (``"skipped_open_ab_test"`` in the receipt
vocabulary vs ``"open_ab_test"`` on the deleted repository class).

With the repository-level class deleted, this base unifies the TWO LIVE
refusals — ``PairResolutionRefused`` and ``SkillFixBurstCapRefused`` —
and the duplicated literal token went with the deleted class.

THE CONTRACT (one shape for every skill-lane refusal)

* ``reason`` — the **stable token** a caller / a receipt matches on. It
  is a **CLASS attribute** on every subclass (so the token is readable
  off the class, not only off an instance) and may be narrowed per
  instance when one concept has several specific reasons.
* ``detail`` — the human-readable explanation. ``str(exc)`` is the
  detail when there is one, so the agent-facing message prefixes
  (e.g. ``"refused: burst cap"``) are preserved byte-for-byte.

ONE TOKEN PER CONCEPT

The token is defined ONCE, on the class that owns the concept. The
REST route reads ``e.reason`` instead of hard-coding a literal, so the
route can never drift from the class.

This module is a LEAF: stdlib-only, import-time inert, no imports of
its own — so every layer may depend on it without creating a cycle
(``daemon/repositories/**`` importing ``daemon/services/**`` is an
established house pattern — e.g. ``daemon/repositories/task/repository.py``
imports ``daemon.services.job_state_machine``).
"""

from __future__ import annotations


class SkillLaneRefused(Exception):
    """The base of every skill-lane refusal envelope.

    Attributes:
        reason: The stable token for the refused concept. Declared as a
            CLASS attribute so it is readable off the class; an instance
            may narrow it when one concept has several specific reasons.
        detail: The human-readable explanation (also ``str(exc)``).
    """

    #: The stable token a caller / a receipt matches on.
    reason: str = "skill_lane_refused"

    def __init__(self, reason: str = "", detail: str = "") -> None:
        """Build the envelope.

        Args:
            reason: The token for this refusal. Empty ⇒ the class's own
                ``reason`` attribute.
            detail: The human-readable explanation. Empty ⇒ the token
                is used as the message.
        """
        token = reason or type(self).reason
        super().__init__(detail or token)
        self.reason = token
        self.detail = detail
