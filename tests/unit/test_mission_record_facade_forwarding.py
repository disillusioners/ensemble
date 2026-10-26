"""Facade-forwarding tests — the ``mission_id`` carrier crosses the facade.

Completion-subsystem Proposal B PR1. The ``InstanceManager`` is a
MANUAL-FORWARDING facade over ``InstanceMessagingService``; a kwarg
added to the facade but NOT forwarded is the project's best-documented
bug class (Facade-Forwarding Discipline — the P0 ``d5c50994`` lesson).
This file is the mission_id half of the discipline, modeled on
``tests/unit/test_manager_enqueue_message_work_id_required.py`` (real
calls against a spied service seam — deliberately NOT
``inspect.getsource`` source-grep only):

  1. ``mission_id`` exists keyword-only on BOTH facade enqueue methods
     AND ``_process_message_with_tracking`` (a real call binds);
  2. the kwarg is FORWARDED to the service with the caller's value;
  3. the default forwards ``None`` (every pre-PR1 caller unchanged);
  4. the signature pins in ``test_phase1_facade_signature_unchanged``
     were EXTENDED (not bypassed) — asserted here via the expected
     tuples so a future kwarg removal fails loudly.

The real end-to-end stamp (facade → service → MessageQueue metadata →
claim → delivery tap) is covered by the service/threading tests and the
D2(c) integration file.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

from daemon.manager import InstanceManager
from daemon.services.messaging_types import AsyncMessageResult

_SENTINEL_RESULT = AsyncMessageResult(
    message_id="msg-1",
    instance_id="inst-1",
    status="queued",
    job_id="job-1",
)

_MISSION = "12345678-1234-4123-8123-123456789abc"


def _facade_with_service_spy() -> tuple[InstanceManager, AsyncMock]:
    manager = InstanceManager.__new__(InstanceManager)
    service = MagicMock()
    enqueue_spy = AsyncMock(return_value=_SENTINEL_RESULT)
    service.enqueue_message = enqueue_spy
    manager._messaging_service = service
    return manager, enqueue_spy


def _facade_with_job_spy() -> tuple[InstanceManager, AsyncMock]:
    manager = InstanceManager.__new__(InstanceManager)
    service = MagicMock()
    job_spy = AsyncMock(return_value=_SENTINEL_RESULT)
    service.enqueue_message_job = job_spy
    manager._messaging_service = service
    return manager, job_spy


class TestFacadeMissionIdForwarding:
    async def test_enqueue_message_forwards_mission_id(self):
        manager, spy = _facade_with_service_spy()
        await manager.enqueue_message(
            "inst-1", "hello", mission_id=_MISSION
        )
        spy.assert_awaited_once()
        assert spy.await_args.kwargs["mission_id"] == _MISSION

    async def test_enqueue_message_default_forwards_none(self):
        manager, spy = _facade_with_service_spy()
        await manager.enqueue_message("inst-1", "hello")
        spy.assert_awaited_once()
        assert spy.await_args.kwargs["mission_id"] is None

    async def test_enqueue_message_job_forwards_mission_id(self):
        manager, spy = _facade_with_job_spy()
        await manager.enqueue_message_job(
            "inst-1", "hello", mission_id=_MISSION
        )
        spy.assert_awaited_once()
        assert spy.await_args.kwargs["mission_id"] == _MISSION

    async def test_enqueue_message_job_default_forwards_none(self):
        manager, spy = _facade_with_job_spy()
        await manager.enqueue_message_job("inst-1", "hello")
        spy.assert_awaited_once()
        assert spy.await_args.kwargs["mission_id"] is None

    def test_mission_id_is_keyword_only_on_both_facade_methods(self):
        for method in (
            InstanceManager.enqueue_message,
            InstanceManager.enqueue_message_job,
        ):
            params = inspect.signature(method).parameters
            assert "mission_id" in params, method
            assert (
                params["mission_id"].kind
                == inspect.Parameter.KEYWORD_ONLY
            ), method
            assert params["mission_id"].default is None, method

    def test_tracking_signature_accepts_mission_id(self):
        params = inspect.signature(
            InstanceManager._process_message_with_tracking
        ).parameters
        assert "mission_id" in params
        assert params["mission_id"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["mission_id"].default is None

    async def test_tracking_forwards_mission_id_to_the_service(self):
        """The REAL dispatch funnel forwards the carrier: the manager's
        ``_process_message_with_tracking`` must hand ``mission_id`` to
        the messaging service (silent=True skips the provenance-window
        stamps so the spy needs no manager internals)."""
        manager = InstanceManager.__new__(InstanceManager)
        service = MagicMock()
        tracking_spy = AsyncMock(return_value=MagicMock(name="result"))
        service._process_message_with_tracking = tracking_spy
        manager._messaging_service = service

        await manager._process_message_with_tracking(
            "inst-1", "hello", "msg-1", silent=True, mission_id=_MISSION
        )

        tracking_spy.assert_awaited_once()
        assert tracking_spy.await_args.kwargs["mission_id"] == _MISSION

    def test_the_extended_signature_pins_carry_mission_id(self):
        """The signature-unchanged guard was EXTENDED for PR1 (not
        bypassed): both expected tuples must now carry ``mission_id`` —
        a future removal of the kwarg must fail here too.

        The LIVE facade signatures are asserted too: pinning only the
        constants would test the literal, not the gate (the repo's
        documented CONSTANT-PIN TAUTOLOGY anti-pattern).
        """
        from tests.unit.test_phase1_facade_signature_unchanged import (
            _EXPECTED_ENQUEUE_MESSAGE_JOB_PARAMS,
            _EXPECTED_ENQUEUE_MESSAGE_PARAMS,
        )

        for method in (
            InstanceManager.enqueue_message,
            InstanceManager.enqueue_message_job,
        ):
            assert "mission_id" in inspect.signature(method).parameters, method

        assert "mission_id" in _EXPECTED_ENQUEUE_MESSAGE_PARAMS
        assert "mission_id" in _EXPECTED_ENQUEUE_MESSAGE_JOB_PARAMS
