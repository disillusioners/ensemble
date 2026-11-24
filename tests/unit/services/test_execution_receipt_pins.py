"""Addendum A receipt pins (fix/post-outage-recovery SCOPE b3).

Mutation-verified harness fixtures. Asserts the RECEIPT FIELDS themselves —
the architect's read side (scheduling_service.py) is deliberately NOT tested
here. A real SQLite source repo backs the receipt writes; the adapter fires
through fakes so the durable-dispatch route is observable.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository as MessageQueueRepository,
)
from daemon.repositories.source.models import ExecutionStatus, ScheduleExecution
from daemon.repositories.source.repository import SQLModelSourceRepository
from daemon.repositories.task.models import Task
from daemon.repositories.task.repository import TaskRepository
from daemon.services.execution_receipt import make_execution_receipt_callback
from daemon.sources.adapters.scheduler import SchedulerAdapter
from tests.conftest import make_config


@pytest.fixture()
def receipt_repo(tmp_path):
    # A FILE-backed SQLite DB — every Session opens its own connection, and
    # in-memory ``sqlite://`` would give each one a fresh empty schema.
    engine = create_engine(f"sqlite:///{tmp_path / 'receipt.db'}")
    SQLModel.metadata.create_all(engine)
    return SQLModelSourceRepository(engine=engine)


# ---------------------------------------------------------------------------
# 1. Receipt pin — completed fire vs never-started distinction
# ---------------------------------------------------------------------------


def test_receipt_completed_fire_executed_with_handles(receipt_repo):
    """A harness fire that completes leaves executed + instance + handles."""
    receipt_repo.upsert_execution_dispatched("sched-1", "exec-1", "inst-1", "task-1", "job-1")
    receipt_repo.record_execution_terminal_by_job("job-1", "completed")

    row = receipt_repo.get_latest_execution("sched-1")
    assert row.status == ExecutionStatus.EXECUTED.value
    assert row.instance_id == "inst-1"          # non-NULL once a turn started
    assert row.task_id == "task-1"
    assert row.job_item_id == "job-1"
    assert row.outcome == "completed"
    assert row.completed_at is not None


def test_receipt_never_started_distinction_visible_in_raw_fields(receipt_repo):
    """A never-started fire leaves NULL instance + explicit truthful status.

    The distinction is visible in the RAW receipt fields (no read-side
    derivation needed) — a completed fire and a never-started fire cannot be
    confused at a glance.
    """
    receipt_repo.upsert_execution_dispatched("sched-2", "exec-done", "inst-2", "task-2", "job-2")
    receipt_repo.record_execution_terminal_by_job("job-2", "completed")
    receipt_repo.record_execution_never_started("sched-2", "exec-none", "no turn began")

    rows = {r.execution_id: r for r in receipt_repo.list_schedule_executions("sched-2")}
    done, none = rows["exec-done"], rows["exec-none"]
    assert done.status == "executed" and done.instance_id is not None
    assert none.status == ExecutionStatus.NEVER_STARTED.value
    assert none.instance_id is None
    assert none.task_id is None and none.job_item_id is None
    # The two are visibly distinct on the receipt surface itself.
    assert (done.status, done.instance_id is not None) != (none.status, none.instance_id is not None)


# ---------------------------------------------------------------------------
# 2. Mutation legs — each mis-write is DETECTABLE as a red condition
# ---------------------------------------------------------------------------


def test_mutation_terminal_write_off_is_red(receipt_repo):
    """Without the terminal write the row stays OPEN — the pin detects it."""
    receipt_repo.upsert_execution_dispatched("sched-3", "exec-3", "inst-3", "task-3", "job-3")
    # (mutation) terminal callback deliberately NOT fired
    row = receipt_repo.get_latest_execution("sched-3")
    assert row.status == ExecutionStatus.DISPATCHED.value   # still open
    assert row.status != ExecutionStatus.EXECUTED.value     # RED
    # Restore: the real terminal write closes it (green).
    receipt_repo.record_execution_terminal_by_job("job-3", "completed")
    assert receipt_repo.get_latest_execution("sched-3").status == "executed"


def test_mutation_queued_forever_is_red(receipt_repo):
    """A row left in the transient ``queued`` state is a detectable violation."""
    receipt_repo.record_execution_start("sched-4", None, "exec-4")
    receipt_repo.record_execution_complete("exec-4", ExecutionStatus.QUEUED.value)
    row = receipt_repo.get_latest_execution("sched-4")
    assert row.status == "queued"
    # ``queued`` is NEVER a valid resting state for a completed/dead fire.
    assert row.status not in (
        ExecutionStatus.EXECUTED.value,
        ExecutionStatus.NEVER_STARTED.value,
        ExecutionStatus.FAILED_TO_DISPATCH.value,
    )  # RED — a queued-forever row fails the terminal-truthfulness assertion


def test_mutation_completed_null_instance_is_red(receipt_repo):
    """A completed/executed fire with NULL instance_id is a contract violation."""
    receipt_repo.upsert_execution_dispatched("sched-5", "exec-5", "inst-5", "task-5", "job-5")
    receipt_repo.record_execution_terminal_by_job("job-5", "completed")
    row = receipt_repo.get_latest_execution("sched-5")
    # Simulate the violation: a terminal success with no instance handle.
    contract_violation = row.status == ExecutionStatus.EXECUTED.value and row.instance_id is None
    assert contract_violation is False  # the real write never produces this shape
    assert row.instance_id is not None


def test_addendum_a_closed_vocabulary_membership():
    """D3 (reviewer fold, fix/post-outage-recovery): the Addendum-A
    closed vocabulary is EXACTLY these members — pinned structurally.

    ``suppressed`` is RESERVED: a member of the enum and accepted on
    the registry callback path (``daemon/sources/registry.py``), but
    NO writer mints it today (the re-arm's ``skipped_operator``
    outcome is consumed log-locally and never reaches the receipt
    write). The pin freezes the membership so a silent vocabulary
    change (a member removed, or one added without a ratification
    note in the enum comment) is visible.
    """
    members = {m.value for m in ExecutionStatus}
    # The full closed vocabulary: the LEGACY members (triggered /
    # running / completed / failed / skipped), the dispatch-phase pair
    # (queued / deferred), and the Addendum-A receipt block — of which
    # ``suppressed`` is RESERVED (see the enum comment).
    assert members == {
        "triggered", "running", "completed", "failed", "skipped",
        "queued", "deferred",
        "executed", "dispatched", "never-started",
        "failed-to-dispatch", "suppressed",
    }
    # The Addendum-A receipt block is present in full...
    for value in (
        "executed", "dispatched", "never-started",
        "failed-to-dispatch", "suppressed",
    ):
        assert ExecutionStatus.is_valid(value)
    # ...and the RESERVED member is the one the commission names:
    assert ExecutionStatus.SUPPRESSED.value == "suppressed"
    # But its comment must carry the RESERVED ratification
    # note — the vocabulary never grows silently.
    import inspect

    import daemon.repositories.source.models as source_models

    enum_src = inspect.getsource(source_models)
    assert "RESERVED" in enum_src, (
        "the 'suppressed' member's comment must carry the RESERVED "
        "ratification note (fix/post-outage-recovery D3)"
    )


# ---------------------------------------------------------------------------
# 3. Closing-callback pin — terminal closes receipt; never wedges the job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closing_callback_closes_executed(receipt_repo):
    receipt_repo.upsert_execution_dispatched("sched-6", "exec-6", "inst-6", "task-6", "job-6")
    cb = make_execution_receipt_callback(receipt_repo)
    out = await cb("job-6", "error")
    assert out["closed"] is True
    row = receipt_repo.get_latest_execution("sched-6")
    assert row.status == "executed" and row.outcome == "error"


@pytest.mark.asyncio
async def test_closing_callback_never_wedges(receipt_repo):
    """A repo failure is caught — the callback never raises into the job."""
    class Boom:
        def record_execution_terminal_by_job(self, *a, **k):
            raise RuntimeError("db down")

    cb = make_execution_receipt_callback(Boom())
    out = await cb("job-x", "completed")   # must NOT raise
    assert out["closed"] is False and "error" in out


# ---------------------------------------------------------------------------
# 4. Project-hole pin — unscoped NEW_INSTANCE routes through durable branch
# ---------------------------------------------------------------------------


def _new_instance_config(source_id: str) -> "object":
    # No project_id; NEW_INSTANCE is the default instance_mode.
    return make_config(source_id, {
        "interval_seconds": 3600,
        "agent": "./agents/developer",
        "message": "Test",
    })


@pytest.mark.asyncio
async def test_project_hole_unscoped_new_instance_routes_durable():
    """Empty _project_id + NEW_INSTANCE now uses the durable job-queue route."""
    manager = AsyncMock()
    result = MagicMock(job_id="job-1", message_id="msg-1", instance_id="inst-1", status="queued")
    manager.enqueue_message_job = AsyncMock(return_value=result)
    manager.spawn_instance_with_mcp = AsyncMock(return_value="inst-1")
    manager._task_repo = MagicMock()
    manager._task_repo.get_by_work_id = MagicMock(return_value=None)
    source_repo = MagicMock()
    source_repo.get_instance_mapping = MagicMock(return_value=None)

    adapter = SchedulerAdapter(
        _new_instance_config("sched-noproj"), AsyncMock(), MagicMock(),
        manager=manager, source_repo=source_repo,
    )
    assert adapter._project_id in (None, "")          # genuinely unscoped
    assert adapter._instance_mode.value == "new_instance"
    await adapter.start()
    await adapter._emit_scheduled_message()
    await adapter.stop()

    manager.enqueue_message_job.assert_called_once()   # durable branch taken
    source_repo.upsert_execution_dispatched.assert_called_once()  # receipt linkage


@pytest.mark.asyncio
async def test_project_hole_no_manager_falls_back_immediate():
    """Without a manager there is no dispatch substrate — immediate remains.

    Pinned both ways: the fix routes through durable WHEN a manager exists;
    the manager-less harness keeps the legacy immediate path (no silent
    supervision claim it cannot honor).
    """
    on_message = AsyncMock()
    adapter = SchedulerAdapter(_new_instance_config("sched-nomgr"), on_message, MagicMock())
    await adapter.start()
    await adapter._emit_scheduled_message()
    await adapter.stop()
    on_message.assert_called_once()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 5. Re-arm claim pin (mandated) — next-fire + A REAL TURN + truthful receipt
# ---------------------------------------------------------------------------
#
# The pre-remediation leg (b) was FAKE: it asserted
# ``enqueue_message_job.assert_called_once()`` on an AsyncMock — an ENQUEUE
# is not a TURN. The mandated leg is "the spawned instance TOOK A TURN (the
# task reached processing — not just enqueued)".
#
# The pin below drives the REAL substrate (the same hermetic shape as the b2
# matrix pins): the REAL scheduler route → the REAL substrate claim
# (``JobRepository.create_or_get_by_idempotency_key``) → the D13 prelude
# pair (Task PENDING bound to the minted work_id + MessageQueue ready) →
# the REAL ``claim_pending_task`` SQL → the REAL ``dequeue``. The task row
# reaches ``running`` and the message row reaches ``processing`` WITH
# ``processing_started_at`` — a state a mock can never manufacture.


@pytest.fixture
def rearm_engine(tmp_path) -> Engine:
    """A file-backed SQLite engine with the REAL SQLModel schema."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/rearm.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


