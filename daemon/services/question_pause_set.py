"""Durable record + symmetric un-pause for question-pause side effects.

Commission: ``fix/question-pause-resume-asymmetry`` (side-effect-paused
instances stranded after a sibling's ``ask_questions``).

THE DEFECT
----------
``ask_questions`` pauses the WHOLE tree of the asker (the pause cascade
— ``daemon/services/instance_lifecycle.py::pause_instance_cascade``).
Every instance the cascade pauses *other than the asker* is a
**side-effect target**: it has no ``awaiting_answer`` suspension handle
of its own, so the answer path can never resume its turn.

Before this module the paused set was never recorded, so:

* the answer path could only un-pause what the tree enumeration
  happened to reach (``resume_instance_cascade``), and
* nothing at all un-paused a side-effect target whose pause landed
  *after* the answer was consumed.

The result was a stranded instance: parked (or running-but-wake-less)
forever, holding its parent in ``waiting_children``.

THE FIX (R1 + R2)
-----------------
R1 — **record** the paused set durably, keyed by the question pack id,
     in the ASKER's ``instances.metadata`` JSON column (the same
     durable-shadow mechanism the question pack payload already uses —
     ``daemon/services/midflight_qa.py``). No schema change: the
     ``instances.metadata`` column already exists and is written
     atomically per key (``jsonb_set`` / ``json_set``).

R2 — **release exactly the recorded set** on answer delivery, on
     BOTH the CAS-win branch AND the already-delivered no-op branch
     (a pack can be ``answered`` while its recorded side-effect set is
     still parked — that IS the stranded state). Idempotent, and
     derived from the RECORDED SET — never from "was the pack
     answered". The release is UNCONDITIONAL once the pack is
     consumed: it runs in a ``finally`` so a raising cascade cannot
     skip it (a skipped release after a consumed pack is the strand
     this commission exists to fix).

Storage shape (durable, on the asker's row)::

    instances.metadata["question_paused_set"] = {
        "<pack_id>": {
            "instance_ids": ["<id>", ...],
            "pre_status": {"<id>": "<status before the pause>", ...},
            "paused_at": "<iso>",
            "originator": "<asker instance id>",
        },
        ...
    }

``pre_status`` records the status each instance held BEFORE the
cascade parked it. It is load-bearing: a ``waiting_children`` parent
must return to ``waiting_children`` (not to ``running``) so the
dependency bus keeps owning its wake — see
:func:`unpause_instance_after_question_pause`.

The inner map is keyed by pack id so two question packs (even on the
same asker) never clobber each other; the outer write is a single
atomic per-key UPDATE, so concurrent writers on DIFFERENT asker rows
never interfere. The map is pruned to the ``_MAX_RECORDED_SETS``
newest entries, and a pack entry is deleted once its release
completes without error.

CONCURRENCY NOTE
----------------
The merge is read-modify-write on one metadata key. It is guarded by a
process-local lock, and the lost-update window is bounded by a single
metadata read + write. A lost update can only ever *hide* a paused
set — it can never strand an instance that the sweep would otherwise
miss, because the backstop sweeper
(``daemon/services/stale_pause_sweep.py``) is predicate-based (parked
+ no live turn + no pending question) and does not depend on the
recorded set at all.

EVENT-LOOP DISCIPLINE
---------------------
Every repository read/write in this module is wrapped in
``asyncio.to_thread`` — the project convention for blocking DB calls
on the event-loop thread (mirrors ``answer_helper._load_asker`` and
``auto_continue_boot_pass``). The release runs on the answer path
(per answer) and in the sweeper (per candidate), so an un-wrapped
sync query would stall the loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

#: Metadata key (on the ASKER's ``instances.metadata`` row) that holds
#: the durable paused-set map. See the module docstring for the shape.
QUESTION_PAUSED_SET_METADATA_KEY = "question_paused_set"

#: Metadata key (on EACH PARKED INSTANCE's own row) that marks the
#: instance as parked BY A QUESTION. This is the sweeper's pause-cause
#: discriminator (R1, review cycle 3): the sweeper only considers
#: instances that carry this marker, so a DELIBERATE pause (user
#: ``/pause``, ``/stop``, the agent pause tools, watchover activation,
#: the compaction executor) is never undone by the backstop.
#:
#: The marker is stamped by the QUESTION pause cascade on every node it
#: parks, and CLEARED by every other pause reason (a deliberate pause
#: must not inherit a stale question marker) and by a clean release.
#: Schema-free: ``instances.metadata`` is an existing JSON column.
QUESTION_PAUSED_MARKER_METADATA_KEY = "question_paused"

#: Process-local guard for the read-modify-write merge of the metadata
#: key. The critical section is one metadata read + one write.
_MERGE_LOCK = threading.Lock()

#: Maximum number of recorded paused sets kept per asker row. The map
#: is pruned by the oldest ``paused_at`` when the bound is exceeded, so
#: the metadata column cannot grow without bound for an instance that
#: asks many questions. A pruned (dropped) record is never fatal: the
#: backstop sweeper is predicate-based and does not depend on the
#: recorded set at all.
_MAX_RECORDED_SETS = 8

# Outcomes returned by :func:`unpause_instance_after_question_pause`.
OUTCOME_MISSING = "missing"
OUTCOME_SKIPPED_STATUS = "skipped_status"
OUTCOME_UNPAUSED = "unpaused"
OUTCOME_ALREADY_RUNNING = "already_running"
OUTCOME_WAKE_SCHEDULED = "wake_scheduled"
OUTCOME_WAKE_REFUSED = "wake_refused"
OUTCOME_WC_PARKED = "wc_parked"
OUTCOME_ERROR = "error"
#: The instance is still parked, but the park is a LATER DELIBERATE
#: pause (user ``/pause``, ``/stop``, the agent pause tools, watchover
#: activation) — NOT the question pause. A deliberate pause always
#: wins: the release leaves the instance parked and does NOT re-arm
#: its stopped turn. The sweeper already defers to a later deliberate
#: pause (the pause-cause MARKER is cleared by it); this outcome makes
#: the RELEASE path agree.
OUTCOME_DELIBERATE_PAUSE = "deliberate_pause"

#: Suspension reasons that mean "a DELIBERATE pause owns this turn".
#: These are the two reasons a deliberate pause writes onto the turn it
#: suspends (``paused_external`` — the default for a user pause /
#: stop / agent pause tool, and ``watchover_setup`` — the watchover
#: activation). A turn suspended by the QUESTION cascade carries
#: ``awaiting_answer`` (the originator) or ``paused_by_parent`` (the
#: cascade artifacts) instead.
#:
#: Only these two are treated as deliberate. A NULL / legacy /
#: unknown reason is NOT evidence of a deliberate pause — the release
#: stays fail-open for it (a missed release is recoverable through the
#: backstop sweeper; a wrong park is not).
_DELIBERATE_SUSPENSION_REASONS = frozenset(
    {"paused_external", "watchover_setup"}
)


#: Resume-result statuses that mean a REAL turn / wake was scheduled.
#: Everything else (``silent_resume`` = the internal-child no-op,
#: ``wake_failed`` = the loud WC refusal, ``None`` = no handle) is a
#: REFUSAL and must be surfaced, never counted as a wake.
_WAKE_STATUSES = frozenset({"resuming", "wake_enqueued", "resumed", "queued"})


# ---------------------------------------------------------------------------
# Pack state
# ---------------------------------------------------------------------------


async def question_pack_state(
    manager: "InstanceManager", instance_id: str
) -> tuple[str, str | None]:
    """Return ``(state, pack_id)`` for ``instance_id``'s question pack.

    ``state`` is one of:

    * ``"pending"``   — a question is outstanding (RAM pack pending OR
      the durable ``question_pack_payload`` shadow says ``pending``);
    * ``"answered"``  — the pack was consumed (RAM pack ``answered``);
    * ``"absent"``    — no pack in RAM and no durable shadow;
    * ``"unknown"``   — the question store is not wired, so the state
      cannot be determined (fail-open: the caller proceeds).

    The durable shadow is consulted as well because the RAM store is
    lost on restart while the shadow survives (the v0.25.2 boot
    rehydrate restores the RAM pack from it).

    The shadow schema is the frozen ``pack_to_dict`` payload, whose id
    key is ``pack_id`` (NOT ``id``) — see
    ``daemon/services/question_manager.py::pack_to_dict``.
    """
    qm = getattr(manager, "_question_manager", None)
    if qm is None:
        # No question store wired — the state cannot be determined.
        return "unknown", None
    try:
        pack = qm.get_question_pack(instance_id)
    except Exception as exc:  # noqa: BLE001 — read-only probe
        logger.warning(
            "question_pause_set: RAM pack read failed for %s: %s",
            instance_id[:8],
            exc,
        )
        pack = None
    if pack is not None:
        if getattr(pack, "status", None) == "pending":
            return "pending", getattr(pack, "id", None)
        return "answered", getattr(pack, "id", None)

    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return "unknown", None
    try:
        from daemon.services.midflight_qa import (
            QUESTION_PACK_PAYLOAD_METADATA_KEY,
        )

        payload = await asyncio.to_thread(
            repo.get_metadata_value,
            instance_id,
            QUESTION_PACK_PAYLOAD_METADATA_KEY,
        )
    except Exception as exc:  # noqa: BLE001 — read-only probe
        logger.warning(
            "question_pause_set: durable shadow read failed for %s: %s",
            instance_id[:8],
            exc,
        )
        payload = None
    if isinstance(payload, dict):
        # The frozen ``pack_to_dict`` schema keys the id as ``pack_id``.
        # ``id`` is accepted as a legacy/alternate spelling.
        shadow_pack_id = payload.get("pack_id") or payload.get("id")
        if payload.get("status") == "pending":
            return "pending", shadow_pack_id
        return "answered", shadow_pack_id

    return "absent", None


# ---------------------------------------------------------------------------
# R1 — durable record
# ---------------------------------------------------------------------------


async def record_question_paused_set(
    manager: "InstanceManager",
    asker_id: str,
    pack_id: str | None,
    instance_ids: list[str],
    pre_status: dict[str, str] | None = None,
) -> list[str]:
    """Persist the side-effect paused set for ``pack_id`` (R1).

    The set is stored on the ASKER's row (the asker owns the question
    pack, so the asker's metadata row is the natural durable home).

    Args:
        manager: The instance manager.
        asker_id: The originator of the question pause.
        pack_id: The question pack id — the key of the record.
        instance_ids: The instances the cascade parked.
        pre_status: Optional ``{instance_id: status-before-the-pause}``
            map. Load-bearing for ``waiting_children`` parents (they
            must return to ``waiting_children``, not to ``running``).

    Returns the recorded id list (empty when nothing was recorded).
    Fail-open: a storage failure never breaks the pause.
    """
    ids = sorted({i for i in (instance_ids or []) if i and i != asker_id})
    if not ids:
        return []

    if not pack_id:
        # THE record-dropped condition: instances were parked but there
        # is no key to record them under, so the release could never
        # find them. This MUST be loud — a silent drop is a silent
        # no-op of the whole fix on this path.
        logger.error(
            "question_pause_set: RECORD DROPPED — %d side-effect pause(s) "
            "for asker %s could NOT be recorded (no question pack id "
            "resolved); the answer path will not be able to release "
            "them (ids=%s)",
            len(ids),
            asker_id[:8],
            ",".join(i[:8] for i in ids),
        )
        return []

    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        logger.error(
            "question_pause_set: RECORD DROPPED — no instance_repository "
            "wired; %d side-effect pause(s) for asker %s are not "
            "recorded (ids=%s)",
            len(ids),
            asker_id[:8],
            ",".join(i[:8] for i in ids),
        )
        return []

    entry: dict[str, Any] = {
        "instance_ids": ids,
        "paused_at": datetime.now(timezone.utc).isoformat(),
        "originator": asker_id,
    }
    if pre_status:
        entry["pre_status"] = {
            str(k): str(v) for k, v in pre_status.items() if k in ids
        }

    try:
        with _MERGE_LOCK:
            current = await asyncio.to_thread(
                repo.get_metadata_value,
                asker_id,
                QUESTION_PAUSED_SET_METADATA_KEY,
            )
            merged: dict[str, Any] = {}
            if isinstance(current, dict):
                merged = {
                    str(k): v for k, v in current.items() if isinstance(v, dict)
                }
            merged[str(pack_id)] = entry
            if len(merged) > _MAX_RECORDED_SETS:
                # Prune the OLDEST entries (by ``paused_at``) so the
                # metadata column stays bounded. The dropped record is
                # covered by the backstop sweeper.
                ordered = sorted(
                    merged.items(),
                    key=lambda kv: str((kv[1] or {}).get("paused_at", "")),
                )
                for stale_key, _stale_value in ordered[
                    : len(merged) - _MAX_RECORDED_SETS
                ]:
                    merged.pop(stale_key, None)
            await asyncio.to_thread(
                repo.set_metadata,
                asker_id,
                QUESTION_PAUSED_SET_METADATA_KEY,
                merged,
            )
    except Exception as exc:  # noqa: BLE001 — durability is best-effort
        logger.error(
            "question_pause_set: record FAILED for asker %s pack %s: %s "
            "(%d parked instance(s) are NOT recorded)",
            asker_id[:8],
            str(pack_id)[:8],
            exc,
            len(ids),
        )
        return []

    logger.info(
        "question_pause_set: recorded %d side-effect pause(s) for "
        "asker %s pack %s",
        len(ids),
        asker_id[:8],
        str(pack_id)[:8],
    )
    return ids


async def read_question_pause_record(
    manager: "InstanceManager", asker_id: str, pack_id: str | None
) -> dict:
    """Read the FULL recorded pause entry for ``pack_id`` (R1/R2).

    Returns ``{"instance_ids": [...], "pre_status": {...}, ...}`` or an
    empty dict when nothing is recorded (or on any read failure —
    fail-open).
    """
    if not pack_id:
        return {}
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return {}
    try:
        current = await asyncio.to_thread(
            repo.get_metadata_value,
            asker_id,
            QUESTION_PAUSED_SET_METADATA_KEY,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "question_pause_set: read failed for asker %s pack %s: %s",
            asker_id[:8],
            str(pack_id)[:8],
            exc,
        )
        return {}
    if not isinstance(current, dict):
        return {}
    entry = current.get(str(pack_id))
    if not isinstance(entry, dict):
        return {}
    return entry


async def read_question_paused_set(
    manager: "InstanceManager", asker_id: str, pack_id: str | None
) -> list[str]:
    """Read the recorded side-effect paused set for ``pack_id`` (R1/R2)."""
    entry = await read_question_pause_record(manager, asker_id, pack_id)
    ids = entry.get("instance_ids")
    if not isinstance(ids, list):
        return []
    return [i for i in ids if isinstance(i, str) and i]


async def delete_question_pause_record(
    manager: "InstanceManager", asker_id: str, pack_id: str | None
) -> None:
    """Drop the recorded entry for ``pack_id`` (after a clean release)."""
    if not pack_id:
        return
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return
    try:
        with _MERGE_LOCK:
            current = await asyncio.to_thread(
                repo.get_metadata_value,
                asker_id,
                QUESTION_PAUSED_SET_METADATA_KEY,
            )
            if not isinstance(current, dict):
                return
            if str(pack_id) not in current:
                return
            merged = {
                str(k): v
                for k, v in current.items()
                if isinstance(v, dict) and str(k) != str(pack_id)
            }
            if merged:
                await asyncio.to_thread(
                    repo.set_metadata,
                    asker_id,
                    QUESTION_PAUSED_SET_METADATA_KEY,
                    merged,
                )
            else:
                await asyncio.to_thread(
                    repo.delete_metadata,
                    asker_id,
                    QUESTION_PAUSED_SET_METADATA_KEY,
                )
    except Exception as exc:  # noqa: BLE001 — cleanup is best-effort
        logger.warning(
            "question_pause_set: delete failed for asker %s pack %s: %s",
            asker_id[:8],
            str(pack_id)[:8],
            exc,
        )


# ---------------------------------------------------------------------------
# R1 (review cycle 3) — the pause-cause marker
# ---------------------------------------------------------------------------


async def stamp_question_pause_marker(
    manager: "InstanceManager",
    instance_id: str,
    pack_id: str | None,
    paused_at: str | None = None,
) -> bool:
    """Mark ``instance_id`` as parked BY A QUESTION (R1 discriminator).

    The marker lives on the parked instance's OWN ``instances.metadata``
    row (an existing JSON column — no migration). It is the sweeper's
    pause-cause filter: the backstop only considers instances that carry
    it, so a DELIBERATE pause is never undone by the sweeper.

    Returns True when the marker was written.
    """
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return False
    payload = {
        "pack_id": pack_id,
        "paused_at": paused_at
        or datetime.now(timezone.utc).isoformat(),
    }
    try:
        await asyncio.to_thread(
            repo.set_metadata,
            instance_id,
            QUESTION_PAUSED_MARKER_METADATA_KEY,
            payload,
        )
    except Exception as exc:  # noqa: BLE001 — fail-safe (the sweeper
        # simply will not consider this instance)
        logger.warning(
            "question_pause_set: marker stamp failed for %s: %s: %s",
            instance_id[:8],
            type(exc).__name__,
            exc,
        )
        return False
    return True


async def clear_question_pause_marker(
    manager: "InstanceManager", instance_id: str
) -> bool:
    """Clear the question-pause marker from ``instance_id``.

    Called by every NON-question pause (a deliberate pause must not
    inherit a stale question marker) and by a clean release.
    """
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return False
    try:
        await asyncio.to_thread(
            repo.delete_metadata,
            instance_id,
            QUESTION_PAUSED_MARKER_METADATA_KEY,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "question_pause_set: marker clear failed for %s: %s: %s",
            instance_id[:8],
            type(exc).__name__,
            exc,
        )
        return False
    return True


# ---------------------------------------------------------------------------
# R2 — symmetric release
# ---------------------------------------------------------------------------


def _classify_wake(result: Any) -> tuple[str, str]:
    """Classify a resume result into ``(outcome, detail)``.

    A result is a REAL wake only when its status is in
    ``_WAKE_STATUSES``. ``silent_resume`` (the internal-child no-op),
    ``wake_failed`` (the loud WC refusal) and ``None`` (no handle) are
    REFUSALS — counting them as a wake would hide the strand from the
    operator.
    """
    if result is None:
        return OUTCOME_WAKE_REFUSED, "no handle (invalid_or_missing_handle)"
    if not isinstance(result, dict):
        return OUTCOME_WAKE_REFUSED, f"unexpected resume result {result!r}"
    status = result.get("status")
    if status in _WAKE_STATUSES:
        return OUTCOME_WAKE_SCHEDULED, str(status)
    return OUTCOME_WAKE_REFUSED, str(status or "unknown_status")


async def _set_instance_status(
    manager: "InstanceManager",
    instance_id: str,
    target_status: str,
    allowed_from: tuple[str, ...] = ("paused",),
) -> bool:
    """Flip an instance's status (guarded, off-loop).

    ``allowed_from`` defaults to the parked shape (``paused``); the
    WC-correction path passes ``("running",)`` because the tree-scoped
    resume cascade has already flipped the parked node to ``running``
    and the correction re-establishes the recorded parked state.
    """
    repo = manager._instance_repository
    try:
        await asyncio.to_thread(
            repo.transition_status_if,
            instance_id,
            target_status,
            allowed_from,
        )
        await asyncio.to_thread(repo.update, instance_id, paused_at=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "question_pause_set: un-pause write failed for %s "
            "(target=%s): %s",
            instance_id[:8],
            target_status,
            exc,
        )
        return False
    return True


async def unpause_instance_after_question_pause(
    manager: "InstanceManager",
    instance_id: str,
    *,
    reason: str,
    pre_status: str | None = None,
) -> str:
    """Release one parked instance and let its turn complete (R2/R3).

    Steps:

    1. **Un-pause** — flip ``PAUSED → <target>`` and clear
       ``paused_at`` (guarded on ``status='paused'`` so a concurrent
       resume is a no-op). ``<target>`` is the RECORDED pre-pause status
       when known:
       * ``waiting_children`` → the parent returns to
         ``waiting_children`` (NOT ``running``): the dependency bus owns
         its wake, and the status flip alone must not fake a live turn;
       * ``idle`` → the instance returns to ``idle`` (it had no turn);
       * otherwise → ``running``.
    2. **Wake** — the status flip alone never completes a turn:
       * a ``waiting_children`` target goes through
         ``resume_processing_job(silent=True)``, whose WC branch
         enqueues a REAL wake turn (``route_outcome=b2_wc_wake``);
       * a target that owns a PAUSED turn is routed through
         ``resume_processing_job`` (PAUSED → PENDING + the graph
         re-arm);
       * a target with no live turn gets a SILENT checkpoint resume —
         but only when a checkpoint actually exists (without one the
         resume would start a FRESH turn with an empty prompt).

    Returns one of the ``OUTCOME_*`` constants. Never raises.
    """
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return OUTCOME_ERROR
    try:
        row = await asyncio.to_thread(repo.get, instance_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "question_pause_set: instance read failed for %s: %s",
            instance_id[:8],
            exc,
        )
        return OUTCOME_ERROR
    if row is None:
        return OUTCOME_MISSING

    current = getattr(row, "status", None)
    if current not in ("paused", "running"):
        # Terminal / idle / waiting_children — not a parked instance.
        return OUTCOME_SKIPPED_STATUS

    def _finish(
        outcome: str,
        restored_status: str | None = None,
        *,
        cause: str = "question_pause",
    ) -> str:
        """Log the per-candidate disposition (A-3).

        The line states the RESTORED STATUS and the PAUSE CAUSE so an
        operator can tell from the log that a pause was reverted — this
        matters precisely because the backstop is allowed to revert a
        pause (and the R1 discriminator is what keeps it to the
        question stratum only).

        ``cause`` is the cause of the pause the disposition applies to:
        ``question_pause`` for the normal release path, and
        ``deliberate_pause`` when a LATER deliberate pause won and the
        instance was left parked (the A-3 line must distinguish the
        two dispositions, not report a deliberate park as a release).
        """
        logger.info(
            "question_pause_set: release instance=%s reason=%s "
            "cause=%s restored_status=%s outcome=%s",
            instance_id[:8],
            reason,
            cause,
            restored_status or "<unchanged>",
            outcome,
        )
        return outcome

    task_repo = getattr(manager, "_task_repo", None)
    paused_turn = None
    probe_failed = False
    if task_repo is not None:
        try:
            paused_turn = await asyncio.to_thread(
                task_repo.find_paused_or_cancellable_turn, instance_id
            )
        except Exception as exc:  # noqa: BLE001
            # The documented "more than one concurrently eligible turn"
            # ambiguity lands here. Do NOT conclude "no turn": the turn
            # may well exist. Route it through the resume path anyway
            # (it handles the ambiguity internally) and surface the
            # invariant violation at ERROR level.
            probe_failed = True
            logger.error(
                "question_pause_set: paused-turn probe FAILED for %s "
                "(reason=%s) — %s: %s; routing through "
                "resume_processing_job anyway",
                instance_id[:8],
                reason,
                type(exc).__name__,
                exc,
            )

    # N3 — PRECEDENCE: a LATER DELIBERATE pause wins.
    #
    # The sweeper already defers to a later deliberate pause (a
    # non-question pause CLEARS the pause-cause marker, so the instance
    # is not a candidate). The release-from-record path did NOT: the
    # instance is still in the asker's recorded set, so when the answer
    # arrived the release un-paused it AND re-armed its deliberately
    # stopped turn. The two paths of the same feature disagreed on
    # precedence.
    #
    # The discriminator is the SUSPENDED TURN's own reason — already
    # probed above (``find_paused_or_cancellable_turn``), so no extra
    # query is needed. A deliberate pause writes ``paused_external``
    # (the default for a user ``/pause`` / ``/stop`` / agent pause
    # tool) or ``watchover_setup`` (the watchover activation) onto the
    # turn it suspends; the QUESTION cascade writes ``awaiting_answer``
    # (the originator) or ``paused_by_parent`` (the cascade artifacts).
    #
    # The instance is left PARKED and its turn is NOT re-armed: the
    # operator's park stands. The question stratum is released exactly
    # as before (a turn carrying a question reason never reaches this
    # branch).
    if current == "paused":
        turn_reason = getattr(paused_turn, "suspension_reason", None)
        if turn_reason in _DELIBERATE_SUSPENSION_REASONS:
            logger.info(
                "question_pause_set: LEAVING instance=%s parked — a later "
                "DELIBERATE pause (reason=%s) owns this turn; the question "
                "release does not revert it",
                instance_id[:8],
                turn_reason,
            )
            return _finish(
                OUTCOME_DELIBERATE_PAUSE,
                current,
                cause="deliberate_pause",
            )

    has_live_turn = bool(
        paused_turn is not None
        and getattr(paused_turn, "status", None) in ("paused", "running")
    )
    if task_repo is not None and not has_live_turn:
        try:
            has_live_turn = await asyncio.to_thread(
                task_repo.has_instance_busy, instance_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "question_pause_set: busy probe failed for %s: %s",
                instance_id[:8],
                exc,
            )

    if pre_status == "waiting_children":
        target_status = "waiting_children"
    elif pre_status == "idle":
        target_status = "idle"
    else:
        target_status = "running"

    outcome = OUTCOME_ALREADY_RUNNING
    restored_status = current
    if current == "paused":
        if not await _set_instance_status(manager, instance_id, target_status):
            return _finish(OUTCOME_ERROR)
        outcome = OUTCOME_UNPAUSED
        restored_status = target_status
    elif (
        current == "running"
        and target_status == "waiting_children"
        and not has_live_turn
    ):
        # The tree-scoped resume cascade flips EVERY parked node to
        # ``running`` — which loses a ``waiting_children`` parent's
        # parked state. Restore it (the dependency bus owns its wake) so
        # the WC wake path can fire.
        if not await _set_instance_status(
            manager, instance_id, "waiting_children", allowed_from=("running",)
        ):
            return _finish(OUTCOME_ERROR)
        outcome = OUTCOME_UNPAUSED
        restored_status = "waiting_children"

    # ── WC parent: the dependency bus owns the wake ──────────────────
    if target_status == "waiting_children":
        resume_processing_job = getattr(manager, "resume_processing_job", None)
        if resume_processing_job is None:
            return _finish(outcome, restored_status)
        try:
            result = await resume_processing_job(
                instance_id, message="resume", silent=True
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "question_pause_set: WC wake failed for %s (reason=%s): "
                "%s: %s",
                instance_id[:8],
                reason,
                type(exc).__name__,
                exc,
            )
            return _finish(OUTCOME_WAKE_REFUSED, restored_status)
        wake_outcome, detail = _classify_wake(result)
        if wake_outcome == OUTCOME_WAKE_SCHEDULED:
            logger.info(
                "question_pause_set: WC wake scheduled for %s "
                "(reason=%s, status=%s)",
                instance_id[:8],
                reason,
                detail,
            )
            return _finish(OUTCOME_WAKE_SCHEDULED, restored_status)
        logger.warning(
            "question_pause_set: WC wake REFUSED for %s (reason=%s): %s "
            "— the parent stays parked; the dependency bus owns the wake",
            instance_id[:8],
            reason,
            detail,
        )
        return _finish(OUTCOME_WC_PARKED, restored_status)

    # ── a target with no live turn owns nothing to complete ──────────
    if target_status == "idle":
        return _finish(outcome, restored_status)

    if probe_failed or (
        paused_turn is not None
        and getattr(paused_turn, "status", None) == "paused"
    ):
        resume_processing_job = getattr(manager, "resume_processing_job", None)
        if resume_processing_job is None:
            return _finish(outcome, restored_status)
        try:
            result = await resume_processing_job(
                instance_id, message="resume", silent=True
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "question_pause_set: turn resume failed for %s "
                "(reason=%s): %s: %s",
                instance_id[:8],
                reason,
                type(exc).__name__,
                exc,
            )
            return _finish(OUTCOME_WAKE_REFUSED, restored_status)
        wake_outcome, detail = _classify_wake(result)
        if wake_outcome == OUTCOME_WAKE_SCHEDULED:
            logger.info(
                "question_pause_set: turn resumed for %s (reason=%s, "
                "status=%s)",
                instance_id[:8],
                reason,
                detail,
            )
            return _finish(OUTCOME_WAKE_SCHEDULED, restored_status)
        logger.warning(
            "question_pause_set: turn resume REFUSED for %s (reason=%s): "
            "%s",
            instance_id[:8],
            reason,
            detail,
        )
        return _finish(OUTCOME_WAKE_REFUSED, restored_status)

    if has_live_turn:
        # A PENDING/RUNNING task owns the turn — the worker pool
        # re-claims it (the cascade flipped PAUSED → PENDING).
        return _finish(outcome, restored_status)

    # No live turn → a silent checkpoint resume is the only way to
    # re-arm the interrupted graph. Guard it on a real checkpoint: with
    # none, the resume would start a FRESH turn with an empty prompt.
    has_checkpoint = getattr(manager, "_has_checkpoint", None)
    if has_checkpoint is not None:
        try:
            checkpoint_exists = await has_checkpoint(instance_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "question_pause_set: checkpoint probe failed for %s: %s",
                instance_id[:8],
                exc,
            )
            checkpoint_exists = False
        if not checkpoint_exists:
            logger.warning(
                "question_pause_set: no checkpoint for %s (reason=%s) — "
                "the interrupted turn cannot be re-armed; leaving the "
                "instance idle (no limbo 'running with no work')",
                instance_id[:8],
                reason,
            )
            try:
                await asyncio.to_thread(
                    repo.transition_status_if,
                    instance_id,
                    "idle",
                    ("running",),
                )
            except Exception:  # noqa: BLE001 — best-effort
                pass
            return _finish(OUTCOME_WAKE_REFUSED, restored_status)

    schedule = getattr(manager, "_schedule_explicit_handle_resume", None)
    if schedule is None:
        logger.warning(
            "question_pause_set: no resume primitive available for %s "
            "(reason=%s) — released without a wake",
            instance_id[:8],
            reason,
        )
        return _finish(outcome, restored_status)

    synthetic_wid = (
        f"question-pause-unpause-{instance_id[:8]}-"
        f"{int(time.monotonic() * 1000)}"
    )
    try:
        result = await schedule(
            instance_id=instance_id,
            message="",
            silent=True,
            images=None,
            target_work_id=synthetic_wid,
            selected_suspension_reason=None,
            handle_work_id=synthetic_wid,
            route_outcome=f"question_pause_unpause_{reason}",
            image_refs=None,
        )
    except Exception as exc:  # noqa: BLE001 — never break the caller
        logger.warning(
            "question_pause_set: wake failed for %s (reason=%s): %s: %s",
            instance_id[:8],
            reason,
            type(exc).__name__,
            exc,
        )
        return OUTCOME_WAKE_REFUSED

    wake_outcome, detail = _classify_wake(result)
    if wake_outcome != OUTCOME_WAKE_SCHEDULED:
        logger.warning(
            "question_pause_set: wake REFUSED for %s (reason=%s): %s",
            instance_id[:8],
            reason,
            detail,
        )
        return _finish(OUTCOME_WAKE_REFUSED, restored_status)
    logger.info(
        "question_pause_set: wake scheduled for %s (reason=%s, status=%s)",
        instance_id[:8],
        reason,
        detail,
    )
    return _finish(OUTCOME_WAKE_SCHEDULED, restored_status)


async def release_question_paused_set(
    manager: "InstanceManager",
    asker_id: str,
    pack_id: str | None,
    *,
    reason: str = "answer",
) -> dict:
    """Release exactly the recorded set for ``pack_id`` (R2).

    Idempotent and branch-independent: the caller may be the CAS winner
    OR the already-delivered no-op — the recorded set is the source of
    truth, never "was the pack answered".

    The record is deleted once the release completes without error, so
    the metadata does not accumulate superseded entries. A release that
    hit an error KEEPS the record so a later attempt can retry it (the
    backstop sweeper is the safety net either way).

    Returns a summary dict:
    ``{"recorded": [...], "outcomes": {id: outcome}, "released": n,
    "woken": n, "refused": n, "errors": n}``.
    """
    record = await read_question_pause_record(manager, asker_id, pack_id)
    recorded = [
        i
        for i in (record.get("instance_ids") or [])
        if isinstance(i, str) and i
    ]
    pre_status = record.get("pre_status") or {}
    if not isinstance(pre_status, dict):
        pre_status = {}

    summary: dict[str, Any] = {
        "recorded": recorded,
        "outcomes": {},
        "released": 0,
        "woken": 0,
        "refused": 0,
        "skipped": 0,
        "errors": 0,
    }
    if not recorded:
        return summary

    for instance_id in recorded:
        outcome = await unpause_instance_after_question_pause(
            manager,
            instance_id,
            reason=reason,
            pre_status=pre_status.get(instance_id),
        )
        summary["outcomes"][instance_id] = outcome
        if outcome in (OUTCOME_UNPAUSED, OUTCOME_WAKE_SCHEDULED):
            summary["released"] += 1
            if outcome == OUTCOME_WAKE_SCHEDULED:
                summary["woken"] += 1
        elif outcome in (OUTCOME_WAKE_REFUSED, OUTCOME_WC_PARKED):
            summary["refused"] += 1
        elif outcome == OUTCOME_ERROR:
            summary["errors"] += 1
        elif outcome in (OUTCOME_MISSING, OUTCOME_SKIPPED_STATUS):
            # The instance is gone / terminal — nothing left to release.
            summary["skipped"] += 1
        elif outcome == OUTCOME_DELIBERATE_PAUSE:
            # A LATER DELIBERATE pause owns the instance — it stays
            # parked on purpose and its turn is NOT re-armed. Nothing was
            # released and nothing must be retried: the operator's park
            # stands (and the sweeper never sees it — a deliberate pause
            # clears the pause-cause marker).
            summary["skipped"] += 1
        else:
            summary["skipped"] += 1

        # The question-pause marker is cleared ONLY on a clean outcome:
        # a refused / errored release keeps it, so the backstop can pick
        # the instance up again (the sweeper is the retry path — A-2).
        #
        # A DELIBERATE park is clean in the same sense: the instance is
        # not waiting for the answer any more, and a question marker on
        # it would let the backstop revert the operator's park.
        if outcome in (
            OUTCOME_UNPAUSED,
            OUTCOME_WAKE_SCHEDULED,
            OUTCOME_WC_PARKED,
            OUTCOME_MISSING,
            OUTCOME_SKIPPED_STATUS,
            OUTCOME_DELIBERATE_PAUSE,
        ):
            await clear_question_pause_marker(manager, instance_id)

    # The record is deleted only after a CLEAN release. A refused /
    # errored release KEEPS it so a later attempt (a duplicate answer,
    # the dismiss path, the backstop) can retry it.
    if summary["errors"] == 0 and summary["refused"] == 0:
        await delete_question_pause_record(manager, asker_id, pack_id)

    logger.info(
        "question_pause_set: release pass (reason=%s) asker=%s pack=%s "
        "recorded=%d released=%d woken=%d refused=%d errors=%d",
        reason,
        asker_id[:8],
        str(pack_id)[:8] if pack_id else "<none>",
        len(recorded),
        summary["released"],
        summary["woken"],
        summary["refused"],
        summary["errors"],
    )
    return summary
