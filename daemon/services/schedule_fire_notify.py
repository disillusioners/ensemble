"""schedule-fire-notify — ONE never-raise entrypoint.

``notify_schedule_fire`` is the adapter-facing entrypoint. It performs
ALL field extraction (config + metadata) INSIDE its guarded body, applies
the identity gate (creator present AND creator != recipient), schedules
the inner worker via ``asyncio.create_task`` with a strong reference
(``warmup_pool.py`` mechanism precedent) + done-callback, and is
WHOLE-BODY guarded so nothing raises into ``_route_via_job_queue`` or
``_defer_delivery`` (A3/F2 — a leaked exception would flip FAILED → 5s
re-arm loop).

The inner worker builds the exact notice, runs the DEC-1 creator-status
pre-check (terminal/absent → drop+log+count; PAUSED → queue), and calls
``manager.enqueue_message_job`` with source ``system:schedule_fire_notify``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from daemon.constants import TERMINAL_INSTANCE_STATUSES
from daemon.services import schedule_fire_notify_metrics as _metrics

logger = logging.getLogger(__name__)

SOURCE: str = "system:schedule_fire_notify"

_TASKS: set[asyncio.Task] = set()

def _normalize_status(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = value.value
    return str(value).strip().lower()


async def _get_creator_row_async(manager: Any, creator_id: str) -> Any:
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        repo = getattr(manager, "instance_repository", None)
    if repo is None:
        return None
    get_fn = getattr(repo, "get", None)
    if get_fn is None:
        return None
    try:
        import inspect

        if inspect.iscoroutinefunction(get_fn):
            return await get_fn(creator_id)
        return await asyncio.to_thread(get_fn, creator_id)
    except Exception:  # noqa: BLE001
        return None


def _build_message(
    *,
    label: Any,
    fired_at: Any,
    target_agent: Any,
    instance_mode: Any,
    recurrence: Any,
    job_id: Any,
    message_id: Any,
    target_instance_id: Any,
) -> str:
    return (
        f"[schedule_fire_notify] "
        f"label={label} "
        f"fired_at={fired_at} "
        f"target_agent={target_agent} "
        f"instance_mode={instance_mode} "
        f"recurrence={recurrence} "
        f"job_id={job_id} "
        f"message_id={message_id} "
        f"target_instance_id={target_instance_id}"
    )


async def _worker(
    manager: Any,
    *,
    creator_instance_id: str | None,
    label: Any,
    fired_at: Any,
    target_agent: Any,
    instance_mode: Any,
    recurrence: Any,
    job_id: Any,
    message_id: Any,
    target_instance_id: Any,
) -> None:
    try:
        # DEC-1 creator-status pre-check (inside the off-path task).
        try:
            row = await _get_creator_row_async(manager, creator_instance_id)
        except Exception as exc:  # noqa: BLE001 — infra read failure, not absence
            logger.warning(
                "schedule_fire_notify: creator lookup failed for %s: %s",
                (creator_instance_id or "-")[:8],
                exc,
            )
            _metrics.record_dropped(
                instance_id=creator_instance_id,
                reason=f"creator_lookup_error:{type(exc).__name__}",
            )
            return
        status_str = (
            _normalize_status(getattr(row, "status", None)) if row is not None else ""
        )
        if row is None:
            _metrics.record_dropped(
                instance_id=creator_instance_id,
                reason="creator_absent",
            )
            logger.info(
                "schedule_fire_notify: creator %s absent -> dropped",
                (creator_instance_id or "-")[:8],
            )
            return
        if status_str in TERMINAL_INSTANCE_STATUSES:
            _metrics.record_dropped(
                instance_id=creator_instance_id,
                reason=f"creator_terminal:{status_str}",
            )
            logger.info(
                "schedule_fire_notify: creator %s terminal (%s) -> dropped",
                (creator_instance_id or "-")[:8],
                status_str,
            )
            return

        message = _build_message(
            label=label,
            fired_at=fired_at,
            target_agent=target_agent,
            instance_mode=instance_mode,
            recurrence=recurrence,
            job_id=job_id,
            message_id=message_id,
            target_instance_id=target_instance_id,
        )

        enqueue_fn = getattr(manager, "enqueue_message_job", None)
        if enqueue_fn is None:
            raise RuntimeError("manager has no enqueue_message_job")
        await enqueue_fn(
            instance_id=creator_instance_id,
            message=message,
            source=SOURCE,
            priority=0,
            metadata={"schedule_fire_notify": True},
        )
        _metrics.record_sent(instance_id=creator_instance_id, reason="delivered")
    except Exception as exc:  # noqa: BLE001 — never raise into fire path
        logger.warning(
            "schedule_fire_notify: coroutine failed for creator %s: %s",
            (creator_instance_id or "-")[:8],
            exc,
        )
        try:
            _metrics.record_dropped(
                instance_id=creator_instance_id,
                reason=f"coroutine_exception:{type(exc).__name__}",
            )
        except Exception:  # pragma: no cover
            pass


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


async def notify_schedule_fire(
    manager: Any,
    *,
    schedule_config: Any = None,
    metadata: Any = None,
    recipient_instance_id: Any = None,
    label: Any = None,
    target_agent: Any = None,
    instance_mode: Any = None,
    recurrence: Any = None,
    job_id: Any = None,
    message_id: Any = None,
    target_instance_id: Any = None,
) -> None:
    """Never-raise entrypoint. All extraction + identity gate + create_task."""
    creator_instance_id: Any = None
    try:
        creator_instance_id = _as_dict(schedule_config).get("creator_instance_id")
        if creator_instance_id is not None:
            creator_instance_id = str(creator_instance_id)

        sched_meta = _as_dict(_as_dict(metadata).get("scheduler"))
        fired_at = sched_meta.get("trigger_time")

        if hasattr(instance_mode, "value"):
            instance_mode = instance_mode.value
        if target_instance_id is None:
            target_instance_id = recipient_instance_id

        # Identity gate: absent key -> silent skip; self-alarm -> skip.
        if not creator_instance_id:
            return
        if (
            recipient_instance_id is not None
            and creator_instance_id == recipient_instance_id
        ):
            return

        coro = _worker(
            manager,
            creator_instance_id=creator_instance_id,
            label=label,
            fired_at=fired_at,
            target_agent=target_agent,
            instance_mode=instance_mode,
            recurrence=recurrence,
            job_id=job_id,
            message_id=message_id,
            target_instance_id=target_instance_id,
        )
        try:
            task = asyncio.create_task(coro)
        except Exception:
            # GREEN #1: a create_task failure would otherwise leave the
            # built coroutine un-awaited ("never awaited" warning + GC
            # leak). Close it explicitly before the drop-counter/log.
            coro.close()
            raise
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
    except Exception as exc:  # noqa: BLE001 — gate-time absorption
        logger.warning("schedule_fire_notify: gate-time failure: %s", exc)
        try:
            _metrics.record_dropped(
                instance_id=creator_instance_id,
                reason=f"gate_exception:{type(exc).__name__}",
            )
        except Exception:  # pragma: no cover
            pass
