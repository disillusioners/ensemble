"""Component C pins — the parent-completion seam (revive-wedge lane).

Lane ``fix/revive-wedge-terminalization`` (2026-11-26). Plan of record:
``.agents/shared/planning/revive-wedge-terminalization/`` — plan.md §15
(the amendment), plan.md §7 (``### Component C`` canonical strings),
decisions.md §9 (D20-D28) and gate-matrix.md §11 (the L8 leg, PC-1..PC-9).

THE DEFECT (half 2 of the fleet-wide terminalization defect): a parent must
complete **at the moment its last child finishes**. The completion guard's
PENDING-TASK COUNT (``ChildReportsService._count_actionable_pending_tasks``
— the SEAM, plan.md §15.2 / D20) refuses that completion when the parent's
own queue holds

  (i)  the WaitingChildren watchdog's OWN wake notice — a notice that exists
       ONLY to tell the parent to complete; counting it defers the very
       completion it asks for, and that deferral has NO re-arm, so the
       parent strands, and
  (ii) a PENDING task that is already DEAD by the repo's own recency rule
       (``is_deferred = false AND created_at < now() - 60s``).

Both conjuncts are FILTERS on the SAME single COUNT query — the pre-existing
INJECTED-report exclusion is UNCHANGED, and the guard's deferral log line is
UNCHANGED.

THE PINS (gate-matrix.md §11.3) — one pin per row:

  PC-1  ROOT path — a queued WC-watchdog notice does NOT defer: terminal fire.
  PC-2  NON-ROOT path — the same shape: the child reports to its parent.
  PC-3  RECENCY — an OLD pending task does NOT defer; a FRESH one DOES.
  PC-4  PRECISION — a ``system:resume_wake`` task STILL defers (the
        exclusion is the WATCHDOG FAMILY, not a blanket ``system:`` one).
  PC-5  PP1 — the plain zero-watcher terminal fire (the filed predicate).
  PC-6  the pre-existing INJECTED-report exclusion still holds.
  PC-7  BOTH guard call sites consult the SAME helper.
  PC-8  R-C3 disposition — the stranded notice: the MessageQueue row
        SURVIVES as the durable audit record; the pending task is the
        repo's terminal-path primitive's to cancel.
  PC-9  PREMATURE-TERMINAL PROTECTION — the recency bound never lets a
        parent complete while it still has live queued work (the own-queue
        leg is the backstop; a FRESH pending task still defers).

FENCE: unit-only, hermetic — an in-memory SQLite engine (StaticPool), no
PostgreSQL, no live DB, no write outside the test engine, no network, no
lifecycle action, no revive. Time handling is DETERMINISTIC: the recency
bound is exercised by BACKDATING the seeded row's ``created_at`` — never by
sleeping.

SELF-CONTAINED: this file depends on NO new non-test helper module (the
tester's base leg copies ONLY this file into the base tree).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, or_, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Model imports — required so SQLModel.metadata sees the tables when
# create_all() runs on the test engine.
from daemon.repositories.dependency_bus.models import (  # noqa: F401
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.dependency_bus.repository import DependencyWatcherRepository
from daemon.repositories.event.models import Event, EventKind  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import (  # noqa: F401
    AdmissionState,
    JobItem,
    JobStatus,
)
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (  # noqa: F401
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services import child_reports as _child_reports_module
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.services.eligible_pending_sweep import DEFAULT_MIN_PENDING_AGE_SECONDS
from daemon.services.timestamps import now_utc_naive
from daemon.services.waiting_children_watchdog import (
    WEDGE_SOURCE,
    WATCHDOG_SOURCE,
)
from daemon.write_pause_guard import WritePauseGuard


# ─────────────────────────────────────────────────────────────────────────────
# Engine + service helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def bus(engine: Engine):
    """Started DependencyBus bound to the test engine (autouse).

    The bus is the SOLE completion authority for the ROOT path — the root
    branch raises a hard error when the bus singleton is ``None`` (A8).
    """
    repo = DependencyWatcherRepository(engine)
    b = DependencyBus(repo)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                ex.submit(asyncio.run, b.start()).result()
        else:
            loop.run_until_complete(b.start())
    except RuntimeError:
        asyncio.run(b.start())
    set_dependency_bus(b)
    # Harness fix (mirrors ``tests/unit/services/test_child_reports.py``):
    # ``DependencyWatcherRepository.count_pending_for_target`` opens its OWN
    # ``Session`` on the test engine. Under ``StaticPool`` (one shared
    # connection) that session's close() rolls back the production
    # transaction on the same connection, so a minted report row vanishes
    # before the test can observe it. In production (Postgres) every session
    # has its own connection and this never happens. These tests seed no
    # watchers, so the correct value is 0; returning it without opening a
    # session keeps the production transaction intact.
    b._repo.count_pending_for_target = lambda target_instance_id: 0
    try:
        yield b
    finally:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    ex.submit(asyncio.run, b.stop()).result()
            else:
                loop.run_until_complete(b.stop())
        except RuntimeError:
            asyncio.run(b.stop())
        set_dependency_bus(None)


@pytest.fixture(autouse=True)
def _reset_dependency_bus():
    """No DependencyBus singleton leaks between tests."""
    set_dependency_bus(None)
    yield
    set_dependency_bus(None)


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    """Build a real ``ChildReportsService`` with a mock manager.

    Mirrors the helper in ``tests/unit/services/test_child_reports.py`` —
    ``__new__`` to skip ``__init__`` and bind the attributes
    ``_process_child_completion_db_sync`` needs.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._checkpointer = None
    manager._live_hub = None  # SSE no-op (guarded on truthiness)
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None  # lifecycle event publish is guarded
    service._strand_heal_inflight = set()
    return service


