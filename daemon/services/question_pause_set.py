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
* nothing at all un-paused a side-effect target whose tree row had
  been re-rooted / whose pause landed *after* the answer was consumed.

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

R2 — **un-pause exactly the recorded set** on answer delivery, on
     BOTH the CAS-win branch AND the already-delivered no-op branch
     (a pack can be ``answered`` while its recorded set is still
     parked — that IS the stranded state). Idempotent, and derived
     from the RECORDED SET — never from "was the pack answered".

Storage shape (durable, on the asker's row)::

    instances.metadata["question_paused_set"] = {
        "<pack_id>": {
            "instance_ids": ["<id>", ...],
            "paused_at": "<iso>",
            "originator": "<asker instance id>",
        },
        ...
    }

The inner map is keyed by pack id so two question packs (even on the
same asker) never clobber each other; the outer write is a single
atomic per-key UPDATE, so concurrent writers on DIFFERENT asker rows
never interfere.

CONCURRENCY NOTE
----------------
The merge is read-modify-write on one metadata key. It is guarded by a
process-local lock, and the lost-update window is bounded by a single
metadata read + write. A lost update can only ever *hide* a paused
set — it can never strand an instance that the sweep would otherwise
miss, because the backstop sweeper
(``daemon/services/stale_pause_sweep.py``) is predicate-based (parked
+ no task row + no pending question) and does not depend on the
recorded set at all.
"""

from __future__ import annotations

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

#: Outcomes returned by :func:`unpause_instance_after_question_pause`.
OUTCOME_MISSING = "missing"
OUTCOME_SKIPPED_STATUS = "skipped_status"
OUTCOME_UNPAUSED = "unpaused"
OUTCOME_ALREADY_RUNNING = "already_running"
OUTCOME_WAKE_SCHEDULED = "wake_scheduled"
OUTCOME_ERROR = "error"


# ---------------------------------------------------------------------------
# Pack state
# ---------------------------------------------------------------------------


def question_pack_state(
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
    """
    qm = getattr(manager, "_question_manager", None)
    if qm is not None:
        try:
            pack = qm.get_question_pack(instance_id)
        except Exception as exc:  # noqa: BLE001 — read-only probe
            logger.warning(
                "question_pack_state: RAM pack read failed for %s: %s",
                instance_id[:8],
                exc,
            )
            pack = None
        if pack is not None:
            if getattr(pack, "status", None) == "pending":
                return "pending", getattr(pack, "id", None)
            return "answered", getattr(pack, "id", None)
    else:
        # No question store wired — the state cannot be determined.
        return "unknown", None

    repo = getattr(manager, "_instance_repository", None)
    if repo is not None:
        try:
            from daemon.services.midflight_qa import (
                QUESTION_PACK_PAYLOAD_METADATA_KEY,
            )

            payload = repo.get_metadata_value(
                instance_id, QUESTION_PACK_PAYLOAD_METADATA_KEY
            )
        except Exception as exc:  # noqa: BLE001 — read-only probe
            logger.warning(
                "question_pack_state: durable shadow read failed for %s: %s",
                instance_id[:8],
                exc,
            )
            payload = None
        if isinstance(payload, dict):
            if payload.get("status") == "pending":
                return "pending", payload.get("id")
            return "answered", payload.get("id")

    return "absent", None


# ---------------------------------------------------------------------------
# R1 — durable record
# ---------------------------------------------------------------------------


def record_question_paused_set(
    manager: "InstanceManager",
    asker_id: str,
    pack_id: str | None,
    instance_ids: list[str],
) -> list[str]:
    """Persist the side-effect paused set for ``pack_id`` (R1).

    The set is stored on the ASKER's row (the asker owns the question
    pack, so the asker's metadata row is the natural durable home).

    Returns the recorded id list (empty when nothing was recorded).
    Fail-open: a storage failure never breaks the pause.
    """
    if not pack_id or not instance_ids:
        return []

    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        logger.debug(
            "question_pause_set: no instance_repository wired — "
            "paused set NOT recorded for %s",
            asker_id[:8],
        )
        return []

    ids = sorted({i for i in instance_ids if i and i != asker_id})
    if not ids:
        return []

    entry = {
        "instance_ids": ids,
        "paused_at": datetime.now(timezone.utc).isoformat(),
        "originator": asker_id,
    }

    try:
        with _MERGE_LOCK:
            current = repo.get_metadata_value(
                asker_id, QUESTION_PAUSED_SET_METADATA_KEY
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
                    key=lambda kv: str(
                        (kv[1] or {}).get("paused_at", "")
                    ),
                )
                for stale_key, _stale_value in ordered[
                    : len(merged) - _MAX_RECORDED_SETS
                ]:
                    merged.pop(stale_key, None)
            repo.set_metadata(
                asker_id, QUESTION_PAUSED_SET_METADATA_KEY, merged
            )
    except Exception as exc:  # noqa: BLE001 — durability is best-effort
        logger.warning(
            "question_pause_set: record failed for asker %s pack %s: %s",
            asker_id[:8],
            str(pack_id)[:8],
            exc,
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


def read_question_paused_set(
    manager: "InstanceManager", asker_id: str, pack_id: str | None
) -> list[str]:
    """Read the recorded side-effect paused set for ``pack_id`` (R1/R2).

    Returns an empty list when nothing is recorded (or on any read
    failure — fail-open).
    """
    if not pack_id:
        return []
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return []
    try:
        current = repo.get_metadata_value(
            asker_id, QUESTION_PAUSED_SET_METADATA_KEY
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "question_pause_set: read failed for asker %s pack %s: %s",
            asker_id[:8],
            str(pack_id)[:8],
            exc,
        )
        return []
    if not isinstance(current, dict):
        return []
    entry = current.get(str(pack_id))
    if not isinstance(entry, dict):
        return []
    ids = entry.get("instance_ids")
    if not isinstance(ids, list):
        return []
    return [i for i in ids if isinstance(i, str) and i]


# ---------------------------------------------------------------------------
# R2 — symmetric un-pause
# ---------------------------------------------------------------------------


async def unpause_instance_after_question_pause(
    manager: "InstanceManager",
    instance_id: str,
    *,
    reason: str,
) -> str:
    """Un-pause one parked instance and let its turn complete (R2/R3).

    Two steps, in order:

    1. **Un-pause** — flip ``PAUSED → RUNNING`` and clear ``paused_at``
       (guarded on ``status='paused'`` so a concurrent resume is a
       no-op).
    2. **Natural resume** — if the instance has no in-flight graph turn
       and no active task row, schedule a SILENT checkpoint resume (the
       same primitive the boot orphan sweep uses,
       ``manager._schedule_explicit_handle_resume``). Without this the
       instance would sit ``running`` with no work and its parent would
       stay in ``waiting_children`` forever.

    Returns one of the ``OUTCOME_*`` constants. Never raises.
    """
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return OUTCOME_ERROR
    try:
        row = repo.get(instance_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "question_pause_set: instance read failed for %s: %s",
            instance_id[:8],
            exc,
        )
        return OUTCOME_ERROR
    if row is None:
        return OUTCOME_MISSING

    status = getattr(row, "status", None)
    outcome = OUTCOME_ALREADY_RUNNING
    if status == "paused":
        try:
            # Guarded status flip — a concurrent resume makes this a
            # no-op (``transition_status_if`` returns None when the
            # precondition no longer holds).
            repo.transition_status_if(
                instance_id, "running", ("paused",)
            )
            repo.update(instance_id, paused_at=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "question_pause_set: un-pause write failed for %s: %s",
                instance_id[:8],
                exc,
            )
            return OUTCOME_ERROR
        outcome = OUTCOME_UNPAUSED
    elif status != "running":
        # Terminal / idle / waiting_children — not a parked instance.
        return OUTCOME_SKIPPED_STATUS

    # ── natural resume ────────────────────────────────────────────────
    # Two shapes, mirroring the answer path's own resume fan-out:
    #
    #   * the instance owns a PAUSED turn → ``resume_processing_job``
    #     routes it (PAUSED → PENDING) and re-arms the graph;
    #   * the instance owns NO live turn (the side-effect target case:
    #     the pause cancelled its graph task and there was no task row
    #     to suspend) → schedule a SILENT checkpoint resume so the
    #     interrupted turn can finish.
    #
    # Without the second branch the instance would sit ``running`` with
    # no work: its parent would never receive a terminal report and
    # would stay in ``waiting_children`` forever.
    task_repo = getattr(manager, "_task_repo", None)
    if task_repo is not None:
        paused_turn = None
        try:
            paused_turn = task_repo.find_paused_or_cancellable_turn(instance_id)
        except Exception as exc:  # noqa: BLE001 — ambiguity / query failure
            logger.warning(
                "question_pause_set: paused-turn probe failed for %s: %s",
                instance_id[:8],
                exc,
            )
        if paused_turn is not None and getattr(
            paused_turn, "status", None
        ) == "paused":
            resume_processing_job = getattr(
                manager, "resume_processing_job", None
            )
            if resume_processing_job is not None:
                try:
                    await resume_processing_job(
                        instance_id, message="resume", silent=True
                    )
                    logger.info(
                        "question_pause_set: paused turn resumed for %s "
                        "(reason=%s)",
                        instance_id[:8],
                        reason,
                    )
                    return OUTCOME_WAKE_SCHEDULED
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "question_pause_set: turn resume failed for %s: %s",
                        instance_id[:8],
                        exc,
                    )
        try:
            if task_repo.has_instance_busy(instance_id):
                # A PENDING/RUNNING task owns the turn — the worker pool
                # re-claims it (the cascade flipped PAUSED → PENDING).
                return outcome
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "question_pause_set: busy probe failed for %s: %s",
                instance_id[:8],
                exc,
            )

    try:
        has_graph_task = bool(
            (getattr(manager, "_graph_tasks", {}) or {}).get(instance_id)
        )
    except Exception:  # noqa: BLE001 — defensive
        has_graph_task = False
    if has_graph_task:
        return outcome

    schedule = getattr(manager, "_schedule_explicit_handle_resume", None)
    if schedule is None:
        logger.warning(
            "question_pause_set: no resume primitive available for %s "
            "(reason=%s) — un-paused without a wake",
            instance_id[:8],
            reason,
        )
        return outcome

    synthetic_wid = (
        f"question-pause-unpause-{instance_id[:8]}-{int(time.monotonic() * 1000)}"
    )
    try:
        resume = await schedule(
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
            "question_pause_set: wake failed for %s (reason=%s): %s",
            instance_id[:8],
            reason,
            exc,
        )
        return OUTCOME_ERROR

    if not isinstance(resume, dict) or resume.get("status") != "resuming":
        logger.info(
            "question_pause_set: wake refused for %s (reason=%s): %r",
            instance_id[:8],
            reason,
            resume,
        )
        return outcome
    logger.info(
        "question_pause_set: wake scheduled for %s (reason=%s)",
        instance_id[:8],
        reason,
    )
    return OUTCOME_WAKE_SCHEDULED


