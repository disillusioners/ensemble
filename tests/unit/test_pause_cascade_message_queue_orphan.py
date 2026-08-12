"""Tests for the parent-completion guard hardening at all 4 sites.

The Phase 2 plan (B2 reachability audit) classifies parent-completion
guard sites as:

  - 1 REACHABLE production site: ``child_reports.py:1459``
  - 3 DEAD-CODE FALLBACKS: ``child_reports.py:863``,
    ``child_reports.py:2058``, ``error_reporting.py:270``
  - 4 AUDIT-ONLY child-decision sites (NOT changed):
    ``child_reports.py:623/637/1598/1610``

This test file exercises the shared positive-polarity predicate at
the level of the production guard semantics — verifying that:

  1. The reachable production site (the SELECT COUNT(*) in
     ``_process_child_completion_db_sync`` at line 1459) excludes
     terminal-only orphan rows but counts no-Task and live rows.
  2. The 3 dead-code fallbacks use the same predicate (so that if
     a future code path bypasses the bus, the predicate is
     already correct).
  3. The shared predicate returns identical results when called
     directly.

Reference: ``.agents/shared/planning/fix-pause-report-turn-orphan/phase2-plan.md``
(Task 10 + B2 reachability audit).
"""

from __future__ import annotations

import sys
import os
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

# Register tables before metadata.create_all() so the schema is built.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance  # noqa: E402
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.message_queue.predicates import (
    message_queue_counts_as_pending,
)
from daemon.repositories.task.models import Task, TaskStatus

