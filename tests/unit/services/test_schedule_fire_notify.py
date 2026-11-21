"""Unit tests for schedule_fire_notify helper."""

import asyncio

import pytest

from daemon.services import schedule_fire_notify as sfn
from daemon.services import schedule_fire_notify_metrics as m


@pytest.mark.asyncio
async def test_smoke_no_raise():
    m.reset_sent_total()
    m.reset_dropped_total()
    import unittest.mock as um
    manager = um.Mock()
    manager._instance_repository = um.Mock()
    row = um.Mock()
    row.status = "running"
    manager._instance_repository.get = um.Mock(return_value=row)
    manager.enqueue_message_job = um.AsyncMock()
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
    assert m.get_sent_total() == 1
