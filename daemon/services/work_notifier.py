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
    result_summary: str | None = None,
) -> int:
    """Notify watchers that ``work_id`` has reached ``status``.

    Kind-agnostic — looks up the work via ``work_resolver.resolve_work``
    so the same function serves both Task-originated and
    JobItem-originated terminal events.

    Delivery contract:
        * **Exactly-once on success, at-least-once on failure.**
          Watchers are deleted only AFTER all notifications are
          successfully delivered. If ``resolve_work`` fails (work
          gone) or any ``enqueue_message`` throws, watcher rows
          remain in place for ``reconcile_terminal_watches`` cleanup
          at next startup — the watching instance is never
          permanently un-notified.

    Ordering (resolve → notify → claim):
        1. **Resolve FIRST.** ``work_resolver.resolve_work`` runs
           before any watcher fetch. If the work is gone, return 0
           early — watchers stay in place for reconcile cleanup.
        2. **Read-only fetch watchers.** ``get_watchers_for_job``
           (SELECT — no DELETE) returns the rows. No DELETE at this
           point: a notification failure in step 3 must not silently
           drop the watch.
        3. **Notify each watcher.** ``enqueue_message`` delivers
           the notification. Any exception propagates to the outer
           ``except`` (returns 0) — watcher rows still exist because
           step 4 has not run.
        4. **CLAIM (delete) ONLY AFTER successful notify.** For
           terminal statuses with ``notified > 0``,
           ``claim_watchers_for_job`` atomically deletes the watcher
           rows. For non-terminal statuses, watchers are NEVER
           claimed — they stay in place so the eventual terminal
           notification can still reach them.

    Exactly-once on the happy path is preserved by the REPO-LEVEL
    non-None gating: the atomic ``WHERE status=running`` guard on
    ``complete_task`` / ``fail_task`` / ``cancel_task`` returns a
    non-None row only for the caller that won the status transition
    race. That caller is the only one that calls this function, so
    claim-after-notify is still exactly-once under the normal path.
    The reorder makes failure paths safe (watchers survive for
    reconcile) without weakening the happy path.

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
            ``get_watchers_for_job`` performs the read-only lookup
            (always used) and whose ``claim_watchers_for_job``
            performs the post-notify atomic delete (terminal
            statuses only — runs only when ``notified > 0``).
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
        # Step 1: Resolve FIRST. If the work record is gone (deleted,
        # purged, etc.) return 0 and leave the watcher rows in place
        # — ``reconcile_terminal_watches`` will pick them up at next
        # startup. Previously resolve_work ran AFTER the claim/delete
        # which meant a missing work record returned 0 anyway but
        # silently dropped the watch (no reconcile path because the
        # row was already deleted).
        work_record = await asyncio.to_thread(
            work_resolver.resolve_work, work_id
        )
        if work_record is None:
            logger.debug(
                "notify_work_watchers: work_id=%s no longer resolvable "
                "— leaving watchers in place for reconcile cleanup",
                work_id[:8] if work_id else "<none>",
            )
            return 0

        # Step 2: Read-only fetch watchers. We deliberately use the
        # SELECT path (``get_watchers_for_job``) and NOT the
        # claim-and-delete path here — the atomic DELETE moves to
        # step 4 below so a notification failure in step 3 cannot
        # silently drop the watch (which would leave the watching
        # instance permanently un-notified). Wrapped in
        # ``asyncio.to_thread`` so SQLite WAL contention cannot block
        # the event loop.
        watchers = await asyncio.to_thread(
            watcher_repo.get_watchers_for_job, work_id
        )
        if not watchers:
            return 0

        agent_id = work_record.agent_id or "unknown"
        # Prefer the caller-supplied result_summary/error (the terminal
        # writer — e.g. JobFeedbackObserver — already pre-fetched the
        # instance's final assistant message via
        # ``_get_last_assistant_message_raw``). The resolver returns
        # ``None`` for job-kind WorkRecords (Phase 5 dropped the
        # ``JobItem.result_summary`` mirror column and never replaced
        # it with an Instance read), so without this override the
        # ``[JOB_EVENT] completed`` body omits the ``Result:`` block.
        effective_result = (
            result_summary if result_summary is not None
            else work_record.result_summary
        )
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
                if effective_result:
                    notification_parts.append(f"  Result:\n{effective_result}")
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

        # Step 4: CLAIM (delete) watchers ONLY AFTER successful notify.
        #
        # Terminal statuses (``completed`` / ``failed`` /
        # ``cancelled`` / ``dead_letter``): if at least one watcher
        # was notified, atomically claim (DELETE ... RETURNING) all
        # watcher rows for this work_id. This is the
        # exactly-once-on-success invariant — the only caller that
        # reaches this function is the one that won the atomic
        # ``WHERE status=running`` guard on ``complete_task`` /
        # ``fail_task`` / ``cancel_task`` (the repo-level non-None
        # gating), so claim-after-notify does not weaken exactly-once.
        # On failure (notified == 0 because every watcher filtered the
        # event out, or because enqueue_message threw), the claim is
        # skipped and the rows remain for reconcile cleanup.
        #
        # Non-terminal statuses (``in_progress`` etc.): NEVER claim —
        # the watcher must stay registered so the eventual terminal
        # notification can still reach it. The earlier implementation
        # unconditionally claimed (deleted) all watchers on every
        # status, which broke progress tracking by silently dropping
        # the watch before the terminal event fired.
        if _is_terminal(status):
            if notified > 0:
                await asyncio.to_thread(
                    watcher_repo.claim_watchers_for_job, work_id
                )
        else:
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
