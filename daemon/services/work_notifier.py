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

The ``source`` parameter on ``enqueue_message`` is built by the
private :func:`_job_event_source` helper → the canonical
``f"internal_agent:job_event:{work_id}:{status}"`` shape — the
orchestrator treats the ``job_event`` tag as the trigger to look up
the work record again from its own side. ANY divergence in this
string silently drops the orchestrator's terminal handler; if a new
caller needs a different source token, route a NEW helper, do not
fork the f-string.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from daemon.services.mission_live_guard import (
    evaluate_mission_live,
)
# CYCLE-TRAP (2026-10-05, fix-cycle-2 polish): this is a DEFERRED
# import on purpose. ``work_notifier`` is imported by
# ``midflight_qa`` (its top-of-module ``from daemon.services.work_notifier
# import notify_mission_qa_watchers`` is the canonical home per the
# mission-scoped QA fan-out), so a top-level import here would create
# a module-load cycle (midflight_qa → work_notifier → midflight_qa).
# The earlier module-load order guarantees the constant is bound by
# the time any function in this module runs; do not hoist without
# auditing both call sites and the package init.
from daemon.services.midflight_qa import (
    MISSION_RECEIPT_SCAN_CAP,
    QA_STATUS_QUESTION_ESCALATION,
    QA_STATUS_QUESTION_REQUESTED,
    QA_STATUS_STUCK_AWAITING_ANSWER,
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
    # Mission-scoped escalation envelope (feature/question-watch-fanout,
    # 2026-10-05): the wedge-guard escalation (emission_index >= 3)
    # delivers to mission watchers in ADDITION to the FE SSE broadcast.
    # Additive words inside the parser's header prefix — byte-compatible.
    # GLYPH COLLISION (fix-cycle-2 polish, item 12): escalation and
    # ``stuck_awaiting_answer`` share the ⏳ icon. A distinct glyph
    # would be visually cleaner but the existing regression test
    # contract (``test_question_watch_fanout.py``:474,543,638) pins
    # the literal ``"question escalation ⏳"`` token — changing the
    # icon requires a paired test edit outside this cycle's allowed
    # scope. Deferred to a follow-up commission.
    "question_escalation": "question escalation ⏳",
}


def _format_status_display(status: str) -> str:
    """Return the user-facing status string with icon.

    Unknown statuses pass through unchanged (the prior behaviour).
    """
    return _STATUS_DISPLAY_MAP.get(status, status)


def _job_event_source(work_id: str, status: str) -> str:
    """Return the canonical ``[JOB_EVENT]`` enqueue ``source`` string.

    Canonical home for the source-string contract documented in the
    module docstring. Every ``enqueue_message(..., source=...)`` call
    that targets the orchestrator's job-event handler MUST go through
    this helper — the orchestrator's parser keys off the ``job_event``
    tag, so a literal divergence silently drops the terminal handler.

    Args:
        work_id: The work_id stamped into the source token.
        status: The status token stamped into the source token.

    Returns:
        The ``f"internal_agent:job_event:{work_id}:{status}"`` string.
    """
    return f"internal_agent:job_event:{work_id}:{status}"


