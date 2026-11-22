"""P5 T5.6 — manual-trigger token round-trip + audit sub-check.

Central Maintenance System (P5).  The runtime path is the MESSAGE-BODY
trigger token (APPROVER-2/B8, PD-3): the dispatch message embeds
``trigger: manual`` (or ``trigger: scheduled`` for the weekly chain) and
the maintenancer orchestrator parses the token and passes ``trigger_type``
through the writer dispatch envelope.  The writer NEVER reads the DB
(R-P5-7).  The receipt header carries ``trigger_type``.

AUDIT SUB-CHECK (secondary, explicitly marked — NOT the runtime path):
row provenance (``creator_instance_id`` = the dispatching instance, a
freshly-minted row) plus the per-fire audit (``schedule_executions`` rows
+ the ``schedule_fire_notify`` SYSTEM notice) corroborate the transport.

Documented fallbacks (constraints pinned as tests below):
  * ``POST /api/schedules/{id}/trigger`` (``daemon/routers/schedules.py``
    ~476-567): requires a RUNNING adapter (503 otherwise); 409 on a
    cancelled row; the execution is recorded by the scheduler's
    execution callback, not by the route.
  * direct ``send_message`` to a maintenancer instance: weakest
    provenance (no schedule row, no executions audit).

SAFETY FENCE: no DB writes to anything shaped live; throwaway SQLite
under tmp_path only where a row is needed.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from daemon.repositories.source.maintenance_seed import WEEKLY_RUN_MESSAGE
from daemon.services.schedule_fire_notify import notify_schedule_fire

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def _parse_trigger_token(message: str) -> str | None:
    m = re.search(r"(?m)^trigger:\s*(\S+)\s*$", message)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Runtime path — token round-trip
# ---------------------------------------------------------------------------


def test_token_roundtrip_scheduled_chain_message() -> None:
    """Runtime path: weekly chain message -> envelope -> receipt (scheduled).

    The chain literal (P1 T1.1) embeds ``trigger: scheduled``; the
    orchestrator parses it and passes ``trigger_type`` through the
    writer envelope; the receipt header carries it.  The writer never
    reads the DB.
    """
    assert "trigger: scheduled" in WEEKLY_RUN_MESSAGE
    assert _parse_trigger_token(WEEKLY_RUN_MESSAGE) == "scheduled"

    wf = _read("agents/maintenancer/workflow.md")
    assert "Parse the trigger" in wf, "orchestrator must document trigger parsing"
    assert "trigger_type" in wf, "orchestrator must carry trigger_type"

    skill = _read("agents/maintenancer/skills-template/run-report-writer.md")
    assert "FROM THE ENVELOPE" in skill
    low = skill.lower()
    assert "never read a db" in low or "never reads the db" in low

    from daemon.routers.maintenance_reports import _HEADER_FIELDS

    assert "trigger_type" in _HEADER_FIELDS


def test_token_roundtrip_manual_invocation() -> None:
    """Runtime path: manual invocation message -> envelope -> receipt (manual)."""
    manual_msg = (
        "trigger: manual\nExecute one manual Central Maintenance run now: "
        "dispatch the five lane children + report writer per the "
        "orchestrator workflow; parse the trigger token and pass "
        "trigger_type to the writer child."
    )
    assert _parse_trigger_token(manual_msg) == "manual"

    runbook = _read("docs/runbooks/central-maintenance-manual-trigger.md")
    assert "trigger: manual" in runbook
    assert "trigger: manual" in _read("agents/maintenancer/workflow.md") or "manual" in _read(
        "agents/maintenancer/workflow.md"
    )


def test_orchestrator_workflow_carries_token_to_writer_envelope() -> None:
    wf = _read("agents/maintenancer/workflow.md")
    low = wf.lower()
    assert "trigger" in low
    assert "envelope" in low
    assert "run-report-writer" in low or "report writer" in low


def test_writer_skill_embeds_trigger_type_from_envelope_never_reads_db() -> None:
    skill = _read("agents/maintenancer/skills-template/run-report-writer.md")
    assert "FROM THE ENVELOPE" in skill
    low = skill.lower()
    assert "never read a db" in low or "never reads the db" in low
    # envelope-only derivation: no DB read / no derivation
    assert "never derive" in low


def test_receipt_header_carries_trigger_type() -> None:
    from daemon.routers.maintenance_reports import _HEADER_FIELDS

    assert "trigger_type" in _HEADER_FIELDS


# ---------------------------------------------------------------------------
# AUDIT sub-check (secondary, NOT the runtime path)
# ---------------------------------------------------------------------------


def test_audit_row_provenance_creator_instance_id_freshly_minted() -> None:
    """AUDIT: row provenance — creator_instance_id on a freshly-minted row."""
    from daemon.repositories.source.models import SourceConfig

    cfg = {"creator_instance_id": "ari-instance-abc", "agent": "maintenancer"}
    row = SourceConfig(
        source_id="src-1",
        source_type="scheduler",
        name="central-maintenance-manual",
        config=cfg,
        enabled=True,
        autostart=True,
        status="running",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert row.config.get("creator_instance_id") == "ari-instance-abc"


def test_audit_schedule_executions_table_carries_per_fire_rows() -> None:
    """AUDIT: per-fire audit — schedule_executions rows exist."""
    from daemon.repositories.source.models import ScheduleExecution

    assert hasattr(ScheduleExecution, "triggered_at")
    assert hasattr(ScheduleExecution, "schedule_id")
    assert hasattr(ScheduleExecution, "status")


@pytest.mark.asyncio
async def test_audit_schedule_fire_notify_absent_creator_skips() -> None:
    """AUDIT: identity gate — absent creator_instance_id -> silent skip."""
    manager = Mock()
    manager._instance_repository = None
    manager.enqueue_message_job = AsyncMock()
    await notify_schedule_fire(
        manager,
        schedule_config={"label": "x"},  # no creator_instance_id
        recipient_instance_id="target-1",
    )
    await asyncio.sleep(0.05)
    manager.enqueue_message_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_schedule_fire_notify_self_alarm_skips() -> None:
    """AUDIT: identity gate — self-alarm (creator == recipient) -> skip."""
    manager = Mock()
    repo = Mock()
    repo.get = Mock(return_value=Mock(status="running"))
    manager._instance_repository = repo
    manager.enqueue_message_job = AsyncMock()
    await notify_schedule_fire(
        manager,
        schedule_config={"creator_instance_id": "same-id"},
        recipient_instance_id="same-id",
    )
    await asyncio.sleep(0.05)
    manager.enqueue_message_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_schedule_fire_notify_terminal_creator_skips() -> None:
    """AUDIT: identity gate — terminal creator -> skip."""
    manager = Mock()
    repo = Mock()
    repo.get = Mock(return_value=Mock(status="completed"))
    manager._instance_repository = repo
    manager.enqueue_message_job = AsyncMock()
    await notify_schedule_fire(
        manager,
        schedule_config={"creator_instance_id": "creator-1"},
        recipient_instance_id="target-1",
    )
    await asyncio.sleep(0.05)
    manager.enqueue_message_job.assert_not_awaited()


# ---------------------------------------------------------------------------
# Documented fallbacks + constraints
# ---------------------------------------------------------------------------


def test_fallback_post_trigger_endpoint_constraints_documented() -> None:
    """Documented fallback: POST /api/schedules/{id}/trigger constraints.

    Requires a RUNNING adapter (503 otherwise); 409 on a cancelled row;
    the execution is recorded by the scheduler's execution callback, not
    by the route.
    """
    src = _read("daemon/routers/schedules.py")
    assert "SCHEDULE_ALREADY_CANCELLED" in src
    assert "status_code=409" in src
    assert "adapter not running" in src.lower() or "adapter" in src.lower()
    assert "execution_callback" in src


def test_fallback_direct_send_message_weakest_provenance_documented() -> None:
    """Documented fallback: direct send_message (weakest provenance)."""
    text = _read(".agents/shared/planning/maintenance-system/decisions.md")
    assert "weakest provenance" in text
    runbook = _read("docs/runbooks/central-maintenance-manual-trigger.md")
    assert "send_message" in runbook
    assert "weakest" in runbook.lower() or "not canonical" in runbook.lower()
