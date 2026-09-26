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
import time
import traceback
from typing import TYPE_CHECKING, Any

from daemon.services.mission_live_guard import (
    evaluate_mission_live,
)
from daemon.services.work_status import is_terminal as _is_terminal

if TYPE_CHECKING:
    from daemon.repositories.job_queue.watcher_models import JobWatcher
    from daemon.services.work_resolver import WorkResolverService

logger = logging.getLogger(__name__)


# ROUND-3 REVIEW (2026-09-24, 🟢 #2): the ``[watch-deliver]`` DEBUG
# log line bounds the body slice at this many bytes (256 by default).
# The line keeps the FULL body byte count as the explicit
# ``body_bytes=`` field so the byte-discrimination contract
# (prefix distinguishes ⟳-with-Result vs ✓-without) is preserved
# even when the slice is clipped — the slice is purely for log
# hygiene (no per-line size explosion on completed envelopes with
# full assistant content). Tuned to fit the standard 8 KiB log
# tail budget with comfortable headroom for ``ts=`` + ``work=`` +
# ``status=`` + ``to=`` + ``body_bytes=`` fields.
_MAX_LOG_BODY_BYTES = 256


def _caller_chain(max_frames: int = 3) -> str:
    """Best-effort caller attribution for the ``[watch-cas]`` debug log.

    Walks the stack newest→oldest, skipping frames that belong to this
    module and to asyncio/contextlib plumbing, and renders the first
    ``max_frames`` application frames as ``file:func:lineno`` joined by
    `` <- `` (immediate caller first). Callers MUST guard behind
    ``logger.isEnabledFor(logging.DEBUG)`` — the stack walk is never
    paid on the hot INFO path.

    DEFECT-1 round 3 (2026-09-24, fix/watch-notify-delivery-gaps):
    this attribution is the arbitration primitive. Three consecutive
    commits fixed producer sites that unit tests proved correct while
    the LIVE completed-leg stayed content-less — because a different
    caller won the CAS claim race. The chain at the claim chokepoint
    names the winner unambiguously (e.g.
    ``job_queue_service.py:notify_watchers:378 <-
    job_feedback_observer.py:_finalize_job:2076``).
    """
    chain: list[str] = []
    try:
        for fi in reversed(traceback.extract_stack()[:-1]):
            filename = fi.filename
            if filename.endswith("work_notifier.py"):
                continue
            if (
                "/asyncio/" in filename
                or filename.endswith(("contextlib.py", "threading.py"))
            ):
                continue
            chain.append(
                f"{filename.rsplit('/', 1)[-1]}:{fi.name}:{fi.lineno}"
            )
            if len(chain) >= max_frames:
                break
    except Exception:  # noqa: BLE001 — attribution must never break notify
        return "<caller-chain-unavailable>"
    return " <- ".join(chain) if chain else "<unknown>"


def _shape(v: str | None) -> str:
    """Render a kwarg's presence/shape for the structured debug log."""
    if v is None:
        return "None"
    if not v:
        return "empty"
    return f"len={len(v)}"


