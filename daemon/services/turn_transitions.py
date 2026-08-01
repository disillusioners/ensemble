"""Named, transaction-owned turn lifecycle transitions.

This is the foundation surface for the turn reconciler migration.  Transitions
only perform database work and return an outbox description; callers own the
transaction and post-commit dispatch.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import local
from typing import Any, ClassVar

from sqlalchemy import text

from daemon.repositories.task.models import TaskStatus

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
    cross_turn_side_effects: tuple[str, ...]
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
    def _reconcile(self):
        if self.task_repo is not None and hasattr(self.task_repo, "reconcile_turn_mirror"):
            return self.task_repo.reconcile_turn_mirror(self.work_id)
    def _result(self, old: str | None, new: str, touched: frozenset[str] | None = None, **kw):
        # ``cross_turn_side_effects`` is a positional field on
        # ``TransitionResult`` (6th position). The concrete transitions
        # (CompleteTurn / AbortTurn / etc.) pass it as a kwarg AND we
        # need to forward it positionally — ``pop`` it from kwargs so
        # the call does not collide. Without the pop, every transition
        # ``run()`` raises ``TypeError: TransitionResult.__init__()
        # got multiple values for argument 'cross_turn_side_effects'``.
        cross_turn = kw.pop("cross_turn_side_effects", ())
        return TransitionResult(self.work_id, self.instance_id, old, new, touched or self.MIRROR_SET, cross_turn, **kw)

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
        session.execute(text("INSERT INTO task (work_id, status, instance_id) VALUES (:work_id, :status, :instance_id)"), {"work_id": self.work_id, "status": "pending", "instance_id": self.instance_id})
        if self.task_repo and hasattr(self.task_repo, "reconcile_turn_mirror"): self.task_repo.reconcile_turn_mirror(self.work_id)
        return TransitionResult(self.work_id, self.instance_id, None, "pending", self.MIRROR_SET, ("instance_running",), None, {"event":"turn_started","work_id":self.work_id}, ())

class ClaimTurn(_StatusTransition):
    """CLAIM_TURN transition — Worker picks up a pending Task.

    Phase 4b stub: this transition is defined but has zero production
    callers currently. The Task-claim path in worker_pool.py will be
    migrated to call ClaimTurn.run() in a future call-site migration
    phase. The MIRROR_SET and run() body are complete and tested.
    """
    MIRROR_SET = frozenset({"task", "job_queue_items", "job_locks"})
    def __init__(self, work_id, worker_id, task_repo=None, **kwargs): super().__init__(work_id, task_repo, kwargs.get("instance_id")); self.worker_id=worker_id
    def run(self, session): self._write(session,"running","pending"); self._reconcile(); return self._result("pending","running", wakeup_payload={"event":"turn_claimed","work_id":self.work_id})

class SuspendTurn(_StatusTransition):
    MIRROR_SET = frozenset({"task", "instances"})
    def __init__(self, work_id, reason, task_repo=None, **kwargs): super().__init__(work_id,task_repo,kwargs.get("instance_id")); self.reason=reason
    def run(self, session): self._write(session,"paused","running"); return self._result("running","paused", cross_turn_side_effects=("instance_paused",), wakeup_payload={"event":"graph_task_cancel","work_id":self.work_id})

class ResumeTurn(_StatusTransition):
    MIRROR_SET = ALL_8_MIRRORS
    def __init__(self, work_id, task_repo=None, new_work_id=None, **kwargs): super().__init__(work_id,task_repo,kwargs.get("instance_id")); self.new_work_id=new_work_id
    def run(self, session): self._write(session,"cancelled","paused"); self._reconcile(); return self._result("paused","cancelled", cross_turn_side_effects=("instance_running","schedule_resume_job"), wakeup_payload={"event":"schedule_resume_job","work_id":self.new_work_id or self.work_id})

class CompleteTurn(_StatusTransition):
    MIRROR_SET = ALL_8_MIRRORS
    def __init__(self, work_id, result, task_repo=None, **kwargs): super().__init__(work_id,task_repo,kwargs.get("instance_id")); self.result=result
    def run(self, session): self._write(session,"completed","running"); self._reconcile(); return self._result("running","completed", cross_turn_side_effects=("instance_completed","cancel_dependency_watchers"), wakeup_payload={"event":"turn_completed","work_id":self.work_id})

class AbortTurn(_StatusTransition):
    MIRROR_SET = ALL_8_MIRRORS
    def __init__(self, work_id, reason, task_repo=None, error=None, **kwargs): super().__init__(work_id,task_repo,kwargs.get("instance_id")); self.reason=reason; self.error=error
    def run(self, session):
        new = "failed" if self.reason == "failed" else "cancelled"
        self._write(session,new,"running"); self._reconcile()
        return self._result("running",new,cross_turn_side_effects=(("instance_error" if new=="failed" else "instance_terminated"),"cancel_dependency_watchers"),wakeup_payload={"event":"turn_aborted","reason":self.reason,"work_id":self.work_id})

class RetryTurn(_Transition):
    """RETRY_TURN — supersede a parent Task with a fresh child.

    Plan reference: increment3-plan.md §5.7 (RETRY_TURN).

    The parent Task transitions to 'cancelled' (it is being superseded);
    a fresh child Task is INSERTed with the next-retry state. All 8
    mirrors are reconciled for both work_ids so the parent reaches
    terminal state and the child reaches the pending-armed state.

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
            session.execute(
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
            session.execute(
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
        #    Increment-1 reconciler). Parent mirrors reach terminal
        #    state; child mirrors get armed (job_queue_items queued,
        #    message_queue ready, job_locks acquired, etc.).
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
            self.child_work_id,
            self.instance_id,
            "cancelled",
            "pending",
            self.MIRROR_SET,
            ("migrate_job_watchers",),
            {
                "event": "turn_retried",
                "parent_work_id": self.parent_work_id,
                "child_work_id": self.child_work_id,
            },
            None,
            (),
        )

TRANSITIONS: tuple[type[_Transition], ...] = (BeginTurn, ClaimTurn, SuspendTurn, ResumeTurn, CompleteTurn, AbortTurn, RetryTurn)
