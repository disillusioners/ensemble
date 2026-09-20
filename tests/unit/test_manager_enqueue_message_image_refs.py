"""Facade-signature tests: ``InstanceManager.enqueue_message`` AND
``InstanceManager.enqueue_message_job`` must accept AND forward
``image_refs`` (Phase 2 / clipboard-image-chat / freeze-list A6).

Background: Phase 2 round-2 amendment #27 made the ``image_refs``
forwarding REQUIRED (not contingent) across the 5-function chain
(facade → service → _prepare_enqueued_message → row + kwargs stamp).
This file pins the facade seam with REAL calls against a spied
service seam — deliberately NOT ``inspect.getsource`` source-grep
assertions (per blueprint Core Architecture §Facade-Forwarding
Discipline, the bug class slips past AsyncMock + inspect.getsource).

The 5-test pattern (mirror ``tests/unit/test_manager_enqueue_message_work_id_required.py``)
covers BOTH facade methods:

  1. Kwarg forwarded verbatim to the service.
  2. Default None forwards None (byte-identical-when-absent).
  3. Keyword-only (positional raise TypeError).
  4. Default-behavior unchanged for an internal self-mint caller.
  5. Existing keyword-only neighbors (images, is_deferred, is_background,
     work_id) still forwarded.

The real end-to-end dispatch (facade → service guard → Task row + row
column) lives in ``tests/integration/test_job_driven_enqueue_image_refs_facade.py``.
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


def _facade_with_service_spy():
    """Build a bare facade whose service seam is spied.

    ``InstanceManager.__new__`` skips ``__init__`` entirely (the
    established pattern in
    ``tests/unit/test_phase4_manager_decomposition.py``). The spy
    replaces the whole ``_messaging_service`` OBJECT, with an
    ``AsyncMock`` standing in for its ``enqueue_message`` method.
    """
    manager = InstanceManager.__new__(InstanceManager)
    service = MagicMock()
    enqueue_spy = AsyncMock(return_value=_SENTINEL_RESULT)
    service.enqueue_message = enqueue_spy
    enqueue_job_spy = AsyncMock(return_value=_SENTINEL_RESULT)
    service.enqueue_message_job = enqueue_job_spy
    manager._messaging_service = service
    return manager, enqueue_spy, enqueue_job_spy


# ===========================================================================
# InstanceManager.enqueue_message
# ===========================================================================


class TestFacadeEnqueueMessageImageRefsForwarding:
    async def test_accepts_kwarg_and_forwards_value(self):
        manager, enqueue_spy, _ = _facade_with_service_spy()
        refs = ["/api/tmp_images/" + "a" * 32]

        result = await manager.enqueue_message(
            "inst-1", "hello", source="api", image_refs=refs
        )

        enqueue_spy.assert_awaited_once()
        forwarded = enqueue_spy.await_args.kwargs
        assert forwarded["image_refs"] == refs
        assert result is _SENTINEL_RESULT

    async def test_omitted_kwarg_forwards_none_default(self):
        manager, enqueue_spy, _ = _facade_with_service_spy()

        await manager.enqueue_message("inst-1", "hello", source="api")

        enqueue_spy.assert_awaited_once()
        forwarded = enqueue_spy.await_args.kwargs
        assert forwarded["image_refs"] is None

    async def test_kwarg_is_keyword_only(self):
        manager, enqueue_spy, _ = _facade_with_service_spy()

        with pytest.raises(TypeError):
            # Positional pass after the existing positional args — the
            # kwarg sits after ``*`` so it has no positional slot.
            await manager.enqueue_message(
                "inst-1",
                "hello",
                "api",
                1,
                None,
                None,
                ["/api/tmp_images/" + "a" * 32],
            )

        enqueue_spy.assert_not_awaited()

    async def test_default_internal_path_unaffected(self):
        """Internal callers (no image_refs at all) must be unaffected:
        the dispatch succeeds and the kwarg is forwarded as None."""
        manager, enqueue_spy, _ = _facade_with_service_spy()

        result = await manager.enqueue_message(
            "inst-1", "internal nudge", source="internal_agent:parent-1"
        )

        assert result is _SENTINEL_RESULT
        enqueue_spy.assert_awaited_once()
        forwarded = enqueue_spy.await_args.kwargs
        assert forwarded["image_refs"] is None

    async def test_existing_keyword_only_neighbors_still_forwarded(self):
        """Pre-existing keyword-only neighbours (is_deferred, is_background,
        work_id, work_id_required, images) must STILL be forwarded —
        the facade change must not disturb the established style."""
        manager, enqueue_spy, _ = _facade_with_service_spy()
        refs = ["/api/tmp_images/" + "a" * 32]
        images = ["data:image/png;base64,xxx"]

        await manager.enqueue_message(
            "inst-1",
            "hello",
            source="api",
            images=images,
            is_deferred=True,
            is_background=False,
            work_id="job-1",
            work_id_required=True,
            image_refs=refs,
        )

        enqueue_spy.assert_awaited_once()
        forwarded = enqueue_spy.await_args.kwargs
        assert forwarded["images"] == images
        assert forwarded["is_deferred"] is True
        assert forwarded["is_background"] is False
        assert forwarded["work_id"] == "job-1"
        assert forwarded["work_id_required"] is True
        assert forwarded["image_refs"] == refs


# ===========================================================================
# InstanceManager.enqueue_message_job
# ===========================================================================


class TestFacadeEnqueueMessageJobImageRefsForwarding:
    async def test_accepts_kwarg_and_forwards_value(self):
        manager, _, enqueue_job_spy = _facade_with_service_spy()
        refs = ["/api/tmp_images/" + "a" * 32]

        result = await manager.enqueue_message_job(
            "inst-1", "hello", source="api", image_refs=refs
        )

        enqueue_job_spy.assert_awaited_once()
        forwarded = enqueue_job_spy.await_args.kwargs
        assert forwarded["image_refs"] == refs
        assert result is _SENTINEL_RESULT

    async def test_omitted_kwarg_forwards_none_default(self):
        manager, _, enqueue_job_spy = _facade_with_service_spy()

        await manager.enqueue_message_job("inst-1", "hello", source="api")

        enqueue_job_spy.assert_awaited_once()
        forwarded = enqueue_job_spy.await_args.kwargs
        assert forwarded["image_refs"] is None

    async def test_kwarg_is_keyword_only(self):
        manager, _, enqueue_job_spy = _facade_with_service_spy()

        with pytest.raises(TypeError):
            # Positional pass — kwarg is keyword-only.
            await manager.enqueue_message_job(
                "inst-1",
                "hello",
                "api",
                1,
                None,
                None,
                ["/api/tmp_images/" + "a" * 32],
            )

        enqueue_job_spy.assert_not_awaited()

    async def test_default_internal_path_unaffected(self):
        manager, _, enqueue_job_spy = _facade_with_service_spy()

        result = await manager.enqueue_message_job(
            "inst-1", "internal nudge", source="internal_agent:parent-1"
        )

        assert result is _SENTINEL_RESULT
        enqueue_job_spy.assert_awaited_once()
        forwarded = enqueue_job_spy.await_args.kwargs
        assert forwarded["image_refs"] is None

    async def test_existing_keyword_only_neighbors_still_forwarded(self):
        """Pre-existing keyword-only neighbours (queue_id, is_deferred,
        is_background) must STILL be forwarded — the facade change
        must not disturb the established style."""
        manager, _, enqueue_job_spy = _facade_with_service_spy()
        refs = ["/api/tmp_images/" + "a" * 32]
        images = ["data:image/png;base64,xxx"]

        await manager.enqueue_message_job(
            "inst-1",
            "hello",
            source="api",
            images=images,
            is_deferred=True,
            is_background=False,
            queue_id="system_parallel_queue",
            image_refs=refs,
        )

        enqueue_job_spy.assert_awaited_once()
        forwarded = enqueue_job_spy.await_args.kwargs
        assert forwarded["images"] == images
        assert forwarded["is_deferred"] is True
        assert forwarded["is_background"] is False
        assert forwarded["queue_id"] == "system_parallel_queue"
        assert forwarded["image_refs"] == refs
