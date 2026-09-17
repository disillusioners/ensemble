"""Shared SQLAlchemy predicate helpers for ``MessageQueue`` parent-completion guards.

This module is the SINGLE source of truth for the "does a message_queue row
count as pending for the parent?" question. The Phase 2 plan (B1) requires
ONE shared positive-polarity predicate used at every parent-completion guard
so the polarity cannot drift back to the inverted ``NOT EXISTS`` design or
accidentally drop no-Task / mixed-attempt rows.

Contract (positive polarity — see phase2-plan.md §"Correct positive guard
polarity"):

    READY                                    → always counts
    PROCESSING / RETRYING                    → counts when
        (no correlated work_id exists)
        OR
        (any correlated work_id has a Task in PENDING/RUNNING/PAUSED)
    PROCESSING / RETRYING                    → does NOT count only when
        (at least one correlated work_id exists)
        AND
        (no correlated work_id has a Task in PENDING/RUNNING/PAUSED)
    COMPLETED / FAILED                       → caller-side base filter
                                                 (this helper is only
                                                 consulted for rows whose
                                                 base status is in
                                                 {READY, PROCESSING, RETRYING})

Correlation paths (see phase2-plan.md §"Work identity and the only valid
correlation paths"):

  1. Direct path (DEAD CODE in production today — future-proofing):
     ``message_queue.processing_task_id IS NOT NULL`` → ``Task.id =
     processing_task_id`` → ``Task.work_id``. No producer in
     ``daemon/`` populates ``processing_task_id`` for any message type
     today (verified by exhaustive grep). The branch is retained so a
     future producer-side change activates the path automatically.

  2. NULL fallback (PRODUCTION REALITY): ``message_id`` is used only
     as a NULL-pointer locator. Candidate Tasks are projected
     (``message_id = task.message_id``), then ``work_id`` is the
     identity key. ``message_id`` is NEVER the terminal/live
     identity key — ``schedule_retry`` reuses ``message_id`` while
     minting a fresh ``work_id``, so keying on ``message_id`` would
     deadlock the retry path.

The helper evaluates the predicate in Python (per-row), taking the row
and the engine. The test harness in
``tests/unit/test_message_queue_pending_predicate.py`` applies this
function to each candidate row from a pre-filtered SELECT, mirroring
the production guard shape at ``child_reports.py:1459-1469``. This
deliberate per-row shape keeps the helper trivially auditable and
identical between SQLite and PostgreSQL.

Reference: ``.agents/shared/planning/fix-pause-report-turn-orphan/phase2-plan.md``
(§B1 + Truth table).
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from ..task.models import Task, TaskStatus
from .models import MessageQueue, MessageStatus

logger = logging.getLogger(__name__)

# Source prefix stamped on the LCA Stage-0 ``child_report_check``
# advisory note rows minted by
# ``daemon/services/child_reports.py::_process_child_completion_db_sync``.
# Single source of truth for the finalizer count guard below — change
# the mint prefix and this constant TOGETHER or the guard silently
# stops matching (drift hazard class).
CHILD_REPORT_CHECK_SOURCE_PREFIX = "child_report_check:"


# Task statuses that count as "live" (any non-terminal work attempt).
# Terminal statuses (COMPLETED, FAILED, CANCELLED) are NOT live — they
# are the exclusion condition for the predicate.
_LIVE_TASK_STATUSES: tuple[str, ...] = (
    TaskStatus.PENDING.value,
    TaskStatus.RUNNING.value,
    TaskStatus.PAUSED.value,
)


def _row_work_ids_with_status(
    engine: Engine,
    *,
    work_ids: Iterable[str],
    statuses: tuple[str, ...],
) -> set[str]:
    """Return the subset of ``work_ids`` whose Task rows are in ``statuses``.

    Helper used by both the direct and the NULL-fallback paths to
    project candidate ``work_id`` values into "is this attempt live?"
    membership sets. Identity-keyed by ``work_id``; the integer
    ``Task.id`` and the ``message_id`` locator are NOT used for the
    terminal/live decision.
    """
    work_ids_list = list(work_ids)
    if not work_ids_list:
        return set()
    with Session(engine) as s:
        rows = s.exec(
            select(Task.work_id).where(
                Task.work_id.in_(work_ids_list),
                Task.status.in_(list(statuses)),
            )
        ).all()
    return {row for row in rows}


def _null_fallback_live_work_ids(
    engine: Engine,
    *,
    message_id: str,
) -> set[str]:
    """NULL-fallback path: project candidate ``work_id`` values via
    ``message_id`` and return the set currently in a live status.

    Production reality: ``message_queue.processing_task_id`` is
    always ``NULL`` for ``completion_report`` rows (no producer
    populates it), so the parent-completion guard must discover
    correlated Tasks through ``message_id`` alone. Because
    ``schedule_retry`` reuses ``message_id`` while minting a fresh
    ``work_id``, a queue row can be associated with several work
    attempts over its lifetime. The shared predicate treats any
    such attempt as live if its ``work_id`` has a Task in
    PENDING/RUNNING/PAUSED — this is the conservative "ambiguous
    retry" branch in the plan's truth table.
    """
    with Session(engine) as s:
        candidate_work_ids = list(
            s.exec(
                select(Task.work_id).where(Task.message_id == message_id)
            ).all()
        )
    if not candidate_work_ids:
        return set()
    return _row_work_ids_with_status(
        engine,
        work_ids=candidate_work_ids,
        statuses=_LIVE_TASK_STATUSES,
    )


def _direct_path_live_work_ids(
    engine: Engine,
    *,
    processing_task_id: str,
) -> set[str]:
    """Direct path: resolve ``processing_task_id`` → ``Task.id`` →
    ``Task.work_id`` and return the set of live attempts.

    DEAD CODE in production today (no producer populates
    ``processing_task_id`` for any message type — verified by
    exhaustive grep). Retained as future-proofing so the helper
    returns the correct positive-polarity answer if a future
    producer-side change activates the path.

    The ``processing_task_id`` column is stored as text on
    PostgreSQL and as text on SQLite (declared ``str | None``
    in the model). ``Task.id`` is integer. The dialect-safe
    cast is delegated to SQLAlchemy's ``cast`` so both engines
    compare correctly. The candidate row is the one Task whose
    ``id`` matches the pointer — there is no fan-out because
    each Task has exactly one ``work_id``; the set is therefore
    either empty or a single ``work_id``.
    """
    from sqlalchemy import cast, Integer

    with Session(engine) as s:
        # Locate the Task row by id. ``cast(Integer, ...)`` makes the
        # text-vs-integer comparison work on both engines.
        task_work_id = s.exec(
            select(Task.work_id).where(
                cast(Task.id, Integer) == cast(processing_task_id, Integer)
            )
        ).first()
    if task_work_id is None:
        # Pointer references a non-existent Task — treat as no live
        # attempt (excluded by the predicate, since the caller is
        # asking "is correlated work live?" and the answer is no).
        return set()
    live = _row_work_ids_with_status(
        engine,
        work_ids=[task_work_id],
        statuses=_LIVE_TASK_STATUSES,
    )
    return live


def message_queue_counts_as_pending(
    row: MessageQueue,
    engine: Engine,
) -> bool:
    """Return ``True`` iff the given ``MessageQueue`` row counts as
    pending for the parent-completion guard.

    The caller is responsible for the base status filter
    (``status IN ('ready', 'processing', 'retrying')``); this helper
    takes rows that already passed the base filter and answers
    "should this row keep the parent from completing?".

    Args:
        row: A ``MessageQueue`` instance (already loaded by the
            caller's base-filter SELECT). The function reads
            ``row.status``, ``row.message_id``, and
            ``row.processing_task_id`` only — it does not modify
            the row.
        engine: The SQLAlchemy ``Engine`` used to query correlated
            Task rows. Required because the helper needs to project
            candidate ``work_id`` values, and that projection is
            the only correct way to disambiguate the queue-to-work
            correlation (see phase2-plan.md §Context).

    Returns:
        ``True`` when the row counts as pending (parent should NOT
        complete yet); ``False`` when the row is safe to ignore
        (parent may complete).
    """
    status = row.status

    # READY: always counts. The "ready to process" message is
    # legitimate own-queue work — a fresh enqueue from HTTP,
    # scheduler, or a fresh spawn. There is no Task yet; the row
    # is not an orphan.
    if status == MessageStatus.READY.value:
        return True

    # COMPLETED / FAILED are outside the base status filter the
    # caller is expected to apply. We accept them here as a no-op
    # return-False (defensive) so the helper is total if the
    # caller mis-applies the base filter.
    if status in (
        MessageStatus.COMPLETED.value,
        MessageStatus.FAILED.value,
    ):
        return False

    # PROCESSING / RETRYING — apply the positive-polarity guard.
    # 1. Direct path: ``processing_task_id`` is set (DEAD CODE in
    #    production today, future-proofing for Open Q6 in the plan).
    if row.processing_task_id is not None:
        live_work_ids = _direct_path_live_work_ids(
            engine,
            processing_task_id=row.processing_task_id,
        )
    else:
        # 2. NULL fallback (PRODUCTION REALITY): use ``message_id``
        #    as a candidate locator, project candidate work_ids, and
        #    decide by their status. ``message_id`` is NEVER the
        #    terminal/live identity key.
        live_work_ids = _null_fallback_live_work_ids(
            engine,
            message_id=row.message_id,
        )

    # Positive polarity: counts if ANY correlated work_id is live.
    # Excluded only if there is at least one correlated work_id and
    # NONE of them are live (i.e. all attempts are terminal).
    if not live_work_ids:
        # Either no correlated Task exists (preserve/count) OR
        # all correlated attempts are terminal (exclude).
        # Distinguishing these two cases requires checking whether
        # any correlated work_id exists at all — that is, whether
        # the NULL fallback discovered at least one Task.
        if row.processing_task_id is not None:
            # Direct path — re-query to check existence vs.
            # all-terminal. Cheaper: we already have the candidate
            # work_id from the direct-path lookup; if it was None,
            # no Task exists (preserve); if it was set and
            # ``live_work_ids`` is empty, the Task is terminal
            # (exclude).
            from sqlalchemy import cast, Integer
            with Session(engine) as s:
                exists = s.exec(
                    select(Task.work_id).where(
                        cast(Task.id, Integer)
                        == cast(row.processing_task_id, Integer)
                    )
                ).first()
            return exists is None
        else:
            # NULL fallback: re-query for ANY correlated work_id.
            with Session(engine) as s:
                any_work_id = s.exec(
                    select(Task.work_id).where(
                        Task.message_id == row.message_id
                    )
                ).first()
            return any_work_id is None

    # Some correlated work_id is live — counts.
    return True


def _row_has_any_task(engine: Engine, *, message_id: str) -> bool:
    """Return ``True`` iff ANY ``Task`` row correlates to the given
    ``message_id`` via ``Task.message_id`` (existence check — ANY
    status, live or terminal).

    This is the "does this queue row have a delivery carrier?"
    probe. A MessageQueue row with a Task in ANY status went through
    (or is going through) the durable enqueue-shaped path; a row
    with NO Task at all was minted task-less and can never be
    delivered by the task-driven pipeline
    (``message_processing_pipeline.py`` claims by Task).
    """
    with Session(engine) as s:
        found = s.exec(
            select(Task.id).where(Task.message_id == message_id)
        ).first()
    return found is not None


def finalizer_counts_as_pending(
    row: MessageQueue,
    engine: Engine,
) -> bool:
    """Root-finalizer hardened variant of
    :func:`message_queue_counts_as_pending` (defense-in-depth for the
    Stage-0 ``child_report_check`` stranding bug, 2026-09-17).

    Contract = the base predicate PLUS two advisory-row refinements:

      1. A task-less advisory note row (``source`` prefix
         ``child_report_check:`` with NO correlated Task) NEVER
         counts. The note was minted without a delivery carrier; the
         task-driven pipeline can never deliver it, so it must never
         defer a parent-completion flip (the permanent
         WAITING_CHILDREN re-park wedge class). An advisory row WITH
         a correlated Task falls through to the base predicate and
         counts normally.
      2. Any OTHER task-less READY row (unknown source, no
         correlated Task) STILL counts conservatively — semantics
         unchanged for unknown producers — but logs a WARNING: a
         task-less READY row is a different bug class (delivery can
         never happen) and needs operator visibility.

    Rows with a correlated Task (any status) behave EXACTLY like the
    base predicate.

    Scope: called from the root turn-end finalizer
    (``child_reports.py`` root carve-out pending-count) only. The
    dead-code fallback parent-completion guards still use the base
    predicate — porting there is deliberately NOT done to keep this
    completion-path behavior change minimal.
    """
    source = str(row.source or "")
    is_advisory_note = source.startswith(CHILD_REPORT_CHECK_SOURCE_PREFIX)

    # Task-existence probe: required for the advisory exclusion and
    # for the task-less-READY WARNING. One small indexed SELECT per
    # READY/advisory candidate row — the root finalizer is a rare
    # (turn-end) event, so the cost is proportional.
    row_has_task: bool | None = None
    if is_advisory_note or row.status == MessageStatus.READY.value:
        row_has_task = _row_has_any_task(engine, message_id=row.message_id)

    # Refinement 1 — task-less advisory note: undeliverable, never
    # blocks a terminal flip.
    if is_advisory_note and not row_has_task:
        return False

    counts = message_queue_counts_as_pending(row, engine)

    # Refinement 2 — unknown task-less READY row: different bug
    # class. Keep the conservative count (do NOT silently change
    # semantics for unknown sources) but make it visible.
    if (
        counts
        and row.status == MessageStatus.READY.value
        and not row_has_task
    ):
        logger.warning(
            "message_queue finalizer: task-less READY row counts as "
            "pending conservatively (unknown task-less source — "
            "delivery can never happen; different bug class, needs "
            f"visibility): message_id={row.message_id} "
            f"source={source!r} instance_id={row.instance_id}"
        )

    return counts
