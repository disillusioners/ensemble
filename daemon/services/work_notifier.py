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

1. Uses ``watcher_repo.claim_watchers_for_job_for_instances``
   (DELETE...RETURNING scoped to the matching instance_id subset) as
   the natural serialization point — invoked CLAIM-FIRST before any
   per-watcher ``enqueue_message``, so two concurrent terminal
   callers cannot both deliver for the same watcher row.
   Notifications therefore fire exactly once per watcher per
   terminal event (N1 fix, 2026-09-03). The held-for-mission rows
   (``mission_terminal`` opt-in with non-terminal mission liveness)
   are excluded from the claim's WHERE clause and survive in the
   DB for the future terminal event.
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
* ``settled``    → ``"settled ✓"`` (M3 mission-class — mirror rows
  carry ``settled``; the transport-receipt terminal is disjoint from
  ``completed`` which is reserved for task rows / the work-outcome
  vocabulary)
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
    "settled": "settled ✓",
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

    Ordering (resolve → partition → claim-first → notify):

        1. **Resolve FIRST.** ``work_resolver.resolve_work`` runs
           before any watcher fetch. If the work is gone, return 0
           early — watchers stay in place for reconcile cleanup.
        2. **Read-only fetch + in-memory partition.** All watchers
           are SELECTed (no DELETE). Each watcher is classified
           into one of two buckets in memory:

           * **matching** — the watcher subscribes to ``status``
             AND (for the ``mission_terminal`` opt-in branch) its
             mission liveness is itself terminal. This bucket
             WILL be notified.
           * **held** — the watcher opts in to ``mission_terminal``
             but its mission liveness is not yet terminal. The row
             is preserved in the DB for the future terminal event;
             it is NEVER claimed on this call.

        3. **CLAIM-FIRST for terminal statuses (N1 fix,
           2026-09-03).** When ``status`` is terminal AND the
           ``matching`` set is non-empty, the atomic
           ``DELETE ... RETURNING`` on
           ``watcher_repo.claim_watchers_for_job_for_instances``
           runs BEFORE any ``enqueue_message``. Only the rows the
           CAS returned are notified. Concurrent callers each
           partition independently; only the CAS winner(s) deliver,
           so the bounded ≤2 duplicate-delivery window the
           notify-then-claim ordering had is closed. The held
           (mission-not-terminal) rows are not in the CAS WHERE
           clause, so they survive untouched.
        4. **Notify ONLY the CAS winners.** The notify loop
           iterates the rows the claim returned. A row that was
           partitioned into ``matching`` but lost the CAS to a
           concurrent caller is NOT notified here — that caller's
           notify loop owns it. This is the exactly-once
           guarantee at the caller level (paired with the
           repo-level CAS).

        For **non-terminal** statuses (``in_progress`` etc.),
        ``claim`` is NEVER called: the notify loop iterates the
        ``matching`` bucket in read-only mode, and the watcher
        rows remain in the DB so the eventual terminal
        notification can still reach them. The earlier
        unconditional-claim implementation silently dropped these
        rows before the terminal event fired.

    Exactly-once invariant (post-N1):

        Two concurrent callers of ``notify_work_watchers`` for the
        same terminal ``work_id`` → exactly ONE ``[JOB_EVENT]``
        per watcher. The repo-level CAS on
        ``claim_watchers_for_job_for_instances`` is the only
        primitive that enforces this — the caller MUST invoke it
        BEFORE any ``enqueue_message``. Do not add new
        notify-then-claim call sites that bypass this helper.

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
            (always used) and whose
            ``claim_watchers_for_job_for_instances`` performs the
            CLAIM-FIRST atomic DELETE...RETURNING (terminal statuses
            only — scoped to the matching instance_id subset so
            held-for-mission rows survive). Non-terminal statuses
            never trigger a claim.
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
        # claim-and-delete path here — the per-instance atomic CAS
        # moves to step 3 below (claim-first for terminal statuses),
        # where the DELETE WHERE clause is scoped to the matching
        # instance_id subset so held-for-mission rows survive. A
        # claim-first ordering also closes the bounded ≤2
        # duplicate-delivery window between two concurrent terminal
        # callers (each SELECTed the same row and delivered before
        # either ran the DELETE in the pre-N1 flow). Wrapped in
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

        # Step 2 (N1 — 2026-09-03): in-memory PARTITION before any
        # notify or claim. The pre-N1 flow did a notify-then-claim
        # over the raw SELECT, which left a bounded ≤2
        # duplicate-delivery window between two concurrent terminal
        # callers (each SELECTed the same row and delivered before
        # either ran the DELETE).
        #
        # Here we split into two pure buckets — no side effects —
        # so the partition itself is race-free (every caller
        # computes the same set from the same SELECT snapshot):
        #   * matching         → will notify (atomic CAS in step 3
        #                        picks which caller(s) actually
        #                        deliver for each instance_id).
        #   * held_for_mission → ``mission_terminal`` opt-in with
        #                        mission liveness NOT yet terminal.
        #                        Rows stay in DB for the future
        #                        terminal event. Never claimed here.
        matching: list = []
        held_for_mission = 0
        for watcher in watchers:
            # Filter by the watcher's subscribed events. The watcher's
            # ``watch_events`` is the JSONB list populated at
            # ``add_watch`` time and defaults to ``ALL_WATCHABLE_EVENTS``.
            #
            # M2 (mission-class, 2026-09-02) — ``mission_terminal``
            # opt-in semantic (contract draft §3.5): a watcher that
            # subscribes to ``mission_terminal`` wants notification
            # on EVERY transport terminal event, gated by mission
            # liveness. The standard ``status not in watch_events``
            # check would otherwise miss every transport terminal
            # event (since ``status`` is a transport value, not
            # ``"mission_terminal"``). Treat the watcher as
            # "matched" on any transport terminal event when
            # ``mission_terminal`` is in its events list.
            standard_match = status in watcher.watch_events
            mission_terminal_opt_in = (
                "mission_terminal" in watcher.watch_events
            )
            if not standard_match and not mission_terminal_opt_in:
                continue

            # M2 (mission-class, 2026-09-02, ``feature/mission-class``)
            # — ``mission_terminal`` opt-in gating (contract draft
            # §3.5). When a watcher opts in via ``mission_terminal``
            # (added to ``watch_events`` at ``add_watch`` time), the
            # notification fires ONLY when both admission AND mission
            # liveness are terminal. The dual-terminal check uses
            # ``work_record.mission_liveness`` (canonical mission
            # vocabulary for mirror rows; ``None`` for task rows).
            if mission_terminal_opt_in:
                # Task row: ``mission_liveness`` is intentionally
                # ``None`` by Fix C split-semantics design — the row
                # IS its own mission. Use ``work_record.status`` as
                # the dual-terminal check for task rows.
                job_type = getattr(work_record, "job_type", None)
                if job_type == "message":
                    mission_live = getattr(
                        work_record, "mission_liveness", None
                    )
                else:
                    mission_live = getattr(work_record, "status", None)
                if mission_live not in {"completed", "failed", "cancelled"}:
                    # Mission not yet terminal — keep the watch alive
                    # for the future terminal event. Skip this
                    # notification; the watcher row stays in place
                    # (NOT in the step-3 claim WHERE clause).
                    held_for_mission += 1
                    continue

            matching.append(watcher)

        # M2 — debug log when ``mission_terminal`` opt-in held
        # notifications back. The watcher rows remain in place for
        # the future terminal event; nothing claims them here.
        if held_for_mission:
            logger.debug(
                "notify_work_watchers: held %d watcher(s) for "
                "mission_terminal gating on work_id=%s status=%s — "
                "mission liveness not yet terminal; rows preserved",
                held_for_mission,
                work_id[:8],
                status,
            )

        # Step 3 (N1 — 2026-09-03): CLAIM-FIRST for terminal
        # statuses. The atomic DELETE...RETURNING on the matching
        # instance_id subset runs BEFORE any ``enqueue_message``.
        # The repo-level CAS is the only primitive that closes the
        # bounded ≤2 duplicate-delivery window the notify-then-claim
        # ordering had. Two concurrent callers each partition
        # independently above; only the CAS winner(s) receive the
        # watcher rows back, and ONLY those rows are notified in
        # step 4. The held-for-mission rows are excluded from the
        # claim's WHERE clause, so they survive untouched for the
        # future terminal event.
        if _is_terminal(status):
            if not matching:
                return 0
            claimed = await asyncio.to_thread(
                watcher_repo.claim_watchers_for_job_for_instances,
                work_id,
                [w.instance_id for w in matching],
            )
            if not claimed:
                # Lost every CAS — every matching row was already
                # claimed by a concurrent terminal caller. Their
                # notify loop owns delivery; ours would be a
                # duplicate. Skip cleanly (return 0, no enqueue).
                return 0
            notify_list = claimed
        else:
            # Non-terminal (``in_progress`` etc.): NEVER claim — the
            # watcher must stay registered so the eventual terminal
            # notification can still reach it. The earlier
            # implementation unconditionally claimed (deleted) all
            # watchers on every status, which broke progress tracking
            # by silently dropping the watch before the terminal
            # event fired. Read-only notify on the matching bucket.
            notify_list = matching
            logger.debug(
                "notify_work_watchers: non-terminal status=%s for "
                "work_id=%s — watcher rows preserved (read-only), "
                "terminal notification will still fire",
                status,
                work_id[:8],
            )

        # Step 4: notify ONLY the CAS winners (terminal) or the
        # read-only matching bucket (non-terminal). Either way, the
        # notify loop iterates ``notify_list`` — a set that, by
        # construction, has no overlap with any concurrent caller's
        # notify list on the same terminal ``work_id``. This is the
        # caller-level exactly-once invariant.
        notified = 0
        for watcher in notify_list:
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
