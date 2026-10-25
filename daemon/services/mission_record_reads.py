"""Mission-record READ side — Proposal B PR2 (the enforce reads).

Proposal B's thesis is that a mission is a **written record**, and every
settle/gate/routing decision should READ that record instead of
re-deriving "what is this mission?" from message shapes and tree
topology. PR1 shipped the record (mints + stamps + the tables); **PR2
ships the reads**.

This module is the READ half of that flip. It is deliberately SEPARATE
from :mod:`daemon.services.mission_record_service` (PR1's single
WRITER): the writer's contract is "a records-side problem must never
break a commissioning lane", while the reader's contract is the mirror
image — **a records-side problem must never break a gate**.

FAIL-OPEN CONTRACT (convention #3 — the ``-1`` sentinel family)
----------------------------------------------------------------

Every read here fails OPEN and NEVER degrades to a ``0`` default:

* ``count_outstanding_commissions`` returns ``-1`` for "unknown" (the
  read failed). ``-1`` is NOT ``0``: ``0`` is a real answer ("no
  outstanding commissions"), and collapsing the two would let a failed
  read masquerade as a quiet mission — the exact blindness D2 is about.
* The census read returns ``None`` for "unknown / no record" (a boolean
  read has no ``-1``; ``None`` is its analogue). ``None`` means "the
  record has no answer" and the caller falls back to the LEGACY leg.
* The anchor read returns ``None`` for "unknown / no record" and the
  caller falls back to the LEGACY six-step exclusion ladder.

The gate-side consumers treat ``-1`` / ``None`` as "behave exactly as
before the records existed" (the legacy fallback leg) — the fail-open
direction is always the LEGACY behavior, never a stricter one.

MODE LADDER (restart-read; ship default ``dry``)
------------------------------------------------

``ENSEMBLE_MISSION_RECORDS = off | dry | enforce`` (resolved by
:func:`daemon.services.mission_record_service.resolve_mission_records_mode`):

* ``off``     — no mints, no reads.
* ``dry``     — mints + stamps + logs; **ALL read sites stay on legacy
  behavior** (PR2's default posture — the operator soaks the records
  before the reads consume them).
* ``enforce`` — the read sites consult the records, with the legacy
  behavior as the declared fallback leg.

:func:`daemon.services.mission_record_service.mission_records_enforce_enabled`
is the ONE gate the read sites consult (the mode ladder's single owner —
this module IMPORTS it, it never redefines it). It NEVER raises (an unrecognized mode token degrades to
"not enforcing" — a records-side problem must never break a gate) and
it reuses the service's CACHED, restart-read resolution so a mid-run
env mutation cannot flip a live knob.

TIMESTAMPS
----------

The mission-record tables carry TEXT ISO timestamps (``now_utc_iso``
family). No naive-datetime binds are involved in this module, so the
``now_utc_naive()`` bind discipline does not apply to these reads.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..repositories.mission_record.models import (
    CommissionState,
    MissionCommission,
    MissionRecord,
)
from ..repositories.mission_record.repository import MissionRecordRepository
# The mode ladder has ONE owner: ``mission_record_service``. The gate
# helper (``mission_records_enforce_enabled``) is IMPORTED here, never
# redefined — a second definition here would be a shadowed copy (two
# sources of truth for one knob) and the service docstring's
# "ONE definition" contract would be a lie.
from .mission_record_service import (
    MissionRecordMode,
    mission_records_enforce_enabled,
    resolve_mission_records_mode,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine

logger = logging.getLogger(__name__)


#: The "unknown" sentinel for COUNT reads (convention #3). NEVER a
#: ``0`` default — ``0`` is a real answer ("nothing outstanding").
MISSION_READ_UNKNOWN = -1


def _repo(engine: "Engine") -> MissionRecordRepository:
    return MissionRecordRepository(engine)


def read_open_mission(
    engine: "Engine",
    anchor_instance_id: str,
) -> MissionRecord | None:
    """Read the anchor's OPEN epoch. ``None`` = no record (or a failure).

    ``None`` is the "no answer" sentinel: the caller falls back to the
    legacy leg (today's behavior, exactly). The read NEVER raises.
    """
    if not anchor_instance_id:
        return None
    try:
        return _repo(engine).find_open_epoch_by_anchor(anchor_instance_id)
    except Exception as exc:  # noqa: BLE001 — fail-open read
        logger.warning(
            "mission_records: open-epoch read failed for %s…: %s: %s "
            "— legacy fallback leg",
            anchor_instance_id[:8],
            type(exc).__name__,
            exc,
        )
        return None


def read_open_mission_id(
    engine: "Engine",
    anchor_instance_id: str,
) -> str | None:
    """The open epoch's ``mission_id`` for an anchor (``None`` on a miss).

    The WRITER-side read (the re-founding amendment's no-carrier
    fallback). NOT mode-gated by the caller: the amendment is a
    record-side WRITE that runs in every mode where mints happen
    (``dry`` included) — the mode ladder gates the GATE-side reads,
    never the record-side writes.
    """
    record = read_open_mission(engine, anchor_instance_id)
    if record is None:
        return None
    return record.mission_id


def read_mission_anchor_instance_id(
    engine: "Engine",
    mission_id: str,
) -> str | None:
    """The ANCHOR INSTANCE of a mission row (``None`` on a miss).

    The re-founding amendment's TARGETING GUARD: an amendment may
    only re-found the epoch whose anchor is the DELIVERING instance. A
    delivery whose carrier names a FOREIGN mission (another instance's
    epoch) must not re-found it — that would move a commissioner's
    anchor to a worker's message id.

    Fail-open: ``None`` on any failure (the caller then refuses the
    amendment — the safe direction).
    """
    if not mission_id:
        return None
    try:
        record = _repo(engine).get_mission(mission_id)
        if record is None:
            return None
        return record.anchor_instance_id
    except Exception as exc:  # noqa: BLE001 — fail-open read
        logger.warning(
            "mission_records: mission-anchor read failed for %s…: %s: %s "
            "— unknown (-1 semantics: the amendment is refused)",
            mission_id[:8],
            type(exc).__name__,
            exc,
        )
        return None


def read_mission_census(
    engine: "Engine",
    anchor_instance_id: str,
) -> bool | None:
    """The delegation census read: "does the open mission have ANY
    commission rows?" ``None`` = no record / failure (legacy fallback).

    ``True`` ⇒ the open mission has commission rows (the mission
    delegated — in ANY lane: ``send_message``, ``instance_nudge``,
    ``job_continue``, ``scheduler``, ``spawn``, ``revive``).

    ``False`` ⇒ the open mission exists and has NO commission rows.

    ``None`` ⇒ no open epoch for this anchor, or the read failed — the
    caller uses the LEGACY delegation scan (the declared fallback leg).

    ADVISORY BACKFILL ROWS ARE EXCLUDED (adoption-delta (b)): the
    historical nudge rows are not a live delegation and must not arm the
    gate for missions that never delegated in this epoch.
    """
    record = read_open_mission(engine, anchor_instance_id)
    if record is None:
        return None
    try:
        count = _repo(engine).count_commissions_for_mission(record.mission_id)
    except Exception as exc:  # noqa: BLE001 — fail-open read
        logger.warning(
            "mission_records: census read failed for mission %s…: %s: %s "
            "— legacy fallback leg",
            record.mission_id[:8],
            type(exc).__name__,
            exc,
        )
        return None
    return count > 0


def read_founding_message_id(
    engine: "Engine",
    anchor_instance_id: str,
) -> str | None:
    """The SOURCE U anchor read: the open epoch's ``founding_message_id``.

    ``None`` = no record / no founding message / failure — the caller
    falls back to the LEGACY six-step exclusion ladder (today's
    behavior, exactly).

    The delegation window is "messages at-or-after the founding /
    amendment message id" (proposal-b §2.5). The value is the message id
    of the epoch's founding message — amended at DELIVERY when a user
    correction lands (proposal-b §2.2's re-founding amendment).
    """
    record = read_open_mission(engine, anchor_instance_id)
    if record is None:
        return None
    founding = record.founding_message_id
    if not founding:
        # An honest NULL founding (boot sweep / backfill / a message-less
        # epoch) — there is no anchor to look up; the caller falls back
        # to the legacy ladder.
        return None
    return founding


def count_outstanding_commissions(
    engine: "Engine",
    anchor_instance_id: str,
) -> int:
    """Count the open mission's OUTSTANDING commissions.

    The 5th R2 input (``outstanding_commissions``) and the L3 root
    gate's mission leg both consume this.

    Returns:
        ``0``  — the anchor has an open epoch with no outstanding
                  commissions (a REAL answer: the mission is quiet), OR
                  the anchor has no open epoch at all (no mission ⇒ no
                  commissions ⇒ quiet — the legacy behavior).
        ``N>0``— the open mission has N outstanding commissions.
        ``-1`` — UNKNOWN (the read failed). The fail-open sentinel: the
                  caller treats it as "behave as before the records
                  existed" — NEVER as "0 outstanding".
    """
    try:
        record = _repo(engine).find_open_epoch_by_anchor(anchor_instance_id)
        if record is None:
            # No open epoch ⇒ no mission ⇒ no outstanding commissions.
            # A REAL zero (not the unknown sentinel): the legacy
            # behavior for an instance with no record.
            return 0
        return _repo(engine).count_outstanding_commissions_for_mission(
            record.mission_id
        )
    except Exception as exc:  # noqa: BLE001 — fail-open read
        logger.warning(
            "mission_records: outstanding-commission read failed for %s…: "
            "%s: %s — unknown (-1) sentinel",
            anchor_instance_id[:8],
            type(exc).__name__,
            exc,
        )
        return MISSION_READ_UNKNOWN


def list_outstanding_commissions(
    engine: "Engine",
    anchor_instance_id: str,
) -> list[MissionCommission]:
    """List the open mission's OUTSTANDING commission rows.

    The judge's SOURCE C membership half (proposal-b §3 NEW(v): "C's
    membership = tree ∪ commissions"). Empty list on any failure (the
    union is fail-open — a broken read degrades to counts-only C
    evidence, never to a wrong verdict).
    """
    try:
        record = _repo(engine).find_open_epoch_by_anchor(anchor_instance_id)
        if record is None:
            return []
        return _repo(engine).list_commissions_for_mission(
            record.mission_id,
            states=(CommissionState.OUTSTANDING.value,),
        )
    except Exception as exc:  # noqa: BLE001 — fail-open read
        logger.warning(
            "mission_records: outstanding-commission list failed for %s…: "
            "%s: %s — empty union",
            anchor_instance_id[:8],
            type(exc).__name__,
            exc,
        )
        return []


def read_mission_id(engine: "Engine", anchor_instance_id: str) -> str | None:
    """The open epoch's ``mission_id`` (``None`` = no record / failure).

    Used by the epoch-tagged deny ledger: the deny entry records the
    owning mission so the boundary prune can drop the closed-epoch
    entries.
    """
    record = read_open_mission(engine, anchor_instance_id)
    if record is None:
        return None
    return record.mission_id


__all__ = [
    "MISSION_READ_UNKNOWN",
    "mission_records_enforce_enabled",
    "read_open_mission",
    "read_open_mission_id",
    "read_mission_anchor_instance_id",
    "read_mission_census",
    "read_founding_message_id",
    "count_outstanding_commissions",
    "list_outstanding_commissions",
    "read_mission_id",
]
