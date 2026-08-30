"""W5 two-turn claim-order semantics + S9 terminal-after-turn-1 edge (T9).

wc-wake-report-integrity (phase1-plan §6-T9; decisions.md W5 ACCEPTED,
C1-Q2 flag). With WC→enqueue, a user message and a child-completion
report targeting the same parked parent BOTH become claimable Task
rows. ``claim_pending_task`` picks strictly by ``ORDER BY created_at
ASC LIMIT 1`` (``daemon/repositories/task/repository.py:1486``) and the
per-instance single-RUNNING guard serializes execution: whichever row
was created first claims first, the other runs as a SECOND turn. That
replaces the pre-wc-wake single-turn behavior (the user message was
absorbed INTO the report turn via FIFO injection).

Three pins live in this file:

1. **Two-turn claim order (W5)** — under ``ENSEMBLE_WC_WAKE_ENQUEUE=1``
   semantics, a report Task and a user-msg Task on a WAITING_CHILDREN
   parent are both claimable and ``created_at ASC`` decides the order;
   the loser is claimed on the NEXT pass (second turn after the first
   completes). Also pins the reverse order (user first → report
   second).

2. **FIFO-leftover single-turn invariant (the W5 deliberately-does-NOT-
   extend case, phase1-plan §6-T9 last paragraph)** — PRE-EXISTING
   parked-FIFO leftovers still drain INTO the wake turn's graph input
   (T5/D2 seam drain: leftovers + new user message = ONE astream call,
   leftovers oldest-first BEFORE the user message, markers preserved).
   This is the S4 input-order positional pin as well — the prior T5
   pass landed the drain code without its mandated tests.

3. **S9 — terminal-after-turn-1** (reconciliation-pass addition): the
   claim pause gate excludes ONLY PAUSED/TERMINATED instances
   (``task/repository.py:1414-1428``) — a queued user-msg Task still
   CLAIMS on a parent that went COMPLETED after turn 1. The
   enqueue-side twin (terminal-revive in ``_prepare_enqueued_message``,
   ``instance_messaging.py:1527-1545``) reactivates the COMPLETED
   parent at send time. Together: terminal-after-enqueue never
   silently strands the row.

**S13 cross-ref** (plan §6-T9 additions): the caller-facing experience
of the two-turn window — a WC target that already has a queued wake is
BUSY — is pinned verbatim by
``tests/unit/tools/test_instance_tools.py::TestWaitingChildrenQueueBusyGuard``
and the ``job_inject`` twin
``tests/unit/tools/test_job_visibility_tools.py::test_job_inject_waiting_children_flag_on_busy``
(see also ``test_load_skill_keeps_queue_busy_guard``). Read the
busy-gate contract and the claim-order contract in this file together:
the gate is what the agent sees while the second turn is pending.

All repository-level tests use a real in-memory SQLite engine
(StaticPool) — no schema or migration surface is touched.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


# ---------------------------------------------------------------------------
# Fixtures / seed helpers
# ---------------------------------------------------------------------------


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


def _seed_instance(
    engine: Engine,
    *,
    status: str = InstanceStatus.WAITING_CHILDREN.value,
    agent_id: str = "developer",
) -> str:
    """Insert an ``Instance`` row. Returns the ``instance_id``."""
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            parent_id=None,
            project_id="test-project",
            status=status,
            created_at=now_iso,
            updated_at=now_iso,
            paused_at=None,
        )
        s.add(inst)
        s.commit()
    return iid


def _seed_task(
    engine: Engine,
    *,
    instance_id: str,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    created_at: datetime | None = None,
) -> int:
    """Insert a PENDING ``Task`` row with an explicit created_at."""
    now = created_at or datetime.now(timezone.utc)
    with Session(engine) as s:
        task = Task(
            task_type=task_type,
            instance_id=instance_id,
            status=TaskStatus.PENDING.value,
            created_at=now,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return int(task.id)


def _set_instance_status(engine: Engine, instance_id: str, status: str) -> None:
    with Session(engine) as s:
        inst = s.get(Instance, instance_id)
        assert inst is not None
        inst.status = status
        s.add(inst)
        s.commit()


# ---------------------------------------------------------------------------
# 1. W5 — two-turn claim-order race (flag-ON semantics)
# ---------------------------------------------------------------------------


class TestW5TwoTurnClaimOrder:
    """User-msg and child-report rows on a WC parent: created_at ASC decides.

    Both rows are claimable — WAITING_CHILDREN is not in the claim pause
    gate (only PAUSED/TERMINATED are). The per-instance single-RUNNING
    guard serializes the two turns: the first claim flips its row to
    RUNNING, the second row becomes claimable only after the first
    completes — i.e. the second claim pass represents the SECOND turn.
    """

    def test_report_first_created_claimed_first_user_msg_second_turn(
        self, engine: Engine
    ):
        """Report row created before the user-msg row → report turn 1,
        user-message turn 2 (both claimable, ASC order)."""
        iid = _seed_instance(engine, status=InstanceStatus.WAITING_CHILDREN.value)
        base = datetime.now(timezone.utc)
        report_task_id = _seed_task(
            engine,
            instance_id=iid,
            task_type=TaskType.PROCESS_REPORT.value,
            created_at=base,
        )
        user_msg_task_id = _seed_task(
            engine,
            instance_id=iid,
            task_type=TaskType.PROCESS_MESSAGE.value,
            created_at=base + timedelta(seconds=1),
        )

        repo = TaskRepository(engine)

        # Turn 1: the OLDER row (the report) claims first.
        first = repo.claim_pending_task(worker_id="worker-1")
        assert first is not None
        assert first.id == report_task_id, (
            "W5: the row created FIRST must claim first — "
            "created_at ASC is the sole ordering authority"
        )
        assert first.status == TaskStatus.RUNNING.value

        # While turn 1 is RUNNING, the per-instance single-RUNNING guard
        # holds the user-msg row (serialization, not stranding).
        blocked = repo.claim_pending_task(worker_id="worker-2")
        assert blocked is None, (
            "the second row must NOT claim while the first turn is "
            "RUNNING (single-RUNNING-per-instance guard)"
        )

        # Turn 1 completes → the user-msg row becomes claimable and runs
        # as the SECOND turn.
        repo.complete_task(report_task_id, result={"summary": "report delivered"})
        second = repo.claim_pending_task(worker_id="worker-1")
        assert second is not None
        assert second.id == user_msg_task_id, (
            "W5: the user message runs as a SECOND turn after the report "
            "turn completes — not absorbed into the report turn"
        )

    def test_user_msg_first_created_claimed_first_report_second_turn(
        self, engine: Engine
    ):
        """Inverse order: the user-msg row created first claims first —
        the race is symmetric, purely created_at-driven."""
        iid = _seed_instance(engine, status=InstanceStatus.WAITING_CHILDREN.value)
        base = datetime.now(timezone.utc)
        user_msg_task_id = _seed_task(
            engine,
            instance_id=iid,
            task_type=TaskType.PROCESS_MESSAGE.value,
            created_at=base,
        )
        report_task_id = _seed_task(
            engine,
            instance_id=iid,
            task_type=TaskType.PROCESS_REPORT.value,
            created_at=base + timedelta(seconds=1),
        )

        repo = TaskRepository(engine)

        first = repo.claim_pending_task(worker_id="worker-1")
        assert first is not None and first.id == user_msg_task_id

        repo.complete_task(user_msg_task_id, result={})
        second = repo.claim_pending_task(worker_id="worker-1")
        assert second is not None and second.id == report_task_id, (
            "W5: whichever row was created first claims first — the race "
            "is symmetric and order is not type-biased"
        )


# ---------------------------------------------------------------------------
# 2. W5 deliberately-not-extended invariant — FIFO leftovers = ONE turn
# ---------------------------------------------------------------------------


def _make_capture_service(leftovers: list[dict]):
    """Build the messaging service + capture-graph pair for the drain test.

    The fake graph records the ``graph_input`` it is astream'd with and
    yields an empty stream (turn ends immediately). The manager's FIFO
    API is backed by the given ``leftovers`` snapshot, mirroring a
    pre-existing parked injection (T5/D2 seam-drain input).
    """
    from daemon.services.cancellation import CancellationService
    from daemon.services.instance_messaging import InstanceMessagingService

    captured: dict = {"astream_calls": 0, "graph_input": None}

    manager = MagicMock()
    manager._graph_tasks = {}
    manager._deferred_question_pause = set()
    manager.get_injection = MagicMock(return_value=leftovers)
    manager.clear_injection = MagicMock(return_value=list(leftovers))
    manager.requeue_injections = MagicMock()

    graph = MagicMock()
    graph.language_check_active = False
    # Healthy checkpoint tail → D1 seam synthesizes nothing.
    graph.aget_state = AsyncMock(return_value=None)

    async def _astream(graph_input, _config, stream_mode=None):
        captured["astream_calls"] += 1
        captured["graph_input"] = graph_input
        return
        yield  # pragma: no cover

    graph.astream = _astream
    manager.get_instance = AsyncMock(return_value=graph)

    service = InstanceMessagingService(
        manager=manager,
        cancellation_service=CancellationService(manager=manager),
    )
    service._maybe_compact_context = AsyncMock()  # type: ignore[method-assign]
    service._maybe_trigger_title_generation = MagicMock()  # type: ignore[method-assign]
    service._has_checkpoint = AsyncMock(return_value=False)  # type: ignore[method-assign]
    service._emit_context_usage = AsyncMock()  # type: ignore[method-assign]

    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._live_hub.stream_tool_result = AsyncMock()
    manager._live_hub.stream_error = AsyncMock()
    manager._llm_semaphore = asyncio.Semaphore()
    manager.config = MagicMock()
    manager.config.limits.graph_recursion_limit = 25
    manager._queue_repository = MagicMock()
    manager._queue_repository.update_activity = MagicMock()
    manager._compactor = None
    manager.source_dispatcher = None
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    manager._instance_repository.set_metadata = MagicMock()
    manager._project_repository = MagicMock()
    manager.shared_meta_kv_repo = MagicMock()
    manager._skill_injection_service = None
    manager._skill_clone_service = None
    manager._skill_metrics_service = None

    return service, manager, captured, graph


class TestW5FifoLeftoverSingleTurn:
    """PRE-EXISTING parked-FIFO leftovers still land in ONE wake turn.

    W5 deliberately does NOT extend the two-turn semantics to the T5
    FIFO-leftover drain: leftovers + the new user message compose a
    single ``graph.astream`` input,
    ``[leftovers oldest-first] + [user_message]`` (the S4 order pin).
    """

    @pytest.mark.asyncio
    async def test_leftovers_and_new_message_yield_single_turn_in_order(self):
        """Two pre-existing leftovers + new message → ONE astream call,
        order [left-1, left-2, user]; markers preserved; FIFO drained."""
        leftovers = [
            {"content": "left-1", "timestamp": "t1"},
            {"content": "left-2", "source": "internal_agent:child-x", "timestamp": "t2"},
        ]
        service, manager, captured, _graph = _make_capture_service(leftovers)

        result = await service._process_message_with_tracking(
            instance_id="iid-wc",
            message="new wake message",
            message_id="msg-new",
            is_retry=True,
            silent=False,
        )
        assert result is not None

        # SINGLE turn — the leftover drain must not fork a second turn.
        assert captured["astream_calls"] == 1, (
            "T5/D2 invariant (W5 does not extend): pre-existing FIFO "
            "leftovers ride the SAME wake turn as the new message"
        )

        messages = captured["graph_input"]["messages"]
        contents = [
            m.content if isinstance(m.content, str) else str(m.content)
            for m in messages
        ]
        assert contents == ["left-1", "left-2", "new wake message"], (
            f"S4 order pin violated: {contents}"
        )

        # Leftovers ARE injections — the marker (+ optional source) is
        # preserved so C3 compaction and the D12 subtree filter keep
        # recognising them.
        left1, left2, user = messages
        assert left1.additional_kwargs.get("injected_message") is True
        assert "source" not in left1.additional_kwargs
        assert left2.additional_kwargs.get("injected_message") is True
        assert left2.additional_kwargs.get("source") == "internal_agent:child-x"
        # The wake user message is a first-class turn message — unmarked.
        assert not user.additional_kwargs.get("injected_message")

        # The FIFO was drained exactly once for the turn.
        manager.clear_injection.assert_called_once_with("iid-wc")


# ---------------------------------------------------------------------------
# 3. S9 — terminal-after-turn-1: queued Task claims on a COMPLETED parent
# ---------------------------------------------------------------------------


class TestS9TerminalAfterTurn1:
    """The claim pause gate excludes ONLY PAUSED/TERMINATED — a queued
    user-msg Task still CLAIMS on a parent that went COMPLETED after
    turn 1 (repo-level), and the enqueue-side terminal-revive
    reactivates a COMPLETED parent at send time (service-level).
    """

    @pytest.mark.parametrize("terminal_status", [
        InstanceStatus.COMPLETED.value,
        InstanceStatus.ERROR.value,
        InstanceStatus.FAILED.value,
    ])
    def test_queued_task_claims_on_terminal_parent(self, engine: Engine, terminal_status):
        """Queued PROCESS_MESSAGE + parent COMPLETED/ERROR/FAILED →
        claim succeeds (terminal is NOT in the claim pause gate)."""
        iid = _seed_instance(engine, status=terminal_status)
        task_id = _seed_task(engine, instance_id=iid)

        repo = TaskRepository(engine)
        claimed = repo.claim_pending_task(worker_id="worker-1")

        assert claimed is not None, (
            f"S9: a queued user-msg Task must still claim on a "
            f"{terminal_status} parent — terminal-after-enqueue must not "
            f"strand the row"
        )
        assert claimed.id == task_id
        assert claimed.status == TaskStatus.RUNNING.value

    @pytest.mark.parametrize("gated_status", [
        InstanceStatus.PAUSED.value,
        InstanceStatus.TERMINATED.value,
    ])
    def test_claim_pause_gate_excludes_only_paused_and_terminated(
        self, engine: Engine, gated_status
    ):
        """Control for the S9 pin: the gate blocks exactly PAUSED and
        TERMINATED — proving the COMPLETED claim above is the gate's
        designed narrowness, not a missing gate."""
        iid = _seed_instance(engine, status=gated_status)
        _seed_task(engine, instance_id=iid)

        repo = TaskRepository(engine)
        claimed = repo.claim_pending_task(worker_id="worker-1")
        assert claimed is None, (
            f"{gated_status} parents ARE excluded by the claim pause gate"
        )

    @pytest.mark.asyncio
    async def test_enqueue_on_completed_parent_revives_to_running(self):
        """Terminal-revive twin (``_prepare_enqueued_message``): a wake
        enqueued against a COMPLETED parent flips it to RUNNING in the
        same transaction that writes the Task row."""
        from contextlib import contextmanager
        from unittest.mock import patch

        from daemon.repositories.instance.models import InstanceStatus as IS
        from daemon.services.instance_messaging import InstanceMessagingService

        class _FakeInstanceRow:
            """Minimal Instance stand-in with a settable status."""

            def __init__(self, status: str):
                self.status = status
                self.agent_id = "developer"
                self.instance_metadata = {}
                self.version = 1
                self.paused_at = None
                self.last_activity_at = None

        row = _FakeInstanceRow(IS.COMPLETED.value)  # went terminal after turn 1

        manager = MagicMock()
        manager._graph_tasks = {}
        manager._deferred_question_pause = set()
        manager._worker_pool = None
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._generate_and_broadcast_title = AsyncMock()
        manager._job_queue_service = None

        cancellation = MagicMock()
        cancellation.is_shutting_down = False

        service = InstanceMessagingService(
            manager=manager, cancellation_service=cancellation
        )

        mock_session = MagicMock()
        mock_session.get.return_value = row

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        with patch("daemon.services.instance_messaging.Session", return_value=mock_session_ctx()), \
             patch("daemon.services.instance_messaging.MainLoopBridge"), \
             patch("daemon.services.instance_messaging.Instance"), \
             patch("daemon.services.instance_messaging.MessageQueue"), \
             patch("daemon.services.instance_messaging.Task"), \
             patch("daemon.services.instance_messaging.Event"):
            ctx = service._prepare_enqueued_message(
                instance_id="iid-completed",
                message="wake",
                source="api",
                priority=1,
                images=None,
                metadata=None,
            )

        assert ctx.status_changed_to_running is True, (
            "S9: enqueue against a COMPLETED parent must flip it to "
            "RUNNING (terminal-revive) in the enqueue transaction"
        )
        assert ctx.previous_status == IS.COMPLETED.value
        assert row.status == IS.RUNNING.value, (
            "the row's status must have been written back as RUNNING"
        )
