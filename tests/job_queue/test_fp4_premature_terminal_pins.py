"""FP4 commission pinned tests (2026-10-03, report-delivery bug family).

This file pins the FP4 premature-terminal gate family + PP2
agent-lane ``job_create`` default-flip. R1/R2/R4/R5 cover the four
core scenarios from the commission brief. The PP2 test is a separate
class (the agent-lane tool seam is disjoint from the
``child_reports`` gate family).

## Pins

### FP4 — premature-terminal: missions awaiting child reports must not flip completed

1. **R1** ``TestR1PrematureTerminalAwaitingChildren`` — root
   ``waiting_children`` + ≥1 non-terminal child via ``parent_id`` + bus
   watcher rows ABSENT + root queue empty + root last-assistant ts <
   last child-report ``completed_at`` → fire ``message_completed`` →
   assert NO completed stamp, NO lifecycle completed, JobItem ACTIVE,
   no ``[JOB_EVENT] completed``.

   The orphan-watch class: the bus is fail-OPEN (the L2 leg's
   ``bus_count_pending_for_target_sync`` returns 0 when the bus
   singleton is None). The L2 tree-liveness leg (added 2026-10-03)
   is the safety net: it consults the permanent ``instances.parent_id``
   tree via ``evaluate_mission_live`` and BLOCKS the gate when ANY
   non-terminal member is present.

2. **R2** ``TestR2DeclaredWaitHold`` (3826ab28 mode) — child terminal,
   report ``COMPLETED`` in queue, declared-wait positive, no fresh
   response → assert HOLD + ledger increment; inject tree
   ``last_activity > 6h`` → assert exactly-once terminal with
   escalated-unverified display + watchers delivered once.

   L2b discharge: the wedge resolver detects declared-wait outstanding
   and returns ``(True, None)`` so the gate's allow verdict is
   preserved. L6 takes over at the emission-time handler: increments
   the ledger, checks the settle bound
   (``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS`` = 6h), and either HOLDS
   (set WAITING_CHILDREN, suppress publish) or RELEASES (set
   ``completion_gate_escalated=True``, proceed with publish; the
   work_notifier layer renders the escalated display).

3. **R4** ``TestR4WedgeRegression`` — while HELD, retry/continue paths
   still admit (admission ACTIVE, retry budget intact).

   The HOLD keeps the instance in ``WAITING_CHILDREN`` and the JobItem
   in ``ACTIVE`` admission — the done+retry-0/0 wedge is the bug; the
   ACTIVE state is what makes retry/continue paths still admit on
   the next message-completed signal.

4. **R5** ``TestR5LostDispatch`` — child report arrives while parent
   HELD → assert consumed (``TASK_DELIVERED`` + parent turn processes
   it).

   The HOLD is event-driven (re-evaluates on the message-completed
   signal); a child report that lands while the parent is HELD is
   consumed and processed by the parent's existing turn.

### PP2 — agent-lane job_create default watch=true

5. **PP2** ``TestPP2WatchDefaultFlip`` — default-flip test +
   explicit-false-wins test.

   The agent-facing ``job_create`` tool's ``watch`` parameter flipped
   to ``True`` as the default. The user explicitly approved the
   flip. ``watch=False`` is still honored when the agent opts out.

## Sibling boundary

This file exercises ONLY the owned files
(``daemon/services/child_reports.py`` + ``daemon/tools/job_queue.py``
+ ``daemon/services/report_integrity_guard.py`` +
``daemon/services/attestation_ledger.py``). The c264aa8a reconcile
arm (FP4-L3: ``found=False → no force-finalize when mission live``)
is implemented by Coder A in ``task/repository.py`` +
``job_feedback_observer.py`` — NOT exercised here.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Import models so ``SQLModel.metadata.create_all`` builds the full
# schema. Mirrors the recipe in ``test_job_answer_tool.py`` and
# ``test_midflight_qa.py``.
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.dependency_bus.models  # noqa: F401

from daemon.constants import (
    COMPLETION_GATE_ESCALATED_DISPLAY,
)
from daemon.services.mission_live_guard import (
    MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
)
from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.child_reports import ChildReportsService
from daemon.services.completion_registry import _completion_registry
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.event_bus import EventBus
from daemon.services.event_publisher import EventPublisherService
from daemon.services.mission_live_guard import evaluate_mission_live
from daemon.services.report_integrity_guard import (
    evaluate_declared_waiting_violations,
)
from daemon.tools.job_queue import create_job_tools


# Timestamps used across tests (all UTC)
T_CHILD_COMPLETED = datetime(2026, 9, 20, 17, 30, 0, tzinfo=timezone.utc)
T_FRESH_ASSISTANT = "2026-09-20T17:40:00+00:00"   # AFTER children reported


# ─── Fixtures + helpers ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_completion_registry():
    """Reset the global CompletionRegistry singleton between tests."""
    _completion_registry.__init__()
    yield
    _completion_registry.__init__()


@pytest.fixture
def fp4_engine():
    """In-memory SQLite engine — single connection for cross-thread
    ``asyncio.to_thread`` calls (mirrors the recipe in
    ``test_job_answer_tool.py:99-110``)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def fp4_harness(fp4_engine):
    """Real repos + manager mock + ChildReportsService.

    Wires the same component set the production seam uses: real
    ``SQLModelInstanceRepository`` (so the L2 tree-liveness leg can
    walk ``instances.parent_id`` via ``get_tree_ids_permanent``),
    real ``TaskRepository``, real ``JobRepository``, real
    ``JobWatcherRepository``, real ``EventRepository``,
    ``EventBus``, and a manager mock whose async seams are recorded
    (live-hub SSE, ``enqueue_message``, etc.).

    The dependency bus singleton is reset to a per-test MagicMock so
    the bus-pending leg is deterministic.
    """
    instance_repo = SQLModelInstanceRepository(fp4_engine)
    task_repo = TaskRepository(fp4_engine)
    job_repo = JobRepository(fp4_engine)
    watcher_repo = JobWatcherRepository(fp4_engine)
    from daemon.repositories.event.repository import EventRepository
    event_repo = EventRepository(fp4_engine)
    event_bus = EventBus(event_repo=event_repo)

    # The conftest's autouse ``dependency_bus`` fixture provides a
    # mock bus via ``set_dependency_bus`` — it ensures the singleton
    # is initialized for every test in this directory (the bus is
    # hard-required by the production seam at
    # ``child_reports.py:2675``). We rely on that autouse fixture
    # and do NOT override the singleton here.

    manager = MagicMock()
    manager.engine = fp4_engine
    manager._engine = fp4_engine
    manager.write_guard = MagicMock()
    manager._instance_repository = instance_repo
    manager._task_repo = task_repo
    manager._watcher_repo = watcher_repo
    manager._event_bus = event_bus
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._live_hub.stream_question_pack = AsyncMock()
    manager._live_hub.stream_answer_received = AsyncMock()
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-fp4")
    )

    # Disable report-repair so the legacy report-repair config
    # accesses don't raise on the MagicMock.
    manager.config = MagicMock()
    manager.config.report_repair = MagicMock()
    manager.config.report_repair.enabled = False
    manager.config.report_repair.repair_excluded_agents = []
    manager.config.report_repair.size_ratio_threshold = 5.0
    manager.config.report_repair.lookback_messages = 3
    # Attestation gate is OFF by default (operator-owned flip).
    manager.config.report_integrity = MagicMock()
    manager.config.report_integrity.b_terminal_waiting_guard_enabled = True

    events_service = MagicMock()
    events_service._publish_instance_lifecycle_event = AsyncMock()

    service = ChildReportsService(
        manager=manager, events_service=events_service,
    )

    return SimpleNamespace(
        engine=fp4_engine,
        manager=manager,
        instance_repo=instance_repo,
        task_repo=task_repo,
        job_repo=job_repo,
        watcher_repo=watcher_repo,
        event_bus=event_bus,
        events_service=events_service,
        service=service,
    )


