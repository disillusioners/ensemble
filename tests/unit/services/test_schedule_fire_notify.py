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


async def _notify(manager, creator="creator", recipient="recipient", **extra):
    cfg = {"creator_instance_id": creator} if creator is not None else {}
    kwargs = dict(
        schedule_config=cfg,
        metadata={"scheduler": {"trigger_time": "2026-01-01T00:00:00"}},
        recipient_instance_id=recipient,
        label="lbl",
        target_agent="ari",
        instance_mode="new_instance",
        recurrence="once",
        job_id="job-1",
        message_id="msg-1",
        target_instance_id=recipient,
    )
    kwargs.update(extra)
    await sfn.notify_schedule_fire(manager, **kwargs)


@pytest.mark.asyncio
async def test_message_shape_source_and_no_spawn():
    manager = _make_manager()
    await _notify(manager)
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
    # no-spawn pin: the notice goes to an existing instance only.
    manager.spawn_instance_with_mcp.assert_not_called()


@pytest.mark.asyncio
async def test_terminal_creator_no_revive_dropped():
    manager = _make_manager(status="completed")
    await _notify(manager)
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 0
    assert m.get_dropped_total() == 1
    assert m.get_sent_total() == 0


@pytest.mark.asyncio
async def test_absent_creator_dropped():
    manager = _make_manager(status=None)
    await _notify(manager)
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 0
    assert m.get_dropped_total() == 1


@pytest.mark.asyncio
async def test_paused_creator_queues_not_dropped():
    manager = _make_manager(status="paused")
    await _notify(manager)
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
    try:
        await _notify(manager)
    finally:
        asyncio.create_task = orig
    await asyncio.sleep(0.1)
    assert m.get_dropped_total() == 1


@pytest.mark.asyncio
async def test_coroutine_time_exception_absorbed():
    manager = _make_manager(enqueue_side_effect=RuntimeError("boom"))
    await _notify(manager)
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 1
    assert m.get_dropped_total() == 1
    assert m.get_sent_total() == 0


@pytest.mark.asyncio
async def test_identity_skip_no_drop():
    manager = _make_manager()
    await _notify(manager, creator="same", recipient="same")
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 0
    assert m.get_dropped_total() == 0


@pytest.mark.asyncio
async def test_absent_creator_key_no_drop():
    manager = _make_manager()
    await _notify(manager, creator=None)
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 0
    assert m.get_dropped_total() == 0


@pytest.mark.asyncio
async def test_non_dict_metadata_does_not_raise():
    """F2: a non-dict metadata/config value must be absorbed, never raise."""
    manager = _make_manager()
    await sfn.notify_schedule_fire(
        manager,
        schedule_config={"creator_instance_id": "creator"},
        metadata="not-a-dict",
        recipient_instance_id="recipient",
        label="lbl",
    )
    await asyncio.sleep(0.1)
    assert manager.enqueue_message_job.call_count == 1


@pytest.mark.asyncio
async def test_reason_taxonomy_is_live():
    """N7: the reason discriminator distinguishes an infra lookup error
    from a clean absent row, a terminal creator, and a missing enqueue
    hook. All four drop paths must emit their distinct reason token."""
    seen: list[dict] = []

    def _sink(event: dict) -> None:
        seen.append(dict(event))

    m.attach_sink(_sink)
    try:
        # (1) repo read RAISES -> creator_lookup_error (not creator_absent).
        err_manager = _make_manager(status="running")
        err_manager._instance_repository.get = um.Mock(
            side_effect=RuntimeError("db down")
        )
        await _notify(err_manager)
        await asyncio.sleep(0.1)
        lookup_reasons = [
            e["reason"] for e in seen
            if e["metric"] == m.SCHEDULE_FIRE_NOTIFY_DROPPED_TOTAL
            and (e["reason"] or "").startswith("creator_lookup_error")
        ]
        assert lookup_reasons, "repo-raise must record creator_lookup_error:*"

        # (2) row is None -> creator_absent.
        seen.clear()
        await _notify(_make_manager(status=None))
        await asyncio.sleep(0.1)
        assert any(e.get("reason") == "creator_absent" for e in seen), (
            "missing row must record creator_absent"
        )

        # (3) terminal row -> creator_terminal:<status>.
        seen.clear()
        await _notify(_make_manager(status="completed"))
        await asyncio.sleep(0.1)
        assert any(
            (e.get("reason") or "").startswith("creator_terminal:") for e in seen
        ), "terminal row must record creator_terminal:<status>"

        # (4) no enqueue hook -> enqueue_hook_absent.
        seen.clear()
        no_hook = _make_manager(status="running")
        no_hook.enqueue_message_job = None
        await _notify(no_hook)
        await asyncio.sleep(0.1)
        assert any(e.get("reason") == "enqueue_hook_absent" for e in seen), (
            "missing enqueue_message_job must record enqueue_hook_absent"
        )
    finally:
        m.attach_sink(None)