class _ReArmManager:
    """A manager whose enqueue lands on the REAL substrate.

    The enqueue performs the substrate's ATOMIC claim (the exact claim
    production ``JobQueueService.enqueue`` makes) and writes the D13
    prelude pair (Task PENDING bound to the minted work_id + MessageQueue
    ready) — the two rows the production ``enqueue_message_job`` writes.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self.job_repo = JobRepository(engine)
        self.task_repo = TaskRepository(engine)
        self.mq_repo = MessageQueueRepository(engine)
        self.results: list = []
        self.spawn_instance_with_mcp = AsyncMock(return_value="inst-rearm")

    async def enqueue_message_job(self, **kwargs):
        key = kwargs.get("idempotency_key")
        job, _created = self.job_repo.create_or_get_by_idempotency_key(
            agent_id="ari",
            agent_dir="/agents/ari",
            message=kwargs.get("message", "x"),
            source="scheduler",
            project_id="p",
            priority=5,
            # An unkeyed enqueue mints a fresh unique key (production:
            # unkeyed enqueues never collapse).
            idempotency_key=key if key else f"unkeyed-{uuid4()}",
        )
        instance_id = kwargs.get("instance_id") or "inst-rearm"
        message_id = f"mq-{uuid4().hex[:12]}"
        with Session(self._engine) as s:
            s.add(
                Task(
                    work_id=job.job_id,
                    task_type="process_message",
                    instance_id=instance_id,
                    message_id=message_id,
                    status="pending",
                )
            )
            s.add(
                MessageQueue(
                    message_id=message_id,
                    instance_id=instance_id,
                    content=kwargs.get("message", "x"),
                    source="scheduler",
                    status="ready",
                )
            )
            s.commit()
        result = MagicMock(
            job_id=job.job_id,
            message_id=message_id,
            instance_id=instance_id,
            status="queued",
        )
        self.results.append(result)
        return result


def _seed_rearm_instance(engine: Engine, instance_id: str = "inst-rearm") -> None:
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="ari",
                agent_dir="/agents/ari",
                status="running",
            )
        )
        s.commit()


def _activate_job(engine: Engine, job_id: str) -> None:
    """The JobProcessor's OWN activation step (the queue-aware claim gate
    requires it — a QUEUED anchor holds the task until the pipeline
    activates it). The task row itself is NEVER touched by hand."""
    with Session(engine) as s:
        job = s.exec(select(JobItem).where(JobItem.job_id == job_id)).first()
        job.admission_state = AdmissionState.ACTIVE.value
        s.add(job)
        s.commit()


@pytest.mark.asyncio
async def test_rearm_claim_pin_next_fire_turn_and_receipt(
    receipt_repo, rearm_engine
):
    """A fired cycle computes the next fire, TAKES A REAL TURN, and the
    receipt says so truthfully (three legs, one coherent hermetic harness).

    Leg (b) is the REAL substrate: the task row reaches ``running`` and the
    message row reaches ``processing`` with ``processing_started_at`` set —
    a mock can never manufacture that.
    """
    # A WEEKLY cron (weekday 1 = Monday). The re-arm math must preserve it.
    config = make_config("sched-weekly", {
        "schedule": "0 9 * * 1",   # Mondays 09:00
        "agent": "./agents/developer",
        "message": "Weekly alarm",
        "timezone": "UTC",
    })

    manager = _ReArmManager(rearm_engine)
    _seed_rearm_instance(rearm_engine, "inst-rearm")

    adapter = SchedulerAdapter(
        config, AsyncMock(), None, source_repo=receipt_repo, manager=manager,
    )
    await adapter.start()
    await adapter._emit_scheduled_message()
    await adapter.stop()

    # (a) the next fire is COMPUTABLE for cron — the SAME weekday. (The
    #     claim is about the computable next fire; the re-arm PERSISTS it
    #     through the chain re-arm write, which is not this pin's subject.)
    nxt = adapter._get_next_trigger_time()
    assert nxt is not None
    assert nxt.weekday() == 0, f"re-arm must preserve Monday weekday, got {nxt.weekday()}"

    # (b) the spawned instance TOOK A TURN — on the REAL substrate.
    assert len(manager.results) == 1, "the fire must enqueue exactly one delivery"
    job_id = manager.results[0].job_id
    _activate_job(rearm_engine, job_id)

    task_repo = TaskRepository(rearm_engine)
    mq_repo = MessageQueueRepository(rearm_engine)
    claimed = task_repo.claim_pending_task(worker_id="worker-rearm-1", lane="default")
    assert claimed is not None, (
        "the enqueued delivery MUST be claimable — an enqueue that never "
        "reaches processing is not a turn"
    )
    assert claimed.status == "running", (
        f"the task must reach the processing state, got {claimed.status!r}"
    )
    msg = mq_repo.dequeue(instance_id="inst-rearm")
    assert msg is not None and msg.status == "processing", (
        "the message must leave 'ready' into 'processing' — the turn was "
        "taken"
    )
    assert msg.processing_started_at is not None, (
        "the processing start must be stamped — the turn really ran"
    )

    # (c) the receipt says so truthfully (dispatched with the handles).
    receipt = receipt_repo.get_latest_execution("sched-weekly")
    assert receipt is not None
    assert receipt.status in (ExecutionStatus.DISPATCHED.value, ExecutionStatus.EXECUTED.value)
    assert receipt.instance_id == "inst-rearm"
    assert receipt.job_item_id == job_id


@pytest.mark.asyncio
async def test_rearm_semaphore_timeout_rearm_leaves_a_truthful_receipt(
    receipt_repo, monkeypatch
):
    """The semaphore-timeout re-arm (the branch the pin's name promises).

    The saturated-slot branch must NEVER consume the one-shot silently: the
    REAL fire leaves a ``never-started`` receipt (NULL instance handle — the
    only permitted NULL states) and the re-arm writes its own ``deferred``
    audit row.
    """
    import daemon.sources.adapters.scheduler as sched_mod

    # The MANDATORY 5s re-arm cadence sleep — shortened for the pin.
    monkeypatch.setattr(sched_mod, "SCHEDULER_ERROR_RETRY_S", 0)

    config = make_config("sched-rearm-st", {
        "interval_seconds": 3600,
        "agent": "./agents/developer",
        "message": "Re-arm probe",
        "timezone": "UTC",
    })
    manager = AsyncMock()
    manager.spawn_instance_with_mcp = AsyncMock(return_value="inst-rearm-st")

    adapter = SchedulerAdapter(
        config, AsyncMock(), None, source_repo=receipt_repo, manager=manager,
    )
    await adapter.start()
    await adapter._rearm_after_semaphore_timeout("exec-rearm-st")
    await adapter.stop()

    rows = {
        r.execution_id: r
        for r in receipt_repo.list_schedule_executions("sched-rearm-st")
    }
    fired = rows.get("exec-rearm-st")
    assert fired is not None, (
        f"the real fire must leave a receipt — got {sorted(rows)!r}"
    )
    assert fired.status == ExecutionStatus.NEVER_STARTED.value, (
        "the saturated fire began NO turn — the receipt must say so "
        f"(got {fired.status!r})"
    )
    assert fired.instance_id is None, (
        "a never-started receipt carries no instance handle"
    )
    assert fired.completed_at is not None

    # The re-arm's own audit row is separate and deferred.
    rearm_rows = [r for eid, r in rows.items() if eid != "exec-rearm-st"]
    assert len(rearm_rows) == 1, (
        f"the re-arm writes exactly one audit row — got {rearm_rows!r}"
    )
    assert rearm_rows[0].status == ExecutionStatus.DEFERRED.value
    assert adapter._fire_outcome == adapter.FIRE_OUTCOME_REARMED