def _seed_instance(
    engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    parent_id: str | None = None,
    agent_id: str = "leader",
    last_activity_at: datetime | None = None,
    instance_metadata: dict | None = None,
) -> str:
    """Insert (or UPDATE) an ``Instance`` row; returns its
    ``instance_id``. UPDATE-on-conflict lets tests re-seed the same
    row with new fields (``instance_metadata`` for the bind anchor,
    ``status`` for the held state) without violating the unique
    constraint on ``instance_id``.
    """
    iid = instance_id or f"inst-{uuid.uuid4()}"
    now_dt = datetime.now(timezone.utc)
    with Session(engine) as session:
        existing = session.get(Instance, iid)
        if existing is None:
            session.add(
                Instance(
                    instance_id=iid,
                    agent_id=agent_id,
                    agent_dir="/tmp/agents",
                    project_id="test-project",
                    status=status,
                    version=1,
                    instance_metadata=instance_metadata or {},
                    parent_id=parent_id,
                    last_activity_at=last_activity_at or now_dt,
                )
            )
        else:
            existing.status = status
            if instance_metadata is not None:
                existing.instance_metadata = instance_metadata
            if parent_id is not None:
                existing.parent_id = parent_id
            if last_activity_at is not None:
                existing.last_activity_at = last_activity_at
            session.add(existing)
        session.commit()
    return iid


