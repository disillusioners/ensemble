"""The A/B DISPOSITION manifest — declared divert pairs for the E-6 backlog.

WHY THIS MODULE EXISTS (2026-11-17, W7 — leader decision)

The sweep posture is **NO knob**: the ``skill_ab_inactivity_sweep``
never carries a "which pairs to divert" switch. Diverts come from an
EXPLICIT disposition — an operator/leader-named pair — never from
sweep judgment (user ruling E-6: never crown a verified-defective
newer member; the corrective-first / non-defective-member branch is
reached through an explicit disposition).

This module is that disposition list: the SIX divert pairs carried
from the E-6 pending-wiring table, each naming the VERIFIED-defective
member and the NON-DEFECTIVE member that wins
(``basis='defective_arm'``). It is DATA + a matcher — it mutates
nothing, and no shipped code path auto-applies it: the sanctioned
resolution stays an explicit, operator-driven act with its own reason
and receipt (the promote-ceremony runbook step is in
``.agents/shared/planning/skill-system-defects/plan-light.md``).

House properties (mirroring ``skill_deactivation_manifest.py``):

* stdlib-only and **import-time inert** (no I/O, no registry side
  effects);
* a DISPOSITION list, never a mutation path — DS-11 spirit: an id
  list is never the deactivation predicate;
* the ids are the 8-character PREFIXES the leader quoted (the full
  row ids are not recoverable from the repo — the same gap the
  revival plan records for the captured-origin ids); the matcher
  accepts a prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

#: Manifest schema version — bump when the entry shape changes.
MANIFEST_VERSION = 1

#: The basis carried by every declared divert pair (E-6): the
#: non-defective member wins; the defective member is retired.
DISPOSITION_BASIS = "defective_arm"


@dataclass(frozen=True)
class AbDisposition:
    """One DECLARED divert pair — an explicit operator disposition.

    Attributes:
        defective_skill_id_prefix: The verified-defective member's
            row id (8-char prefix as quoted).
        winner_skill_id_prefix: The NON-defective member's row id
            (8-char prefix) — the sanctioned winner.
        basis: The sanctioned basis (always ``defective_arm``).
        reason: The auditable provenance of the disposition.
        ab_test_group: The pair's group UUID when known (the live
            table's groups are not recoverable from the repo — the
            disposition is matched by member id, not by group).
    """

    defective_skill_id_prefix: str
    winner_skill_id_prefix: str
    reason: str
    basis: str = DISPOSITION_BASIS
    ab_test_group: Optional[str] = None

    def matches_defective(self, skill_id: Any) -> bool:
        """True iff *skill_id* is (a prefix of) the defective member."""
        sid = str(skill_id or "")
        return bool(sid) and sid.startswith(self.defective_skill_id_prefix)

    def matches_winner(self, skill_id: Any) -> bool:
        """True iff *skill_id* is (a prefix of) the winning member."""
        sid = str(skill_id or "")
        return bool(sid) and sid.startswith(self.winner_skill_id_prefix)


#: The SIX declared divert pairs (W7, 2026-11-17). Each names the
#: VERIFIED-defective member FIRST and the non-defective winner
#: SECOND. Order is preserved exactly as the leader listed it.
DECLARED_AB_DISPOSITIONS: tuple[AbDisposition, ...] = (
    AbDisposition(
        defective_skill_id_prefix="1b8e4d80",
        winner_skill_id_prefix="8bb22eb8",
        reason=(
            "E-6 pending-wiring divert: verified-defective member "
            "retired; the non-defective member wins"
        ),
    ),
    AbDisposition(
        defective_skill_id_prefix="8f8cee3a",
        winner_skill_id_prefix="08e67030",
        reason=(
            "E-6 pending-wiring divert: verified-defective member "
            "retired; the non-defective member wins"
        ),
    ),
    AbDisposition(
        defective_skill_id_prefix="b77128c5",
        winner_skill_id_prefix="76e3e1a7",
        reason=(
            "E-6 pending-wiring divert: verified-defective member "
            "retired; the non-defective member wins"
        ),
    ),
    AbDisposition(
        defective_skill_id_prefix="604550ac",
        winner_skill_id_prefix="c595f7cf",
        reason=(
            "E-6 pending-wiring divert: verified-defective member "
            "retired; the non-defective member wins"
        ),
    ),
    AbDisposition(
        defective_skill_id_prefix="4ba4086d",
        winner_skill_id_prefix="0757f082",
        reason=(
            "E-6 pending-wiring divert: verified-defective member "
            "retired; the non-defective member wins"
        ),
    ),
    AbDisposition(
        defective_skill_id_prefix="f3bc3a3e",
        winner_skill_id_prefix="607425ef",
        reason=(
            "E-6 pending-wiring divert: verified-defective member "
            "retired; the non-defective member wins"
        ),
    ),
)


def match_ab_disposition(skill_id: Any) -> Optional[AbDisposition]:
    """Return the declared disposition naming *skill_id*, or ``None``.

    Matches on either member of the pair (the defective one and the
    winner) — the disposition is looked up by EITHER id prefix.

    Args:
        skill_id: A skill row id (full id or the quoted 8-char prefix).

    Returns:
        The matching :class:`AbDisposition`, or ``None``.
    """
    if not skill_id:
        return None
    for disposition in DECLARED_AB_DISPOSITIONS:
        if disposition.matches_defective(skill_id) or (
            disposition.matches_winner(skill_id)
        ):
            return disposition
    return None


def disposition_winner(skill_id: Any) -> Optional[str]:
    """The declared WINNER prefix for the pair containing *skill_id*.

    Returns ``None`` when no disposition names the id (the operator
    then decides explicitly — the sweep never guesses).
    """
    disposition = match_ab_disposition(skill_id)
    if disposition is None:
        return None
    return disposition.winner_skill_id_prefix