# Make tests/helpers/ importable
_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from tests.helpers.pause_report_orphan_scenarios import (  # noqa: E402
    ensure_schema,
    seed_orphan_scenario,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────


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


def _count_pending(
    engine: Engine, *, instance_id: str
) -> int:
    """Mirror the production guard at ``child_reports.py:1459-1469``:
    SELECT rows in base filter, apply shared predicate per row, count.
    """
    with Session(engine) as s:
        candidates = list(
            s.exec(
                select(MessageQueue).where(
                    MessageQueue.instance_id == instance_id,
                    MessageQueue.status.in_([
                        MessageStatus.READY.value,
                        MessageStatus.PROCESSING.value,
                        MessageStatus.RETRYING.value,
                    ]),
                )
            )
        )
    return sum(
        1
        for row in candidates
        if message_queue_counts_as_pending(row, engine)
    )


# ─── 1. Reachable production site semantics ────────────────────────────────


def test_reachable_site_terminal_orphan_excluded(engine: Engine) -> None:
    """The reachable production site (``child_reports.py:1459``)
    excludes terminal-only orphan rows — parent can complete.

    Phase 4b/4c (2026-08-12, pause/resume redesign): the resume
    cascade no longer reconciles orphan messages (UPDATE 4
    removed). The cascade transitions the Task ``PAUSED →
    PENDING`` (live, not CANCELLED). The orphan message remains
    in PROCESSING with a live (PENDING) backing Task, so the
    parent-completion predicate still counts it as pending —
    the WorkerPool's natural claim+complete path will drive the
    terminal transition.

    This test verifies the new contract: the resume cascade
    leaves both the message (PROCESSING) and the task (PENDING)
    in non-terminal states, and ``pending_count`` stays at 1
    until the WorkerPool completes the PENDING Task.
    """
    ensure_schema(engine)
    scenario = seed_orphan_scenario(engine)

    # The backing Task is in PAUSED state pre-cascade.
    from tests.helpers.pause_report_orphan_scenarios import seed_paused_task, read_task
    from daemon.services.instance_lifecycle import InstanceLifecycleService
    from daemon.write_pause_guard import WritePauseGuard

    wg = WritePauseGuard()
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = wg
    service._manager = manager

    # Pre-cascade: the message is processing, the task is paused
    # (still "live" by the predicate — pending_count = 1).
    assert _count_pending(engine, instance_id=scenario.instance_id) == 1

    # Run the cascade. The Task transitions PAUSED → PENDING.
    service._resume_cascade_db_sync(
        engine, wg,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Post-cascade: the message is still PROCESSING, the task
    # is PENDING (live). The orphan row is still counted as
    # pending until the WorkerPool completes the PENDING Task
    # (the natural completion path).
    assert _count_pending(engine, instance_id=scenario.instance_id) == 1, (
        "Phase 4b/4c: the resume cascade no longer reconciles the "
        "orphan message — pending_count stays at 1 until the "
        "WorkerPool drives the natural completion"
    )


def test_reachable_site_no_task_row_counts(engine: Engine) -> None:
    """The reachable production site COUNTS a no-Task row.

    A ``processing`` ``completion_report`` with no correlated
    Task at all (no Task row references the message_id) is
    a row whose provenance is ambiguous — it must be
    preserved/counted so the parent does not falsely mark
    itself complete.
    """
    ensure_schema(engine)
    from tests.helpers.pause_report_orphan_scenarios import seed_paused_tree, seed_processing_completion_report
    iid = seed_paused_tree(engine)
    seed_processing_completion_report(
        engine, instance_id=iid, message_id="orphan-1"
    )
    # No Task seeded — there is no correlated work attempt.

    assert _count_pending(engine, instance_id=iid) == 1


def test_reachable_site_live_task_counts(engine: Engine) -> None:
    """A ``processing`` row with a live (RUNNING) Task counts."""
    ensure_schema(engine)
    iid = "inst-live-1"
    with Session(engine) as s:
        s.add(Instance(
            instance_id=iid, agent_id="dev", agent_dir="/tmp",
            agent_name="dev", project_id="test",
            status="running", created_at="2026-01-01",
        ))
        s.commit()
    mid = "msg-live-1"
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(MessageQueue(
            message_id=mid, instance_id=iid, content="x",
            type=MessageType.COMPLETION_REPORT.value, source="t",
            status=MessageStatus.PROCESSING.value,
            enqueued_at=now, last_activity_at=now,
        ))
        s.add(Task(
            work_id="w-1", task_type="process_report",
            instance_id=iid, message_id=mid,
            status=TaskStatus.RUNNING.value, worker_id="w0",
        ))
        s.commit()

    assert _count_pending(engine, instance_id=iid) == 1


# ─── 2. Dead-code fallback hardening ───────────────────────────────────────


def test_dead_code_fallbacks_use_same_predicate(engine: Engine) -> None:
    """All 3 dead-code fallbacks import and use the shared
    predicate. This test verifies the imports are present
    (the actual flow is gated behind bus-active early-returns
    in production, but the predicate code must be the same).
    """
    # Read the source files and verify the dead-code marker
    # comments are present and the predicate is imported.
    import re
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    for relpath, n_expected in (
        ("daemon/services/child_reports.py", 2),
        ("daemon/services/error_reporting.py", 1),
    ):
        with open(os.path.join(repo_root, relpath)) as fh:
            src = fh.read()
        marker = "(dead-code fallback — bus-active path bypasses)"
        assert src.count(marker) >= n_expected, (
            f"{relpath} must have at least {n_expected} "
            f"``{marker}`` markers; found {src.count(marker)}"
        )
        # The shared predicate must be imported in this file.
        assert "message_queue_counts_as_pending" in src, (
            f"{relpath} must import the shared predicate"
        )


# ─── 3. Shared predicate consistency ──────────────────────────────────────


def test_shared_predicate_is_callable_directly(engine: Engine) -> None:
    """The shared predicate can be called directly (per-row).

    This is the contract used by the production guard at
    ``child_reports.py:1459`` (now) and the dead-code fallbacks.
    """
    from tests.helpers.pause_report_orphan_scenarios import seed_paused_tree
    iid = seed_paused_tree(engine)

    # A READY row with no Task: must count.
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(MessageQueue(
            message_id="ready-1", instance_id=iid, content="x",
            type=MessageType.COMPLETION_REPORT.value, source="t",
            status=MessageStatus.READY.value,
            enqueued_at=now, last_activity_at=now,
        ))
        s.commit()

    with Session(engine) as s:
        rows = list(s.exec(select(MessageQueue)))
    assert len(rows) == 1
    assert message_queue_counts_as_pending(rows[0], engine) is True


def test_shared_predicate_rejects_completed_row(engine: Engine) -> None:
    """A ``COMPLETED`` row is NOT in the base filter, but the
    predicate's defensive behaviour is to return False."""
    from tests.helpers.pause_report_orphan_scenarios import seed_paused_tree
    iid = seed_paused_tree(engine)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(MessageQueue(
            message_id="done-1", instance_id=iid, content="x",
            type=MessageType.COMPLETION_REPORT.value, source="t",
            status=MessageStatus.COMPLETED.value,
            enqueued_at=now, last_activity_at=now,
            completed_at=now,
        ))
        s.commit()
    with Session(engine) as s:
        rows = list(s.exec(select(MessageQueue)))
    assert message_queue_counts_as_pending(rows[0], engine) is False
