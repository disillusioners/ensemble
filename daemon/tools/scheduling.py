"""Agent-facing scheduling tools — the ``scheduling`` category (4 tools).

Thin closure layer over ``daemon.services.scheduling_service``. The tools
inject caller identity (instance id, agent id) and translate user-stated
local times into the canonical payload shape. They NEVER mint jobs and
NEVER touch the HTTP job-creation endpoint — dispatch is the
SchedulerAdapter's job.

House rules honored here (ADR-005):
* Tools NEVER raise — every failure returns an ``"ERROR: {msg}"`` string
  (precedent: ``daemon/tools/time.py``).
``task_schedule(agent_id=None)`` defaults the invoked agent to the
CALLING agent via the factory closure (precedent:
``create_job_tools(..., agent_id, ...)``).
* The tz fallback warning is NEVER suppressed (ADR-002 / OD-4) — it is
  echoed verbatim in every create/update response.
* Cancel is TERMINAL (history preserved, hidden from default list);
  pause/resume via ``task_schedule_update(paused=...)`` is RESUMABLE.
* Every echo surface returns BOTH ``next_run_at_local`` (in the
  schedule's timezone) AND ``next_run_at_utc``.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ._tool_registry import register_tool_category
from ._truncate import truncate_dict_result

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Scheduling"
CATEGORY_DOC = """\
Schedule tasks at a specific time (one-shot or recurring daily/weekly/cron).

Tools in this category:
- ``task_schedule``        — create a scheduled task
- ``task_schedule_list``   — list scheduled tasks (cancelled hidden by default)
- ``task_schedule_cancel`` — cancel a scheduled task (terminal, history preserved)
- ``task_schedule_update`` — update a scheduled task (reschedule, pause, resume)

