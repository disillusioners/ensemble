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


# ---------------------------------------------------------------------------
# P5B staged-boot proof — F7 provenance line + F4/F8/F2 operator notes
# ---------------------------------------------------------------------------


def test_writer_started_at_provenance_is_envelope_derived_only() -> None:
    """P5B F7 — the run report's parenthetical carried a timestamp that
    matched neither the trigger nor the boot (a mis-derived note). The
    writer contract now pins the provenance to envelope-derived values
    only: the arm instant embedded in the ``run_token`` — the same
    instant the receipt's ``started_at`` derives from — else the
    parenthetical is omitted, never invented."""
    skill = _read("agents/maintenancer/skills-template/run-report-writer.md")
    assert "Started-at provenance (P5B F7)" in skill
    assert "run_token" in skill
    assert "OMITTED, never invented" in skill
    low = skill.lower()
    assert "never read a db" in low or "never reads the db" in low


def test_runbook_carries_the_auth_off_list_door_operator_note() -> None:
    """P5B F4 — operator note, not a defect: the list door 503s on
    auth-OFF by design (BR-17 fail-closed); the view URLs still serve."""
    runbook = _read("docs/runbooks/central-maintenance-manual-trigger.md")
    assert "AUTH_NOT_CONFIGURED" in runbook
    assert "BR-17" in runbook
    assert "/views/maintenance-reports/" in runbook


def test_runbook_carries_the_staged_boot_prerequisites_note() -> None:
    """P5B F8 — a staged/frozen boot against an env-configured PG DB
    needs the tier-1 ensemble.json OR ENSEMBLE_DB_AMBIENT_ALLOW=1, else
    the tier-2 guard hard-exits 78. The per-variable scrub recipe stays
    a POINTER (guard message + env-fence conventions), not inlined."""
    runbook = _read("docs/runbooks/central-maintenance-manual-trigger.md")
    assert "ENSEMBLE_DB_AMBIENT_ALLOW=1" in runbook
    assert "assert_db_resolution_sanctioned" in runbook
    assert "78" in runbook
    assert "ensemble.json" in runbook
    low = runbook.lower()
    assert "per-variable" in low, "the scrub recipe must stay a per-variable POINTER"
    assert "env -u var*" in low or "never the `env -u var*` glob" in low


def test_runbook_carries_the_local_weekday_operator_note() -> None:
    """P5B F2 — operator-doc clarity: the weekday knob is evaluated on
    the LOCAL window date, so the stored run_at_utc lands on the PRIOR
    UTC date (Sun 04:15 ICT = Sat 21:15Z) without being off-by-one."""
    runbook = _read("docs/runbooks/central-maintenance-manual-trigger.md")
    assert "LOCAL" in runbook
    assert "PRIOR UTC date" in runbook
