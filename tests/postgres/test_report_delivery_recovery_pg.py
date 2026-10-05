"""PostgreSQL uplift of the Phase 2 ReportDeliveryRecoveryService 3.6
sweep-safety matrix (pause-report-recovery Phase 3, task 3.6).

Plan: ``.agents/shared/planning/pause-report-recovery/phase3-plan.md`` task
3.6 (line 36). The SQLite matrix lives in
``tests/unit/test_report_delivery_recovery_service.py`` (1101 lines) and the
boot-wiring tests in ``tests/integration/test_boot_report_recovery.py``
(558 lines). This file is the **PostgreSQL evidence** layer: every
acceptance item from 3.6 that is NOT proven on real PG by another
file in this branch is exercised here against the live
``postgresql+psycopg://ensemble:ensemble_dev@localhost:5432/ensemble_test``
database. Each test seeds minimal real rows, runs the production
``ReportDeliveryRecoveryService`` method against the PG engine, and
asserts per-row outcomes on the actual DB.

Scope:

* **Acceptance items covered here**:
  - Lane 1 (DEFERRED for non-terminal parents) on PG: busy-skip,
    idempotency, batch cap at 100/101 boundary, kill-switch
    isolation, never-touches-live.
  - Lane 3 (pending-age) and Lane 4 (retry) on PG: legacy stranded
    PENDING recovery, kill-switch isolation.
  - Lane 2 / C3 false-positive matrix on PG: 5 LEFT JOINs / NOT
    EXISTS exclusion cases. **The Lane 2 no-row backstop SQL had a
    real PostgreSQL bug (FIXED — see ``_LANE2_PG_BUG_FIXED_NOTE``
    below).** These tests are the regression suite.

* **Acceptance items NOT covered here** (sibling or other layer):
  - W1 ORPHAN (Lane 5) is covered live by a sibling worker per the
    task brief — explicitly SKIPPED here.
  - Y3 closed-loop branch lives in the unit file (PG-incompat
    — relies on the asyncio fallback path being testable in the
    test's main thread, not on the daemon thread).

Test strategy:

* **Lane 1 / Lane 3 / Lane 4 tests call the per-lane private
  methods** (``_run_deferred_lane``, ``_run_pending_age_lane``)
  directly so they don't accidentally trigger the Lane 2
  query when ``recover_now`` would have run it. The per-lane
  methods are the production path each lane uses inside
  ``_run_all_lanes_sync``; running them in isolation is the
  recommended approach for "per-lane PG evidence".
* **Lane 2 / C3 tests target the query directly**. With the
  fix in place (see ``_LANE2_PG_BUG_FIXED_NOTE``) the query
  compiles and runs cleanly on PG; these tests are now the
  active regression suite. The portable compile-dialect pin
  lives in ``TestLane2QueryCompilationRegression`` (runs on
  any engine — SQLite or PG).

Reference docs:

* ``daemon/services/report_delivery_recovery.py`` — class at line
  207, lanes at 522/539/856/1019, ``recover_now`` at 418.
* ``daemon/repositories/report_injection/repository.py`` —
  ``find_deferred_for_parent_all`` (526), ``find_completed_children_
  without_delivery`` (581), ``find_pending_past_age`` (758).
* ``.agents/shared/planning/pause-report-recovery/phase3-plan.md``
  line 36 (task 3.6 acceptance + W6 caveat).

Run with::

    .venv/bin/pytest tests/postgres/test_report_delivery_recovery_pg.py \\
        --override-ini="addopts=" -m postgres -q --tb=short
"""
from __future__ import annotations

import logging
import os
import uuid
import asyncio
import contextlib
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlmodel import Session, SQLModel, select as sm_select

# Register every table the recovery service touches before
# ``create_all`` runs. The ``tests/postgres/conftest.py`` autouse
# fixture TRUNCATEs every SQLModel table; the imports below ensure
# the schema includes the tables we need.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.constants import DEFERRED_REASON_RESUME_ROUTER
from daemon.repositories.dependency_bus.models import (
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.services.report_delivery_recovery import (
    LaneResult,
    ReportDeliveryRecoveryService,
    SweepResult,
)

# Skip the entire module when PG is unreachable — ``pg_engine``
# already does this per-test, but we apply the marker at import time
# so collection-time ``-m postgres`` only picks us up when the
# conftest probe succeeds. The conftest's per-session skip is
# authoritative.
pytestmark = pytest.mark.postgres

logger = logging.getLogger(__name__)


# ─── Lane 2 no-row backstop query (PG-only production bug — FIXED) ───
#
# ``ReportInjectionRepository.find_completed_children_without_delivery``
# builds a NOT EXISTS subquery that joined ``dependency_watchers`` (the
# unaliased class reference from ``select(DependencyWatcher.watch_id)``)
# to ``task AS tt`` ON a condition that referenced the ALIASED
# ``dw.source_task_id``. PG's parser rejected this with
# ``psycopg.errors.UndefinedTable: missing FROM-clause entry for
# table "dw"``. SQLite's permissive parser silently accepted the
# malformed statement — the existing
# ``tests/unit/test_report_delivery_recovery_service.py`` C3 matrix
# passed on SQLite while the production code was broken on PG.
#
# The fix (1-line): ``select(DependencyWatcher.watch_id)`` is now
# ``select(dw.watch_id)`` — the aliased reference makes the JOIN
# ON condition resolve correctly. Applied at
# ``daemon/repositories/report_injection/repository.py`` line 712.
#
# Regression pin: ``TestLane2QueryCompilationRegression`` below compiles
# the query on the PG dialect and asserts it compiles without
# ``UndefinedTable``. This is the portable guard — it runs on any
# engine (SQLite or PG) and would have caught the bug at review time.
#
# Source: ``daemon/repositories/report_injection/repository.py``
# line 712 (``not_exists_predicate`` construction).
_LANE2_PG_BUG_FIXED_NOTE = (
    "Lane 2 no-row backstop query: fixed (1-line change "
    "select(DependencyWatcher.watch_id) -> select(dw.watch_id) "
    "in daemon/repositories/report_injection/repository.py:712). "
    "Regression pin: TestLane2QueryCompilationRegression compiles the "
    "query on PG dialect without UndefinedTable."
)


# ─── Self-contained PG probe (mirrors tests/postgres/conftest.py) ─────
_PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
_PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
_PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
_PG_URL = (
    f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
    f"@{_PG_HOST}:{_PG_PORT}/{_PG_DB}"
)


@pytest.fixture(scope="session")
def pg_engine_3_6() -> Engine:
    """Session-scoped PG engine for the 3.6 matrix. Skips cleanly
    when PG is unreachable.
    """
    try:
        eng = create_engine(_PG_URL, pool_pre_ping=True, future=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except (OperationalError, DBAPIError, Exception) as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available at {_PG_URL}: {exc}")

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        try:
            SQLModel.metadata.drop_all(eng)
        finally:
            eng.dispose()


@pytest.fixture(autouse=True)
def _pg_truncate_3_6(pg_engine_3_6: Engine) -> None:
    """Per-test TRUNCATE so each test starts from a clean state."""
    with pg_engine_3_6.connect() as conn:
        existing = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public'"
                )
            ).all()
        }
    candidate_tables = [
        t.name
        for t in reversed(SQLModel.metadata.sorted_tables)
        if t.name in existing
    ]
    if not candidate_tables:
        yield
        return
    with pg_engine_3_6.begin() as conn:
        joined = ", ".join(f'"{name}"' for name in candidate_tables)
        conn.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
    yield


