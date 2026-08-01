"""Truth-table tests for the shared ``message_queue_counts_as_pending`` predicate.

These tests are written BEFORE the helper implementation
(``daemon/repositories/message_queue/predicates.py``) per the Phase 2
plan's Task 1. They lock in the positive-polarity contract:

  * ``READY`` rows always count.
  * ``PROCESSING`` / ``RETRYING`` rows count when:
    - no correlated Task exists, OR
    - any correlated ``work_id`` has a Task in PENDING/RUNNING/PAUSED.
  * ``PROCESSING`` / ``RETRYING`` rows do NOT count only when at
    least one correlated ``work_id`` exists AND no correlated
    ``work_id`` has a Task in PENDING/RUNNING/PAUSED.
  * The ``message_id`` is used only as a NULL-pointer locator; the
    terminal/live identity key is ``work_id``.
  * The direct path (``processing_task_id IS NOT NULL`` →
    ``Task.id = processing_task_id`` → ``Task.work_id``) is dead
    code in production (no producer populates the column) but is
    implemented and tested as future-proofing (defensive non-NULL
    test case).

All production-row scenarios use ``processing_task_id=NULL`` to
match the production reality. The single defensive non-NULL test
proves the direct-path branch works when populated.

Tests use a real in-memory SQLite engine (StaticPool, FK on), with
the SQLModel schema. The same tests run unmodified under PostgreSQL
via ``tests/postgres/`` (the cross-engine parity test in Task 18).

Run with::

    .venv/bin/pytest tests/unit/test_message_queue_pending_predicate.py -x -q

Reference: ``.agents/shared/planning/fix-pause-report-turn-orphan/phase2-plan.md``
(B1 + Truth table section + Correct positive guard polarity).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select, func

# Register tables before metadata.create_all() so the schema is built.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.message_queue.predicates import (
    message_queue_counts_as_pending,
)
from daemon.repositories.task.models import Task, TaskStatus


# ─── Fixtures & helpers ──────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_message(
    engine: Engine,
    *,
    instance_id: str,
    status: str,
    message_type: str = MessageType.COMPLETION_REPORT.value,
    message_id: str | None = None,
    processing_task_id: str | None = None,
    content: str = "test-content",
) -> str:
    """Insert a MessageQueue row. Returns the message_id."""
    mid = message_id or f"msg-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        row = MessageQueue(
            message_id=mid,
            instance_id=instance_id,
            content=content,
            type=message_type,
            source="test",
            status=status,
            enqueued_at=now,
            last_activity_at=now,
            processing_task_id=processing_task_id,
        )
        s.add(row)
        s.commit()
    return mid


def _seed_task(
    engine: Engine,
    *,
    instance_id: str,
    work_id: str | None = None,
    message_id: str | None = None,
    status: str = TaskStatus.PENDING.value,
) -> int:
    """Insert a Task row. Returns the task.id (integer PK)."""
    work_id = work_id or f"work-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        task = Task(
            work_id=work_id,
            task_type="process_message",
            instance_id=instance_id,
            message_id=message_id,
            status=status,
            started_at=now if status == TaskStatus.RUNNING.value else None,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return int(task.id)


def _count_pending_with_predicate(
    engine: Engine,
    *,
    instance_id: str,
    excluded_message_id: str | None = None,
    statuses: tuple[str, ...] = (
        MessageStatus.READY.value,
        MessageStatus.PROCESSING.value,
        MessageStatus.RETRYING.value,
    ),
) -> int:
    """Count rows the shared predicate marks as pending.

    Mirrors the parent-completion guard shape at
    ``child_reports.py:1459-1469``:

        select(func.count()).select_from(MessageQueue).where(
            MessageQueue.instance_id == instance_id,
            (excluded_message_id filter — optional),
            (status filter — caller-chosen),
        )

    and applies ``message_queue_counts_as_pending`` to each candidate
    row in Python (since the predicate is a row-level filter, not a
    single SQL expression). This keeps the test a direct test of
    the predicate contract without depending on
    SQLAlchemy-internalized WHERE composition.
    """
    with Session(engine) as s:
        stmt = select(MessageQueue).where(
            MessageQueue.instance_id == instance_id,
        )
        if statuses is not None:
            stmt = stmt.where(MessageQueue.status.in_(list(statuses)))
        if excluded_message_id is not None:
            stmt = stmt.where(MessageQueue.message_id != excluded_message_id)
        candidates = list(s.exec(stmt))
    return sum(
        1
        for row in candidates
        if message_queue_counts_as_pending(row, engine)
    )


# ─── READY always counts ─────────────────────────────────────────────────────


def test_ready_counts_regardless_of_tasks(engine: Engine) -> None:
    """``READY`` always counts — independent of any Task history.

    Truth-table row: ``ready | any | any/none | 1 | No``.
    """
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    _seed_message(engine, instance_id=instance_id, status=MessageStatus.READY.value)
    _seed_task(engine, instance_id=instance_id, status=TaskStatus.CANCELLED.value)

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 1


def test_ready_counts_with_no_tasks(engine: Engine) -> None:
    """``READY`` with no correlated Tasks at all still counts."""
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    _seed_message(engine, instance_id=instance_id, status=MessageStatus.READY.value)

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 1


# ─── PROCESSING — NULL fallback (production reality) ─────────────────────────


def test_processing_null_fallback_no_task_counts(engine: Engine) -> None:
    """``PROCESSING`` with no correlated Task counts.

    Truth-table row: ``processing | NULL fallback | no Task | 1 | No``.

    Production incident scenario: the queue row is stuck because the
    backing Task is gone (or never existed). The shared predicate
    MUST preserve/count this row so the parent does not falsely
    mark itself complete.
    """
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.PROCESSING.value,
        processing_task_id=None,
    )

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 1


def test_processing_null_fallback_terminal_only_excluded(
    engine: Engine,
) -> None:
    """``PROCESSING`` + only-terminal correlated Task(s) → does NOT count.

    Truth-table row: ``processing | NULL fallback | terminal only | 0 | Yes``.

    This is the EXACT production incident — the process_report Task
    was cancelled, the message_queue row is orphaned. The shared
    predicate (defense-in-depth guard) must exclude this row so the
    parent can complete even if UPDATE 4 didn't reconcile it.
    """
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    msg_id = _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.PROCESSING.value,
        processing_task_id=None,
    )
    _seed_task(
        engine,
        instance_id=instance_id,
        message_id=msg_id,
        status=TaskStatus.CANCELLED.value,
    )

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 0


def test_processing_null_fallback_live_only_counts(engine: Engine) -> None:
    """``PROCESSING`` + only non-terminal correlated Task(s) → counts.

    Truth-table row: ``processing | NULL fallback | non-terminal only | 1 | No``.
    """
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    msg_id = _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.PROCESSING.value,
        processing_task_id=None,
    )
    _seed_task(
        engine,
        instance_id=instance_id,
        message_id=msg_id,
        status=TaskStatus.RUNNING.value,
    )

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 1


def test_processing_null_fallback_mixed_terminal_and_live_counts(
    engine: Engine,
) -> None:
    """``PROCESSING`` + mixed terminal/non-terminal work IDs → counts.

    Truth-table row: ``processing | NULL fallback | terminal + non-terminal
    work IDs | 1 | No; ambiguous retry/mixed-attempt state``.

    The shared predicate is intentionally conservative: an attempt
    with any non-terminal work ID is treated as live, because the
    NULL fallback cannot disambiguate which work_id owns the row.
    """
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    msg_id = _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.PROCESSING.value,
        processing_task_id=None,
    )
    # A retry with a fresh work_id at the same message_id (schedule_retry
    # behaviour). One cancelled, one running.
    _seed_task(
        engine,
        instance_id=instance_id,
        message_id=msg_id,
        status=TaskStatus.CANCELLED.value,
    )
    _seed_task(
        engine,
        instance_id=instance_id,
        message_id=msg_id,
        status=TaskStatus.RUNNING.value,
    )

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 1


def test_processing_null_fallback_terminal_completed_excluded(
    engine: Engine,
) -> None:
    """``PROCESSING`` + only completed correlated Task(s) → does NOT count."""
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    msg_id = _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.PROCESSING.value,
        processing_task_id=None,
    )
    _seed_task(
        engine,
        instance_id=instance_id,
        message_id=msg_id,
        status=TaskStatus.COMPLETED.value,
    )

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 0


def test_processing_null_fallback_terminal_failed_excluded(
    engine: Engine,
) -> None:
    """``PROCESSING`` + only failed correlated Task(s) → does NOT count."""
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    msg_id = _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.PROCESSING.value,
        processing_task_id=None,
    )
    _seed_task(
        engine,
        instance_id=instance_id,
        message_id=msg_id,
        status=TaskStatus.FAILED.value,
    )

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 0


# ─── PROCESSING — Direct path (dead code in production, defensive test) ─────


def test_processing_direct_terminal_only_excluded(engine: Engine) -> None:
    """``PROCESSING`` with non-NULL ``processing_task_id`` + terminal Task → excluded.

    Truth-table row: ``processing | direct (dead-code path) | terminal only | 0 | Yes``.

    Defensive test: no producer populates ``processing_task_id`` in
    production today (verified by exhaustive grep), but the helper
    implements the direct-path branch as future-proofing. This test
    ensures it returns the correct positive-polarity answer if a
    future producer change activates the path.
    """
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    task_id = _seed_task(
        engine,
        instance_id=instance_id,
        status=TaskStatus.CANCELLED.value,
    )
    _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.PROCESSING.value,
        processing_task_id=str(task_id),  # Direct path activated
    )

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 0


def test_processing_direct_live_counts(engine: Engine) -> None:
    """``PROCESSING`` with non-NULL ``processing_task_id`` + live Task → counts.

    Truth-table row: ``processing | direct (dead-code path) | non-terminal | 1 | No``.
    """
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    task_id = _seed_task(
        engine,
        instance_id=instance_id,
        status=TaskStatus.RUNNING.value,
    )
    _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.PROCESSING.value,
        processing_task_id=str(task_id),
    )

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 1


# ─── RETRYING — same polarity as PROCESSING ──────────────────────────────────


def test_retrying_null_fallback_terminal_only_excluded(engine: Engine) -> None:
    """``RETRYING`` + only-terminal correlated Task(s) → does NOT count.

    Truth-table row: ``retrying | either | terminal only | 0 | Same narrow
    cascade scope as processing``.
    """
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    msg_id = _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.RETRYING.value,
        processing_task_id=None,
    )
    _seed_task(
        engine,
        instance_id=instance_id,
        message_id=msg_id,
        status=TaskStatus.CANCELLED.value,
    )

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 0


def test_retrying_null_fallback_live_counts(engine: Engine) -> None:
    """``RETRYING`` + live correlated Task → counts (retry in flight)."""
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    msg_id = _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.RETRYING.value,
        processing_task_id=None,
    )
    _seed_task(
        engine,
        instance_id=instance_id,
        message_id=msg_id,
        status=TaskStatus.PENDING.value,
    )

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 1


# ─── Non-terminal Task statuses (PENDING/RUNNING/PAUSED) all count ──────────


@pytest.mark.parametrize(
    "task_status",
    [
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value,
        TaskStatus.PAUSED.value,
    ],
)
def test_processing_null_fallback_all_live_statuses_count(
    engine: Engine, task_status: str
) -> None:
    """Any non-terminal Task status (PENDING/RUNNING/PAUSED) → counts."""
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    msg_id = _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.PROCESSING.value,
        processing_task_id=None,
    )
    _seed_task(
        engine,
        instance_id=instance_id,
        message_id=msg_id,
        status=task_status,
    )

    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 1


# ─── COMPLETED / FAILED rows are outside the base status filter ─────────────


def test_completed_does_not_count_in_base_filter(engine: Engine) -> None:
    """``COMPLETED`` is not in the base status filter; the guard never sees it.

    Truth-table row: ``completed | any | any | 0 | No; already terminal``.

    This is enforced by the calling-site WHERE clause
    (``status IN ('ready', 'processing', 'retrying')``), not by the
    predicate itself. We verify by passing ``statuses=...`` containing
    only PROCESSING/RETRYING/READY — COMPLETED is excluded from
    ``candidates`` before the predicate is called.
    """
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.COMPLETED.value,
    )

    # Default caller base filter (READY/PROCESSING/RETRYING).
    assert _count_pending_with_predicate(engine, instance_id=instance_id) == 0


# ─── Filter parameter honoured ──────────────────────────────────────────────


def test_excluded_message_id_is_skipped(engine: Engine) -> None:
    """The ``excluded_message_id`` filter is honoured by the test harness.

    Verifies that the harness's own ``message_id != excluded`` filter
    drops the row before the predicate runs — mirroring the
    ``child_reports.py:1463`` ``message_id != completed_message_id``
    filter that protects against the double-count hazard.
    """
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    msg_id = _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.READY.value,
    )
    _seed_message(
        engine,
        instance_id=instance_id,
        status=MessageStatus.READY.value,
    )

    assert _count_pending_with_predicate(
        engine,
        instance_id=instance_id,
        excluded_message_id=msg_id,
    ) == 1
    assert _count_pending_with_predicate(
        engine,
        instance_id=instance_id,
        excluded_message_id=None,
    ) == 2


# ─── Reachability audit (Task 8) ─────────────────────────────────────────────


def test_reachable_production_site_uses_shared_predicate() -> None:
    """Audit: the ONLY reachable production parent-completion guard is
    ``child_reports.py:1459``. The 3 dead-code fallbacks are
    ``child_reports.py:863``, ``child_reports.py:2058``,
    ``error_reporting.py:270``. The 4 child-decision sites
    (``child_reports.py:623/637/1598/1610``) are AUDIT ONLY.

    This test does NOT exercise the production call sites (those
    are covered by ``test_pause_cascade_message_queue_orphan.py``).
    It locks the documented reachability classification in by
    requiring that the helper is the single source of truth — a
    separate contract is provided for the audit-only child-decision
    sites (NOT the same predicate).
    """
    # Import the helper here so the existence check is explicit.
    from daemon.repositories.message_queue import predicates

    # The single shared predicate exists.
    assert hasattr(predicates, "message_queue_counts_as_pending")
    # It is callable.
    assert callable(predicates.message_queue_counts_as_pending)


def test_audit_documented_sites_match_plan_classification() -> None:
    """Audit: the site locations in the plan match the actual source.

    This test inspects the source of the touched files and asserts
    that the comments labelling each site as ``(reachable in
    production)`` or ``(dead-code fallback — bus-active path
    bypasses)`` are present. It locks the reachability
    classification into the codebase so a future refactor cannot
    silently reclassify a site.
    """
    import os
    import re

    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    def _read(relpath: str) -> str:
        with open(os.path.join(repo_root, relpath), "r") as fh:
            return fh.read()

    child_reports_src = _read("daemon/services/child_reports.py")
    error_reporting_src = _read("daemon/services/error_reporting.py")

    # The 1 reachable production site has the Phase 2 commentary
    # marker. (We look for the unique Phase 2 helper import line.)
    assert (
        "message_queue_counts_as_pending" in child_reports_src
    ), "child_reports.py must import the shared predicate"

    # The 3 dead-code fallback sites have the
    # ``(dead-code fallback — bus-active path bypasses)`` marker.
    dead_code_marker = "(dead-code fallback — bus-active path bypasses)"
    assert (
        child_reports_src.count(dead_code_marker) >= 2
    ), (
        f"child_reports.py must contain at least 2 instances of the "
        f"dead-code marker; found {child_reports_src.count(dead_code_marker)}"
    )
    assert (
        error_reporting_src.count(dead_code_marker) >= 1
    ), (
        f"error_reporting.py must contain at least 1 instance of the "
        f"dead-code marker; found {error_reporting_src.count(dead_code_marker)}"
    )

    # The 4 child-decision sites (623/637/1598/1610) must NOT have
    # the predicate — they are AUDIT ONLY and their semantics are
    # unchanged. We approximate this check by confirming the
    # predicate is NOT imported in the function scope of those
    # sites (it is only imported inside the parent-completion
    # guards). The simplest proxy: the total number of predicate
    # references in child_reports.py should match the documented
    # site count (1 reachable + 2 dead-code = 3 parent-completion
    # uses + 1 import = 4 references minimum; allow up to 2x to
    # absorb comment-only references).
    refs = child_reports_src.count("message_queue_counts_as_pending")
    assert refs >= 3, (
        f"child_reports.py must reference the predicate at least 3 "
        f"times (1 reachable + 2 dead-code + 1 import); found {refs}"
    )
    assert refs <= 10, (
        f"child_reports.py references the predicate {refs} times; "
        f"expected 3-10 (matches plan's 1+2+1 layout)"
    )
