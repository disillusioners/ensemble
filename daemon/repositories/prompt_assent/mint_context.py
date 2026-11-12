"""The AR-1 private-channel classifier — fail-closed by construction.

The pure, lane-scoped half of the AR-1 mint-context derivation: given the
NESTED ``metadata["discord"]`` dict the Discord adapter builds
(``daemon/sources/adapters/discord/adapter.py`` — the ``discord_meta``
build site), decide whether the channel is a PRIVATE 1:1 DM.

AR-1 leg 2 (K-3): ``private = is_dm AND NOT is_group_dm``. The ruling's
fail-closed rule is absolute — an ABSENT or UNPARSEABLE discriminator ⇒
NOT private. This bounds a stale/older message row to an AVAILABILITY
problem (the lane refuses to mint/approve there), never an AUTHORITY
problem: only literal ``True``/``False`` booleans are trusted.

The adapter-side discriminator (``is_group_dm`` added to the nested
``discord_meta``, additive metadata, no schema change) is task 1.12 and
P1-owned; this classifier is the consumption contract for the P2 tool
gate (mint step M-4c) and the P3 approving-context check (step 4f).

NEVER derive authority from this module: it is a pure function over
daemon-owned metadata. The mint context itself is derived from the
factor-2 window's message row — never from tool arguments.

PHASE 1.5 (designated channels, pinned-approver model)
------------------------------------------------------
This module ALSO owns the private-**equivalent** rule (D-6) and it is the
ONE owner of the classification ordering (architect §4.4):

    ``designation_outcome(designations, discord_meta, user_id) ->
    DesignationResult``

with the seven SIDE-NEUTRAL variants of **D-18** (``PASS |
NOT_DESIGNATED | GROUP_DM_SHAPE | APPROVER_NOT_PINNED |
ABSENT_DISCRIMINATOR | ABSENT_IDENTITY | CONFIG_INVALID``). Both gates —
the mint gate (inside its ``not private`` arm) and the approve-side service
predicate (inside its own ``not private`` arm) — call this ONE function and
map the result to their side's codes. Two implementations of the ordering
are FORBIDDEN.

``is_private_assent_channel`` is BYTE-UNCHANGED by this phase: the DM leg
is the first leg of :func:`designation_outcome` and it reads the same
identity checks, so the two can never disagree.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from .designation import DesignationParseResult


def is_private_assent_channel(discord_meta: Mapping[str, Any] | None) -> bool:
    """``True`` ONLY for a proven 1:1 DM (AR-1 leg 2 / K-3).

    Fail-closed on every non-proven shape:

    * ``meta`` ``None`` / empty ⇒ ``False`` (not private);
    * ``is_dm`` absent, non-bool, or ``False`` ⇒ ``False``;
    * ``is_group_dm`` absent, non-bool, or ``True`` ⇒ ``False`` — this is
      the fail-closed discriminator rule: an older row that predates the
      discriminator cannot mint or approve until the discriminator ships
      (ruling: *"absent/unparseable discriminator = NOT private"*);
    * only ``is_dm is True AND is_group_dm is False`` ⇒ ``True``.

    The identity checks (``is True`` / ``is False``) are deliberate:
    truthy strings like ``"yes"`` or ``1`` are UNPARSEABLE under the
    ruling and classify as NOT private.
    """
    if not discord_meta:
        return False
    return discord_meta.get("is_dm") is True and (
        discord_meta.get("is_group_dm") is False
    )


# ── The private-EQUIVALENT classification (Phase 1.5 — designated channels) ─
#
# D-6's ONE rule: a turn is private-equivalent iff it is a proven 1:1 DM OR
# (its channel is in the operator's designation map AND the turn's
# daemon-derived user id equals that channel's pinned approver).
#
# ONE function owns the WHOLE leg ordering (architect §4.4). BOTH gates — the
# mint gate (inside its ``not private`` arm) and the service predicate (inside
# its own ``not private`` arm) — call it; two implementations of this ordering
# are forbidden (the drift hazard the single-owner rule exists to prevent).
#
# The function NEVER reads ``os.environ``: the caller (the tool's cached-lazy
# resolver) parses the env value once per call and passes the TYPED result in
# (B2(c)). Neither this module nor ``designation.py`` owns an environment
# read or any I/O — the decision function is a pure function of
# (map, metadata, user id).
#
# The result is SIDE-NEUTRAL (D-18): these are neutral LEG identities, not
# refusal codes. Each gate maps the leg to its own side's code via a pinned
# per-side table (mint: ``mint-requires-private-channel`` /
# ``approver-not-pinned`` / ``extraction-incomplete`` /
# ``designation-config-invalid``; approve: ``token-channel-mismatch`` /
# the same three non-mismatch codes). The burning ``token-approver-mismatch``
# keeps ONLY its existing 4e different-human role — it is never the mapping of
# ``APPROVER_NOT_PINNED``.


class DesignationResult(str, Enum):
    """The typed neutral outcome of :func:`designation_outcome` (D-18).

    Every variant except :attr:`PASS` is a NEUTRAL leg identity; the gates
    translate it into their side's code. The values are prefixed ``leg:`` on
    purpose: a variant must NEVER be usable as, or compare equal to, a
    refusal code — ``approver-not-pinned`` is a real code on BOTH sides, and
    an unprefixed enum value would silently be ``in MISMATCH_CODES``-style
    membership tests away from a wrong decision.
    """

    PASS = "leg:pass"
    NOT_DESIGNATED = "leg:not-designated"
    GROUP_DM_SHAPE = "leg:group-dm-shape"
    APPROVER_NOT_PINNED = "leg:approver-not-pinned"
    ABSENT_DISCRIMINATOR = "leg:absent-discriminator"
    ABSENT_IDENTITY = "leg:absent-identity"
    CONFIG_INVALID = "leg:config-invalid"


def designation_outcome(
    designations: "DesignationParseResult | None",
    discord_meta: Mapping[str, Any] | None,
    user_id: Any,
) -> DesignationResult:
    """Classify a turn against the designation map — the WHOLE leg ordering.

    The ordering is LOAD-BEARING (refusal-cell-matrix §1.1); each leg is
    listed with the reason it must precede the next:

    1. a **proven 1:1 DM** ⇒ :attr:`DesignationResult.PASS` — short-circuits
       BEFORE the map is looked at, which is the structural guarantee that
       the DM path never consults the operator's configuration
       (``INVARIANT-DM``);
    2. a **proven group DM** ⇒ ``GROUP_DM_SHAPE`` — before the map, so the
       existing group-DM refusal holds whatever the map contains (M-15: a
       group DM whose channel id IS in a valid map still refuses);
    3. an **absent / non-bool ``is_dm``** ⇒ ``ABSENT_DISCRIMINATOR`` — fail
       closed; before the map, for the same reason;
    4. a guild-shaped turn **whose meta LACKS ``is_group_dm``** ⇒
       ``ABSENT_DISCRIMINATOR`` — the M-18 gate: the interaction path builds
       ``discord_meta`` without the key, so it is never designation-eligible
       (the retired ``text``-turn rule's replacement);
    5. an **invalid map** ⇒ ``CONFIG_INVALID`` — the designated-channel path
       is unavailable (the DM path was already short-circuited at leg 1);
    6. a **channel not in the map** ⇒ ``NOT_DESIGNATED``;
    7. an **absent identity** ⇒ ``ABSENT_IDENTITY`` — before the pinned
       comparison, so "no human at all" is never miscoded as "a human who is
       not pinned" (M-8/M-19 are reachable only because of this leg);
    8. a **user ≠ the pinned approver** ⇒ ``APPROVER_NOT_PINNED``;
    9. otherwise ⇒ :attr:`DesignationResult.PASS`.
    """
    meta: Mapping[str, Any] = discord_meta if discord_meta else {}
    is_dm = meta.get("is_dm")
    is_group_dm = meta.get("is_group_dm")

    # 1. a proven 1:1 DM — the map is NEVER read.
    if is_dm is True and is_group_dm is False:
        return DesignationResult.PASS
    # 2. a proven group DM.
    if is_group_dm is True:
        return DesignationResult.GROUP_DM_SHAPE
    # 3. an absent / non-bool DM discriminator (fail closed).
    if is_dm is not False:
        return DesignationResult.ABSENT_DISCRIMINATOR
    # 4. a guild-shaped turn with an absent / non-False is_group_dm (M-18).
    if is_group_dm is not False:
        return DesignationResult.ABSENT_DISCRIMINATOR

    # 5. the operator's map — passed IN, never read from the environment.
    if designations is None or not designations.valid or designations.map is None:
        return DesignationResult.CONFIG_INVALID

    # 6. exact channel-id match only (D-9 — no thread inheritance).
    pinned = designations.map.pinned_approver(meta.get("channel_id"))
    if pinned is None:
        return DesignationResult.NOT_DESIGNATED

    # 7. the absent-identity leg — precedes the pinned comparison.
    if not (isinstance(user_id, str) and user_id.strip()):
        return DesignationResult.ABSENT_IDENTITY

    # 8. the pinned comparison (per channel, not per human — M-14).
    if user_id.strip() != pinned:
        return DesignationResult.APPROVER_NOT_PINNED

    # 9. the designated channel, by its pinned approver.
    return DesignationResult.PASS


def resolve_pinned_approver(
    designations: "DesignationParseResult | None",
    discord_meta: Mapping[str, Any] | None,
) -> str | None:
    """The approver pinned to this turn's channel, or ``None``.

    A convenience accessor for diagnostics/tests only — it does NOT decide
    anything (the decision is :func:`designation_outcome`). ``None`` when the
    map is unset/invalid or the channel is not designated.
    """
    if designations is None or not designations.valid or designations.map is None:
        return None
    meta: Mapping[str, Any] = discord_meta if discord_meta else {}
    return designations.map.pinned_approver(meta.get("channel_id"))


def is_designated_assent_channel(
    designations: "DesignationParseResult | None",
    discord_meta: Mapping[str, Any] | None,
    user_id: Any,
) -> bool:
    """``True`` ONLY for a designated channel turned by its pinned approver.

    The boolean convenience over :func:`designation_outcome` — but NOT merely
    ``outcome is PASS``: a proven 1:1 DM also returns ``PASS`` (leg 1) while
    being *private*, not *designated*. This accessor answers the narrower
    question (AC4: *"``True`` only for an exact channel-id match whose pinned
    approver equals the turn's user id"*), so a DM — and a group DM — is
    always ``False`` here.

    Callers that need to REFUSE with a code must use the typed outcome (a
    boolean loses which leg failed).
    """
    return (
        designation_outcome(designations, discord_meta, user_id)
        is DesignationResult.PASS
        and not is_private_assent_channel(discord_meta)
    )
