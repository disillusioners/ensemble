"""Periodic report-delivery recovery service.

Phase 2 (pause-report-recovery, tasks 2.4 + 2.5 + 2.6). The recovery
sweep is a periodic background thread that drains every recoverable
report-delivery obligation across the system. Five lanes run in a
single sweep pass; each lane is independently kill-switchable via
config (``lane_deferred``, ``lane_no_row_backstop``,
``lane_pending_age``, ``lane_recovery_retry``, ``lane_orphan``).

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

Five lanes (v3, no one-time-only lanes — W9, no-row backstop
designed-in — C3, terminal-parent ORPHAN — W1):

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
   duplicates), then the router's transition + reconcile + re-enter
   path.

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

Per-row invariants (every lane):

* Skip if ``has_instance_busy(parent_id)`` — a live parent turn wins
  (the natural path will drain the report when its turn resumes).
* TOCTOU re-check inside the claim — re-fetch the injection row
  state and the parent's status before mutating.
* ``transition_deferred_to_pending`` is rowcount-guarded (rowcount=0
  = another actor already recovered → skip).
* ``ensure_deferred`` absorbs ``IntegrityError`` on the obligation-
  triple index (W6 — the router/sweep NEVER see raw IntegrityError).
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
        "deferred": {"recovered": N, "skipped_busy": N, "already_recovered": N, "errors": N},
        "no_row_backstop": {...},
        "pending_age": {...},
        "recovery_retry": {...},
        "orphan": {"recovered": N, "skipped_busy": N, "orphan_disposition": N, ...},
      },
      "total_recovered": N,
    }

Configuration (task 2.6): see ``daemon/config.py`` ``ServicesConfig``
— ``report_delivery_recovery_*`` knobs (interval, age bound, batch
cap, retry minutes, enabled, per-lane kill-switches).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from daemon.constants import (
    DEFERRED_REASON_RESUME_ROUTER,
)
from daemon.repositories.instance.models import InstanceStatus

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.repositories.report_injection.repository import (
        ReportInjectionRepository,
    )

logger = logging.getLogger(__name__)


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


@dataclass
class LaneResult:
    """Per-lane aggregated counts for a single sweep pass.

    Each lane returns this shape to the caller (the endpoint /
    tests). ``skipped_busy`` counts parents that had a live task
    when the sweep checked; ``already_recovered`` counts rows where
    ``transition_deferred_to_pending`` returned ``False`` (another
    actor won the race); ``errors`` counts per-row exceptions that
    left the row in a recoverable state.
    """

    recovered: int = 0
    skipped_busy: int = 0
    already_recovered: int = 0
    orphan_disposition: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "recovered": self.recovered,
            "skipped_busy": self.skipped_busy,
            "already_recovered": self.already_recovered,
            "orphan_disposition": self.orphan_disposition,
            "errors": self.errors,
        }


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
    """Periodic recovery sweep with five lanes (pause-report-recovery Phase 2).

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
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # --------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------

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
            f"orphan={self._lane_orphan}]"
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
        """
        return self._run_all_lanes_sync()

    # --------------------------------------------------------
    # Lane runner — five lanes, sequential, bounded
    # --------------------------------------------------------

    def _run_all_lanes_sync(self) -> SweepResult:
        """Run every lane once and aggregate.

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
                recovery_retry_minutes=0
            )
            if self._stop_event.is_set():
                self._finalize_sweep_result(result)
                return result
        if self._lane_recovery_retry:
            result.lanes["recovery_retry"] = self._run_pending_age_lane(
                recovery_retry_minutes=self._recovery_retry_minutes
            )
            if self._stop_event.is_set():
                self._finalize_sweep_result(result)
                return result
        if self._lane_orphan:
            result.lanes["orphan"] = self._run_orphan_lane()
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
                    f"sweep lane error child={row.child_instance_id[:8]}..., "
                    f"msg={row.child_message_id[:8]}..., "
                    f"reason={row.deferred_reason}: {type(exc).__name__}: {exc}"
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

        # Step 1: busy-check (widened PENDING + RUNNING + PAUSED via
        # ``has_instance_busy``).
        try:
            busy = self._task_repo.has_instance_busy(parent_id)
        except Exception as exc:
            # TOCTOU re-check failure: default to busy (safe — the
            # natural path will recover when its turn resumes).
            logger.warning(
                f"sweep busy-check failed parent={parent_id[:8]}...: "
                f"{type(exc).__name__}: {exc} — defaulting to busy"
            )
            busy = True
        if busy:
            result.skipped_busy += 1
            logger.debug(
                f"sweep skipped busy parent={parent_id[:8]}..., "
                f"child={child_id[:8]}..."
            )
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
                f"sweep reconcile+re-enter failed "
                f"child={child_id[:8]}..., msg={child_msg_id[:8]}...: "
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
                    f"sweep no_row lane error child={child_id[:8]}..., "
                    f"msg={child_msg_id[:8]}...: "
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

        1. Skip if ``has_instance_busy(parent_id)``.
        2. ``ensure_deferred(parent, child, child_msg, RESUME_ROUTER)``
           — write-once gate (W6 absorbs IntegrityError on
           duplicate).
        3. Hand off to the manager's reconcile + re-enter path.
        """
        # Step 1: busy-check.
        try:
            busy = self._task_repo.has_instance_busy(parent_id)
        except Exception as exc:
            busy = True
            logger.warning(
                f"sweep no_row busy-check failed parent={parent_id[:8]}...: "
                f"{type(exc).__name__}: {exc} — defaulting to busy"
            )
        if busy:
            result.skipped_busy += 1
            return

        # Step 2: ensure_deferred. W6: IntegrityError is absorbed by
        # ``ensure_deferred`` itself — the call returns ``None`` for
        # a concurrent duplicate (e.g. Site 1 already wrote a
        # DEFERRED row for the same triple). We proceed regardless
        # of the return value because the router/sweep NEVER see
        # raw IntegrityError.
        try:
            row = self._report_injection_repo.ensure_deferred(
                parent_instance_id=parent_id,
                child_instance_id=child_id,
                child_message_id=child_msg_id,
                deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
            )
        except Exception as exc:
            logger.warning(
                f"sweep ensure_deferred failed "
                f"child={child_id[:8]}..., msg={child_msg_id[:8]}...: "
                f"{type(exc).__name__}: {exc}"
            )
            result.errors += 1
            return

        # ``row is None`` means a concurrent duplicate was absorbed
        # (W6) — the OTHER actor (Site 1 / another sweep cycle) has
        # the obligation; we treat it as already_recovered and skip
        # to avoid duplicate transitions. The other actor's
        # reconcile+re-enter path will recover the row.
        if row is None:
            result.already_recovered += 1
            return

        # Step 3: hand off to the manager's reconcile + re-enter
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
                f"sweep no_row reconcile+re-enter failed "
                f"child={child_id[:8]}..., msg={child_msg_id[:8]}...: "
                f"{type(exc).__name__}: {exc}"
            )
            raise
        result.recovered += 1

    # --------------------------------------------------------
    # Lanes 3 + 4 — Age-bounded PENDING + retry
    # --------------------------------------------------------

    def _run_pending_age_lane(
        self, *, recovery_retry_minutes: int
    ) -> LaneResult:
        """Process stranded PENDING rows past the age guard.

        Args:
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
                self._recover_one_pending_row(row, result=out)
            except Exception as exc:
                logger.warning(
                    f"sweep pending_age lane error "
                    f"injection_id={row.injection_id[:8]}...: "
                    f"{type(exc).__name__}: {exc}"
                )
                out.errors += 1
        return out

    def _recover_one_pending_row(
        self, row: Any, *, result: LaneResult
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

        # Step 1: busy-check.
        try:
            busy = self._task_repo.has_instance_busy(parent_id)
        except Exception as exc:
            busy = True
            logger.warning(
                f"sweep pending_age busy-check failed "
                f"parent={parent_id[:8]}...: "
                f"{type(exc).__name__}: {exc} — defaulting to busy"
            )
        if busy:
            result.skipped_busy += 1
            return

        # Step 2: hand off. No transition (the row is already
        # PENDING) — just reconcile + re-enter.
        try:
            self._manager._handle_recover_deferred_report(
                child_instance_id=child_id,
                child_message_id=child_msg_id,
                injection_id=row.injection_id,
                source=(
                    "sweep_pending_age"
                    if self._recovery_retry_minutes == 0
                    else "sweep_recovery_retry"
                ),
            )
        except Exception as exc:
            logger.warning(
                f"sweep pending_age reconcile+re-enter failed "
                f"child={child_id[:8]}..., msg={child_msg_id[:8]}...: "
                f"{type(exc).__name__}: {exc}"
            )
            raise
        result.recovered += 1
