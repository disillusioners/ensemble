"""Centralized work-notification helper for the virtual job surface.

Phase 2 (Batch 2) of ``feature/virtual-job-management-surface``:
single kind-agnostic function that fires watcher notifications for ANY
work unit (Task or JobItem) that has reached a terminal (or
``in_progress``) state.

## Why this exists

Before Phase 2 Batch 2, watcher notifications were only emitted from
the job-side path (``JobQueueService.notify_watchers``). The seven
task-terminal sites (``worker_pool._handle_cancellation`` x3,
``worker_pool._handle_task_failure``, ``stale_task_recovery.recover_*``
x4, ``task_processor.on_success``, ``manager._resume_processing_background``
failure path) did NOT fire notifications at all, and the existing
``notify_watchers`` had two race-prone patterns:

* **TOCTOU read/delete** — ``get_watchers_for_job`` then
  ``remove_all_watches_for_job`` is not atomic. Two concurrent terminal
  callers (e.g. ``stale_task_recovery.fail_task`` racing with
  ``worker_pool.complete_task``) can both notify the same watcher
  (double-notify) before either deletes the row.
* **JobItem-only data fetch** — ``self._repository.get(job_id)``
  returns a ``JobItem`` so the notification builder can only reach the
  fields JobItem carries (``agent_id``, ``result_summary``). A Task
  terminal notification would crash on attribute access because Task
  has neither column (those fields live on the Instance for Tasks).

This module centralises the notification behind one function that:

1. Uses ``watcher_repo.claim_watchers_for_job`` (DELETE...RETURNING) as
   the natural serialization point — two concurrent callers cannot
   both receive the same watcher row, so notifications fire exactly
   once per watcher per terminal event.
2. Resolves the ``work_id`` through the ``WorkResolverService`` so the
   same code path serves both ``task`` and ``job`` work — the resolver
   pulls ``agent_id`` from the matching Instance (Task side) or the
   ``JobItem`` itself, and ``result_summary`` / ``error`` from the
   per-table representation.
3. Gating is done at the call site: the helper is invoked ONLY when the
   atomic terminal repo method (``complete_task`` / ``fail_task`` /
   ``cancel_task``) returned a non-None row, proving the caller won
   the status-guard race.

## Format contract (DO NOT CHANGE)

The notification body must remain byte-for-byte identical to the
existing ``JobQueueService.notify_watchers`` format because the
orchestrator's parsing contract in
``agents/job-orchestration/skill.md`` keys off the
``[JOB_EVENT]`` prefix and the trailing icon glyphs:

.. code-block:: text

    [JOB_EVENT] Job {work_id[:8]}... {status_display}
      Agent: {agent_id}
      Result: {result_summary}

(Or ``Error: {error}`` for failed events, ``Progress:`` for
``in_progress`` events.) ``status_display`` mapping:

* ``completed``  → ``"completed ✓"``
* ``failed``     → ``"failed ✗"``
* ``in_progress`` → ``"in progress ⟳"``
* ``paused``     → ``"paused ⏸"``
* anything else  → status string verbatim (identity)

The ``source`` parameter on ``enqueue_message`` is fixed at
``f"internal_agent:job_event:{work_id}:{status}"`` — the orchestrator
treats the ``job_event`` tag as the trigger to look up the work
record again from its own side.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from daemon.repositories.job_queue.watcher_models import ALL_TERMINAL_STATES
from daemon.services.work_status import is_terminal as _is_terminal

if TYPE_CHECKING:
    from daemon.services.work_resolver import WorkResolverService

logger = logging.getLogger(__name__)


# Status display mapping — must stay byte-for-byte identical to the
# JobQueueService.notify_watchers implementation (orchestrator's
# parser contract).
_STATUS_DISPLAY_MAP: dict[str, str] = {
    "completed": "completed ✓",
    "failed": "failed ✗",
    "in_progress": "in progress ⟳",
    "paused": "paused ⏸",
}


def _format_status_display(status: str) -> str:
    """Return the user-facing status string with icon.

    Unknown statuses pass through unchanged (the prior behaviour).
    """
    return _STATUS_DISPLAY_MAP.get(status, status)


async def notify_work_watchers(
    work_id: str,
    status: str,
    error: str | None = None,
    *,
    instance_manager: Any,
    work_resolver: "WorkResolverService",
    watcher_repo: Any,
    progress: str | None = None,
) -> int:
    """Notify watchers that ``work_id`` has reached ``status``.

    Kind-agnostic — looks up the work via ``work_resolver.resolve_work``
    so the same function serves both Task-originated and
    JobItem-originated terminal events.

    Atomicity (terminal statuses): uses
    ``watcher_repo.claim_watchers_for_job`` (a single
    ``DELETE ... RETURNING *``) so concurrent callers cannot both
    receive the same watcher row. The caller that wins the
    ``status=running`` SQL guard on ``complete_task``/``fail_task``/
    ``cancel_task`` is the caller that calls this function — the repo
    returning non-None is the only serialization token we need.

    Watcher preservation (non-terminal statuses): for
    ``in_progress`` and other non-terminal statuses, the read-only
    ``get_watchers_for_job`` is used instead — the watcher row stays
    in place so the eventual terminal notification can still reach
    it. Previously the claim-and-delete ran unconditionally on every
    status, which permanently removed the watch the moment the first
    progress update fired and left the watching instance unable to
    receive the final terminal event.

    Args:
        work_id: The stable cross-system UUID4 (``Task.work_id`` or
            ``JobItem.job_id`` — they share the same column).
        status: Canonical status (``"completed"``, ``"failed"``,
            ``"cancelled"``, ``"dead_letter"``, or ``"in_progress"``).
        error: Optional error string — included verbatim as the
            ``Error:`` line for ``failed`` notifications.
        instance_manager: The ``InstanceManager`` whose
            ``enqueue_message`` delivers the notification to each
            watcher's instance queue.
        work_resolver: The ``WorkResolverService`` used to resolve
            ``work_id`` to a ``WorkRecord`` (provides ``agent_id``,
            ``result_summary``, ``error``).
        watcher_repo: The ``JobWatcherRepository`` whose
            ``claim_watchers_for_job`` performs the atomic claim-and-
            delete (used only for terminal statuses) and whose
            ``get_watchers_for_job`` performs the read-only lookup
            (used for non-terminal statuses).
        progress: Optional progress payload for ``in_progress``
            notifications — rendered as the ``  Progress:\n{progress}``
            line that ``JobFeedbackObserver._emit_in_progress`` passes
            in. Ignored for non-``in_progress`` statuses.

    Returns:
        Number of watchers notified (zero is a valid no-op if no
        watchers exist, were already claimed, or work cannot be
        resolved).
    """
    # Defensive: a test or a future wiring bug could call us with the
    # dependencies not yet attached. Returning 0 (the same no-op the
    # caller would see without any wiring) is safer than raising and
    # blocking the work-terminal write that already succeeded.
    if instance_manager is None or work_resolver is None or watcher_repo is None:
        logger.debug(
            "notify_work_watchers: missing dependency for work_id=%s "
            "status=%s — skipping (instance_manager=%s, work_resolver=%s, "
            "watcher_repo=%s)",
            work_id[:8] if work_id else "<none>",
            status,
            instance_manager is not None,
            work_resolver is not None,
            watcher_repo is not None,
        )
        return 0

    try:
        # Atomic claim-and-delete for terminal statuses (``completed``,
        # ``failed``, ``cancelled``, ``dead_letter``). Two concurrent
        # callers cannot both receive the same watcher row, so
        # notifications fire exactly once per watcher per terminal
        # event.
        #
        # For non-terminal statuses (e.g. ``in_progress``) we use the
        # read-only ``get_watchers_for_job`` instead — the watcher must
        # stay registered so the eventual terminal notification can
        # still reach it. Without this branch, the
        # ``job_feedback_observer._emit_in_progress`` path would
        # permanently delete the watcher, and the subsequent terminal
        # event would find no watcher rows to notify.
        #
        # Wrapped in ``asyncio.to_thread`` so SQLite WAL contention
        # cannot block the event loop (matches the existing
        # ``notify_watchers`` pattern).
        if _is_terminal(status):
            watchers = await asyncio.to_thread(
                watcher_repo.claim_watchers_for_job, work_id
            )
        else:
            watchers = await asyncio.to_thread(
                watcher_repo.get_watchers_for_job, work_id
            )
        if not watchers:
            return 0

        # Resolve the work record for the notification payload. The
        # resolver looks up Task first then JobItem — for terminal
        # notifications fired from the task terminal sites the Task
        # branch is hit; for the existing job-side notification callers
        # the JobItem branch is hit. Either way, the WorkRecord gives
        # us a uniform ``agent_id`` / ``result_summary`` / ``error``
        # triple.
        work_record = await asyncio.to_thread(
            work_resolver.resolve_work, work_id
        )
        if work_record is None:
            # Work was deleted between the watcher fetch and the
            # notification — nothing to say. For terminal statuses
            # the watchers were already claimed (and deleted) above;
            # for non-terminal statuses they were read-only and
            # remain in place until a future event triggers cleanup.
            # Either way there is nothing left to notify, so 0 is
            # the correct return.
            return 0

        agent_id = work_record.agent_id or "unknown"
        result_summary = work_record.result_summary
        # Prefer the caller-supplied error for ``failed`` notifications
        # (this is the most-recent failure reason, including the
        # caller-context like "max retries exceeded"). Fall back to
        # ``WorkRecord.error`` if no caller error is provided — this
        # keeps the existing ``notify_watchers`` behaviour where the
        # JobItem's ``error_message`` flows through.
        effective_error = error if error is not None else work_record.error

        status_display = _format_status_display(status)

        notified = 0
        for watcher in watchers:
            # Filter by the watcher's subscribed events. The watcher's
            # ``watch_events`` is the JSONB list populated at
            # ``add_watch`` time and defaults to ``ALL_WATCHABLE_EVENTS``.
            if status not in watcher.watch_events:
                continue

            notification_parts = [
                f"[JOB_EVENT] Job {work_id[:8]}... {status_display}",
                f"  Agent: {agent_id}",
            ]

            if status == "in_progress":
                if progress:
                    notification_parts.append(f"  Progress:\n{progress}")
            else:
                if result_summary:
                    notification_parts.append(f"  Result:\n{result_summary}")
                if effective_error:
                    notification_parts.append(f"  Error: {effective_error}")

            notification = "\n".join(notification_parts)

            # ``enqueue_message`` is async — call it directly since we
            # are already on the event loop. The watcher's instance
            # may not be running; ``enqueue_message`` queues the
            # message in the DB for later delivery in that case.
            await instance_manager.enqueue_message(
                instance_id=watcher.instance_id,
                message=notification,
                source=f"internal_agent:job_event:{work_id}:{status}",
            )
            notified += 1

        # NOTE: for terminal statuses, ``claim_watchers_for_job``
        # already deleted the watcher rows in the same atomic
        # statement, so no follow-up ``remove_all_watches_for_job`` is
        # needed. This is the race-free cleanup — the previous
        # ``notify_watchers`` used a separate
        # ``remove_all_watches_for_job`` call AFTER the notify loop,
        # which could leave rows visible to a concurrent caller in the
        # window between the SELECT and the DELETE.
        #
        # Non-terminal statuses (e.g. ``in_progress``) take the
        # read-only ``get_watchers_for_job`` branch above — the
        # watcher is NOT deleted so the watching instance will still
        # receive the eventual terminal notification. The earlier
        # implementation unconditionally claimed (deleted) all
        # watchers on every status, which broke progress tracking by
        # silently dropping the watch before the terminal event fired.
        if status not in ALL_TERMINAL_STATES:
            logger.debug(
                "notify_work_watchers: non-terminal status=%s for "
                "work_id=%s — watcher rows preserved (read-only), "
                "terminal notification will still fire",
                status,
                work_id[:8],
            )

        return notified

    except Exception as e:
        # Notification is best-effort. The terminal write already
        # succeeded; the worst-case outcome of a failed notify is
        # that the watcher misses the event, which the next manual
        # ``reconcile_terminal_watches`` sweep will pick up at next
        # startup. Log at warning so operators can spot systemic
        # issues without crashing the worker thread.
        logger.warning(
            "notify_work_watchers: failed to notify watchers for "
            "work_id=%s status=%s: %s",
            work_id[:8] if work_id else "<none>",
            status,
            e,
        )
        return 0


__all__ = ["notify_work_watchers"]
