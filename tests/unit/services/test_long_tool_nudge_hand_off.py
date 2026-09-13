"""T6 — deliver_long_tool_nudge hand-off seam contract (phase 1 stub).

Pins the phase-2 contract: the seam signature
``(parent_id, child_id, episode_ctx) -> bool``, the ``[LongToolNudge]
STUB_FIRE`` marker carrying the EpisodeCtx fields, and the
``handoff_fn`` injection seam. Phase 2 must NOT change the
``LongToolNudgeEpisodeCtx`` field set or the marker line.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from daemon.services.long_tool_nudge import (
    LONG_TOOL_NUDGE_SOURCE,
    LongToolNudgeEpisodeCtx,
    LongToolNudgeScanner,
    deliver_long_tool_nudge,
)
from tests.helpers.long_tool_nudge import make_episode_ctx


def _ctx(**overrides) -> LongToolNudgeEpisodeCtx:
    """M2 — thin alias over the shared helper."""
    return make_episode_ctx(**overrides)


@pytest.mark.asyncio
async def test_module_stub_logs_stub_fire_and_returns_true(caplog):
    with caplog.at_level("INFO", logger="daemon.services.long_tool_nudge"):
        result = await deliver_long_tool_nudge(
            "parent-1", "child-1", _ctx(tool_call_id="call-abc123")
        )
    assert result is True
    records = [r for r in caplog.records if "STUB_FIRE" in r.message]
    assert len(records) == 1
    message = records[0].getMessage()
    for fragment in (
        "parent-1"[:8],
        "child-1"[:8],
        "bash",
        "call-abc"[:8],
    ):
        assert fragment in message
    assert "950" in message and "900" in message


@pytest.mark.asyncio
async def test_scanner_stub_enabled_emits_stub_fire(caplog):
    scanner = LongToolNudgeScanner(
        instance_repository=None,
        manager=None,
        handoff_stub_enabled=True,
    )
    with caplog.at_level("INFO", logger="daemon.services.long_tool_nudge"):
        result = await scanner.deliver_long_tool_nudge(
            "parent-1", "child-1", _ctx()
        )
    assert result is True
    assert any("STUB_FIRE" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_injected_handoff_fn_called_once_with_same_ctx():
    handoff_fn = AsyncMock(return_value=True)
    scanner = LongToolNudgeScanner(
        instance_repository=None,
        manager=None,
        handoff_stub_enabled=False,
        handoff_fn=handoff_fn,
    )
    ctx = _ctx()
    result = await scanner.deliver_long_tool_nudge("parent-1", "child-1", ctx)
    assert result is True
    handoff_fn.assert_awaited_once_with("parent-1", "child-1", ctx)


@pytest.mark.asyncio
async def test_handoff_fn_returning_false_is_not_a_successful_fire():
    handoff_fn = AsyncMock(return_value=False)
    scanner = LongToolNudgeScanner(
        instance_repository=None,
        manager=None,
        handoff_stub_enabled=False,
        handoff_fn=handoff_fn,
    )
    result = await scanner.deliver_long_tool_nudge(
        "parent-1", "child-1", _ctx()
    )
    assert result is False


@pytest.mark.asyncio
async def test_handoff_fn_exception_returns_false_not_raise():
    handoff_fn = AsyncMock(side_effect=RuntimeError("boom"))
    scanner = LongToolNudgeScanner(
        instance_repository=None,
        manager=None,
        handoff_stub_enabled=False,
        handoff_fn=handoff_fn,
    )
    result = await scanner.deliver_long_tool_nudge(
        "parent-1", "child-1", _ctx()
    )
    assert result is False


@pytest.mark.asyncio
async def test_no_handoff_and_no_stub_exercises_phase2_branch():
    """LOWS — this test previously passed via the WRONG path: with
    ``instance_repository=None`` and ``manager=None``, the
    ``_get_parent`` callee would AttributeError on
    ``None.get(parent_id)``, swallow, return ``None``, hit the
    "parent not found" WARN, return ``False`` — and the test would
    report success without ever entering the phase-2 real-delivery
    branch it claims to exercise.

    Give it a valid MagicMock repo + AsyncMock manager so the test
    actually walks the phase-2 branch: pre-read returns a running
    parent → enqueue is awaited → nudge durable → ``True``. The
    renamed test name matches the exercised surface.
    """
    from unittest.mock import AsyncMock, MagicMock

    repo = MagicMock()
    repo.get_metadata_value = MagicMock(return_value=None)
    parent = MagicMock()
    parent.status = "running"
    repo.get = MagicMock(return_value=parent)
    manager = AsyncMock()
    manager.enqueue_message = AsyncMock()

    scanner = LongToolNudgeScanner(
        instance_repository=repo,
        manager=manager,
        handoff_stub_enabled=False,
        handoff_fn=None,
    )
    result = await scanner.deliver_long_tool_nudge("p", "c", _ctx())
    assert result is True
    manager.enqueue_message.assert_awaited_once()


def test_source_constant_canonical():
    assert LONG_TOOL_NUDGE_SOURCE == "system:long-tool-nudge"