def _seed_root_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Insert a root Instance row (parent_id=None)."""
    iid = instance_id or f"root-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=iid,
                agent_id="leader",
                agent_name="leader",
                agent_dir="/tmp/leader",
                parent_id=None,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return iid


def _seed_child_instance(
    engine: Engine,
    *,
    parent_id: str,
    status: str = InstanceStatus.RUNNING.value,
    instance_metadata: dict | None = None,
) -> str:
    """Insert a non-root Instance row (the child reports to ``parent_id``)."""
    cid = f"child-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=cid,
                agent_id="worker",
                agent_name="worker",
                agent_dir="/tmp/worker",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata=instance_metadata or {},
            )
        )
        session.commit()
    return cid


def _seed_message_row(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str | None = None,
    source: str | None = None,
    metadata: dict | None = None,
    status: str = MessageStatus.READY.value,
    content: str = "queued message",
) -> str:
    """Insert a MessageQueue row for ``instance_id``."""
    mid = message_id or f"msg-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=mid,
                instance_id=instance_id,
                content=content,
                type=MessageType.SYSTEM.value
                if source and source.startswith("system:")
                else MessageType.HUMAN.value,
                source=source,
                status=status,
                message_metadata=metadata or {},
            )
        )
        session.commit()
    return mid


def _seed_pending_task(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str | None = None,
    task_type: str = "process_message",
    age_seconds: int = 0,
    is_deferred: bool = False,
) -> int:
    """Insert a PENDING Task row for ``instance_id``.

    ``age_seconds`` BACKDATES ``created_at`` (the deterministic recency
    lever — the tests never sleep). ``created_at`` is naive-UTC (the
    column's own frame).
    """
    with Session(engine) as session:
        task = Task(
            task_type=task_type,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.PENDING.value,
            is_deferred=is_deferred,
        )
        if age_seconds:
            task.created_at = now_utc_naive() - timedelta(seconds=age_seconds)
        session.add(task)
        session.commit()
        return task.id


def _seed_injected_report(
    engine: Engine,
    *,
    report_message_id: str,
    child_instance_id: str,
    parent_instance_id: str,
    child_message_id: str = "msg-child-report",
) -> str:
    """Insert an INJECTED ReportInjection row (the pre-existing exclusion)."""
    with Session(engine) as session:
        injection = ReportInjection(
            child_instance_id=child_instance_id,
            parent_instance_id=parent_instance_id,
            child_message_id=child_message_id,
            report_message_id=report_message_id,
            state=ReportInjectionState.INJECTED.value,
        )
        session.add(injection)
        session.commit()
        return injection.injection_id


def _instance_status(engine: Engine, instance_id: str) -> str | None:
    with Session(engine) as session:
        inst = session.get(Instance, instance_id)
        return inst.status if inst is not None else None


def _task_status(engine: Engine, task_id: int) -> str | None:
    with Session(engine) as session:
        task = session.get(Task, task_id)
        return task.status if task is not None else None


def _message_exists(engine: Engine, message_id: str) -> bool:
    with Session(engine) as session:
        row = session.exec(
            SQLModel.metadata.tables["message_queue"]
            .select()
            .where(
                SQLModel.metadata.tables["message_queue"].c.message_id
                == message_id
            )
        ).first()
        return row is not None


def _report_rows_for_parent(engine: Engine, parent_id: str) -> list[str]:
    """The ``source`` values of the completion_report rows minted for a parent."""
    with Session(engine) as session:
        rows = session.exec(
            SQLModel.metadata.tables["message_queue"]
            .select()
            .where(
                SQLModel.metadata.tables["message_queue"].c.instance_id
                == parent_id
            )
        ).all()
        return [row._mapping["source"] for row in rows]


# ─────────────────────────────────────────────────────────────────────────────
# The pins
# ─────────────────────────────────────────────────────────────────────────────


class TestParentCompletionGuard:
    """PC-1..PC-9 — the completion guard's pending-task count (the SEAM)."""

    # ── PC-1 ────────────────────────────────────────────────────────────────
    def test_pc1_root_terminal_fire_with_a_queued_watchdog_notice(
        self, engine: Engine
    ):
        """PC-1 — ROOT path: a queued WC-watchdog notice does NOT defer.

        THE MAINTAINER'S SPECIMEN SHAPE (789c8b69, leader ruling D32):
        the notice's ``MessageQueue`` row is **READY** and its Task is
        **PENDING** (zero children, zero watchers). At the pre-fix base
        BOTH root legs defer — the seam counts the PENDING task AND the
        own-queue leg counts the READY row unconditionally ("READY:
        always counts") — so the parent never reaches COMPLETED ⇒ RED.
        At merge the SAME two-layer watchdog exclusion applies at BOTH
        the seam and the ROOT own-queue leg (M-C8 proves the latter).
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        notice_message_id = _seed_message_row(
            engine,
            instance_id=root_id,
            source=WATCHDOG_SOURCE,
            metadata={"watchdog_notice": {"kind": "hang", "episode": "ep-1"}},
            status=MessageStatus.READY.value,
            content="[system:watchdog] Hang notice",
        )
        _seed_pending_task(engine, instance_id=root_id, message_id=notice_message_id)

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )

        assert result.outcome == "root_completed", (
            "PC-1: the watchdog's own notice must NOT defer the parent's "
            f"completion; got {result.outcome!r}"
        )
        assert _instance_status(engine, root_id) == InstanceStatus.COMPLETED.value

        # The discriminator set is the WATCHDOG FAMILY — pinned against the
        # watchdog's own provenance constants (drift guard).
        assert getattr(
            _child_reports_module, "WATCHDOG_NOTICE_SOURCES", None
        ) == (
            WATCHDOG_SOURCE,
            WEDGE_SOURCE,
        ), "PC-1: the notice sources must be the watchdog's own constants"
        assert set(
            getattr(_child_reports_module, "WATCHDOG_NOTICE_METADATA_KEYS", ())
        ) == {
            "watchdog_notice",
            "watchdog_escalation",
            "watchdog_release",
            "wedge_notice",
        }, "PC-1: the notice metadata keys must be the pinned set"

    # ── PC-2 ────────────────────────────────────────────────────────────────
    def test_pc2_non_root_reports_to_parent_with_a_queued_watchdog_notice(
        self, engine: Engine
    ):
        """PC-2 — NON-ROOT path: the same shape ⇒ the child reports upward.

        At the pre-fix base the notice's PENDING task is counted ⇒ the
        non-root guard defers (``child_still_running_defer``) ⇒ RED at base.

        The NON-ROOT ladder has NO own-queue leg, so the notice's message
        row keeps its PRODUCTION status (``ready``) here.
        """
        service = _build_child_reports_service(engine)
        parent_id = _seed_root_instance(engine)
        child_id = _seed_child_instance(engine, parent_id=parent_id)
        notice_message_id = _seed_message_row(
            engine,
            instance_id=child_id,
            source=WEDGE_SOURCE,
            metadata={"wedge_notice": {"kind": "wedge"}},
            status=MessageStatus.READY.value,
            content="[system:watchdog:wedge] Wedge notice",
        )
        _seed_pending_task(engine, instance_id=child_id, message_id=notice_message_id)

        result = service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )

        assert result.outcome != "child_still_running_defer", (
            "PC-2: the watchdog's own notice must NOT defer the child's "
            f"report to its parent; got {result.outcome!r}"
        )
        assert result.outcome == "regular_child_completed"
        assert result.parent_id == parent_id
        report_sources = _report_rows_for_parent(engine, parent_id)
        assert any(
            src and src.startswith(f"internal_report:{child_id}:")
            for src in report_sources
        ), (
            "PC-2: the child must have reported to its parent; rows="
            f"{report_sources!r}"
        )

    # ── PC-3 ────────────────────────────────────────────────────────────────
    def test_pc3_recency_old_task_does_not_defer_fresh_task_does(
        self, engine: Engine
    ):
        """PC-3 — the RECENCY conjunct (mirror of the repo's orphan rule).

        (a) a PENDING, non-deferred task OLDER than the bound is DEAD ⇒ it
            does NOT defer the completion — on the ROOT path, which is the
            ONLY call site that applies recency (P-C-RECENCY-ROOT-ONLY).
            At the pre-fix base every PENDING task counts ⇒ deferred ⇒ RED.
        (b) a FRESH PENDING task DOES defer on BOTH paths (unchanged
            behaviour — GREEN at base).

        The bound is the repo's OWN constant — pinned here.
        """
        assert getattr(
            _child_reports_module, "P_C_RECENCY_BOUND_SECONDS",
            DEFAULT_MIN_PENDING_AGE_SECONDS,
        ) == (
            DEFAULT_MIN_PENDING_AGE_SECONDS
        ), "PC-3: the recency bound must be the repo's own constant"
        assert DEFAULT_MIN_PENDING_AGE_SECONDS == 60, (
            "PC-3: the repo's age boundary is 60s — the drift guard's and "
            "the observer's orphan rule share it"
        )

        service = _build_child_reports_service(engine)

        # ── arm (a): an OLD pending task does NOT defer (ROOT call site) ───
        root_a = _seed_root_instance(engine)
        old_message_id = _seed_message_row(
            engine,
            instance_id=root_a,
            status=MessageStatus.COMPLETED.value,
            content="an old, already-consumed message",
        )
        _seed_pending_task(
            engine,
            instance_id=root_a,
            message_id=old_message_id,
            age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS + 60,
        )

        result_a = service._process_child_completion_db_sync(
            instance_id=root_a,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )
        assert result_a.outcome == "root_completed", (
            "PC-3(a): a task that is DEAD by the repo's own recency rule "
            f"(is_deferred=false AND created_at < now-60s) must not defer; "
            f"got {result_a.outcome!r}"
        )

        # ── arm (b): a FRESH pending task DOES defer ────────────────────────
        parent_b = _seed_root_instance(engine)
        child_b = _seed_child_instance(engine, parent_id=parent_b)
        fresh_message_id = _seed_message_row(
            engine,
            instance_id=child_b,
            status=MessageStatus.COMPLETED.value,
            content="a fresh message",
        )
        _seed_pending_task(
            engine, instance_id=child_b, message_id=fresh_message_id
        )

        result_b = service._process_child_completion_db_sync(
            instance_id=child_b,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )
        assert result_b.outcome == "child_still_running_defer", (
            "PC-3(b): a FRESH pending task must still defer; got "
            f"{result_b.outcome!r}"
        )

        # ── arm (c): a FRESH pending task DOES defer at the ROOT too ───────
        root_c = _seed_root_instance(engine)
        fresh_root_message_id = _seed_message_row(
            engine,
            instance_id=root_c,
            status=MessageStatus.COMPLETED.value,
            content="a fresh message at the root",
        )
        _seed_pending_task(
            engine, instance_id=root_c, message_id=fresh_root_message_id
        )
        result_c = service._process_child_completion_db_sync(
            instance_id=root_c,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )
        assert result_c.outcome != "root_completed", (
            "PC-3(c): a FRESH pending task must still defer at the ROOT "
            f"(the recency conjunct is age-gated); got {result_c.outcome!r}"
        )

    # ── PC-4 ────────────────────────────────────────────────────────────────
    def test_pc4_system_resume_wake_still_defers(self, engine: Engine):
        """PC-4 — PRECISION: the exclusion is the WATCHDOG FAMILY only.

        ``system:resume_wake`` is a SYSTEM wake that is NOT a watchdog
        notice ⇒ it STILL counts as pending work at BOTH sites: (a) the
        pending-task SEAM (a PENDING task whose message source is
        ``system:resume_wake``) and (b) the ROOT own-queue leg (a READY
        ``system:resume_wake`` MessageQueue row). GREEN at base; the
        target of mutation M-C3 (widening the exclusion to every
        ``system:`` source).
        """
        service = _build_child_reports_service(engine)
        parent_id = _seed_root_instance(engine)
        child_id = _seed_child_instance(engine, parent_id=parent_id)
        wake_message_id = _seed_message_row(
            engine,
            instance_id=child_id,
            source="system:resume_wake",
            metadata={"resume": True},
            status=MessageStatus.COMPLETED.value,
            content="resume wake",
        )
        _seed_pending_task(engine, instance_id=child_id, message_id=wake_message_id)

        result = service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )

        assert result.outcome == "child_still_running_defer", (
            "PC-4(a): a non-watchdog SYSTEM wake must still defer the "
            f"completion (the exclusion is not a blanket 'system:' one); "
            f"got {result.outcome!r}"
        )

        # ── arm (b): the ROOT own-queue leg must count it too ──────────────
        root_id = _seed_root_instance(engine)
        _seed_message_row(
            engine,
            instance_id=root_id,
            source="system:resume_wake",
            metadata={"resume": True},
            status=MessageStatus.READY.value,
            content="resume wake in the root's own queue",
        )
        root_result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )
        assert root_result.outcome == "root_waiting_children", (
            "PC-4(b): a READY non-watchdog SYSTEM wake must still defer "
            f"at the ROOT own-queue leg; got {root_result.outcome!r}"
        )

    # ── PC-5 ────────────────────────────────────────────────────────────────
    def test_pc5_zero_watcher_zero_children_terminal_fire(self, engine: Engine):
        """PC-5 — PP1: the plain zero-watcher terminal fire.

        Zero fulfillable bus watchers + zero active children + zero
        actionable pending ⇒ the root falls through to the terminal CAS.
        The predicate is FILED (pinned), not changed — GREEN at base and at
        merge.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )

        assert result.outcome == "root_completed"
        assert _instance_status(engine, root_id) == InstanceStatus.COMPLETED.value

    # ── PC-6 ────────────────────────────────────────────────────────────────
    def test_pc6_injected_report_exclusion_still_holds(self, engine: Engine):
        """PC-6 — the PRE-EXISTING INJECTED-report exclusion is UNCHANGED.

        A PENDING ``process_report`` task whose report was already delivered
        (``state = injected``) runs NO graph turn ⇒ it must NOT defer.
        GREEN at base; the target of mutation M-C4 (the exclusion must not
        be lost).
        """
        service = _build_child_reports_service(engine)
        parent_id = _seed_root_instance(engine)
        child_id = _seed_child_instance(engine, parent_id=parent_id)
        report_message_id = _seed_message_row(
            engine,
            instance_id=child_id,
            status=MessageStatus.COMPLETED.value,
            content="a completion report already delivered",
        )
        _seed_injected_report(
            engine,
            report_message_id=report_message_id,
            child_instance_id=child_id,
            parent_instance_id=parent_id,
        )
        _seed_pending_task(
            engine,
            instance_id=child_id,
            message_id=report_message_id,
            task_type="process_report",
        )

        result = service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )

        assert result.outcome == "regular_child_completed", (
            "PC-6: an ALREADY-INJECTED report task must not defer the "
            f"completion (the pre-existing exclusion must be preserved); "
            f"got {result.outcome!r}"
        )

    # ── PC-7 ────────────────────────────────────────────────────────────────
    def test_pc7_both_guard_sites_use_the_same_helper(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ):
        """PC-7 — WIRING: BOTH guard call sites consult the SAME helper.

        A one-site fix that left the other path broken would be invisible
        to a single-path pin; the spy proves the root guard AND the
        non-root guard both route through
        ``_count_actionable_pending_tasks``.
        """
        service = _build_child_reports_service(engine)
        calls: list[str] = []
        real_helper = service._count_actionable_pending_tasks

        def _recording_helper(session, instance_id, *args, **kwargs):  # noqa: ANN001, ANN202
            calls.append(instance_id)
            return real_helper(session, instance_id, *args, **kwargs)

        monkeypatch.setattr(
            service, "_count_actionable_pending_tasks", _recording_helper
        )

        root_id = _seed_root_instance(engine)
        service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )

        parent_id = _seed_root_instance(engine)
        child_id = _seed_child_instance(engine, parent_id=parent_id)
        service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )

        assert calls == [root_id, child_id], (
            "PC-7: BOTH guard sites (root + non-root) must consult the "
            f"SAME helper; recorded calls={calls!r}"
        )

    # ── PC-8 ────────────────────────────────────────────────────────────────
    def test_pc8_stranded_notice_disposition(self, engine: Engine):
        """PC-8 — R-C3 disposition of the stranded notice.

        After the completion evaluation the notice's MessageQueue row
        SURVIVES as the durable audit record (the enqueue path writes the
        message row unconditionally — it is never deleted), and the notice's
        PENDING task row is the repo's terminal-path primitive's to cancel
        (``TaskRepository.cancel_pending_tasks_for_instance``): the
        completion path itself performs NO task cancellation.

        The pin is deliberately STAY-GREEN at the pre-fix base (it pins the
        DISPOSITION, not the fix): at the base the completion is deferred by
        the notice's task instead of firing — the disposition of the two
        durable rows is the same either way.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        notice_message_id = _seed_message_row(
            engine,
            instance_id=root_id,
            source=WATCHDOG_SOURCE,
            metadata={"watchdog_notice": {"kind": "hang"}},
            status=MessageStatus.COMPLETED.value,
            content="[system:watchdog] Hang notice",
        )
        notice_task_id = _seed_pending_task(
            engine, instance_id=root_id, message_id=notice_message_id
        )

        service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )

        # The durable audit record survives the completion evaluation.
        assert _message_exists(engine, notice_message_id), (
            "PC-8: the notice's MessageQueue row must SURVIVE the completion "
            "evaluation (it is the durable audit record)"
        )

        # The repo's terminal-path primitive cancels the stranded task —
        # and the message row STILL survives afterwards.
        cancelled = TaskRepository(engine).cancel_pending_tasks_for_instance(
            root_id
        )
        assert cancelled == 1, (
            "PC-8: exactly the stranded notice task must be cancelled; got "
            f"{cancelled}"
        )
        assert _task_status(engine, notice_task_id) == TaskStatus.CANCELLED.value
        assert _message_exists(engine, notice_message_id), (
            "PC-8: the audit record must survive the cancellation too"
        )

    # ── PC-9 ────────────────────────────────────────────────────────────────
    def test_pc9_two_arm_premature_terminal_protection(self, engine: Engine):
        """PC-9 — the TWO-ARM spec (plan.md §15.3.1 / gate-matrix.md §11.3).

        Arm A — the ROOT backstop is REAL: 0 fulfillable bus watchers,
        0 live children, 0 active children and ONE PENDING non-deferred
        task whose created_at is older than the bound AND whose backing
        MessageQueue row is READY and NON-watchdog ⇒ the ROOT still
        defers. The row is a NON-watchdog row on purpose: with the D32
        own-queue exclusion a READY watchdog row would make this arm pass
        for the WRONG reason.

        Arm B — the NON-ROOT path is NOT exposed: the SAME stale shape on
        the non-root ladder MUST still defer (``child_still_running_defer``).
        This arm goes RED if the recency conjunct is applied at the
        non-root call site (mutation M-C6 — the 777eff96 regression).

        Arms C/D — the own-queue leg alone holds the parent; a FRESH
        pending task still defers. STAY-GREEN at base.
        """
        service = _build_child_reports_service(engine)

        # ── Arm A: ROOT backstop, stale task + READY NON-watchdog row ──────
        root_a = _seed_root_instance(engine)
        stale_message_id = _seed_message_row(
            engine,
            instance_id=root_a,
            source="internal_agent:caller",
            metadata={},
            status=MessageStatus.READY.value,
            content="live queued work (non-watchdog)",
        )
        _seed_pending_task(
            engine,
            instance_id=root_a,
            message_id=stale_message_id,
            age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS + 60,
        )
        result_a = service._process_child_completion_db_sync(
            instance_id=root_a,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )
        assert result_a.outcome != "root_completed", (
            "PC-9 Arm A: the ROOT own-queue backstop must hold even when "
            "the pending task is DEAD by recency; got "
            f"{result_a.outcome!r}"
        )
        assert _instance_status(engine, root_a) != InstanceStatus.COMPLETED.value

        # ── Arm B: NON-ROOT path with the SAME stale shape ─────────────────
        parent_b = _seed_root_instance(engine)
        child_b = _seed_child_instance(engine, parent_id=parent_b)
        stale_child_message_id = _seed_message_row(
            engine,
            instance_id=child_b,
            source="internal_agent:caller",
            metadata={},
            status=MessageStatus.READY.value,
            content="live queued work (non-watchdog)",
        )
        _seed_pending_task(
            engine,
            instance_id=child_b,
            message_id=stale_child_message_id,
            age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS + 60,
        )
        result_b = service._process_child_completion_db_sync(
            instance_id=child_b,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )
        assert result_b.outcome == "child_still_running_defer", (
            "PC-9 Arm B: the NON-ROOT ladder has no own-queue backstop, so "
            "recency must NOT be applied there — the child must still "
            f"defer; got {result_b.outcome!r}"
        )

        # ── Arm C: the own-queue leg ALONE holds the parent ────────────────
        root_c = _seed_root_instance(engine)
        _seed_message_row(
            engine,
            instance_id=root_c,
            source="internal_agent:caller",
            status=MessageStatus.READY.value,
            content="live queued work",
        )
        result_c = service._process_child_completion_db_sync(
            instance_id=root_c,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )
        assert result_c.outcome == "root_waiting_children", (
            "PC-9 Arm C: the own-queue leg must hold the parent on its own "
            f"(it is the backstop); got {result_c.outcome!r}"
        )
        assert _instance_status(engine, root_c) == (
            InstanceStatus.WAITING_CHILDREN.value
        )

        # ── Arm D: a FRESH pending task still defers ───────────────────────
        root_d = _seed_root_instance(engine)
        fresh_message_id = _seed_message_row(
            engine,
            instance_id=root_d,
            status=MessageStatus.COMPLETED.value,
            content="a fresh message",
        )
        _seed_pending_task(
            engine, instance_id=root_d, message_id=fresh_message_id
        )
        result_d = service._process_child_completion_db_sync(
            instance_id=root_d,
            completed_message_id="msg-unrelated",
            last_content="assistant text",
        )
        assert result_d.outcome != "root_completed", (
            "PC-9 Arm D: a FRESH pending task must still defer; got "
            f"{result_d.outcome!r}"
        )
        assert _instance_status(engine, root_d) != InstanceStatus.COMPLETED.value

    # ── PC-10 ───────────────────────────────────────────────────────────────
    def test_pc10_dual_dialect_portability_compile(self):
        """PC-10 — compile the notices-exclusion statement for BOTH dialects.

        SQLAlchemy dialect compilers are pure: NO database is touched
        (the house technique at ``daemon/repositories/job_queue/
        repository.py:60-67``). The ONE portable expression must render
        ``->>`` on PostgreSQL and ``JSON_EXTRACT`` on SQLite; a raw ``?``
        operator or a ``text()`` containing ``?`` is FORBIDDEN (SQLite
        reads ``?`` as a bind placeholder). At the pre-fix base no
        exclusion exists, so this pin compiles the pinned shape locally
        and is vacuously GREEN (M-C7 turns it RED at merge).
        """
        factory = getattr(
            _child_reports_module, "_watchdog_notice_exists_expression", None
        )
        if factory is None:
            expression = or_(
                MessageQueue.source.in_((WATCHDOG_SOURCE, WEDGE_SOURCE)),
                *(
                    MessageQueue.message_metadata[key].as_string().is_not(None)
                    for key in (
                        "watchdog_notice",
                        "watchdog_escalation",
                        "watchdog_release",
                        "wedge_notice",
                    )
                ),
            )
        else:
            expression = factory()

        statement = select(MessageQueue.message_id).where(expression)
        pg_sql = str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        sqlite_sql = str(
            statement.compile(
                dialect=sqlite.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "->>" in pg_sql, (
            "PC-10: the PostgreSQL rendering must use the portable "
            f"->> operator; got {pg_sql!r}"
        )
        assert "JSON_EXTRACT" in sqlite_sql.upper(), (
            "PC-10: the SQLite rendering must use JSON_EXTRACT; got "
            f"{sqlite_sql!r}"
        )
        assert "?" not in pg_sql and "?" not in sqlite_sql, (
            "PC-10: a raw ? operator / text() containing ? is FORBIDDEN "
            f"(SQLite bind placeholder); pg={pg_sql!r} sqlite={sqlite_sql!r}"
        )