# Status display mapping — must stay byte-for-byte identical to the
# JobQueueService.notify_watchers implementation (orchestrator's
# parser contract).
_STATUS_DISPLAY_MAP: dict[str, str] = {
    "completed": "completed ✓",
    "settled": "settled ✓",
    "failed": "failed ✗",
    "in_progress": "in progress ⟳",
    "paused": "paused ⏸",
    # Mid-flight QA channel (2026-09-21, feature/midflight-qa-channel).
    # Four NEW NON-TERMINAL statuses — they take the non-terminal
    # branch (:417-447 area) which NEVER claims (no CAS DELETE...
    # RETURNING), so the watcher row survives for the eventual
    # terminal event. Icon glyphs match the existing vocabulary
    # (✓ ✗ ⟳ ⏸ ❓ ⏳). The parser keys off the ``[JOB_EVENT] Job
    # {work_id}... {status_display}`` header; these are additive
    # words inside that header (byte-compatible with the parser).
    "question_requested": "question requested ❓",
    "answer_received": "answer received ✓",
    "midflight_report": "mid-flight report ⟳",
    "stuck_awaiting_answer": "stuck awaiting answer ⏳",
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
           are SELECTed (no DELETE). The partition runs in two
           phases:

           a. **Mission-live verdict hoisted once** — when ANY
              subscribing watcher opts in to ``mission_terminal``,
              the canonical ``evaluate_mission_live`` guard
              (``daemon/services/mission_live_guard.py``) is invoked
              exactly ONCE per ``notify_work_watchers`` call. The
              guard consults the dependency-bus pending count, the
              permanent ``instances.parent_id`` tree, and the
              ``completed_at`` zombie-backstop window — the same
              legs the boot-sweep ``reconcile_terminal_watches``
              and the observer's ``_fire_watcher_notify_for_terminal``
              re-fire already use. This replaces the pre-C1 proxy
              check (``work_record.mission_liveness`` /
              ``work_record.status``) that missed live children
              on the 2026-09-25 mission 36be8aef incident.

           b. **Three-bucket classifier.** Each watcher lands in
              exactly one of:

              * **matching_claimable** — all subscribed events fire
                NOW (transport-kind status matches AND, for rows
                subscribing to ``mission_terminal``, mission
                liveness is terminal). These rows are CAS-claimed
                before notify (the N1 exactly-once invariant).
              * **matching_readonly** — SOME but not all subscribed
                events fire NOW (the multi-kind retire rule: a row
                subscribing to BOTH a transport kind AND
                ``mission_terminal`` fires the transport-kind
                delivery mid-mission but the row survives for the
                future mission-terminal fire — the LAST firing event
                is the one that claims). The notify loop iterates
                these rows WITHOUT claiming them so the row stays
                in the DB.
              * **held_for_mission** — the watcher subscribes only
                to ``mission_terminal`` AND the mission is still
                live. Skipped entirely on this call; survives for
                the future mission-terminal re-fire.

        3. **CLAIM-FIRST for terminal statuses (N1 fix,
           2026-09-03, extended by C1).** When ``status`` is
           terminal AND ``matching_claimable`` is non-empty, the
           atomic ``DELETE ... RETURNING`` on
           ``watcher_repo.claim_watchers_for_job_for_instances``
           runs BEFORE any ``enqueue_message``. Only the rows the
           CAS returned are notified via the claimable path.
           ``matching_readonly`` rows bypass the CAS — they are
           delivered (so the multi-kind row gets its transport-kind
           fire NOW) but the row itself stays in the DB for the
           future mission-terminal claim. ``held_for_mission`` rows
           are not in the CAS WHERE clause, so they survive
           untouched.

        4. **Notify ONLY the deliverable rows.** The notify loop
           iterates ``claimed ∪ matching_readonly`` (terminal
           status) or ``matching_claimable ∪ matching_readonly``
           (non-terminal). A row that was partitioned into
           ``matching_claimable`` but lost the CAS to a concurrent
           caller is NOT notified via the claimable path here —
           that caller's notify loop owns it. The
           ``matching_readonly`` rows are NOT part of the CAS
           contention (they bypass the claim) so they always
           deliver on this call.

        For **non-terminal** statuses (``in_progress`` etc.),
        ``claim`` is NEVER called: the notify loop iterates
        ``matching_claimable ∪ matching_readonly`` in read-only
        mode, and the watcher rows remain in the DB so the
        eventual terminal notification can still reach them. The
        earlier unconditional-claim implementation silently dropped
        these rows before the terminal event fired.

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
        status: Canonical status (``"completed"``, ``"settled"``,
            ``"failed"``, ``"cancelled"``, ``"dead_letter"``, or
            ``"in_progress"``).
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
        result_summary: Optional result text — included verbatim as
            the ``  Result:\n{result_summary}`` line for terminal
            notifications (every non-``in_progress`` status).

    Race-safe content (F-1, 2026-09-25): producer-side terminal-commit
    callers (the natural-completion paths in ``job_feedback_observer``
    and ``child_reports._dispatch_post_commit_side_effects``; the
    inline mirror finalize at ``task_processor.py:1159``; the
    PROCESS_REPORT dedup-skip site at
    ``task_processor._skip_task_as_completed``) MUST thread
    ``result_summary=<in-memory content>`` directly. The resolver
    fallback ``work_record.result_summary`` reads ``Task.result``
    which races the ``complete_task`` commit-visibility window
    (the live E2E race the DEFECT-1b close documented — see
    ``task_processor.py:1010-1020``); a caller that omits the kwarg
    and lets the resolver fallback run risks a content-less
    ``Result:`` block on the delivered envelope even when the
    producer had the content in scope. The race-prone resolver
    fallback remains active as the backwards-compat path for
    callers that haven't migrated to producer-side threading;
    new callers should thread explicitly.

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

        # 7d4a3bd9 false-completion fix (2026-09-26) — Fix 1 unverified
        # surface. When the resolved work's linked instance ended via
        # the gate escalation, the terminal ``completed`` event renders
        # the DISTINCT unverified string instead of plain
        # ``completed ✓`` (additive words inside the header — the
        # parser contract keys off the ``[JOB_EVENT] Job {id}...``
        # prefix and stays byte-compatible, same evolution rule the
        # midflight-status additions used). Non-escalated rows and
        # every other status are UNCHANGED.
        status_display = _format_status_display(status)
        if (
            getattr(work_record, "completion_gate_escalated", False)
            and status == "completed"
        ):
            from daemon.constants import COMPLETION_GATE_ESCALATED_DISPLAY

            status_display = COMPLETION_GATE_ESCALATED_DISPLAY

        matching_claimable: list[JobWatcher] = []
        """Rows that have ALL subscribed events firing NOW — claim + deliver."""

        matching_readonly: list[JobWatcher] = []
        """Rows that have SOME (but not all) subscribed events firing NOW.

        Deliver the body but DO NOT claim — the row stays in the DB for
        the still-pending subscribed event(s). Currently populated when
        a row subscribes to BOTH a transport-kind event (the current
        ``status``) AND ``mission_terminal`` while the mission is still
        live: the transport-kind fires NOW but ``mission_terminal`` is
        pending, so the row survives for the future mission-terminal
        delivery (which is the LAST firing event — see the
        commission-mandated retire rule below).
        """

        held_for_mission = 0

        # C1 fix (2026-09-25, fix/mission-terminal-watch-report-publish):
        # replace the PROXY mission liveness check (the
        # ``work_record.mission_liveness`` / ``work_record.status``
        # sniff) with the canonical ``evaluate_mission_live`` guard.
        # The proxy missed live children in the permanent instance tree
        # (the message-mirror flip per turn + per-receipt settlement
        # pattern dropped the row 6m42s early on the 2026-09-25
        # incident, mission 36be8aef). The guard consults the bus, the
        # root instance, and every descendant via the permanent
        # ``instances.parent_id`` reference — the same legs the boot
        # sweep and the observer re-fire paths already use, so
        # admission-vs-mission-liveness semantics stay aligned across
        # all three notify lanes.
        #
        # Hoisted out of the per-watcher loop: every watcher on the
        # same work_id shares the same mission liveness verdict, so
        # the guard runs ONCE per notify call (not per-watcher). The
        # expensive async ``evaluate_mission_live`` call is bypassed
        # entirely when no subscribing watcher opts in to
        # ``mission_terminal`` (the dominant case in production —
        # default ``ALL_WATCHABLE_EVENTS`` rows do NOT include
        # ``mission_terminal``; the M2 mission-class opt-in is
        # explicit).
        mission_live: bool | None = None
        any_mission_terminal_opt_in = any(
            "mission_terminal" in w.watch_events for w in watchers
        )
        if any_mission_terminal_opt_in:
            instance_repository = getattr(
                instance_manager, "_instance_repository", None
            )
            mission_instance_id = getattr(
                work_record, "instance_id", None
            )
            try:
                _guard_verdict = await evaluate_mission_live(
                    instance_repository=instance_repository,
                    instance_id=mission_instance_id,
                    task_completed_at=getattr(
                        work_record, "completed_at", None
                    ),
                    bus_pending_count=None,
                )
                mission_live = _guard_verdict.live
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "notify_work_watchers: mission-live guard "
                        "verdict for work_id=%s status=%s: live=%s "
                        "reason=%s",
                        work_id[:8],
                        status,
                        _guard_verdict.live,
                        _guard_verdict.reason,
                    )
            except Exception as guard_exc:  # noqa: BLE001
                # C3 fail-CLOSED (cycle 2 fixback, 2026-09-25): when
                # the mission-live guard cannot evaluate (seam
                # missing, guard-wrapper regression, etc.), HOLD
                # the row — ``mission_live = True`` means the
                # held_for_mission bucket below retains the watcher
                # until the future terminal flip / boot sweep. The
                # pre-C3 direction (live=False → claim + deliver) is
                # the one that CANNOT be allowed here: at-least-once
                # terminal delivery is preserved at the cost of
                # stranding the watch if the seam is broken, because
                # a stranded watch is recoverable on the next sweep
                # but a premature claim re-introduces the C1
                # false-positive class (the 2026-09-25 mission
                # 36be8aef incident). Production wiring guarantees
                # the seam (``InstanceManager._instance_repository``
                # is set in ``daemon/manager.py`` and propagated
                # through ``daemon/api.py``); a missing seam in
                # this path is a wiring regression — log loudly so
                # operators can spot the regression rather than
                # silently masquerading as the pre-C1 proxy
                # semantics.
                logger.error(
                    "notify_work_watchers: mission-live guard "
                    "raised %s for work_id=%s — fail-CLOSED (HOLD the "
                    "row; refuse premature claim — pre-C3 fail-OPEN "
                    "direction re-introduces the C1 false-positive "
                    "class): %s",
                    type(guard_exc).__name__,
                    work_id[:8],
                    guard_exc,
                )
                mission_live = True

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

            # C1 partition rule (multi-kind retire, design choice
            # 2026-09-25, ratified by user): a row R subscribed to
            # events E is claimable ONLY when EVERY subscribed event
            # has fired (or fires NOW). When SOME but not all
            # subscribed events fire NOW the row is delivered but NOT
            # claimed — it survives in the DB for the still-pending
            # event(s). The four shapes this rule admits:
            #
            #   (1) Pure transport-kind row (no ``mission_terminal``
            #       in events): the only subscribed event is the
            #       transport status. Fires iff ``standard_match``.
            #       Claim iff fires NOW. (Pre-existing A1 closure
            #       behaviour; preserved verbatim.)
            #
            #   (2) Pure ``mission_terminal`` row (no transport
            #       kind in events): the only subscribed event is
            #       ``mission_terminal``. Fires iff mission liveness
            #       is terminal. Claim iff fires NOW.
            #
            #   (3) Multi-kind row, mission LIVE: a subscribed
            #       transport kind fires NOW but
            #       ``mission_terminal`` is still pending. Deliver
            #       the transport-kind body (no claim) — the row
            #       survives for the future mission-terminal
            #       delivery.
            #
            #   (4) Multi-kind row, mission TERMINAL: the
            #       transport kind fires NOW AND
            #       ``mission_terminal`` fires NOW. This is the
            #       LAST firing event — claim + deliver. The
            #       transport-kind body uses the current
            #       ``status`` token (which may be a per-kind
            #       mirror rename like ``"settled"`` for
            #       ``job_type="message"`` rows); the user's
            #       ``mission_terminal`` subscription is honored
            #       by virtue of this delivery being the
            #       mission-terminal moment.
            #
            # Coalescing caveat (3 → 4): a user subscribed to
            # ``["completed", "mission_terminal"]`` for a task-kind
            # work_id whose mission settles naturally gets TWO
            # ``[JOB_EVENT]`` envelopes — the first at
            # receipt-settle (mid-mission, "completed ✓") and the
            # second at mission-terminal (the current status).
            # This is the user-intended semantics: each subscribed
            # event fires exactly once when its trigger arrives.
            # The pre-C1 A1 closure coalesced to a single
            # receipt-settle delivery that DROPPED the
            # ``mission_terminal`` event entirely — the
            # commission overrides that with the multi-event rule.
            #
            # Non-terminal kinds (DEFECT-5 preservation,
            # 2026-09-24, ``fix/watch-notify-delivery-gaps``): a
            # multi-kind row with a non-terminal kind subscription
            # (``[in_progress, mission_terminal]``,
            # ``[midflight_report, mission_terminal]``, etc.)
            # delivers the non-terminal kind immediately even
            # mid-mission — the mission-live guard is only
            # consulted to gate ``mission_terminal`` itself.
            # The non-terminal fire lands in the read-only bucket
            # (no CAS claim); the eventual mission-terminal fire
            # is the LAST firing event and claims.
            if mission_terminal_opt_in:
                if mission_live is True:
                    # Mission still live — ``mission_terminal``
                    # is pending. The current ``status`` either
                    # fires (multi-kind, non-terminal-kind, or
                    # transport-kind row) or does not (pure
                    # mission_terminal-only row).
                    if not standard_match:
                        # Pure ``mission_terminal`` row with
                        # mission still live — HOLD the row.
                        held_for_mission += 1
                        continue
                    # Multi-kind row with at least one non-terminal
                    # or terminal transport kind firing NOW:
                    # deliver the body but DO NOT claim (the
                    # ``mission_terminal`` subscription is still
                    # pending and will fire later).
                    matching_readonly.append(watcher)
                    continue
                # Mission terminal (or guard failed-OPEN): the
                # ``mission_terminal`` event fires NOW. The
                # current ``status`` may ALSO fire (multi-kind)
                # or may not (pure mission_terminal). Either way,
                # this is the LAST firing event — claim + deliver.
                # Fall through to ``matching_claimable``.

            # Path 1 (pure transport-kind, status matches) AND
            # Path 2 (pure ``mission_terminal``, mission terminal)
            # AND Path 4 (multi-kind, mission terminal): all
            # subscribed events have fired NOW — claim + deliver.
            matching_claimable.append(watcher)

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

        # Step 3 (N1 — 2026-09-03, extended by C1 2026-09-25):
        # CLAIM-FIRST for terminal statuses, but ONLY for rows whose
        # ALL subscribed events fire NOW (``matching_claimable``).
        # Rows that have SOME but not all subscribed events firing
        # NOW (``matching_readonly`` — multi-kind rows whose
        # ``mission_terminal`` subscription is still pending while a
        # transport kind fires NOW) are delivered WITHOUT claim so the
        # row survives for the future mission-terminal fire.
        # ``held_for_mission`` rows are pure ``mission_terminal``
        # rows with mission still live — neither claimed nor
        # delivered this call; they wait for the eventual
        # mission-terminal re-fire (which reaches the matching_claimable
        # bucket when the guard flips to ``live=False``).
        if _is_terminal(status):
            if not matching_claimable:
                # No row was fully-claimable; skip the CAS chokepoint
                # entirely. ``matching_readonly`` rows will be
                # delivered read-only below.
                claimed = []
            else:
                claimed = await asyncio.to_thread(
                    watcher_repo.claim_watchers_for_job_for_instances,
                    work_id,
                    [w.instance_id for w in matching_claimable],
                )
                # DEFECT-1 round-3 arbitration instrumentation (2026-09-24,
                # fix/watch-notify-delivery-gaps): permanent structured DEBUG
                # log at the CAS claim chokepoint. Emits for BOTH outcomes —
                # the claim winner (claimed>=1) AND the loser (claimed=0) —
                # with ns timestamp, stack-derived caller chain, and the
                # kwarg shape each side held. This is the ground-truth
                # record of WHICH caller site won the exactly-once race and
                # what content it carried (``kw_result_summary=None`` on a
                # winning line IS the content-less-delivery proof).
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "[watch-cas] ts=%d work=%s status=%s caller=%s "
                        "kw_result_summary=%s resolver_result_summary=%s "
                        "effective_result=%s kw_error=%s effective_error=%s "
                        "claimable=%d readonly=%d claimed=%d",
                        time.time_ns(),
                        work_id[:8],
                        status,
                        _caller_chain(),
                        _shape(result_summary),
                        _shape(getattr(work_record, "result_summary", None)),
                        _shape(effective_result),
                        _shape(error),
                        _shape(effective_error),
                        len(matching_claimable),
                        len(matching_readonly),
                        len(claimed),
                    )
            if matching_claimable and not claimed:
                # Lost every CAS — every claimable row was already
                # claimed by a concurrent terminal caller. Their
                # notify loop owns delivery of the claimable rows;
                # ours would be a duplicate. ``matching_readonly``
                # rows still deliver (they were not part of the CAS
                # contention) so include them in the notify list.
                notify_list = list(matching_readonly)
            else:
                # CAS winners + read-only rows. The CAS winner set is
                # the DB-confirmed claim list (exactly-once by repo
                # CAS); the read-only rows bypass the CAS entirely
                # (multi-kind rows whose mission_terminal subscription
                # is still pending).
                notify_list = list(claimed) + list(matching_readonly)
        else:
            # Non-terminal (``in_progress`` etc.): NEVER claim — the
            # watcher must stay registered so the eventual terminal
            # notification can still reach it. The earlier
            # implementation unconditionally claimed (deleted) all
            # watchers on every status, which broke progress tracking
            # by silently dropping the watch before the terminal
            # event fired. Read-only notify on BOTH buckets.
            notify_list = list(matching_claimable) + list(matching_readonly)
            logger.debug(
                "notify_work_watchers: non-terminal status=%s for "
                "work_id=%s — watcher rows preserved (read-only), "
                "terminal notification will still fire",
                status,
                work_id[:8],
            )

        # Step 4: notify ONLY the deliverable rows — either the
        # CAS-winners ∪ matching_readonly (terminal) or
        # matching_claimable ∪ matching_readonly (non-terminal).
        # By construction ``notify_list`` has no overlap with any
        # concurrent caller's notify list on the same terminal
        # ``work_id`` — the caller-level exactly-once invariant.
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

            # DEFECT-1 round-3 arbitration instrumentation: full body at
            # DEBUG so the delivered envelope can be byte-discriminated
            # straight from the log (⟳-with-Result vs ✓-without), paired
            # with the ``[watch-cas]`` claim line above.
            #
            # ROUND-3 REVIEW (2026-09-24, 🟢 #2): Bounded log. The
            # ``body=%r`` field used to dump the WHOLE notification,
            # which can be many KB on a completed envelope with full
            # assistant content — enough to swamp a log tail, bloat
            # the test-pack archive snapshot, and (worst case) trip
            # the structured-sink per-line cap. The byte-discrimination
            # contract this log line serves only needs the body
            # PREFIX (the ``[JOB_EVENT]`` header + ``Result:`` slot
            # start). Cap the body slice at ``MAX_LOG_BODY_BYTES``
            # (256) and KEEP the full byte count as an explicit
            # ``body_bytes=`` field. An ellipsis marker
            # (``<…truncated>``) suffixes the slice when the cap
            # fires so it's unambiguous from the log that the body
            # was clipped.
            if logger.isEnabledFor(logging.DEBUG):
                _body_bytes = len(notification)
                _slice = notification[:_MAX_LOG_BODY_BYTES]
                if _body_bytes > _MAX_LOG_BODY_BYTES:
                    _slice = f"{_slice}<…truncated>"
                logger.debug(
                    "[watch-deliver] ts=%d work=%s status=%s to=%s "
                    "body_bytes=%d body=%r",
                    time.time_ns(),
                    work_id[:8],
                    status,
                    watcher.instance_id[:8],
                    _body_bytes,
                    _slice,
                )

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