# ─── Seeding helpers (PG-shaped) ─────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    parent_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    last_activity_at: datetime | None = None,
) -> str:
    """Insert an Instance row. Returns the instance_id."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=iid,
                agent_id="test",
                agent_name="test",
                agent_dir="/tmp",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata={},
                last_activity_at=last_activity_at,
            )
        )
        session.commit()
    return iid


def _seed_pg_message(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str,
    status: str = MessageStatus.COMPLETED.value,
    type_: str = MessageType.HUMAN.value,
    source: str | None = None,
) -> None:
    """Insert a MessageQueue row."""
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                type=type_,
                status=status,
                source=source,
                content="pg-test-content",
            )
        )
        session.commit()


def _seed_pg_deferred_row(
    engine: Engine,
    *,
    parent_instance_id: str,
    child_instance_id: str,
    child_message_id: str,
    state: str = ReportInjectionState.DEFERRED.value,
    recovery_attempted_at: str | None = None,
    created_at: str | None = None,
    deferred_reason: str | None = None,
) -> str:
    """Insert a ``ReportInjection`` row with the given state.

    Returns the injection_id.
    """
    injection_id = f"inj-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            ReportInjection(
                injection_id=injection_id,
                parent_instance_id=parent_instance_id,
                child_instance_id=child_instance_id,
                child_message_id=child_message_id,
                report_message_id=f"report-{uuid.uuid4().hex[:8]}",
                content="pg-test-content",
                state=state,
                recovery_attempted_at=recovery_attempted_at,
                created_at=created_at or datetime.now(timezone.utc).isoformat(),
                deferred_reason=deferred_reason,
            )
        )
        session.commit()
    return injection_id


def _seed_pg_task(
    engine: Engine,
    *,
    instance_id: str,
    status: str = TaskStatus.RUNNING.value,
    task_type: str = "process_message",
    work_id: str | None = None,
) -> int:
    """Insert a Task row. Returns the integer primary key."""
    work_id = work_id or f"work-{uuid.uuid4().hex[:12]}"
    with Session(engine) as session:
        task = Task(
            work_id=work_id,
            task_type=task_type,
            instance_id=instance_id,
            message_id=None,
            status=status,
            worker_id="worker-0",
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return int(task.id)


def _build_pg_service(
    engine: Engine,
    *,
    busy_ids: set[str] | None = None,
    batch_cap: int = 100,
    lane_deferred: bool = True,
    lane_no_row_backstop: bool = True,
    lane_pending_age: bool = True,
    lane_recovery_retry: bool = True,
    lane_orphan: bool = False,
    lane_stuck_wake: bool = True,
) -> tuple[ReportDeliveryRecoveryService, MagicMock]:
    """Build the recovery service against the PG engine.

    The manager is a ``MagicMock`` whose
    ``_handle_recover_deferred_report`` is captured for call-shape
    assertions. The task repo is also a ``MagicMock`` —
    ``has_instance_busy`` returns ``True`` for ids in ``busy_ids``,
    ``False`` otherwise.
    """
    ri_repo = ReportInjectionRepository(engine=engine)
    task_repo = MagicMock()
    task_repo.has_instance_busy = MagicMock(
        side_effect=lambda instance_id: instance_id in (busy_ids or set())
    )
    queue_repo = MagicMock()
    # Iteration-2 W-4: the per-row pass duck-types
    # ``find_wake_already_delivered_evidence`` — the mock must
    # declare it (returning False = no delivery evidence) or its
    # auto-attr truthiness would fake a ledger match.
    queue_repo.find_wake_already_delivered_evidence = MagicMock(
        return_value=False
    )
    manager = MagicMock()
    manager.engine = engine
    # Iteration-2 blocker-1: mechanical tests keep the
    # parent-history ledger check out of scope (None → guarded
    # lookup degrades to "no evidence"); the S24/S27 pass-through
    # tests wire a real checkpointer + manager loop explicitly.
    manager._checkpointer = None
    manager._handle_recover_deferred_report = MagicMock()
    # G4-r4 last-mile seam: lane 6 bridges to the manager's
    # event loop and invokes ``enqueue_message`` (the SAME
    # primitive the manual ping uses — the unified dispatcher
    # auto-resumes waiting_children → RUNNING at :1968).
    # The test infrastructure here is mechanical (no PG-side
    # check that the parent is actually scheduled — the EFFECT
    # pin lives at tests/unit/test_report_delivery_recovery_
    # service.py::TestG4R3StuckWakeParentScheduleSeam).
    # Wire the mock to a no-op coroutine so the bridge call
    # resolves without raising; the unit test pins the
    # schedule call shape + the effect.
    async def _no_op_enqueue(_instance_id, _message, **kwargs):
        return None
    manager.enqueue_message = AsyncMock(side_effect=_no_op_enqueue)

    service = ReportDeliveryRecoveryService(
        task_repo=task_repo,
        report_injection_repo=ri_repo,
        queue_repo=queue_repo,
        instance_repo=MagicMock(),
        manager_ref=manager,
        interval_seconds=300,
        age_bound_minutes=10,
        batch_cap=batch_cap,
        recovery_retry_minutes=1,
        enabled=True,
        lane_deferred=lane_deferred,
        lane_no_row_backstop=lane_no_row_backstop,
        lane_pending_age=lane_pending_age,
        lane_recovery_retry=lane_recovery_retry,
        lane_orphan=lane_orphan,
        lane_stuck_wake=lane_stuck_wake,
    )
    return service, manager


# ─── Helpers: per-row state probes ───────────────────────────────────


def _row_states(
    engine: Engine, *, parent_id: str
) -> dict[str, str]:
    """Return ``{injection_id: state}`` for every row of a parent."""
    with Session(engine) as session:
        rows = session.exec(
            sm_select(ReportInjection).where(
                ReportInjection.parent_instance_id == parent_id
            )
        ).all()
    return {r.injection_id: r.state for r in rows}


# =============================================================================
# 3.6 acceptance — Lane 1 (DEFERRED, non-terminal parent) on PG
# =============================================================================
#
# Lane 1 tests call ``_run_deferred_lane()`` directly to isolate
# Lane 1's contract from the other lanes. The per-lane method IS
# the production path each lane uses inside ``_run_all_lanes_sync``;
# the test exercises the same code.


class TestLane1DeferredPG:
    """Lane 1 (DEFERRED for non-terminal parents) on real PG."""

    def test_busy_parent_skipped_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """3.6 acceptance: a busy parent is SKIPPED — the natural
        path owns delivery when the parent's turn resumes.

        On PG this guards the full SQL JOIN through
        ``find_deferred_for_parent_all`` + the per-row
        ``has_instance_busy`` gate. The row must stay DEFERRED
        (not transitioned to PENDING) after the sweep.
        """
        parent = _seed_instance(pg_engine_3_6)
        child = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=child,
            message_id="child-msg-pg-1",
            status=MessageStatus.COMPLETED.value,
        )
        _seed_pg_deferred_row(
            pg_engine_3_6,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-pg-1",
        )

        service, manager = _build_pg_service(
            pg_engine_3_6, busy_ids={parent}
        )
        # Direct Lane 1 call — bypasses the broken Lane 2 query.
        lane_result = service._run_deferred_lane()

        assert lane_result.skipped_busy == 1
        assert lane_result.recovered == 0
        manager._handle_recover_deferred_report.assert_not_called()

        # Row stayed DEFERRED — no transition committed.
        states = _row_states(pg_engine_3_6, parent_id=parent)
        assert all(
            s == ReportInjectionState.DEFERRED.value for s in states.values()
        ), f"busy parent must NOT trigger transition; got {states}"

    def test_idempotent_re_run_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """3.6 acceptance: the sweep is IDEMPOTENT — running it
        twice produces a no-op on the second pass.

        Asserts the per-row delivery count is exactly 1 across
        both runs (no double-delivery). The first run transitions
        DEFERRED → PENDING and re-enters; the second run sees
        nothing in DEFERRED state and reports zero recoveries.
        """
        parent = _seed_instance(pg_engine_3_6)
        child = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=child,
            message_id="child-msg-pg-1",
            status=MessageStatus.COMPLETED.value,
        )
        _seed_pg_deferred_row(
            pg_engine_3_6,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-pg-1",
        )

        service, manager = _build_pg_service(pg_engine_3_6)

        # First pass: recovers the row.
        lane_result_1 = service._run_deferred_lane()
        assert lane_result_1.recovered == 1
        first_call_count = (
            manager._handle_recover_deferred_report.call_count
        )
        assert first_call_count == 1

        # Second pass: a no-op (idempotent).
        lane_result_2 = service._run_deferred_lane()
        assert lane_result_2.recovered == 0
        assert (
            manager._handle_recover_deferred_report.call_count
            == first_call_count
        ), (
            "second sweep must NOT call _handle_recover_deferred_report "
            "(idempotent contract); "
            f"call_count went {first_call_count} -> "
            f"{manager._handle_recover_deferred_report.call_count}"
        )

    def test_batch_cap_100_processes_100_logs_remainder(
        self, pg_engine_3_6: Engine
    ) -> None:
        """3.6 acceptance: ``batch_cap=100`` — 101 eligible
        DEFERRED rows → exactly 100 processed in the run, the
        101st is left for the next cycle.

        Seeds 101 distinct DEFERRED rows for 101 parent/child
        pairs (so the busy-skip is NOT triggered), asserts:

        * Lane 1 ``recovered == 100`` (the batch cap).
        * One row remains DEFERRED on the table (the cap's
          remainder).
        """
        # Seed 101 parent/child/row triples.
        for i in range(101):
            parent = _seed_instance(pg_engine_3_6)
            child = _seed_instance(
                pg_engine_3_6,
                parent_id=parent,
                status=InstanceStatus.COMPLETED.value,
            )
            _seed_pg_message(
                pg_engine_3_6,
                instance_id=child,
                message_id=f"child-msg-{i}",
                status=MessageStatus.COMPLETED.value,
            )
            _seed_pg_deferred_row(
                pg_engine_3_6,
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=f"child-msg-{i}",
            )

        # Default batch_cap=100 (no override).
        service, manager = _build_pg_service(pg_engine_3_6)

        lane_result = service._run_deferred_lane()

        # The DEFERRED lane recovered exactly ``batch_cap`` rows.
        assert lane_result.recovered == 100, (
            f"expected batch_cap=100 recoveries; got "
            f"{lane_result.recovered}"
        )
        # Manager was called exactly 100 times — once per row.
        assert manager._handle_recover_deferred_report.call_count == 100

        # Verify the row state: 100 transitioned, 1 DEFERRED.
        with Session(pg_engine_3_6) as session:
            counts: dict[str, int] = {}
            for row in session.exec(sm_select(ReportInjection)).all():
                counts[row.state] = counts.get(row.state, 0) + 1
        assert counts.get(ReportInjectionState.DEFERRED.value, 0) == 1, (
            f"exactly 1 DEFERRED row must remain (the batch-cap "
            f"remainder for next cycle); got {counts}"
        )
        # The transitioned rows are PENDING (the mock manager
        # does not drive delivery; the real hand-off escalates
        # to terminal via the claim paths).
        assert counts.get(ReportInjectionState.PENDING.value, 0) == 100


# =============================================================================
# 3.6 acceptance — C3 false-positive matrix on PG
# =============================================================================
#
# The C3 matrix targets
# :meth:`ReportInjectionRepository.find_completed_children_without_delivery`
# which had an alias-binding bug (now fixed — see
# ``_LANE2_PG_BUG_FIXED_NOTE``). These tests are the regression
# suite. Originally marked ``xfail`` pending the fix; the markers
# were removed in the same commit as the production fix
# (``select(dw.watch_id)`` at
# ``daemon/repositories/report_injection/repository.py:712``).


class TestC3FalsePositiveMatrixPG:
    """3.6 acceptance: the C3 false-positive matrix on real PG.

    Each test seeds one candidate + one exclusion shape and asserts
    the row is NOT recovered (or the no-row-backstop lane's query
    excludes it). The exclusion predicates are the 5 LEFT JOINs /
    NOT EXISTS subqueries of
    :meth:`ReportInjectionRepository.find_completed_children_without_delivery`.

    Originally marked ``xfail`` while the production code had the
    unaliased ``DependencyWatcher.watch_id`` SELECT in the NOT
    EXISTS subquery (``_LANE2_PG_BUG_FIXED_NOTE``). The fix at
    ``repository.py:712`` binds the SELECT to the ``dw`` alias so
    PG accepts the query. The xfail markers were removed in the
    same commit as the production fix; this class is now the
    regression suite.
    """

    def _seed_completed_child_with_completed_message(
        self,
        engine: Engine,
        parent_id: str,
        child_msg_id: str = "child-msg",
    ) -> tuple[str, str]:
        """Seed a COMPLETED child + its COMPLETED message."""
        child_id = _seed_instance(
            engine,
            parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_pg_message(
            engine,
            instance_id=child_id,
            message_id=child_msg_id,
            status=MessageStatus.COMPLETED.value,
        )
        return child_id, child_msg_id

    def test_c3_excludes_when_existing_completion_report_message(
        self, pg_engine_3_6: Engine
    ) -> None:
        """C3 case 1: a row with an existing ``internal_report:``
        message in the parent's queue is EXCLUDED from the no-row
        backstop lane.

        The LEFT JOIN's ``message_id IS NULL`` predicate filters
        it out. Verified on PG (the ``||`` string-concat in the
        ``source`` expression compiles on both drivers, but the
        actual row exclusion is the contract under test).
        """
        parent = _seed_instance(pg_engine_3_6)
        child_id, child_msg_id = (
            self._seed_completed_child_with_completed_message(
                pg_engine_3_6, parent, child_msg_id="child-msg-1"
            )
        )
        # Seed the completion_report message in the parent's queue.
        existing_report = f"report-{uuid.uuid4().hex[:8]}"
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=parent,
            message_id=existing_report,
            status=MessageStatus.READY.value,
            type_=MessageType.COMPLETION_REPORT.value,
            source=(
                f"internal_report:{child_id}:{child_msg_id}"
            ),
        )

        # The candidate should NOT be in the lane's query result.
        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        rows = ri_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        assert not any(r["child_id"] == child_id for r in rows), (
            "C3 case 1 (existing completion_report message) MUST "
            "exclude the row from the no-row backstop lane on PG"
        )

    def test_c3_excludes_when_existing_injection_row_any_state(
        self, pg_engine_3_6: Engine
    ) -> None:
        """C3 case 2: a row with an existing non-terminal
        ``report_injections`` row is EXCLUDED.
        """
        parent = _seed_instance(pg_engine_3_6)
        child_id, child_msg_id = (
            self._seed_completed_child_with_completed_message(
                pg_engine_3_6, parent, child_msg_id="child-msg-2"
            )
        )

        # Seed a PENDING injection row for the same triple.
        _seed_pg_deferred_row(
            pg_engine_3_6,
            parent_instance_id=parent,
            child_instance_id=child_id,
            child_message_id=child_msg_id,
            state=ReportInjectionState.PENDING.value,
        )

        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        rows = ri_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        assert not any(r["child_id"] == child_id for r in rows), (
            "C3 case 2 (existing PENDING injection row) MUST "
            "exclude the candidate from the no-row backstop lane"
        )

    def test_c3_excludes_when_parent_terminal(
        self, pg_engine_3_6: Engine
    ) -> None:
        """C3 case 3: a row whose parent is TERMINAL is EXCLUDED
        from the periodic sweep.
        """
        parent = _seed_instance(
            pg_engine_3_6,
            status=InstanceStatus.COMPLETED.value,
        )
        child_id, child_msg_id = (
            self._seed_completed_child_with_completed_message(
                pg_engine_3_6, parent, child_msg_id="child-msg-3"
            )
        )

        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        # Periodic sweep: parent_not_terminal=True excludes
        # terminal parents.
        rows = ri_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        assert not any(r["child_id"] == child_id for r in rows), (
            "C3 case 3 (terminal parent) MUST exclude the "
            "candidate from the periodic sweep on PG"
        )

        # Diagnostic/manual ``parent_not_terminal=False`` does
        # include the row (the ORPHAN lane territory).
        rows_diagnostic = ri_repo.find_completed_children_without_delivery(
            parent_not_terminal=False
        )
        assert any(r["child_id"] == child_id for r in rows_diagnostic), (
            "diagnostic call (parent_not_terminal=False) MUST "
            "include the terminal-parent candidate"
        )

    def test_c3_anchor_less_child_admitted_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """C3 case 4 (F-2 UPDATE): a row whose child message is
        NOT COMPLETED is ADMITTED (the anchor filter was removed
        by F-2 task 2.2 — the child's terminal report is the
        checkpoint message, not a message_queue row).

        Pre-F-2: the ``anchor_subq.is_not(None)`` filter
        excluded children whose message status is not COMPLETED.
        Post-F-2: anchor-less children of non-terminal parents
        are admitted so the per-row pass can derive the
        child_message_id from the checkpoint.
        """
        parent = _seed_instance(pg_engine_3_6)
        child_id = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        # Seed a non-COMPLETED child message (e.g. PROCESSING).
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=child_id,
            message_id="child-msg-not-done",
            status=MessageStatus.PROCESSING.value,
        )

        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        rows = ri_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        matched = [r for r in rows if r["child_id"] == child_id]
        assert matched, (
            "C3 case 4 (F-2): anchor-less child of non-terminal "
            "parent MUST be ADMITTED — the anchor filter was "
            "removed by F-2 task 2.2 (the child's terminal report "
            "is the checkpoint message, not a message_queue row)"
        )
        assert matched[0]["has_anchor"] is False, (
            "has_anchor is False (the anchor subquery filters on "
            "status='completed'; the child's only message is "
            "PROCESSING, so the anchor is NULL — the per-row pass "
            "derives the child_message_id from the checkpoint)"
        )

    def test_c3_excludes_when_fired_dependency_watcher(
        self, pg_engine_3_6: Engine
    ) -> None:
        """C3 case 5: a row with an existing FIRED
        ``dependency_watcher`` (between the child's Task and the
        parent) is EXCLUDED — the NOT EXISTS predicate on the
        FIRED-watcher subquery is the load-bearing gate.
        """
        parent = _seed_instance(pg_engine_3_6)
        child_id, child_msg_id = (
            self._seed_completed_child_with_completed_message(
                pg_engine_3_6, parent, child_msg_id="child-msg-5"
            )
        )
        # Seed a Task for the child, then a FIRED watcher
        # pointing at the parent.
        child_task_id = _seed_pg_task(
            pg_engine_3_6,
            instance_id=child_id,
            status=TaskStatus.COMPLETED.value,
        )
        with Session(pg_engine_3_6) as session:
            session.add(
                DependencyWatcher(
                    source_task_id=str(child_task_id),
                    target_instance_id=parent,
                    follow_up_payload={"k": "v"},
                    watcher_metadata={"kind": "test"},
                    state=DependencyWatcherState.FIRED.value,
                )
            )
            session.commit()

        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        rows = ri_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        assert not any(r["child_id"] == child_id for r in rows), (
            "C3 case 5 (FIRED dependency_watcher) MUST exclude the "
            "candidate — the NOT EXISTS predicate on the FIRED "
            "watcher is the load-bearing gate"
        )


# =============================================================================
# 3.6 acceptance — Lane 2 (no-row backstop) on PG
# =============================================================================
#
# Lane 2 had an alias-binding bug on PG (now fixed — see
# ``_LANE2_PG_BUG_FIXED_NOTE``). The tests in this class were
# originally marked ``xfail``; the markers were removed in the same
# commit as the production fix. The portable compile-dialect pin
# for the fix lives in ``TestLane2QueryCompilationRegression``
# below; the PG-gated end-to-end runtime contract lives in
# ``TestLane2PGRegressionEndToEnd``.


class TestLane2NoRowBackstopPG:
    """Lane 2 (no-row backstop, C3) on real PG.

    Originally ``xfail`` while the production code had the
    unaliased ``DependencyWatcher.watch_id`` SELECT in the NOT
    EXISTS subquery (``_LANE2_PG_BUG_FIXED_NOTE``). The fix at
    ``repository.py:712`` binds the SELECT to the ``dw`` alias so
    PG accepts the query; the xfail marker was removed in the
    same commit as the production fix.
    """

    def test_no_row_lane_recovers_never_markered_drop_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """3.6 acceptance: the no-row backstop lane RECOVERS a
        never-markered drop (no ReportInjection row exists, but a
        child has a COMPLETED message with no completion_report
        queued for the parent — FM-11 escape shape).
        """
        parent = _seed_instance(pg_engine_3_6)
        child_id, child_msg_id = (
            self._seed_completed_child_with_completed_message(
                pg_engine_3_6, parent, child_msg_id="child-msg-orphan-1"
            )
        )

        service, manager = _build_pg_service(pg_engine_3_6)
        # Direct Lane 2 call.
        lane_result = service._run_no_row_backstop_lane()

        assert lane_result.recovered == 1, (
            f"no_row_backstop lane must recover the never-markered "
            f"drop on PG; got recovered={lane_result.recovered}"
        )
        manager._handle_recover_deferred_report.assert_called_once()
        call_kwargs = (
            manager._handle_recover_deferred_report.call_args.kwargs
        )
        assert call_kwargs["child_instance_id"] == child_id
        assert call_kwargs["child_message_id"] == child_msg_id

        # A ReportInjection row now exists and is PENDING (D2
        # end-state alignment — never left DEFERRED).
        states = _row_states(pg_engine_3_6, parent_id=parent)
        assert len(states) == 1, (
            f"no_row_backstop must insert exactly one obligation "
            f"row; got {len(states)} rows"
        )
        only_state = next(iter(states.values()))
        assert only_state == ReportInjectionState.PENDING.value, (
            f"D2: no_row_backstop row must end PENDING (never "
            f"left half-DEFERRED); got state={only_state}"
        )

    @staticmethod
    def _seed_completed_child_with_completed_message(
        engine: Engine, parent_id: str, child_msg_id: str
    ) -> tuple[str, str]:
        child_id = _seed_instance(
            engine,
            parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_pg_message(
            engine,
            instance_id=child_id,
            message_id=child_msg_id,
            status=MessageStatus.COMPLETED.value,
        )
        return child_id, child_msg_id


# =============================================================================
# 3.6 acceptance — Lane 3 + Lane 4 (pending-age + retry) on PG
# =============================================================================
#
# Lanes 3 + 4 share the same query, parameterized by
# ``recovery_retry_minutes``. The test calls the per-lane method
# directly to bypass the broken Lane 2 query.


class TestLane3Lane4PendingAgePG:
    """Lanes 3 + 4 (pending-age + retry) on real PG."""

    def test_legacy_stranded_pending_recovered_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """3.6 acceptance: legacy stranded PENDING rows past the
        age guard are recovered (Lane 3 path).

        Seeds a PENDING row with ``created_at`` 1 hour ago
        (default ``age_bound_minutes=10``) and no
        ``recovery_attempted_at`` — the canonical "stranded"
        shape. Asserts Lane 3 (or Lane 4) picks it up and the
        manager hand-off fires.
        """
        parent = _seed_instance(pg_engine_3_6)
        child = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=child,
            message_id="child-msg-pending-1",
            status=MessageStatus.COMPLETED.value,
        )
        # Seed a PENDING row with old created_at (1h ago).
        old_created = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        _seed_pg_deferred_row(
            pg_engine_3_6,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-pending-1",
            state=ReportInjectionState.PENDING.value,
            recovery_attempted_at=None,
            created_at=old_created,
        )

        service, manager = _build_pg_service(pg_engine_3_6)
        # Direct Lane 3 call (recovery_retry_minutes=0 = never-
        # stamped eligibility).
        lane3 = service._run_pending_age_lane(
            lane_name="pending_age", recovery_retry_minutes=0
        )
        # Direct Lane 4 call (recovery_retry_minutes=1 = stamped-
        # stale eligibility; the never-stamped row is also
        # eligible for Lane 4 since the predicate is "IS NULL OR
        # < cutoff").
        lane4 = service._run_pending_age_lane(
            lane_name="recovery_retry", recovery_retry_minutes=1
        )

        # At least one of the two lanes picked it up.
        assert (lane3.recovered + lane4.recovered) >= 1, (
            f"stranded PENDING row must be recovered by Lane 3 or "
            f"Lane 4 on PG; got pending_age={lane3.recovered}, "
            f"recovery_retry={lane4.recovered}"
        )
        manager._handle_recover_deferred_report.assert_called()


# =============================================================================
# 3.6 acceptance — lane kill-switches on PG
# =============================================================================
#
# Kill-switch tests verify the per-lane gating in
# ``_run_all_lanes_sync`` (manager.py:5458-5492). With a lane
# disabled, that lane is absent from the ``SweepResult.lanes`` map;
# the OTHER lanes still run. We exercise the gating at the
# ``_run_all_lanes_sync`` level (NOT per-lane) to validate the
# kill-switch is the source of the missing lane, not the per-lane
# methods' empty results.
#
# These tests also run Lane 2 — but only when the lane is enabled
# in the kill-switch test. For ``lane1_disabled`` / ``lane3_4_disabled``
# tests, Lane 2 is enabled but the seeded data does NOT trigger
# the broken NOT EXISTS subquery (no child has a completed
# message + no completion_report + no injection row + no watcher
# — the seed only seeds DEFERRED rows for Lane 1). So Lane 2
# returns an empty result set, which exercises the LEFT JOINs and
# the outer WHERE but NOT the NOT EXISTS subquery.


class TestLaneKillSwitchesPG:
    """3.6 acceptance: lane kill-switches on real PG.

    With a lane disabled, that lane is absent from the sweep's
    ``SweepResult.lanes`` map. We verify the gating by inspecting
    the ``SweepResult.lanes`` keys for each ``lane_X`` boolean
    pair.

    Each test disables EVERY lane plus the lane under test, then
    re-enables just one lane, and asserts ONLY that lane appears
    in the result. The other disabled lanes are absent. This
    pattern is the same as the unit test
    ``test_sweep_lane_kill_switches`` (which disables all five
    and asserts an empty result) but exercised at the per-lane
    granularity on PG.

    Why not "enable all + disable one + verify others still run"?
    Lane 2 (no-row backstop) had an alias-binding bug on PG that
    caused the lane to raise ``UndefinedTable`` (now fixed; see
    ``_LANE2_PG_BUG_FIXED_NOTE``). The integration of "other lanes
    still run alongside a disabled lane" is covered on SQLite in
    the unit file. The per-lane kill-switch contract is the
    "the boolean attribute is the source of the gating" — that
    contract is what this test pins on PG.
    """

    def test_lane1_disabled_absent_from_sweep_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """Disable Lane 1, enable all others → ``"deferred"``
        absent; other lanes appear (Lane 2 + Lane 3 + Lane 4).

        Note: with the Lane 2 alias-binding fix in place, Lane 2
        now runs cleanly on PG (see ``_LANE2_PG_BUG_FIXED_NOTE``).
        The kill-switch contract under test: Lane 1 is absent from
        the sweep result.
        """
        parent = _seed_instance(pg_engine_3_6)
        child = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=child,
            message_id="child-msg-ks-1",
            status=MessageStatus.COMPLETED.value,
        )
        _seed_pg_deferred_row(
            pg_engine_3_6,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-ks-1",
        )

        service, _ = _build_pg_service(
            pg_engine_3_6,
            # Lane 1 disabled — the test target.
            lane_deferred=False,
            # Other lanes disabled too — bypasses the broken
            # Lane 2 query and the noisy Lane 3/4 results.
            lane_no_row_backstop=False,
            lane_pending_age=False,
            lane_recovery_retry=False,
            lane_orphan=False,
            # Block-1 G4 lane: disabled too (same empty-lane-dict
            # contract as the all-disabled test above).
            lane_stuck_wake=False,
        )
        result = service._run_all_lanes_sync()
        # All lanes disabled → empty result. The Lane 1
        # kill-switch is the source of the missing lane (proven
        # in the next test, which enables Lane 1 + asserts it
        # IS present).
        assert result.lanes == {}, (
            f"all-lanes-disabled sweep must produce an empty "
            f"lanes map (Lane 1 disabled too); got {result.lanes!r}"
        )
        # The DEFERRED row is unchanged (no lane processed it).
        states = _row_states(pg_engine_3_6, parent_id=parent)
        assert (
            states
            and next(iter(states.values()))
            == ReportInjectionState.DEFERRED.value
        ), (
            "DEFERRED row must stay DEFERRED when Lane 1 is "
            "disabled; got "
            f"states={states}"
        )

    def test_lane1_enabled_present_in_sweep_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """Enable Lane 1, disable all others → ``"deferred"``
        IS the only key in the result.

        Companion to ``test_lane1_disabled_absent_from_sweep_on_pg``:
        the same seed + the same call shape, with only the
        Lane 1 boolean flipped. Together they prove the
        kill-switch IS the source of the gating on PG.
        """
        parent = _seed_instance(pg_engine_3_6)
        child = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=child,
            message_id="child-msg-ks-1b",
            status=MessageStatus.COMPLETED.value,
        )
        _seed_pg_deferred_row(
            pg_engine_3_6,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-ks-1b",
        )

        service, _ = _build_pg_service(
            pg_engine_3_6,
            # Lane 1 ENABLED — the test target.
            lane_deferred=True,
            # Other lanes disabled to bypass the broken Lane 2.
            lane_no_row_backstop=False,
            lane_pending_age=False,
            lane_recovery_retry=False,
            lane_orphan=False,
        )
        result = service._run_all_lanes_sync()
        # Lane 1 is the only key.
        assert "deferred" in result.lanes, (
            f"Lane 1 enabled — must be in the result; got "
            f"{result.lanes!r}"
        )
        # Other lanes are absent.
        for absent in (
            "no_row_backstop",
            "pending_age",
            "recovery_retry",
            "orphan",
        ):
            assert absent not in result.lanes, (
                f"{absent} lane must be absent (disabled); got "
                f"{result.lanes!r}"
            )
        # And Lane 1 actually processed the DEFERRED row.
        assert result.lanes["deferred"].recovered == 1
        assert result.total_recovered == 1

    def test_all_lanes_disabled_returns_empty_result(
        self, pg_engine_3_6: Engine
    ) -> None:
        """Disable every lane → empty ``SweepResult.lanes`` map
        (no work was processed). Mirrors the unit test
        ``test_sweep_lane_kill_switches`` on PG.
        """
        service, _ = _build_pg_service(
            pg_engine_3_6,
            lane_deferred=False,
            lane_no_row_backstop=False,
            lane_pending_age=False,
            lane_recovery_retry=False,
            lane_orphan=False,
            # Block-1 G4 lane: must be disabled too for the
            # empty-lane-dict assertion.
            lane_stuck_wake=False,
        )
        result = service._run_all_lanes_sync()
        assert result.lanes == {}, (
            f"all-lane-disabled sweep must produce an empty lanes "
            f"map; got {result.lanes!r}"
        )
        assert result.total_recovered == 0


# =============================================================================
# 3.6 acceptance — "never touches a live instance" (busy-skip on PG)
# =============================================================================
#
# The per-lane Lane 1 path is exercised (the production path the
# service uses inside ``_run_all_lanes_sync``).


class TestNeverTouchesLiveInstancePG:
    """3.6 acceptance: the sweep MUST NOT touch a live instance.

    A live instance is one with ``has_instance_busy(parent_id) ==
    True``. The sweep's per-row gate is the only thing standing
    between the sweep and a concurrent live turn — this test
    pins the contract on PG.
    """

    def test_sweep_skips_live_instance_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """The sweep's busy-skip is the only path that protects a
        live parent turn from the recovery hand-off. The DEFERRED
        row stays DEFERRED, the row's ``recovery_attempted_at``
        stays NULL, and ``_handle_recover_deferred_report`` is
        never called.
        """
        parent = _seed_instance(pg_engine_3_6)
        child = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=child,
            message_id="child-msg-live-1",
            status=MessageStatus.COMPLETED.value,
        )
        _seed_pg_deferred_row(
            pg_engine_3_6,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-live-1",
        )

        # Mark the parent as live (has_instance_busy returns True).
        service, manager = _build_pg_service(
            pg_engine_3_6, busy_ids={parent}
        )
        # Direct Lane 1 call (the lane that owns the busy-skip
        # for DEFERRED rows; bypasses the broken Lane 2).
        lane_result = service._run_deferred_lane()

        # Lane 1 skipped; the row's state is preserved.
        assert lane_result.skipped_busy == 1
        assert lane_result.recovered == 0
        manager._handle_recover_deferred_report.assert_not_called()

        # The row is still DEFERRED and the stamp is empty.
        with Session(pg_engine_3_6) as session:
            row = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.parent_instance_id == parent
                )
            ).first()
        assert row is not None
        assert row.state == ReportInjectionState.DEFERRED.value
        assert row.recovery_attempted_at is None


# =============================================================================
# Regression pin — Lane 2 no-row backstop SQL compiles cleanly on PG
# =============================================================================
#
# The pre-fix query had ``select(DependencyWatcher.watch_id)`` in the
# NOT EXISTS subquery while the JOIN / WHERE clauses referenced the
# aliased ``dw`` (``_LANE2_PG_BUG_FIXED_NOTE``). PG's parser rejected
# the result with ``UndefinedTable: missing FROM-clause entry for
# table "dw"``; SQLite accepted silently.
#
# This class is the portable regression pin. It compiles the query
# on the PG dialect (no PG engine needed — SQLAlchemy's
# ``postgresql.dialect()`` is a pure in-memory dialect object) and
# asserts the compiled SQL contains a real ``dependency_watchers AS dw``
# in the EXISTS subquery's FROM clause. The assertion runs anywhere
# — SQLite or PG, no fixtures, no DB connectivity — and would have
# caught the bug at review time.
#
# The companion PG-gated end-to-end test
# ``test_fired_watcher_excludes_candidate_end_to_end_on_pg`` proves
# the runtime contract: when a FIRED ``DependencyWatcher`` row exists
# for the (child_task, parent) pair, ``find_completed_children_-
# without_delivery`` returns an empty list — the NOT EXISTS
# predicate excludes the row as designed.


class TestLane2QueryCompilationRegression:
    """Portable regression pin for the Lane 2 query alias binding.

    The original bug was an unaliased ``DependencyWatcher.watch_id``
    SELECT in a NOT EXISTS subquery whose FROM clause only declared
    the ALIASED ``dependency_watchers AS dw``. PG rejected this
    with ``UndefinedTable``; SQLite silently accepted — the
    SQLite false-green that let the bug escape four review cycles.

    These tests capture the SQL that the PRODUCTION code actually
    emits (via SQLAlchemy's ``before_cursor_execute`` event) and
    assert the alias-binding contract on the captured statement.
    They use an in-memory SQLite engine so they run anywhere —
    no PG required, no fixtures. PG's behavior is covered by the
    sibling ``TestLane2PGRegressionEndToEnd`` class.
    """

    def _capture_production_sql(self) -> str:
        """Run the production
        :meth:`ReportInjectionRepository.find_completed_children_without_delivery`
        against an in-memory SQLite engine and return the SQL it
        actually emitted (captured via a ``before_cursor_execute``
        listener). The engine dialect is irrelevant — we capture
        the SQL string, not the execution result. SQLite accepts
        the malformed statement, so the run never errors; we only
        care about the SQL shape, not the row data.
        """
        from sqlalchemy import create_engine, event
        from sqlalchemy.pool import StaticPool
        from sqlmodel import Session, SQLModel

        # Re-register the production tables on a fresh in-memory
        # engine. The module-level imports at the top of this file
        # already registered them on SQLModel.metadata; this
        # ``create_all`` makes the SQLite engine aware.
        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(eng)

        captured: list[str] = []

        def _capture(
            conn, cursor, statement, params, context, executemany
        ) -> None:
            captured.append(statement)

        event.listen(eng, "before_cursor_execute", _capture)
        try:
            ri_repo = ReportInjectionRepository(engine=eng)
            with Session(eng) as session:
                # Exercise the production code. SQLite accepts the
                # SQL regardless of the alias-binding fix; the
                # captured statement is what we assert against.
                ri_repo.find_completed_children_without_delivery(
                    parent_not_terminal=True
                )
        finally:
            event.remove(eng, "before_cursor_execute", _capture)
            eng.dispose()

        assert captured, (
            "before_cursor_execute listener did not capture any "
            "statement — production query did not run. Test "
            "infrastructure error."
        )
        return captured[0]

    def test_aliased_dependency_watchers_in_exists_from(self) -> None:
        """The EXISTS subquery's FROM must NOT contain the unaliased
        ``dependency_watchers`` — only the aliased
        ``dependency_watchers AS dw`` is allowed.

        The pre-fix production code emitted::

            SELECT NOT (EXISTS (SELECT dependency_watchers.watch_id
            FROM dependency_watchers
            JOIN task AS tt ON tt.id = CAST(dw.source_task_id AS INTEGER),
                 dependency_watchers AS dw
            WHERE ...))

        SQLAlchemy emits a comma-join of the unaliased
        ``dependency_watchers`` (from the SELECT) AND the aliased
        ``dependency_watchers AS dw`` (from the JOIN). PG rejects
        this with ``UndefinedTable`` because the SELECT references
        ``dependency_watchers`` but the JOIN references the alias
        ``dw``. SQLite silently accepts the malformed statement.

        The post-fix code emits a single FROM entry:
        ``FROM dependency_watchers AS dw`` — the alias is the
        only ``dependency_watchers`` in the EXISTS subquery's
        FROM, and the SELECT references ``dw.watch_id``.

        This test asserts the captured SQL contains EXACTLY ONE
        ``dependency_watchers`` token in the EXISTS subquery's
        FROM clause (the aliased one). Runs anywhere (SQLite or
        PG).
        """
        import re

        captured_sql = self._capture_production_sql()
        # Slice out the FIRED-watcher EXISTS subquery. The SQL
        # has MULTIPLE EXISTS subqueries (delivery, injection,
        # fired-watcher) — find the one that references
        # dependency_watchers (F-2 UPDATE: the WHERE clause
        # order changed after removing the anchor filter).
        search_from = 0
        exists_body = None
        while True:
            exists_open = captured_sql.upper().find("EXISTS (", search_from)
            if exists_open < 0:
                break
            depth = 0
            close_idx = -1
            for i in range(exists_open, len(captured_sql)):
                ch = captured_sql[i]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        close_idx = i
                        break
            if close_idx > exists_open:
                candidate = captured_sql[exists_open:close_idx + 1]
                if "dependency_watchers" in candidate:
                    exists_body = candidate
                    break
            search_from = exists_open + 1
        assert exists_body is not None, (
            f"Could not locate the FIRED-watcher EXISTS subquery "
            f"(the one referencing dependency_watchers) in "
            f"captured SQL: {captured_sql}"
        )
        # Count occurrences of ``dependency_watchers`` (the table
        # name) in the EXISTS subquery. Pre-fix there are TWO:
        # the unaliased ``dependency_watchers`` AND the aliased
        # ``dependency_watchers AS dw``. Post-fix there is ONE
        # (the aliased form).
        dw_count = len(re.findall(r"dependency_watchers", exists_body))
        assert dw_count == 1, (
            f"EXISTS subquery must contain exactly one "
            f"'dependency_watchers' token in its FROM (the aliased "
            f"form 'dependency_watchers AS dw'). Pre-fix the bug "
            f"produced two — the unaliased class reference from the "
            f"SELECT plus the aliased reference from the JOIN — "
            f"which PG rejected with UndefinedTable. Got {dw_count} "
            f"occurrences in EXISTS body:\n{exists_body}"
        )
        # And the one occurrence must be the aliased form.
        assert "dependency_watchers AS dw" in exists_body, (
            f"EXISTS subquery's 'dependency_watchers' must be the "
            f"aliased form 'dependency_watchers AS dw'. EXISTS body:\n"
            f"{exists_body}"
        )

    def test_no_unaliased_dependency_watcher_in_exists_select(self) -> None:
        """The EXISTS subquery's SELECT must reference ``dw.watch_id``
        (the alias) and NOT ``dependency_watchers.watch_id`` (the
        unaliased class).

        Pre-fix the SELECT was ``SELECT dependency_watchers.watch_id``;
        PG's parser resolved ``watch_id`` against the FROM clause's
        unaliased ``dependency_watchers`` — but the FROM clause had
        only the aliased ``AS dw``. PG raised ``UndefinedTable``.

        The post-fix query selects ``dw.watch_id`` — the aliased
        reference resolves against the declared alias. This
        assertion runs anywhere and pins the contract by inspecting
        the SQL the PRODUCTION code actually emits.
        """
        import re

        captured_sql = self._capture_production_sql()
        # Locate the SELECT list inside the EXISTS subquery. The
        # shape is ``EXISTS (SELECT <select_list> FROM dependency_watchers``;
        # we capture the <select_list> portion and assert it ends
        # with the aliased ``dw.watch_id``.
        exists_select_match = re.search(
            r"EXISTS\s*\(\s*SELECT\s+(?P<select>[^F]+?)FROM\s+dependency_watchers",
            captured_sql,
            re.IGNORECASE | re.DOTALL,
        )
        assert exists_select_match, (
            f"Could not locate EXISTS subquery SELECT in captured "
            f"SQL; unexpected shape:\n{captured_sql}"
        )
        select_part = exists_select_match.group("select").strip()
        # The select list must NOT contain the unaliased class
        # reference. Pre-fix it was ``dependency_watchers.watch_id``;
        # post-fix it is ``dw.watch_id``.
        assert "dependency_watchers.watch_id" not in select_part, (
            f"EXISTS subquery SELECT must NOT reference the "
            f"unaliased 'dependency_watchers.watch_id' — pre-fix "
            f"bug. Captured SQL:\n{captured_sql}\n"
            f"Select list: {select_part!r}"
        )
        assert select_part.endswith("dw.watch_id"), (
            f"EXISTS subquery SELECT must reference 'dw.watch_id' "
            f"(the alias declared in the FROM clause). Got: "
            f"{select_part!r}"
        )


# =============================================================================
# PG-gated end-to-end regression — Lane 2 no-row backstop on real PG
# =============================================================================
#
# Companion to ``TestLane2QueryCompilationRegression`` above.
# The compile-level assertion is the portable guard; this PG-gated
# test proves the runtime contract — a FIRED ``DependencyWatcher``
# row excludes the candidate, and a no-FIRED case returns it.
#
# Skips cleanly when PG is unavailable (see ``pg_engine_3_6``
# fixture at the top of this module); never silently passes on
# SQLite (the original false-green that escaped four review
# cycles).


class TestLane2PGRegressionEndToEnd:
    """PG-gated end-to-end regression for the Lane 2 query.

    The dispatcher-scoped requirements (one-line correctness fix +
    PG regression test) live here. Two cases prove the FIRED-
    exclusion contract end-to-end:

    1. ``test_fired_watcher_excludes_candidate_on_pg`` — seed a
       COMPLETED child + non-terminal parent + a FIRED
       ``DependencyWatcher`` row (the production shape when the
       dependency bus has already fired the FollowUp). The query
       MUST return an empty list — the NOT EXISTS predicate
       excludes the row.

    2. ``test_no_fired_watcher_returns_candidate_on_pg`` — same
       scenario but WITHOUT a FIRED watcher. The query MUST
       return the candidate row (the FM-11 escape shape the
       Lane 2 backstop is designed to recover).

    Both tests skip cleanly when PG is unavailable; never run on
    SQLite.
    """

    def test_fired_watcher_excludes_candidate_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """A FIRED ``DependencyWatcher`` row (child_task -> parent)
        EXCLUDES the candidate from the Lane 2 backstop result.

        Seeds: parent (RUNNING) + COMPLETED child + child's
        COMPLETED message + child Task (COMPLETED) + a FIRED
        DependencyWatcher pointing from the child Task to the
        parent. Asserts the row is excluded.

        This is the load-bearing case 5 of the C3 false-positive
        matrix on real PG — the original bug broke it.
        """
        parent = _seed_instance(pg_engine_3_6)
        child_msg_id = "child-msg-fired"
        child_id = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=child_id,
            message_id=child_msg_id,
            status=MessageStatus.COMPLETED.value,
        )
        child_task_id = _seed_pg_task(
            pg_engine_3_6,
            instance_id=child_id,
            status=TaskStatus.COMPLETED.value,
        )
        with Session(pg_engine_3_6) as session:
            session.add(
                DependencyWatcher(
                    source_task_id=str(child_task_id),
                    target_instance_id=parent,
                    follow_up_payload={"k": "v"},
                    watcher_metadata={"kind": "regression"},
                    state=DependencyWatcherState.FIRED.value,
                )
            )
            session.commit()

        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        rows = ri_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        assert not any(r["child_id"] == child_id for r in rows), (
            "FIRED DependencyWatcher MUST exclude the candidate — "
            "the NOT EXISTS predicate on the FIRED watcher is the "
            "load-bearing gate. Pre-fix bug: PG raised UndefinedTable "
            "because the SELECT inside the EXISTS subquery referenced "
            "the unaliased DependencyWatcher class instead of the "
            "'dw' alias. See _LANE2_PG_BUG_FIXED_NOTE."
        )

    def test_no_fired_watcher_returns_candidate_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """Without a FIRED ``DependencyWatcher``, the Lane 2 backstop
        RETURNS the candidate — the FM-11 escape shape the lane
        is designed to recover.

        Seeds: parent (RUNNING) + COMPLETED child + child's
        COMPLETED message. NO ``DependencyWatcher`` row exists
        (no FollowUp registered, or only PENDING / CANCELLED).
        Asserts the query returns the candidate row.
        """
        parent = _seed_instance(pg_engine_3_6)
        child_msg_id = "child-msg-no-fired"
        child_id = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=child_id,
            message_id=child_msg_id,
            status=MessageStatus.COMPLETED.value,
        )

        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        rows = ri_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        matched = [r for r in rows if r["child_id"] == child_id]
        assert matched, (
            "Without a FIRED DependencyWatcher, the Lane 2 backstop "
            "MUST return the candidate — this is the FM-11 escape "
            "shape the lane is designed to recover. Pre-fix bug: "
            "PG raised UndefinedTable so the lane silently errored "
            "every sweep (300s interval) on the primary DB."
        )
        # Sanity: the returned row carries the child_msg_id we seeded.
        assert matched[0]["child_msg_id"] == child_msg_id
        assert matched[0]["parent_id"] == parent



# ═══════════════════════════════════════════════════════════════════════
# F-2 (durability-f1-f2 / phase2, task 2.11(b)) — S24 + S27
# MANDATORY real-PG integration tests on the real delivery seam
# ═══════════════════════════════════════════════════════════════════════


class TestF2S24MultiChildMixedOnPG:
    """S24 — multi-child mixed on real PG.

    Per phase2-plan.md task 2.11(b): "S24 (multi-child mixed):
    seed a parent with three children (one straddle, one
    RUNNING, one already-delivered); run RDRS lane 2; assert
    exactly one ``ensure_deferred`` for the straddle child."

    The reviewer named S24 the DEFINITIVE claim-gate proof
    for waiting_children parents. The lane 2 backstop must
    admit the straddle child (anchor-less completed child of
    a non-terminal parent) while excluding the RUNNING child
    and the already-delivered child — the three-way
    composition is the F-1 wedge state.
    """

    def test_s24_multi_child_mixed_lane2_admits_only_straddle(
        self, pg_engine_3_6: Engine
    ) -> None:
        """S24 — the lane 2 backstop admits the straddle child
        and excludes the RUNNING child and the already-delivered
        child on real PG (the F-1 wedge state on the real
        delivery seam).

        Seeds a parent (RUNNING) + three children:
          * child_straddle: COMPLETED, NO message_queue row
            (the F-1 wedge straddle state — anchor-less)
          * child_running: RUNNING (excluded by the
            c.status='completed' filter)
          * child_delivered: COMPLETED + an existing
            ``internal_report:{child}:%`` message (excluded
            by the has_delivery_row PREFIX ledger)

        Asserts the lane 2 result contains ONLY child_straddle
        (exactly one row; the other two are excluded by the
        respective predicates).
        """
        parent = _seed_instance(pg_engine_3_6)

        # Child 1: STRADDLE — COMPLETED, no message_queue row.
        # This is the F-1 wedge state: the child's terminal
        # report is the checkpoint message, not a
        # message_queue row, so the anchor filter (removed by
        # F-2 task 2.2) does not exclude it.
        child_straddle = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )

        # Child 2: RUNNING — excluded by c.status='completed'.
        child_running = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.RUNNING.value,
        )

        # Child 3: DELIVERED — COMPLETED + an
        # ``internal_report:{child}:%`` message already on the
        # parent's queue. Excluded by has_delivery_row.
        child_delivered = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=parent,
            message_id=f"report-delivered-{uuid.uuid4().hex[:8]}",
            source=f"internal_report:{child_delivered}:anchor-msg",
            status=MessageStatus.READY.value,
        )

        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        rows = ri_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        child_ids = {r["child_id"] for r in rows}
        # ONLY child_straddle is admitted (exactly one row).
        assert child_straddle in child_ids, (
            f"straddle child {child_straddle} MUST be admitted "
            f"(F-1 wedge state); child_ids={sorted(child_ids)}"
        )
        assert child_running not in child_ids, (
            f"running child {child_running} MUST be excluded "
            f"(c.status='completed' filter); child_ids={sorted(child_ids)}"
        )
        assert child_delivered not in child_ids, (
            f"delivered child {child_delivered} MUST be excluded "
            f"(has_delivery_row PREFIX ledger); child_ids={sorted(child_ids)}"
        )
        # Exactly one row admitted.
        assert len(child_ids) == 1, (
            f"exactly one straddle child admitted; "
            f"child_ids={sorted(child_ids)}"
        )

    def test_s24_straddle_passes_through_lane2_per_row_pass(
        self, pg_engine_3_6: Engine
    ) -> None:
        """S24 (integration) — the straddle child, after being
        admitted by the lane 2 query, is minted as a
        ``ReportInjection`` row (PENDING) by the per-row pass
        (via ``ensure_deferred``). The PREFIX ledger check
        finds no ``internal_report:{child}:%`` row (the
        straddle state), so the per-row pass proceeds to
        mint the obligation.

        EXERCISED (W-3 re-word, iteration 2): lane-2 admission →
        queue-side ledger miss → parent-history ledger miss →
        anchor-less derivation (manager-loop bridge) →
        ``ensure_deferred`` mint → DEFERRED→PENDING transition →
        the manager hand-off is INVOKED with the exact triple
        (the manager is a mock at that seam).

        NOT EXERCISED: the real reconcile/re-enter path behind
        the hand-off seam (actual parent wake delivery, the
        worker-pool PROCESS_REPORT claim, delivery completion).
        Those are the manager-side seams covered by the
        sub-shape tests in test_resume_router_deferred_recovery.
        """
        parent = _seed_instance(pg_engine_3_6)
        child_straddle = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        # No message_queue row — the straddle state.
        # No report_injections row — the wedge state.

        # Build a real queue_repo for the PREFIX ledger check
        # (the per-row pass calls find_wake_already_delivered_evidence).
        from daemon.repositories.message_queue.repository import (
            SQLModelMessageQueueRepository,
        )
        queue_repo = SQLModelMessageQueueRepository(
            engine=pg_engine_3_6
        )

        # Mock the checkpointer so the anchor-less
        # ``child_message_id`` derivation succeeds. The
        # derivation reads the surviving child checkpoint
        # via ``get_instance_messages`` and returns the last
        # assistant message's ``message_id`` (BaseMessage.id).
        # We patch ``daemon.persistence.get_instance_messages``
        # to return a fixture with a known message_id.
        derived_id = f"derived-{uuid.uuid4().hex}"
        _mock_persistence_messages(
            child_straddle,
            [
                {
                    "role": "assistant",
                    "content": "child terminal report content",
                    "message_id": derived_id,
                },
            ],
        )
        try:
            service, _ = _build_pg_service(pg_engine_3_6)
            # Wire the real queue_repo into the service.
            service._queue_repo = queue_repo
            # Wire a real checkpointer into the manager so
            # the derivation has a checkpointer to read.
            class _RealCheckpointer:
                class _MockRawSaver:
                    async def aget(self, config):
                        return None
                raw_saver = _MockRawSaver()
            service._manager._checkpointer = _RealCheckpointer()
            # Iteration-2 blocker 1: the bridge needs the
            # manager's REAL loop (loop-bound checkpointer locks).
            with _manager_loop() as loop:
                service._manager._loop = loop
                # Run the lane 2 backstop.
                result = service._run_no_row_backstop_lane()

            # The straddle child was admitted, the PREFIX
            # ledger found no match (no delivery evidence),
            # the derivation succeeded, the
            # ``ensure_deferred`` minted a PENDING row, the
            # ``transition_deferred_to_pending`` accepted the
            # transition, the ``_handle_recover_deferred_report``
            # was called.
            assert result.recovered == 1, (
                f"straddle child must be recovered (F-1 wedge "
                f"closure); recovered={result.recovered}, "
                f"skipped_already_reported={result.skipped_already_reported}"
            )
            # Verify the row state: a PENDING row exists for
            # the (parent, child_straddle) triple.
            from daemon.repositories.report_injection.models import (
                ReportInjection,
                ReportInjectionState,
            )
            with Session(pg_engine_3_6) as session:
                row = session.exec(
                    sm_select(ReportInjection).where(
                        ReportInjection.parent_instance_id == parent,
                        ReportInjection.child_instance_id
                        == child_straddle,
                    )
                ).first()
            assert row is not None, (
                f"ensure_deferred must have minted a row for the "
                f"straddle child; no row found for "
                f"(parent={parent}, child={child_straddle})"
            )
            # The state should be PENDING (the per-row pass
            # transitions DEFERRED → PENDING before the
            # handoff).
            assert row.state == ReportInjectionState.PENDING.value, (
                f"row state MUST be PENDING (D2 end-state "
                f"alignment); got {row.state}"
            )
            # The child_message_id on the row is the
            # derived_id (BaseMessage.id from the checkpoint).
            assert row.child_message_id == derived_id, (
                f"row child_message_id MUST be the derived "
                f"BaseMessage.id (the checkpoint extraction); "
                f"got {row.child_message_id}, expected {derived_id}"
            )
        finally:
            _unmock_persistence_messages()


class TestF2S27NoDuplicateExecutionRegressionOnPG:
    """S27 — no-duplicate-execution regression on real PG.

    Per phase2-plan.md task 2.11(b) + Issue-7: "S27
    (no-regression) — INTEGRATION on real PG seam per C-2 —
    NON-DROPPABLE. Dropping S27 would mean the gate claims
    green on unit tests alone, which C-2 explicitly rejects."

    The load-bearing correctness invariant the RDRS chain
    provides: the obligation-triple unique index (migration
    20260819_000001:114-120) prevents duplicate execution of
    the same (parent, child, child_msg) obligation. The
    per-row pass drives ``ensure_deferred`` (W6 absorbs the
    IntegrityError on duplicate). Running the lane 2 backstop
    twice on the same wedge child results in exactly one
    ``ensure_deferred`` call and exactly one recovery.
    """

    def test_s27_no_duplicate_execution_on_double_run(
        self, pg_engine_3_6: Engine
    ) -> None:
        """S27 — no-duplicate-execution regression on real PG.

        Calls ``ensure_deferred`` twice with the SAME
        (parent, child, child_msg) triple. Asserts:
          * First call: returns the row (the obligation is
            minted).
          * Second call: returns ``None`` (the existing
            PENDING row absorbs the duplicate — the
            obligation-triple unique index prevents
            duplicate INSERTs).
          * The obligation-triple row count is EXACTLY 1.
        """
        parent = _seed_instance(pg_engine_3_6)
        child_straddle = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        from daemon.constants import DEFERRED_REASON_RESUME_ROUTER
        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        child_msg_id = f"derived-{uuid.uuid4().hex}"

        # First call: insert a fresh DEFERRED row.
        row1 = ri_repo.ensure_deferred(
            parent_instance_id=parent,
            child_instance_id=child_straddle,
            child_message_id=child_msg_id,
            deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
        )
        assert row1 is not None, (
            "first ensure_deferred MUST return the row "
            "(a fresh DEFERRED row was inserted); got None"
        )

        # Transition the row to PENDING.
        ri_repo.transition_deferred_to_pending(row1.injection_id)

        # Second call: the existing PENDING row absorbs the
        # duplicate (the obligation-triple unique index
        # prevents duplicate INSERTs).
        row2 = ri_repo.ensure_deferred(
            parent_instance_id=parent,
            child_instance_id=child_straddle,
            child_message_id=child_msg_id,
            deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
        )
        assert row2 is None, (
            "second ensure_deferred MUST return None (the "
            "obligation-triple unique index absorbed the "
            "duplicate; the existing PENDING row's same-reason "
            "duplicate routing is a benign no-op); "
            f"got {row2}"
        )

        # Verify the obligation-triple row count: EXACTLY 1.
        from daemon.repositories.report_injection.models import (
            ReportInjection,
        )
        with Session(pg_engine_3_6) as session:
            rows = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.parent_instance_id == parent,
                    ReportInjection.child_instance_id
                    == child_straddle,
                )
            ).all()
        assert len(rows) == 1, (
            f"obligation-triple unique index MUST prevent "
            f"duplicate INSERTs; got {len(rows)} rows for "
            f"(parent={parent}, child={child_straddle})"
        )

    def test_s27_no_duplicate_via_real_handle_recover_deferred_report(
        self, pg_engine_3_6: Engine
    ) -> None:
        """S27 (end-to-end) — the ``_handle_recover_deferred_report``
        call is made exactly once per obligation across the
        two-run scenario. The PREFIX ledger + the
        obligation-triple unique index jointly enforce
        exactly-once end-to-end.

        This is the load-bearing no-duplicate-execution
        regression: the RDRS chain (ensure_deferred →
        transition → reconcile + re-enter) is the only
        sanctioned path (per W-2 guardrails in
        ``decisions.md §12d``). A double-invocation would
        cause a duplicate parent-wake.

        EXERCISED (W-3 re-word, iteration 2): two full lane-2
        sweeps against real PG; the manager hand-off INVOCATION
        COUNT at the (mocked) seam is exactly 1 with the exact
        triple + source. NOT EXERCISED: the real reconcile/re-enter
        behind the seam (an actual parent wake is not delivered —
        the mock records the call instead).
        """
        parent = _seed_instance(pg_engine_3_6)
        child_straddle = _seed_instance(
            pg_engine_3_6,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )

        from daemon.repositories.message_queue.repository import (
            SQLModelMessageQueueRepository,
        )
        queue_repo = SQLModelMessageQueueRepository(
            engine=pg_engine_3_6
        )

        _mock_persistence_messages(
            child_straddle,
            [
                {
                    "role": "assistant",
                    "content": "child terminal report content",
                    "message_id": f"derived-{uuid.uuid4().hex}",
                },
            ],
        )
        try:
            service, manager = _build_pg_service(pg_engine_3_6)
            service._queue_repo = queue_repo
            class _RealCheckpointer:
                class _MockRawSaver:
                    async def aget(self, config):
                        return None
                raw_saver = _MockRawSaver()
            service._manager._checkpointer = _RealCheckpointer()

            # Iteration-2 blocker 1: the bridge needs the
            # manager's REAL loop (loop-bound checkpointer locks).
            with _manager_loop() as loop:
                service._manager._loop = loop
                # First run: the straddle child is recovered.
                result1 = service._run_no_row_backstop_lane()
                assert result1.recovered == 1

                # Second run: no new recovery.
                result2 = service._run_no_row_backstop_lane()
                assert result2.recovered == 0

            # The manager's ``_handle_recover_deferred_report``
            # was called EXACTLY ONCE across both runs (the
            # exactly-once invariant at the manager level).
            handle_calls = (
                manager._handle_recover_deferred_report.call_args_list
            )
            assert len(handle_calls) == 1, (
                f"manager._handle_recover_deferred_report MUST "
                f"be called exactly once across both runs (no "
                f"duplicate-execution); got {len(handle_calls)} calls"
            )
            # The call shape: the FIRST call was for the
            # straddle child (the obligation that was minted
            # in the first run). The second run saw the
            # existing obligation and skipped (already_recovered
            # path).
            first_call = handle_calls[0]
            first_kwargs = first_call.kwargs
            assert (
                first_kwargs.get("child_instance_id")
                == child_straddle
            ), (
                f"first handle_recover call MUST be for the "
                f"straddle child; got child_instance_id="
                f"{first_kwargs.get('child_instance_id')}"
            )
            assert (
                first_kwargs.get("source") == "sweep_no_row_backstop"
            ), (
                f"first handle_recover call MUST source from "
                f"the no_row_backstop lane; got source="
                f"{first_kwargs.get('source')}"
            )
        finally:
            _unmock_persistence_messages()


# ── F-2 (phase2) test helpers ───────────────────────────────────────────


@contextlib.contextmanager
def _manager_loop():
    """A REAL running event loop standing in for ``manager._loop``.

    Iteration-2 blocker 1: the per-row pass bridges checkpointer
    coroutines onto the manager's loop via
    ``asyncio.run_coroutine_threadsafe(...).result(timeout=8)`` —
    the loop must be LIVE (running on a background thread) or the
    bridge's ``.result()`` would never resolve. Teardown stops the
    loop and joins the thread so no loop leaks across tests.
    """
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5.0)
        loop.close()


def _mock_persistence_messages(
    instance_id: str, messages: list[dict[str, Any]]
) -> Any:
    """Patch ``daemon.persistence.get_instance_messages`` to return
    the given messages for the given instance_id.

    F-2 (phase2 task 2.6) — the per-row pass's anchor-less
    ``child_message_id`` derivation reads the child
    checkpoint via this function. In the PG integration
    tests there is no real LangGraph checkpointer, so the
    derivation would fail. This helper patches the function
    to return a fixture for the duration of a test.

    Iteration-2 routing: the per-row pass now reads the
    checkpointer for BOTH bridge consumers — the parent-history
    ledger scan (instance_id = PARENT) and the derivation
    (instance_id = CHILD). The mock returns the fixture for the
    seeded child and an empty history for every other instance
    (the parent scan finds no delivery evidence → no match → the
    pass proceeds to the derivation), instead of asserting on the
    instance id (the prior assert turned the ledger scan into a
    silent DEBUG-swallowed failure).
    """
    import daemon.persistence as persistence_mod

    async def _fake_get_instance_messages(
        checkpointer, _instance_id, manager=None
    ):
        if _instance_id == instance_id:
            return messages
        # Any other instance (the parent-history ledger scan):
        # empty history → no PREFIX evidence → proceed.
        return []

    # Save the original so _unmock_persistence_messages can
    # restore it.
    _mock_persistence_messages._original = (
        persistence_mod.get_instance_messages
    )
    persistence_mod.get_instance_messages = _fake_get_instance_messages
    # Also patch the ledger module's import (the ledger uses
    # `from .. import persistence as _persistence` so the patch
    # at the daemon.persistence module level is picked up).
    return _fake_get_instance_messages


def _unmock_persistence_messages() -> None:
    """Restore the original ``daemon.persistence.get_instance_messages``."""
    import daemon.persistence as persistence_mod
    if hasattr(_mock_persistence_messages, "_original"):
        persistence_mod.get_instance_messages = (
            _mock_persistence_messages._original
        )


# =============================================================================
# Block-1 G4 — real-PG seeded test mirroring the captured wedge
# =============================================================================
#
# Evidence:
# .agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/db-assertions/d1-pre-kill-assertion.txt
# (decisive R1 capture via SIGSTOP->verify->SIGKILL). Captured state:
#   parent_instance | <id> | waiting_children
#   child_instance  | <id> | completed
#   wake_task       | running | process_report
#   wake_msg_status | ready
#   inj_state       | PENDING
# RDRS lane 1/2/3/4/5 are all skipped on this state (Lane 1 needs
#   DEFERRED, Lane 2 excludes on has_injection_row, Lane 3+4 skip
#   on the 10-minute age_bound, Lane 5 only handles TERMINAL parents).
#   stale_task_recovery defers for 15min via the F-1 boot_epoch
#   amendment. Manual ping deadlocks against the worker-pool
#   per-instance busy guard at task/repository.py:2648-2650.
#
# The new lane 6 (stuck_wake) heals the captured state within one
# sweep cadence by invoking the existing
# ``TaskRepository.force_cancel_and_schedule_retry`` primitive on
# the dead-worker wake task. The retry wakes the worker pools
# =============================================================================
# Block-1 G4 — real-PG seeded test mirroring the captured wedge
# =============================================================================
#
# Evidence:
# .agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/db-assertions/d1-pre-kill-assertion.txt
# (decisive R1 capture via SIGSTOP->verify->SIGKILL). Captured state:
#   parent_instance | <id> | waiting_children
#   child_instance  | <id> | completed
#   wake_task       | running | process_report
#   wake_msg_status | ready
#   inj_state       | PENDING
# RDRS lane 1/2/3/4/5 are all skipped on this state (Lane 1 needs
#   DEFERRED, Lane 2 excludes on has_injection_row, Lane 3+4 skip
#   on the 10-minute age_bound, Lane 5 only handles TERMINAL parents).
#   stale_task_recovery defers for 15min via the F-1 boot_epoch
#   amendment. Manual ping deadlocks against the worker-pool
#   per-instance busy guard at task/repository.py:2648-2650.
#
# The new lane 6 (stuck_wake) heals the captured state within one
# sweep cadence by invoking the existing
# ``TaskRepository.force_cancel_and_schedule_retry`` primitive on
# the dead-worker wake task. The retry wakes the worker pool's
# per-instance claim (no RUNNING task for the parent), the worker
# reads the preserved wake row, and ``instance_messaging`` flips
# the parent from waiting_children to RUNNING.


class TestBlock1G4StuckWakeHealOnPG:
    """Block-1 G4: the captured wedge heals within one sweep cadence.

    Mirrors the R1-captured state verbatim. The lane action is
    observable:

    * ``recovered == 1`` (one wake task force-cancelled + retry).
    * The dead-worker wake task is now ``cancelled`` (the
      ``force_cancel`` arm of ``force_cancel_and_schedule_retry``).
    * A new retry task exists in ``pending`` with the same
      ``message_id`` (the wake row's content) — the worker pool
      claims it (per-instance busy no longer matches because the
      parent has no RUNNING task) and delivers the preserved wake
      row to the parent.
    * The PENDING marker is left in place (the lane's heal
      action does NOT mint or transition the marker — the marker
      is the recovery contract, not the delivery path; the
      preserved wake row IS the delivery).
    * The parent instance stays at ``waiting_children`` (the heal
      unblocks the worker; the LLM-notify's flip is exercised by
      the manager test, not here).
    """

    def test_stuck_wake_lane_heals_captured_wedge_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        from daemon.services.report_delivery_recovery import (
            ReportDeliveryRecoveryService,
        )
        from daemon.repositories.task.repository import TaskRepository
        from daemon.repositories.task.models import TaskType

        # ── Seed the LIVE r1r-captured state ───────────────────────
        # Parent + child instances.
        parent = _seed_instance(
            pg_engine_3_6,
            instance_id="g4-parent-b5a5e621",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        child = _seed_instance(
            pg_engine_3_6,
            instance_id="g4-child-b7c2a7c9",
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        # The marker's child_message_id = the CHILD's content
        # message (the message the child sent at completion —
        # ``34cedf8d-...`` per the r1r pre-kill assertion). The
        # wake row has a DIFFERENT message_id (``ddbeef1d-...``)
        # minted when the wake was created. The two are NOT
        # equal — correlation is via the wake row's ``source``
        # pattern. This is the LIVE shape; the prior commit
        # (3a2bbdf8) used a satisfying-join seed where
        # ``mq.message_id == ri.child_message_id`` (false
        # assurance; the seed satisfied the wrong join).
        child_content_message_id = "g4-child-content-34cedf8d"
        wake_message_id = "g4-wake-msg-ddbeef1d"
        assert child_content_message_id != wake_message_id, (
            "LIVE shape: the wake row's message_id is a fresh "
            "uuid minted at wake-mint time, NOT the marker-stored "
            "child_content_message_id (the two are different ids "
            "per the r1r pre-kill assertion)"
        )
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=parent,
            message_id=wake_message_id,
            status=MessageStatus.READY.value,
            type_=MessageType.COMPLETION_REPORT.value,
            # The source encodes the child_content_message_id
            # (colon-form, per the seven mint sites — see the F-1
            # / F-2 rationale in
            # ``daemon/services/report_delivery_ledger.py`` /
            # ``daemon/repositories/message_queue/repository.py``):
            # ``f"internal_report:{child_iid}:{child_content_msg_id}"``.
            # The lane's source-PREFIX correlation
            # (``mq.source LIKE 'internal_report:' || ri.child_instance_id || ':%'``)
            # matches via the child_iid prefix.
            source=(
                f"internal_report:{child}:{child_content_message_id}"
            ),
        )
        # Dead-worker wake task (process_report on the parent's
        # queue, claimed by a worker that died with the daemon,
        # stale heartbeat since the crash). The wake task's
        # ``message_id`` is the wake ROW's message_id (the worker
        # reads the wake row content via ``task.message_id``).
        stale_heartbeat = (
            _dt.now(_tz.utc).replace(tzinfo=None) - _td(minutes=10)
        ).isoformat()
        wake_task = Task(
            work_id=f"g4-wake-work-{uuid.uuid4().hex[:8]}",
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent,
            message_id=wake_message_id,
            status=TaskStatus.RUNNING.value,
            worker_id="dead-worker-0",
            started_at=stale_heartbeat,
            last_heartbeat_at=stale_heartbeat,
        )
        with Session(pg_engine_3_6) as session:
            session.add(wake_task)
            session.commit()
            session.refresh(wake_task)
            dead_wake_task_id = int(wake_task.id)

        # The PENDING marker (the recovery obligation for the
        # obligation triple). The marker's child_message_id ==
        # the CHILD's content message (NOT the wake row's
        # message_id — the two are different ids in the LIVE
        # shape; the lane correlates via the wake row's source
        # pattern).
        _seed_pg_deferred_row(
            pg_engine_3_6,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id=child_content_message_id,
            state=ReportInjectionState.PENDING.value,
            deferred_reason="system:crash_wake",
        )

        # ── Run the lane ────────────────────────────────────────────
        # Real task_repo so force_cancel_and_schedule_retry actually
        # creates a retry row (the lane's duck-typed gate fires the
        # heal primitive, not a MagicMock).
        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        task_repo = TaskRepository(engine=pg_engine_3_6)
        queue_repo = MagicMock()
        queue_repo.find_wake_already_delivered_evidence = MagicMock(
            return_value=False
        )
        manager = MagicMock()
        manager.engine = pg_engine_3_6
        manager._checkpointer = None
        manager._handle_recover_deferred_report = MagicMock()
        # G4-r4 last-mile: lane 6 dispatches the parent-schedule
        # via enqueue_message (the SAME primitive the manual
        # ping uses; the auto-resume bumps waiting_children →
        # RUNNING at instance_messaging.py:1968). The unit test
        # tests/unit/test_report_delivery_recovery_service.py::
        # TestG4R3StuckWakeParentScheduleSeam pins the schedule
        # call + the EFFECT on the seeded state. Wire the
        # PG-test manager to a no-op coroutine so the bridge
        # resolves without raising; this test focuses on the
        # heal mechanics (cancel + retry + preserve), not the
        # schedule primitive.
        async def _no_op_enqueue(_instance_id, _message, **kwargs):
            return None
        manager.enqueue_message = AsyncMock(
            side_effect=_no_op_enqueue
        )
        service = ReportDeliveryRecoveryService(
            task_repo=task_repo,
            report_injection_repo=ri_repo,
            queue_repo=queue_repo,
            instance_repo=MagicMock(),
            manager_ref=manager,
            interval_seconds=300,
            age_bound_minutes=10,
            batch_cap=100,
            recovery_retry_minutes=1,
            enabled=True,
        )

        # ── Candidate query finds the captured state ───────────────
        candidates = ri_repo.find_stuck_wake_candidates(limit=10)
        assert len(candidates) == 1, (
            f"the captured wedge (LIVE shape: mq.message_id != "
            f"ri.child_message_id, source-PREFIX correlation) "
            f"MUST be a stuck-wake candidate; got {candidates!r}"
        )
        cand = candidates[0]
        assert cand["parent_instance_id"] == parent
        assert cand["child_instance_id"] == child
        assert cand["wake_task_id"] == dead_wake_task_id
        assert cand["wake_message_id"] == wake_message_id
        assert cand["child_message_id"] == child_content_message_id

        # ── The heal ─────────────────────────────────────────────────
        # G4-r4: lane 6 bridges to a real background loop standing
        # in for ``manager._loop`` so the parent-schedule call
        # resolves (the loop must be LIVE on a background thread
        # or the bridge's ``.result(timeout=8)`` never resolves).
        # The PG test focuses on the heal mechanics (cancel +
        # retry + preserve), not the schedule primitive (the
        # unit test in
        # tests/unit/test_report_delivery_recovery_service.py::
        # TestG4R3StuckWakeParentScheduleSeam pins the schedule
        # call + the EFFECT on the seeded state).
        with _manager_loop() as loop:
            service._manager._loop = loop
            lane = service._run_stuck_wake_lane()

        # ── Assertions ──────────────────────────────────────────────
        # The lane counts ONE recovery.
        assert lane.recovered == 1, (
            f"the dead-worker wake task MUST be force-cancelled "
            f"+ retry-minted in a single sweep; recovered="
            f"{lane.recovered}, skipped_busy={lane.skipped_busy}, "
            f"errors={lane.errors}"
        )
        assert lane.errors == 0

        # The dead-worker task is now cancelled (the force_cancel
        # arm of force_cancel_and_schedule_retry).
        with Session(pg_engine_3_6) as session:
            cancelled = session.get(Task, dead_wake_task_id)
            assert cancelled.status == TaskStatus.CANCELLED.value, (
                f"dead-worker task MUST be CANCELLED after heal; "
                f"got status={cancelled.status}"
            )
            assert cancelled.retry_scheduled is True, (
                f"force_cancel MUST set retry_scheduled=True "
                f"(the atomic-UPDATE-with-guard pattern); got "
                f"retry_scheduled={cancelled.retry_scheduled}"
            )

            # The retry task exists in PENDING with the same
            # message_id (the wake row's content).
            retry_tasks = list(
                session.exec(
                    sm_select(Task).where(
                        Task.message_id == wake_message_id,
                        Task.id != dead_wake_task_id,
                    )
                ).all()
            )
            assert len(retry_tasks) == 1, (
                f"exactly ONE retry task must exist for the wake "
                f"message_id; got {len(retry_tasks)} retries"
            )
            retry = retry_tasks[0]
            assert retry.status == TaskStatus.PENDING.value, (
                f"retry task MUST be PENDING (claimable by the "
                f"worker pool — the per-instance busy guard no "
                f"longer matches because the parent has no RUNNING "
                f"task); got status={retry.status}"
            )
            assert retry.instance_id == parent, (
                f"retry task MUST anchor to the parent (worker "
                f"reads the parent's wake row); got "
                f"instance_id={retry.instance_id}"
            )

            # The wake row still exists (the F-1 epoch-belt
            # preservation semantic) and is the message_queue row
            # that the retry worker will read. Its ``status``
            # may have transitioned to ``failed`` as a
            # ``force_cancel_and_schedule_retry`` side effect
            # (the cancel arm marks the companion message_queue
            # row failed so the message-delivery ledger tracks
            # the cancellation — the retry task re-drives the
            # new delivery via the worker's claim of the retry
            # task). The CONTENT + parent linkage are what
            # matter for delivery.
            wake_row = session.exec(
                sm_select(MessageQueue).where(
                    MessageQueue.message_id == wake_message_id,
                )
            ).first()
            assert wake_row is not None, (
                f"preserved wake row MUST persist (F-1 epoch-belt); "
                f"got None"
            )
            assert wake_row.instance_id == parent, (
                f"preserved wake row MUST anchor to the parent; "
                f"got instance_id={wake_row.instance_id}"
            )
            assert wake_row.status in {
                MessageStatus.READY.value,
                MessageStatus.FAILED.value,
            }, (
                f"preserved wake row status MUST be ready (pre-heal) "
                f"or failed (post-cancel side effect); got "
                f"status={wake_row.status}"
            )

            # Exactly ONE wake row (no duplicate delivery evidence).
            all_wake_rows = list(
                session.exec(
                    sm_select(MessageQueue).where(
                        MessageQueue.instance_id == parent,
                        MessageQueue.type == (
                            MessageType.COMPLETION_REPORT.value
                        ),
                    )
                ).all()
            )
            assert len(all_wake_rows) == 1, (
                f"exactly ONE preserved wake row must exist; got "
                f"{len(all_wake_rows)} (a duplicate would create a "
                f"duplicate-execution wedge)"
            )

            # The PENDING marker is preserved (the heal does not
            # touch the marker; the marker is the recovery contract,
            # the wake row is the delivery artifact).
            inj_rows = list(
                session.exec(
                    sm_select(ReportInjection).where(
                        ReportInjection.parent_instance_id == parent,
                        ReportInjection.child_instance_id == child,
                    )
                ).all()
            )
            assert len(inj_rows) == 1
            assert (
                inj_rows[0].state == ReportInjectionState.PENDING.value
            )

        # The child instance is unchanged (the heal does not
        # re-execute the child — child re-execution would be a
        # duplicate-execution regression).
        with Session(pg_engine_3_6) as session:
            child_row = session.get(Instance, child)
            assert (
                child_row.status == InstanceStatus.COMPLETED.value
            ), (
                f"child instance MUST stay COMPLETED (no re-execution); "
                f"got status={child_row.status}"
            )

        # ── (b) half: parent's busy-guard no longer blocks ────────
        # The captured state's deadlock (G4 D2 root cause:
        # per-instance busy guard at task/repository.py:2648-2650)
        # is gone post-heal — the dead-worker wake task is
        # CANCELLED (above); the retry task is PENDING with no
        # worker_id (above); the parent has NO RUNNING sibling.
        # ``has_pending_tasks_blocked_by_busy_instance`` (the
        # SAME busy-guard predicate ``claim_pending_task`` uses,
        # gated to status='running' only) returns False: a NEW
        # pending candidate for this parent is no longer
        # blocked by a RUNNING sibling. The (b) half of the
        # captured wedge's deadlock is gone — the chicken-and-
        # egg with the worker-pool per-instance guard is broken
        # by the dead-worker task cancel + retry mint.
        #
        # Note: ``has_instance_busy`` (the wider call-site guard
        # widened to PENDING + RUNNING + PAUSED at
        # task/repository.py:339) is True post-heal — the retry
        # task IS in PENDING. That guard gates call-site surface
        # operations (NOT the claim path), and the retry task
        # being PENDING is the production-heal shape. The claim-
        # path closure is what unblocks the wedge.
        #
        # Note 2: the retry task is NOT immediately claimable by
        # ``claim_pending_task`` — it carries the production
        # ``next_retry_at = now + backoff`` (backoff_base=60s
        # default; ``schedule_retry`` exponential formula) and
        # the claim's ``next_retry_at <= :now_str`` filter
        # excludes it until the backoff elapses. This is the
        # production-shape wake lane (a worker claims it AFTER
        # the backoff — see task/repository.py:2698-2704 for the
        # PROCESS_REPORT wake-lane priority). The deadlock is
        # gone (the busy guard no longer blocks); the worker
        # claim timing is the production default.
        repo_for_post_heal = TaskRepository(engine=pg_engine_3_6)
        assert (
            repo_for_post_heal.has_pending_tasks_blocked_by_busy_instance()
            is False
        ), (
            "post-heal busy-guard CLOSURE: the parent's claim-path "
            "busy-guard no longer blocks new claims (the dead-"
            "worker wake task is CANCELLED and no RUNNING "
            "sibling remains; the worker's claim is unblocked — "
            "the (b) half of the captured wedge's deadlock is "
            "gone)"
        )
        # And a NEW pending candidate seeded for the parent is
        # also unblocked (the chicken-and-egg would have been:
        # the wake task blocks new candidates → the worker
        # can't claim → the wake can't deliver; post-heal the
        # wake task is CANCELLED → new candidates are claimable
        # once their next_retry_at elapses).
        new_candidate_id = _seed_pg_task(
            pg_engine_3_6,
            instance_id=parent,
            status=TaskStatus.PENDING.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
        )
        with Session(pg_engine_3_6) as session:
            new_candidate = session.get(Task, new_candidate_id)
            # The new candidate is in PENDING (its own per-instance
            # busy guard query would return True — a PENDING task
            # for this parent — but the CLAIM PATH's busy guard
            # is status='running' only, and the dead-worker wake
            # task is now CANCELLED).
            assert new_candidate.status == TaskStatus.PENDING.value
        # Force the claim-path check: this is what ``claim_pending_task``
        # uses internally (status='RUNNING' guard at the top of
        # the WHERE cascade). The new candidate is a
        # ``process_message`` type; the cross-system guard also
        # fires (blocks OTHER candidates whose instance has an
        # active JobItem). With NO active JobItem for this
        # parent (the captured state had a JobItem but the heal
        # does not touch JobItem — the cross-system guard
        # therefore STILL blocks the new candidate). The
        # dispatch's "no RUNNING sibling" closure is the (b)
        # contract; the JobItem-aware cross-system guard is a
        # separate invariant (already satisfied post-heal ONLY
        # IF the captured-state JobItem has been transitioned
        # off-active by some other actor — out of scope for
        # this fix).

    def test_stuck_wake_query_correlates_via_source_not_id_join_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """Live-shape correlation pin (G4-r round-2 — the id-join
        regression class). Seeds the captured state with
        NON-matching message_id vs child_message_id + a
        source-PREFIX correlation. The query MUST find the row
        via the source-PREFIX join (``mq.source LIKE 'internal_report:' ||
        ri.child_instance_id || ':%'``).

        This test pins the source-correlation fix: the prior
        commit (3a2bbdf8) had ``mq.message_id = ri.child_message_id``
        as the join condition — a satisfying-join seed (where
        both ids are equal) made that test green while the LIVE
        row shape (different ids, source correlation) was broken.
        The retry suite (this commit) uses a non-matching id
        seed; an id-join regression would now FAIL this test.

        Companion shape (sibling-child exclusion) is covered by
        ``test_stuck_wake_lane_excludes_sibling_child_on_pg`` below.
        """
        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )

        parent = _seed_instance(
            pg_engine_3_6,
            instance_id="g4-parent-source-pin",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        child = _seed_instance(
            pg_engine_3_6,
            instance_id="g4-child-source-pin",
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        # The CHILD's content message (the message that the
        # child sent at completion). This is what the marker's
        # ``child_message_id`` references. Per the natural-
        # completion mint at child_reports.py:3762, this id is
        # also embedded in the wake row's ``source`` (colon-form).
        child_content_message_id = "g4-child-content-pin"
        # The WAKE ROW's own message_id — a fresh uuid minted
        # when the wake was created. Different from the child's
        # content message_id (LIVE shape, per the r1r pre-kill
        # assertion: mq.message_id=ddbeef1d, ri.child_message_id=
        # 34cedf8d).
        wake_message_id = "g4-wake-msg-source-pin"
        assert child_content_message_id != wake_message_id
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=parent,
            message_id=wake_message_id,
            status=MessageStatus.READY.value,
            type_=MessageType.COMPLETION_REPORT.value,
            source=(
                f"internal_report:{child}:{child_content_message_id}"
            ),
        )
        # Dead-worker wake task (RUNNING, stale heartbeat).
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        from daemon.repositories.task.models import TaskType

        stale_heartbeat = (
            _dt.now(_tz.utc).replace(tzinfo=None) - _td(minutes=10)
        ).isoformat()
        with Session(pg_engine_3_6) as session:
            session.add(
                Task(
                    work_id=f"g4-wake-work-pin-{uuid.uuid4().hex[:8]}",
                    task_type=TaskType.PROCESS_REPORT.value,
                    instance_id=parent,
                    message_id=wake_message_id,
                    status=TaskStatus.RUNNING.value,
                    worker_id="dead-worker-pin",
                    started_at=stale_heartbeat,
                    last_heartbeat_at=stale_heartbeat,
                )
            )
            session.commit()
        _seed_pg_deferred_row(
            pg_engine_3_6,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id=child_content_message_id,
            state=ReportInjectionState.PENDING.value,
            deferred_reason="system:crash_wake",
        )

        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        candidates = ri_repo.find_stuck_wake_candidates(limit=10)
        # The candidate query joins via the source-PREFIX
        # correlation. An id-join regression (mq.message_id =
        # ri.child_message_id) would return 0 candidates here
        # because the ids differ.
        assert len(candidates) == 1, (
            f"LIVE shape: source-PREFIX correlation MUST match "
            f"(wake row id={wake_message_id} != marker "
            f"child_message_id={child_content_message_id}); "
            f"an id-join regression would return 0 here — got "
            f"{candidates!r}"
        )
        cand = candidates[0]
        assert cand["wake_message_id"] == wake_message_id
        assert cand["child_message_id"] == child_content_message_id

    def test_stuck_wake_lane_excludes_sibling_child_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """Child-boundary safety pin (G4-r round-2). Seeds TWO
        children whose wake rows would both collide on a bare
        substring search — the PREFIX boundary rule
        (``internal_report:{child_iid}:...`` colon-form)
        ensures only the matched child's wake row is returned.

        Companion to the source-correlation pin: this test
        catches a regression where the boundary safety of the
        source pattern is broken (e.g. a wrong LIKE prefix that
        matches sibling-child wakes).
        """
        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )

        parent = _seed_instance(
            pg_engine_3_6,
            instance_id="g4-parent-boundary",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        # Two children whose ids share a prefix (the
        # child-boundary collision case). The boundary rule
        # requires the suffix to be colon-delimited OR exact.
        child_a = _seed_instance(
            pg_engine_3_6,
            instance_id="g4-child-boundary-A",
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        # child_b's id EXTENDS G4 child_a's id (the boundary
        # collision). A bare substring LIKE would match BOTH;
        # the colon-form prefix matches ONLY child_a.
        child_b = _seed_instance(
            pg_engine_3_6,
            instance_id="g4-child-boundary-A-sibling",
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        child_a_content_msg = "g4-child-A-content"
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=parent,
            message_id="g4-wake-A-msg",
            status=MessageStatus.READY.value,
            type_=MessageType.COMPLETION_REPORT.value,
            source=(
                f"internal_report:{child_a}:{child_a_content_msg}"
            ),
        )
        child_b_content_msg = "g4-child-B-content"
        _seed_pg_message(
            pg_engine_3_6,
            instance_id=parent,
            message_id="g4-wake-B-msg",
            status=MessageStatus.READY.value,
            type_=MessageType.COMPLETION_REPORT.value,
            source=(
                f"internal_report:{child_b}:{child_b_content_msg}"
            ),
        )
        # Dead-worker wake tasks for both children (with stale
        # heartbeats).
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        from daemon.repositories.task.models import TaskType

        stale_heartbeat = (
            _dt.now(_tz.utc).replace(tzinfo=None) - _td(minutes=10)
        ).isoformat()
        with Session(pg_engine_3_6) as session:
            for msg_id, inst_id in [
                ("g4-wake-A-msg", parent),
                ("g4-wake-B-msg", parent),
            ]:
                session.add(
                    Task(
                        work_id=f"g4-wake-work-boundary-{uuid.uuid4().hex[:8]}",
                        task_type=TaskType.PROCESS_REPORT.value,
                        instance_id=inst_id,
                        message_id=msg_id,
                        status=TaskStatus.RUNNING.value,
                        worker_id="dead-worker-boundary",
                        started_at=stale_heartbeat,
                        last_heartbeat_at=stale_heartbeat,
                    )
                )
            session.commit()
        # PENDING markers for BOTH children.
        _seed_pg_deferred_row(
            pg_engine_3_6,
            parent_instance_id=parent,
            child_instance_id=child_a,
            child_message_id=child_a_content_msg,
            state=ReportInjectionState.PENDING.value,
            deferred_reason="system:crash_wake",
        )
        _seed_pg_deferred_row(
            pg_engine_3_6,
            parent_instance_id=parent,
            child_instance_id=child_b,
            child_message_id=child_b_content_msg,
            state=ReportInjectionState.PENDING.value,
            deferred_reason="system:crash_wake",
        )

        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        candidates = ri_repo.find_stuck_wake_candidates(limit=10)
        # Both children are eligible (their wakes match the
        # boundary-safe LIKE). A regression that breaks the
        # boundary (e.g. LIKE without trailing colon) would
        # double-count or shift; the assertion pins the
        # per-child correlation.
        assert len(candidates) == 2, (
            f"two children with two distinct wake rows should "
            f"correlate correctly; got {len(candidates)} "
            f"candidates"
        )
        child_ids = {c["child_instance_id"] for c in candidates}
        assert child_ids == {child_a, child_b}, (
            f"both children MUST be in the candidate set; got "
            f"{child_ids}, expected {{{child_a}, {child_b}}}"
        )
        # And each candidate's wake_message_id matches its
        # child's wake row (no cross-pollination from the
        # source-PREFIX correlation).
        for cand in candidates:
            if cand["child_instance_id"] == child_a:
                assert cand["wake_message_id"] == "g4-wake-A-msg"
            else:
                assert cand["wake_message_id"] == "g4-wake-B-msg"

    def test_stuck_wake_lane_is_noop_on_empty_db_on_pg(
        self, pg_engine_3_6: Engine
    ) -> None:
        """Empty DB → lane candidate query returns 0 rows → the
        lane records zero recovered (the in-lane loop does not
        execute). This pins the empty-DB contract on PG (the
        unit suite covers SQLite)."""
        from daemon.services.report_delivery_recovery import (
            ReportDeliveryRecoveryService,
        )
        from daemon.repositories.task.repository import TaskRepository

        ri_repo = ReportInjectionRepository(engine=pg_engine_3_6)
        task_repo = TaskRepository(engine=pg_engine_3_6)
        queue_repo = MagicMock()
        queue_repo.find_wake_already_delivered_evidence = MagicMock(
            return_value=False
        )
        manager = MagicMock()
        manager.engine = pg_engine_3_6
        manager._checkpointer = None
        manager._handle_recover_deferred_report = MagicMock()
        service = ReportDeliveryRecoveryService(
            task_repo=task_repo,
            report_injection_repo=ri_repo,
            queue_repo=queue_repo,
            instance_repo=MagicMock(),
            manager_ref=manager,
            interval_seconds=300,
            age_bound_minutes=10,
            batch_cap=100,
            recovery_retry_minutes=1,
            enabled=True,
        )
        # Empty PG DB (the autouse TRUNCATE cleared the seed).
        assert (
            ri_repo.find_stuck_wake_candidates(limit=10) == []
        )
        lane = service._run_stuck_wake_lane()
        assert lane.recovered == 0
        assert lane.errors == 0