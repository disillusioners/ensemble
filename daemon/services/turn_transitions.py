"""Named, transaction-owned turn lifecycle transitions.

This is the foundation surface for the turn reconciler migration.  Transitions
only perform database work and return an outbox description; callers own the
transaction and post-commit dispatch.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import local
from typing import Any, ClassVar

from sqlalchemy import text

from daemon.repositories.task.models import SuspensionReason, TaskStatus

ALL_8_MIRRORS: frozenset[str] = frozenset({
    "task", "job_queue_items", "message_queue", "job_locks",
    "dependency_watchers", "report_injections", "instances", "job_watchers",
})

@dataclass(frozen=True)
class TransitionResult:
    work_id: str
    instance_id: str | None
    old_status: str | None
    new_status: str
    mirrors_touched: frozenset[str]
    rowcount: int = 0
    cross_turn_side_effects: tuple[str, ...] = ()
    wakeup_payload: dict | None = None
    sse_payload: dict | None = None
    watcher_notify: tuple[str, ...] = ()

_guard = local()
class _TransitionContext:
    """Thread-local status-write guard (disabled until the guard feature lands)."""
    def __enter__(self):
        self._previous = getattr(_guard, "enabled", False)
        _guard.enabled = True
        return self
    def __exit__(self, *_):
        _guard.enabled = self._previous
        return False

class _Transition(ABC):
    MIRROR_SET: ClassVar[frozenset[str]]
    @abstractmethod
    def run(self, session: Any) -> TransitionResult: ...

class _StatusTransition(_Transition):
    # ``connection=`` kwarg threaded to ``reconcile_turn_mirror`` (F1
    # fix): callers that already hold an open transaction (the
    # ``_pattern_e_dead_letter_dead_parent_process_reports`` sweep
    # opens ``engine.begin()`` once for the entire batch) must NOT
    # let ``reconcile_turn_mirror`` open a nested transaction against
    # the same engine — nested transactions self-deadlock on PostgreSQL
    # (no ``lock_timeout`` configured) and silently fail on file SQLite
    # (``OperationalError`` after busy-timeout, swallowed by the
    # per-row except — mirror reconcile is lost). ``connection=`` is
    # the canonical seam ``reconcile_turn_mirror`` already supports.
    status_from: ClassVar[str | None] = None
    status_to: ClassVar[str] = ""
    def __init__(self, work_id: str, task_repo: Any = None, instance_id: str | None = None, **_: Any):
        self.work_id, self.task_repo, self.instance_id = work_id, task_repo, instance_id
    def _write(self, session: Any, new: str, old: str | None = None) -> int:
        where = "work_id = :work_id" + (" AND status = :old_status" if old else "")
        params = {"work_id": self.work_id, "new_status": new}
        if old: params["old_status"] = old
        result = session.execute(text(f"UPDATE task SET status = :new_status WHERE {where}"), params)
        return getattr(result, "rowcount", 1)
    def _reconcile(self, connection: Any | None = None):
        # See class docstring above for the F1 threading rationale.
        if self.task_repo is not None and hasattr(self.task_repo, "reconcile_turn_mirror"):
            return self.task_repo.reconcile_turn_mirror(self.work_id, connection=connection)
    def _result(
        self,
        old: str | None,
        new: str,
        touched: frozenset[str] | None = None,
        rowcount: int = 0,
        **kw,
    ):
        # ``cross_turn_side_effects`` is a positional field on
        # ``TransitionResult`` (6th position). The concrete transitions
        # (CompleteTurn / AbortTurn / etc.) pass it as a kwarg AND we
        # need to forward it positionally — ``pop`` it from kwargs so
        # the call does not collide. Without the pop, every transition
        # ``run()`` raises ``TypeError: TransitionResult.__init__()
        # got multiple values for argument 'cross_turn_side_effects'``.
        cross_turn = kw.pop("cross_turn_side_effects", ())
        return TransitionResult(
            work_id=self.work_id,
            instance_id=self.instance_id,
            old_status=old,
            new_status=new,
            mirrors_touched=touched or self.MIRROR_SET,
            rowcount=rowcount,
            cross_turn_side_effects=cross_turn,
            **kw,
        )

class BeginTurn(_Transition):
    """BEGIN_TURN transition — Task creation path.

    Phase 4b stub: this transition is defined but has zero production
    callers currently. The Task-insertion path in repository.py will
    be migrated to call BeginTurn.run() in a future call-site migration
    phase. The MIRROR_SET and run() body are complete and tested.
    """
    MIRROR_SET = frozenset({"task", "job_queue_items", "message_queue", "job_locks"})
    def __init__(self, task_type: str, instance_id: str | None, message_id: str | None, work_id: str, task_repo: Any = None, **kwargs):
        self.task_type, self.instance_id, self.message_id, self.work_id, self.task_repo = task_type, instance_id, message_id, work_id, task_repo
    def run(self, session):
        result = session.execute(text("INSERT INTO task (work_id, status, instance_id) VALUES (:work_id, :status, :instance_id)"), {"work_id": self.work_id, "status": "pending", "instance_id": self.instance_id})
        rowcount = getattr(result, "rowcount", 1)
        if self.task_repo and hasattr(self.task_repo, "reconcile_turn_mirror"): self.task_repo.reconcile_turn_mirror(self.work_id)
        return TransitionResult(
            work_id=self.work_id,
            instance_id=self.instance_id,
            old_status=None,
            new_status="pending",
            mirrors_touched=self.MIRROR_SET,
            rowcount=rowcount,
            cross_turn_side_effects=("instance_running",),
            sse_payload={"event": "turn_started", "work_id": self.work_id},
        )

class ClaimTurn(_StatusTransition):
    """CLAIM_TURN transition — Worker picks up a pending Task.

    Phase 4b stub: this transition is defined but has zero production
    callers currently. The Task-claim path in worker_pool.py will be
    migrated to call ClaimTurn.run() in a future call-site migration
    phase. The MIRROR_SET and run() body are complete and tested.
    """
    MIRROR_SET = frozenset({"task", "job_queue_items", "job_locks"})
    def __init__(self, work_id, worker_id, task_repo=None, **kwargs): super().__init__(work_id, task_repo, kwargs.get("instance_id")); self.worker_id=worker_id
    def run(self, session):
        rowcount = self._write(session, "running", "pending")
        self._reconcile()
        return self._result(
            "pending",
            "running",
            rowcount=rowcount,
            wakeup_payload={"event": "turn_claimed", "work_id": self.work_id},
        )

_SUSPENSION_REASON_VALUES: frozenset[str] = frozenset({
    s.value for s in SuspensionReason
})


class SuspendTurn(_StatusTransition):
    """SUSPEND_TURN transition — declare suspension intent on a running Task.

    Phase 3 (Increment 4, 2026-08-01). This transition persists the
    suspension HANDLE atomically with the ``RUNNING → PAUSED`` status
    transition so a later resume can target the recorded
    ``resume_target_turn_id`` without re-deriving it from a set of
    task statuses.

    The transition writes, in a single UPDATE (guarded by
    ``status='running'``):

      * ``status='paused'``  (the existing transition)
      * ``suspension_reason=:reason``  (one of awaiting_answer /
        awaiting_children / paused_external — see §7; new reason
        values MAY be added to ``SuspensionReason`` over time, in
        which case the validation here accepts them)
      * ``resume_target_turn_id=:target``  (the ``task.work_id`` of the
        turn a later resume should reattach to; may be ``None`` for
        reasons that do not require a target)

    Field validation (§7 invariant 2): ``suspension_reason=
    'awaiting_answer'`` REQUIRES a non-null ``resume_target_turn_id``.
    Non-answer reasons MAY omit the target. The transition raises
    ``ValueError`` before the UPDATE when the field shape is
    invalid — the caller sees the validation failure synchronously
    rather than discovering a NULL-handle row after commit.

    Args:
        work_id: The Task being suspended (must currently be
            ``status='running'``).
        reason: A valid ``SuspensionReason`` enum value
            (awaiting_answer / awaiting_children / paused_external).
        resume_target_turn_id: Optional Task ``work_id`` of the turn
            to resume onto. Required when ``reason='awaiting_answer'``.
        task_repo: TaskRepository for mirror reconciliation.
        **kwargs: Forwarded to ``_StatusTransition.__init__`` —
            ``instance_id`` is captured for the outbox result.
    """
    MIRROR_SET = frozenset({"task", "instances"})
    def __init__(
        self,
        work_id,
        reason,
        resume_target_turn_id=None,
        task_repo=None,
        **kwargs,
    ):
        super().__init__(work_id, task_repo, kwargs.get("instance_id"))
        # Validate the reason value before touching the database. Only
        # the declared SuspensionReason vocabulary is accepted.
        if reason not in _SUSPENSION_REASON_VALUES:
            raise ValueError(
                f"SuspendTurn(reason={reason!r}) is not a valid SuspensionReason; "
                f"valid values: {sorted(_SUSPENSION_REASON_VALUES)}."
            )
        # Strict invariant (§7.2): ``awaiting_answer`` requires a
        # non-null target. Other reasons may omit the target.
        if reason == SuspensionReason.AWAITING_ANSWER.value and not resume_target_turn_id:
            raise ValueError(
                f"SuspendTurn(reason='awaiting_answer') requires a "
                f"non-null resume_target_turn_id (§7 invariant 2)."
            )
        self.reason = reason
        self.resume_target_turn_id = resume_target_turn_id

    def run(self, session):
        # Single guarded UPDATE writes status + both handle fields
        # atomically. The ``status='running'`` guard is the race gate
        # — a competing transition cannot sneak past and leave a
        # half-written handle row.
        result = session.execute(
            text(
                "UPDATE task SET status = :new_status, "
                "suspension_reason = :suspension_reason, "
                "resume_target_turn_id = :resume_target_turn_id "
                "WHERE work_id = :work_id AND status = :old_status"
            ),
            {
                "new_status": "paused",
                "suspension_reason": self.reason,
                "resume_target_turn_id": self.resume_target_turn_id,
                "work_id": self.work_id,
                "old_status": "running",
            },
        )
        rowcount = getattr(result, "rowcount", 1)
        return self._result(
            "running",
            "paused",
            cross_turn_side_effects=("instance_paused",),
            wakeup_payload={
                "event": "graph_task_cancel",
                "work_id": self.work_id,
                "suspension_reason": self.reason,
                "resume_target_turn_id": self.resume_target_turn_id,
            },
            rowcount=rowcount,
            # Surface the actual UPDATE rowcount so the caller can
            # detect a swallowed guard (zero rows means the Task
            # was no longer RUNNING when the transition fired — the
            # caller should treat this as a stale invocation).
        )


class ResumeTurn(_StatusTransition):
    """RESUME_TURN transition — consume the suspension handle on resume.

    Phase 4b/4c (2026-08-12, pause/resume redesign). This transition:

      1. Transitions the Task ``PAUSED → PENDING`` (was ``CANCELLED``
         pre-migration). The new behavior keeps the same ``work_id``
         live throughout the pause/resume cycle, so the worker pool
         can re-claim it naturally and the LangGraph checkpoint
         reloads under the same ``work_id`` (matching the documented
         design intent). This closes the T2–T4 race window the prior
         cancel-and-recreate flow opened (the architecture
         recommendation §4 amplifier risk).
      2. CLEARS ``suspension_reason=NULL, resume_target_turn_id=NULL``
         in the same guarded UPDATE so the handle is atomically
         consumed (§7 invariant 7: "successful resume consumes the
         handle exactly once").
      3. Calls ``reconcile_turn_mirror`` to bring JobItem,
         MessageQueue, lock, watcher, and instance mirrors into
         the prescribed state. The Task is NOT terminal
         (``status='pending'``) so the reconciler does NOT mark
         linked ``message_queue`` rows as completed — the worker
         pool will claim and drive the Task naturally, and the
         message's terminal status follows the Task's natural
         ``complete_task`` / ``fail_task`` call.

    A duplicate answer-gate resume sees the cleared handle and
    becomes idempotent (§9.1 step 10); the answer-gate selector
    ``find_suspended_turn_for_answer`` returns ``None`` once the
    handle is consumed.

    ``wakeup_payload.work_id`` is always ``self.work_id`` (the same
    Task that was paused is the one that will be re-claimed). The
    legacy ``new_work_id`` parameter is retained for backward
    compatibility with the cancel-and-recreate call sites but is
    always ``None`` in production — the cascade path no longer mints
    a fresh child ``work_id`` for the resumed Task.

    Lifecycle (post Phase 4b/4c):

      1. Pause: ``task`` RUNNING → PAUSED (in
         ``_pause_cascade_db_sync``).
      2. Resume: ``task`` PAUSED → PENDING (this transition).
      3. WorkerPool OR ``_resume_processing_background`` claims:
         ``task`` PENDING → RUNNING.
      4. Worker / resume path: ``task`` RUNNING → COMPLETED/FAILED
         via ``complete_task`` / ``fail_task`` after the graph
         turn finishes.
    """
    MIRROR_SET = ALL_8_MIRRORS
    def __init__(self, work_id, task_repo=None, new_work_id=None, **kwargs):
        super().__init__(work_id, task_repo, kwargs.get("instance_id"))
        # ``new_work_id`` retained for backward compatibility with the
        # cancel-and-recreate flow but always None in production —
        # the resume keeps the same work_id (PAUSED → PENDING).
        self.new_work_id = new_work_id

    def run(self, session):
        # Phase 4b/4c: status + handle clear in one guarded UPDATE.
        # The ``status='paused'`` guard prevents racy concurrent
        # consumes from double-clearing the handle. The transition
        # is PAUSED → PENDING (was PAUSED → CANCELLED pre-migration) —
        # the Task stays live so the worker pool can re-claim it
        # under the same work_id (closes the T2–T4 race window the
        # cancel-and-recreate flow opened).
        result = session.execute(
            text(
                "UPDATE task SET status = :new_status, "
                "suspension_reason = NULL, "
                "resume_target_turn_id = NULL "
                "WHERE work_id = :work_id AND status = :old_status"
            ),
            {
                "new_status": "pending",
                "work_id": self.work_id,
                "old_status": "paused",
            },
        )
        rowcount = getattr(result, "rowcount", 1)
        self._reconcile()
        return self._result(
            "paused",
            "pending",
            rowcount=rowcount,
            cross_turn_side_effects=("instance_running",),
            wakeup_payload={
                # The wakeup payload points at the SAME work_id —
                # the Task stays alive throughout pause/resume.
                "event": "resume_claimed",
                "work_id": self.work_id,
            },
        )


class CompleteTurn(_StatusTransition):
    """COMPLETE_TURN transition — mark a Task completed.

    Phase 3 (Increment 4, 2026-08-01). On the terminal ``RUNNING →
    COMPLETED`` transition, the stale suspension handle is cleared
    (§7 invariant 9) so a completed historical Task cannot be
    selected later by answer-gate or pause-cascade selectors.
    """
    MIRROR_SET = ALL_8_MIRRORS
    def __init__(self, work_id, result, task_repo=None, **kwargs):
        super().__init__(work_id, task_repo, kwargs.get("instance_id"))
        self.result = result

    def run(self, session):
        # Phase 3: status + handle clear on terminalization.
        result = session.execute(
            text(
                "UPDATE task SET status = :new_status, "
                "suspension_reason = NULL, "
                "resume_target_turn_id = NULL "
                "WHERE work_id = :work_id AND status = :old_status"
            ),
            {
                "new_status": "completed",
                "work_id": self.work_id,
                "old_status": "running",
            },
        )
        rowcount = getattr(result, "rowcount", 1)
        self._reconcile()
        return self._result(
            "running",
            "completed",
            rowcount=rowcount,
            cross_turn_side_effects=("instance_completed", "cancel_dependency_watchers"),
            wakeup_payload={"event": "turn_completed", "work_id": self.work_id},
        )


class AbortTurn(_StatusTransition):
    """ABORT_TURN transition — terminate a running Task.

    Phase 3 (Increment 4, 2026-08-01). On the terminal
    ``RUNNING → FAILED|CANCELLED`` transition, the stale suspension
    handle is cleared (§7 invariant 9). If the Task was a pending
    answer-gate suspension, the answer selector will no longer find
    it; the answer payload will surface as ``None`` from
    ``find_suspended_turn_for_answer`` rather than a stale
    handle that routes onto a terminal Task.
    """
    MIRROR_SET = ALL_8_MIRRORS
    def __init__(self, work_id, reason, task_repo=None, error=None, **kwargs):
        super().__init__(work_id, task_repo, kwargs.get("instance_id"))
        self.reason = reason
        self.error = error

    def run(self, session):
        new = "failed" if self.reason == "failed" else "cancelled"
        # Phase 3: status + handle clear on terminalization.
        result = session.execute(
            text(
                "UPDATE task SET status = :new_status, "
                "suspension_reason = NULL, "
                "resume_target_turn_id = NULL "
                "WHERE work_id = :work_id AND status IN (:old_status, :alt_old_status)"
            ),
            {
                "new_status": new,
                "work_id": self.work_id,
                "old_status": "running",
                "alt_old_status": "paused",
            },
        )
        rowcount = getattr(result, "rowcount", 1)
        self._reconcile()
        return self._result(
            "running",
            new,
            rowcount=rowcount,
            cross_turn_side_effects=(
                ("instance_error" if new == "failed" else "instance_terminated"),
                "cancel_dependency_watchers",
            ),
            wakeup_payload={"event": "turn_aborted", "reason": self.reason, "work_id": self.work_id},
        )

class DeadLetterTurn(_StatusTransition):
    """DEAD_LETTER_TURN transition — terminal-write a PENDING Task as FAILED.

    Phase 1 / T8 (c) of the pause/resume/terminate tree-fix
    (``.agents/shared/planning/pause-resume-terminate-tree-fix/``,
    Rev 2.1, council 2bb126df). Replaces the Rev-1 plan's
    ``fail_task → AbortTurn(reason='failed')`` shape which was
    proven a no-op on PENDING rows:

      * ``fail_task`` pre-check ``prior_row[0] != RUNNING → return
        None`` (``task/repository.py`` D8 chokepoint — gates on
        RUNNING).
      * ``AbortTurn.run`` gates ``status IN ('running','paused')``
        (``turn_transitions.py:402-415``).
      * No named transition terminal-writes PENDING.

    ``DeadLetterTurn`` closes the gap for ``process_report`` Tasks
    that target a TERMINATED/missing parent — the B4 livelock root
    cause. Per leader D3 the canonical ``terminal_reason='failed'``
    is the operator-visible signal ("delivery did not succeed").
    ``'cancelled'`` is the architect-acceptable alternative but
    ``'failed'`` is preferred because it accurately distinguishes
    "delivery structurally impossible" from "user cancellation".

    Atomic shape: a single guarded UPDATE
    ``status='pending' → 'failed'`` so a racing
    ``claim_pending_task`` (which already transitioned the row to
    RUNNING) cannot double-fire. Rowcount 0 means the row was no
    longer PENDING when we ran — caller treats this as a stale
    invocation. Mirrors the transition-helper convention used by
    ``AbortTurn`` / ``ResumeTurn``.

    Companion ``reconcile_turn_mirror`` is invoked to propagate the
    terminal state across the 8-mirror set (``task``,
    ``job_queue_items``, ``message_queue``, ``job_locks``,
    ``dependency_watchers``, ``report_injections``, ``instances``,
    ``job_watchers``). The reconciler is the SOLE completion
    authority for ``JobItem`` rows — ``DeadLetterTurn`` itself never
    touches ``job_queue_items`` directly, preserving the turn-
    reconciler invariant from increment1-plan §5.1.

    Used by:

      * T8 (d) drift sweep (``job_recovery_service.reconcile_drift_states``
        pattern (e)) — for already-stranded PENDING ``process_report``
        rows whose target instance is TERMINATED or missing.
      * T8 (a) enqueue seam dead-letter fallback (no production
        callers yet — the seam is the primary skip path).

    Args:
        work_id: The Task ``work_id`` to dead-letter (must currently
            be ``status='pending'``).
        task_repo: TaskRepository for mirror reconciliation. Optional
            — when ``None`` the caller is responsible for running
            ``reconcile_turn_mirror`` themselves.
        reason: Optional free-form reason for the structured log
            line. Defaults to ``"dead_letter"``; callers may
            override (``"drift_sweep_dead_parent"``,
            ``"enqueue_seam_dead_parent"``, etc.). The Task model
            has no ``terminal_reason`` column (only JobItem does) so
            the reason is purely observability.
        **kwargs: Forwarded to ``_StatusTransition.__init__`` —
            ``instance_id`` is captured for the outbox result.
    """
    MIRROR_SET = ALL_8_MIRRORS

    def __init__(self, work_id, task_repo=None, reason: str = "dead_letter", **kwargs):
        super().__init__(work_id, task_repo, kwargs.get("instance_id"))
        self.reason = reason

    def run(self, session):
        # Single guarded UPDATE — ``status='pending'`` is the race gate
        # against ``claim_pending_task`` which transitions PENDING →
        # RUNNING atomically inside the same DB. rowcount 0 means the
        # row is no longer PENDING (already claimed, cancelled, or
        # completed) and the caller should treat this as a stale
        # invocation; the caller's structured log will note the skip.
        result = session.execute(
            text(
                "UPDATE task SET status = :new_status, "
                "completed_at = :completed_at, "
                "error = :error "
                "WHERE work_id = :work_id AND status = :old_status"
            ),
            {
                "new_status": "failed",
                "completed_at": datetime.now(timezone.utc),
                "error": self.reason,
                "work_id": self.work_id,
                "old_status": "pending",
            },
        )
        rowcount = getattr(result, "rowcount", 1)
        # F1 fix: if the caller passed a ``_SessionAdapter`` (a thin
        # wrapper around a raw SQLAlchemy ``Connection``), forward the
        # underlying connection to ``_reconcile`` so the mirror
        # reconcile joins the caller's open transaction instead of
        # opening a nested one. Bare ``Session`` objects do not expose
        # ``._conn``; in that case ``_reconcile`` falls back to the
        # default behaviour (open its own transaction).
        connection = getattr(session, "_conn", None)
        self._reconcile(connection=connection)
        return self._result(
            "pending",
            "failed",
            rowcount=rowcount,
            cross_turn_side_effects=("instance_error", "cancel_dependency_watchers"),
            wakeup_payload={
                "event": "turn_dead_lettered",
                "work_id": self.work_id,
                "reason": self.reason,
            },
        )


class RetryTurn(_Transition):
    """RETRY_TURN — supersede a parent Task with a fresh child.

    Plan reference: increment3-plan.md §5.7 (RETRY_TURN).

    The parent Task transitions to 'cancelled' (it is being superseded);
    a fresh child Task is INSERTed with the next-retry state. All 8
    mirrors are reconciled for both work_ids; the parent TASK reaches
    'cancelled'. The parent job_queue_items terminal write is NOT done
    here for a non-terminal instance — the reconciler's alive-instance
    guard (paused/running/waiting_children) defers it to the observer's
    finalize, which stamps failed_at so the row stays retryable. The
    child reaches the pending-armed state.

    Caller owns the conditional UPDATE-with-guard (the race gate that
    prevents duplicate retry children); this transition executes the
    happy-path:

      1. Defensive parent UPDATE — ``status='cancelled'`` (idempotent
         when the wrapper's gate UPDATE already set it). When
         ``parent_error`` is provided, the parent's ``error`` column
         is also set (for force_cancel path).
      2. INSERT child Task with all configured columns (task_type,
         message_id, retry_count, next_retry_at, is_deferred,
         is_background, created_at, etc., plus the wrapper-supplied
         work_id, status, instance_id).
      3. Reconcile mirrors for both work_ids via the Increment-1
         reconciler (``reconcile_turn_mirror``).
      4. Migrate ``job_watchers`` from parent to child work_id (F6
         fix; the parent row is only cancelled, not deleted, so
         reusing the parent's work_id would violate the UNIQUE
         constraint on ``task.work_id`` and watchers notified via
         ``get_watchers_for_job`` would silently miss the retry).

    Args:
        parent_work_id: The parent's work_id (already cancelled by
            the wrapper's gate UPDATE; this transition's UPDATE is
            the contractually-declared parent cancel).
        child_work_id: The child's fresh work_id (UUID4 generated
            by the wrapper to keep the parent's UNIQUE constraint).
        task_repo: TaskRepository for mirror reconciliation; the
            ``reconcile_turn_mirror`` method is invoked for both
            work_ids.
        child_kwargs: Column values for the child INSERT. Must
            include all NOT NULL columns except ``work_id``,
            ``status``, and ``instance_id`` (which are method-level
            defaults). The wrapper supplies: task_type, message_id,
            retry_count, next_retry_at, created_at, cancel_requested,
            retry_scheduled, is_deferred, is_background.
        parent_error: Optional error message to record on the
            parent (for force_cancel_and_schedule_retry path).
            When ``None``, the parent's ``error`` column is left
            untouched; the gate UPDATE in the wrapper is the
            authoritative writer of the parent's error column.
    """
    MIRROR_SET = ALL_8_MIRRORS

    def __init__(self, parent_work_id, child_work_id, task_repo=None,
                 child_kwargs=None, parent_error=None, **kwargs):
        self.parent_work_id = parent_work_id
        self.child_work_id = child_work_id
        self.task_repo = task_repo
        self.instance_id = kwargs.get("instance_id")
        self.child_kwargs = child_kwargs or {}
        self.parent_error = parent_error

    def run(self, session):
        # 1. Defensive parent UPDATE. The wrapper's gate UPDATE has
        #    already set status='cancelled' (and retry_scheduled=true,
        #    cancel_requested=true, cancel_requested_at=now,
        #    completed_at=now, and possibly error) — this UPDATE is
        #    the contractually-declared parent cancel. When the
        #    wrapper does NOT touch the error column (schedule_retry
        #    path), this UPDATE leaves it untouched (parent_error is
        #    None). When the wrapper passes a parent_error (force_cancel
        #    path), the error column is recorded on the parent.
        if self.parent_error is not None:
            status_result = session.execute(
                text(
                    "UPDATE task SET status = :cancelled, error = :error "
                    "WHERE work_id = :work_id"
                ),
                {
                    "cancelled": TaskStatus.CANCELLED.value,
                    "error": self.parent_error,
                    "work_id": self.parent_work_id,
                },
            )
        else:
            status_result = session.execute(
                text("UPDATE task SET status = :cancelled WHERE work_id = :work_id"),
                {
                    "cancelled": TaskStatus.CANCELLED.value,
                    "work_id": self.parent_work_id,
                },
            )

        # 2. INSERT child Task with all configured columns. The
        #    child_kwargs dict supplies every NOT NULL column except
        #    work_id, status, instance_id (which are method-level
        #    defaults). NULL columns (e.g. message_id, next_retry_at)
        #    pass through naturally as None.
        insert_columns = (
            ["work_id", "status", "instance_id"] + list(self.child_kwargs.keys())
        )
        insert_params = {
            "work_id": self.child_work_id,
            "status": TaskStatus.PENDING.value,
            "instance_id": self.instance_id,
            **self.child_kwargs,
        }
        col_list = ", ".join(insert_columns)
        placeholder_list = ", ".join(f":{col}" for col in insert_columns)
        session.execute(
            text(f"INSERT INTO task ({col_list}) VALUES ({placeholder_list})"),
            insert_params,
        )

        # 3. Reconcile both turns' mirrors (8 mirrors each via the
        #    Increment-1 reconciler). The parent TASK mirror reaches
        #    'cancelled'; the job_queue_items terminal write defers to
        #    the observer's finalize when the instance is non-terminal
        #    (alive-instance guard); child mirrors get armed
        #    (job_queue_items queued, message_queue ready, job_locks
        #    acquired, etc.).
        if self.task_repo and hasattr(self.task_repo, "reconcile_turn_mirror"):
            self.task_repo.reconcile_turn_mirror(self.parent_work_id, session)
            self.task_repo.reconcile_turn_mirror(self.child_work_id, session)

        # 4. F6 fix: migrate watcher rows from parent to child work_id.
        #    Atomic with the parent UPDATE + child INSERT — done inside
        #    the same transaction. Outside this transaction, a watcher
        #    migration without the child row would leave
        #    ``get_watchers_for_job`` returning zero rows on the (non-
        #    existent) child work_id, silently losing notifications.
        session.execute(
            text(
                "UPDATE job_watchers SET job_id = :child_work_id "
                "WHERE job_id = :parent_work_id"
            ),
            {
                "child_work_id": self.child_work_id,
                "parent_work_id": self.parent_work_id,
            },
        )

        return TransitionResult(
            work_id=self.child_work_id,
            instance_id=self.instance_id,
            old_status="cancelled",
            new_status="pending",
            mirrors_touched=self.MIRROR_SET,
            rowcount=getattr(status_result, "rowcount", 1),
            cross_turn_side_effects=("migrate_job_watchers",),
            wakeup_payload={
                "event": "turn_retried",
                "parent_work_id": self.parent_work_id,
                "child_work_id": self.child_work_id,
            },
        )

TRANSITIONS: tuple[type[_Transition], ...] = (BeginTurn, ClaimTurn, SuspendTurn, ResumeTurn, CompleteTurn, AbortTurn, DeadLetterTurn, RetryTurn)