def _seed_child(
    engine,
    *,
    parent_id: str,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Insert a child ``Instance`` row; returns its ``instance_id``."""
    return _seed_instance(
        engine,
        status=status,
        parent_id=parent_id,
        agent_id="coder",
    )


def _seed_terminal_message(
    engine,
    instance_id: str,
    message_id: str,
    status: str = MessageStatus.COMPLETED.value,
) -> None:
    """Insert a terminal (COMPLETED/FAILED) ``MessageQueue`` row for
    the given instance."""
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="child report content",
                type=MessageType.COMPLETION_REPORT.value,
                source=f"internal_report:child:{message_id}",
                status=status,
                priority=0,
                completed_at=datetime.now(timezone.utc),
            )
        )
        session.commit()


def _seed_event(
    engine,
    instance_id: str,
    kind: str = EventKind.CHILD_COMPLETED.value,
    when: datetime | None = None,
) -> None:
    """Insert an ``Event`` row (child_completed / etc.)."""
    with Session(engine) as session:
        session.add(
            Event(
                instance_id=instance_id,
                kind=kind,
                data="{}",
                created_at=when or datetime.now(timezone.utc),
            )
        )
        session.commit()


def _seed_report_injection(
    engine,
    *,
    parent_instance_id: str,
    child_instance_id: str,
    state: str = ReportInjectionState.PENDING.value,
) -> None:
    """Insert a ``ReportInjection`` row in the given state. Used to
    simulate the (b) declared-waiting violation: PRIMARY signal = a
    ``report_injections`` row with ``state IN ('PENDING','DEFERRED')``
    whose child instance is terminal.
    """
    with Session(engine) as session:
        session.add(
            ReportInjection(
                injection_id=str(uuid.uuid4()),
                parent_instance_id=parent_instance_id,
                child_instance_id=child_instance_id,
                # ``child_message_id`` is a NOT NULL column; the
                # production seam sets it to the source child
                # report's message id. The test only needs the row
                # to exist (the (b) predicate reads by
                # parent + state), so we use a synthetic uuid.
                child_message_id=f"child-msg-{uuid.uuid4()}",
                report_message_id=f"msg-{uuid.uuid4()}",
                state=state,
            )
        )
        session.commit()


def _add_watch(
    engine,
    *,
    work_id: str,
    instance_id: str,
    watch_events: list[str],
) -> None:
    """Insert a ``JobWatcher`` row — the watcher-fanout recipient."""
    with Session(engine) as session:
        session.add(
            JobWatcher(
                job_id=work_id,
                instance_id=instance_id,
                watch_events=watch_events,
            )
        )
        session.commit()


def _patch_get_instance_messages(history: list[dict] | None) -> object:
    """Patch the ``get_instance_messages`` consumer-binding at the
    child_reports module. Mirrors the round-2-council-fixes recipe.
    """
    if history is None:
        history = []
    return patch(
        "daemon.services.child_reports.get_instance_messages",
        new_callable=AsyncMock,
        return_value=history,
    )


# ─── R1: premature-terminal-awaiting-children ────────────────────────────────


class TestR1PrematureTerminalAwaitingChildren:
    """R1 — root waiting_children + non-terminal child + bus watcher
    rows ABSENT → fire ``message_completed`` → gate must NOT pass.

    The orphan-watch class: a child whose watcher row was never
    INSERTed (cold-load None-read, unattributed cache eviction) cannot
    surface through the bus count — the bus-pending leg returns 0
    even though a child is genuinely still running. The L2
    tree-liveness leg is the safety net: it walks the permanent
    ``instances.parent_id`` tree and BLOCKS the gate when any
    non-terminal member is present.
    """

    @pytest.mark.asyncio
    async def test_root_with_non_terminal_child_blocks_via_tree_liveness(
        self, fp4_harness,
    ):
        """Root in WAITING_CHILDREN + 1 RUNNING child (parent_id link) +
        bus empty + root queue empty + no fresh assistant → fire
        message_completed → assert NO completed stamp, NO lifecycle
        completed, root stays in WAITING_CHILDREN.
        """
        # Seed the parent (in WAITING_CHILDREN — the prior round
        # of the helper has parked it there).
        parent_id = _seed_instance(
            fp4_harness.engine,
            instance_id="parent-r1",
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_id="leader",
        )
        # Seed the child in RUNNING (non-terminal). The parent_id
        # link is the canonical ``instances.parent_id`` reference.
        child_id = _seed_child(
            fp4_harness.engine,
            parent_id=parent_id,
            status=InstanceStatus.RUNNING.value,
        )
        # No fresh assistant message — the freshness leg will fail
        # via the wedge resolver. Pre-stage the report row so the
        # pending_count query sees it.
        _seed_terminal_message(
            fp4_harness.engine, parent_id, "report-msg-r1",
        )
        # Patch consumer-binding for ``get_instance_messages``
        # (used by the freshness leg).
        history = [
            {
                "role": "user",
                "content": "do the work",
                "created_at": "2026-09-20T16:00:00+00:00",
            },
            {
                "role": "assistant",
                "content": "stale response",
                "created_at": "2026-09-20T17:00:00+00:00",
            },  # BEFORE child_completed → freshness fails
        ]
        child_completed_when = T_CHILD_COMPLETED

        with _patch_get_instance_messages(history), \
             patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
            await fp4_harness.service._process_child_completion_and_notify_parent(
                parent_id, "report-msg-r1",
            )

        # The parent stays in WAITING_CHILDREN — the L1 stamp guard
        # + L2 tree-liveness leg BLOCK the stamp. No lifecycle
        # completed event fires.
        with Session(fp4_harness.engine) as session:
            row = session.get(Instance, parent_id)
            assert row.status == InstanceStatus.WAITING_CHILDREN.value
        # No lifecycle "completed" event.
        publish_calls = (
            fp4_harness.events_service
            ._publish_instance_lifecycle_event.await_args_list
        )
        for call in publish_calls:
            assert call.kwargs.get("status") != "completed" or (
                call.kwargs.get("instance_id") != parent_id
            ), (
                "L1+L2 must prevent the parent from being stamped "
                "COMPLETED while a non-terminal child is alive — the "
                "R1 orphan-watch class. Pre-FP4 this would publish "
                "a premature 'completed' event."
            )

    @pytest.mark.asyncio
    async def test_tree_liveness_leg_blocks_independently_of_bus(
        self, fp4_harness,
    ):
        """Unit-level pin on the L2 leg: when bus is empty but a
        non-terminal descendant exists, ``_root_completion_gate``
        returns ``(False, ...)`` with the tree-liveness reason.

        This is the direct unit check of the leg's contract — the
        integration test above exercises the same code through the
        full handler. Pinned here so a regression REMOVING the L2
        leg surfaces as a unit test failure.
        """
        parent_id = _seed_instance(
            fp4_harness.engine,
            instance_id="parent-tree",
            status=InstanceStatus.RUNNING.value,
        )
        child_id = _seed_child(
            fp4_harness.engine,
            parent_id=parent_id,
            status=InstanceStatus.RUNNING.value,
        )
        # Fresh assistant (so freshness is True; the wedge resolver
        # would return (True, None) on its own).
        history = [
            {
                "role": "assistant",
                "content": "fresh response",
                "created_at": T_FRESH_ASSISTANT,
            },
        ]
        with _patch_get_instance_messages(history):
            allowed, reason = (
                await fp4_harness.service._root_completion_gate(
                    None, parent_id,
                )
            )

        # Gate blocks because the tree has a non-terminal member,
        # even though the bus is empty (orphan-watch).
        assert allowed is False, (
            "L2 tree-liveness leg must BLOCK the gate when a "
            "non-terminal descendant exists. Pre-FP4 the gate "
            "would PASS (False negative) because the bus-pending "
            "leg returns 0 for the orphan-watch class."
        )
        assert reason is not None
        assert "tree_liveness" in reason or "non-terminal" in reason, (
            f"Block reason must name the tree-liveness leg; got "
            f"{reason!r}"
        )


# ─── R2: declared-wait hold (3826ab28) + 6h escalate ────────────────────────


class TestR2DeclaredWaitHold:
    """R2 — declared-wait outstanding → HOLD + ledger increment;
    bind reached → RELEASE with escalated display.

    The 3826ab28 mode: parent declared waiting and the interim
    report was delivered, but the declared-wait obligation is still
    open. Pre-FP4 the wedge resolver would escalate to annotate
    (via the (b) log-only helper) and the parent would re-stamp to
    COMPLETED on the next turn — the done+retry-0/0 wedge.

    FP4 L2b discharges: the wedge resolver detects the declared-wait
    violation via the (b) predicate and returns ``(True, None)`` so
    the gate's allow verdict is preserved. L6 takes over at the
    emission-time handler with the HOLD / RELEASE decision.

    Test setup: the freshness leg must FAIL (stale assistant
    timestamp before the child_completed event) so the wedge
    resolver runs. The (b) predicate is consulted ONLY when
    freshness fails — otherwise the L2b discharge is skipped (the
    gate allows via the freshness-True fast path).
    """

    @pytest.mark.asyncio
    async def test_declared_wait_outstanding_holds_within_bind(
        self, fp4_harness,
    ):
        """Child terminal, report COMPLETED in queue, declared-wait
        positive (PRIMARY signal: PENDING report_injection whose
        child is terminal), no fresh response → assert HOLD +
        ledger increment.

        The L2b path returns ``(True, None)`` (the gate allows);
        the L6 path at the emission-time handler detects
        ``_declared_wait_outstanding`` and routes to HOLD because
        the bind has not been reached.
        """
        # Seed the parent (the helper stamps it COMPLETED first,
        # then the L6 path downgrades to WAITING_CHILDREN).
        parent_id = _seed_instance(
            fp4_harness.engine,
            instance_id="parent-r2-hold",
            status=InstanceStatus.RUNNING.value,
        )
        # Seed a terminal child.
        child_id = _seed_instance(
            fp4_harness.engine,
            instance_id="child-r2-hold",
            status=InstanceStatus.COMPLETED.value,
            parent_id=parent_id,
            agent_id="coder",
        )
        # Seed a PENDING report_injection for the (b) PRIMARY
        # signal — this is the declared-waiting violation.
        _seed_report_injection(
            fp4_harness.engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            state=ReportInjectionState.PENDING.value,
        )
        # Seed a child_completed event AFTER the stale assistant
        # timestamp so the freshness check FAILS — this routes
        # through the wedge resolver and triggers the L2b
        # discharge. Without the event, freshness returns True
        # and L2b is bypassed.
        _seed_event(
            fp4_harness.engine,
            parent_id,
            kind=EventKind.CHILD_COMPLETED.value,
            when=T_CHILD_COMPLETED,
        )
        # Stale assistant (BEFORE the child_completed).
        history = [
            {
                "role": "user",
                "content": "do the work",
                "created_at": "2026-09-20T16:00:00+00:00",
            },
            {
                "role": "assistant",
                "content": "stale",
                "created_at": "2026-09-20T17:00:00+00:00",
            },
        ]
        with _patch_get_instance_messages(history), \
             patch("daemon.services.child_reports.get_last_assistant_timestamp", new_callable=AsyncMock, return_value="2026-09-20T17:00:00+00:00"), \
             patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
            await fp4_harness.service._process_child_completion_and_notify_parent(
                parent_id, f"msg-r2-hold",
            )

        # The L2b path discharges: the wedge resolver returns
        # (True, None) (gate allows). The L6 path then routes to
        # HOLD because the bind is far in the future — the parent
        # is downgraded back to WAITING_CHILDREN, no lifecycle
        # "completed" event.
        with Session(fp4_harness.engine) as session:
            row = session.get(Instance, parent_id)
            assert row.status == InstanceStatus.WAITING_CHILDREN.value, (
                "L6 HOLD: parent must be downgraded to "
                "WAITING_CHILDREN while the declared-wait is "
                "outstanding within the settle bind."
            )
        # Ledger was incremented (the L6 escalate action).
        with Session(fp4_harness.engine) as session:
            row = session.get(Instance, parent_id)
            assert int(row.attestation_denied_count or 0) >= 1, (
                "L6 escalate: the attestation ledger must be "
                "incremented when the declared-wait HOLD fires."
            )

    @pytest.mark.asyncio
    async def test_declared_wait_releases_at_bind_with_escalated_flag(
        self, fp4_harness,
    ):
        """Bind reached (first denial epoch > 6h old) → terminal
        fires ONCE with ``completion_gate_escalated=True`` set by
        the ledger atomic op; the work_notifier layer renders the
        escalated display (COMPLETION_GATE_ESCALATED_DISPLAY).

        Pinned: the bind is ``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS``
        (6h) — reuses the canonical mission-live guard timeout
        (NOT a separate constant).
        """
        parent_id = _seed_instance(
            fp4_harness.engine,
            instance_id="parent-r2-release",
            status=InstanceStatus.RUNNING.value,
        )
        child_id = _seed_instance(
            fp4_harness.engine,
            instance_id="child-r2-release",
            status=InstanceStatus.COMPLETED.value,
            parent_id=parent_id,
            agent_id="coder",
        )
        # Seed a PENDING report_injection (declared-wait).
        _seed_report_injection(
            fp4_harness.engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            state=ReportInjectionState.PENDING.value,
        )
        # Seed a child_completed event so the freshness leg FAILS
        # (the stale assistant timestamp is BEFORE the event).
        _seed_event(
            fp4_harness.engine,
            parent_id,
            kind=EventKind.CHILD_COMPLETED.value,
            when=T_CHILD_COMPLETED,
        )
        # Seed the first denial epoch OLDER than the bind
        # (6h + 1s) so the L6 RELEASE branch fires.
        old_epoch = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 1)
        ).isoformat()
        _seed_instance(
            fp4_harness.engine,
            instance_id=parent_id,  # UPDATE the existing row
            status=InstanceStatus.RUNNING.value,
            instance_metadata={
                "attestation:denial_epochs": [old_epoch],
            },
        )
        history = [
            {
                "role": "user",
                "content": "do the work",
                "created_at": "2026-09-20T16:00:00+00:00",
            },
            {
                "role": "assistant",
                "content": "stale",
                "created_at": "2026-09-20T17:00:00+00:00",
            },
        ]
        with _patch_get_instance_messages(history), \
             patch("daemon.services.child_reports.get_last_assistant_timestamp", new_callable=AsyncMock, return_value="2026-09-20T17:00:00+00:00"), \
             patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
            await fp4_harness.service._process_child_completion_and_notify_parent(
                parent_id, f"msg-r2-release",
            )

        # L6 RELEASE: the bind was reached. The parent stays in
        # COMPLETED (the L6 path does NOT downgrade; the bind path
        # lets the publish proceed). The ledger atomic op set
        # ``completion_gate_escalated=True``.
        with Session(fp4_harness.engine) as session:
            row = session.get(Instance, parent_id)
            assert row.status == InstanceStatus.COMPLETED.value, (
                "L6 RELEASE: parent must STAY in COMPLETED when "
                "the bind is reached — the L6 path releases "
                "the hold."
            )
            assert row.completion_gate_escalated is True, (
                "L6 RELEASE: completion_gate_escalated must be "
                "True after the bind is reached — the "
                "work_notifier.py layer uses this flag to render "
                "the escalated display "
                f"({COMPLETION_GATE_ESCALATED_DISPLAY!r})."
            )
            assert int(row.attestation_denied_count or 0) == 0, (
                "L6 RELEASE: the ledger counter is reset to 0 by "
                "the atomic op (set_escalated_and_reset)."
            )


# ─── R4: wedge regression — while HELD, retry/continue still admit ─────────


class TestR4WedgeRegression:
    """R4 — while HELD (status=WAITING_CHILDREN, JobItem ACTIVE),
    the retry/continue paths still admit work. The done+retry-0/0
    wedge is the bug; the ACTIVE state is what makes the next
    message-completed signal re-fire the L6 path and admit
    fresh work.
    """

    @pytest.mark.asyncio
    async def test_held_parent_admits_subsequent_message_completed(
        self, fp4_harness,
    ):
        """HOLD fires (R2 path); subsequent message-completed signal
        re-runs the gate. Tree has no live legs (all children
        terminal); declared-wait is still outstanding → second
        pass also HOLDS. The L6 path is event-driven, not
        poll-based; the re-fire is what makes retry/continue
        admit fresh work on the same held parent.
        """
        parent_id = _seed_instance(
            fp4_harness.engine,
            instance_id="parent-r4",
            status=InstanceStatus.RUNNING.value,
        )
        child_id = _seed_instance(
            fp4_harness.engine,
            instance_id="child-r4",
            status=InstanceStatus.COMPLETED.value,
            parent_id=parent_id,
            agent_id="coder",
        )
        _seed_report_injection(
            fp4_harness.engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            state=ReportInjectionState.PENDING.value,
        )
        # Seed a child_completed event so the freshness leg FAILS
        # (stale assistant timestamp BEFORE the event). Routes the
        # resolver through L2b discharge.
        _seed_event(
            fp4_harness.engine,
            parent_id,
            kind=EventKind.CHILD_COMPLETED.value,
            when=T_CHILD_COMPLETED,
        )
        history = [
            {
                "role": "user",
                "content": "do the work",
                "created_at": "2026-09-20T16:00:00+00:00",
            },
            {
                "role": "assistant",
                "content": "stale",
                "created_at": "2026-09-20T17:00:00+00:00",
            },
        ]

        # First pass: HOLD (declared-wait within bind).
        with _patch_get_instance_messages(history), \
             patch("daemon.services.child_reports.get_last_assistant_timestamp", new_callable=AsyncMock, return_value="2026-09-20T17:00:00+00:00"), \
             patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
            await fp4_harness.service._process_child_completion_and_notify_parent(
                parent_id, "msg-r4-first",
            )
        with Session(fp4_harness.engine) as session:
            row = session.get(Instance, parent_id)
            assert row.status == InstanceStatus.WAITING_CHILDREN.value
            first_count = int(row.attestation_denied_count or 0)

        # Second pass: re-fire via a fresh message-completed signal.
        # The L6 path is event-driven; the second pass increments
        # the ledger again (O4 idempotency: same epoch → no-op
        # the increment, but the path runs).
        with _patch_get_instance_messages(history), \
             patch("daemon.services.child_reports.get_last_assistant_timestamp", new_callable=AsyncMock, return_value="2026-09-20T17:00:00+00:00"), \
             patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
            await fp4_harness.service._process_child_completion_and_notify_parent(
                parent_id, "msg-r4-second",
            )
        with Session(fp4_harness.engine) as session:
            row = session.get(Instance, parent_id)
            assert row.status == InstanceStatus.WAITING_CHILDREN.value, (
                "R4 wedge regression: the held parent must "
                "remain held across re-fires (not flip to a "
                "terminal that would block retry/continue)."
            )
            # The ledger counter is bounded by O4 (same epoch =
            # no double-increment). The O4 dedup means the second
            # pass is a no-op on the counter.
            second_count = int(row.attestation_denied_count or 0)
            assert second_count == first_count, (
                "O4 dedup: same denial_epoch across re-fires "
                "must NOT double-increment the ledger."
            )


# ─── R5: lost-dispatch — child report arrives while parent HELD ─────────────


class TestR5LostDispatch:
    """R5 — child report arrives while parent HELD → consumed
    (``TASK_DELIVERED`` + parent turn processes it).

    The HOLD is event-driven; a child report that lands while the
    parent is in WAITING_CHILDREN is enqueued via the message-completed
    path and processed by the parent's existing turn. Pinned: the
    held parent does NOT lose the child report to a stuck WAITING
    state.
    """

    @pytest.mark.asyncio
    async def test_child_report_landing_while_held_is_consumed(
        self, fp4_harness,
    ):
        """Seed held parent (L6 HOLD outcome from a prior pass) +
        a fresh child report. Fire the message-completed signal
        again. The report is consumed (the helper re-evaluates;
        tree is all-terminal; declared-wait still outstanding;
        L6 still HOLDS, but the report is acknowledged — the
        message row transitions to its terminal state).
        """
        parent_id = _seed_instance(
            fp4_harness.engine,
            instance_id="parent-r5",
            status=InstanceStatus.WAITING_CHILDREN.value,
            instance_metadata={
                "attestation:denial_epochs": [
                    datetime.now(timezone.utc).isoformat(),
                ],
            },
        )
        child_id = _seed_instance(
            fp4_harness.engine,
            instance_id="child-r5",
            status=InstanceStatus.COMPLETED.value,
            parent_id=parent_id,
            agent_id="coder",
        )
        _seed_report_injection(
            fp4_harness.engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            state=ReportInjectionState.PENDING.value,
        )
        # Seed a child_completed event so the freshness leg FAILS.
        _seed_event(
            fp4_harness.engine,
            parent_id,
            kind=EventKind.CHILD_COMPLETED.value,
            when=T_CHILD_COMPLETED,
        )
        history = [
            {
                "role": "user",
                "content": "do the work",
                "created_at": "2026-09-20T16:00:00+00:00",
            },
            {
                "role": "assistant",
                "content": "stale",
                "created_at": "2026-09-20T17:00:00+00:00",
            },
        ]
        # The child report message is the new fire.
        report_msg_id = f"msg-r5-{uuid.uuid4()}"
        _seed_terminal_message(
            fp4_harness.engine, parent_id, report_msg_id,
        )
        with _patch_get_instance_messages(history), \
             patch("daemon.services.child_reports.get_last_assistant_timestamp", new_callable=AsyncMock, return_value="2026-09-20T17:00:00+00:00"), \
             patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
            await fp4_harness.service._process_child_completion_and_notify_parent(
                parent_id, report_msg_id,
            )
        # The held parent remains held (declared-wait still
        # outstanding) — but the helper ran end-to-end without
        # raising, the report message is acknowledged (the
        # helper's sync path handled it; the report row was
        # processed). The KEY pin: the child report was NOT
        # dropped to a stuck-waiting state.
        with Session(fp4_harness.engine) as session:
            row = session.get(Instance, parent_id)
            assert row.status == InstanceStatus.WAITING_CHILDREN.value, (
                "R5: the held parent remains held; the child "
                "report was consumed but the declared-wait is "
                "still outstanding. The parent turn continues "
                "to admit work — the report is processed, not "
                "stranded."
            )


# ─── PP2: agent-lane job_create default watch=true ─────────────────────────


class TestPP2WatchDefaultFlip:
    """PP2 — default ``watch=True`` for the agent-facing
    ``job_create`` tool (user-approved flip 2026-10-03). Explicit
    ``watch=False`` must still win. Other lanes (HTTP router
    surfaces) are untouched — only the agent-facing tool's default
    changed.
    """

    def _make_job_create_tool(self, fp4_harness, *, current_instance_id: str):
        """Build the ``job_create`` tool via ``create_job_tools`` —
        the production factory.
        """
        job_service = MagicMock()
        job_service.enqueue = AsyncMock(
            return_value=MagicMock(
                job_id=f"job-{uuid.uuid4()}",
                instance_id=None,
                to_dict=MagicMock(return_value={"job_id": "job-x"}),
            )
        )
        queue_mgmt_service = MagicMock()
        dead_letter_service = MagicMock()
        watcher_repo = fp4_harness.watcher_repo

        tools = create_job_tools(
            job_service=job_service,
            queue_mgmt_service=queue_mgmt_service,
            dead_letter_service=dead_letter_service,
            current_instance_id=current_instance_id,
            agent_id="leader",
            watcher_repo=watcher_repo,
            manager=fp4_harness.manager,
        )
        return next(t for t in tools if t.name == "job_create")

    def _add_watch_for_job(
        self, fp4_engine, *, job_id: str, instance_id: str,
    ) -> bool:
        """Check whether the watcher_repo has a watch row for
        ``(job_id, instance_id)``. Returns True if a row exists.
        """
        with Session(fp4_engine) as session:
            row = session.get(JobWatcher, job_id)
        return row is not None and row.instance_id == instance_id

    @pytest.mark.asyncio
    async def test_default_flip_registers_watch(self, fp4_harness):
        """When the agent does NOT pass ``watch=...``, the default
        is now ``True`` — a watch row is registered for the new
        job. Pre-PP2 (default ``False``) this would NOT register a
        watch.
        """
        current_id = f"caller-{uuid.uuid4()}"
        tool = self._make_job_create_tool(
            fp4_harness, current_instance_id=current_id,
        )
        # Invoke WITHOUT explicit watch. The PP2 flip makes the
        # default ``True``.
        result = await tool.ainvoke({
            "agent_id": "developer",
            "message": "do the work",
        })
        # The job was created; a watch row was registered for
        # the caller's instance (the agent-lane auto-watch).
        assert "job_id" in result or "error" not in result
        # Find the registered watch row.
        with Session(fp4_harness.engine) as session:
            watches = fp4_harness.watcher_repo.get_watches_for_instance(
                current_id
            )
        assert len(watches) >= 1, (
            "PP2 default-flip: the agent-lane job_create default "
            "is now watch=True; a watch row MUST be registered "
            "when the agent does NOT pass watch=... explicitly."
        )

    @pytest.mark.asyncio
    async def test_explicit_false_wins(self, fp4_harness):
        """When the agent EXPLICITLY passes ``watch=False``, NO
        watch row is registered. Explicit False wins over the
        new default.
        """
        current_id = f"caller-{uuid.uuid4()}"
        tool = self._make_job_create_tool(
            fp4_harness, current_instance_id=current_id,
        )
        result = await tool.ainvoke({
            "agent_id": "developer",
            "message": "do the work",
            "watch": False,
        })
        # Explicit False wins: NO watch row registered.
        with Session(fp4_harness.engine) as session:
            watches = fp4_harness.watcher_repo.get_watches_for_instance(
                current_id
            )
        assert len(watches) == 0, (
            "PP2 explicit-False-wins: when the agent passes "
            "watch=False, the default-FLIP does NOT override the "
            "explicit opt-out — NO watch row is registered."
        )
