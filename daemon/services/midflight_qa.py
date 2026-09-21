"""Mid-flight QA channel — shared emission helpers.

2026-09-21, ``feature/midflight-qa-channel`` (design
``.agents/shared/planning/midflight-qa-channel/design.md``).

This module is the single home for the QUESTION-EMISSION path shared by
three emitters:

* ``ask_questions`` (``daemon/tools/question_tools.py``) — steps [3]+[4]
  of design §4.1: EventBus ``QUESTION_REQUESTED`` + work_notifier
  fan-out over every live work_id (MAJOR-1), plus the durable
  ``instance_metadata`` shadow stamps (``question_pack_id`` /
  ``question_pack_payload``).
* ``mid_flight_report`` (``daemon/tools/midflight_report.py``) — the
  non-blocking ``MIDFLIGHT_REPORT`` emit-and-return.
* The wedge guard (``_pause_cascade_db_sync`` transition-time emission
  + ``HeartbeatEmitStuckProcessor``) — ``STUCK_AWAITING_ANSWER``
  emissions and the one-shot successor minting.

HARD CONSTRAINT — EVENT-DRIVEN ONLY: nothing in this module polls,
sleeps, or scans periodically. Every emission is triggered by a
transition (tool call, pause commit, one-shot Task claim). The
wedge-guard chain is a finite sequence of future-dated one-shot Task
rows (``next_retry_at``), never a loop.

Lane safety (v0.13.9 acceptance surface): every EventBus kind emitted
here is filtered out by ``JobFeedbackObserver._process_event`` (it
hard-filters ``event_type != "instance_lifecycle"``), and every
work_notifier status used here is NON-TERMINAL — the notifier's
non-terminal branch never claims the watcher row, so the eventual
terminal ``[JOB_EVENT]`` still fires with its ``Result:`` body intact.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from daemon.constants import STUCK_HEARTBEAT_AFTER_SECONDS
from daemon.repositories.event.models import EventKind
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.services.question_manager import pack_to_dict

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

# Instance statuses that make an answer target unrecoverable for the
# answer path (design §8.4 / leader decision 1). ERROR/FAILED are
# deliberately NOT here — those are the revive-and-deliver affordance.
ANSWER_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {InstanceStatus.COMPLETED.value, InstanceStatus.TERMINATED.value}
)

# Instance statuses that count as "live" for the child_count derivation
# (design §3.3 / approver item H6 — concrete derivation, no placeholder:
# child_count = number of DIRECT children of the asker whose instance
# status is NOT terminal).
_INSTANCE_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        InstanceStatus.COMPLETED.value,
        InstanceStatus.ERROR.value,
        InstanceStatus.TERMINATED.value,
        InstanceStatus.FAILED.value,
    }
)

# Metadata keys for the durable question-pack shadow (§5.4).
QUESTION_PACK_ID_METADATA_KEY = "question_pack_id"
QUESTION_PACK_PAYLOAD_METADATA_KEY = "question_pack_payload"


# =============================================================================
# Live work-id enumeration (MAJOR-1 fan-out)
# =============================================================================


def enumerate_live_work_ids(manager: "InstanceManager", instance_id: str) -> list[str]:
    """Enumerate the asker's live work_ids, newest-first, deduplicated.

    MAJOR-1 (review-carried, binding): the question is fanned out to
    EVERY work_id that currently maps to the asker. Without this, a
    turn-2+ question on a long-running job silently notifies a work_id
    nobody watches — that IS incident 1 recurring.

    Sources (verified APIs — no invented ``task.work_id_for``):

    * ``JobItemRepository.get_active_by_instance(instance_id)`` — the
      live JobItem (QUEUED or ACTIVE admission state; excludes terminal
      + soft-deleted). Fallback for multi-JobItem instances (revive
      races, manual DB ops): ``get_by_instance`` (most recent
      non-deleted, deterministic).
    * ``TaskRepository.get_by_instance(instance_id)`` — all tasks
      newest-first; the runtime's current Task is the newest
      non-terminal row (``status`` in pending/running/paused).

    Returns:
        Deduplicated list of live work_ids (newest activity first).
        May be empty (ad-hoc asker with no job/task backing — the
        question still rides EventBus + LiveEventHub).
    """
    work_ids: list[str] = []

    # ── JobItem side ──────────────────────────────────────────────────
    job_repo = None
    work_resolver = getattr(manager, "_work_resolver", None)
    if work_resolver is not None:
        job_repo = getattr(work_resolver, "_job_repo", None)
    if job_repo is not None:
        try:
            active_job = job_repo.get_active_by_instance(instance_id)
            if active_job is not None:
                work_ids.append(active_job.job_id)
            else:
                fallback_job = job_repo.get_by_instance(instance_id)
                if fallback_job is not None:
                    work_ids.append(fallback_job.job_id)
        except Exception as e:  # noqa: BLE001 — enumeration is best-effort
            logger.warning(
                "midflight_qa: JobItem enumeration failed for instance "
                "%s: %s",
                instance_id[:8] if instance_id else "<none>",
                e,
            )

    # ── Task side ─────────────────────────────────────────────────────
    task_repo = getattr(manager, "_task_repo", None)
    if task_repo is not None:
        try:
            tasks = task_repo.get_by_instance(instance_id) or []
            live_task_work_ids = [
                t.work_id
                for t in tasks
                if t.status
                in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value, TaskStatus.PAUSED.value)
            ]
            work_ids.extend(live_task_work_ids)
        except Exception as e:  # noqa: BLE001 — enumeration is best-effort
            logger.warning(
                "midflight_qa: Task enumeration failed for instance %s: %s",
                instance_id[:8] if instance_id else "<none>",
                e,
            )

    # Deduplicate, preserving first-seen (newest-first) order.
    seen: set[str] = set()
    ordered: list[str] = []
    for wid in work_ids:
        if wid and wid not in seen:
            seen.add(wid)
            ordered.append(wid)
    return ordered


def _asker_display_name(instance: Any) -> str:
    """Concrete asker_instance_name derivation (H6)."""
    if instance is None:
        return "unknown"
    title = getattr(instance, "title", None)
    if isinstance(title, str) and title:
        return title
    instance_name = (getattr(instance, "instance_metadata", None) or {}).get(
        "instance_name"
    )
    if isinstance(instance_name, str) and instance_name:
        return instance_name
    return getattr(instance, "agent_id", None) or "unknown"


def build_question_requested_payload(
    manager: "InstanceManager",
    instance_id: str,
    pack: Any,
) -> dict[str, Any]:
    """Build the ``QUESTION_REQUESTED`` payload per design §3.3 (job_id ARRAY)."""
    instance_repo = getattr(manager, "_instance_repository", None)
    instance = None
    parent_id: str | None = None
    mission_id: str | None = instance_id
    child_count = 0
    if instance_repo is not None:
        try:
            instance = instance_repo.get(instance_id)
            if instance is not None:
                parent_id = instance.parent_id
            root_id = instance_repo.get_tree_root_id(instance_id)
            if root_id:
                mission_id = root_id
            children = instance_repo.get_children(instance_id) or []
            child_count = sum(
                1
                for child in children
                if getattr(child, "status", None) not in _INSTANCE_TERMINAL_STATUSES
            )
        except Exception as e:  # noqa: BLE001 — payload enrichment is best-effort
            logger.warning(
                "midflight_qa: payload enrichment failed for instance %s: %s",
                instance_id[:8],
                e,
            )

    work_ids = enumerate_live_work_ids(manager, instance_id)

    return {
        "instance_id": instance_id,
        # ARRAY (MAJOR-1) — every live work_id that maps to the asker.
        "job_id": work_ids,
        "mission_id": mission_id,
        "parent_id": parent_id,
        "asker_agent_id": getattr(instance, "agent_id", None) if instance else None,
        "asker_instance_name": _asker_display_name(instance),
        "question_pack_id": getattr(pack, "id", None),
        "questions": [
            {
                "id": q.id,
                "text": q.text,
                "options": list(q.options),
                "allow_custom": q.allow_custom,
                "required": q.required,
            }
            for q in pack.questions
        ],
        "suspension_reason": "awaiting_answer",
        "paused_at": datetime.now(timezone.utc).isoformat(),
        "child_count": child_count,
        "fan_out_count": len(work_ids),
    }


def stamp_question_pack_metadata(manager: "InstanceManager", instance_id: str, pack: Any) -> None:
    """Write the durable question-pack shadow into ``instance_metadata``.

    §5.4 durability hook: ``question_pack_id`` + ``question_pack_payload``
    (= ``pack_to_dict(pack)``, the frozen FE schema). Uses the
    InstanceRepository's dialect-aware ``set_metadata_many`` — a single
    atomic JSONB UPDATE (MINOR-9), so concurrent metadata writes compose
    instead of clobbering. Cleared at ANSWER-CONSUMPTION time (R3),
    never here.
    """
    instance_repo = getattr(manager, "_instance_repository", None)
    if instance_repo is None:
        logger.debug(
            "midflight_qa: no instance_repository wired — skipping "
            "question-pack metadata stamp for %s",
            instance_id[:8],
        )
        return
    try:
        instance_repo.set_metadata_many(
            instance_id,
            {
                QUESTION_PACK_ID_METADATA_KEY: getattr(pack, "id", None),
                QUESTION_PACK_PAYLOAD_METADATA_KEY: pack_to_dict(pack),
            },
        )
    except Exception as e:  # noqa: BLE001 — stamp is best-effort durability
        logger.warning(
            "midflight_qa: question-pack metadata stamp failed for "
            "instance %s: %s",
            instance_id[:8],
            e,
        )


def clear_question_pack_metadata(manager: "InstanceManager", instance_id: str) -> None:
    """Clear the durable question-pack shadow (answer-consumption site, R3).

    Called by the shared answer helper on BOTH the CAS-win and the
    already-delivered no-op branch — the pack's durable lifetime ends
    at answer consumption regardless of which branch fired. Clearing
    at pack-CREATE time (the rejected alternative) would leave a
    stale-rehydration window where an answered pack is resurrected on
    boot.
    """
    instance_repo = getattr(manager, "_instance_repository", None)
    if instance_repo is None:
        return
    for key in (
        QUESTION_PACK_ID_METADATA_KEY,
        QUESTION_PACK_PAYLOAD_METADATA_KEY,
    ):
        try:
            instance_repo.delete_metadata(instance_id, key)
        except Exception as e:  # noqa: BLE001 — clear is best-effort
            logger.warning(
                "midflight_qa: question-pack metadata clear failed for "
                "instance %s key=%s: %s",
                instance_id[:8],
                key,
                e,
            )


# =============================================================================
# Emission fan-out (the four push lanes — all push, zero polling)
# =============================================================================


async def emit_question_requested(
    manager: "InstanceManager",
    instance_id: str,
    pack: Any,
) -> int:
    """Emit ``QUESTION_REQUESTED`` on the load-bearing lanes (design §4.1 steps 3+4).

    Lanes (in order, BEFORE the pause flag is set — the pause cascade
    cancels the graph task and any post-pause tool-side code is moot):

    1. ``EventBus.create_event`` — persists to the ``event`` table +
       broadcasts to global subscribers.
    2. ``notify_work_watchers`` per live work_id — the ``[JOB_EVENT]
       Job {work_id}... question requested ❓`` line into every
       watcher's instance, with the pack payload as the ``Result:``-
       style body (``result_summary=pack_to_dict(pack)``). Non-terminal
       → watcher rows are PRESERVED (no CAS claim).

    Returns the total number of watcher notifications delivered.
    """
    from daemon.services.work_notifier import notify_work_watchers

    payload = build_question_requested_payload(manager, instance_id, pack)
    work_ids: list[str] = list(payload["job_id"])

    event_bus = getattr(manager, "_event_bus", None)
    if event_bus is not None:
        try:
            await event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.QUESTION_REQUESTED,
                data=payload,
            )
        except Exception as e:  # noqa: BLE001 — §8.6 emission must not break the asker
            logger.warning(
                "midflight_qa: QUESTION_REQUESTED EventBus emission failed "
                "for instance %s: %s",
                instance_id[:8],
                e,
            )

    notified = 0
    for work_id in work_ids:
        try:
            notified += await notify_work_watchers(
                work_id=work_id,
                status="question_requested",
                instance_manager=manager,
                work_resolver=getattr(manager, "_work_resolver", None),
                watcher_repo=getattr(manager, "_watcher_repo", None),
                progress=None,
                result_summary=pack_to_dict(pack),
            )
        except Exception as e:  # noqa: BLE001 — §8.6
            logger.warning(
                "midflight_qa: question_requested work_notifier fan-out "
                "failed for work_id=%s instance=%s: %s",
                work_id[:8] if work_id else "<none>",
                instance_id[:8],
                e,
            )
    return notified


async def emit_midflight_report(
    manager: "InstanceManager",
    instance_id: str,
    summary: str,
    details: str,
    level: str,
    decision_required: bool,
) -> int:
    """Emit ``MIDFLIGHT_REPORT`` — non-blocking, no pause flag (design §4.2).

    Lanes: EventBus → LiveEventHub banner → work_notifier
    (``status="midflight_report"``, non-terminal) per live work_id.
    """
    import uuid as _uuid

    from daemon.services.work_notifier import notify_work_watchers

    work_ids = enumerate_live_work_ids(manager, instance_id)
    payload: dict[str, Any] = {
        "instance_id": instance_id,
        "job_id": work_ids,
        "report_id": str(_uuid.uuid4()),
        "summary": summary,
        "details": details,
        "level": level,
        "paused": False,  # explicitly false — reports never pause
        "tools_used": [],  # best-effort; not derivable at this seam
        "decision_required": decision_required,
    }

    event_bus = getattr(manager, "_event_bus", None)
    if event_bus is not None:
        try:
            await event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.MIDFLIGHT_REPORT,
                data=payload,
            )
        except Exception as e:  # noqa: BLE001 — §8.6
            logger.warning(
                "midflight_qa: MIDFLIGHT_REPORT EventBus emission failed "
                "for instance %s: %s",
                instance_id[:8],
                e,
            )

    live_hub = getattr(manager, "_live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_midflight_report(instance_id, payload)
        except Exception as e:  # noqa: BLE001 — §8.6
            logger.warning(
                "midflight_qa: MIDFLIGHT_REPORT SSE emission failed for "
                "instance %s: %s",
                instance_id[:8],
                e,
            )

    notified = 0
    for work_id in work_ids:
        try:
            notified += await notify_work_watchers(
                work_id=work_id,
                status="midflight_report",
                instance_manager=manager,
                work_resolver=getattr(manager, "_work_resolver", None),
                watcher_repo=getattr(manager, "_watcher_repo", None),
                progress=summary,
                result_summary=summary,
            )
        except Exception as e:  # noqa: BLE001 — §8.6
            logger.warning(
                "midflight_qa: midflight_report work_notifier fan-out "
                "failed for work_id=%s instance=%s: %s",
                work_id[:8] if work_id else "<none>",
                instance_id[:8],
                e,
            )
    return notified


# =============================================================================
# Wedge-guard one-shot chain (design §4.3 — finite, no polling)
# =============================================================================


def compute_wedge_chain(manager: "InstanceManager", asker_instance_id: str) -> list[str]:
    """Compute the wedge chain: paused ANCESTORS of the asker, root-first.

    Design §3.3: ``wedge_chain`` is the ancestry of paused instances
    from the root down toward the asker. An EMPTY list means the asker
    IS the wedge root (the orchestrator should treat it as the actual
    question target). Only ancestors whose instance status is PAUSED
    are included — a running ancestor is not part of the wedge.

    One ``get_parent`` walk per ancestor (bounded by tree depth; adds
    ONE cheap DB read per emission per §8.7).
    """
    instance_repo = getattr(manager, "_instance_repository", None)
    if instance_repo is None:
        return []
    chain: list[str] = []
    try:
        parent = instance_repo.get_parent(asker_instance_id)
        hops = 0
        while parent is not None and hops < 32:
            if getattr(parent, "status", None) == InstanceStatus.PAUSED.value:
                chain.append(parent.instance_id)
            parent = instance_repo.get_parent(parent.instance_id)
            hops += 1
    except Exception as e:  # noqa: BLE001 — chain enrichment is best-effort
        logger.warning(
            "midflight_qa: wedge-chain computation failed for asker %s: %s",
            asker_instance_id[:8],
            e,
        )
    chain.reverse()  # root-first
    return chain


def derive_emission_index(
    manager: "InstanceManager", asker_instance_id: str, question_pack_id: str | None
) -> int:
    """Derive the wedge-guard emission index from persisted event history.

    Design §4.3: ``emission_index`` derives from the count of prior
    ``stuck_awaiting_answer`` event rows for this ``question_pack_id``
    (EventBus persists BEFORE broadcast, so the count is durable and
    restart-safe; it also survives StaleTaskRecovery's retry-child
    minting which drops unknown Task columns). The current emission's
    index is ``prior_count + 1``.
    """
    event_repo = getattr(manager, "_event_repo", None)
    if event_repo is None or not question_pack_id:
        return 1
    try:
        prior = event_repo.count_kind_for_instance_matching(
            instance_id=asker_instance_id,
            kind=EventKind.STUCK_AWAITING_ANSWER.value,
            data_like=question_pack_id,
        )
        return int(prior) + 1
    except Exception as e:  # noqa: BLE001 — derivation is best-effort
        logger.warning(
            "midflight_qa: emission-index derivation failed for asker %s: %s",
            asker_instance_id[:8],
            e,
        )
        return 1


def read_question_pack_id_from_metadata(
    manager: "InstanceManager", instance_id: str
) -> str | None:
    """Read the durable ``question_pack_id`` shadow for ``instance_id``."""
    instance_repo = getattr(manager, "_instance_repository", None)
    if instance_repo is None:
        return None
    try:
        value = instance_repo.get_metadata_value(
            instance_id, QUESTION_PACK_ID_METADATA_KEY
        )
        return value if isinstance(value, str) and value else None
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "midflight_qa: question_pack_id metadata read failed for %s: %s",
            instance_id[:8],
            e,
        )
        return None


async def emit_stuck_awaiting_answer(
    manager: "InstanceManager",
    asker_instance_id: str,
    question_pack_id: str | None,
    *,
    waiting_for_seconds: int,
    paused_at: str | None = None,
) -> tuple[int, int]:
    """Emit ``STUCK_AWAITING_ANSWER`` (transition-time or one-shot wake).

    Lanes: EventBus → LiveEventHub banner → work_notifier
    (``status="stuck_awaiting_answer"``, non-terminal — watcher rows
    preserved) per live work_id. The ``emission_index`` is derived from
    persisted event history inside this helper and stamped on the
    payload so consumers can distinguish reminder cadence.

    Returns ``(emission_index, watchers_notified)``.
    """
    from daemon.services.work_notifier import notify_work_watchers

    emission_index = derive_emission_index(manager, asker_instance_id, question_pack_id)
    work_ids = enumerate_live_work_ids(manager, asker_instance_id)
    wedge_chain = compute_wedge_chain(manager, asker_instance_id)
    payload: dict[str, Any] = {
        "instance_id": asker_instance_id,
        "job_id": work_ids,
        "question_pack_id": question_pack_id,
        "paused_at": paused_at,
        "waiting_for_seconds": waiting_for_seconds,
        "emission_index": emission_index,
        "wedge_chain": wedge_chain,
    }

    event_bus = getattr(manager, "_event_bus", None)
    if event_bus is not None:
        try:
            await event_bus.create_event(
                instance_id=asker_instance_id,
                kind=EventKind.STUCK_AWAITING_ANSWER,
                data=payload,
            )
        except Exception as e:  # noqa: BLE001 — §8.6
            logger.warning(
                "midflight_qa: STUCK_AWAITING_ANSWER EventBus emission failed "
                "for asker %s: %s",
                asker_instance_id[:8],
                e,
            )

    live_hub = getattr(manager, "_live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_stuck_awaiting_answer(asker_instance_id, payload)
        except Exception as e:  # noqa: BLE001 — §8.6
            logger.warning(
                "midflight_qa: STUCK_AWAITING_ANSWER SSE emission failed for "
                "asker %s: %s",
                asker_instance_id[:8],
                e,
            )

    notified = 0
    for work_id in work_ids:
        try:
            notified += await notify_work_watchers(
                work_id=work_id,
                status="stuck_awaiting_answer",
                instance_manager=manager,
                work_resolver=getattr(manager, "_work_resolver", None),
                watcher_repo=getattr(manager, "_watcher_repo", None),
                progress=(
                    f"paused awaiting answer for {waiting_for_seconds}s "
                    f"(emission {emission_index})"
                ),
            )
        except Exception as e:  # noqa: BLE001 — §8.6
            logger.warning(
                "midflight_qa: stuck_awaiting_answer fan-out failed for "
                "work_id=%s asker=%s: %s",
                work_id[:8] if work_id else "<none>",
                asker_instance_id[:8],
                e,
            )
    return emission_index, notified


async def emit_child_question_still_pending(
    manager: "InstanceManager",
    child_instance_id: str,
    question_pack_id: str | None,
    *,
    parent_id: str | None = None,
) -> int:
    """Emit ``CHILD_QUESTION_STILL_PENDING`` (OQ-2 decision B, §8.3).

    Fired post-commit in ``_resume_cascade_db_sync`` for each resumed
    instance whose own question pack is still ``pending`` — the
    cascade's ResumeTurn wiped its ``awaiting_answer`` handle while
    the pack stays pending in RAM. Informational only — NO pause.
    Lanes: EventBus + LiveEventHub banner (this kind deliberately
    NEVER enters the work_notifier status map — design R1).
    """
    work_ids = enumerate_live_work_ids(manager, child_instance_id)
    payload: dict[str, Any] = {
        "child_instance_id": child_instance_id,
        "child_job_id": work_ids,
        "question_pack_id": question_pack_id,
        "wedge_chain": [parent_id, child_instance_id] if parent_id else [child_instance_id],
        "paused_by_parent": True,
    }

    event_bus = getattr(manager, "_event_bus", None)
    if event_bus is not None:
        try:
            await event_bus.create_event(
                instance_id=child_instance_id,
                kind=EventKind.CHILD_QUESTION_STILL_PENDING,
                data=payload,
            )
        except Exception as e:  # noqa: BLE001 — §8.6
            logger.warning(
                "midflight_qa: CHILD_QUESTION_STILL_PENDING EventBus emission "
                "failed for child %s: %s",
                child_instance_id[:8],
                e,
            )

    live_hub = getattr(manager, "_live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_child_question_still_pending(
                child_instance_id, payload
            )
        except Exception as e:  # noqa: BLE001 — §8.6
            logger.warning(
                "midflight_qa: CHILD_QUESTION_STILL_PENDING SSE emission "
                "failed for child %s: %s",
                child_instance_id[:8],
                e,
            )
    return 0


async def emit_question_escalation_notification(
    manager: "InstanceManager",
    asker_instance_id: str,
    asker_agent_id: str | None,
    question_pack_id: str | None,
    emission_index: int,
) -> int:
    """Fan out the wedge escalation to ALL SSE clients (NotificationBroadcaster).

    Design §8.7: at ``emission_index=3`` the escalation fans out
    UNCONDITIONALLY to NotificationBroadcaster (parallel to
    ``emit_root_completion``) so operators see it even when no
    ``watch_job`` row survives. Fires ONLY at escalation — never on
    the normal question/report paths.
    """
    broadcaster = getattr(manager, "_notification_broadcaster", None)
    if broadcaster is None:
        return 0
    try:
        return await broadcaster.emit_question_escalation(
            instance_id=asker_instance_id,
            agent_id=asker_agent_id,
            question_pack_id=question_pack_id,
            emission_index=emission_index,
        )
    except Exception as e:  # noqa: BLE001 — MINOR-7: escalation emit failures are WARN-logged
        logger.warning(
            "midflight_qa: question escalation broadcast failed for asker=%s "
            "pack_id=%s emission_index=%s: %s",
            asker_instance_id[:8] if asker_instance_id else "<none>",
            question_pack_id,
            emission_index,
            e,
        )
        return 0


def mint_stuck_heartbeat_one_shot(
    manager: "InstanceManager",
    asker_instance_id: str,
    fire_after_seconds: int | None = None,
) -> Any | None:
    """Mint ONE future-dated ``heartbeat_emit_stuck`` Task row for the asker.

    The wedge-guard substrate: a single asker-bound Task row whose
    ``next_retry_at`` sits ahead of time. The row exists to observe the
    PAUSED asker, so the claim gate's pause exclusion is bypassed for
    this task type (type-scoped carve-out in ``claim_pending_task``,
    ``TaskRepository.create_one_shot_heartbeat``). The row is claimed
    exactly once by the atomic claim; wake latency ≤3s after
    ``next_retry_at`` via the existing condition-timeout claim loop.

    Not a loop: each call mints exactly one link; the processor's
    re-arm clause mints the next link only while ``emission_index < 3``
    and the wedge is still alive.

    Fix pass (MINOR-2/MINOR-3, 2026-09-21) — PENDING-row cap: the mint
    is SKIPPED when a PENDING ``heartbeat_emit_stuck`` row already
    exists for the asker, so an asker never carries more than one
    pending wedge-guard link. This closes two holes:

    * the re-ask stale link: a NEW pause-site mint while an old
      chain's link is still pending would leave two live chains; both
      fire, both read the CURRENT pack id from metadata, emissions
      double per interval → EARLY escalation (terminating the asker
      before the designed ~60 min);
    * finiteness: minting is idempotent-per-pending-row, bounding the
      chain even when ``derive_emission_index`` degrades to 1 on
      event-persistence failure (index never reaching the escalation
      threshold can no longer compound the row population).

    The cap deliberately matches status ``pending`` ONLY — a claimed
    (``running``) link does not count, so the re-arm site's successor
    mint (issued while the CURRENT link is still ``running``) is never
    self-blocked.
    """
    task_repo = getattr(manager, "_task_repo", None)
    if task_repo is None:
        return None
    try:
        if task_repo.has_pending_of_type_for_instance(
            asker_instance_id, TaskType.HEARTBEAT_EMIT_STUCK.value
        ):
            logger.info(
                "midflight_qa: PENDING wedge-guard row already exists for "
                "asker %s — skipping one-shot mint (MINOR-2/3 cap)",
                asker_instance_id[:8],
            )
            return None
    except Exception as e:  # noqa: BLE001 — cap check failure must not kill the chain
        logger.warning(
            "midflight_qa: PENDING-row cap check failed for asker %s "
            "(%s: %s) — fail-open, minting anyway",
            asker_instance_id[:8],
            type(e).__name__,
            e,
        )
    delay = (
        fire_after_seconds
        if fire_after_seconds is not None
        else STUCK_HEARTBEAT_AFTER_SECONDS
    )
    fire_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    try:
        return task_repo.create_one_shot_heartbeat(
            task_type=TaskType.HEARTBEAT_EMIT_STUCK.value,
            instance_id=asker_instance_id,
            next_retry_at=fire_at,
        )
    except Exception as e:  # noqa: BLE001 — mint is best-effort
        logger.warning(
            "midflight_qa: one-shot heartbeat mint failed for asker %s: %s",
            asker_instance_id[:8],
            e,
        )
        return None


__all__ = [
    "ANSWER_TERMINAL_STATUSES",
    "QUESTION_PACK_ID_METADATA_KEY",
    "QUESTION_PACK_PAYLOAD_METADATA_KEY",
    "build_question_requested_payload",
    "clear_question_pack_metadata",
    "compute_wedge_chain",
    "derive_emission_index",
    "emit_child_question_still_pending",
    "emit_midflight_report",
    "emit_question_escalation_notification",
    "emit_question_requested",
    "emit_stuck_awaiting_answer",
    "enumerate_live_work_ids",
    "mint_stuck_heartbeat_one_shot",
    "read_question_pack_id_from_metadata",
    "stamp_question_pack_metadata",
]
