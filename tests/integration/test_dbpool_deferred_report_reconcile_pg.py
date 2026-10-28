"""F8-b / F8-c / F8-d / F8-e pins — the deferred-report reconcile on real PG.

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F8.1–F8.9 and §5.2. Marker:
``integration``.

What these pins assert:

* **F8-b** — driving the reconcile with a SLOWED content fetch leaves
  **no** manager-pool backend attributable to the reconcile in an
  ``idle in transaction`` state, and ``engine.pool.checkedout()``
  returns to baseline. (This leg is also the post-F8 re-measurement
  the bound rule needs — DR-11.)
* **F8-c** — a concurrent escalation injected at the **P0→P2 seam**
  (the test-only ``_f8_p0_p2_seam_hook``) produces **NO artefacts**:
  no message INSERT, no task INSERT, no injection UPDATE past the
  guard.
* **F8-d** — the write scope still commits the message INSERT + task
  INSERT + injection UPDATE in **ONE** transaction (the three artefacts
  are consistent with each other and land together).
* **F8-e** — the async twin's hold is **bounded** (the fetch is wrapped
  in ``asyncio.wait_for``); a fetch that exceeds the budget degrades to
  the fallback content instead of hanging.

Plantable mutations:

* re-introduce the borrow-then-await (an ``await`` inside the held
  manager-engine scope) ⇒ F8-b REDs (the census sees the idle-in-txn
  backend);
* inject a concurrent escalation at the P0→P2 seam ⇒ artefacts are
  created ⇒ F8-c REDs;
* split the write scope's single-transaction commit ⇒ F8-d REDs;
* remove the async twin's ``wait_for`` bound ⇒ F8-e REDs.

Run (house form — scratch PG, per-variable env scrub, marker override)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5433 PG_TEST_DB=<scratch db> \
    PG_TEST_USER=<scratch role> PG_TEST_PASSWORD=<scratch pw> \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/integration/test_dbpool_deferred_report_reconcile_pg.py \
        --override-ini="addopts=" -m integration -q --timeout=300
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from types import MethodType
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.pool import QueuePool
from sqlmodel import Session as SQLModelSession
from sqlmodel import SQLModel

# Register every table the helper touches before ``create_all``.
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
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.services import child_reports as child_reports_mod

pytestmark = [pytest.mark.integration]

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 4

_PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
_PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
_PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
_PG_URL = (
    f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
    f"@{_PG_HOST}:{_PG_PORT}/{_PG_DB}"
)


@pytest.fixture(scope="module")
def pg_engine_module():
    """A module-scoped PG engine with the schema (drop → create)."""
    try:
        engine = create_engine(_PG_URL, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except (OperationalError, DBAPIError, Exception) as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available at {_PG_URL}: {exc}")
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def engine(pg_engine_module):
    """A per-test engine with the lane's pool shape + a clean schema."""
    eng = create_engine(
        _PG_URL,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    with eng.begin() as conn:
        for table in (
            "report_injections",
            "task",
            "message_queue",
            "instances",
        ):
            conn.execute(text(f"truncate table {table} cascade"))
    try:
        yield eng
    finally:
        eng.dispose()


# ── seeding ───────────────────────────────────────────────────────────────


def _seed_instance(engine, *, instance_id: str, status: str) -> str:
    with SQLModelSession(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="leader",
                agent_dir="/tmp/leader",
                agent_name="leader",
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_injection(engine, *, injection_id: str, parent_id: str, child_id: str) -> None:
    with SQLModelSession(engine) as session:
        session.add(
            ReportInjection(
                injection_id=injection_id,
                parent_instance_id=parent_id,
                child_instance_id=child_id,
                child_message_id="child-msg-f8",
                report_message_id=None,
                content="report content",
                state=ReportInjectionState.DEFERRED.value,
            )
        )
        session.commit()


def _build_holder(engine):
    """A minimal holder for the reconcile (the house pattern from
    ``tests/unit/test_reconciler_wedge_fix.py``)."""
    from daemon.manager import InstanceManager
    from daemon.write_pause_guard import WritePauseGuard

    @contextmanager
    def _session_scope():
        session = SQLModelSession(engine)
        try:
            yield session
        finally:
            session.close()

    holder: Any = type("F8Holder", (), {})()
    holder.engine = engine
    holder._write_guard = WritePauseGuard()
    holder._session_scope = _session_scope
    holder._child_reports_service = None
    holder._reconcile_deferred_report_async = MethodType(
        InstanceManager._reconcile_deferred_report_async, holder
    )
    holder._has_live_process_report_carrier = MethodType(
        InstanceManager._has_live_process_report_carrier, holder
    )
    # F8: the content fetch is its own helper (no manager-engine session
    # argument any more) — it must be rebound on the holder like the
    # other seam helpers.
    holder._fetch_subshape_a_content_async = MethodType(
        InstanceManager._fetch_subshape_a_content_async, holder
    )
    holder._fetch_subshape_a_content_sync = MethodType(
        InstanceManager._fetch_subshape_a_content_sync, holder
    )
    # The artefact creation (message + task + injection UPDATE in ONE
    # transaction) — shared by both reconcile variants.
    holder._create_subshape_a_artifacts = MethodType(
        InstanceManager._create_subshape_a_artifacts, holder
    )
    holder._is_parent_alive = MethodType(
        InstanceManager._is_parent_alive, holder
    )

    def _notify(self) -> None:
        return None

    holder._notify_all_pools = MethodType(_notify, holder)
    return holder


def _slow_content_fetch(monkeypatch, delay_s: float, *, window=None):
    """Make the content fetch take ``delay_s`` (the instrumented seam).

    ``window`` (optional) receives the fetch window's start/end marks
    so the F8-b census can scope itself to the FETCH — the brief P0/P2
    scoped transactions are legitimate and must not be counted.
    """
    import threading as _threading

    started = _threading.Event()
    ended = _threading.Event()

    async def _patched(service, instance_id, agent_id, **kwargs):
        started.set()
        try:
            await asyncio.sleep(delay_s)
        finally:
            ended.set()
        return "slow child content"

    if window is not None:
        window.started = started
        window.ended = ended
    monkeypatch.setattr(
        child_reports_mod.ChildReportsService,
        "_get_last_assistant_message",
        _patched,
    )


def _count_rows(engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text(f"select count(*) from {table}")
        ).scalar()


# ── the pins ──────────────────────────────────────────────────────────────


class TestF8bNoIdleInTransactionDuringTheFetch:
    """F8-b — the reconcile holds NO manager-engine transaction across
    the content fetch."""

    @pytest.mark.timeout(120)
    def test_no_manager_backend_is_idle_in_transaction_during_the_fetch(
        self, engine, monkeypatch
    ):
        parent_id = _seed_instance(
            engine,
            instance_id=f"parent-{uuid.uuid4().hex[:6]}",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        child_id = _seed_instance(
            engine,
            instance_id=f"child-{uuid.uuid4().hex[:6]}",
            status=InstanceStatus.COMPLETED.value,
        )
        injection_id = f"inj-{uuid.uuid4().hex[:8]}"
        _seed_injection(
            engine, injection_id=injection_id, parent_id=parent_id, child_id=child_id
        )

        class _Window:
            pass

        window = _Window()
        _slow_content_fetch(monkeypatch, 2.0, window=window)
        holder = _build_holder(engine)

        # Sampler: every 200 ms, census the scratch DB's backends and
        # the MANAGER pool's occupancy — from a DEDICATED census engine
        # so the census's own connection is not a manager-pool slot and
        # its own backend state cannot pollute the count.
        census_engine = create_engine(
            _PG_URL, pool_pre_ping=True, future=True
        )
        all_samples: list[tuple[bool, int, int]] = []
        stop = threading.Event()

        def _sample() -> None:
            while not stop.is_set():
                in_window = window.started.is_set() and not window.ended.is_set()
                try:
                    with census_engine.connect() as conn:
                        idle_in_txn = conn.execute(
                            text(
                                "select count(*) from pg_stat_activity "
                                "where datname = current_database() "
                                "and state = 'idle in transaction'"
                            )
                        ).scalar()
                    all_samples.append(
                        (in_window, idle_in_txn, engine.pool.checkedout())
                    )
                except Exception:  # noqa: BLE001 — sampling is best-effort
                    pass
                time.sleep(0.2)

        sampler = threading.Thread(target=_sample, daemon=True)
        sampler.start()
        try:
            result = asyncio.run(
                holder._reconcile_deferred_report_async(
                    child_instance_id=child_id,
                    child_message_id="child-msg-f8",
                    injection_id=injection_id,
                    source="f8-pin",
                )
            )
        finally:
            stop.set()
            sampler.join(timeout=5)
            census_engine.dispose()

        assert result is not None and result["shape"] == "null_marker_first", (
            f"F8-b: the reconcile did not create the artefacts: {result!r}"
        )
        # Non-vacuity: the sampler caught the FETCH WINDOW itself.
        window_samples = [s for s in all_samples if s[0]]
        assert len(window_samples) >= 3, (
            f"F8-b: the sampler caught only {len(window_samples)} "
            "fetch-window samples — the census is vacuous"
        )
        peak_checkedout = max(s[2] for s in window_samples)
        peak_idle_in_txn = max(s[1] for s in window_samples)
        assert peak_checkedout == 0, (
            "F8-b: a manager-engine slot was held during the content "
            f"fetch (peak checkedout={peak_checkedout}) — the "
            "borrow-then-await is back"
        )
        assert peak_idle_in_txn == 0, (
            "F8-b: a backend sat 'idle in transaction' while the reconcile "
            f"awaited the content fetch (peak={peak_idle_in_txn})"
        )
        # …and the pool returned to baseline.
        assert engine.pool.checkedout() == 0, (
            f"F8-b: the pool did not return to baseline "
            f"(checkedout={engine.pool.checkedout()})"
        )


class TestF8cConcurrentEscalationAtTheSeam:
    """F8-c — a concurrent escalation at the P0→P2 seam creates NO
    artefacts."""

    @pytest.mark.timeout(120)
    def test_escalation_at_the_seam_produces_no_artefacts(self, engine):
        parent_id = _seed_instance(
            engine,
            instance_id=f"parent-{uuid.uuid4().hex[:6]}",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        child_id = _seed_instance(
            engine,
            instance_id=f"child-{uuid.uuid4().hex[:6]}",
            status=InstanceStatus.COMPLETED.value,
        )
        injection_id = f"inj-{uuid.uuid4().hex[:8]}"
        _seed_injection(
            engine, injection_id=injection_id, parent_id=parent_id, child_id=child_id
        )

        holder = _build_holder(engine)

        def _escalate(injection_id: str) -> None:
            """Flip the injection row to a terminal state at the seam."""
            with SQLModelSession(engine) as session:
                row = session.get(ReportInjection, injection_id)
                row.state = ReportInjectionState.INJECTED.value
                session.commit()

        holder._f8_p0_p2_seam_hook = _escalate

        result = asyncio.run(
            holder._reconcile_deferred_report_async(
                child_instance_id=child_id,
                child_message_id="child-msg-f8",
                injection_id=injection_id,
                source="f8-pin",
            )
        )

        assert result is None, (
            "F8-c: the reconcile did not skip the escalated row "
            f"(result={result!r})"
        )
        assert _count_rows(engine, "message_queue") == 0, (
            "F8-c: a message row was created for an escalated injection"
        )
        assert _count_rows(engine, "task") == 0, (
            "F8-c: a task row was created for an escalated injection"
        )
        with SQLModelSession(engine) as session:
            row = session.get(ReportInjection, injection_id)
        assert row.report_message_id is None, (
            "F8-c: the injection row was backfilled past the guard "
            f"(report_message_id={row.report_message_id})"
        )
        assert row.state == ReportInjectionState.INJECTED.value


class TestF8dSingleTransactionWrite:
    """F8-d — the write scope commits the three artefacts together."""

    @pytest.mark.timeout(120)
    def test_message_task_and_injection_land_together(self, engine, monkeypatch):
        parent_id = _seed_instance(
            engine,
            instance_id=f"parent-{uuid.uuid4().hex[:6]}",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        child_id = _seed_instance(
            engine,
            instance_id=f"child-{uuid.uuid4().hex[:6]}",
            status=InstanceStatus.COMPLETED.value,
        )
        injection_id = f"inj-{uuid.uuid4().hex[:8]}"
        _seed_injection(
            engine, injection_id=injection_id, parent_id=parent_id, child_id=child_id
        )

        _slow_content_fetch(monkeypatch, 0.1)
        holder = _build_holder(engine)

        result = asyncio.run(
            holder._reconcile_deferred_report_async(
                child_instance_id=child_id,
                child_message_id="child-msg-f8",
                injection_id=injection_id,
                source="f8-pin",
            )
        )
        assert result is not None and result["shape"] == "null_marker_first"

        with SQLModelSession(engine) as session:
            injection = session.get(ReportInjection, injection_id)
            report_message_id = injection.report_message_id
            message = session.get(MessageQueue, report_message_id)
            task = session.execute(
                text(
                    "select count(*) from task where message_id = :m "
                    "and task_type = :t"
                ),
                {"m": report_message_id, "t": TaskType.PROCESS_REPORT.value},
            ).first()

        assert report_message_id, "F8-d: the injection row was not backfilled"
        assert message is not None, "F8-d: the message row is missing"
        assert message.content == "slow child content", (
            "F8-d: the message row does not carry the fetched content"
        )
        assert task is not None and task[0] == 1, (
            "F8-d: the PROCESS_REPORT task row is missing — the three "
            "artefacts did not commit together"
        )


class TestF8eBoundedAsyncHold:
    """F8-e — the async twin's hold is bounded."""

    @pytest.mark.timeout(120)
    def test_fetch_over_the_budget_degrades_instead_of_hanging(
        self, engine, monkeypatch
    ):
        parent_id = _seed_instance(
            engine,
            instance_id=f"parent-{uuid.uuid4().hex[:6]}",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        child_id = _seed_instance(
            engine,
            instance_id=f"child-{uuid.uuid4().hex[:6]}",
            status=InstanceStatus.COMPLETED.value,
        )
        injection_id = f"inj-{uuid.uuid4().hex[:8]}"
        _seed_injection(
            engine, injection_id=injection_id, parent_id=parent_id, child_id=child_id
        )

        # A fetch that exceeds the 8.0 s budget.
        _slow_content_fetch(monkeypatch, 9.5)
        holder = _build_holder(engine)

        started = time.monotonic()
        result = asyncio.run(
            holder._reconcile_deferred_report_async(
                child_instance_id=child_id,
                child_message_id="child-msg-f8",
                injection_id=injection_id,
                source="f8-pin",
            )
        )
        elapsed = time.monotonic() - started

        assert elapsed < 20, (
            f"F8-e: the reconcile took {elapsed:.1f}s — the async twin's "
            "hold is not bounded"
        )
        assert result is not None and result["shape"] == "null_marker_first", (
            f"F8-e: the budget-exceeding fetch did not degrade cleanly: "
            f"{result!r}"
        )
        with SQLModelSession(engine) as session:
            injection = session.get(ReportInjection, injection_id)
            message = session.get(MessageQueue, injection.report_message_id)
        assert message.content == "[No response content]", (
            "F8-e: the budget-exceeding fetch did not degrade to the "
            f"fallback content (got {message.content!r})"
        )
