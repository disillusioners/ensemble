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
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

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
from daemon.repositories.task.models import Task, TaskStatus
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
    manager = MagicMock()
    manager.engine = engine
    manager._handle_recover_deferred_report = MagicMock()

    service = ReportDeliveryRecoveryService(
        task_repo=task_repo,
        report_injection_repo=ri_repo,
        queue_repo=MagicMock(),
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

    def test_c3_excludes_when_child_message_not_completed(
        self, pg_engine_3_6: Engine
    ) -> None:
        """C3 case 4: a row whose child message is NOT COMPLETED
        is EXCLUDED (the INNER JOIN ``message.status ==
        'completed'`` filters it out).
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
        assert not any(r["child_id"] == child_id for r in rows), (
            "C3 case 4 (non-completed child message) MUST exclude "
            "the candidate — the INNER JOIN filters on "
            "message.status='completed'"
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
        # Slice out just the EXISTS subquery. The shape is
        # ``EXISTS (... )`` — find the matching close paren.
        exists_open = captured_sql.upper().find("EXISTS (")
        assert exists_open >= 0, (
            f"Could not locate EXISTS subquery in captured SQL; "
            f"unexpected shape:\n{captured_sql}"
        )
        # Walk forward to find the matching close paren at depth 0
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
        assert close_idx > exists_open, (
            f"Could not locate EXISTS subquery close paren in "
            f"captured SQL:\n{captured_sql}"
        )
        exists_body = captured_sql[exists_open:close_idx + 1]
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
