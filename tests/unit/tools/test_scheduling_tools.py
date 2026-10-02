"""Unit tests for the agent-facing ``scheduling`` tools (phase-2 §Task 2.7).

Explicitly DEFERRED from unit 2 to this commit (unit 4) per the commission
instructions. Tests ``create_scheduling_tools`` (``daemon/tools/scheduling.py``)
with a mocked ``scheduling_service`` — the four ``@tool`` callables are driven
via ``await tool.coroutine(...)`` (house convention, mirrors
``tests/unit/tools/test_job_pause_resume_tools.py``).

Pinned wrapper contract:
  * echo surfaces BOTH ``next_run_at_local`` AND ``next_run_at_utc``
  * cancel-by-label resolution via the injected ``source_repo``
  * tools NEVER raise — every failure is an ``"ERROR: {msg}"`` string
    (duplicate label / invalid input / cancelled update / not-found label)

SAFETY FENCE: pure unit tests — mocks only, the daemon is NEVER booted and
no DB is ever touched, so no ambient ``POSTGRES_*`` can leak anywhere.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.scheduling_service import (
    ScheduleCancelResponse,
    ScheduleCreateResponse,
    ScheduleListItem,
    ScheduleUpdateResponse,
)
from daemon.tools.scheduling import create_scheduling_tools

CALLER_INSTANCE = "inst-caller-1"
CALLER_AGENT = "worker"


@pytest.fixture
def tool_env():
    """The four tools bound to a mocked service + source repo."""
    service = MagicMock()
    # Pre-wire ALL four service seams as AsyncMocks so per-test overrides and
    # assert_not_awaited() work uniformly (the tools await every call).
    service.create_schedule = AsyncMock(return_value=None)
    service.list_schedules = AsyncMock(return_value=[])
    service.cancel_schedule = AsyncMock(return_value=None)
    service.update_schedule = AsyncMock(return_value=None)
    repo = MagicMock()
    tools = create_scheduling_tools(
        service,
        source_repo=repo,
        current_instance_id=CALLER_INSTANCE,
        agent_id=CALLER_AGENT,
    )
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {
        "task_schedule", "task_schedule_list", "task_schedule_cancel", "task_schedule_update",
    }
    return by_name, service, repo


def _create_response(**overrides) -> ScheduleCreateResponse:
    values = dict(
        source_id="s-1", label="s-1", status="running",
        next_run_at_local="2026-10-02T06:00:00-04:00",
        next_run_at_utc="2026-10-02T10:00:00+00:00",
        tz_warning="",
    )
    values.update(overrides)
    return ScheduleCreateResponse(**values)


# ---------------------------------------------------------------------------
# task_schedule (create)
# ---------------------------------------------------------------------------


class TestTaskScheduleTool:
    @pytest.mark.asyncio
    async def test_create_echoes_both_timezones(self, tool_env):
        tools, service, _repo = tool_env
        service.create_schedule = AsyncMock(return_value=_create_response())
        result = await tools["task_schedule"].coroutine(
            label="brief", message="hello", when="06:00", recurrence="daily",
            timezone="America/New_York",
        )
        assert not isinstance(result, str), f"unexpected error string: {result}"
        # BOTH tz echoes — the OD-4 pin.
        assert result["next_run_at_local"] == "2026-10-02T06:00:00-04:00"
        assert result["next_run_at_utc"] == "2026-10-02T10:00:00+00:00"
        assert result["source_id"] == "s-1"

    @pytest.mark.asyncio
    async def test_create_defaults_agent_to_caller(self, tool_env):
        """``agent_id=None`` → the CALLING agent via the factory closure."""
        from daemon.services.scheduling_service import ScheduleCreatePayload

        tools, service, _repo = tool_env
        service.create_schedule = AsyncMock(return_value=_create_response())
        await tools["task_schedule"].coroutine(
            label="self-1", message="hello", when="06:00", recurrence="daily",
        )
        payload = service.create_schedule.await_args.args[0]
        assert isinstance(payload, ScheduleCreatePayload)
        assert payload.agent == CALLER_AGENT
        assert service.create_schedule.await_args.kwargs["caller_instance_id"] == CALLER_INSTANCE
        assert service.create_schedule.await_args.kwargs["caller_agent_id"] == CALLER_AGENT

    @pytest.mark.asyncio
    async def test_create_duplicate_label_is_error_string(self, tool_env):
        tools, service, _repo = tool_env
        service.create_schedule = AsyncMock(
            side_effect=ValueError("label already exists: brief")
        )
        result = await tools["task_schedule"].coroutine(
            label="brief", message="hello", when="06:00", recurrence="daily",
        )
        assert isinstance(result, str)
        assert result.startswith("ERROR:")
        assert "label already exists" in result

    @pytest.mark.asyncio
    async def test_create_invalid_input_is_error_string(self, tool_env):
        tools, service, _repo = tool_env
        service.create_schedule = AsyncMock(
            side_effect=ValueError("Invalid recurrence 'hourly': expected one of once, daily, weekly, cron")
        )
        result = await tools["task_schedule"].coroutine(
            label="bad", message="hello", when="06:00", recurrence="hourly",
        )
        assert isinstance(result, str)
        assert result.startswith("ERROR:")
        assert "Invalid recurrence" in result


# ---------------------------------------------------------------------------
# task_schedule_list
# ---------------------------------------------------------------------------


def _list_item(source_id: str, status: str = "running") -> ScheduleListItem:
    return ScheduleListItem(
        source_id=source_id, label=source_id, status=status,
        recurrence="daily", local_time="06:00", timezone="UTC", weekday=None,
        cron_expression=None, instance_mode="new_instance",
        next_run_at_local=None, next_run_at_utc=None,
        agent=CALLER_AGENT, project_id=None, last_run_at=None,
        cancelled_at=None, tz_warning="",
    )


class TestTaskScheduleListTool:
    @pytest.mark.asyncio
    async def test_list_returns_schedules_and_count(self, tool_env):
        tools, service, _repo = tool_env
        service.list_schedules = AsyncMock(
            return_value=[_list_item("a"), _list_item("b", status="stopped")]
        )
        result = await tools["task_schedule_list"].coroutine()
        assert result["count"] == 2
        assert [s["source_id"] for s in result["schedules"]] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_list_defaults_agent_filter_to_caller(self, tool_env):
        tools, service, _repo = tool_env
        service.list_schedules = AsyncMock(return_value=[])
        await tools["task_schedule_list"].coroutine()
        assert service.list_schedules.await_args.kwargs["caller_agent_id"] == CALLER_AGENT

    @pytest.mark.asyncio
    async def test_list_error_is_error_string(self, tool_env):
        tools, service, _repo = tool_env
        service.list_schedules = AsyncMock(side_effect=RuntimeError("repo offline"))
        result = await tools["task_schedule_list"].coroutine()
        assert isinstance(result, str)
        assert result.startswith("ERROR:")


# ---------------------------------------------------------------------------
# task_schedule_cancel
# ---------------------------------------------------------------------------


class TestTaskScheduleCancelTool:
    @pytest.mark.asyncio
    async def test_cancel_by_source_id_echoes(self, tool_env):
        tools, service, _repo = tool_env
        service.cancel_schedule = AsyncMock(
            return_value=ScheduleCancelResponse(
                source_id="s-1", status="cancelled",
                cancelled_at="2026-10-01T20:00:00+00:00",
                last_execution_id="exec-9",
            )
        )
        result = await tools["task_schedule_cancel"].coroutine(source_id="s-1")
        assert result["status"] == "cancelled"
        assert result["last_execution_id"] == "exec-9"
        service.cancel_schedule.assert_awaited_with("s-1")

    @pytest.mark.asyncio
    async def test_cancel_by_label_resolves_via_source_repo(self, tool_env):
        """Label → source_id resolution goes through the injected repo's
        ``get_source_config_by_name`` (exact match)."""
        tools, service, repo = tool_env
        row = MagicMock(source_id="resolved-1", name="my-label")
        repo.get_source_config_by_name = MagicMock(return_value=row)
        service.cancel_schedule = AsyncMock(
            return_value=ScheduleCancelResponse(
                source_id="resolved-1", status="cancelled",
                cancelled_at="2026-10-01T20:00:00+00:00",
                last_execution_id=None,
            )
        )
        result = await tools["task_schedule_cancel"].coroutine(label="my-label")
        assert result["source_id"] == "resolved-1"
        assert result["label"] == "my-label"  # echo of the human reference
        service.cancel_schedule.assert_awaited_with("resolved-1")

    @pytest.mark.asyncio
    async def test_cancel_label_not_found_is_error_string(self, tool_env):
        tools, service, repo = tool_env
        repo.get_source_config_by_name = MagicMock(return_value=None)
        result = await tools["task_schedule_cancel"].coroutine(label="ghost")
        assert isinstance(result, str)
        assert result.startswith("ERROR:")
        assert "No schedule found with label" in result
        service.cancel_schedule.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_requires_exactly_one_reference(self, tool_env):
        tools, service, _repo = tool_env
        both = await tools["task_schedule_cancel"].coroutine(source_id="a", label="b")
        assert isinstance(both, str) and both.startswith("ERROR:")
        neither = await tools["task_schedule_cancel"].coroutine()
        assert isinstance(neither, str) and neither.startswith("ERROR:")
        service.cancel_schedule.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_already_cancelled_is_error_string(self, tool_env):
        tools, service, _repo = tool_env
        service.cancel_schedule = AsyncMock(
            side_effect=ValueError("Schedule already cancelled: s-1")
        )
        result = await tools["task_schedule_cancel"].coroutine(source_id="s-1")
        assert isinstance(result, str)
        assert result.startswith("ERROR:")
        assert "already cancelled" in result


# ---------------------------------------------------------------------------
# task_schedule_update
# ---------------------------------------------------------------------------


class TestTaskScheduleUpdateTool:
    @pytest.mark.asyncio
    async def test_update_returns_response_dump(self, tool_env):
        tools, service, _repo = tool_env
        service.update_schedule = AsyncMock(
            return_value=ScheduleUpdateResponse(
                source_id="s-1", label="s-1", status="stopped", paused=True,
                next_run_at_local="2026-10-03T06:00:00+00:00",
                next_run_at_utc="2026-10-03T06:00:00+00:00",
                tz_warning="",
            )
        )
        result = await tools["task_schedule_update"].coroutine(
            source_id="s-1", when="07:00",
        )
        assert result["paused"] is True
        # BOTH tz echoes on the update surface too.
        assert result["next_run_at_local"] is not None
        assert result["next_run_at_utc"] is not None

    @pytest.mark.asyncio
    async def test_update_cancelled_schedule_is_error_string(self, tool_env):
        tools, service, _repo = tool_env
        service.update_schedule = AsyncMock(
            side_effect=ValueError("Schedule is cancelled and cannot be updated: s-1")
        )
        result = await tools["task_schedule_update"].coroutine(source_id="s-1", when="07:00")
        assert isinstance(result, str)
        assert result.startswith("ERROR:")
        assert "cancelled" in result
