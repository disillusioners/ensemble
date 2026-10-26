"""Periodic report-delivery recovery service.

Phase 2 (pause-report-recovery, tasks 2.4 + 2.5 + 2.6). The recovery
sweep is a periodic background thread that drains every recoverable
report-delivery obligation across the system. SEVEN lanes run in a
single sweep pass; each lane is independently kill-switchable via
config (``lane_deferred``, ``lane_no_row_backstop``,
``lane_pending_age``, ``lane_recovery_retry``, ``lane_orphan``,
``lane_stuck_wake``, ``lane_mission_reconciliation``).

════════════════════════════════════════════════════════════════════
W-2 DO NOT CALL (durability-f1-f2 / phase2 task 2.9)
════════════════════════════════════════════════════════════════════

The following seams are documented as **NEVER** direct-call
entry points from any F-2 recovery lane. The RDRS chain is the
ONLY sanctioned path (see ``decisions.md §12d`` + §12a (B3)):

* **DO NOT** make a **DIRECT** call into
  ``ChildReportsService._process_child_completion_and_notify_parent(
  self, instance_id, completed_message_id)`` at
  ``child_reports.py:2458`` — takes the **CHILD** id first; passing
  the parent id silently no-ops on the root branch
  (``deferred_waiting_children`` → SSE-only at
  ``child_reports.py:2828-2854, :4481-4495``) or mints a report to
  the wrong grandparent for non-root parents. **The prohibition
  is on DIRECT calls; the transitive re-entry into
  ``_process_child_completion_and_notify_parent``
  POST-materialization, through the sanctioned RDRS chain, is NOT
  prohibited** — the artifacts (MessageQueue READY +
  PROCESS_REPORT PENDING) are minted in one txn at
  ``manager.py:9178-9331`` BEFORE re-entry, and the
  ``child_reports.py:2773-2787`` child-status guard returns
  ``idempotency_skip`` (no exception, no erase, no duplicate). The
  transitive re-entry is the 5th hop of the chain listed in
  §12a (B3) and is the only path by which the parent-wake claim
  reaches the WAITING_CHILDREN exception at
  ``task/repository.py:2789-2830``.

* **DO NOT** wrap any public ``ChildReportsService`` entry in
  ``bus._get_parent_lock`` — ``_get_parent_lock`` at
  ``dependency_bus.py:1627-1665`` constructs a **plain
  non-reentrant** ``asyncio.Lock()`` (``:1664``); same-task
  re-acquire blocks forever with no timeout. The public entry
  self-acquires the same key at ``child_reports.py:2563``; wrapping
  it deadlocks deterministically.

* **DO NOT** call the async twin
  ``_handle_recover_deferred_report_async`` (``manager.py:8393``)
  from the sweep thread — it is loop-only. The sweep-side seam is
  the **SYNC** ``_handle_recover_deferred_report`` (``manager.py:8487``,
  "Sweep-side entry point" docstring at ``:8497``, calls
  ``_reconcile_deferred_report`` at ``:8546``, bridges re-entry via
  ``run_coroutine_threadsafe(...).result(8.0)`` at ``:8586-8596``).

* **DO NOT** call ``_dispatch_post_commit_side_effects``
  (``child_reports.py:4435-4440``) standalone — its input is a
  ``_ChildCompletionDbResult`` produced only by the db-sync helper;
  standalone caller must synthesize the object + hand-pick an
  outcome. Pure post-commit dispatcher; holds NO transaction context.

The transitive re-entry path is the ONLY sanctioned way to
deliver a child completion to a waiting_children parent from
this module. Any direct call into the four prohibited seams
above is a deterministic deadlock (b), a silent no-op or
wrong-grandparent mint (a), an asyncio loop violation (c), or a
context-less dispatcher call (d).

**LANE-6 EXCEPTION (G4-r4, 2026-10-05; commit this commit,
 ``decisions.md §12d`` companion note; supersedes the
 G4-r3 exception at commit ``4b601955``).** The seam-(a)
 blanket prohibition above is UNCHANGED for every other
 caller. **The ONE explicit exception is
 ``_run_stuck_wake_lane``** (this module, at :1041 —
 the dispatch site), which calls
 ``self._manager.enqueue_message(parent_id, message,
 source="system:wedge-resolve", priority=1)`` DIRECTLY from
 the sweep thread via the sanctioned manager-loop bridge
 (``run_coroutine_threadsafe(...).result(8.0)`` — the same
 pattern as ``_handle_recover_deferred_report`` at
 ``manager.py:8586-8596`` and the revival seam at
 ``manager.py:9118``).

  **Why ``enqueue_message`` (and not the G4-r3 call to
  ``_process_child_completion_and_notify_parent``):** the
  round-3 primitive short-circuits on the FROZEN
  ``child_reports.py:2773-2787`` idempotency guard (the
  child is ALREADY terminal — completed naturally pre-kill
  — so the guard returns ``idempotency_skip`` and the
  primitive's step-3 wake row is never created). The
  wake-only primitive (``enqueue_message``) does NOT hit
  that guard: it auto-resumes the parent to RUNNING via
  ``instance_messaging._prepare_enqueued_message`` at
  ``:1968`` (``waiting_children → RUNNING``) and notifies
  the worker pool via ``safe_notify_all_pools`` — exactly
  the seam the MANUAL PING uses (the F-3 LEG 1 tester
  evidence proved a manual ping completes cleanly from this
  exact state).

  **Sanctioned because:**

  (i) The artifacts already exist — minted by the NATURAL
      flow pre-crash and preserved across the wipe (the
      ``report_injections`` row is in state ``task_delivered``
      / ``pending``; the wake row is in ``message_queue`` with
      status ``ready`` preserved by the F-1 epoch-belt). This
      call does NOT mint new artifacts — it dispatches the
      parent graph turn for the artifacts the natural flow
      already created and the dead worker died before
      delivering.

  (ii) The ``enqueue_message`` call is test-locked —
       ``tests/unit/test_report_delivery_recovery_service.py::
       TestG4R3StuckWakeParentScheduleSeam::
       test_lane6_heal_dispatches_parent_via_ping_seam_and_turn_runs``
       asserts (a) the call fires exactly once with source
       ``system:wedge-resolve`` (the manual-ping provenance);
       (b) the cross-loop seam (the coroutine runs on the
       manager loop, not an ephemeral ``asyncio.run`` loop);
       (c) the parent's ``instances.status`` row transitions
       OUT OF ``waiting_children`` on the seeded LIVE state
       (the EFFECT-level pin — the dispatch says NOT a
       dispatch-call assertion, the round-3 pin was the
       4th consecutive LESSONS-#6 instance; this one is
       effect-level).

  (iii) The primitive is idempotent / dedup-absorbing —
        ``enqueue_message`` mints a fresh UUID for every new
        message; the wake row's original UUID is preserved.
        The retry's claim path stays unchanged (the parent's
        RUNNING transition unblocks the per-instance busy
        guard; the worker's ``already_delivered`` skip is the
        dedup contract working as designed).

  (iv) The round-4 leader commission explicitly authorized
       this seam ("wake-only dispatch via the ping seam" —
       see the G4-r4 commission brief + the manual-ping-seam
       identification at
       ``instance_messaging.py:2108`` / ``manager.py:7987``).

  **Without this exception, the captured-wedge shape (marker
  TASK_DELIVERED + wake row ready + dead-worker wake task +
  parent waiting_children — the F-4 LEG 1 evidence) is
  unreachable: the retry's claim sees ``already_delivered``
  and skips per the dedup contract, and the round-3
  primitive's idempotency guard short-circuits before the
  wake row is created. The next review would flag this call
  as a seam-(a) violation; a future cleanup would remove the
  heal. This exception keeps the doc in lockstep with the
  G4-r4 sanctioned behavior.**

════════════════════════════════════════════════════════════════════

Service shape (matches the ``StaleTaskRecovery`` precedent):

* ``__init__`` accepts the task / report-injection / queue / instance
  repositories and a ``manager_ref`` for the ``has_instance_busy``
  gate + child-completion re-entry.
* :meth:`start` launches the daemon thread; :meth:`stop` terminates
  it cleanly.
* :meth:`recover_on_startup` runs ONE sweep pass at boot (fire-and-
  forget — the sweep is idempotent).
* :meth:`recover_now` runs ONE sweep pass synchronously; the
  ``recover_report_delivery`` crash-recovery endpoint calls this and
  returns structured per-row results.

Seven lanes (v3, no one-time-only lanes — W9, no-row backstop
designed-in — C3, terminal-parent ORPHAN — W1 — the G4 stuck-wake
heal — and the Proposal B PR3 mission-commission reconciliation):

1. **DEFERRED lane** — ``find_deferred_for_parent_all(parent_not_terminal=True)``
   transitions each row to PENDING, reconciles artifacts, and re-enters
   child completion. Skip busy parents; per-row errors leave rows DEFERRED
   (retried next cycle).

2. **NO-ROW BACKSTOP lane (C3)** —
   ``find_completed_children_without_delivery(parent_not_terminal=True)``.
   The designed-from-scratch query (5 LEFT JOINs / NOT EXISTS
   subqueries — see ``repository.py`` docstring for the false-positive
   matrix). Catches FM-11 escapes, cancel-mid-shield, and any future
   no-marker drop lane. For each row: ``ensure_deferred`` (W6 absorbs
   duplicates), then ``transition_deferred_to_pending`` + reconcile +
   re-enter (D2 end-state alignment with Lanes 1/3/4).

3. **Age-bounded PENDING lane (W9)** — ``find_pending_past_age``
   without a ``recovery_attempted_at`` predicate: stranded PENDING
   rows that the recovery actors never stamped. Per row: same path
   as DEFERRED lane, but the row is already PENDING (no transition
   needed — just reconcile + re-enter).

4. **``recovery_attempted_at`` retry lane (W9/FM-13)** — same query
   as lane 3, but with the ``recovery_attempted_at < now - retry``
   predicate active: stamped-stale rows that need another pass.
   Closes the mid-sweep-crash gap.

5. **ORPHAN lane (W1)** — ``find_deferred_for_parent_all(parent_not_terminal=False)``
   filtered to terminal parents. Per row: try revival (instance_messaging
   precedent at line 1486) first; on revival failure, log + structured
   metric disposition (never silent — observed by test 3.6).

6. **STUCK-WAKE lane (G4)** — the destructive heal: a dead worker's
   preserved wake row is force-cancelled and the parent is woken
   through the sanctioned manager-loop ping seam.

7. **MISSION-RECONCILIATION lane (Proposal B PR3)** — the ESCAPE VALVE
   for the eternal-mission class: an outstanding
   ``mission_commissions`` row whose commissioned work will never
   produce a report holds its mission open forever (the D2(c)
   settle-hold refuses to settle while ``outstanding_commissions >
   0``). This lane resolves the provably-dead rows to ``abandoned``
   (and the provably-delivered ones to ``satisfied``) under a
   FAIL-MISS posture: every probe error HOLDS the row, and the TTL is
   the one time-bounded exception. It writes ONLY
   ``mission_commissions``.

Per-row invariants (every lane):

* Skip if ``has_instance_busy(parent_id)`` — a live parent turn wins
  (the natural path will drain the report when its turn resumes).
* TOCTOU re-check inside the claim — re-fetch the injection row
  state and the parent's status before mutating.
* ``transition_deferred_to_pending`` is rowcount-guarded (rowcount=0
  = another actor already recovered → skip).
* ``ensure_deferred`` absorbs ``IntegrityError`` on the obligation-
  triple index (W6 — the router/sweep NEVER see raw IntegrityError).
  Debug Phase 4 (2026-09-07): ``ensure_deferred`` is INSERT-ON-
  MISSING — a post-rollback re-read finding ZERO rows inserts a
  fresh DEFERRED marker (absence is never "already delivered");
  ``None`` now requires positive evidence (a duplicate non-terminal
  row or a terminal delivered row).
* Re-enter ``_process_child_completion_and_notify_parent`` under
  per-instance S3 serialization.
* Per-row errors leave rows DEFERRED (no rollback to terminal —
  retried next cycle).
* Mid-sweep crash after transition → fresh PENDING row caught by
  lane 3/4 next cycle.

Bounds (S-e defaults):

* ``age_bound_minutes=10`` — minimum age before a row is eligible.
* ``batch_cap=100`` — max rows per lane per run; remainder logged
  and re-claimed next cycle.
* ``recovery_retry_minutes=1`` — lane 4 retry interval (proposed
  default per S-e).
* ``interval_seconds=300`` — periodic interval (5 min default).
* ``enabled=true`` — config-gated master switch.

Crash-recovery endpoint (task 2.5):

* ``POST /api/recovery/recover_report_delivery`` calls
  :meth:`recover_now` and returns structured per-row results in the
  shape::

    {
      "lanes": {
        "deferred": {"recovered": N, "skipped_busy": N, "busy_check_failed": N, "already_recovered": N, "errors": N},
        "no_row_backstop": {...},
        "pending_age": {...},
        "recovery_retry": {...},
        "orphan": {"recovered": N, "skipped_busy": N, "busy_check_failed": N, "orphan_disposition": N, ...},
      },
      "total_recovered": N,
    }

  ``busy_check_failed`` distinguishes "busy-check itself errored
  (fail-closed skip)" from ``skipped_busy`` ("parent genuinely busy")
  — both skip the row, but the former signals transient infra
  problems worth investigating.

Configuration (task 2.6): see ``daemon/config.py`` ``ServicesConfig``
— ``report_delivery_recovery_*`` knobs (interval, age bound, batch
cap, retry minutes, enabled, per-lane kill-switches).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from typing import TYPE_CHECKING, Any

from daemon.constants import (
    DEFERRED_REASON_RESUME_ROUTER,
    MISSION_COMMISSION_ABANDON_REASONS,
)
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import ACTIVE_ADMISSION_STATES
from daemon.services.report_recipient import (
    parse_iso_utc,
    resolve_report_recipient,
)
from daemon.services.timestamps import now_utc, now_utc_naive

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.repositories.report_injection.repository import (
        ReportInjectionRepository,
    )

logger = logging.getLogger(__name__)


# ── F-2 (durability-f1-f2 / phase2, tasks 2.5 + 2.6) helpers ────────────────


async def _derive_anchor_less_child_message_id(
    checkpointer: Any,
    child_id: str,
) -> str | None:
    """Derive the anchor-less child's ``message_id`` from the checkpoint.

    F-2 (durability-f1-f2 / phase2 task 2.6). For anchor-less
    children (the ``has_anchor`` flag from the lane-2 query is
    ``False``), the ``child_msg_id`` from the anchor subquery is
    ``NULL`` and the derivation must come from the surviving child
    checkpoint via the ``serialize_message`` chain (W-1 fallback
    (ii) per ``decisions.md §14a``).

    Implementation:
      1. ``get_instance_messages(checkpointer, child_id, manager=None)``
         (manager=None per the W-5 polarity check — no synthetic
         system-prompt injection needed for id extraction).
      2. Find the last assistant message that carries BOTH a
         truthy ``message_id`` AND non-empty content (the plan-2.6
         empty-content guard: an empty-content assistant message is
         not a derivable report anchor — skip it and keep scanning
         older assistant messages).
      3. Return the ``message_id``; ``None`` if the child has
         no qualifying message (no messages, missing checkpoint,
         or every assistant message lacks an id or has empty
         content).

    The derived id is ``BaseMessage.id`` (UUID4) —
    STABLE-BUT-DIFFERENT from the natural path's
    ``MessageQueue.message_id`` (per W-1 confirmation). The
    PREFIX ledger (step 0 in ``_recover_one_no_row``) is the
    operative cross-path dedup; the id mismatch is by design.

    LOOP AFFINITY (verification iteration 2, blocker 1): this
    coroutine MUST be driven on the manager's event loop (via
    ``ReportDeliveryRecoveryService._run_async_on_manager_loop``) —
    production checkpointers (``AsyncSqliteSaver`` /
    ``AsyncPostgresSaver``) hold loop-bound ``asyncio.Lock`` s, so
    an ephemeral ``asyncio.run`` loop raises cross-loop on every
    real read.
    """
    from .. import persistence as _persistence

    if checkpointer is None:
        return None
    # ``_persistence`` is bound to the ``daemon.persistence``
    # module, so unit tests can patch
    # ``daemon.persistence.get_instance_messages`` at the module
    # level (the binding target is the same module either way).
    messages = await _persistence.get_instance_messages(
        checkpointer, child_id, manager=None
    )
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        msg_id = msg.get("message_id")
        if not msg_id:
            # Plan-2.6: missing id → not derivable from this
            # message; keep scanning older assistant messages.
            continue
        if not msg.get("content"):
            # Plan-2.6: empty content → not a derivable report
            # anchor; log for operator visibility and keep
            # scanning older assistant messages. The check is
            # falsy-scope (skips falsy values including 0/[]); in
            # practice content is a string here.
            logger.warning(
                "sweep no_row_backstop anchor-less derivation "
                "skipped an assistant message with empty content "
                f"(child={child_id[:8]}..., "
                f"msg_id={str(msg_id)[:8]}...); scanning older "
                "messages"
            )
            continue
        return str(msg_id)
    return None


# Defaults — overridable via ``ServicesConfig`` (task 2.6). Kept
# module-level so the constants have a single source of truth and
# config can reference them in the pydantic Field default factory.
DEFAULT_RECOVERY_INTERVAL_SECONDS: int = 300
DEFAULT_AGE_BOUND_MINUTES: int = 10
DEFAULT_BATCH_CAP: int = 100
DEFAULT_RECOVERY_RETRY_MINUTES: int = 1


# Parent terminal set (matches the bus/pause-cascade selector
# terminal set + FAILED for task-level failures).
_PARENT_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        InstanceStatus.COMPLETED.value,
        InstanceStatus.ERROR.value,
        InstanceStatus.TERMINATED.value,
        InstanceStatus.FAILED.value,
    }
)


# ── Lane 7 (Proposal B PR3) — the mission-commission reconciliation ──
#
# The mode-ladder tokens (the SAME ladder the rest of the subsystem
# uses — the sweep does NOT invent a second mode knob).
MISSION_RECORDS_MODE_OFF = "off"
MISSION_RECORDS_MODE_DRY = "dry"
MISSION_RECORDS_MODE_ENFORCE = "enforce"

# The commission-state tokens the sweep writes (``superseded`` is
# RESERVED to the mint lanes and is never written by the sweep).
MISSION_COMMISSION_STATE_SATISFIED = "satisfied"
MISSION_COMMISSION_STATE_ABANDONED = "abandoned"

# The FROZEN abandon-reason vocabulary (the design's R9 / OQ-4).
MISSION_ABANDON_REASON_WORKER_TERMINAL = "worker_terminal_no_report"
MISSION_ABANDON_REASON_TTL = "ttl_expired_not_live"
MISSION_ABANDON_REASON_WORKER_MISSING = "worker_row_missing"

# The worker-terminal set for R1 — the host's OWN parent-terminal
# family (``_PARENT_TERMINAL_STATUSES``: COMPLETED / ERROR /
# TERMINATED / FAILED). ``PAUSED`` / ``WAITING_CHILDREN`` / ``RUNNING``
# are NON-terminal: a paused worker is temporarily idle, not dead.
_MISSION_COMMISSION_TERMINAL_STATUSES: frozenset[str] = (
    _PARENT_TERMINAL_STATUSES
)


class MissionReconciliationProbeError(RuntimeError):
    """A lane-7 probe could not RUN (an unwired dependency).

    Distinct from a probe that ran and answered "no": a probe that
    cannot run must HOLD the row (P1 — a probe that cannot prove its
    predicate must never authorize the destructive transition).
    """


@dataclass
class LaneResult:
    """Per-lane aggregated counts for a single sweep pass.

    Each lane returns this shape to the caller (the endpoint /
    tests). ``skipped_busy`` counts parents that had a live task
    when the sweep checked; ``busy_check_failed`` counts parents
    whose busy-check itself errored (the helper's fail-closed
    default — operators can distinguish "parent genuinely busy"
    from "busy-check itself failed" via the two counters);
    ``already_recovered`` counts rows where
    ``transition_deferred_to_pending`` returned ``False`` (another
    actor won the race); ``skipped_already_reported`` counts
    rows where the F-2 PREFIX ledger (task 2.5) matched a
    parent-side ``internal_report:{child}:%`` delivery-evidence
    row (the natural path already delivered or is in-flight —
    the sweep defers); ``errors`` counts per-row exceptions that
    left the row in a recoverable state.
    """

    recovered: int = 0
    skipped_busy: int = 0
    busy_check_failed: int = 0
    already_recovered: int = 0
    orphan_disposition: int = 0
    # F-2 (durability-f1-f2 / phase2 task 2.5): PREFIX-ledger
    # cross-path dedup match count. The lane did NOT mint a
    # report because the natural path already delivered or is
    # in-flight; the row is left in its current state and the
    # periodic 300s loop is the retry mechanism.
    skipped_already_reported: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "recovered": self.recovered,
            "skipped_busy": self.skipped_busy,
            "busy_check_failed": self.busy_check_failed,
            "already_recovered": self.already_recovered,
            "orphan_disposition": self.orphan_disposition,
            "skipped_already_reported": self.skipped_already_reported,
            "errors": self.errors,
        }


@dataclass
class MissionReconciliationLaneResult(LaneResult):
    """Lane 7's counters (the design's R9 set).

    A SUBCLASS of :class:`LaneResult` (so the shared
    ``recovered`` / ``errors`` fields and the sweep's aggregation keep
    working) with the lane-7-specific counters added. The endpoint
    surfaces this dict verbatim — the operator sees the sweep's
    dispositions per row class.

    Counter semantics:

    * ``abandoned_worker_terminal`` / ``abandoned_ttl`` /
      ``abandoned_worker_missing`` — the DETECTED abandonments (the
      three frozen abandon reasons). They move in ``dry`` mode too
      (the dry-run evidence that tunes the TTL default) and in
      ``enforce`` mode they mean "the row was actually abandoned".
    * ``satisfied_on_evidence`` — R1b (the delivery happened; the
      natural satisfy write was lost).
    * ``skipped_live`` — the worker is live (R2a: young/live work beats
      the clock).
    * ``skipped_watcher_pending`` — a PENDING bus watcher exists for
      the (commissioner, worker) pair (R4): the row is HELD. The hold
      is a PER-PASS decision, re-evaluated against the current watcher
      set on every cycle (MAJ-5) — it is not an eternal hold.
    * ``below_ttl_hold`` — the row is young (or the worker is
      non-terminal and the TTL has not elapsed): HOLD, retried next
      cycle.
    * ``already_resolved`` — another actor won the race (the guarded
      transition's rowcount was 0). NEVER retried in place.
    * ``masked_suspected`` — the D4(iii) masked-enqueue signal
      (observability only — never an auto-action).
    * ``probe_errors`` — the probes that could not prove their
      predicate (P1: the row was HELD).
    * ``masked_probe_errors`` — the MASKED-ENQUEUE probe's own faults
      (MAJ-3: the masked probe is an OBSERVABILITY probe, so its fault
      has its OWN counter — it no longer borrows ``probe_errors``, whose
      contract says "the row was HELD"). A masked-probe fault HOLDS
      the row for that pass (the masked signal never authorises an
      abandon or a satisfy).
    """

    abandoned_worker_terminal: int = 0
    abandoned_ttl: int = 0
    abandoned_worker_missing: int = 0
    satisfied_on_evidence: int = 0
    skipped_live: int = 0
    skipped_watcher_pending: int = 0
    below_ttl_hold: int = 0
    already_resolved: int = 0
    masked_suspected: int = 0
    probe_errors: int = 0
    masked_probe_errors: int = 0

    def to_dict(self) -> dict[str, int]:
        base = super().to_dict()
        base.update(
            {
                "abandoned_worker_terminal": self.abandoned_worker_terminal,
                "abandoned_ttl": self.abandoned_ttl,
                "abandoned_worker_missing": self.abandoned_worker_missing,
                "satisfied_on_evidence": self.satisfied_on_evidence,
                "skipped_live": self.skipped_live,
                "skipped_watcher_pending": self.skipped_watcher_pending,
                "below_ttl_hold": self.below_ttl_hold,
                "already_resolved": self.already_resolved,
                "masked_suspected": self.masked_suspected,
                "probe_errors": self.probe_errors,
                "masked_probe_errors": self.masked_probe_errors,
            }
        )
        return base


@dataclass
class SweepResult:
    """Top-level structured result of a single sweep pass.

    Per-lane results + a top-level ``total_recovered`` sum. Used by
    the crash-recovery endpoint to surface what happened; the
    periodic sweep logs the same shape.
    """

    lanes: dict[str, LaneResult] = field(default_factory=dict)
    total_recovered: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "lanes": {name: lane.to_dict() for name, lane in self.lanes.items()},
            "total_recovered": self.total_recovered,
        }


class ReportDeliveryRecoveryService:
    """Periodic recovery sweep with SEVEN lanes (pause-report-recovery
    Phase 2 + the G4 stuck-wake heal + the Proposal B PR3
    mission-commission reconciliation).

    Mirrors :class:`daemon.services.stale_task_recovery.StaleTaskRecovery`
    (thread + ``_stop_event`` pattern; ``start`` / ``stop``; ``recover_on_startup``
    fire-and-forget at boot). All DB writes go through the injected
    repositories; the per-row re-entry uses the ``manager_ref`` to call
    ``_process_child_completion_and_notify_parent`` on the event loop.
    """

    def __init__(
        self,
        task_repo: Any,
        report_injection_repo: ReportInjectionRepository,
        queue_repo: Any,
        instance_repo: Any,
        manager_ref: "InstanceManager",
        *,
        interval_seconds: int = DEFAULT_RECOVERY_INTERVAL_SECONDS,
        age_bound_minutes: int = DEFAULT_AGE_BOUND_MINUTES,
        batch_cap: int = DEFAULT_BATCH_CAP,
        recovery_retry_minutes: int = DEFAULT_RECOVERY_RETRY_MINUTES,
        enabled: bool = True,
        lane_deferred: bool = True,
        lane_no_row_backstop: bool = True,
        lane_pending_age: bool = True,
        lane_recovery_retry: bool = True,
        lane_orphan: bool = True,
        lane_stuck_wake: bool = True,
        lane_mission_reconciliation: bool = True,
        mission_record_repo: Any = None,
        mission_record_service: Any = None,
        dependency_bus: Any = None,
        job_repo: Any = None,
        mission_commission_ttl_seconds: int | None = None,
    ) -> None:
        """Initialize the recovery service.

        Args:
            task_repo: Task repository (``has_instance_busy``).
            report_injection_repo: The Phase 1 + 2
                :class:`ReportInjectionRepository`.
            queue_repo: Message-queue repository (currently unused;
                kept in the signature for future sweep lanes that
                need queue introspection — e.g. a "stuck READY"
                lane).
            instance_repo: Instance repository (currently unused;
                kept in the signature for the same reason as
                ``queue_repo``).
            manager_ref: The :class:`InstanceManager` — used for the
                ``has_instance_busy`` gate + the
                ``_process_child_completion_and_notify_parent``
                re-entry.
            interval_seconds: Periodic sweep interval.
            age_bound_minutes: Minimum age before a row is eligible
                for recovery (Lane 1 + 3 + 4).
            batch_cap: Maximum rows per lane per run.
            recovery_retry_minutes: Lane 4 retry interval (rows
                stamped ``recovery_attempted_at`` younger than this
                are skipped).
            enabled: Master kill-switch.
            lane_deferred: Lane 1 kill-switch (default ``True``).
            lane_no_row_backstop: Lane 2 kill-switch (default
                ``True``).
            lane_pending_age: Lane 3 kill-switch (default ``True``).
            lane_recovery_retry: Lane 4 kill-switch (default
                ``True``).
            lane_orphan: Lane 5 kill-switch (default ``True``).
            lane_stuck_wake: Lane 6 kill-switch (default ``True``).
            lane_mission_reconciliation: Lane 7 kill-switch (default
                ``True``) — the mission-commission reconciliation sweep
                (Proposal B PR3).
            mission_record_repo: The duck-typed PR1 mission-record
                repository. Lane 7's ADMISSION read lives there.
                ``None`` (or a repository without
                ``find_outstanding_commissions``) → the lane logs ONE
                INFO line and returns ZERO (the unwired-degrade
                contract, the ``force_cancel_and_schedule_retry``
                precedent) — it never reads and never writes.
            mission_record_service: The mission-record SERVICE — lane
                7's WRITER (MAJ-6: the sweep resolves commissions
                through ``MissionRecordService.satisfy_commission`` /
                ``abandon_commission``, the ONE guarded UPDATE that
                owns the ``resolved_at`` / ``satisfied_at`` stamps).
                Unwired → the lane is inert in ``enforce`` (it cannot
                write, and a lane that detects but cannot write would
                report abandonments that never happened).
            dependency_bus: The duck-typed dependency bus (lane 7's
                PENDING-watcher probe). Unwired → the probe HOLDS the
                row (fail-MISS), it never assumes absence.
            job_repo: The duck-typed job repository (lane 7's
                JobItem-live probe, OQ-7). Unwired → the probe HOLDS
                the row (fail-MISS).
            mission_commission_ttl_seconds: The commission TTL horizon
                (the time-bounded fail-MISS backstop). ``None`` → the
                ``ENSEMBLE_MISSION_COMMISSION_TTL_SECONDS`` env knob
                (default 21600s = 6h).
        """
        self._task_repo = task_repo
        self._report_injection_repo = report_injection_repo
        self._queue_repo = queue_repo
        self._instance_repo = instance_repo
        self._manager = manager_ref
        self._interval_seconds = max(1, int(interval_seconds))
        self._age_bound = timedelta(minutes=max(0, int(age_bound_minutes)))
        self._batch_cap = max(1, int(batch_cap))
        self._recovery_retry_minutes = max(0, int(recovery_retry_minutes))
        self._enabled = bool(enabled)
        self._lane_deferred = bool(lane_deferred)
        self._lane_no_row_backstop = bool(lane_no_row_backstop)
        self._lane_pending_age = bool(lane_pending_age)
        self._lane_recovery_retry = bool(lane_recovery_retry)
        self._lane_orphan = bool(lane_orphan)
        self._lane_stuck_wake = bool(lane_stuck_wake)
        self._lane_mission_reconciliation = bool(lane_mission_reconciliation)
        self._mission_record_repo = mission_record_repo
        self._mission_record_service = mission_record_service
        self._dependency_bus = dependency_bus
        self._job_repo = job_repo
        self._mission_commission_ttl_seconds = (
            self._resolve_commission_ttl_seconds(mission_commission_ttl_seconds)
        )
        # The unwired-degrade INFO line is logged ONCE per service
        # lifetime (not once per sweep pass).
        self._mission_lane_degrade_logged = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _resolve_commission_ttl_seconds(configured: int | None = None) -> int:
        """Resolve the commission TTL horizon.

        PRECEDENCE: the ``ENSEMBLE_MISSION_COMMISSION_TTL_SECONDS`` env
        knob (the operator lever) wins over the configured default; with
        neither, the house default (21600s = 6h) applies — the same
        horizon family as ``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS`` (the
        one-horizon principle).

        The resolver is the house-switch pattern (restart-read, cached)
        and NEVER raises: a misconfigured knob must not strand every
        commission (the exact failure the sweep exists to prevent).
        """
        from daemon.constants import DEFAULT_MISSION_COMMISSION_TTL_SECONDS

        fallback = int(
            configured
            if configured is not None
            else DEFAULT_MISSION_COMMISSION_TTL_SECONDS
        )
        try:
            from daemon.services.mission_record_service import (
                resolve_mission_commission_ttl_seconds,
            )

            return int(resolve_mission_commission_ttl_seconds(default=fallback))
        except Exception as exc:  # noqa: BLE001 — never break the sweep
            logger.warning(
                "mission_records: the commission TTL knob could not be "
                "resolved (%s) — using the %ss default",
                exc,
                fallback,
            )
            return fallback

    # --------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """The MASTER kill-switch state (read-only).

        The public read of the same fact the sweep's own gate uses
        (``self._enabled``). The crash-recovery endpoint reads it to
        honour its documented 503 contract ("the recovery service is
        disabled by config") WITHOUT a second copy of the switch: the
        service stays the single source of truth, the router only
        READS it.

        The service is CONSTRUCTED even when the switch is off (the
        wiring passes ``enabled=`` through), so "wired" and "enabled"
        are two different facts — a caller that only checks for
        ``None`` sees a disabled service as a healthy one.
        """
        return self._enabled

    def start(self) -> None:
        """Start the periodic background thread.

        Idempotent — calling ``start`` while the thread is already
        running is a no-op (mirrors ``StaleTaskRecovery.start``).
        """
        if not self._enabled:
            logger.info(
                "ReportDeliveryRecoveryService disabled by config; skipping start"
            )
            return
        if self._thread is not None and self._thread.is_alive():
            logger.warning(
                "ReportDeliveryRecoveryService already running"
            )
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="ReportDeliveryRecoveryService",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "ReportDeliveryRecoveryService started: "
            f"interval={self._interval_seconds}s, "
            f"age_bound={int(self._age_bound.total_seconds() // 60)}min, "
            f"batch_cap={self._batch_cap}, "
            f"retry={self._recovery_retry_minutes}min, "
            f"lanes=[deferred={self._lane_deferred}, "
            f"no_row_backstop={self._lane_no_row_backstop}, "
            f"pending_age={self._lane_pending_age}, "
            f"recovery_retry={self._lane_recovery_retry}, "
            f"orphan={self._lane_orphan}, "
            f"stuck_wake={self._lane_stuck_wake}, "
            f"mission_reconciliation={self._lane_mission_reconciliation}]"
        )

    def stop(self, timeout: float | None = None) -> None:
        """Stop the periodic background thread.

        Honest worst-case join budget (F4, 2026-08-20):

        W3 aligned every per-step ``.result(timeout=8.0)`` in the
        sweep to 8s (2s headroom under the prior 10s ``stop()``
        budget). A single per-row path (ORPHAN lane → revival →
        re-enter) chains THREE ``run_coroutine_threadsafe(...).result``
        bridges at 8s each — worst-case 24s. The previous default
        ``timeout=10.0`` was therefore a LIE: ``thread.join`` could
        expire mid-.result() and orphan the daemon thread on
        shutdown.

        Default ``timeout`` is now computed as
        ``max(3 * 8.0 + 4.0, 10.0) == 28.0s`` — covers the
        worst-case chain (3 bridges × 8s = 24s + 4s for the
        inter-bridge DB / TOCTOU work) with margin, AND floors at
        the prior 10s budget so callers passing ``timeout=None``
        never join-faster than the original.

        The per-row loop is ALSO interruptible (F4, 2026-08-20):
        between every row the worker checks ``self._stop_event``
        and exits the batch promptly — ``stop()`` returns without
        needing the full join budget on a polite shutdown.

        Args:
            timeout: ``None`` (use the auto-computed worst-case
                budget) or an explicit float in seconds. The
                ``timeout=10.0`` default from the prior revision
                is REPLACED — callers that depended on the literal
                ``10.0`` should pass ``timeout=10.0`` explicitly.
        """
        self._stop_event.set()
        if self._thread is not None:
            join_timeout = (
                timeout
                if timeout is not None
                else max(3 * 8.0 + 4.0, 10.0)
            )
            self._thread.join(timeout=join_timeout)
            if self._thread.is_alive():
                logger.warning(
                    "ReportDeliveryRecoveryService.stop: thread did "
                    "not exit within %.1fs join budget — daemon thread "
                    "left running (shutdown will reap on process exit)",
                    join_timeout,
                )
            self._thread = None
        logger.info("ReportDeliveryRecoveryService stopped")

    def _run_loop(self) -> None:
        """Main loop: periodic sweep passes."""
        while not self._stop_event.is_set():
            try:
                result = self._run_all_lanes_sync()
                if result.total_recovered > 0:
                    logger.info(
                        f"ReportDeliveryRecoveryService sweep: "
                        f"recovered={result.total_recovered}, "
                        f"lanes={result.to_dict()['lanes']}"
                    )
            except Exception as exc:
                logger.error(
                    f"ReportDeliveryRecoveryService sweep error: {exc}",
                    exc_info=True,
                )
            self._stop_event.wait(timeout=self._interval_seconds)

    def recover_on_startup(self) -> SweepResult:
        """Run one sweep pass at boot (fire-and-forget).

        Called from :class:`InstanceManager._wire_post_migrate_services`
        AFTER ``_ensure_postgres_columns`` + ``StaleTaskRecovery``
        (binding order S-c — the report-injections table must have
        its Phase 1 columns + indexes BEFORE the sweep queries it).

        Mirrors ``StaleTaskRecovery.recover_on_startup``. Idempotent.
        """
        if not self._enabled:
            logger.info(
                "ReportDeliveryRecoveryService disabled by config; "
                "skipping startup sweep"
            )
            return SweepResult()
        return self._run_all_lanes_sync()

    # --------------------------------------------------------
    # On-demand sweep (crash-recovery endpoint, task 2.5)
    # --------------------------------------------------------

    def recover_now(self) -> SweepResult:
        """Run one sweep pass synchronously.

        Used by the ``POST /api/recovery/recover_report_delivery``
        crash-recovery endpoint. Returns the structured per-lane
        results the endpoint surfaces.

        The endpoint runs ``recover_now`` on the asyncio event loop
        via ``asyncio.to_thread`` so the DB queries do not block
        the loop. The sweep itself is sync (mirrors
        ``StaleTaskRecovery.recover_stale_tasks``).

        **MAJ-2 — the MASTER kill-switch is honoured here.** The
        service is CONSTRUCTED even when the switch is off (the wiring
        passes ``enabled=`` through), so the on-demand endpoint used to
        run the whole sweep — lane 7 included — with the switch off. In
        ``enforce`` that meant the endpoint could WRITE
        ``mission_commissions`` rows while the operator had the master
        kill-switch OFF. This method is the SINGLE choke point: a
        disabled service returns an EMPTY :class:`SweepResult` (no
        lane ran) and the endpoint's own contract (503, "the service
        is disabled by config") is honoured by the router, which READS
        the same :attr:`enabled` flag — the switch itself is NOT
        duplicated in a second place.
        """
        if not self._enabled:
            logger.info(
                "ReportDeliveryRecoveryService disabled by config; "
                "recover_now is a no-op (no lane ran)"
            )
            return SweepResult()
        return self._run_all_lanes_sync()

    # --------------------------------------------------------
    # Lane runner — seven lanes, sequential, bounded
    # --------------------------------------------------------

    def _run_all_lanes_sync(self) -> SweepResult:
        """Run every lane once and aggregate.

        **F-2 (durability-f1-f2 / phase2, task 2.7) — Window-to-lane
        map (binding):**

        | F-2 sub-window | What happens | Lane | Why |
        |---|---|---|---|
        | W-A (terminal-write → wake-rows-commit) | child's natural completion writes ``internal_report:`` to the parent's queue; wake row PENDING | **Lane 2** (post-wipe) | the wipe deletes the wake row + the anchor; Lane 2 admits anchor-less completed children of non-terminal parents and derives the id from the checkpoint |
        | W-B (commit → ``enqueued_at`` stamp) | wake row stamped | **Lane 2** (post-wipe) | same post-wipe shape; the stamp does NOT survive the wipe |
        | W-C (stamp → wake-task claim) | PROCESS_REPORT task claimed by worker pool | **Lane 2** (post-wipe) | the PROCESS_REPORT task does NOT survive the wipe (the ``task`` table is wiped regardless of ``work_id`` — the reviewer-corrected justification, iteration 2); Lane 2's per-row pass materializes a FRESH PROCESS_REPORT + MessageQueue READY + re-enters through the sanctioned RDRS chain (per W-2 guardrail #1 in decisions.md §12d) |
        | marker-minted pre-restart | a ``report_injections`` row was written before the restart | **Lanes 1/3/4** | the marker survives the wipe (the wipe targets ``message_queue`` + ``task`` only); Lanes 1/3/4 own the recovery of these rows via ``ensure_deferred`` → ``transition_deferred_to_pending`` |
        | orphan-deferred-of-terminal-parents | a DEFERRED row whose parent is TERMINAL | **Lane 5** | the ORPHAN lane's terminal-parent revival path; observable log + metric, never silent |

        All three F-2 sub-windows (W-A/W-B/W-C) converge to the
        same post-wipe shape (PENDING wake rows deleted regardless
        of stamp state) and are covered by Lane 2 (the no-row
        backstop, with the F-2 anchor-less extension at task 2.2).
        The mixed case (marker WAS minted pre-restart) is handled
        by Lanes 1/3/4 + sub-shape (c) carrier revival at
        ``manager.py:8971-9017``.

        Lanes run in dependency order: DEFERRED (Lane 1) first so
        any row the router / FM-1-guarded path already transitioned
        wins the race (``transition_deferred_to_pending`` returns
        False for lanes 3/4 to pick up next cycle). Then no-row
        backstop (Lane 2), then PENDING-age + retry (Lanes 3 + 4),
        then ORPHAN (Lane 5).

        The ORPHAN lane is intentionally LAST — the design review
        verified that a terminal parent's obligation is observable
        (logged + metric) but never silent.

        F4 (2026-08-20): a single ``self._stop_event.is_set()`` check
        between lanes (cheap; two extra ``is_set()`` calls per
        sweep) — the per-row check inside each lane is the primary
        prompt-exit, but if a lane returns with a large batch held
        in memory the inter-lane check cuts the next lane's DB
        query + results-processing early.
        """
        result = SweepResult()
        if self._lane_deferred:
            result.lanes["deferred"] = self._run_deferred_lane()
            if self._stop_event.is_set():
                self._finalize_sweep_result(result)
                return result
        if self._lane_no_row_backstop:
            result.lanes["no_row_backstop"] = (
                self._run_no_row_backstop_lane()
            )
            if self._stop_event.is_set():
                self._finalize_sweep_result(result)
                return result
        # Lanes 3 + 4 share the same query (Lane 3 covers never-
        # stamped rows; Lane 4 covers stamped-stale rows). We run
        # them with distinct ``recovery_retry_minutes`` arguments —
        # Lane 3 sets the retry to ``0`` (always eligible); Lane 4
        # uses the configured retry interval.
        if self._lane_pending_age:
            result.lanes["pending_age"] = self._run_pending_age_lane(
                lane_name="pending_age", recovery_retry_minutes=0
            )
            if self._stop_event.is_set():
                self._finalize_sweep_result(result)
                return result
        if self._lane_recovery_retry:
            result.lanes["recovery_retry"] = self._run_pending_age_lane(
                lane_name="recovery_retry",
                recovery_retry_minutes=self._recovery_retry_minutes,
            )
            if self._stop_event.is_set():
                self._finalize_sweep_result(result)
                return result
        if self._lane_orphan:
            result.lanes["orphan"] = self._run_orphan_lane()
            if self._stop_event.is_set():
                self._finalize_sweep_result(result)
                return result
        # Lane 6 (Block-1 G4): the stuck-wake heal. Sits AFTER the
        # marker-targeted lanes (1/3/4) because the heal action —
        # ``force_cancel_and_schedule_retry`` on the dead-worker
        # wake task — is destructive (cancels the running task)
        # and the lanes above own any prior obligation. Also sits
        # AFTER Lane 2 (no-row backstop) because Lane 2's
        # anchor-less admission is for the post-wipe window where
        # the marker does not exist; here the marker DOES exist
        # (state='PENDING') and the wake is preserved.
        if self._lane_stuck_wake:
            result.lanes["stuck_wake"] = self._run_stuck_wake_lane()
        # Lane 7 (Proposal B PR3): the mission-commission reconciliation
        # sweep. LAST — lanes 1-6 are DELIVERY healing, so a commission
        # whose report is re-delivered by them in this same pass must be
        # judged on the POST-HEAL state: the abandonment decision runs
        # after every delivery lane had its chance.
        if self._lane_mission_reconciliation:
            result.lanes["mission_reconciliation"] = (
                self._run_mission_reconciliation_lane()
            )
        self._finalize_sweep_result(result)
        return result

    @staticmethod
    def _finalize_sweep_result(result: SweepResult) -> None:
        """Aggregate ``total_recovered`` for a partial-or-full sweep.

        F4 (2026-08-20) helper — pulled out so the inter-lane
        stop-exit branches can share the same aggregation without
        repeating the ``sum(...)`` literal.
        """
        result.total_recovered = sum(
            lane.recovered for lane in result.lanes.values()
        )

    # --------------------------------------------------------
    # Lane 1 — DEFERRED rows for non-terminal parents
    # --------------------------------------------------------
    # POST-DEEP-REVIEW (W1, 2026-08-20): the prior comment
    # ("past the age guard") was misleading. ``find_deferred_for_
    # parent_all`` has NO ``age_bound`` predicate — age filtering
    # lives on Lane 3 + Lane 4 (PENDING-age + retry) via
    # ``find_pending_past_age``. Lane 1's actual gate is the
    # ``has_instance_busy(parent_id)`` per-row check (steps 1+2 in
    # ``_recover_one_deferred_row``). The real predicate is:
    #   state='DEFERRED' AND parent NOT IN terminal states
    # AND per-row has_instance_busy(parent_id) is False.
    # Lane 5 (ORPHAN) shares the same query with
    # ``parent_not_terminal=False``.

    def _run_deferred_lane(self) -> LaneResult:
        """Process DEFERRED rows whose parent is non-terminal.

        Returns:
            Per-row outcomes: ``recovered`` (transition + reconcile
            + re-enter succeeded), ``skipped_busy`` (parent had an
            active task/job — natural path owns delivery), or
            ``already_recovered`` (rowcount=0 on
            ``transition_deferred_to_pending`` — a concurrent actor
            already transitioned the row).
        """
        return self._process_deferred_rows(parent_not_terminal=True)

    # --------------------------------------------------------
    # Lane 5 — ORPHAN (terminal parents)
    # --------------------------------------------------------

    def _run_orphan_lane(self) -> LaneResult:
        """Process DEFERRED rows whose parent is TERMINAL.

        The plan-overview Q4 decision: revive-and-deliver is the
        PRIMARY path (instance_messaging.py:1486-1510 precedent);
        for rows that cannot revive (revival raises), log + metric
        disposition (NEVER silent). This lane is observable by the
        test 3.6 sub-case — every row reaches ONE of: recovered,
        skipped_busy, orphan_disposition.
        """
        return self._process_deferred_rows(parent_not_terminal=False)

    def _process_deferred_rows(
        self, *, parent_not_terminal: bool
    ) -> LaneResult:
        """Shared Lane 1 + Lane 5 logic.

        Args:
            parent_not_terminal: ``True`` for Lane 1 (periodic
                sweep — terminal parents excluded); ``False`` for
                Lane 5 (ORPHAN — terminal parents only).
        """
        lane = "deferred" if parent_not_terminal else "orphan"
        out = LaneResult()
        # The repo query joins ``Instance`` to filter on parent
        # status. Caller-supplied parameter picks the lane.
        rows = self._report_injection_repo.find_deferred_for_parent_all(
            parent_not_terminal=parent_not_terminal,
            limit=self._batch_cap,
        )
        for row in rows:
            # F4 (2026-08-20): stop-event check between rows — a
            # polite ``stop()`` exits the batch promptly without
            # waiting on the join budget. Cheap (one Event.is_set).
            if self._stop_event.is_set():
                logger.info(
                    "ReportDeliveryRecoveryService._process_deferred_rows: "
                    "stop requested mid-batch — exiting loop "
                    "(remaining rows deferred to next sweep cycle)"
                )
                break
            try:
                self._recover_one_deferred_row(
                    row,
                    result=out,
                    parent_not_terminal=parent_not_terminal,
                )
            except Exception as exc:
                # Per-row fail-safe: leave the row DEFERRED (the row
                # is unchanged because ``transition_deferred_to_pending``
                # commits before we touch it, but the artifact
                # reconciliation may have raised — the row is now
                # PENDING with a fresh ``recovery_attempted_at``).
                # Either shape is recoverable next cycle (lanes 3/4
                # cover PENDING; Lane 1 covers DEFERRED).
                logger.warning(
                    f"sweep {lane} row error "
                    f"child={row.child_instance_id[:8]}..., "
                    f"msg={row.child_message_id[:8]}..., "
                    f"injection_id={row.injection_id[:8]}...: "
                    f"{type(exc).__name__}: {exc}"
                )
                out.errors += 1
        return out

    def _check_parent_busy(
        self, parent_id: str, result: LaneResult, *, lane: str
    ) -> bool:
        """Shared per-row busy-check gate (Rec-2, 2026-08-20).

        One uniform implementation of the "skip if the parent has a
        live task/job" invariant that every lane open-coded 3×.
        ``True`` means BUSY — the caller MUST skip the row (the
        natural path owns delivery when the parent's turn resumes).
        ``False`` means idle — proceed with the lane's recovery
        steps.

        A busy-check EXCEPTION also returns ``True`` (safe default):
        a transient ``has_instance_busy`` failure must not let the
        sweep race a live parent turn. The exception branch counts
        toward ``result.busy_check_failed`` (NOT ``skipped_busy``) so
        operators can distinguish the two skip causes from the
        structured lane output:

        * ``skipped_busy`` — parent confirmed busy (live task).
        * ``busy_check_failed`` — busy-check itself errored; we
          fail-closed-skipped the row. WARNING log emitted.
        """
        try:
            busy = self._task_repo.has_instance_busy(parent_id)
        except Exception as exc:
            # TOCTOU re-check failure: default to busy (safe — the
            # natural path will recover when its turn resumes).
            # Counted as ``busy_check_failed`` so operators can
            # distinguish transient busy-check failures from
            # genuinely-busy parents in the structured LaneResult.
            result.busy_check_failed += 1
            logger.warning(
                f"sweep {lane} busy-check failed "
                f"parent={parent_id[:8]}...: "
                f"{type(exc).__name__}: {exc} — defaulting to busy"
            )
            return True
        if busy:
            result.skipped_busy += 1
            logger.info(
                f"sweep {lane} skipped busy parent={parent_id[:8]}..."
            )
            return True
        return False

    # --------------------------------------------------------
    # Lane 6 — stuck-wake heal (Block-1 G4, merge gate)
    # --------------------------------------------------------

    def _run_stuck_wake_lane(self) -> LaneResult:
        """Heal PENDING-marker + preserved-ready-wake wedges.

        Block-1 G4 (durability-f1-f2 / merge-gate G4 main leg,
        evidence in
        ``.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/db-assertions/d1-pre-kill-assertion.txt``).
        The captured wedge — PENDING marker, wake row in
        ``message_queue`` status ``ready`` (preserved by the F-1
        epoch-belt), wake ``task`` still ``running`` (dead worker),
        parent in ``waiting_children`` — is unreachable by every
        existing RDRS lane (the design review's own §12a claim that
        "marker-minted pre-restart" cases are handled by Lanes
        1/3/4 is contradicted by observation). This lane is the
        unfrozen alternative: detect the captured state via a
        dedicated query (no existing lane predicate is touched) +
        invoke the existing ``TaskRepository.force_cancel_and_
        schedule_retry`` primitive on the dead-worker wake task.
        The retry task has no ``worker_id`` and status ``PENDING``,
        so the worker pool's per-instance busy guard (the only
        guard blocking the claim — see
        ``task/repository.py:2648-2650`` + :2789-2830) no longer
        matches; the worker claims, reads the preserved wake row,
        and ``instance_messaging`` flips the parent from
        ``waiting_children`` to ``RUNNING``.

        Per-row counters:
        * ``recovered``: ``force_cancel_and_schedule_retry`` returned
          a retry task (the wake task was successfully cancelled
          and a new pending task minted).
        * ``errors``: anything that went wrong — logged + counted;
          per-row ``except`` absorbs so the sweeper continues.

        Duck-typed on ``force_cancel_and_schedule_retry`` (mirrors
        the W-4 duck-typed queue-repo gate): a task-repo mock that
        lacks the method is treated as "no heal available" and the
        lane records zero recovered (never crashes the sweep).
        """
        out = LaneResult()
        candidates = self._report_injection_repo.find_stuck_wake_candidates(
            limit=self._batch_cap,
        )
        force_cancel_fn = getattr(
            self._task_repo, "force_cancel_and_schedule_retry", None
        )
        if not callable(force_cancel_fn):
            # Duck-typed: a test double without the heal primitive.
            # Log once at INFO so a misconfigured wiring is visible,
            # then return — the sweep continues (zero recovered).
            logger.info(
                "stuck_wake lane: task_repo has no "
                "force_cancel_and_schedule_retry (duck-typed); "
                "skipping the heal — wired task_repo must be a "
                "real TaskRepository"
            )
            return out

        for cand in candidates:
            if self._stop_event.is_set():
                break
            wake_task_id = cand["wake_task_id"]
            parent_id = cand["parent_instance_id"]
            child_id = cand["child_instance_id"]
            child_message_id = cand["child_message_id"]
            try:
                retry_task = force_cancel_fn(
                    task_id=wake_task_id,
                    max_retries=3,
                    reason=(
                        "RDRS lane 6 stuck-wake heal: dead-worker "
                        "wake task + preserved wake row + PENDING "
                        "marker (Block-1 G4 wedge; "
                        "durability-f1-f2 merge gate)"
                    ),
                )
            except Exception as exc:
                logger.warning(
                    f"sweep stuck_wake force_cancel failed "
                    f"wake_task_id={wake_task_id} "
                    f"parent={parent_id[:8]}... "
                    f"child={child_id[:8]}...: "
                    f"{type(exc).__name__}: {exc}"
                )
                out.errors += 1
                continue
            if retry_task is None:
                # The retry gate refused — another actor owns the
                # obligation, or the task is no longer a force-
                # cancel candidate (e.g. its worker may have updated
                # the heartbeat between query and force_cancel).
                out.skipped_busy += 1
                continue
            logger.info(
                "sweep stuck_wake heal: dead-worker wake task "
                f"id={wake_task_id} → retry id={retry_task.id} "
                f"parent={parent_id[:8]}... "
                f"child={child_id[:8]}..."
            )
            out.recovered += 1
            # ── Last-mile parent-schedule seam (G4-r4 — wake-only) ───
            # The captured-wedge shape (LIVE F-4 evidence): the
            # dead worker had already transitioned the marker
            # PENDING→TASK_DELIVERED (its claim_for_task_delivery
            # call ran pre-SIGKILL), so the retry's claim now
            # sees TASK_DELIVERED → returns "already_delivered" →
            # the worker's drain path correctly skips (the
            # dedup contract) — but the parent is still wedged
            # in waiting_children because the dead worker never
            # dispatched the parent turn before the kill.
            #
            # The round-3 fix
            # (``_process_child_completion_and_notify_parent``)
            # ALSO short-circuited: the primitive's :2773-2787
            # idempotency guard sees the terminal child and
            # returns early as ``idempotency_skip`` — the wake
            # row (the primitive's step 3) is never created. The
            # function does NOT dispatch a parent turn in the
            # skip branch.
            #
            # Close the gap with the WAKE-ONLY primitive: the
            # SAME primitive the manual ping uses to schedule
            # a parent turn —
            # ``self._manager.enqueue_message(parent_id, message,
            # source="system:wedge-resolve", ...)`` at
            # ``daemon/manager.py:7987``. The unified dispatcher
            # (``instance_messaging._prepare_enqueued_message``)
            # writes a fresh MessageQueue + Task
            # (process_message) AND auto-resumes the parent to
            # RUNNING (the auto-resume at :1968 transitions
            # waiting_children → RUNNING) AND notifies the
            # worker pool via ``safe_notify_all_pools``. The
            # worker's normal pipeline then processes the
            # sentinel and dispatches the parent's graph turn.
            #
            # Frozen-behavior discipline: the primitive is the
            # EXISTING ping-seam (not a new path). The :2773
            # idempotency guard stays untouched (the wake-only
            # dispatch is a NEW caller of an EXISTING primitive,
            # not a change to the frozen guard). The retry's
            # claim path is unchanged (the parent's RUNNING
            # transition unblocks the per-instance busy guard;
            # the worker's "already_delivered" skip is the
            # dedup contract working as designed).
            #
            # Bridge to the manager's event loop using the
            # module's own sanctioned step-4 pattern
            # (``asyncio.run_coroutine_threadsafe(...).result(
            # timeout=8.0)`` — same as
            # ``_handle_recover_deferred_report`` at
            # ``manager.py:8586-8596`` and the revival seam
            # at ``manager.py:9118``).
            try:
                loop = self._get_event_loop()
                coroutine = self._manager.enqueue_message
                future = asyncio.run_coroutine_threadsafe(
                    coroutine(
                        parent_id,
                        (
                            "[system:wedge-resolve] Your child "
                            "has completed. The pending report "
                            "is preserved in your message queue; "
                            "process it to continue."
                        ),
                        source="system:wedge-resolve",
                        priority=1,
                    ),
                    loop,
                )
                future.result(timeout=8.0)
            except Exception as exc:
                # Per-row fail-safe — log + count, sweep
                # continues. The retry is still pending; the
                # worker will retry the delivery on next claim
                # cycle.
                logger.warning(
                    f"sweep stuck_wake parent-schedule failed "
                    f"parent={parent_id[:8]}... "
                    f"child={child_id[:8]}...: "
                    f"{type(exc).__name__}: {exc}"
                )
                out.errors += 1
        return out

    def _recover_one_deferred_row(
        self,
        row: Any,
        *,
        result: LaneResult,
        parent_not_terminal: bool,
    ) -> None:
        """Recover a single DEFERRED row.

        Steps (per-row invariant ordering):

        1. Skip if ``has_instance_busy(parent_id)`` (live parent
           turn wins; the natural path drains the report when its
           turn resumes).
        2. For terminal parents (ORPHAN lane): try revival first
           (instance_messaging.py:1486-1510 precedent). Revival
           failure → orphan_disposition (log + metric, NEVER
           silent). Revival success → proceed to step 3.
        3. ``transition_deferred_to_pending(row.injection_id)``.
           rowcount=0 → already_recovered (skip).
        4. Hand off to the manager's reconcile + re-enter path
           (the same code path 2.1's router uses).
        """
        parent_id = row.parent_instance_id
        child_id = row.child_instance_id
        child_msg_id = row.child_message_id
        lane = "deferred" if parent_not_terminal else "orphan"

        # Step 1: busy-check (widened PENDING + RUNNING + PAUSED via
        # ``has_instance_busy``). Uniform helper — busy-skip logged
        # at INFO for every lane (Rec-2, 2026-08-20).
        if self._check_parent_busy(parent_id, result, lane=lane):
            return

        # Step 2 (ORPHAN lane only): try revival first.
        if not parent_not_terminal:
            revived = self._try_revive_terminal_parent(parent_id)
            if not revived:
                # Revive failed — observable disposition. Plan W1
                # requires NEVER silent; structured log + count.
                result.orphan_disposition += 1
                logger.warning(
                    f"sweep orphan_disposition: parent={parent_id[:8]}... "
                    f"is TERMINAL and revival failed — DEFERRED row "
                    f"injection_id={row.injection_id[:8]}... "
                    f"left for explicit disposition "
                    f"(child={child_id[:8]}..., "
                    f"msg={child_msg_id[:8]}..., "
                    f"reason={row.deferred_reason})"
                )
                return
            # Revived — parent now RUNNING, proceed to transition.

        # Step 3: guarded transition DEFERRED → PENDING.
        transitioned = self._report_injection_repo.transition_deferred_to_pending(
            row.injection_id
        )
        if not transitioned:
            result.already_recovered += 1
            logger.debug(
                f"sweep skipped already_recovered "
                f"injection_id={row.injection_id[:8]}..."
            )
            return

        # Step 4: hand off to the manager's reconcile + re-enter
        # path. The router (task 2.1) calls the same code path —
        # we delegate to ``_handle_recover_deferred_report`` on the
        # manager so the reconciliation logic is single-sourced.
        #
        # Propagation style (Rec-2, 2026-08-20): catch + LOG + RAISE
        # — the uniform style across ALL per-row handlers in this
        # service. Rationale: a failed row leaves its DEFERRED /
        # PENDING row UNTOUCHED (pre-transition failure) or
        # committed-but-recoverable (post-transition reconcile /
        # re-enter failure → the row is PENDING with a fresh
        # ``recovery_attempted_at``, retried by lanes 3/4 next
        # cycle), so re-raising lets the lane's outer per-row
        # except bump ``errors`` in ONE place.
        try:
            self._manager._handle_recover_deferred_report(
                child_instance_id=child_id,
                child_message_id=child_msg_id,
                injection_id=row.injection_id,
                source="sweep",
            )
        except Exception as exc:
            # Per-row fail-safe (caught by the caller's outer
            # ``except``); re-raise so the caller's count is bumped.
            logger.warning(
                f"sweep {lane} reconcile+re-enter failed "
                f"child={child_id[:8]}..., msg={child_msg_id[:8]}..., "
                f"injection_id={row.injection_id[:8]}...: "
                f"{type(exc).__name__}: {exc}"
            )
            raise
        result.recovered += 1

    def _try_revive_terminal_parent(self, parent_id: str) -> bool:
        """Revive a TERMINAL parent via the instance_messaging precedent.

        Phase 2 (W1). Mirrors :func:`InstanceMessagingService._prepare_enqueued_message`
        (instance_messaging.py:1486-1510): a COMPLETED / TERMINATED /
        ERROR / FAILED parent is auto-transitioned to RUNNING so a
        fresh message can drive a turn. The same path is the
        revival mechanism for the ORPHAN lane — we DO NOT re-fire a
        turn here (the sweep re-entry already drives
        ``_process_child_completion_and_notify_parent`` which
        enqueues a completion_report via the normal path).

        Returns ``True`` on successful revival, ``False`` on
        failure (the caller treats False as orphan_disposition).

        POST-DEEP-REVIEW (W3, 2026-08-20): per-row timeout aligned
        with the sweep ``stop()`` thread-join budget. The prior
        10s timeout matched ``stop()``'s 10s budget exactly —
        the join could expire mid-.result() and orphan the thread.
        8s leaves a 2s headroom for the join; a revival that
        exceeds the budget falls back to ``orphan_disposition``
        (the lane's structured logging handles the
        non-silent-required-by-W1 disposition).
        """
        try:
            # The instance-messaging path is the canonical revival;
            # calling it with a marker message (no source content) is
            # the safe no-op-the-turn revival: the parent goes
            # RUNNING so the natural completion_report drain works.
            # We use the dedicated ``_revive_terminal_instance`` seam
            # on the manager (added alongside 2.1 — see manager.py
            # docstring) — it wraps the instance_messaging path with
            # idempotency + structured logging.
            return bool(
                asyncio.run_coroutine_threadsafe(
                    self._manager._revive_terminal_instance(parent_id),
                    self._get_event_loop(),
                ).result(timeout=8.0)
            )
        except Exception as exc:
            logger.warning(
                f"sweep revival failed parent={parent_id[:8]}...: "
                f"{type(exc).__name__}: {exc}"
            )
            return False

    def _get_event_loop(self) -> asyncio.AbstractEventLoop:
        """Resolve the manager's event loop (sync accessor).

        The sweep runs on a daemon thread; calling
        ``asyncio.run_coroutine_threadsafe`` requires the destination
        loop. The manager exposes ``self._loop``; we fall back to
        :func:`asyncio.get_event_loop` for older test doubles that
        do not set it.

        POST-DEEP-REVIEW (W2, 2026-08-20): hardened against the
        closed/stale loop case — if the manager's ``_loop`` is set
        but ``is_closed()`` (shutdown / crash-recovery replay path),
        we re-resolve via ``asyncio.get_event_loop()`` which is the
        loop the manager's recovery flow should target.

        POST-DEEP-REVIEW (Y3, 2026-08-20): removed the previous
        ``asyncio.new_event_loop()`` fallback on the terminal
        branch. A brand-new loop is NOT the manager's canonical
        loop — scheduling onto it while blocking on ``.result()``
        is a confusing failure mode that masks stale-loop state
        with a fresh, never-running loop. Instead, log at WARNING
        and raise a clear ``RuntimeError``; the per-row caller
        (which already wraps ``run_coroutine_threadsafe(...).result()``
        in ``except Exception``) catches it, counts the row as an
        error, and the row is retried next sweep cycle.
        """
        loop = getattr(self._manager, "_loop", None)
        if loop is not None:
            if not loop.is_closed():
                return loop
            # Manager's stored loop is closed (shutdown path /
            # restart replay). Fall through to the live-resolution
            # branch below — the recovery flow should target the
            # live loop, not the closed one.
            logger.warning(
                "ReportDeliveryRecoveryService._get_event_loop: "
                "manager._loop is closed — falling back to "
                "asyncio.get_event_loop()"
            )
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            # Terminal branch: no live loop available. The row
            # MUST be retried next sweep cycle — DO NOT create a
            # fresh loop (it is not the manager's canonical loop,
            # and scheduling onto it while blocking on ``.result()``
            # would surface a confusing failure mode).
            logger.warning(
                "ReportDeliveryRecoveryService._get_event_loop: "
                "no live event loop available (manager loop closed, "
                "no running loop in this thread); the row will be "
                "retried on the next sweep cycle"
            )
            raise RuntimeError(
                "report-delivery recovery: no live event loop "
                "available (manager loop closed)"
            ) from None

    def _run_async_on_manager_loop(
        self, coro_func: Any, *args: Any, **kwargs: Any
    ) -> Any:
        """Bridge a coroutine onto the manager's loop (sync caller).

        F-2 (durability-f1-f2 / phase2, verification iteration 2
        blocker 1): the per-row pass's checkpointer-touching
        coroutines (the parent-history PREFIX ledger check and the
        anchor-less ``child_message_id`` derivation) MUST run on the
        manager's canonical event loop. Production checkpointers
        (``AsyncSqliteSaver`` / ``AsyncPostgresSaver``) hold
        loop-bound ``asyncio.Lock`` s — driving them from an
        ephemeral ``asyncio.run`` loop raises cross-loop on every
        production read (the prior ``_run_async`` adapter did
        exactly that, turning the F-2 belt into a production
        no-op and risking a boot-race poisoning of the main
        loop's saver lock).

        This mirrors the module's OWN sanctioned step-4 bridge
        (``manager.py`` ``_reenter_completion_via_loop`` /
        ``asyncio.run_coroutine_threadsafe(...).result(timeout)``
        at manager.py:8612-8622) and the revival seam directly
        below (``_revive_terminal_parent``). The per-row timeout
        is 8.0s — the same W3-aligned budget: ``stop()`` joins the
        sweep thread with a 10s budget; 8s leaves 2s headroom, and
        a row that cannot complete within 8s propagates to the
        per-row ``except`` handler (counted as an error, retried
        next cycle).

        Failure semantics per call site:
          * parent-history PREFIX check — caught by the caller's
            ``except`` (WARNING, proceed; the check is a skip-gate,
            not a delivery step).
          * derivation — propagates to the lane's per-row handler
            (``errors += 1``, retried next cycle).
        """
        loop = self._get_event_loop()
        future = asyncio.run_coroutine_threadsafe(
            coro_func(*args, **kwargs), loop
        )
        return future.result(timeout=8.0)

    # --------------------------------------------------------
    # Lane 2 — NO-ROW BACKSTOP (C3)
    # --------------------------------------------------------

    def _run_no_row_backstop_lane(self) -> LaneResult:
        """Process rows missing report_injections / message_queue / FIRED.

        Catches FM-11 escapes, cancel-mid-shield, and any future
        no-marker drop lane. For each row: ``ensure_deferred``
        first (W6 absorbs duplicates), then the router's
        transition + reconcile + re-enter path.
        """
        # F-2 (task 2.8): one-line boot log marker so the operator
        # can confirm lane 2 ran on the boot path (post-wipe
        # recovery, per the phase-2 plan task 2.8 acceptance). The
        # marker is emitted ONCE PER LANE-2 INVOCATION (at lane
        # entry, before any row is processed — the comment
        # previously claimed "first row's first call", which did
        # not match the code; iteration-2 cheap-item fix).
        logger.info(
            "RDRS lane 2: post-wipe recovery (no-row backstop; "
            "F-2 wedge closure per durability-f1-f2 / phase2 task 2.8)"
        )
        out = LaneResult()
        rows = self._report_injection_repo.find_completed_children_without_delivery(
            parent_not_terminal=True,
            limit=self._batch_cap,
        )
        for row in rows:
            # F4 (2026-08-20): stop-event check between rows — a
            # polite ``stop()`` exits the batch promptly without
            # waiting on the join budget. Cheap (one Event.is_set).
            if self._stop_event.is_set():
                logger.info(
                    "ReportDeliveryRecoveryService._run_no_row_backstop_lane: "
                    "stop requested mid-batch — exiting loop"
                )
                break
            child_id = row["child_id"]
            child_msg_id = row["child_msg_id"]
            parent_id = row["parent_id"]
            try:
                self._recover_one_no_row(
                    child_id=child_id,
                    child_msg_id=child_msg_id,
                    parent_id=parent_id,
                    result=out,
                )
            except Exception as exc:
                logger.warning(
                    f"sweep no_row_backstop row error "
                    f"child={child_id[:8]}..., "
                    f"msg={(child_msg_id or 'NONE')[:8]}..., "
                    f"parent={parent_id[:8]}...: "
                    f"{type(exc).__name__}: {exc}"
                )
                out.errors += 1
        return out

    def _recover_one_no_row(
        self,
        *,
        child_id: str,
        child_msg_id: str,
        parent_id: str,
        result: LaneResult,
    ) -> None:
        """Recover one no-row backstop candidate.

        Per-row invariant ordering (mirror of
        :meth:`_recover_one_deferred_row`):

        **F-2 (durability-f1-f2 / phase2, task 2.5 + 2.6) extended
        invariant ordering (prepended as steps 0 + 0b):**

        0. **PREFIX-ledger cross-path dedup** (task 2.5):
           ``MessageQueueRepository.find_wake_already_delivered_evidence(parent, child)``
           (queue-side) + ``parent_history_has_internal_report(checkpointer, parent, child, manager=None)``
           (parent-history-side). Either match → ``skipped_already_reported += 1; return``
           (the PREFIX ledger is the operative cross-path dedup per
           decisions.md §14a; the obligation-triple unique index
           handles within-path idempotency per migration
           ``20260819_000001:114-120``).
        0b. **Anchor-less ``child_message_id`` derivation** (task
           2.6): for anchor-less children (``child_msg_id IS NULL``),
           derive the id from the surviving child checkpoint via
           ``completion_content.get_last_assistant_message`` +
           ``get_instance_messages`` (``serialize_message`` chain per
           W-1 fallback (ii)). Empty/missing content → WARNING +
           ``return`` (the periodic 300s loop is the retry
           mechanism; the child stays in the wedge for this pass).
           The derived id is ``BaseMessage.id`` (UUID4) —
           STABLE-BUT-DIFFERENT from the natural path's
           ``MessageQueue.message_id`` (per W-1 confirmation).

        1. Skip if ``has_instance_busy(parent_id)``.
        2. ``ensure_deferred(parent, child, child_msg, RESUME_ROUTER)``
           — write-once gate (W6 absorbs IntegrityError on
           duplicate).
        3. ``transition_deferred_to_pending(row.injection_id)``
           (D2 — end-state alignment with Lanes 1/3/4; see the
           inline D2 comment). rowcount=0 → already_recovered.
        4. Hand off to the manager's reconcile + re-enter path.
        """
        # D1 (completion-subsystem Phase 1) — resolve the report
        # RECIPIENT ONCE, at the top of the per-row recovery. The four
        # recipient-aware sites below (the queue PREFIX ledger, the
        # parent-history PREFIX ledger, the busy-check and the
        # ``ensure_deferred`` marker write) all read ``parent_id``, so
        # resolving here teaches every one of them the SAME rule the
        # natural write path uses.
        #
        # Without this the backstop re-addressed the structural parent:
        # the commissioner-addressed report looked UNDELIVERED (the
        # PREFIX ledger was queried for the structural parent) and the
        # lane minted a SECOND obligation for the structural parent —
        # the §7.4 duplication firewall.
        #
        # PR3 (Proposal B §3 D1): the resolution gained its
        # COMMISSION-FIRST leg (the child's oldest outstanding
        # commission row → its commissioner, enforce-gated) INSIDE
        # ``resolve_report_recipient`` — this site substitutes the
        # resolution and touches nothing downstream (the property the
        # paragraph above documents; the sweep design §3's "teach the
        # RESOLUTION, not the downstream guards"). Lane 7 (the
        # mission_reconciliation abandonment probes) is a separate
        # cycle and is deliberately NOT added here.
        parent_id = resolve_report_recipient(
            engine=getattr(self._manager, "engine", None),
            child_instance_id=child_id,
            structural_parent_id=parent_id,
        ) or parent_id

        # Step 0 (F-2 task 2.5): PREFIX-ledger cross-path dedup.
        # The two checks below are the operative cross-path dedup
        # per decisions.md §14a (W-1 LOCKED to fallback (ii)); the
        # exact-id equality at child_reports.py:3498-3507 is the
        # natural-path-only check. The PREFIX-ledger cross-path
        # check + the obligation-triple unique index (migration
        # 20260819_000001:114-120) jointly enforce exactly-once
        # across the natural and recovery paths.
        #   - Queue-side: MessageQueueRepository.find_wake_already_delivered_evidence
        #     (PREFIX on `source LIKE 'internal_report:{child}:%'`)
        #   - Parent-history-side: report_delivery_ledger.parent_history_has_internal_report
        #     (PREFIX on serialized.get("source","").startswith(...))
        # Either match ⇒ the report is already delivered (or
        # in-flight) on the natural path; skip and increment
        # `skipped_already_reported`. The PREFIX ledger is the
        # CROSS-PATH dedup; the obligation-triple unique index
        # is the WITHIN-PATH idempotency (per task 2.5).
        # Queue-side PREFIX check. Duck-typed (verification
        # iteration 2, W-4): the previous isinstance gate on
        # ``SQLModelMessageQueueRepository`` silently fail-opened
        # for any other repo implementation; calling the method
        # when it exists covers every real repo (and explicitly
        # configured test doubles) without the type check.
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )
        queue_evidence_fn = getattr(
            self._queue_repo,
            "find_wake_already_delivered_evidence",
            None,
        )
        if callable(queue_evidence_fn) and queue_evidence_fn(
            parent_id, child_id
        ):
            result.skipped_already_reported += 1
            logger.debug(
                "sweep no_row_backstop skipped_already_reported "
                "(queue-side PREFIX ledger match) "
                f"parent={parent_id[:8]}..., child={child_id[:8]}..."
            )
            return
        # Parent-history-side PREFIX check. The manager's
        # checkpointer is the canonical access path;
        # ``getattr(self._manager, "_checkpointer", None)`` is the
        # guarded lookup that degrades to "no parent-history
        # evidence" when the checkpointer is absent. The
        # ``manager=None`` default further degrades pre-migration
        # parents to "not yet reported" (the correct default per
        # the report_delivery_ledger docstring).
        #
        # LOOP AFFINITY (iteration-2 blocker 1): the ledger
        # coroutine is bridged onto the manager's loop — NOT an
        # ephemeral ``asyncio.run`` loop — because production
        # checkpointers hold loop-bound locks. See
        # ``_run_async_on_manager_loop``.
        checkpointer = getattr(self._manager, "_checkpointer", None)
        if checkpointer is not None:
            try:
                if self._run_async_on_manager_loop(
                    parent_history_has_internal_report,
                    checkpointer,
                    parent_id,
                    child_id,
                    None,
                ):
                    result.skipped_already_reported += 1
                    logger.debug(
                        "sweep no_row_backstop skipped_already_reported "
                        "(parent-history PREFIX ledger match) "
                        f"parent={parent_id[:8]}..., "
                        f"child={child_id[:8]}..."
                    )
                    return
            except Exception as exc:
                # Defensive: a parent-history lookup failure must
                # NOT abort the sweep (the queue-side check above
                # already covers the cross-path dedup for the
                # PREFIX). Log and proceed. WARNING (iteration-2
                # blocker 1): the prior DEBUG level swallowed the
                # dead-belt signal — a persistently failing ledger
                # check means the cross-path dedup is running
                # one-legged (queue-side only) and MUST be visible
                # to the operator. Counter semantics unchanged: the
                # row proceeds (no error bump, no skip).
                logger.warning(
                    "sweep no_row_backstop parent-history PREFIX "
                    f"lookup failed (non-fatal, proceeding): {exc}",
                    exc_info=True,
                )

        # Step 0b (F-2 task 2.6): anchor-less `child_message_id`
        # derivation. For anchor-less children (the `has_anchor`
        # flag is False from the lane-2 query at task 2.2), the
        # `child_msg_id` from the anchor subquery is NULL and the
        # derivation must come from the surviving child checkpoint
        # via the `serialize_message` chain (W-1 fallback (ii) per
        # decisions.md §14a). Empty/missing content → WARNING +
        # `return` (the child stays in the wedge for this pass;
        # the periodic 300s loop is the retry mechanism).
        # The derived id is `BaseMessage.id` (UUID4) —
        # STABLE-BUT-DIFFERENT from the natural path's
        # `MessageQueue.message_id` (per W-1 confirmation). The
        # PREFIX ledger (step 0 above) is the operative
        # cross-path dedup; the id mismatch is by design.
        if not child_msg_id:
            # The derivation is async; the sweep is sync — bridge
            # it onto the manager's loop via the SAME sanctioned
            # pattern the step-4 seam uses (iteration-2 blocker 1:
            # the prior ephemeral-loop adapter failed cross-loop
            # against every production checkpointer).
            # A None checkpointer short-circuits INSIDE the
            # coroutine (returns None); skip the bridge entirely
            # for that case so the graceful "no derivable id"
            # WARNING path below is preserved even when no loop
            # is available.
            if checkpointer is None:
                derived_id = None
            else:
                derived_id = self._run_async_on_manager_loop(
                    _derive_anchor_less_child_message_id,
                    checkpointer,
                    child_id,
                )
            if derived_id is None:
                logger.warning(
                    "sweep no_row_backstop anchor-less child has no "
                    "derivable child_message_id (child checkpoint "
                    "missing or empty); continuing — the periodic "
                    "300s loop is the retry mechanism. "
                    f"parent={parent_id[:8]}..., "
                    f"child={child_id[:8]}..."
                )
                return
            child_msg_id = derived_id

        # Step 1: busy-check — uniform helper (Lanes 2/3/4 busy-skips
        # are now logged at INFO like Lane 1/5, Rec-2 2026-08-20).
        if self._check_parent_busy(parent_id, result, lane="no_row_backstop"):
            return

        # Step 2: ensure_deferred. W6: IntegrityError is absorbed by
        # ``ensure_deferred`` itself — the call returns ``None`` ONLY
        # with positive evidence: a concurrent duplicate was absorbed
        # (a non-terminal row for the triple already exists — e.g.
        # Site 1 wrote it) or a TERMINAL row proves prior delivery.
        # Since Debug Phase 4 (2026-09-07) a ZERO-row triple INSERTs
        # a fresh marker (insert-on-missing) — the old "racing
        # delivery won" no-op for zero rows was a false positive that
        # made this lane flap every cycle without healing (incident
        # b7ead8a4/d90b18f9). We proceed regardless of the return
        # value because the router/sweep NEVER see raw IntegrityError
        # (the persistent-conflict-with-zero-rows case re-raises and
        # is caught by the per-row handler below → errors, retried
        # next cycle).
        #
        # Propagation style (Rec-2, 2026-08-20): catch + LOG + RAISE
        # — the SAME style as every other per-row handler in this
        # service. Rationale: a failed row leaves its DEFERRED /
        # PENDING row UNTOUCHED (``ensure_deferred`` failed before
        # any state change, or the reconcile/re-enter failed after a
        # committed-but-recoverable transition), so re-raising lets
        # the lane's outer per-row except bump ``errors`` in ONE
        # place and the row is retried next sweep cycle. The
        # catch+count+return style previously used ONLY here hid the
        # double-counting risk and split the error-accounting logic
        # across two layers for no behavioral gain.
        try:
            row = self._report_injection_repo.ensure_deferred(
                parent_instance_id=parent_id,
                child_instance_id=child_id,
                child_message_id=child_msg_id,
                deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
            )
        except Exception as exc:
            logger.warning(
                f"sweep no_row_backstop ensure_deferred failed "
                f"child={child_id[:8]}..., msg={child_msg_id[:8]}..., "
                f"parent={parent_id[:8]}...: "
                f"{type(exc).__name__}: {exc}"
            )
            raise

        # ``row is None`` now REQUIRES positive evidence (Debug Phase
        # 4, 2026-09-07): a concurrent duplicate was absorbed (a
        # non-terminal row for the triple exists — the OTHER actor
        # (Site 1 / another sweep cycle) has the obligation and its
        # recovery path owns it) OR a terminal row proves the report
        # was already delivered. Both dispositions are safe to skip —
        # we treat either as already_recovered and avoid duplicate
        # transitions. ``None`` is NEVER returned for a zero-row
        # triple anymore (that shape inserts instead).
        if row is None:
            result.already_recovered += 1
            return

        # Step 3 (D2, 2026-08-20): guarded transition DEFERRED →
        # PENDING — the SAME end-state alignment as Lanes 1/3/4.
        # Pre-D2 this lane handed the fresh DEFERRED row straight
        # to reconcile, leaving the row in a HALF-RECOVERED state
        # (state=DEFERRED with a backfilled artifact) that
        # re-triggered every cycle: both claim paths
        # (``claim_for_injection`` / ``claim_for_task_delivery``)
        # are guarded ``WHERE state='PENDING'``, so a DEFERRED row
        # can NEVER be claimed — delivery only completed when the
        # NEXT cycle's Lane 1 re-picked the row and transitioned
        # it. Transitioning here means the row ends the cycle
        # DELIVERED-or-terminal (or PENDING + claimed by the worker
        # pool) — never a half-recovered shape.
        transitioned = (
            self._report_injection_repo.transition_deferred_to_pending(
                row.injection_id
            )
        )
        if not transitioned:
            result.already_recovered += 1
            logger.debug(
                f"sweep no_row_backstop skipped already_recovered "
                f"injection_id={row.injection_id[:8]}..."
            )
            return

        # Step 4: hand off to the manager's reconcile + re-enter
        # path.
        try:
            self._manager._handle_recover_deferred_report(
                child_instance_id=child_id,
                child_message_id=child_msg_id,
                injection_id=row.injection_id,
                source="sweep_no_row_backstop",
            )
        except Exception as exc:
            logger.warning(
                f"sweep no_row_backstop reconcile+re-enter failed "
                f"child={child_id[:8]}..., msg={child_msg_id[:8]}..., "
                f"injection_id={row.injection_id[:8]}...: "
                f"{type(exc).__name__}: {exc}"
            )
            raise
        result.recovered += 1

    # --------------------------------------------------------
    # Lanes 3 + 4 — Age-bounded PENDING + retry
    # --------------------------------------------------------

    def _run_pending_age_lane(
        self, *, lane_name: str, recovery_retry_minutes: int
    ) -> LaneResult:
        """Process stranded PENDING rows past the age guard.

        Args:
            lane_name: ``"pending_age"`` (Lane 3) or
                ``"recovery_retry"`` (Lane 4) — threaded into every
                per-row log so the two lanes are distinguishable in
                production logs (Rec-2, 2026-08-20: both lanes
                previously logged as "pending_age").
            recovery_retry_minutes: ``0`` for Lane 3 (covers never-
                stamped rows); configured value for Lane 4 (covers
                stamped-stale rows whose ``recovery_attempted_at``
                is past the retry interval).
        """
        out = LaneResult()
        rows = self._report_injection_repo.find_pending_past_age(
            age_bound=self._age_bound,
            recovery_retry_minutes=recovery_retry_minutes,
            limit=self._batch_cap,
        )
        for row in rows:
            # F4 (2026-08-20): stop-event check between rows — a
            # polite ``stop()`` exits the batch promptly without
            # waiting on the join budget. Cheap (one Event.is_set).
            if self._stop_event.is_set():
                logger.info(
                    "ReportDeliveryRecoveryService._run_pending_age_lane: "
                    "stop requested mid-batch — exiting loop"
                )
                break
            try:
                self._recover_one_pending_row(
                    row, result=out, lane_name=lane_name
                )
            except Exception as exc:
                logger.warning(
                    f"sweep {lane_name} row error "
                    f"child={row.child_instance_id[:8]}..., "
                    f"msg={row.child_message_id[:8]}..., "
                    f"injection_id={row.injection_id[:8]}...: "
                    f"{type(exc).__name__}: {exc}"
                )
                out.errors += 1
        return out

    def _recover_one_pending_row(
        self, row: Any, *, result: LaneResult, lane_name: str
    ) -> None:
        """Recover one stranded PENDING row.

        The row is already PENDING (Lane 3 + 4 do NOT need a
        transition); we just hand off to the manager's
        reconcile + re-enter path.

        Per-row invariant ordering:

        1. Skip if ``has_instance_busy(parent_id)``.
        2. Hand off to the manager's reconcile + re-enter path
           (it re-validates row state inside the same session).
        """
        parent_id = row.parent_instance_id
        child_id = row.child_instance_id
        child_msg_id = row.child_message_id

        # Step 1: busy-check — uniform helper (Rec-2, 2026-08-20).
        if self._check_parent_busy(parent_id, result, lane=lane_name):
            return

        # Step 2: hand off. No transition (the row is already
        # PENDING) — just reconcile + re-enter. The ``source``
        # branch keys off the per-call ``lane_name`` (Rec-2,
        # 2026-08-20: it previously keyed off the service-level
        # ``_recovery_retry_minutes`` config value, which
        # mislabeled Lane 3 rows whenever the operator configured
        # a retry interval of 0).
        source = (
            "sweep_pending_age"
            if lane_name == "pending_age"
            else "sweep_recovery_retry"
        )
        try:
            self._manager._handle_recover_deferred_report(
                child_instance_id=child_id,
                child_message_id=child_msg_id,
                injection_id=row.injection_id,
                source=source,
            )
        except Exception as exc:
            logger.warning(
                f"sweep {lane_name} reconcile+re-enter failed "
                f"child={child_id[:8]}..., msg={child_msg_id[:8]}..., "
                f"injection_id={row.injection_id[:8]}...: "
                f"{type(exc).__name__}: {exc}"
            )
            raise
        result.recovered += 1

    # --------------------------------------------------------
    # Lane 7 — MISSION-COMMISSION RECONCILIATION (Proposal B PR3)
    # --------------------------------------------------------
    #
    # THE ESCAPE VALVE for the "eternal mission" class: an outstanding
    # ``mission_commissions`` row whose commissioned work will never
    # produce a report holds its mission open forever (the D2(c)
    # settle-hold refuses to settle while ``outstanding_commissions >
    # 0``). This lane resolves the provably-dead rows to ``abandoned``
    # (and the provably-delivered ones to ``satisfied``), so the EXISTING
    # settle machinery can do its job.
    #
    # POSTURE — FAIL-MISS, everywhere (§2 of the sweep design):
    #   * every probe error HOLDS the row (the commission stays
    #     outstanding, a per-probe error counter increments, the row is
    #     retried next cycle);
    #   * the sweep NEVER resolves a commission on a failed probe —
    #     abandoning a commission is a wrong-side terminal transition
    #     in the same moral category as finalizing a live mission;
    #   * the ONE bounded exception is the TTL (P3): past the horizon
    #     AND with a SUCCESSFUL "not live" liveness probe, the
    #     unprovable state resolves to ``abandoned`` LOUDLY. A
    #     liveness-probe ERROR still holds at any age.
    #
    # The sweep writes ONLY ``mission_commissions`` (R6): no
    # ``instances.status`` writes (the terminal-write census stays
    # untouched), no ``mission_records`` writes (the settle machinery
    # owns settle), no ``report_injections`` / ``dependency_watchers``
    # mutations (lanes 1-6 own delivery; the bus owns its lifecycle).

    def _run_mission_reconciliation_lane(
        self,
    ) -> MissionReconciliationLaneResult:
        """Lane 7 — resolve the provably-dead outstanding commissions.

        Admission (R0) is COMMISSION-centric (not report-injection
        centric): the global ``state='outstanding'`` query is the only
        admission shape that can see the T2 (worker row gone) / T3
        (worker stalled) / T4 (masked enqueue) commission shapes the
        delivery lanes never see.

        Mode ladder (R7): ``off`` → the lane is NOT EVEN QUERIED;
        ``dry`` → full detect + log + counters, ZERO writes; ``enforce``
        → writes (the guarded transition).

        Returns:
            The lane counters (the design's R9 set).
        """
        out = MissionReconciliationLaneResult()
        repo = self._mission_record_repo
        find_outstanding = getattr(repo, "find_outstanding_commissions", None)
        if repo is None or not callable(find_outstanding):
            # UNWIRED DEGRADE (the ``force_cancel_and_schedule_retry``
            # duck-typed gate precedent): a single INFO log, ZERO — no
            # reads, no writes. A pre-PR1 binary or a test double
            # without the repository must stay honest.
            if not self._mission_lane_degrade_logged:
                self._mission_lane_degrade_logged = True
                logger.info(
                    "RDRS lane 7: mission_record_repo is not wired — the "
                    "mission-commission reconciliation lane is inert "
                    "(no reads, no writes)"
                )
            return out

        mode = self._mission_records_mode()
        if mode == MISSION_RECORDS_MODE_OFF:
            # R7: ``off`` → the lane is not even queried.
            logger.info(
                "RDRS lane 7: mission-records mode is off — lane not queried"
            )
            return out

        if (
            mode == MISSION_RECORDS_MODE_ENFORCE
            and self._resolve_mission_record_service() is None
        ):
            # MAJ-6: the WRITER is the mission-record SERVICE (the one
            # guarded UPDATE that owns the ``resolved_at`` /
            # ``satisfied_at`` stamps). Without it the lane cannot
            # resolve anything, and a lane that DETECTS but cannot WRITE
            # would report abandonments that never happened — so the
            # lane is inert in ``enforce`` (the same posture as the
            # unwired-repository degrade). In ``dry`` the lane still
            # runs: it writes nothing anyway, and the counters are the
            # tuning dataset.
            if not self._mission_lane_degrade_logged:
                self._mission_lane_degrade_logged = True
                logger.warning(
                    "RDRS lane 7: the mission-record SERVICE is not wired "
                    "— the mission-commission reconciliation lane is "
                    "inert in enforce (no reads, no writes)"
                )
            return out

        rows = list(find_outstanding(limit=self._batch_cap))
        logger.info(
            "RDRS lane 7: mission_reconciliation admitted %d outstanding "
            "commission(s) (mode=%s, ttl=%ss, batch_cap=%d)",
            len(rows),
            mode,
            self._mission_commission_ttl_seconds,
            self._batch_cap,
        )
        for row in rows:
            # F4 (2026-08-20): stop-event check between rows — a polite
            # ``stop()`` exits the batch promptly. Cheap (one
            # Event.is_set).
            if self._stop_event.is_set():
                logger.info(
                    "RDRS lane 7: stop requested mid-batch — exiting loop"
                )
                break
            try:
                self._reconcile_one_commission(row, out, mode=mode)
            except Exception as exc:
                # P2 — the host's per-row posture: log + errors += 1 +
                # row untouched. A crashed pass abandons NOTHING.
                out.errors += 1
                logger.warning(
                    "sweep mission_reconciliation row error "
                    "mission_id=%s, commission_id=%s, worker=%s: "
                    "%s: %s",
                    getattr(row, "mission_id", None),
                    getattr(row, "commission_id", None),
                    getattr(row, "worker_instance_id", None),
                    type(exc).__name__,
                    exc,
                )
        return out

    def _reconcile_one_commission(
        self,
        commission: Any,
        out: MissionReconciliationLaneResult,
        *,
        mode: str,
    ) -> None:
        """Reconcile ONE outstanding commission (the design's per-row
        pseudo-code, step by step).

        Every probe is fail-MISS (P1): a probe error HOLDS the row.
        """
        commission_id = commission.commission_id
        mission_id = commission.mission_id
        worker_id = commission.worker_instance_id
        commissioner_id = commission.commissioner_instance_id
        lane = getattr(commission, "lane", "")

        # ── (1) TTL age math — parsed in PYTHON ──
        # Never string-compared in SQL (the TEXT-ISO convention). An
        # unparsable ``created_at`` HOLDS the row — never guess age.
        age_seconds = self._commission_age_seconds(commission)
        if age_seconds is None:
            out.probe_errors += 1
            logger.warning(
                "sweep mission_reconciliation HOLD (unparsable created_at) "
                "mission_id=%s, commission_id=%s, commissioner=%s, "
                "worker=%s, lane=%s",
                mission_id,
                commission_id,
                commissioner_id,
                worker_id,
                lane,
            )
            return

        # ── (2) the worker instance row read ──
        try:
            worker = self._instance_repo.get(worker_id)
        except Exception as exc:
            out.probe_errors += 1
            logger.warning(
                "sweep mission_reconciliation HOLD (worker read failed) "
                "mission_id=%s, commission_id=%s, commissioner=%s, "
                "worker=%s, lane=%s: %s: %s",
                mission_id,
                commission_id,
                commissioner_id,
                worker_id,
                lane,
                type(exc).__name__,
                exc,
            )
            return

        if worker is None:
            # POSITIVE ABSENCE (the read succeeded and returned no
            # row) — the T2 shape. Below the TTL a recent absence may
            # be a visibility artifact → HOLD.
            if age_seconds > self._mission_commission_ttl_seconds:
                # ── MAJ-5: the R4 bus-watcher probe runs BEFORE the
                # R2c abandon. A PENDING watcher targeting the
                # commission is LIVE OBLIGATION EVIDENCE ⇒ the row is
                # HELD (no abandon). The old shape abandoned a
                # commission whose obligation was still tracked
                # whenever the worker's row had disappeared.
                #
                # BOUNDED SEMANTICS: the hold is a PER-PASS decision,
                # re-evaluated on every cycle against the CURRENT
                # watcher set — it is NOT an eternal hold (when the
                # watcher stops being pending, the row proceeds to the
                # TTL backstop) and it is NOT the design's "hold even
                # past the TTL" pseudo-code, which reads as an
                # unconditional promise the sweep does not make.
                try:
                    watchers = self._pending_bus_watchers(
                        commissioner_id, worker_id
                    )
                except Exception as exc:
                    out.probe_errors += 1
                    logger.warning(
                        "sweep mission_reconciliation HOLD (bus-watcher "
                        "probe failed) mission_id=%s, commission_id=%s, "
                        "commissioner=%s, worker=%s, lane=%s: %s: %s",
                        mission_id,
                        commission_id,
                        commissioner_id,
                        worker_id,
                        lane,
                        type(exc).__name__,
                        exc,
                    )
                    return
                if watchers:
                    out.skipped_watcher_pending += 1
                    return
                # MAJ-3: a masked-probe FAULT holds the row for this
                # pass — the caller must NOT abandon a row whose
                # observability probe could not run.
                if self._note_masked_enqueue(
                    commission, out, age_seconds=age_seconds
                ):
                    return
                self._abandon_commission(
                    commission,
                    out,
                    mode=mode,
                    reason=MISSION_ABANDON_REASON_WORKER_MISSING,
                    age_seconds=age_seconds,
                )
            else:
                out.below_ttl_hold += 1
            return

        worker_status = getattr(worker, "status", None)

        # ── (3) liveness (R3) — the guard's own probe family + the
        # JobItem-live check (OQ-7). A live worker is NEVER abandoned,
        # at any age (R2a).
        try:
            live = self._commission_worker_is_live(worker_id, commission)
        except Exception as exc:
            out.probe_errors += 1
            logger.warning(
                "sweep mission_reconciliation HOLD (liveness probe failed) "
                "mission_id=%s, commission_id=%s, commissioner=%s, "
                "worker=%s, lane=%s: %s: %s",
                mission_id,
                commission_id,
                commissioner_id,
                worker_id,
                lane,
                type(exc).__name__,
                exc,
            )
            return
        if live:
            out.skipped_live += 1
            return

        # ── (4) the bus watcher (R4) — a PENDING watcher from that
        # worker targeting the commissioner is live obligation
        # evidence: the row is HELD for this pass (the sweep never
        # abandons a row whose obligation is still tracked).
        #
        # BOUNDED SEMANTICS (MAJ-5): the hold is a PER-PASS decision,
        # re-evaluated on every cycle against the CURRENT watcher set
        # — it is NOT an eternal hold (when the watcher stops being
        # pending, the row proceeds to the TTL backstop) and it is NOT
        # the design's "hold even past the TTL" pseudo-code, which
        # reads as an unconditional promise the sweep does not make.
        # The worker-missing branch runs the SAME probe BEFORE its
        # abandon (the obligation may still be tracked even when the
        # worker's own row has disappeared).
        try:
            watchers = self._pending_bus_watchers(commissioner_id, worker_id)
        except Exception as exc:
            out.probe_errors += 1
            logger.warning(
                "sweep mission_reconciliation HOLD (bus-watcher probe "
                "failed) mission_id=%s, commission_id=%s, commissioner=%s, "
                "worker=%s, lane=%s: %s: %s",
                mission_id,
                commission_id,
                commissioner_id,
                worker_id,
                lane,
                type(exc).__name__,
                exc,
            )
            return
        if watchers:
            out.skipped_watcher_pending += 1
            return

        # ── (5) the delivery-evidence TRILET (R1/R1b) — the lane-2
        # evidence helpers, read on the COMMISSIONER as the recipient.
        # MAJ-1: the injection-row leg is DELIVERED-states-only AND
        # bounded to THIS commission (a row strictly older than the
        # mint belongs to an earlier commission on the same pair).
        try:
            has_evidence = self._delivery_evidence_exists(
                commissioner_id, worker_id, commission
            )
        except Exception as exc:
            out.probe_errors += 1
            logger.warning(
                "sweep mission_reconciliation HOLD (delivery-evidence probe "
                "failed) mission_id=%s, commission_id=%s, commissioner=%s, "
                "worker=%s, lane=%s: %s: %s",
                mission_id,
                commission_id,
                commissioner_id,
                worker_id,
                lane,
                type(exc).__name__,
                exc,
            )
            return

        # ── (§6) the MASKED-ENQUEUE observability — past the grace
        # window (= the TTL), a commission with NO queue row at all is
        # the conservative "the enqueue was masked" signal. OBSERVATION
        # ONLY: no auto-action, and it NEVER abandons a commission.
        # MAJ-3: a masked-probe FAULT holds the row for this pass — the
        # satisfy / abandon legs below must NOT run on a row whose
        # observability probe could not complete.
        if age_seconds > self._mission_commission_ttl_seconds:
            if self._note_masked_enqueue(
                commission, out, age_seconds=age_seconds
            ):
                return

        if has_evidence:
            # R1b — SATISFY ON EVIDENCE: the delivery happened; the
            # natural satisfy write was lost (crash between the
            # delivery and the satisfy). Positive evidence is PROOF, so
            # the same guarded transition applies.
            self._satisfy_commission_on_evidence(
                commission, out, mode=mode, age_seconds=age_seconds
            )
            return

        # ── (6) R1 — the worker is terminal and NOTHING was delivered.
        if worker_status in _MISSION_COMMISSION_TERMINAL_STATUSES:
            self._abandon_commission(
                commission,
                out,
                mode=mode,
                reason=MISSION_ABANDON_REASON_WORKER_TERMINAL,
                age_seconds=age_seconds,
            )
            return

        # ── (7) R2b — the TTL backstop (non-terminal, provably idle).
        if age_seconds > self._mission_commission_ttl_seconds:
            self._abandon_commission(
                commission,
                out,
                mode=mode,
                reason=MISSION_ABANDON_REASON_TTL,
                age_seconds=age_seconds,
            )
            return

        # ── (8) below the TTL → HOLD (the row is retried next cycle).
        out.below_ttl_hold += 1

    # --------------------------------------------------------
    # Lane 7 — the probes (each one fail-MISS)
    # --------------------------------------------------------

    def _mission_records_mode(self) -> str:
        """The mission-record mode ladder (``off`` / ``dry`` / ``enforce``).

        Deferred import (the house style — the mode ladder's owner is
        ``daemon.services.mission_record_service``). An UNRECOGNIZED
        token raises loudly at the boot resolution site; the lane must
        never break on a knob fault, so it degrades to the SAFEST mode
        (``off`` — no reads, no writes).
        """
        from daemon.services.mission_record_service import (
            MISSION_RECORDS_MODE_ENV,
            MissionRecordMode,
            resolve_mission_records_mode,
        )

        try:
            return resolve_mission_records_mode()
        except Exception as exc:  # noqa: BLE001 — never break on a knob fault
            logger.warning(
                "mission_records: %s unresolved (%s) — the reconciliation "
                "lane stays INERT",
                MISSION_RECORDS_MODE_ENV,
                exc,
            )
            return MissionRecordMode.OFF

    def _commission_age_seconds(self, commission: Any) -> float | None:
        """The commission's age in seconds — parsed in PYTHON.

        ``None`` when ``created_at`` is missing or unparsable (the
        ``parse_iso_utc`` contract): the caller HOLDS the row — age is
        never guessed. The TEXT-ISO column is NEVER string-compared in
        SQL (the report-recipient rule-4 precedent).
        """
        created_at = getattr(commission, "created_at", None)
        parsed = parse_iso_utc(created_at) if created_at else None
        if parsed is None:
            return None
        return max(0.0, (now_utc() - parsed).total_seconds())

    def _commission_worker_is_live(
        self, worker_id: str, commission: Any
    ) -> bool:
        """R3 — is the commissioned worker LIVE?

        Two legs, both from the SAME repository method families the
        mission-live guard reads (no consumer-side re-derivation):

        * the guard's member-alive leg (``instance_repo.has_live_work``),
          now with the guard's TASK-STAMP HORIZON attached (MAJ-4);
        * the JobItem-live check (OQ-7): a commission whose ``work_id``
          resolves to a NON-TERMINAL JobItem is live.

        **MAJ-4 — the horizon.** The member-alive leg used to be a
        bare ``bool(has_live_work(worker_id))`` — NO horizon — so a
        permanently-stalled RUNNING task read as live FOREVER: the
        commission was never abandoned, the L3 hold never released, and
        the only escape was StaleTaskRecovery (an unnamed
        cross-subsystem dependency). The horizon predicate is the
        guard's OWN (:func:`mission_live_guard.member_live_task_attempt_at`
        — reused, NOT re-derived: two spellings of one rule drift).

        WIRING TRUTH (PR3 review, 2026-10-26) — the second leg is
        IMPLEMENTED and WIRED, but currently UNREACHABLE in production:
        NO mint lane stamps ``work_id`` on the commission row. The
        ``send_message`` lane (``daemon/tools/instance.py``) and the
        ``instance_nudge`` lane (``daemon/tools/instance_ops.py``) mint
        commissions WITHOUT a work id; the only job-driven lane
        (``job_continue``, ``daemon/tools/job_queue.py``) attaches the
        work id to the EPOCH row (``mission_records.work_id``) and mints
        NO commission row. So every production commission takes the
        "no linkage → not live" branch below.

        The sweep stays FAIL-SAFE: the worker-side
        ``instance_repo.has_live_work`` leg covers the JobItem-queued-
        on-the-worker case, so a live worker is never abandoned at any
        age. The wiring gap is recorded as an explicit OPEN item (the
        B-arc closure table §(d.2)) — stamping the linkage at a mint
        lane is the follow-up, deliberately NOT invented here.

        Raises:
            MissionReconciliationProbeError: the probe could not run
                (an unwired dependency) — the caller HOLDS (P1).
        """
        live = bool(self._instance_repo.has_live_work(worker_id))
        if live:
            # MAJ-4: the horizon leg — a member whose newest
            # non-terminal task stamp is OLDER than the horizon is NOT
            # live for sweep purposes (a permanently-stalled RUNNING
            # task would otherwise read live forever). A stalled member
            # falls THROUGH to the JobItem leg, which may still find
            # live work under a different linkage.
            if self._member_is_within_the_liveness_horizon(worker_id):
                return True
        work_id = getattr(commission, "work_id", None)
        if not work_id:
            # No job linkage → the JobItem leg does not apply (NOT an
            # error: there is nothing to probe).
            return False
        get_job = getattr(self._resolve_job_repo(), "get", None)
        if not callable(get_job):
            raise MissionReconciliationProbeError(
                "job_repo is not wired — the JobItem-live check cannot run"
            )
        job = get_job(work_id)
        if job is None:
            # A missing JobItem is NOT live work (the row simply does
            # not exist — the T2 shape is handled by the WORKER read).
            return False
        admission_state = getattr(job, "admission_state", None)
        # The "LIVE work" membership set comes from the SINGLE SOURCE
        # (``ACTIVE_ADMISSION_STATES`` — the set every membership
        # filter routes through; a local copy would be a second
        # spelling of the same set).
        return admission_state in ACTIVE_ADMISSION_STATES

    def _member_is_within_the_liveness_horizon(self, worker_id: str) -> bool:
        """MAJ-4 — is the member's live work WITHIN the stall horizon?

        The predicate is the mission-live guard's OWN task-stamp probe
        (:func:`mission_live_guard.member_live_task_attempt_at`) —
        REUSED, not re-derived: the guard and the sweep must not hold
        two spellings of one rule.

        **THE HORIZON: the sweep's OWN TTL**
        (``self._mission_commission_ttl_seconds``), NOT the guard's
        ``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS``. Rationale: the TTL is
        the horizon the sweep's abandon backstop is measured against,
        so a member's stall is judged on the SAME clock that will
        release the row — one horizon family inside the sweep. (The
        guard's orphan timeout belongs to the mission-finalisation
        backstop: a different subsystem with a different lifecycle.
        The two happen to share the 6 h default.)

        **Fail-safe on a probe fault.** A probe that cannot run (no
        ``engine`` on the repository, a DB error) cannot prove that
        the member is stalled, so the member is treated as LIVE and
        the row is HELD. Only a SUCCESSFUL "the stamp is older than
        the horizon" reading makes the member not-live.

        A member with NO dateable non-terminal task row (no task row at
        all, or an undateable one) keeps the live verdict — there is no
        evidence of a stalled turn, and the sweep must not abandon a
        member whose live work is simply not task-row-backed (the
        guard's own H2/C2 posture).

        Returns:
            ``True`` when the member is (still) within the horizon —
            i.e. live for sweep purposes.
        """
        from daemon.services.mission_live_guard import (
            member_live_task_attempt_at,
        )

        try:
            attempt_at = member_live_task_attempt_at(
                self._instance_repo, worker_id
            )
        except Exception as exc:  # noqa: BLE001 — fail-safe: HOLD
            logger.warning(
                "sweep mission_reconciliation task-stamp probe failed "
                "worker=%s: %s: %s — treating the member as LIVE "
                "(fail-safe: the row is held)",
                worker_id,
                type(exc).__name__,
                exc,
            )
            return True
        if attempt_at is None:
            return True
        # NAIVE-UTC age math (the naive-timestamp convention: never mix
        # an aware stamp with a naive ``now``).
        age_seconds = (now_utc_naive() - attempt_at).total_seconds()
        return age_seconds < self._mission_commission_ttl_seconds

    def _resolve_mission_record_service(self) -> Any:
        """The mission-record SERVICE — lane 7's ONE resolution writer.

        **MAJ-6 — single writer.** The sweep's resolution writes go
        through the SERVICE (``satisfy_commission`` /
        ``abandon_commission``), NOT through a second repository-level
        guarded UPDATE. The service owns the ``resolved_at`` /
        ``satisfied_at`` stamps; two implementations of one guarded
        UPDATE are exactly how those stamps drift (one writer stamped
        ``resolved_at``, the other did not).

        The service is wired EXPLICITLY at construction (the pool
        orchestrator passes it). The manager's PUBLIC accessor is the
        fallback — and it accepts the REAL service type only: a
        duck-typed stand-in would "win" every guarded transition
        without touching a row.

        Returns:
            The service, or ``None`` when it is not wired (the lane
            degrades to inert in ``enforce``).
        """
        if self._mission_record_service is not None:
            return self._mission_record_service
        from daemon.services.mission_record_service import MissionRecordService

        service = getattr(self._manager, "mission_record_service", None)
        if isinstance(service, MissionRecordService):
            return service
        return None

    def _pending_bus_watchers(
        self, commissioner_id: str, worker_id: str
    ) -> list[Any]:
        """R4 — the PENDING bus watchers for the (commissioner, worker) pair.

        Raises:
            MissionReconciliationProbeError: the bus is not wired — the
                caller HOLDS (P1: we cannot prove absence).
        """
        probe = getattr(
            self._resolve_dependency_bus(), "pending_watchers_for_pair", None
        )
        if not callable(probe):
            raise MissionReconciliationProbeError(
                "dependency_bus is not wired — the bus-watcher probe "
                "cannot run"
            )
        return list(probe(commissioner_id, worker_id))

    def _resolve_dependency_bus(self) -> Any:
        """The dependency bus (the injected one, else the singleton).

        LAZY resolution on purpose: the bus singleton is registered
        AFTER the recovery service is wired (the boot order), so the
        constructor-time value is often ``None``. The probe resolves it
        at CALL time; an absent bus HOLDs the row (fail-MISS) — the
        sweep never assumes "no watcher" from a missing dependency.
        """
        if self._dependency_bus is not None:
            return self._dependency_bus
        try:
            from daemon.services.dependency_bus import get_dependency_bus

            return get_dependency_bus()
        except Exception:  # noqa: BLE001 — the probe HOLDs on a miss
            return None

    def _resolve_job_repo(self) -> Any:
        """The job repository (the injected one, else the manager's).

        LAZY resolution on purpose: the JobQueueService is wired AFTER
        the recovery service (the boot order). The PUBLIC accessors are
        used on both hops — ``InstanceManager.job_queue_service`` then
        ``JobQueueService.repository`` — so there is NO private
        ``_job_queue_service`` / ``_repository`` reach-through. An
        absent service degrades to ``None`` (the caller HOLDS the row).
        """
        if self._job_repo is not None:
            return self._job_repo
        service = getattr(self._manager, "job_queue_service", None)
        if service is None:
            return None
        return getattr(service, "repository", None)

    def _delivery_evidence_exists(
        self, commissioner_id: str, worker_id: str, commission: Any
    ) -> bool:
        """R1/R1b — the delivery-evidence TRILET, read on the COMMISSIONER.

        The lane-2 evidence helpers, reused on the COMMISSIONER as the
        recipient (the commissioner's report is the obligation):

        1. an injection row in a DELIVERED state (``INJECTED`` /
           ``TASK_DELIVERED``) for the (commissioner, worker) pair,
           **bounded to THIS commission** (MAJ-1: a row strictly older
           than the commission's mint is an EARLIER commission's
           evidence, not this one's);
        2. the queue-side PREFIX ledger (``internal_report:{worker}:%``);
        3. the parent-history PREFIX ledger (loop-bound → bridged onto
           the manager's loop via ``_run_async_on_manager_loop``).

        **MAJ-1 SCOPE, stated honestly:** the commission bound applies to
        leg 1 (the injection-row read — the one the ruling names). Legs
        2 and 3 are PAIR-keyed PREFIX ledgers (they carry no commission
        id), so a delivery they record for an EARLIER commission on the
        same pair still reads as evidence. Narrowing them is NOT in this
        PR's ruling scope; the residual is recorded in the B-arc closure
        table's deferral list.

        Raises:
            MissionReconciliationProbeError: a probe could not run — the
                caller HOLDS (P1).
        """
        find_pair = getattr(
            self._report_injection_repo, "find_delivered_for_pair", None
        )
        if not callable(find_pair):
            raise MissionReconciliationProbeError(
                "report_injection_repo lacks find_delivered_for_pair — the "
                "injection-row probe cannot run"
            )
        delivered_rows = list(find_pair(commissioner_id, worker_id))
        if delivered_rows and self._delivered_row_is_this_commission(
            commission, delivered_rows
        ):
            return True

        queue_evidence = getattr(
            self._queue_repo, "find_wake_already_delivered_evidence", None
        )
        if not callable(queue_evidence):
            raise MissionReconciliationProbeError(
                "queue_repo lacks find_wake_already_delivered_evidence — "
                "the queue-side PREFIX probe cannot run"
            )
        if queue_evidence(commissioner_id, worker_id):
            return True

        checkpointer = getattr(self._manager, "_checkpointer", None)
        if checkpointer is None:
            # Without the parent-history ledger we cannot PROVE absence
            # → HOLD (P1). The lane-2 delivery path degrades to
            # "no evidence" because a MISS there is the normal case;
            # here a miss authorizes a DESTRUCTIVE transition.
            raise MissionReconciliationProbeError(
                "no checkpointer — the parent-history PREFIX probe cannot run"
            )
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )

        return bool(
            self._run_async_on_manager_loop(
                parent_history_has_internal_report,
                checkpointer,
                commissioner_id,
                worker_id,
                None,
            )
        )

    def _delivered_row_is_this_commission(
        self, commission: Any, delivered_rows: list[Any]
    ) -> bool:
        """Is one of the DELIVERED injection rows THIS commission's evidence?

        **MAJ-1 — the commission linkage.** A delivered injection row
        that is STRICTLY OLDER than the commission's mint belongs to an
        EARLIER commission on the same (commissioner, worker) pair: it
        proves that the EARLIER obligation was delivered, not this one.
        Without the bound, a prior commission's delivery SATISFIED a
        fresh commission (premature publish — the D2 harm re-created
        from the far side).

        **PYTHON-SIDE by ruling.** The two columns carry different ISO
        shapes — the commission's ``now_utc_iso()`` vs the injection
        row's ``datetime.now(timezone.utc).isoformat()`` — so a TEXT
        comparison across shapes is not safe, and the TEXT-ISO
        convention forbids a string comparison in SQL anyway. The
        comparison is therefore made on PARSED, tz-aware UTC instants
        (``parse_iso_utc`` — the sweep's own stamp parser).

        **Fail-MISS on an undateable stamp.** An unparsable commission
        mint or an unparsable row stamp cannot PROVE the row is not
        older than the mint ⇒ it is not evidence (the row is held, not
        satisfied). A row whose stamp EQUALS the mint counts as
        evidence (only a STRICTLY older row is excluded).
        """
        minted_at = parse_iso_utc(getattr(commission, "created_at", None))
        if minted_at is None:
            return False
        for row in delivered_rows:
            delivered_at = parse_iso_utc(getattr(row, "created_at", None))
            if delivered_at is None:
                continue
            if delivered_at >= minted_at:
                return True
        return False

    def _note_masked_enqueue(
        self,
        commission: Any,
        out: MissionReconciliationLaneResult,
        *,
        age_seconds: float,
    ) -> bool:
        """§6 — the MASKED-ENQUEUE observability (counter + log ONLY).

        A commission whose commissioning act left NO queue row at all
        (whole-window absence — any source, any status) past the grace
        window (= the TTL) is the conservative "the enqueue was masked"
        signal (D4(iii)).

        NO AUTO-ACTION: a masked commission is NOT abandoned on this
        signal alone (the worker may be a live target of a sub-lane that
        legitimately writes no MessageQueue row), and it is NOT
        re-delivered (no re-commissioning machinery is in scope).

        **MAJ-3 — the fault HALDS the row.** A probe fault is not a
        decision fault, but it is still a fault: the row is NOT
        resolved in this pass. The method now RETURNS ``True`` on a
        fault and the callers HOLD the row (no abandon, no satisfy).
        The old shape ("it increments ``probe_errors`` but never HOLDS
        the row") let the caller proceed to the abandon / the evidence
        probe in the SAME pass — while ``probe_errors``' contract says
        "the row was HELD". The counter was lying.

        The fault also has its OWN counter now
        (``masked_probe_errors``): ``probe_errors`` means "a DECISION
        probe could not prove its predicate ⇒ the row was HELD", and
        borrowing it for an OBSERVABILITY probe broke that contract.

        Returns:
            ``True`` when the masked probe FAILED (the caller MUST hold
            the row for this pass); ``False`` when the probe ran
            (whether or not the masked signal fired).
        """
        if age_seconds <= self._mission_commission_ttl_seconds:
            return False
        try:
            has_rows = self._queue_repo.has_row_since(
                commission.worker_instance_id,
                self._commission_created_at_naive(commission),
            )
        except Exception as exc:
            out.masked_probe_errors += 1
            logger.warning(
                "sweep mission_reconciliation masked-probe failed "
                "mission_id=%s, commission_id=%s, worker=%s: %s: %s "
                "— HOLDING the row for this pass",
                commission.mission_id,
                commission.commission_id,
                commission.worker_instance_id,
                type(exc).__name__,
                exc,
            )
            return True
        if has_rows:
            return False
        out.masked_suspected += 1
        logger.warning(
            "sweep mission_reconciliation MASKED ENQUEUE suspected "
            "mission_id=%s, commission_id=%s, commissioner=%s, worker=%s, "
            "lane=%s, age_seconds=%d — no queue row since the mint; "
            "NO auto-action",
            commission.mission_id,
            commission.commission_id,
            commission.commissioner_instance_id,
            commission.worker_instance_id,
            getattr(commission, "lane", ""),
            int(age_seconds),
        )
        return False

    def _commission_created_at_naive(self, commission: Any):
        """The commission's mint time as a NAIVE UTC datetime.

        The ``message_queue.enqueued_at`` column is a
        ``timestamp without time zone`` column — the PG-UTC convention
        binds NAIVE UTC values (a tz-aware bind would render in the
        session TimeZone).
        """
        parsed = parse_iso_utc(getattr(commission, "created_at", None))
        if parsed is None:
            raise MissionReconciliationProbeError(
                "commission created_at is unparsable"
            )
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)

    # --------------------------------------------------------
    # Lane 7 — the writes (R5 guarded transition + R7 mode ladder)
    # --------------------------------------------------------

    def _abandon_commission(
        self,
        commission: Any,
        out: MissionReconciliationLaneResult,
        *,
        mode: str,
        reason: str,
        age_seconds: float,
    ) -> None:
        """R5/R7 — the guarded ABANDONMENT.

        ONE guarded statement (``UPDATE ... WHERE state='outstanding'``);
        ``rowcount == 0`` means another actor won the race →
        ``already_resolved``, NEVER a retry in place.

        The abandon-reason vocabulary is FROZEN (the design's OQ-4):
        ``worker_terminal_no_report`` / ``ttl_expired_not_live`` /
        ``worker_row_missing``. ``superseded`` is reserved to the mint
        lanes and is never written here.
        """
        if reason not in MISSION_COMMISSION_ABANDON_REASONS:
            raise ValueError(
                f"unknown mission-commission abandon reason: {reason!r}"
            )
        enforcing = mode == MISSION_RECORDS_MODE_ENFORCE
        if enforcing:
            service = self._resolve_mission_record_service()
            if service is None:
                # No writer wired → the lane is inert in enforce (the
                # lane-level degrade already returned ZERO); this is the
                # belt-and-braces guard.
                logger.warning(
                    "sweep mission_reconciliation ABANDON skipped "
                    "(mission_id=%s, commission_id=%s) — the "
                    "mission-record service is not wired",
                    commission.mission_id,
                    commission.commission_id,
                )
                return
            resolved = service.abandon_commission(
                commission.commission_id,
                reason=reason,
            )
            if not resolved:
                out.already_resolved += 1
                logger.info(
                    "sweep mission_reconciliation already_resolved "
                    "mission_id=%s, commission_id=%s, commissioner=%s, "
                    "worker=%s, lane=%s, abandon_reason=%s",
                    commission.mission_id,
                    commission.commission_id,
                    commission.commissioner_instance_id,
                    commission.worker_instance_id,
                    getattr(commission, "lane", ""),
                    reason,
                )
                return
            out.recovered += 1
        # The DETECTION counters move in every mode (in ``dry`` they are
        # the dry-run evidence that tunes the TTL default).
        if reason == MISSION_ABANDON_REASON_WORKER_TERMINAL:
            out.abandoned_worker_terminal += 1
        elif reason == MISSION_ABANDON_REASON_TTL:
            out.abandoned_ttl += 1
        else:
            out.abandoned_worker_missing += 1
        # WARNING is for sweep ACTIONS only (a REAL ledger write); the
        # dry-run preview is INFO (a dry preview is not an action, and a
        # WARNING that fires on every dry pass drowns the real signal).
        log = logger.warning if enforcing else logger.info
        log(
            "sweep mission_reconciliation %s mission_id=%s, "
            "commission_id=%s, commissioner=%s, worker=%s, lane=%s, "
            "abandon_reason=%s, age_seconds=%d",
            "ABANDONED" if enforcing else "WOULD ABANDON (dry-run)",
            commission.mission_id,
            commission.commission_id,
            commission.commissioner_instance_id,
            commission.worker_instance_id,
            getattr(commission, "lane", ""),
            reason,
            int(age_seconds),
        )

    def _satisfy_commission_on_evidence(
        self,
        commission: Any,
        out: MissionReconciliationLaneResult,
        *,
        mode: str,
        age_seconds: float,
    ) -> None:
        """R1b — SATISFY ON EVIDENCE (the mirror of R1).

        The worker reached a terminal state (or not) WITH positive
        delivery evidence while the row is still ``outstanding``: the
        natural satisfy write was lost (a crash between the delivery and
        the satisfy). Positive evidence is PROOF, so the same guarded
        transition and the same fail-MISS probing apply.
        """
        enforcing = mode == MISSION_RECORDS_MODE_ENFORCE
        if enforcing:
            service = self._resolve_mission_record_service()
            if service is None:
                logger.warning(
                    "sweep mission_reconciliation SATISFY skipped "
                    "(mission_id=%s, commission_id=%s) — the "
                    "mission-record service is not wired",
                    commission.mission_id,
                    commission.commission_id,
                )
                return
            resolved = service.satisfy_commission(commission.commission_id)
            if not resolved:
                out.already_resolved += 1
                logger.info(
                    "sweep mission_reconciliation already_resolved "
                    "(satisfy-on-evidence) mission_id=%s, commission_id=%s, "
                    "commissioner=%s, worker=%s, lane=%s",
                    commission.mission_id,
                    commission.commission_id,
                    commission.commissioner_instance_id,
                    commission.worker_instance_id,
                    getattr(commission, "lane", ""),
                )
                return
            out.recovered += 1
        out.satisfied_on_evidence += 1
        # WARNING is for sweep ACTIONS only (a REAL ledger write); the
        # dry-run preview is INFO.
        log = logger.warning if enforcing else logger.info
        log(
            "sweep mission_reconciliation %s mission_id=%s, "
            "commission_id=%s, commissioner=%s, worker=%s, lane=%s, "
            "satisfy_reason=delivery_evidence, age_seconds=%d",
            "SATISFIED" if enforcing else "WOULD SATISFY (dry-run)",
            commission.mission_id,
            commission.commission_id,
            commission.commissioner_instance_id,
            commission.worker_instance_id,
            getattr(commission, "lane", ""),
            int(age_seconds),
        )
