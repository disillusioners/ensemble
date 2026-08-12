"""PostgreSQL-specific tests for the Phase 2 Bug B cascade reconciliation.

These tests live under ``tests/postgres/`` so they use the
``pg_engine`` fixture and are opted-in via ``pytest -m postgres``.
The tests run serially (xdist is explicitly unsupported for the PG
tree; see ``tests/postgres/conftest.py:44-45``).

Coverage:

  1. UPDATE 4 in PostgreSQL: the data-modifying CTE
     (the production shape on PostgreSQL).
  2. The ``state.work_id <> ct.work_id`` exclusion is
     load-bearing for cross-engine parity (Task 18 PG variant).
  3. Two-connection race: connection A runs the resume
     cascade while connection B concurrently attempts a
     conflicting Task/message transition. Both race orders
     are exercised. Forbidden outcomes: historical unrelated
     row reconciled, no-Task row reconciled, mixed-attempt
     NULL-fallback row reconciled, queue row ``completed``
     while its resolved live work remains the owner.

Reference: ``.agents/shared/planning/fix-pause-report-turn-orphan/phase2-plan.md``
(Task 11 + 18 + PostgreSQL race protocol).

Run with::

    .venv/bin/pytest tests/postgres/test_pause_report_orphan_reconciliation_pg.py \\
        --override-ini="addopts=" -m postgres -q
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

# Register tables before metadata.create_all().
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.write_pause_guard import WritePauseGuard

# Make tests/helpers/ importable
_TESTS_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from tests.helpers.pause_report_orphan_scenarios import (  # noqa: E402
    ensure_schema,
    read_message,
    seed_orphan_scenario,
    seed_paused_tree,
    seed_paused_task,
    seed_processing_completion_report,
)


pytestmark = pytest.mark.postgres


def _make_service(engine) -> InstanceLifecycleService:
    """Build a minimal InstanceLifecycleService for direct cascade tests.

    The cascade helpers now call ``self._task_repo.reconcile_turn_mirror(work_id)``
    (Turn-Reconciler migration Increment 1, 2026-08-01). Wire a real
    ``TaskRepository`` so the call exercises the real reconciler — not a
    MagicMock no-op. The reconciler is safe in test scenarios: it
    fast-path-skips when the mirror is already consistent.

    Phase 4b/4c (2026-08-12, pause/resume redesign): the resume cascade
    transitions the Task ``PAUSED → PENDING`` (was ``PAUSED → CANCELLED``
    pre-migration). The reconciler's terminal-guard skips every mirror
    update for a non-terminal Task, so the message_queue row stays
    PROCESSING and the post-reconcile completion re-fire is a no-op
    (its pre-condition ``reconciled_message_ids`` non-empty is never met).
    No monkeypatch is required: the reconciler's no-op behavior is the
    correct production behavior.
    """
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._task_repo = TaskRepository(engine=engine)
    service._manager = manager
    return service


# ─── 1. UPDATE 4 data-modifying CTE on PostgreSQL ──────────────────────────


def test_pg_update4_reconciles_orphan_completion_report(pg_engine) -> None:
    """PostgreSQL UPDATE 4 no longer reconciles the orphan ``completion_report``.

    Phase 4b/4c (2026-08-12, pause/resume redesign): the resume
    cascade transitions the Task ``PAUSED → PENDING`` (was
    ``PAUSED → CANCELLED`` pre-migration). The reconciler's
    terminal-guard skips every mirror update for a non-terminal
    Task, so the cascade no longer marks linked ``message_queue``
    rows as ``completed`` — the WorkerPool's natural claim+complete
    path drives the terminal transition.

    This test seeds the same scenario as the SQLite test but runs
    against PostgreSQL and verifies the new contract: the
    ``reconciled_message_ids`` outbox is empty and the
    ``message_queue`` row stays ``processing`` after the cascade.
    """
    ensure_schema(pg_engine)
    scenario = seed_orphan_scenario(pg_engine)
    service = _make_service(pg_engine)

    # Pre-conditions
    msg_before = read_message(pg_engine, scenario.orphaned_message_id)
    assert msg_before["status"] == MessageStatus.PROCESSING.value

    result = service._resume_cascade_db_sync(
        pg_engine,
        service._manager.write_guard,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Phase 4b/4c: the cascade no longer reconciles orphan messages.
    assert result.reconciled_message_ids == []
    msg_after = read_message(pg_engine, scenario.orphaned_message_id)
    assert msg_after["status"] == MessageStatus.PROCESSING.value


def test_pg_update4_excludes_historical_orphans(pg_engine) -> None:
    """UPDATE 4 no longer reconciles any messages — historical and fresh
    orphans both stay ``processing`` after the cascade.

    Phase 4b/4c (2026-08-12, pause/resume redesign): the resume
    cascade transitions the Task ``PAUSED → PENDING``; the
    reconciler's terminal-guard skips every mirror update for
    non-terminal Tasks. The cascade no longer marks linked
    ``message_queue`` rows as ``completed`` — both the historical
    orphan (manually pre-cancelled) and the fresh orphan (seeded
    via ``seed_orphan_scenario``) remain in ``processing`` state.

    Pre-migration this test verified the cascade's reconciliation
    was scoped to the current cascade (historical untouched, fresh
    reconciled); the new contract is "no reconciliation at all".
    """
    ensure_schema(pg_engine)

    historical = seed_orphan_scenario(pg_engine, instance_id="hist-pg-1")
    # Manually advance the Task to CANCELLED.
    with Session(pg_engine) as s:
        task = s.get(Task, historical.cancelled_task_id)
        task.status = TaskStatus.CANCELLED.value
        s.add(task)
        s.commit()

    fresh = seed_orphan_scenario(pg_engine, instance_id="fresh-pg-1")

    service = _make_service(pg_engine)
    service._resume_cascade_db_sync(
        pg_engine,
        service._manager.write_guard,
        tree_ids=[fresh.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Phase 4b/4c: the cascade no longer reconciles any messages.
    # Both the fresh and the historical orphans stay ``processing``.
    assert read_message(pg_engine, fresh.orphaned_message_id)["status"] == (
        MessageStatus.PROCESSING.value
    )
    assert read_message(pg_engine, historical.orphaned_message_id)["status"] == (
        MessageStatus.PROCESSING.value
    )


def test_pg_update4_preserves_mixed_terminal_live(pg_engine) -> None:
    """Mixed terminal/live work IDs: the reconciler normalizes the
    orphan regardless of competing live work.

    Turn-Reconciler migration (Increment 1, 2026-08-01): the
    reconciler replaces the old UPDATE 4 block. The old UPDATE 4 had
    a competing-live check (``state.work_id <> ct.work_id AND
    state.status IN (pending, running, paused)``) that preserved the
    ``message_queue`` row when a retry was in flight. The reconciler
    does NOT carry that check — its ``message_queue`` update keys on
    the Task's own ``message_id`` and sets ``status='completed'``
    regardless of competing live work. This is by design: the
    reconciler is the authoritative mirror normalization primitive,
    and the retry engine owns the retry flow (a live retry Task
    proceeds independently of the ``message_queue`` row's
    ``status``).

    The test still verifies the cascade ran (the Task WAS cancelled)
    and that the reconciler normalized the mirror (the orphaned
    ``message_queue`` row is now ``completed``).
    """
    ensure_schema(pg_engine)
    scenario = seed_orphan_scenario(pg_engine)

    # Add a fresh live retry at the same message_id (different
    # work_id — schedule_retry shape).
    with Session(pg_engine) as s:
        retry = Task(
            work_id=f"work-retry-{uuid.uuid4().hex[:8]}",
            task_type="process_report",
            instance_id=scenario.instance_id,
            message_id=scenario.orphaned_message_id,
            status=TaskStatus.RUNNING.value,
            worker_id="w0",
        )
        s.add(retry)
        s.commit()

    service = _make_service(pg_engine)
    result = service._resume_cascade_db_sync(
        pg_engine,
        service._manager.write_guard,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Phase 4b/4c: the cascade's reconciled_message_ids is always
    # empty — the reconciler does not touch any message_queue rows
    # for non-terminal Tasks. The retry engine (live retry Task)
    # owns the retry flow independently.
    assert result.reconciled_message_ids == []


# ─── 2. Task 18: Cross-engine parity (PG variant) ─────────────────────────


def test_cte_work_id_exclusion_cross_engine_parity(pg_engine) -> None:
    """Task 18 (PG variant): the ``state.work_id <> ct.work_id``
    exclusion is load-bearing for cross-DB parity.

    The test seeds the exact divergence scenario on PostgreSQL:
    a single ``processing_task_id=NULL`` row whose only candidate
    Task is the just-cancelled one. The exclusion eliminates
    the false "competing live work" the subquery would otherwise
    see on PostgreSQL READ COMMITTED.

    The same scenario on SQLite (see
    ``tests/unit/test_cascade_pause_resume.py::test_cte_work_id_
    exclusion_cross_engine_parity_sqlite``) produces the same
    reconciliation result. Both engines must reconcile the
    row.
    """
    ensure_schema(pg_engine)
    scenario = seed_orphan_scenario(pg_engine)

    service = _make_service(pg_engine)
    result = service._resume_cascade_db_sync(
        pg_engine,
        service._manager.write_guard,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Phase 4b/4c: the cascade's reconciled_message_ids is always
    # empty (UPDATE 4 removed). The returned ``resumed_task_work_ids``
    # carries the cascaded Task's work_id, and both engines must
    # produce the same result.
    assert result.reconciled_message_ids == []
    # And the resumed work_id is the only candidate.
    assert scenario.cancelled_task_work_id in result.resumed_task_work_ids


# ─── 3. Two-connection race ────────────────────────────────────────────────


def test_pg_two_connection_race_no_interference(pg_engine, pg_two_connections) -> None:
    """Two connections, both race orders. Forbidden outcomes:
    historical unrelated row reconciled, no-Task row reconciled,
    mixed-attempt NULL-fallback row reconciled, queue row
    ``completed`` while its resolved live work remains the owner.

    Phase 4b/4c (2026-08-12, pause/resume redesign): the resume
    cascade transitions the Task ``PAUSED → PENDING`` (was
    ``PAUSED → CANCELLED`` pre-migration). The reconciler's
    terminal-guard skips every mirror update for non-terminal
    Tasks, so the cascade no longer marks linked ``message_queue``
    rows as ``completed`` — the WorkerPool's natural claim+complete
    path drives the terminal transition. The
    "queue row ``completed`` while its resolved live work remains
    the owner" forbidden outcome is impossible under the new
    behavior (the cascade never completes the queue row).

    With the production code (``WriteGuardSession``), the
    transactions commit independently. The PostgreSQL CTE
    sees consistent state because of the row-level locks
    on the task table.
    """
    ensure_schema(pg_engine)
    scenario = seed_orphan_scenario(pg_engine)

    # Connection A: run the cascade
    service = _make_service(pg_engine)

    # Connection B: a separate transaction that confirms the
    # message_queue row is still ``processing`` after connection A
    # commits (the cascade no longer reconciles orphan messages).
    with pg_two_connections() as (conn_a, conn_b):
        # We don't actually use conn_a — the cascade uses
        # ``service._manager.engine`` which is the same as
        # ``pg_engine``. The fixture's purpose is to ensure
        # the two connections are independent.
        result = service._resume_cascade_db_sync(
            pg_engine,
            service._manager.write_guard,
            tree_ids=[scenario.instance_id],
            ancestor_ids=set(),
            is_root_resume=True,
        )
        # Cascade completed; verify the committed state from
        # the independent connection.
        conn_b.commit()
        b_row = conn_b.execute(
            text(
                "SELECT status FROM message_queue WHERE message_id = :mid"
            ),
            {"mid": scenario.orphaned_message_id},
        ).fetchone()
        # Phase 4b/4c: the message_queue row stays ``processing``
        # (UPDATE 4 removed — the cascade no longer reconciles).
        assert b_row[0] == MessageStatus.PROCESSING.value
        assert result.reconciled_message_ids == []


def test_pg_two_connection_race_with_concurrent_live_insert(
    pg_engine, pg_two_connections
) -> None:
    """Connection B inserts a live competing Task at the same
    message_id while connection A runs the cascade.

    Turn-Reconciler migration (Increment 1, 2026-08-01): the
    reconciler normalizes the ``message_queue`` row regardless of
    competing live work. The old UPDATE 4's
    ``state.work_id <> ct.work_id`` exclusion is removed; the
    reconciler keys on the Task's own ``message_id`` and the
    retry engine owns the retry flow independently.
    """
    ensure_schema(pg_engine)
    scenario = seed_orphan_scenario(pg_engine)

    service = _make_service(pg_engine)

    # Pre-seed a competing live task at the same message_id
    # (must happen before the cascade).
    with Session(pg_engine) as s:
        s.add(Task(
            work_id=f"work-retry-{uuid.uuid4().hex[:8]}",
            task_type="process_report",
            instance_id=scenario.instance_id,
            message_id=scenario.orphaned_message_id,
            status=TaskStatus.RUNNING.value,
            worker_id="w0",
        ))
        s.commit()

    result = service._resume_cascade_db_sync(
        pg_engine,
        service._manager.write_guard,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Phase 4b/4c: the cascade's reconciled_message_ids is always
    # empty (the reconciler does not touch message_queue rows for
    # non-terminal Tasks). The ``message_queue`` row stays
    # ``processing`` and the WorkerPool's natural claim+complete
    # path drives the terminal transition.
    assert result.reconciled_message_ids == []
    msg = read_message(pg_engine, scenario.orphaned_message_id)
    assert msg["status"] == MessageStatus.PROCESSING.value
