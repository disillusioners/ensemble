"""The E4 candidate-manifest — DECLARED dispositions for non-rule candidates.

WHY THIS MODULE EXISTS (skill-system-revival Phase 1, workstream E4, plan §9)

The E4 target set is **rule-based** (DS-11): R1/R2/R3 (and the pair-resolver)
decide what may be touched. DS-11 forbids an *id list* as the deactivation
predicate — and this module is **not** one. Nothing here can deactivate,
re-activate or delete anything. This module is the *disposition* side of the
candidate-set manifest: the rows that are **declared** (not rule-derived) and
the action each one carries.

THE ONE DECLARED DISPOSITION (leader item 5)

``adversarial-plan-ledger-verification`` (``c0c427a0…``) is a **tooling
artifact**, not a skill: a plan verifier created it while probing the skill
tools. It must never be injectable. It is carried here with an explicit
disposition:

* ``injection`` → ``excluded`` — the E4.3 injection filter consults THIS
  manifest, so the row is never served, even before the boot reconciliation
  has run (the exclusion is **enforced in code**, not merely documented).
* ``deactivation`` → ``operator_path_only`` — the row is NEVER deactivated by
  the shipped reconciliation (no rule matches it), and the operator script
  refuses to deactivate a declared artifact unless the operator passes the
  explicit ``--allow-declared-artifact`` override.

The FULL row id is not recoverable from the repo (the same gap the plan
records for the 6 captured-origin ids), so the manifest carries the
**8-character id prefix** the leader quoted plus the **stable name**. The
matcher accepts either.

THE CANDIDATE SET STAYS RULE-BASED

Every OTHER candidate in the manifest is rule-derived (R1/R2/R3/R5) or
pair-resolver-derived. This file only adds *declared* dispositions on top;
it never widens the deactivation predicate.

Env note: this module is stdlib-only and import-time inert (no I/O, no
registry side effects) — see the house note on import-time side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

#: Manifest schema version — bump when the entry shape changes.
MANIFEST_VERSION = 1

#: The rule id carried by a DECLARED (non-rule-derived) candidate.
RULE_DECLARED = "DECLARED_DISPOSITION"


@dataclass(frozen=True)
class DeclaredDisposition:
    """A declared, non-rule-derived disposition for one skill row.

    Attributes:
        skill_id_prefix: The leading characters of the row id (the
            full id is not recoverable from the repo — see the module
            docstring). Matched as a prefix.
        name: The row's stable name (the primary matcher).
        disposition: The human-readable disposition (the audit text).
        injection: ``"excluded"`` — the E4.3 filter refuses to serve it.
        deactivation: ``"operator_path_only"`` — never deactivated by the
            shipped reconciliation.
    """

    skill_id_prefix: str
    name: str
    disposition: str
    injection: str
    deactivation: str


#: The declared dispositions (currently exactly one — the plan-verifier
#: tooling artifact). This is a DISPOSITION list, never a deactivation
#: predicate (DS-11).
DECLARED_DISPOSITIONS: tuple[DeclaredDisposition, ...] = (
    DeclaredDisposition(
        skill_id_prefix="c0c427a0",
        name="adversarial-plan-ledger-verification",
        disposition=(
            "tooling artifact - not a skill - exclude from injection; "
            "deactivation only via the operator path, never executed in "
            "Phase 1"
        ),
        injection="excluded",
        deactivation="operator_path_only",
    ),
)


def match_declared_disposition(skill: Any) -> Optional[DeclaredDisposition]:
    """Return the declared disposition for *skill*, or ``None``.

    Matches on the row **name** first (stable), then on the row **id
    prefix** (the id the leader quoted was truncated).

    Args:
        skill: A ``Skill`` row (or any object exposing ``id``/``name``).

    Returns:
        The matching :class:`DeclaredDisposition`, or ``None`` when the row
        carries no declared disposition.
    """
    if skill is None:
        return None
    name = str(getattr(skill, "name", "") or "")
    skill_id = str(getattr(skill, "id", "") or "")
    for declared in DECLARED_DISPOSITIONS:
        if name and name == declared.name:
            return declared
        if skill_id and declared.skill_id_prefix and skill_id.startswith(
            declared.skill_id_prefix
        ):
            return declared
    return None


def is_excluded_from_injection(skill: Any) -> bool:
    """True iff *skill* carries a declared ``injection='excluded'`` disposition."""
    declared = match_declared_disposition(skill)
    return declared is not None and declared.injection == "excluded"
