"""Focused validation test for the get_message_status endpoint (D13).

Verifies:
  1. The endpoint reads from the TaskRepository (post-D13) instead of
     querying job_queue_items (pre-D13 legacy path).
  2. The ``running`` Task status is mapped to ``processing`` for the
     frontend (pre-D13 API contract). This mapping is one-way — the
     DB stores canonical Task status; the API response carries the
     legacy contract value.
  3. Other Task statuses map correctly (``pending``, ``paused``,
     ``completed``, ``failed``, ``cancelled``).
  4. Fallback to queue stats works when no Task row exists for the
     message_id (internal WorkerPool messages that used a different
     code path).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_task_row(*, status: str, result: str | None = None, error: str | None = None):
    """Build a mock Task row matching what TaskRepository.get_by_message returns."""
    row = MagicMock()
    row.id = 1
    row.task_type = "process_message"
    row.instance_id = "inst-abc"
    row.message_id = "msg-xyz"
    row.status = status
    row.worker_id = "worker-1"
    row.retry_count = 0
    row.result = result
    row.error = error
    return row


def _make_manager(*, task_row=None, queue_stats=None):
    """Build a mock InstanceManager with the minimal surface needed
    by get_message_status.
    """
    manager = MagicMock()

    # get_instance (async) — succeeds
    async def _get_instance(instance_id):
        return MagicMock(instance_id=instance_id)

    manager.get_instance = _get_instance

    # task_repo — present, returns the row we wired (or None for fallback)
    manager._task_repo = MagicMock()
    if task_row is not None:
        manager._task_repo.get_by_message = MagicMock(return_value=task_row)
    else:
        manager._task_repo.get_by_message = MagicMock(return_value=None)

    # queue_stats fallback
    if queue_stats is None:
        queue_stats = MagicMock(
            pending_count=2,
            processing_count=1,
            oldest_message_age_seconds=42.0,
        )
    manager.get_queue_stats = AsyncMock(return_value=queue_stats)

    return manager


@pytest.fixture
def client_with_manager():
    """Provide a TestClient and a way to inject a manager into app.state."""
    from daemon.routers.messages import router

    app = FastAPI()
    app.include_router(router)
    state = {"manager": None}

    @app.middleware("http")
    async def _inject_manager(request, call_next):
        request.app.state.manager = state["manager"]
        return await call_next(request)

    client = TestClient(app)
    return client, state


class TestGetMessageStatusMapping:
    """Verify the status mapping that the D13 endpoint relies on."""

    def test_running_maps_to_processing(self, client_with_manager):
        """CORE MAPPING: Task.status='running' must map to 'processing' for the FE.

        The frontend compares ``status === 'processing'`` directly; returning
        the raw ``'running'`` would silently break the UI.
        """
        client, state = client_with_manager
        state["manager"] = _make_manager(task_row=_make_task_row(status="running"))

        resp = client.get("/instances/inst-abc/messages/msg-xyz")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "processing", (
            f"D13 status mapping broken: 'running' must map to 'processing'; "
            f"got {body['status']!r}. Frontend compares status === 'processing' "
            f"directly — returning 'running' would silently break the UI."
        )
        assert body["message_id"] == "msg-xyz"
        assert body["instance_id"] == "inst-abc"

    def test_pending_passthrough(self, client_with_manager):
        """pending → pending (no change)."""
        client, state = client_with_manager
        state["manager"] = _make_manager(task_row=_make_task_row(status="pending"))

        resp = client.get("/instances/inst-abc/messages/msg-xyz")

        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    def test_paused_passthrough(self, client_with_manager):
        """paused → paused (preserved — pause was a first-class state)."""
        client, state = client_with_manager
        state["manager"] = _make_manager(task_row=_make_task_row(status="paused"))

        resp = client.get("/instances/inst-abc/messages/msg-xyz")

        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    def test_completed_passthrough(self, client_with_manager):
        """completed → completed."""
        client, state = client_with_manager
        state["manager"] = _make_manager(task_row=_make_task_row(status="completed"))

        resp = client.get("/instances/inst-abc/messages/msg-xyz")

        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_failed_passthrough(self, client_with_manager):
        """failed → failed."""
        client, state = client_with_manager
        state["manager"] = _make_manager(task_row=_make_task_row(status="failed"))

        resp = client.get("/instances/inst-abc/messages/msg-xyz")

        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

    def test_cancelled_passthrough(self, client_with_manager):
        """cancelled → cancelled."""
        client, state = client_with_manager
        state["manager"] = _make_manager(task_row=_make_task_row(status="cancelled"))

        resp = client.get("/instances/inst-abc/messages/msg-xyz")

        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_result_summary_from_json_payload(self, client_with_manager):
        """result_summary parses a JSON Task.result into a string."""
        client, state = client_with_manager
        state["manager"] = _make_manager(
            task_row=_make_task_row(
                status="completed",
                result=json.dumps({"summary": "all good", "tokens": 1234}),
            )
        )

        resp = client.get("/instances/inst-abc/messages/msg-xyz")

        assert resp.status_code == 200
        body = resp.json()
        # The result_summary is a string representation of the parsed payload
        assert isinstance(body["result_summary"], str)
        # The parsed JSON should appear in the serialized summary
        assert "all good" in body["result_summary"]

    def test_error_field_propagates(self, client_with_manager):
        """The error field from Task.error is preserved in the response."""
        client, state = client_with_manager
        state["manager"] = _make_manager(
            task_row=_make_task_row(
                status="failed",
                error="LLM provider timeout after 30s",
            )
        )

        resp = client.get("/instances/inst-abc/messages/msg-xyz")

        assert resp.status_code == 200
        assert resp.json()["error"] == "LLM provider timeout after 30s"

    def test_instance_not_found_returns_404(self, client_with_manager):
        """Looking up an unknown instance_id returns 404."""
        client, state = client_with_manager

        async def _get_instance_missing(instance_id):
            raise KeyError(instance_id)

        manager = _make_manager(task_row=None)
        manager.get_instance = _get_instance_missing
        state["manager"] = manager

        resp = client.get("/instances/missing/messages/msg-xyz")

        assert resp.status_code == 404
        assert "not found" in resp.text.lower()

    def test_fallback_to_queue_stats_when_no_task_row(self, client_with_manager):
        """If no Task row exists for message_id, return queue stats fallback.

        Internal WorkerPool messages may use a different code path that does
        not create a Task row keyed by message_id. The endpoint must return
        the legacy queue_stats payload so the frontend can still display
        pending/processing counts.
        """
        client, state = client_with_manager
        state["manager"] = _make_manager(task_row=None)

        resp = client.get("/instances/inst-abc/messages/msg-xyz")

        assert resp.status_code == 200
        body = resp.json()
        # Legacy shape preserved
        assert "queue_stats" in body, (
            f"Fallback must return queue_stats; got {body!r}"
        )
        assert body["queue_stats"]["pending_count"] == 2
        assert body["queue_stats"]["processing_count"] == 1
        # message_id/instance_id echoed back
        assert body["message_id"] == "msg-xyz"
        assert body["instance_id"] == "inst-abc"
        # No status field in fallback (legacy contract)
        assert "status" not in body

    def test_task_repo_lookup_exception_falls_back_to_stats(self, client_with_manager):
        """If task_repo.get_by_message raises, log warning and fall back to stats.

        The endpoint must not crash if the task table lookup fails — it logs
        the exception and falls back to the queue_stats contract.
        """
        client, state = client_with_manager
        manager = MagicMock()

        async def _get_instance(instance_id):
            return MagicMock(instance_id=instance_id)
        manager.get_instance = _get_instance

        # task_repo present but lookup raises
        manager._task_repo = MagicMock()
        manager._task_repo.get_by_message = MagicMock(
            side_effect=RuntimeError("task table missing")
        )
        manager.get_queue_stats = AsyncMock(
            return_value=MagicMock(
                pending_count=0, processing_count=0, oldest_message_age_seconds=None
            )
        )
        state["manager"] = manager

        resp = client.get("/instances/inst-abc/messages/msg-xyz")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "queue_stats" in body