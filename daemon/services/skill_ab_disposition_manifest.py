"""The A/B DISPOSITION manifest — declared divert pairs for the E-6 backlog.

WHAT THIS MODULE IS (data corrected 2026-11-18)

This module is **DATA ONLY**. It records, per A/B pair, what the
operator lane actually adjudicated on 2026-11-18: whether the pair's
NEWER member is a VERIFIED-defective member (the only case that
authorises a defect-divert), WHICH named finding supports that
claim, and what was found for the pairs whose original claim did NOT
hold.

CURRENT TRUTH — there is NO CONSUMER (measured 2026-11-18)

* ``match_ab_disposition`` has **no call sites** anywhere in
  ``daemon/`` — the only references to this module are the module
  itself and its own test.
* The shipped sweep (``daemon/manager.py`` —
  ``_run_skill_ab_inactivity_sweep``) calls
  ``resolve_ab_test_sanctioned(basis="stalled", ...)`` with **no**
  ``winner_id`` and **no** ``defective_arm_id`` — i.e. the shipped
  sweep is a pure "newer lineage generation wins" default.

That is DELIBERATE, not a gap. The sweep's own decision comment
states the design: *"Defect-divert is NEVER sweep-judgment: the
sweep only resolves pairs no one has flagged; an operator/manifest
defective disposition diverts through the sanctioned REST mode /
corrective path."* This manifest is the OPERATOR-FACING half of that
contract — an operator reads it, decides, and acts through the
sanctioned path (the REST ``defective_arm`` mode, or the corrective
FIX path), each with its own reason + receipt.

CONSEQUENCE — the entries are NOT a blanket-apply list

Three of the six pairs do NOT authorise a divert. Their status says
so explicitly, and ``AbDisposition.divert_authorized`` (the
machine-checkable gate) refuses them:

* ``verified-defective`` — the newer member IS the verified-defective
  member; the pair may be diverted through the sanctioned path.
* ``not-verified-defective`` — the defect claim is UNSUPPORTED; the
  plain E-6 default applies and the pair MUST NOT be diverted.
* ``no-finding`` — nothing names the pair at all; it MUST NOT be
  diverted.

``disposition_winner()`` honours that gate: it returns the declared
winner ONLY for an authorised disposition.

ID SEMANTICS (corrected 2026-11-18)

The former left-hand field name was a MISNOMER: it held the
``skill_ab_tests`` PAIR-ROW id, not a skill id and not a group id.
The field is now named for what it actually holds:

* the LEFT id is the ``skill_ab_tests`` **PAIR-ROW** id — it is NOT a
  skill id and NOT a group id. It is now ``ab_test_row_id_prefix``.
* the RIGHT id IS a **skill** id — the declared winner
  (``winner_skill_id_prefix``, unchanged).
* the pair's ``ab_test_group`` id is now carried explicitly
  (``ab_test_group_prefix``) — it is the identifier the sanctioned
  resolver (``resolve_ab_test_sanctioned(ab_test_group=...)``) takes.
* the pair's OTHER member — the NEWER lineage generation — is now
  carried explicitly (``newer_member_skill_id_prefix``).

All ids are the 8-character PREFIXES the leader quoted: the full
uuids are not recoverable from the repo (the same gap the revival
plan records for the captured-origin ids), so every matcher accepts a
prefix.

House properties (mirroring ``skill_deactivation_manifest.py``):

* stdlib-only and **import-time inert** (no I/O, no registry side
  effects);
* a DISPOSITION list, never a mutation path — DS-11 spirit: an id
  list is never the deactivation predicate;
* every entry that claims a defect names the finding that supports
  it (anomaly uuid and/or POK slug), or carries an explicit
  no-finding marker — a bare "verified-defective" claim is a test
  failure, not a silent data change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

#: Manifest schema version — bump when the entry shape changes.
MANIFEST_VERSION = 2

#: The date the per-pair adjudication was recorded.
MANIFEST_CORRECTION_DATE = "2026-11-18"

#: The basis carried by a SUPPORTED defect-divert pair (E-6): the
#: non-defective member wins; the defective member is retired.
DISPOSITION_BASIS = "defective_arm"

#: The OPERATIVE basis of a pair whose defect claim is UNSUPPORTED —
#: the plain E-6 default (the sweep's newer-generation-wins stall
#: resolution). Such a pair is NOT a disposition.
DEFAULT_BASIS = "stalled"

#: Machine-checkable per-entry status — the ONLY place a defect claim
#: may be asserted. A claim without named evidence is a test failure.
STATUS_VERIFIED_DEFECTIVE = "verified-defective"
STATUS_NOT_VERIFIED_DEFECTIVE = "not-verified-defective"
STATUS_NO_FINDING = "no-finding"

#: The explicit marker carried by an entry that has NO finding. It is
#: a VALUE in ``evidence`` (never an empty tuple) so that "no
#: finding" is an assertion and never an omission.
NO_FINDING_MARKER = "none"

#: The original, generic claim every entry carried BEFORE the
#: 2026-11-18 adjudication — preserved verbatim, per entry, so the
#: history stays auditable.
ORIGINAL_GENERIC_CLAIM = (
    "E-6 pending-wiring divert: verified-defective member "
    "retired; the non-defective member wins"
)


@dataclass(frozen=True)
class AbDisposition:
    """One DECLARED pair — an explicit operator disposition record.

    Attributes:
        ab_test_row_id_prefix: The ``skill_ab_tests`` PAIR-ROW id
            prefix (8-char, as quoted). NOT a skill id and NOT a
            group id — the id of the ROW that holds the pair.
        ab_test_group_prefix: The pair's ``ab_test_group`` id prefix
            — the identifier the sanctioned resolver takes.
        winner_skill_id_prefix: The declared winner SKILL id prefix.
        newer_member_skill_id_prefix: The pair's OTHER member — the
            NEWER lineage generation. This is the member the plain
            E-6 default would crown and, for a SUPPORTED entry, the
            verified-defective member that must NOT be crowned.
        status: The machine-checkable claim — one of
            ``STATUS_VERIFIED_DEFECTIVE`` /
            ``STATUS_NOT_VERIFIED_DEFECTIVE`` / ``STATUS_NO_FINDING``.
        evidence: The NAMED finding ids supporting (or refuting) the
            claim — ``anomaly:<uuid>`` / ``pok:<slug>`` entries, or
            ``NO_FINDING_MARKER`` for a no-finding entry.
        finding: What was actually found, in one sentence.
        original_claim: The claim as it stood BEFORE the 2026-11-18
            adjudication (preserved verbatim, per entry).
        original_basis: The basis as it stood BEFORE the adjudication
            (always ``defective_arm``).
        basis: The OPERATIVE basis — ``defective_arm`` for a
            supported entry, ``stalled`` (the plain E-6 default) for
            an unsupported one.
        corrected_on: The date the adjudication was recorded.
        correction_note: A short note of what changed for this entry.
    """

    ab_test_row_id_prefix: str
    ab_test_group_prefix: str
    winner_skill_id_prefix: str
    newer_member_skill_id_prefix: str
    status: str
    evidence: tuple[str, ...]
    finding: str
    original_claim: str
    original_basis: str
    basis: str
    corrected_on: str
    correction_note: str

    @property
    def named_evidence_ids(self) -> tuple[str, ...]:
        """The evidence ids EXCLUDING the no-finding marker."""
        return tuple(e for e in self.evidence if e != NO_FINDING_MARKER)

    @property
    def divert_authorized(self) -> bool:
        """True iff this entry authorises a defect-divert.

        A divert is authorised ONLY when the claim is
        ``verified-defective`` AND at least one NAMED finding id
        supports it. An unsupported claim, or a no-finding entry, is
        NOT a disposition: the plain E-6 default applies and the
        operator decides explicitly.
        """
        return (
            self.status == STATUS_VERIFIED_DEFECTIVE
            and bool(self.named_evidence_ids)
        )

    def matches_ab_test_row(self, row_id: Any) -> bool:
        """True iff *row_id* is (a prefix of) the PAIR-ROW id."""
        rid = str(row_id or "")
        return bool(rid) and rid.startswith(self.ab_test_row_id_prefix)

    def matches_ab_test_group(self, group_id: Any) -> bool:
        """True iff *group_id* is (a prefix of) the ``ab_test_group``."""
        gid = str(group_id or "")
        return bool(gid) and gid.startswith(self.ab_test_group_prefix)

    def matches_winner(self, skill_id: Any) -> bool:
        """True iff *skill_id* is (a prefix of) the winning member."""
        sid = str(skill_id or "")
        return bool(sid) and sid.startswith(self.winner_skill_id_prefix)

    def matches_newer_member(self, skill_id: Any) -> bool:
        """True iff *skill_id* is (a prefix of) the newer member."""
        sid = str(skill_id or "")
        return bool(sid) and sid.startswith(
            self.newer_member_skill_id_prefix
        )


#: The SIX declared pairs (W7, 2026-11-17), in the leader's order.
#: Per-pair data corrected 2026-11-18 against live evidence (anomaly
#: reports + POK rows + the live skill content): three pairs keep a
#: SUPPORTED defect claim, three do NOT (their original generic claim
#: is preserved in ``original_claim`` and withdrawn in ``status``).
DECLARED_AB_DISPOSITIONS: tuple[AbDisposition, ...] = (
    AbDisposition(
        ab_test_row_id_prefix="1b8e4d80",
        ab_test_group_prefix="58c601e0",
        winner_skill_id_prefix="8bb22eb8",
        newer_member_skill_id_prefix="7b3d71e3",
        status=STATUS_NO_FINDING,
        evidence=(NO_FINDING_MARKER,),
        finding=(
            "no finding — no anomaly row and no POK row names "
            "7b3d71e3, 8bb22eb8, the group 58c601e0 or the pair "
            "row 1b8e4d80"
        ),
        original_claim=ORIGINAL_GENERIC_CLAIM,
        original_basis=DISPOSITION_BASIS,
        basis=DEFAULT_BASIS,
        corrected_on=MANIFEST_CORRECTION_DATE,
        correction_note=(
            "claim WITHDRAWN — nothing names this pair; the plain "
            "E-6 default applies and the pair MUST NOT be diverted"
        ),
    ),
    AbDisposition(
        ab_test_row_id_prefix="8f8cee3a",
        ab_test_group_prefix="a617317b",
        winner_skill_id_prefix="08e67030",
        newer_member_skill_id_prefix="de796413",
        status=STATUS_NOT_VERIFIED_DEFECTIVE,
        evidence=(
            "anomaly:557992f4-8d5e-4b12-b806-09280950da72",
            "pok:abskill-01-canon-file-family4-gap",
        ),
        finding=(
            "the newer member de796413's BODY IS A SUPERSET (it adds "
            "family 4 and a .git pre-flight); only the row-level "
            "description column is stale — cosmetic metadata "
            "staleness, not a defect"
        ),
        original_claim=ORIGINAL_GENERIC_CLAIM,
        original_basis=DISPOSITION_BASIS,
        basis=DEFAULT_BASIS,
        corrected_on=MANIFEST_CORRECTION_DATE,
        correction_note=(
            "claim WITHDRAWN — the newer member is not "
            "verified-defective; the plain E-6 default applies and "
            "the pair MUST NOT be diverted"
        ),
    ),
    AbDisposition(
        ab_test_row_id_prefix="b77128c5",
        ab_test_group_prefix="ee28042c",
        winner_skill_id_prefix="76e3e1a7",
        newer_member_skill_id_prefix="09c2746f",
        status=STATUS_NOT_VERIFIED_DEFECTIVE,
        evidence=(
            "anomaly:70dda1e9-0c28-4597-979e-e45dd932111c",
            "pok:failed-digest-gate-canon-sync-pending",
        ),
        finding=(
            "the newer member 09c2746f was found CORRECT (a correct "
            "junitxml-first generation); the real defect is CANON "
            "SHADOWING — the PF canon 'pf:tester:failed-digest-"
            "equality-gate' shadows BOTH 09c2746f and 76e3e1a7"
        ),
        original_claim=ORIGINAL_GENERIC_CLAIM,
        original_basis=DISPOSITION_BASIS,
        basis=DEFAULT_BASIS,
        corrected_on=MANIFEST_CORRECTION_DATE,
        correction_note=(
            "claim WITHDRAWN — the newer member is not "
            "verified-defective; the plain E-6 default applies and "
            "the pair MUST NOT be diverted"
        ),
    ),
    AbDisposition(
        ab_test_row_id_prefix="604550ac",
        ab_test_group_prefix="8e9853e9",
        winner_skill_id_prefix="c595f7cf",
        newer_member_skill_id_prefix="10ca5fbf",
        status=STATUS_VERIFIED_DEFECTIVE,
        evidence=(
            "pok:skill-fix-defer-sql-census-8e9853e9",
            "anomaly:d91f8f8e",
        ),
        finding=(
            "the newer member 10ca5fbf carries stray BODY_START / "
            "BODY_END markers in the live skill content "
            "(binary-embedded-sql-census-and-prefix-trap)"
        ),
        original_claim=ORIGINAL_GENERIC_CLAIM,
        original_basis=DISPOSITION_BASIS,
        basis=DISPOSITION_BASIS,
        corrected_on=MANIFEST_CORRECTION_DATE,
        correction_note=(
            "claim UPHELD — the named evidence is now recorded "
            "explicitly (the entry previously carried a bare generic "
            "claim)"
        ),
    ),
    AbDisposition(
        ab_test_row_id_prefix="4ba4086d",
        ab_test_group_prefix="39a27f20",
        winner_skill_id_prefix="0757f082",
        newer_member_skill_id_prefix="38bad026",
        status=STATUS_VERIFIED_DEFECTIVE,
        evidence=(
            "anomaly:34205aa0-1ab1-4da4-8e66-de1fac8177d5",
            "anomaly:b6c61db6-20ec-48d6-a93b-d5b6af9ec3bc",
            "anomaly:7ded385b-ae38-4e8a-acd8-dbc4db7f4a09",
            "pok:ab-39a27f20-strict-superset-fix-pending",
        ),
        finding=(
            "the newer member 38bad026 over-trimmed its parent "
            "0757f082, dropping four pre-existing sections "
            "(other-paths bite proof + cleanup line; Known limitation; "
            "Environment fence incl. the per-variable-scrub trap; "
            "'Check the fork ordering first') against an explicit "
            "strict-superset constraint"
        ),
        original_claim=ORIGINAL_GENERIC_CLAIM,
        original_basis=DISPOSITION_BASIS,
        basis=DISPOSITION_BASIS,
        corrected_on=MANIFEST_CORRECTION_DATE,
        correction_note=(
            "claim UPHELD — the named evidence is now recorded "
            "explicitly (the entry previously carried a bare generic "
            "claim)"
        ),
    ),
    AbDisposition(
        ab_test_row_id_prefix="f3bc3a3e",
        ab_test_group_prefix="bddd6559",
        winner_skill_id_prefix="607425ef",
        newer_member_skill_id_prefix="d8359c50",
        status=STATUS_VERIFIED_DEFECTIVE,
        evidence=("anomaly:2b5d2cbc-fe5f-4c44-9b5b-94704aabf24c",),
        finding=(
            "the newer member d8359c50 violated explicit preserve "
            "instructions — it summarised five measured baselines "
            "into vague one-liners, dropped verbatim refusal/reason "
            "tokens and file:line anchors, and INVENTED an unmeasured "
            "'Post ACK' baseline"
        ),
        original_claim=ORIGINAL_GENERIC_CLAIM,
        original_basis=DISPOSITION_BASIS,
        basis=DISPOSITION_BASIS,
        corrected_on=MANIFEST_CORRECTION_DATE,
        correction_note=(
            "claim UPHELD — the named evidence is now recorded "
            "explicitly (the entry previously carried a bare generic "
            "claim)"
        ),
    ),
)


def match_ab_disposition(identifier: Any) -> Optional[AbDisposition]:
    """Return the declared entry naming *identifier*, or ``None``.

    Matches on ANY identifier the entry names: the PAIR-ROW id, the
    ``ab_test_group`` id, the declared winner skill id, or the pair's
    other (newer) member skill id.

    Args:
        identifier: A pair-row id, group id, or skill id (full id or
            the quoted 8-char prefix).

    Returns:
        The matching :class:`AbDisposition`, or ``None``.
    """
    if not identifier:
        return None
    value = str(identifier)
    for disposition in DECLARED_AB_DISPOSITIONS:
        if (
            disposition.matches_ab_test_row(value)
            or disposition.matches_ab_test_group(value)
            or disposition.matches_winner(value)
            or disposition.matches_newer_member(value)
        ):
            return disposition
    return None


def declared_divert_dispositions() -> tuple[AbDisposition, ...]:
    """The entries whose defect claim is SUPPORTED by named evidence.

    An entry whose status is ``not-verified-defective`` or
    ``no-finding`` is NOT a disposition — the plain E-6 default
    applies to it and it must never be blanket-diverted.
    """
    return tuple(
        d for d in DECLARED_AB_DISPOSITIONS if d.divert_authorized
    )


def disposition_winner(skill_id: Any) -> Optional[str]:
    """The declared WINNER prefix for the pair naming *skill_id*.

    Returns the winner ONLY for an entry whose defect claim is
    SUPPORTED (``divert_authorized``). For an unsupported or
    no-finding entry the pair is NOT a disposition: the plain E-6
    default applies and the operator decides explicitly — so
    ``None`` is returned.

    Returns ``None`` when no disposition names the id at all.
    """
    disposition = match_ab_disposition(skill_id)
    if disposition is None or not disposition.divert_authorized:
        return None
    return disposition.winner_skill_id_prefix
