"""Unit tests for schedule_fire_notify helper (A7 matrix)."""

import asyncio
import unittest.mock as um

import pytest

from daemon.services import schedule_fire_notify as sfn
from daemon.services import schedule_fire_notify_metrics as m


@pytest.fixture(autouse=True)
def _reset_counters():
    m.reset_sent_total()
    m.reset_dropped_total()
    yield
    m.reset_sent_total()
    m.reset_dropped_total()


def _make_manager(*, status="running", enqueue_side_effect=None):
    manager = um.Mock()
    manager._instance_repository = um.Mock()
    if status is None:
        manager._instance_repository.get = um.Mock(return_value=None)
    else:
        row = um.Mock()
        row.status = status
        manager._instance_repository.get = um.Mock(return_value=row)
    if enqueue_side_effect is None:
        manager.enqueue_message_job = um.AsyncMock()
    else:
        manager.enqueue_message_job = um.AsyncMock(side_effect=enqueue_side_effect)
    return manager


@pytest.mark.asyncio
async def test_message_shape_source_and_no_spawn():
    manager = _make_manager()
    await sfn.notify_schedule_fire(
        manager,
        creator_instance_id="creator",
        recipient_instance_id="recipient",
        label="lbl",
        fired_at="2026-01-01T00:00:00",
        target_agent="ari",
        instance_mode="new_instance",
        recurrence="once",
        job_id="job-1",
        message_id="msg-1",
        target_instance_id="recipient",
    )
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 1
    kwargs = manager.enqueue_message_job.call_args.kwargs
    assert kwargs["source"] == "system:schedule_fire_notify"
    assert kwargs["instance_id"] == "creator"
    msg = kwargs["message"]
    for field in ("label=lbl", "fired_at=2026-01-01T00:00:00", "target_agent=ari",
                  "instance_mode=new_instance", "recurrence=once", "job_id=job-1",
                  "message_id=msg-1", "target_instance_id=recipient"):
        assert field in msg, field
    assert m.get_sent_total() == 1
    assert m.get_dropped_total() == 0


@pytest.mark.asyncio
async def test_terminal_creator_no_revive_dropped():
    manager = _make_manager(status="completed")
    await sfn.notify_schedule_fire(
        manager, creator_instance_id="creator", recipient_instance_id="recipient",
        label="lbl",
    )
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 0
    assert m.get_dropped_total() == 1
    assert m.get_sent_total() == 0


@pytest.mark.asyncio
async def test_absent_creator_dropped():
    manager = _make_manager(status=None)
    await sfn.notify_schedule_fire(
        manager, creator_instance_id="creator", recipient_instance_id="recipient",
        label="lbl",
    )
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 0
    assert m.get_dropped_total() == 1


@pytest.mark.asyncio
async def test_paused_creator_queues_not_dropped():
    manager = _make_manager(status="paused")
    await sfn.notify_schedule_fire(
        manager, creator_instance_id="creator", recipient_instance_id="recipient",
        label="lbl",
    )
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 1
    assert m.get_dropped_total() == 0
    assert m.get_sent_total() == 1


@pytest.mark.asyncio
async def test_gate_time_exception_absorbed():
    orig = asyncio.create_task
    def boom(*a, **k):
        raise RuntimeError("gate boom")
    asyncio.create_task = boom
    manager = _make_manager()
    await sfn.notify_schedule_fire(
        manager, creator_instance_id="creator", recipient_instance_id="recipient",
        label="lbl",
    )
    asyncio.create_task = orig
    await asyncio.sleep(0.1)
    assert m.get_dropped_total() == 1


@pytest.mark.asyncio
async def test_coroutine_time_exception_absorbed():
    manager = _make_manager(enqueue_side_effect=RuntimeError("boom"))
    await sfn.notify_schedule_fire(
        manager, creator_instance_id="creator", recipient_instance_id="recipient",
        label="lbl",
    )
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 1
    assert m.get_dropped_total() == 1
    assert m.get_sent_total() == 0


@pytest.mark.asyncio
async def test_identity_skip_no_drop():
    manager = _make_manager()
    await sfn.notify_schedule_fire(
        manager, creator_instance_id="same", recipient_instance_id="same",
        label="lbl",
    )
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 0
    assert m.get_dropped_total() == 0


@pytest.mark.asyncio
async def test_absent_creator_key_no_drop():
    manager = _make_manager()
    await sfn.notify_schedule_fire(
        manager, creator_instance_id=None, recipient_instance_id="recipient",
        label="lbl",
    )
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 0
    assert m.get_dropped_total() == 0