def _build_event_envelope(
    *,
    work_id: str,
    status: str,
    status_display: str,
    agent_id: str,
    progress: str | None = None,
    result_summary: str | None = None,
    error: str | None = None,
) -> str:
    """Build the ``[JOB_EVENT]`` envelope — the SINGLE body builder.

    Extracted (feature/question-watch-fanout, 2026-10-05) from the
    ``notify_work_watchers`` notify loop so the mission-scoped QA lane
    (``notify_mission_qa_watchers``) renders byte-identical envelopes
    from the same code. The parser contract keys off the
    ``[JOB_EVENT] Job {work_id[:8]}... {status_display}`` header and
    the ``  Agent:`` / ``  Progress:`` / ``  Result:`` / ``  Error:``
    line shapes — do not reformat.

    The ``in_progress`` branch is keyed on the raw ``status`` token
    (progress line only for progress events); every other status
    renders ``Result:`` / ``Error:`` blocks from the provided content.
    """
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
        if error:
            notification_parts.append(f"  Error: {error}")

    return "\n".join(notification_parts)


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
        * **Exactly-once on success; at-least-once on transient
          enqueue failure.** Watchers are CAS-claimed via the
          repo-level atomic ``DELETE ... RETURNING`` (the N1
          exactly-once invariant), enqueued per-row, and on
          per-watch enqueue failure each dropped-claim row is
          re-inserted via ``watcher_repo.add_watch`` UPSERT so the
          next sweep tick OR a future terminal re-fire can
          deliver. The pre-S15 flow claimed-then-enqueued-threw
          and silently lost the row (the docstring claim was
          structurally false under that throw path); S15
          (2026-09-28) catches per-watch and runs compensation
          AFTER the loop completes. If ``resolve_work`` fails
          (work gone), watchers stay in place for reconcile
          cleanup at the next startup — the watching instance
          is never permanently un-notified.

    Ordering (resolve → partition → claim-first → notify → compensate-on-fail):

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
              guard consults the dependency-bus pending count and
              the permanent ``instances.parent_id`` tree; the
              zombie backstop is anchored to the freshest
              ``last_activity_at`` across the tree (U7,
              2026-09-28) — the same legs the boot-sweep
              ``reconcile_terminal_watches`` and the observer's
              ``_fire_watcher_notify_for_terminal`` re-fire
              already use. This replaces the pre-C1 proxy check
              (``work_record.mission_liveness`` /
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
            # PP1 (2026-10-03, report-delivery-bug-family) — on a
            # TERMINAL event with ZERO watchers, the early return is
            # silent under the pre-fix behaviour. The watcher missed
            # the event but the operator has no log line to correlate
            # the dropped report. For terminal statuses, emit a WARN
            # carrying the job_id, the terminal status, and a pointer
            # to the durable ``job_completed`` event row (where the
            # ``result_summary`` / ``error`` lives) so recovery is
            # trivial: re-fire from the event row, or surface the
            # event payload directly. Non-terminal statuses (e.g.
            # ``in_progress``) stay claim-free by design (see
            # :data:`site 3 below <claim-site>`) and are NOT warned
            # here — only the terminal case is a delivery gap.
            if _is_terminal(status):
                logger.warning(
                    "PP1 zero-watcher terminal fire: work_id=%s "
                    "status=%s — no watchers found, the terminal "
                    "report is NOT being delivered. The producer of "
                    "this terminal is expected to publish a "
                    "job_completed event row carrying the "
                    "result_summary / error payload (it has not been "
                    "verified at this layer; this WARN never queries "
                    "the events table). For recovery, look up the "
                    "instance's events in the events table by "
                    "instance_id, or consult the producer-side log "
                    "for the published event id.",
                    work_id[:8] if work_id else "<none>",
                    status,
                )
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

        # M2 + U7 (2026-09-28) — held-watcher diagnostic. Pre-U7 this
        # was DEBUG-only, which made "all rows held, mission still
        # live, nothing delivered" silent in production logs even
        # though that shape is the wave-3 misfire surface. Promote
        # to an INFO line that fires AT MOST once per notify call
        # (the periodic sweep tick cadence handles the heartbeat
        # for orphaned rows; per-call INFO here would be spam on a
        # hot row). Operator greppable: ``held_for_mission_observation``.
        if held_for_mission:
            log_fn = (
                logger.info
                if held_for_mission > 0
                and not matching_claimable
                and not matching_readonly
                else logger.debug
            )
            log_fn(
                "notify_work_watchers: held_for_mission_observation "
                "work_id=%s status=%s — %d watcher(s) on mission_terminal "
                "gating; mission_live=%s; ZERO deliverable rows this "
                "call (the eventual mission-terminal fire claims them "
                "OR the periodic sweep / hook (b) does so within the "
                "guaranteed ≤300s interval); pairing this line with "
                "the row's ``created_at`` lets operators spot rows "
                "that are stuck past one tick",
                work_id[:8],
                status,
                held_for_mission,
                mission_live,
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
                # PP1 (2026-10-03, report-delivery-bug-family) —
                # terminal event with CAS claim returning ZERO.
                # The pre-fix path was silent (DEBUG-only at
                # best); for terminal events the loss is a
                # delivery gap, not an arbitration detail. WARN
                # carrying the job_id, status, claimable count
                # vs claimed count, and a pointer to the durable
                # ``job_completed`` event row for recovery.
                # The companion DEBUG log below stays for the
                # exactly-once arbitration audit (caller chain
                # / kwarg shape); the WARN is the delivery-gap
                # signal.
                if not claimed:
                    # m3 (2026-10-03, report-delivery-bug-family) —
                    # soften the wording to not claim a specific
                    # event-row id has been verified at this layer.
                    # The CAS-claim chokepoint never queries the
                    # events table; the JOB_COMPLETED row is
                    # published by the producer (the
                    # ``job_feedback_observer`` Item-3b publish at
                    # ``job_feedback_observer.py:~2375``) AFTER the
                    # notify path returns. The WARN is the
                    # delivery-gap signal; recovery is the
                    # operator's job to correlate via the
                    # instance's events table.
                    logger.warning(
                        "PP1 zero-claimed terminal fire: work_id=%s "
                        "status=%s — CAS claim returned 0 rows "
                        "(claimable=%d). The terminal report is NOT "
                        "being delivered to the %d claimable "
                        "watcher(s). The producer-side log should "
                        "carry the JOB_COMPLETED event id once it "
                        "is published; the durable event row carries "
                        "the result_summary / error payload and can "
                        "be re-fired from the events table for "
                        "recovery. NOTE: re-delivery is NOT "
                        "auto-compensated here (duplicate-delivery "
                        "risk) — the WARN is the observability arm "
                        "and a follow-up commission owns re-arm.",
                        work_id[:8] if work_id else "<none>",
                        status,
                        len(matching_claimable),
                        len(matching_claimable),
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
            #
            # PP1 (2026-10-03, report-delivery-bug-family) — the
            # non-terminal case stays CLAIM-FREE by design. The PP1
            # zero-watcher WARN at the early-return site above (and
            # the zero-claimed WARN at the CAS site) is ONLY emitted
            # for terminal statuses. A non-terminal fire with zero
            # watchers is the expected state (no terminal event has
            # fired yet; ``mission_terminal`` rows wait for the
            # mission-class finalization, ``in_progress`` rows
            # simply have no observers). Logging at WARN would
            # flood the operator log for every progress tick — the
            # contract is documented here so future maintainers do
            # not "fix" the non-terminal zero-watcher case.
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
        #
        # S15 (2026-09-28, fix/u7-orphan-anchor-s15): per-watcher
        # exception boundary. The pre-S15 flow threw into the
        # outer ``except Exception`` on the FIRST enqueue failure,
        # which logged a warning and returned 0 — silently DROPPING
        # every claimed row whose enqueue hadn't yet completed.
        # The module docstring claim that "watchers are deleted
        # only AFTER all notifications are delivered" was
        # structurally false under this throw path. The fix:
        # catch per-watch, accumulate the failed-claimed set, run
        # compensation (``watcher_repo.add_watch`` UPSERT to put
        # the dropped rows BACK) AFTER the loop. Success path
        # exactly-once (CAS-claim remains the only thing that
        # transitions a row out of the DB); failure path
        # at-least-once via compensation.
        notified = 0
        failed_claimed: list[JobWatcher] = []  # S15: per-watch failures → compensation
        for watcher in notify_list:
            # Envelope build extracted to ``_build_event_envelope`` (the
            # single body builder — the mission-scoped QA lane renders
            # byte-identical envelopes from the same code).
            notification = _build_event_envelope(
                work_id=work_id,
                status=status,
                status_display=status_display,
                agent_id=agent_id,
                progress=progress,
                result_summary=effective_result,
                error=effective_error,
            )

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

            # S15 (2026-09-28): per-watch exception boundary. The
            # CAS-claim above DELETED this row; an enqueue throw
            # here used to silently drop the row (the outer
            # ``except Exception`` caught, returned 0, and the
            # docstring's "deleted only AFTER delivery" promise
            # was structurally false). Now: catch here, record
            # the failed-claimed watcher for compensation, and
            # continue with the rest. The compensation (add_watch
            # UPSERT) runs AFTER the loop completes — restores
            # the dropped rows so the periodic sweep / natural
            # retry can re-attempt. The success path still pays
            # only one claim cost (atomic DELETE...RETURNING);
            # failure path becomes at-least-once instead of
            # permanent-loss.
            try:
                await instance_manager.enqueue_message(
                    instance_id=watcher.instance_id,
                    message=notification,
                    source=_job_event_source(work_id, status),
                )
                notified += 1
            except Exception as enq_err:  # noqa: BLE001
                # Accumulate for compensation; do NOT propagate
                # (the outer try/except used to catch this and
                # return 0, which was structurally wrong under the
                # docstring's delivery contract).
                failed_claimed.append(watcher)
                logger.warning(
                    "S15: notify_work_watchers enqueue failed for "
                    "watcher work_id=%s status=%s to=%s (%s: %s) — "
                    "scheduling compensation UPSERT after the loop "
                    "so the dropped-claim row is preserved for the "
                    "next sweep tick (at-least-once path)",
                    work_id[:8] if work_id else "<none>",
                    status,
                    watcher.instance_id[:8],
                    type(enq_err).__name__,
                    enq_err,
                )

        # S15 compensation: re-insert every claimed row whose enqueue
        # threw. ``add_watch`` is the UPSERT (``INSERT ... ON CONFLICT
        # DO UPDATE``) — restores the row with the original
        # ``watch_events`` list, so the periodic sweep OR a future
        # terminal re-fire can deliver. The UPSERT is per-row inside
        # its own transaction; one row's failure does not block the
        # others. ``add_watch`` UPSERTs the row — both INSERT and
        # UPDATE branches are covered by the same
        # ``INSERT ... ON CONFLICT DO UPDATE`` statement (see
        # ``JobWatcherRepository.add_watch``); the UPDATE side only
        # touches ``watch_events`` (created_at is preserved on the
        # UPDATE branch, re-stamped on the INSERT branch — see the
        # S15 created_at re-stamp seam below). A row that ANOTHER
        # concurrent claim raced and re-deleted (extremely unlikely
        # under the claim-first semantics) gets re-supplied here.
        # S15 created_at re-stamp seam (MINOR-8, 2026-09-28): the
        # compensation INSERTs a FRESH row — the original was
        # CAS-claim DELETEd in step 3 — so ``JobWatcher.created_at``
        # is re-stamped to the compensation's current INSERT time,
        # NOT preserved from the original observation. Operators
        # reading ``created_at`` to spot stuck rows must therefore
        # take the re-stamp into account: a row that survived
        # compensation appears "fresh" relative to the original
        # observation, even though the watch itself is not new.
        # This is the only S15 site that resets a row's created_at;
        # the natural notify path leaves created_at untouched on
        # first insert (the row exists from registration to fire).
        if failed_claimed:
            _compensated = 0
            _lost = 0
            for watcher in failed_claimed:
                try:
                    await asyncio.to_thread(
                        watcher_repo.add_watch,
                        job_id=watcher.job_id,
                        instance_id=watcher.instance_id,
                        watch_events=list(watcher.watch_events or []),
                    )
                    _compensated += 1
                except Exception as comp_err:  # noqa: BLE001
                    _lost += 1
                    logger.error(
                        "S15: compensation UPSERT failed for "
                        "watcher work_id=%s to=%s (%s: %s) — row "
                        "DROPPED from the DB; the next sweep tick "
                        "WILL NOT see it. This is the residual "
                        "exactly-once-vs-at-least-once edge case: "
                        "log loudly so the operator can spot the "
                        "stranded row",
                        watcher.job_id[:8] if watcher.job_id else "<none>",
                        watcher.instance_id[:8],
                        type(comp_err).__name__,
                        comp_err,
                    )
            # Diagnostic INFO line — the operator can grep this for
            # "compensation" and count dropout ratios during incident
            # triage.
            logger.info(
                "S15: notify_work_watchers for work_id=%s status=%s "
                "completed with partial drops: notified=%d "
                "compensated=%d lost=%d",
                work_id[:8] if work_id else "<none>",
                status,
                notified,
                _compensated,
                _lost,
            )

        return notified

    except Exception as e:
        # Notification is best-effort. The terminal write already
        # succeeded; the worst-case outcome of a failed notify is
        # that the watcher misses the event, which the next manual
        # ``reconcile_terminal_watches`` sweep will pick up at next
        # startup. Log at warning so operators can spot systemic
        # issues without crashing the worker thread.
        #
        # S15 NOTE (2026-09-28): the per-watch compensation path
        # above catches enqueue failures explicitly. This outer
        # except now covers UNEXPECTED exceptions only (e.g. a
        # repo read failure during ``get_watchers_for_job`` or
        # ``claim_watchers_for_job_for_instances``). Compensation
        # is NOT possible here because we never reached the
        # claim step — no rows were deleted, so there is nothing
        # to put back.
        logger.warning(
            "notify_work_watchers: failed to notify watchers for "
            "work_id=%s status=%s: %s",
            work_id[:8] if work_id else "<none>",
            status,
            e,
        )
        return 0


# =============================================================================
# Mission-scoped, EMISSION-TIME QA fan-out
# (feature/question-watch-fanout, 2026-10-05)
# =============================================================================
#
# RCA (ratified): QA fan-out was recipient-addressed PER RECEIPT, so
# (1) spontaneous receipts (child-report wake mints a new Task row)
# carried ZERO watch coverage and the asking turn rode a receipt nobody
# watched, and (2) ``mission_terminal``-only rows never matched QA
# statuses (standard_match=False → held_for_mission) — the mission
# e71d133d incident: watcher armed on settled receipt 4d31ddbe, wake
# minted 6d8765f2, ask on that turn → ZERO emissions.
#
# The fix resolves recipients at EMISSION TIME by MISSION SCOPE: every
# watcher holding an UNCLAIMED row on ANY receipt associated with the
# asking instance's mission, regardless of (i) which receipt the asking
# turn rides and (ii) the row's ``watch_events`` (events-filter-EXEMPT
# — a mission blocked awaiting a human answer is state every mission
# watcher needs; filtering it recreates the silent wedge).
#
# Invariants (do not regress):
# * NON-CLAIMING — this lane never claims/transitions watcher rows; the
#   three-bucket partition (claimable / readonly / held_for_mission),
#   the C1 ``evaluate_mission_live`` gate, the N1 claim-first CAS, the
#   C3 fail-CLOSED direction, and the F1 no-replay registration filter
#   are untouched (the 36be8aef family). The ``mission_live`` HOLD
#   governs the TERMINAL fire only — QA delivers while the mission is
#   live (PAUSED is live) and never consumes a row.
# * ``mission_terminal`` stays receipt-keyed via ``notify_work_watchers``
#   — spontaneous receipts still need a ``watch_mission`` re-arm for
#   TERMINAL coverage; QA reach does NOT create terminal coverage.
# * BOUNDED — the recipient query is ONE ``job_id IN (...)`` SELECT with
#   a defensive LIMIT over a capped receipt set (see
#   ``daemon.services.midflight_qa.enumerate_mission_work_ids``); dedupe
#   is per watcher instance (rows on multiple receipts → ONE emission).

# Built from the canonical constants in ``daemon.services.midflight_qa``
# (fix-cycle-2 polish, item 6) — typo-safety: a constant name drift
# fails loud at module load, not silently at emission time. The set
# stays a frozenset (immutable; safe to share across the deferred-
# import boundary).
_MISSION_SCOPED_QA_STATUSES: frozenset[str] = frozenset(
    {
        QA_STATUS_QUESTION_REQUESTED,
        QA_STATUS_STUCK_AWAITING_ANSWER,
        QA_STATUS_QUESTION_ESCALATION,
    }
)

# Defensive row cap on the multi-receipt watcher SELECT (pairs with the
# ``JobWatcherRepository.get_watchers_for_jobs`` limit parameter).
# PUBLIC (fix-cycle-2 polish, 2026-10-05): the cap is the
# service-canonical home — cross-referenced by name in
# ``daemon/repositories/job_queue/watcher_repository.py`` and pinned
# for equality in ``test_question_watch_fanout.py`` (cap-equality pin).
QA_WATCHER_ROW_CAP = 256


@dataclass(frozen=True)
class NotifyQAPayload:
    """Atomic payload for :func:`notify_mission_qa_watchers`.

    Collapses the helper's 8-caller-kwarg surface into a single value
    object (fix-cycle-2 polish, 2026-10-05) so the public function
    signature stays minimal: ``notify_mission_qa_watchers(payload)``.
    The dataclass carries NO behavior - pure value object consumed by
    the private helpers below.

    Field semantics:

    * ``work_id``: Primary envelope candidate (the asking-turn receipt
      when one exists) and the fallback ``Agent:`` identity source
      via ``work_resolver.resolve_work``. A resolve miss degrades to
      ``"unknown"`` and STILL delivers: dropping the question would
      recreate the silent wedge this lane closes.
    * ``status``: One of :data:`_MISSION_SCOPED_QA_STATUSES`. Anything
      else (transport kinds, ``mission_terminal``) is REFUSED
      fail-closed at the public entry - terminal delivery stays
      exclusively on :func:`notify_work_watchers` C1/N1 machinery.
    * ``mission_work_ids``: The mission's bounded receipt set
      (newest-live first). Callers build it with
      :func:`midflight_qa.enumerate_mission_work_ids`. REQUIRED
      (positional-no-default) - the call-site contract owns the
      pre-flip snapshot semantics (terminate-flip cascades delete
      Task rows; the public helper refuses a degraded fallback).
    * ``instance_manager``: The :class:`InstanceManager` whose
      :meth:`enqueue_message` delivers the envelope.
    * ``work_resolver``: :class:`WorkResolverService` - Agent-line
      identity.
    * ``watcher_repo``: The :class:`JobWatcherRepository` whose
      :meth:`get_watchers_for_jobs` performs the read-only IN-select.
    * ``result_summary``: Optional ``Result:`` body (pack payload /
      escalation description). The ``progress`` kwarg that used to
      ride here was STRUCTURALLY DEAD (the QA lane's
      ``_MISSION_SCOPED_QA_STATUSES`` excludes ``in_progress``, so
      callers' content was silently discarded by
      :func:`_build_event_envelope`); callers route any text they
      need rendered through ``result_summary``.
    * ``error``: Optional ``Error:`` body.
    """

    work_id: str
    status: str
    mission_work_ids: list[str]
    instance_manager: Any  # InstanceManager - typed loosely to avoid an import cycle
    work_resolver: "WorkResolverService"
    watcher_repo: Any  # JobWatcherRepository - typed loosely to avoid an import cycle
    result_summary: str | None = None
    error: str | None = None


def _build_candidate_ids(payload: NotifyQAPayload) -> list[str]:
    """Build the deduped + capped receipt candidate set for the IN-select.

    Order: ``work_id`` (the primary envelope candidate) first, then
    ``mission_work_ids`` (the mission's receipt set, newest-live
    first). Defensive re-cap at :data:`MISSION_RECEIPT_SCAN_CAP` - the
    caller should have already capped, but a cap here keeps the
    bounded guarantee under any future caller regression.
    """
    candidate_ids: list[str] = []
    seen: set[str] = set()
    for rid in [payload.work_id, *(payload.mission_work_ids or [])]:
        if rid and rid not in seen:
            seen.add(rid)
            candidate_ids.append(rid)
    return candidate_ids[:MISSION_RECEIPT_SCAN_CAP]


async def _select_watchers(
    payload: NotifyQAPayload, candidate_ids: list[str]
) -> list[JobWatcher]:
    """Read-only multi-receipt watcher SELECT (best-effort, warns on failure).

    Returns an empty list on a repository failure (the emission must
    not break the asker - the orchestrator's parsing is
    fire-and-forget from the helper's perspective). The cap-hit
    observability (DESC ordering + WARN listing dropped watcher ids)
    is the repository's responsibility - see
    :meth:`JobWatcherRepository.get_watchers_for_jobs`.
    """
    try:
        return await asyncio.to_thread(
            payload.watcher_repo.get_watchers_for_jobs,
            candidate_ids,
            QA_WATCHER_ROW_CAP,
        )
    except Exception as sel_err:  # noqa: BLE001 - emission must not break the asker
        logger.warning(
            "notify_mission_qa_watchers: receipt-select failed for "
            "status=%s work_id=%s (%d candidates): %s",
            payload.status,
            payload.work_id[:8] if payload.work_id else "<none>",
            len(candidate_ids),
            sel_err,
        )
        return []


async def _resolve_agent_id(payload: NotifyQAPayload) -> str:
    """Resolve the Agent-line identity for the envelope ONCE.

    The asker is the same for every recipient, so a single resolve
    suffices. A resolve failure degrades to ``"unknown"`` - QA
    delivery never depends on the resolver.
    """
    if payload.work_resolver is None:
        return "unknown"
    try:
        work_record = await asyncio.to_thread(
            payload.work_resolver.resolve_work, payload.work_id
        )
        if work_record is not None:
            return work_record.agent_id or "unknown"
    except Exception as res_err:  # noqa: BLE001
        logger.debug(
            "notify_mission_qa_watchers: agent resolve failed for "
            "work_id=%s (%s) - delivering with unknown identity",
            payload.work_id[:8] if payload.work_id else "<none>",
            res_err,
        )
    return "unknown"


def _group_rows_by_instance(
    watchers: list[JobWatcher],
) -> dict[str, list[JobWatcher]]:
    """Bucket ``watchers`` by ``instance_id`` for the per-watcher dedupe."""
    rows_by_instance: dict[str, list[JobWatcher]] = {}
    for watcher in watchers:
        rows_by_instance.setdefault(watcher.instance_id, []).append(watcher)
    return rows_by_instance


def _pick_envelope_receipt(
    rows: list[JobWatcher], candidate_ids: list[str]
) -> str:
    """Pick the envelope's receipt key for ``rows``.

    Prefers the first candidate in priority order that the watcher
    holds a row on (live asking-turn receipts first, then newest
    mission receipts - mirrors :func:`_build_candidate_ids` order).
    Falls back to the watcher's first row's ``job_id`` (deterministic
    - the watcher is the source of truth for its own armed identity).
    """
    row_ids = {r.job_id for r in rows}
    for rid in candidate_ids:
        if rid in row_ids:
            return rid
    return rows[0].job_id


async def _enqueue_for_watchers(
    payload: NotifyQAPayload,
    candidate_ids: list[str],
    rows_by_instance: dict[str, list[JobWatcher]],
    agent_id: str,
    status_display: str,
) -> int:
    """Enqueue the envelope to every deduped watcher (S15 per-watch boundary).

    NON-CLAIMING: rows survive intact (no CAS, no row transition,
    no ``evaluate_mission_live`` call). A per-watcher enqueue failure
    is WARN-logged and does not break the other recipients - no
    compensation is needed (nothing was deleted).
    """
    notified = 0
    for instance_id, rows in rows_by_instance.items():
        key_work_id = _pick_envelope_receipt(rows, candidate_ids)
        envelope = _build_event_envelope(
            work_id=key_work_id,
            status=payload.status,
            status_display=status_display,
            agent_id=agent_id,
            result_summary=payload.result_summary,
            error=payload.error,
        )
        try:
            await payload.instance_manager.enqueue_message(
                instance_id=instance_id,
                message=envelope,
                source=_job_event_source(key_work_id, payload.status),
            )
            notified += 1
        except Exception as enq_err:  # noqa: BLE001
            logger.warning(
                "notify_mission_qa_watchers: enqueue failed for watcher "
                "status=%s key_work_id=%s to=%s (%s: %s) - row untouched "
                "(non-claiming lane)",
                payload.status,
                key_work_id[:8] if key_work_id else "<none>",
                instance_id[:8],
                type(enq_err).__name__,
                enq_err,
            )
    return notified


def _pack_legacy_kwargs(
    *,
    work_id: str | None,
    status: str | None,
    mission_work_ids: list[str] | None,
    instance_manager: Any,
    work_resolver: "WorkResolverService" | None,
    watcher_repo: Any,
    progress: str | None,
    result_summary: str | None,
    error: str | None,
) -> NotifyQAPayload:
    """Build a :class:`NotifyQAPayload` from the legacy 8-kwarg surface.

    Backward-compat shim (fix-cycle-2 polish): the original 8-kwarg
    signature is retained for the regression test surface
    (``test_question_watch_fanout.py``:745-753, 820-827). The shim
    packs the kwargs into the canonical payload; ``progress`` is
    accepted-but-ignored (the QA lane's
    ``_MISSION_SCOPED_QA_STATUSES`` excludes ``in_progress``;
    ``_build_event_envelope`` would silently discard the content).
    """
    if progress is not None:
        logger.debug(
            "notify_mission_qa_watchers: legacy 'progress' kwarg is "
            "structurally dead in the QA lane (statuses exclude "
            "in_progress); caller's content is discarded. Route "
            "through 'result_summary' instead."
        )
    return NotifyQAPayload(
        work_id=work_id or "",
        status=status or "",
        mission_work_ids=mission_work_ids or [],
        instance_manager=instance_manager,
        work_resolver=work_resolver,
        watcher_repo=watcher_repo,
        result_summary=result_summary,
        error=error,
    )


async def notify_mission_qa_watchers(
    payload: NotifyQAPayload | None = None,
    *,
    work_id: str | None = None,
    status: str | None = None,
    mission_work_ids: list[str] | None = None,
    instance_manager: Any = None,
    work_resolver: "WorkResolverService" | None = None,
    watcher_repo: Any = None,
    progress: str | None = None,
    result_summary: str | None = None,
    error: str | None = None,
) -> int:
    """Deliver one QA event to every mission watcher - non-claiming.

    Recipient resolution is EMISSION-TIME and MISSION-SCOPED: one
    bounded ``job_id IN (...)`` SELECT over the mission's receipt set
    (capped + deduped by the caller's
    :func:`midflight_qa.enumerate_mission_work_ids`) finds every
    watcher holding an UNCLAIMED row on ANY associated receipt. QA
    events are EVENTS-FILTER-EXEMPT - the row's ``watch_events``
    subscription does not gate delivery (a ``mission_terminal``-only
    row DOES receive QA events). Dedupe is per watcher instance: rows
    on multiple receipts yield ONE emission, whose envelope names the
    watcher's highest-priority receipt (its own armed identity - the
    asking-turn live receipt when it holds a row there, else the
    newest mission receipt it holds). Any mission receipt resolves
    back to the asker through the WorkResolver, so the answer route
    (``POST /api/jobs/{work_id}/answer`` / ``job_answer``) needs no
    watch row - the pack lives on the asker.

    Delivery is NON-CLAIMING (readonly): no CAS, no row transition, no
    ``evaluate_mission_live`` call - the mission_live HOLD governs
    the terminal fire only. Rows survive this delivery intact for
    their own subscribed events.

    Caller surface (fix-cycle-2 polish, item 3):

    * **NEW (canonical):** pass a single :class:`NotifyQAPayload`
      value object - ``await notify_mission_qa_watchers(payload)``.
      This is the surface all in-tree callers use.
    * **LEGACY (backward-compat):** the original 8-kwarg surface is
      STILL ACCEPTED for backward-compat with the regression test
      surface; the kwargs are packed into a :class:`NotifyQAPayload`
      via :func:`_pack_legacy_kwargs` before the helper dispatch.

    Returns:
        Number of watcher emissions delivered (deduped per watcher).
    """
    if payload is None:
        payload = _pack_legacy_kwargs(
            work_id=work_id,
            status=status,
            mission_work_ids=mission_work_ids,
            instance_manager=instance_manager,
            work_resolver=work_resolver,
            watcher_repo=watcher_repo,
            progress=progress,
            result_summary=result_summary,
            error=error,
        )
    if payload.status not in _MISSION_SCOPED_QA_STATUSES:
        logger.warning(
            "notify_mission_qa_watchers: refused non-QA status=%s for "
            "work_id=%s - mission-scoped QA lane is events-exempt and "
            "non-claiming by contract; terminal delivery stays on "
            "notify_work_watchers",
            payload.status,
            payload.work_id[:8] if payload.work_id else "<none>",
        )
        return 0
    if payload.instance_manager is None or payload.watcher_repo is None:
        logger.debug(
            "notify_mission_qa_watchers: missing dependency for "
            "work_id=%s status=%s - skipping (instance_manager=%s, "
            "watcher_repo=%s)",
            payload.work_id[:8] if payload.work_id else "<none>",
            payload.status,
            payload.instance_manager is not None,
            payload.watcher_repo is not None,
        )
        return 0
    candidate_ids = _build_candidate_ids(payload)
    watchers = await _select_watchers(payload, candidate_ids)
    if not watchers:
        return 0
    agent_id = await _resolve_agent_id(payload)
    rows_by_instance = _group_rows_by_instance(watchers)
    status_display = _format_status_display(payload.status)
    notified = await _enqueue_for_watchers(
        payload=payload,
        candidate_ids=candidate_ids,
        rows_by_instance=rows_by_instance,
        agent_id=agent_id,
        status_display=status_display,
    )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "[qa-fanout] ts=%d status=%s primary=%s candidates=%d "
            "rows=%d watchers=%d delivered=%d",
            time.time_ns(),
            payload.status,
            payload.work_id[:8] if payload.work_id else "<none>",
            len(candidate_ids),
            len(watchers),
            len(rows_by_instance),
            notified,
        )
    return notified

__all__ = [
    "NotifyQAPayload",
    "QA_WATCHER_ROW_CAP",
    "notify_mission_qa_watchers",
    "notify_work_watchers",
]