async def unpause_recorded_question_pauses(
    manager: "InstanceManager",
    asker_id: str,
    pack_id: str | None,
    *,
    reason: str = "answer",
) -> dict:
    """Un-pause exactly the recorded set for ``pack_id`` (R2).

    Idempotent and branch-independent: the caller may be the CAS winner
    OR the already-delivered no-op — the recorded set is the source of
    truth, never "was the pack answered".

    Returns a summary dict:
    ``{"recorded": [...], "outcomes": {id: outcome}, "unpaused": n,
    "woken": n, "skipped": n}``.
    """
    recorded = read_question_paused_set(manager, asker_id, pack_id)
    summary: dict[str, Any] = {
        "recorded": recorded,
        "outcomes": {},
        "unpaused": 0,
        "woken": 0,
        "skipped": 0,
    }
    if not recorded:
        return summary

    for instance_id in recorded:
        outcome = await unpause_instance_after_question_pause(
            manager, instance_id, reason=reason
        )
        summary["outcomes"][instance_id] = outcome
        if outcome == OUTCOME_UNPAUSED:
            summary["unpaused"] += 1
        elif outcome == OUTCOME_WAKE_SCHEDULED:
            summary["unpaused"] += 1
            summary["woken"] += 1
        else:
            summary["skipped"] += 1

    logger.info(
        "question_pause_set: un-pause pass (reason=%s) asker=%s "
        "pack=%s recorded=%d unpaused=%d woken=%d skipped=%d",
        reason,
        asker_id[:8],
        str(pack_id)[:8] if pack_id else "<none>",
        len(recorded),
        summary["unpaused"],
        summary["woken"],
        summary["skipped"],
    )
    return summary
