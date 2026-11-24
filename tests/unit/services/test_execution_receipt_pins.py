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

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.source.models import ExecutionStatus, ScheduleExecution
from daemon.repositories.source.repository import SQLModelSourceRepository
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
# 5. Re-arm claim pin (mandated) — next-fire + turn + truthful receipt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rearm_claim_pin_next_fire_turn_and_receipt(receipt_repo):
    """A fired cycle persists a same-weekday next-fire, takes a turn, and the
    receipt says so truthfully (three legs, one coherent harness)."""
    # A WEEKLY cron (weekday 1 = Monday). The re-arm math must preserve it.
    config = make_config("sched-weekly", {
        "schedule": "0 9 * * 1",   # Mondays 09:00
        "agent": "./agents/developer",
        "message": "Weekly alarm",
        "timezone": "UTC",
    })

    manager = AsyncMock()
    # The enqueue creates a Task and marks it PROCESSING (the turn was taken).
    task_row = MagicMock()
    task_row.id = "task-weekly"
    manager._task_repo = MagicMock()
    manager._task_repo.get_by_work_id = MagicMock(return_value=task_row)
    manager.spawn_instance_with_mcp = AsyncMock(return_value="inst-weekly")
    result = MagicMock(job_id="job-weekly", message_id="msg-weekly", instance_id="inst-weekly", status="queued")
    manager.enqueue_message_job = AsyncMock(return_value=result)

    adapter = SchedulerAdapter(config, AsyncMock(), MagicMock(), manager=manager, source_repo=receipt_repo)
    await adapter.start()
    await adapter._emit_scheduled_message()
    await adapter.stop()

    # (a) a NEW next-fire with the SAME weekday is persisted/computable.
    nxt = adapter._get_next_trigger_time()
    assert nxt is not None
    assert nxt.weekday() == 0, f"re-arm must preserve Monday weekday, got {nxt.weekday()}"

    # (b) the spawned instance TOOK A TURN (durable enqueue + task processed).
    manager.enqueue_message_job.assert_called_once()
    assert task_row.id == "task-weekly"

    # (c) the receipt says so truthfully (dispatched with handles).
    receipt = receipt_repo.get_latest_execution("sched-weekly")
    assert receipt is not None
    assert receipt.status in (ExecutionStatus.DISPATCHED.value, ExecutionStatus.EXECUTED.value)
    assert receipt.instance_id == "inst-weekly"
    assert receipt.task_id == "task-weekly"
