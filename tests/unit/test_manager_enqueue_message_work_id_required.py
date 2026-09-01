"""Facade-signature tests: ``InstanceManager.enqueue_message`` must accept AND
forward ``work_id_required`` (blocker C1 fix, 2026-09-01).

Background: Fix A (constitution Phase 0) added a ``work_id_required`` kwarg
to the service-layer ``InstanceMessagingService.enqueue_message`` and its
``_ensure_work_id_fail_closed`` guard. The four job-driven dispatch sites
(``job_processor`` / ``job_feedback_observer``) call the message dispatch
through the ``InstanceManager`` FACADE, not the service directly — and the
facade declared no ``work_id_required`` kwarg and forwarded nothing. Every
job-driven dispatch therefore died with ``TypeError`` at facade bind time,
BEFORE the ``LinkageContractError`` contract was ever reachable, and the
``except Exception`` handlers degraded it into retry → dead-letter (the
observer path additionally M10-terminated the fresh instance per attempt).

This file pins the facade seam with REAL calls against a spied service
seam — deliberately NOT ``inspect.getsource`` source-grep assertions:

  1. the kwarg exists on the facade signature (a real call with
     ``work_id_required=True`` would raise ``TypeError`` otherwise);
  2. the kwarg is forwarded to the service with the caller's value;
  3. the default forwards ``False`` (internal self-mint paths unchanged);
  4. the kwarg is keyword-only, matching the service signature;
  5. the facade remains a pass-through for the result object.

The real end-to-end dispatch (facade → service guard → Task row) lives in
``tests/integration/test_job_driven_enqueue_work_id_facade.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.manager import InstanceManager
from daemon.services.messaging_types import AsyncMessageResult

_SENTINEL_RESULT = AsyncMessageResult(
    message_id="msg-1",
    instance_id="inst-1",
    status="queued",
    job_id="job-1",
)


def _facade_with_service_spy() -> tuple[InstanceManager, AsyncMock]:
    """Build a bare facade whose service seam is spied.

    ``InstanceManager.__new__`` skips ``__init__`` entirely (the
    established pattern in ``tests/unit/test_phase4_manager_decomposition.py``).
    The spy replaces the whole ``_messaging_service`` OBJECT, with an
    ``AsyncMock`` standing in for its ``enqueue_message`` method — the
    facade calls ``self._messaging_service.enqueue_message(...)``, so
    that method-level mock is what receives (and records) the forwarded
    kwargs.
    """
    manager = InstanceManager.__new__(InstanceManager)
    service = MagicMock()
    enqueue_spy = AsyncMock(return_value=_SENTINEL_RESULT)
    service.enqueue_message = enqueue_spy
    manager._messaging_service = service
    return manager, enqueue_spy


class TestFacadeWorkIdRequiredForwarding:
    """The facade must accept ``work_id_required`` and forward it verbatim."""

    async def test_accepts_kwarg_and_forwards_true(self):
        """A real call with ``work_id_required=True`` binds (no TypeError —
        the pre-fix blocker) and the service receives the flag."""
        manager, spy = _facade_with_service_spy()

        result = await manager.enqueue_message(
            "inst-1",
            "hello",
            source="job:processor",
            work_id="job-1",
            work_id_required=True,
        )

        spy.assert_awaited_once()
        forwarded = spy.await_args.kwargs
        assert forwarded["work_id_required"] is True
        assert forwarded["work_id"] == "job-1"
        # The facade is a pass-through: the caller gets the service's
        # result object back, untouched.
        assert result is _SENTINEL_RESULT

    async def test_omitted_kwarg_forwards_false_default(self):
        """Omitting the kwarg forwards ``work_id_required=False`` — the
        internal self-mint paths (agent-to-agent, cascade-resume, child
        reports) must be unaffected by the facade change."""
        manager, spy = _facade_with_service_spy()

        await manager.enqueue_message("inst-1", "hello")

        spy.assert_awaited_once()
        forwarded = spy.await_args.kwargs
        assert forwarded["work_id_required"] is False
        assert forwarded["work_id"] is None

    async def test_kwarg_is_keyword_only(self):
        """``work_id_required`` sits after the ``*`` marker — passing it
        positionally must raise ``TypeError`` (it has no positional
        slot), matching the service-layer signature it forwards to."""
        manager, spy = _facade_with_service_spy()

        with pytest.raises(TypeError):
            await manager.enqueue_message(
                "inst-1",
                "hello",
                "job:processor",
                1,
                None,
                None,
                True,  # 7th positional — no slot for it
            )

        spy.assert_not_awaited()

    async def test_existing_keyword_only_neighbours_still_forwarded(self):
        """The pre-existing keyword-only neighbours (``is_deferred``,
        ``is_background``) must still be forwarded — the facade change
        must not disturb the established forwarding style."""
        manager, spy = _facade_with_service_spy()

        await manager.enqueue_message(
            "inst-1",
            "hello",
            source="api",
            is_deferred=True,
            is_background=False,
            work_id="job-2",
            work_id_required=True,
        )

        forwarded = spy.await_args.kwargs
        assert forwarded["is_deferred"] is True
        assert forwarded["is_background"] is False
        assert forwarded["work_id"] == "job-2"
        assert forwarded["work_id_required"] is True