Timezone rule: a user-stated time (``when``) is interpreted in the
supplied ``timezone``; when omitted, the daemon-wide default
(``ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE``) applies, then the host-local
timezone, then UTC — with a loud warning echoed in the response. Every
response surfaces BOTH ``next_run_at_local`` (in the resolved timezone)
AND ``next_run_at_utc``.
"""


def _error(msg: str) -> str:
    """House error envelope — tools never raise."""
    return f"ERROR: {msg}"


def create_scheduling_tools(
    scheduling_service: Any,
    *,
    source_repo: Any = None,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the four scheduling tools for one instance/agent.

    Args:
        scheduling_service: The ``daemon.services.scheduling_service``
            module (exposes create_schedule / cancel_schedule /
            get_schedule / list_schedules / update_schedule). Injected
            per-instance from ``InstanceManager.scheduling_service``.
        source_repo: Source repository, used ONLY by
            ``task_schedule_cancel`` to resolve a human label to a
            source_id (exact match on the name column).
        current_instance_id: Caller's instance ID (audit echo only).
        agent_id: Caller's agent ID — the default invoked agent when
            ``task_schedule`` receives ``agent_id=None``. Captured at
            factory time; an agent rename invalidates the closure at
            the next daemon restart (documented, acceptable).
        agent_tag: Caller's version tag. Currently unused by the tools
            themselves; reserved so the factory signature matches the
            other per-instance factories (``create_job_tools`` et al.).

    Returns:
        A list of the four ``@tool``-decorated callables.
    """
    caller_agent_id = agent_id
    caller_instance_id = current_instance_id

    class TaskScheduleInput(BaseModel):
        """Input schema for task_schedule."""

        label: Annotated[str, Field(description="Unique human-readable name for the schedule (1-128 chars)")]
        message: Annotated[str, Field(description="The message sent to the agent when the schedule fires")]
        when: Annotated[str, Field(description="User-stated local time: ISO 8601 ('2026-10-15 06:00' or '2026-10-15T06:00:00+07:00') for recurrence=once; 'HH:MM' for daily/weekly")]
        recurrence: Annotated[str, Field(description="once | daily | weekly | cron")]
        timezone: Annotated[str | None, Field(default=None, description="IANA timezone for `when` (e.g. 'Asia/Ho_Chi_Minh'); omit for the daemon default chain (default -> host-local -> UTC with a warning)")]
        weekday: Annotated[int | None, Field(default=None, ge=0, le=6, description="Day of week for recurrence=weekly: 0=Sunday..6=Saturday")]
        cron_expression: Annotated[str | None, Field(default=None, description="Raw cron expression; required when recurrence='cron'")]
        agent_id: Annotated[str | None, Field(default=None, description="Agent to invoke when the schedule fires; omit for yourself (the calling agent)")]
        project_id: Annotated[str | None, Field(default=None, description="Project ID for job routing and list filtering")]
        priority: Annotated[int, Field(default=5, ge=1, le=10, description="Job priority 1-10 (1=lowest, 10=highest)")]
        instance_mode: Annotated[str, Field(default="new_instance", description="new_instance (default) | reuse_instance")]

    @register_tool_category("scheduling")
    @tool(args_schema=TaskScheduleInput)
    async def task_schedule(
        label: Annotated[str, Field(description="Unique human-readable name for the schedule (1-128 chars)")],
        message: Annotated[str, Field(description="The message sent to the agent when the schedule fires")],
        when: Annotated[str, Field(description="User-stated local time: ISO 8601 for recurrence=once; 'HH:MM' for daily/weekly")],
        recurrence: Annotated[str, Field(description="once | daily | weekly | cron")],
        timezone: Annotated[str | None, Field(default=None, description="IANA timezone for `when`; omit for the daemon default chain")] = None,
        weekday: Annotated[int | None, Field(default=None, ge=0, le=6, description="0=Sunday..6=Saturday (weekly only)")] = None,
        cron_expression: Annotated[str | None, Field(default=None, description="Raw cron expression; required when recurrence='cron'")] = None,
        agent_id: Annotated[str | None, Field(default=None, description="Agent to invoke; omit for yourself (the calling agent)")] = None,
        project_id: Annotated[str | None, Field(default=None, description="Project ID for job routing and list filtering")] = None,
        priority: Annotated[int, Field(default=5, ge=1, le=10, description="Job priority 1-10")] = 5,
        instance_mode: Annotated[str, Field(default="new_instance", description="new_instance (default) | reuse_instance")] = "new_instance",
    ) -> dict:
        """Create a scheduled task. Use tool_help("task_schedule") for details."""
        try:
            from daemon.services.scheduling_service import ScheduleCreatePayload

            payload = ScheduleCreatePayload(
                label=label,
                agent=agent_id or caller_agent_id,
                message=message,
                when=when,
                timezone=timezone,
                recurrence=recurrence,
                weekday=weekday,
                cron_expression=cron_expression,
                project_id=project_id,
                priority=priority,
                instance_mode=instance_mode,
            )
            result = await scheduling_service.create_schedule(
                payload,
                caller_instance_id=caller_instance_id,
                caller_agent_id=caller_agent_id,
            )
            return result.model_dump()
        except Exception as exc:  # noqa: BLE001 — house pattern: error string, never raise
            return _error(str(exc))

    class TaskScheduleListInput(BaseModel):
        """Input schema for task_schedule_list."""

        status: Annotated[str | None, Field(default=None, description="Filter by status: running | stopped | cancelled")]
        project_id: Annotated[str | None, Field(default=None, description="Filter by project ID")]
        include_cancelled: Annotated[bool, Field(default=False, description="Include cancelled schedules (hidden by default)")]
        agent_id: Annotated[str | None, Field(default=None, description="Filter by invoked agent; omit for yourself (the calling agent)")]

    @register_tool_category("scheduling")
    @tool(args_schema=TaskScheduleListInput)
    async def task_schedule_list(
        status: Annotated[str | None, Field(default=None, description="Filter by status: running | stopped | cancelled")] = None,
        project_id: Annotated[str | None, Field(default=None, description="Filter by project ID")] = None,
        include_cancelled: Annotated[bool, Field(default=False, description="Include cancelled schedules (hidden by default)")] = False,
        agent_id: Annotated[str | None, Field(default=None, description="Filter by invoked agent; omit for yourself (the calling agent)")] = None,
    ) -> dict:
        """List scheduled tasks (cancelled hidden by default). Use tool_help("task_schedule_list") for details."""
        try:
            items = await scheduling_service.list_schedules(
                project_id=project_id,
                status=status,
                include_cancelled=include_cancelled,
                caller_agent_id=agent_id or caller_agent_id,
            )
            result = {
                "schedules": [item.model_dump() for item in items],
                "count": len(items),
            }
            return truncate_dict_result(result, "schedules")
        except Exception as exc:  # noqa: BLE001 — house pattern: error string, never raise
            return _error(str(exc))

    class TaskScheduleCancelInput(BaseModel):
        """Input schema for task_schedule_cancel."""

        source_id: Annotated[str | None, Field(default=None, description="Schedule ID (from task_schedule / task_schedule_list)")]
        label: Annotated[str | None, Field(default=None, description="Schedule label — alternative to source_id (exact match; first match wins on collision)")]

    @register_tool_category("scheduling")
    @tool(args_schema=TaskScheduleCancelInput)
    async def task_schedule_cancel(
        source_id: Annotated[str | None, Field(default=None, description="Schedule ID")] = None,
        label: Annotated[str | None, Field(default=None, description="Schedule label — alternative to source_id (exact match; first match wins on collision)")] = None,
    ) -> dict:
        """Cancel a scheduled task (TERMINAL — history is preserved). Use tool_help("task_schedule_cancel") for details."""
        try:
            if (source_id is None) == (label is None):
                return _error(
                    "Provide exactly one of source_id or label "
                    f"(got source_id={source_id!r}, label={label!r})"
                )
            resolved_id = source_id
            if label is not None:
                if source_repo is None:
                    return _error("label resolution unavailable: source repository not wired")
                row = source_repo.get_source_config_by_name(label)
                if row is None:
                    return _error(f"No schedule found with label {label!r}")
                resolved_id = row.source_id
            result = await scheduling_service.cancel_schedule(resolved_id)
            echo: dict[str, Any] = result.model_dump()
            if label is not None:
                echo["label"] = label
            return echo
        except Exception as exc:  # noqa: BLE001 — house pattern: error string, never raise
            return _error(str(exc))

    class TaskScheduleUpdateInput(BaseModel):
        """Input schema for task_schedule_update."""

        source_id: Annotated[str, Field(description="Schedule ID to update")]
        label: Annotated[str | None, Field(default=None, description="Rename the schedule (must stay unique)")]
        message: Annotated[str | None, Field(default=None, description="Replace the fired message")]
        when: Annotated[str, Field(default=None, description="New trigger time (ISO 8601 for once; 'HH:MM' for daily/weekly)")]
        timezone: Annotated[str | None, Field(default=None, description="IANA timezone; re-interprets wall-clock times in this timezone")]
        paused: Annotated[bool | None, Field(default=None, description="true = pause (resumable); false = resume")]
        priority: Annotated[int | None, Field(default=None, ge=1, le=10, description="Job priority 1-10")]

    @register_tool_category("scheduling")
    @tool(args_schema=TaskScheduleUpdateInput)
    async def task_schedule_update(
        source_id: Annotated[str, Field(description="Schedule ID to update")],
        label: Annotated[str | None, Field(default=None, description="Rename the schedule (must stay unique)")] = None,
        message: Annotated[str | None, Field(default=None, description="Replace the fired message")] = None,
        when: Annotated[str, Field(default=None, description="New trigger time (ISO 8601 for once; 'HH:MM' for daily/weekly)")] = None,
        timezone: Annotated[str | None, Field(default=None, description="IANA timezone; re-interprets wall-clock times in this timezone")] = None,
        paused: Annotated[bool | None, Field(default=None, description="true = pause (resumable); false = resume")] = None,
        priority: Annotated[int | None, Field(default=None, ge=1, le=10, description="Job priority 1-10")] = None,
    ) -> dict:
        """Update a scheduled task (reschedule / re-message / pause / resume). Use tool_help("task_schedule_update") for details."""
        try:
            from daemon.services.scheduling_service import ScheduleUpdatePayload

            payload = ScheduleUpdatePayload(
                label=label,
                message=message,
                when=when,
                timezone=timezone,
                paused=paused,
                priority=priority,
            )
            result = await scheduling_service.update_schedule(source_id, payload)
            return result.model_dump()
        except Exception as exc:  # noqa: BLE001 — house pattern: error string, never raise
            return _error(str(exc))

    task_schedule._full_doc_ = """Create a new scheduled task (wall-clock trigger).

The trigger time is interpreted in the supplied timezone; when the
timezone is omitted the daemon default chain applies (configured
default, then host-local detection, then UTC with a loud warning). The
warning is never suppressed — read `tz_warning` in the response.

Args:
    label: Unique human-readable name (1-128 chars). Duplicate labels are
        rejected.
    message: The message sent to the agent when the schedule fires.
    when: User-stated local time. ISO 8601 (naive or tz-aware) for
        recurrence="once"; "HH:MM" for daily/weekly. Ignored for cron.
    recurrence: "once" | "daily" | "weekly" | "cron".
    timezone: IANA name (e.g. "America/New_York"). Omit for the daemon
        default chain.
    weekday: Day of week for recurrence="weekly": 0=Sunday..6=Saturday.
    cron_expression: Raw cron expression; required when recurrence="cron".
    agent_id: Agent to invoke. Omit to schedule for yourself (the
        calling agent).
    project_id: Project ID for job routing and list filtering.
    priority: Job priority 1-10 (default 5).
    instance_mode: "new_instance" (default) or "reuse_instance".

Returns:
    dict with source_id, label, status, next_run_at_local,
    next_run_at_utc (BOTH timezones always present; null when nothing is
    upcoming), and tz_warning (non-empty when the timezone chain fell
    back).

Examples:
    task_schedule(label="morning-briefing", message="Give me a briefing",
                  when="06:00", recurrence="daily")
    task_schedule(label="deploy-reminder", message="Remind the team",
                  when="2026-10-15T14:00:00", recurrence="once",
                  timezone="Europe/Berlin")
    task_schedule(label="friday-rollup", message="Post the weekly rollup",
                  when="16:30", recurrence="weekly", weekday=5)

Error cases (returned as "ERROR: ..." strings, never raised):
    duplicate label, unknown agent, invalid recurrence / when / cron,
    invalid timezone name (falls back to UTC with tz_warning instead).
"""

    task_schedule_list._full_doc_ = """List scheduled tasks.

Cancelled schedules are HIDDEN by default (cancel is terminal — users do
not want to see what they killed); pass include_cancelled=True to audit
them. Every item echoes BOTH next_run_at_local and next_run_at_utc.

Args:
    status: Filter by row status ("running" | "stopped" | "cancelled").
    project_id: Only schedules created for this project.
    include_cancelled: Include cancelled schedules (default False).
    agent_id: Filter by invoked agent. Omit for yourself (the calling
        agent).

Returns:
    dict {"schedules": [...], "count": N}; each schedule carries
    source_id, label, status, recurrence, local_time, timezone,
    next_run_at_local, next_run_at_utc, agent, project_id, last_run_at.
    Large lists are truncated with a _pagination hint.

Examples:
    task_schedule_list()
    task_schedule_list(include_cancelled=True)
    task_schedule_list(status="stopped", project_id="my-project")
"""

    task_schedule_cancel._full_doc_ = """Cancel a scheduled task — TERMINAL.

A cancelled schedule NEVER fires again (not after a restart, not after a
re-list). Its execution history is preserved, and cancelled schedules
disappear from the default list. To take a break without losing the
schedule, use task_schedule_update(paused=True) instead — pause is
resumable.

Args:
    source_id: The schedule ID (from task_schedule / task_schedule_list).
    label: The schedule's human-readable name — alternative to source_id.
        Exact match; if two schedules share a label the first match wins
        (prefer source_id when labels may collide). Provide exactly one
        of source_id / label.

Returns:
    dict with source_id, status ("cancelled"), cancelled_at, and
    last_execution_id — the most recent execution. A trigger that
    already dispatched its job before the cancel landed still runs;
    cancel that in-flight job directly using last_execution_id.

Examples:
    task_schedule_cancel(source_id="3f2a...")
    task_schedule_cancel(label="morning-briefing")

Error cases (returned as "ERROR: ..." strings): both/neither of
source_id+label, unknown label, unknown schedule, already-cancelled.
"""

    task_schedule_update._full_doc_ = """Update a scheduled task (reschedule / re-message / pause / resume).

Pause is RESUMABLE (the schedule stays, the trigger stops); cancel is
TERMINAL (different tool). A pause survives a daemon restart; resume
picks the schedule back up. Rescheduling a "once" task re-anchors its
run time; rescheduling daily/weekly tasks rewrites their wall-clock
cron while keeping the schedule's timezone.

Args:
    source_id: The schedule ID to update.
    label: Rename the schedule (must stay unique).
    message: Replace the message fired at trigger time.
    when: New trigger time — ISO 8601 for recurrence="once", "HH:MM" for
        daily/weekly.
    timezone: IANA name; re-interprets wall-clock times in this timezone.
    paused: true = pause (resumable); false = resume.
    priority: Job priority 1-10.

Returns:
    dict with source_id, label, status, paused, next_run_at_local,
    next_run_at_utc, tz_warning.

Examples:
    task_schedule_update(source_id="3f2a...", when="07:30")
    task_schedule_update(source_id="3f2a...", paused=True)
    task_schedule_update(source_id="3f2a...", paused=False)

Error cases (returned as "ERROR: ..." strings): unknown schedule,
updating a CANCELLED schedule (terminal — create a new one instead),
duplicate new label, invalid when/timezone.
"""

    return [
        task_schedule,
        task_schedule_list,
        task_schedule_cancel,
        task_schedule_update,
    ]
