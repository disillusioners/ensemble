"""Phase-3 service-contract CI guard (scheduled-tasks phase 2 §Task 5.1).

Phase-3 REST handlers delegate to ``daemon.services.scheduling_service``
(the CANONICAL service seam landed in phase 2). This test pins the
import contract BEFORE phase 3 wires: the five module-level async
service functions and the canonical Pydantic models must resolve at
import time. If a later refactor renames any of these symbols, this
guard trips in CI instead of phase-3's handlers failing at request time.
"""

from __future__ import annotations

import inspect

import pytest

import daemon.services.scheduling_service as scheduling_service

EXPECTED_SERVICE_FUNCTIONS = (
    "create_schedule",
    "cancel_schedule",
    "get_schedule",
    "list_schedules",
    "update_schedule",
)

EXPECTED_MODELS = (
    "ScheduleCreatePayload",
    "ScheduleUpdatePayload",
    "ScheduleCreateResponse",
    "ScheduleCancelResponse",
    "ScheduleDetail",
    "ScheduleListItem",
    "ScheduleUpdateResponse",
)


@pytest.mark.parametrize("function_name", EXPECTED_SERVICE_FUNCTIONS)
def test_phase3_service_contract_resolved(function_name: str) -> None:
    """Each phase-3-delegated symbol exists and is a module-level coroutine function."""
    func = getattr(scheduling_service, function_name, None)
    assert func is not None, (
        f"daemon.services.scheduling_service.{function_name} is missing — "
        f"phase-3 REST imports this name verbatim; restore it or update "
        f"phase-3 handlers in the same commit"
    )
    assert inspect.iscoroutinefunction(func), (
        f"daemon.services.scheduling_service.{function_name} must be an "
        f"async def (phase-3 handlers await it directly)"
    )


def test_phase3_service_models_resolved() -> None:
    """The canonical Pydantic models (ADR-013 boundary contract) resolve."""
    for model_name in EXPECTED_MODELS:
        model = getattr(scheduling_service, model_name, None)
        assert model is not None, (
            f"daemon.services.scheduling_service.{model_name} is missing — "
            f"phase-2 is the CANONICAL HOME for these models; phase-3 "
            f"re-exports via import, never redefines"
        )
        from pydantic import BaseModel

        assert issubclass(model, BaseModel)


def test_create_schedule_payload_canonical_field_names() -> None:
    """Canonical create-payload field names (architecture §5 cleanup):
    timezone (not tz), cron_expression (not raw_cron), weekday=Sun=0."""
    fields = scheduling_service.ScheduleCreatePayload.model_fields
    for name in ("label", "agent", "message", "when", "timezone", "recurrence",
                 "weekday", "cron_expression", "project_id", "priority",
                 "instance_mode"):
        assert name in fields, (
            f"ScheduleCreatePayload lost canonical field {name!r} — "
            f"phase-3 mapping (_schedule_create_to_payload) depends on it"
        )
    assert "tz" not in fields and "raw_cron" not in fields


def test_service_never_imports_jobs_or_delete() -> None:
    """ADR-010 guard: the service source must not reference the job-dispatch
    path (``POST /api/jobs`` / ``enqueue_message_job``) or
    ``delete_source_config`` (cancel preserves history)."""
    import pathlib

    source = pathlib.Path(scheduling_service.__file__).read_text()
    for banned in ("enqueue_message_job", "delete_source_config", "POST /api/jobs"):
        assert banned not in source, (
            f"scheduling_service.py must not reference {banned!r} — the "
            f"service creates schedule rows; dispatch is the adapter's job"
        )
