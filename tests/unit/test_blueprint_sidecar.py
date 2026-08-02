"""Unit tests for the Blueprinter post-experience sidecar hook.

Covers the keyword filter (``_BLUEPRINT_TRIGGER_KEYWORDS``) and the async
sidecar enqueue helper (``_enqueue_blueprinter_scan``) in
:mod:`daemon.tools.knowledge_tools`.

* **Keyword filter** — verifies the membership-check idiom used in
  ``_enqueue_experience_job`` to decide whether a blueprinter scan is
  warranted.
* **Sidecar enqueue** — exercises the fire-and-forget helper against a mocked
  ``InstanceManager`` / ``JobQueueService``, covering the happy path, missing
  dependencies, and error swallowing.

pytest is configured with ``asyncio_mode = "auto"`` — async test functions do
not require ``@pytest.mark.asyncio``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.tools.knowledge_tools import (
    _BLUEPRINT_TRIGGER_KEYWORDS,
    _enqueue_blueprinter_scan,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _matches_keywords(text: str) -> bool:
    """Replicate the membership check from ``_enqueue_experience_job``."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in _BLUEPRINT_TRIGGER_KEYWORDS)


def _make_manager(
    *,
    job_service: MagicMock | None = None,
) -> MagicMock:
    """Build a MagicMock manager with the attributes the sidecar touches."""
    manager = MagicMock()
    manager._job_queue_service = job_service
    return manager


def _make_job_service(
    *,
    queue_id: str | None = "bg-queue-123",
    enqueue_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a MagicMock JobQueueService.

    Args:
        queue_id: If non-None, ``get_by_name`` returns a mock queue with this
            id.  If None, ``get_by_name`` returns None (queue missing).
        enqueue_side_effect: If set, ``enqueue`` raises this exception.
    """
    job_service = MagicMock()
    if queue_id is not None:
        mock_queue = MagicMock()
        mock_queue.queue_id = queue_id
        job_service._queue_repo.get_by_name = MagicMock(return_value=mock_queue)
    else:
        job_service._queue_repo.get_by_name = MagicMock(return_value=None)
    if enqueue_side_effect is not None:
        job_service.enqueue = AsyncMock(side_effect=enqueue_side_effect)
    else:
        job_service.enqueue = AsyncMock(return_value=MagicMock(job_id="job-1"))
    return job_service


# ─── Keyword filter ───────────────────────────────────────────────────────────


class TestKeywordFilter:
    """The keyword membership check used to gate blueprinter scans."""

    def test_keyword_filter_architecture_text(self) -> None:
        """Text mentioning 'architecture' matches the trigger set."""
        text = "The project uses a microservices architecture with Redis."
        assert _matches_keywords(text) is True

    def test_keyword_filter_routine_text(self) -> None:
        """Routine text with no architecture-domain terms does not match."""
        text = "Fixed a typo in the README file."
        assert _matches_keywords(text) is False

    def test_keyword_filter_case_insensitive(self) -> None:
        """Uppercase 'ARCHITECTURE' matches after .lower()."""
        text = "Updated the DATABASE SCHEMA for the new MIGRATION."
        assert _matches_keywords(text) is True

    def test_keyword_filter_multiple_keywords(self) -> None:
        """Text containing several keywords still matches (single True)."""
        text = "Added a new service endpoint with a queue handler."
        assert _matches_keywords(text) is True

    def test_keyword_filter_boundary_exact_phrase(self) -> None:
        """Multi-word keyword phrases match as substrings of the text."""
        # "directory structure" is one of the 31 keywords
        text = "We reorganised the directory structure for clarity."
        assert _matches_keywords(text) is True


# ─── Sidecar enqueue ──────────────────────────────────────────────────────────


class TestEnqueueBlueprinterScan:
    """``_enqueue_blueprinter_scan`` fire-and-forget behaviour."""

    async def test_enqueue_blueprinter_scan_success(self) -> None:
        """Happy path: enqueue is called with correct agent/queue/priority."""
        job_service = _make_job_service(queue_id="bg-queue-123")
        manager = _make_manager(job_service=job_service)

        await _enqueue_blueprinter_scan(
            manager, "text about architecture", "proj-1", "inst-1"
        )

        job_service.enqueue.assert_called_once()
        call_kwargs = job_service.enqueue.call_args.kwargs
        assert call_kwargs["agent_id"] == "blueprinter"
        assert call_kwargs["queue_id"] == "bg-queue-123"
        assert call_kwargs["priority"] == 8
        assert call_kwargs["project_id"] == "proj-1"
        assert "blueprint-sidecar:inst-1" == call_kwargs["source"]

    async def test_enqueue_blueprinter_scan_no_job_service(self) -> None:
        """No JobQueueService on manager → returns silently, no enqueue."""
        manager = _make_manager(job_service=None)

        # Must not raise
        await _enqueue_blueprinter_scan(
            manager, "architecture text", "proj-1", "inst-1"
        )

    async def test_enqueue_blueprinter_scan_no_background_queue(self) -> None:
        """Background queue not found → returns silently, no enqueue."""
        job_service = _make_job_service(queue_id=None)
        manager = _make_manager(job_service=job_service)

        await _enqueue_blueprinter_scan(
            manager, "architecture text", "proj-1", "inst-1"
        )

        job_service.enqueue.assert_not_called()

    async def test_enqueue_blueprinter_scan_never_raises(self) -> None:
        """If enqueue raises, the sidecar swallows it and returns None."""
        job_service = _make_job_service(
            enqueue_side_effect=RuntimeError("DB connection lost")
        )
        manager = _make_manager(job_service=job_service)

        # Must not raise — fire-and-forget
        result = await _enqueue_blueprinter_scan(
            manager, "architecture text", "proj-1", "inst-1"
        )
        assert result is None
